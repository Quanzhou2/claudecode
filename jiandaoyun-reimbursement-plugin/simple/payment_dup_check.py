# -*- coding: utf-8 -*-
"""
简道云后端函数(Python) —— 付款凭证查重（按图片相似度）。

场景：填写「费用报销单」时上传付款凭证图片 → 前端事件调用本函数 → 返回是否重复。
入口：main(params, context)
入参：imageUrl(必填) 凭证图片URL；dataId(选填) 当前报销单id，用于排除自身
出参：duplicated(bool 是否重复)、similarity(0~1 最高相似度)、message(说明)

只做一件事：用多模态 LLM 把本次上传的凭证与历史凭证比相似度，超过阈值就 duplicated=True。
"""

import json
import re
import urllib.request

try:
    import requests as _rq
except Exception:
    _rq = None

# ====== 配置：把 FILL_* 换成你的值 ======
CONF = {
    # 小米 MiMo（OpenAI 兼容）。密钥在 platform.xiaomimimo.com 申请。
    # 注意：本函数要把凭证图片发给模型比相似度，必须用“多模态/能看图”的 MiMo 版本。
    "llm_url": "https://api.xiaomimimo.com/v1/chat/completions",
    "llm_key": "FILL_MiMo_APIKey",
    "llm_model": "mimo-v2.5-pro",   # 按平台上的模型名填，如看图不生效可换多模态版（如 mimo-v2-omni）
    "jdy_key": "FILL_简道云APIKey",
    "app_id": "68ca0e2fb59e070714b68aa0",
    "entry_id": "6899902c9582f683ab885f8d",
    "sub_widget": "FILL_票据录入子表单_widget",   # 发票信息子表单
    "attach_widget": "FILL_附件_widget",           # 子表单里的「附件」（付款凭证）字段
    "threshold": 0.9,       # 相似度阈值，超过判重
    "max_compare": 40,      # 最多比对多少张历史图，控制成本
}


def main(params, context=None):
    p = params or {}
    image_url = p.get("imageUrl")
    if not image_url:
        return {"duplicated": False, "similarity": 0, "message": "未上传凭证图片"}

    new_b64, mt = download_b64(image_url)
    hist = history_images(p.get("dataId"))[: CONF["max_compare"]]
    if not hist:
        return {"duplicated": False, "similarity": 0, "message": "无历史凭证可比对"}

    best = 0.0
    for i in range(0, len(hist), 8):           # 每次最多带 8 张历史图
        best = max(best, compare(new_b64, mt, hist[i:i + 8]))
        if best >= CONF["threshold"]:
            break

    if best >= CONF["threshold"]:
        return {"duplicated": True, "similarity": best,
                "message": "付款凭证疑似重复（相似度 %d%%），不能重复提交" % round(best * 100)}
    return {"duplicated": False, "similarity": best, "message": "凭证未重复"}


def compare(new_b64, mt, batch):
    """把上传图 + 一批历史图发给 LLM，返回与最像一张的相似度 0~1。"""
    content = [{"type": "text", "text":
                "第一张是本次上传的付款凭证，其余是历史付款凭证。判断本次上传与其中最像的一张有多相似"
                "（翻拍/截图/裁剪/调色算同一张；仅版式相同但金额单号不同不算）。"
                "只输出一个 0 到 1 之间的小数，不要其它文字。"}]
    content.append({"type": "image_url", "image_url": {"url": "data:%s;base64,%s" % (mt, new_b64)}})
    for h in batch:
        content.append({"type": "image_url", "image_url": {"url": "data:%s;base64,%s" % (h[1], h[0])}})
    body = {"model": CONF["llm_model"], "temperature": 0, "max_tokens": 10,
            "messages": [{"role": "user", "content": content}]}
    data = post_json(CONF["llm_url"], body, {"Authorization": "Bearer " + CONF["llm_key"]})
    try:
        text = data["choices"][0]["message"]["content"]
    except Exception:
        text = ""
    m = re.search(r"[01](?:\.\d+)?", str(text))
    return max(0.0, min(1.0, float(m.group(0)))) if m else 0.0


def history_images(self_id):
    """拉取历史报销单里所有付款凭证图片，下载为 base64，排除当前记录自身。"""
    urls, cursor = [], ""
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
                for f in file_urls(line.get(CONF["attach_widget"])):
                    urls.append(f)
        if len(rows) < 100:
            break
        cursor = rows[-1].get("_id")
        if not cursor:
            break
    out = []
    for u in urls[: CONF["max_compare"]]:
        try:
            out.append(download_b64(u))
        except Exception:
            pass
    return out


def file_urls(v):
    if not v:
        return []
    arr = v if isinstance(v, list) else [v]
    out = []
    for it in arr:
        if isinstance(it, str):
            out.append(it)
        elif isinstance(it, dict) and it.get("url"):
            out.append(it["url"])
    return out


# ---------- HTTP（requests 优先，回退 urllib）----------
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
        r.raise_for_status()
        return r.json()
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as f:  # noqa: S310
        return json.loads(f.read().decode("utf-8"))
