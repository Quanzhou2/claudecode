# -*- coding: utf-8 -*-
"""
简道云后端函数(Python) —— 发票查重（按发票号码）。

场景：填写「费用报销单」时上传发票图片 → 前端事件调用本函数 → 返回是否重复。
入口：main(params, context)
入参：imageUrl(必填) 发票图片URL；dataId(选填) 当前报销单id，用于排除自身
出参：duplicated(bool 是否重复)、invoiceNumber(识别到的号码)、message(说明)

只做一件事：用 LLM 读出发票号码，和历史记录里的发票号码比，重复就返回 duplicated=True。
"""

import json
import re
import urllib.error
import urllib.request

try:
    import requests as _rq
except Exception:
    _rq = None

# ====== 配置：把 FILL_* 换成你的值 ======
CONF = {
    # 小米 MiMo（OpenAI 兼容）。密钥在 platform.xiaomimimo.com 申请。
    # 注意：本函数要把发票图片发给模型，必须用“多模态/能看图”的 MiMo 版本。
    "llm_url": "https://api.xiaomimimo.com/v1/chat/completions",
    "llm_key": "FILL_MiMo_APIKey",
    "llm_model": "mimo-v2.5-pro",   # 按平台上的模型名填，如看图不生效可换多模态版（如 mimo-v2-omni）
    "jdy_key": "FILL_简道云APIKey",
    "app_id": "68ca0e2fb59e070714b68aa0",
    "entry_id": "6899902c9582f683ab885f8d",
    "sub_widget": "FILL_票据录入子表单_widget",   # 发票信息子表单
    "number_widget": "FILL_发票号码_widget",       # 子表单里的「发票号码」字段
}


def main(params, context=None):
    p = params or {}
    image_url = p.get("imageUrl")
    if not image_url:
        return {"duplicated": False, "invoiceNumber": "", "message": "未上传发票图片"}

    number, raw = read_invoice_number(image_url)
    if not number:
        return {"duplicated": False, "invoiceNumber": "",
                "message": "未识别到发票号码；模型返回：%s" % (raw[:150] or "(空，可能被推理耗光token或模型看不了图)")}

    if number in history_numbers(p.get("dataId")):
        return {"duplicated": True, "invoiceNumber": number,
                "message": "发票号码 %s 已报销过，不能重复提交" % number}
    return {"duplicated": False, "invoiceNumber": number, "message": "发票未重复"}


def read_invoice_number(image_url):
    """用多模态 LLM 从发票图片里读出发票号码。返回 (号码, 模型原始文本)。"""
    b64, mt = download_b64(image_url)
    content = [
        {"type": "text", "text": "识别这张发票的发票号码，只输出号码本身，不要任何其它文字。"},
        {"type": "image_url", "image_url": {"url": "data:%s;base64,%s" % (mt, b64)}},
    ]
    # MiMo 是推理模型，max_tokens 太小会被“思考”耗光导致 content 为空，这里给足余量
    body = {"model": CONF["llm_model"], "temperature": 0, "max_tokens": 1024,
            "messages": [{"role": "user", "content": content}]}
    data = post_json(CONF["llm_url"], body, llm_headers())
    text = answer_text(data)
    joined = re.sub(r"[\s\-_.]", "", text)              # 去掉空格/连字符，避免号码被拆断
    nums = re.findall(r"[0-9A-Za-z]{6,}", joined)        # 发票号码是 8 位或数电 20 位
    return (max(nums, key=len).upper() if nums else ""), text


def answer_text(data):
    """从（可能带推理的）OpenAI 兼容返回里取出最终回答文本。"""
    try:
        msg = data["choices"][0]["message"]
    except Exception:
        return ""
    c = msg.get("content")
    if isinstance(c, list):   # 多模态返回可能是分段列表
        c = "".join(p.get("text", "") for p in c if isinstance(p, dict))
    text = c or msg.get("reasoning_content") or ""       # content 为空时兜底看 reasoning
    return re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.I).strip()


def history_numbers(self_id):
    """拉取历史报销单里所有发票号码（去空白/大写归一），排除当前记录自身。"""
    seen, cursor = set(), ""
    for _ in range(200):
        body = {"app_id": CONF["app_id"], "entry_id": CONF["entry_id"], "limit": 100,
                "fields": [CONF["sub_widget"]]}
        if cursor:
            body["data_id"] = cursor
        resp = post_json("https://api.jiandaoyun.com/api/v5/app/entry/data/list", body,
                         {"Authorization": "Bearer " + CONF["jdy_key"]})
        rows = (resp or {}).get("data") or []
        for rec in rows:
            if self_id and rec.get("_id") == self_id:
                continue
            for line in rec.get(CONF["sub_widget"]) or []:
                n = re.sub(r"[^0-9A-Za-z]", "", str(line.get(CONF["number_widget"]) or "")).upper()
                if n:
                    seen.add(n)
        if len(rows) < 100:
            break
        cursor = rows[-1].get("_id")
        if not cursor:
            break
    return seen


# ---------- HTTP（requests 优先，回退 urllib）----------
def llm_headers():
    # 小米 MiMo 用 api-key 头；同时带上 Authorization: Bearer 以兼容其它网关
    return {"api-key": CONF["llm_key"], "Authorization": "Bearer " + CONF["llm_key"]}


def download_b64(url):
    import base64
    mt = "image/png" if ".png" in url.lower() else ("image/webp" if ".webp" in url.lower() else "image/jpeg")
    if _rq is not None:
        r = _rq.get(url, timeout=30)
        r.raise_for_status()
        raw = r.content
    else:
        with urllib.request.urlopen(url, timeout=30) as f:  # noqa: S310
            raw = f.read()
    return base64.b64encode(raw).decode("ascii"), mt


def post_json(url, body, headers):
    headers = dict(headers or {})
    headers["Content-Type"] = "application/json"
    data = json.dumps(body).encode("utf-8")
    if _rq is not None:
        r = _rq.post(url, data=data, headers=headers, timeout=30)
        if not (200 <= r.status_code < 300):
            # 把服务器返回体带出来，才能看到 404 到底是“模型不存在”还是“路径不对”等
            raise RuntimeError("HTTP %s -> %s" % (r.status_code, (r.text or "")[:500]))
        return r.json()
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as f:  # noqa: S310
            return json.loads(f.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError("HTTP %s -> %s" % (e.code, e.read().decode("utf-8", "ignore")[:500]))
