# -*- coding: utf-8 -*-
"""
简道云 · 自建插件 · 后端函数（Python）：付款凭证图片相似度查重
=====================================================================
安装：简道云 → 插件管理 → 新建自建插件 → 新建函数（后端函数 / Python）→
      把本文件整段粘进代码框。入口函数为 main(params, context)。

入参声明：imageUrl(必填) 付款凭证图片 URL；dataId(选填)；rowId(选填)
出参声明：ok, status, duplicate, similarity, threshold, note, matchedRecord

LLM 走通用 OpenAI 兼容接口（POST /chat/completions，视觉消息，base64 图片）。
运行时假设：入口 main(params, context)；HTTP 用 requests（无则回退 urllib）。
=====================================================================
"""

import base64
import json
import re

try:
    import requests as _requests
except Exception:  # pragma: no cover
    _requests = None
import urllib.request

CONFIG = {
    "jdy": {
        "apiKey": "FILL_简道云APIKey",
        "appId": "68ca0e2fb59e070714b68aa0",
        "entryId": "6899902c9582f683ab885f8d",
        "listUrl": "https://api.jiandaoyun.com/api/v5/app/entry/data/list",
    },
    "fields": {
        "subform": "FILL_票据录入_widget",
        "attachment": "FILL_附件_widget",
        "recordNo": "FILL_报销单编号_widget",
        "flowStatus": "FILL_流程状态_widget",
    },
    "dedup": {"statusIncludes": ["已完成", "审批通过", "已报销"], "scanLimit": 5000, "pageSize": 100, "excludeSelf": True},
    "llm": {
        "endpoint": "https://api.openai.com/v1/chat/completions",  # 可指向任意 OpenAI 兼容服务
        "apiKey": "FILL_LLM_APIKey",
        "model": "gpt-4o",
        "threshold": 0.9,
        "maxCandidates": 60,
        "batchSize": 8,
        "timeoutMs": 30000,
    },
    "status": {"verified": "验证通过", "duplicateVoucher": "凭证重复", "failed": "识别失败"},
}

PROMPT = (
    "你是付款凭证查重助手。第一张图片是本次上传的付款凭证，其余是历史已报销记录中的凭证。"
    "请判断本次上传是否与其中某一张为“同一张凭证”（翻拍、重扫、截图、轻微裁剪或调色都算同一张；"
    "仅版式相同但金额/单号/时间/收付款方不同不算）。只输出一个 JSON，不要解释。格式："
    '{"scores":[每张历史图与上传图的相似度0~1],"bestIndex":最相似下标,"similarity":最高相似度,'
    '"sameDocument":true或false,"reason":"简要中文理由"}'
)


# ============================ 入口 ============================
def main(params, context=None):
    p = params or {}
    return run_voucher_similarity(image_url=p.get("imageUrl"), data_id=p.get("dataId"), row_id=p.get("rowId"))


def run_voucher_similarity(image_url, data_id=None, row_id=None):
    S = CONFIG["status"]
    th = CONFIG["llm"]["threshold"]
    base = {"ok": False, "status": S["failed"], "duplicate": False, "similarity": 0,
            "threshold": th, "note": "", "matchedRecord": None}
    if not image_url:
        return _merge(base, note="未取到上传的凭证图片地址")

    # 1) 上传图 -> base64
    try:
        new_image = fetch_image(image_url)
    except Exception as e:
        return _merge(base, note="读取上传凭证失败：%s" % e)

    # 2) 历史凭证图片
    try:
        candidates = query_history_vouchers(data_id)
    except Exception as e:
        return _merge(base, status=S["duplicateVoucher"], note="查重查询失败，暂不能提交：%s" % e)
    if not candidates:
        return _merge(base, ok=True, status=S["verified"], note="无历史凭证可比对，通过")
    candidates = candidates[: CONFIG["llm"]["maxCandidates"]]

    # 3) LLM 分批比对，命中即停
    best = {"similarity": 0.0, "candidate": None, "reason": ""}
    bs = CONFIG["llm"]["batchSize"]
    try:
        for i in range(0, len(candidates), bs):
            batch = candidates[i:i + bs]
            imgs = [fetch_image(c["url"]) for c in batch]
            res = llm_compare(new_image, imgs)
            scores = res.get("scores") or []
            for k, cand in enumerate(batch):
                s = _to_float(scores[k]) if k < len(scores) else 0.0
                if s > best["similarity"]:
                    best = {"similarity": s, "candidate": cand, "reason": res.get("reason") or ""}
            if best["similarity"] >= th:
                break
    except Exception as e:
        return _merge(base, status=S["duplicateVoucher"], note="相似度分析失败，暂不能提交：%s" % e)

    # 4) 判定
    if best["similarity"] >= th:
        m = best["candidate"] or {}
        return _merge(
            base, status=S["duplicateVoucher"], duplicate=True, similarity=best["similarity"],
            matchedRecord={"dataId": m.get("dataId"), "recordNo": m.get("recordNo"),
                           "rowId": m.get("rowId"), "imageUrl": m.get("url")},
            note="付款凭证疑似重复（相似度 %d%% ≥ 阈值 %d%%）%s：%s" % (
                round(best["similarity"] * 100), round(th * 100),
                ("，命中历史报销单「%s」" % m["recordNo"]) if m.get("recordNo") else "", best["reason"]),
        )
    return _merge(base, ok=True, status=S["verified"], similarity=best["similarity"],
                  note="未发现重复凭证（最高相似度 %d%% < 阈值 %d%%）" % (round(best["similarity"] * 100), round(th * 100)))


# ---------------- 历史数据 ----------------
def query_history_vouchers(self_data_id=None):
    f, d = CONFIG["fields"], CONFIG["dedup"]
    wanted = [w for w in [f["subform"], f["recordNo"], f["flowStatus"]] if w and not w.startswith("FILL_")]
    flt = None
    if f["flowStatus"] and not f["flowStatus"].startswith("FILL_") and d.get("statusIncludes"):
        flt = {"rel": "and", "cond": [{"field": f["flowStatus"], "type": "text", "method": "in", "value": d["statusIncludes"]}]}
    records, cursor = [], ""
    limit = min(d.get("pageSize", 100), 100)
    scan_limit = d.get("scanLimit", 5000)
    max_pages = (scan_limit // limit) + 1
    headers = {"Content-Type": "application/json", "Authorization": "Bearer " + CONFIG["jdy"]["apiKey"]}
    for _ in range(max_pages):
        body = {"app_id": CONFIG["jdy"]["appId"], "entry_id": CONFIG["jdy"]["entryId"], "limit": limit}
        if wanted:
            body["fields"] = wanted
        if flt:
            body["filter"] = flt
        if cursor:
            body["data_id"] = cursor
        resp = http_post_json(CONFIG["jdy"]["listUrl"], body, headers, 15000)
        rows = (resp or {}).get("data") or []
        for row in rows:
            records.append(row)
            if len(records) >= scan_limit:
                break
        if len(rows) < limit:
            break
        cursor = rows[-1].get("_id")
        if not cursor:
            break

    out = []
    for rec in records:
        if d["excludeSelf"] and self_data_id and rec.get("_id") == self_data_id:
            continue
        sub = rec.get(f["subform"]) or []
        if not isinstance(sub, list):
            continue
        for row in sub:
            for url in file_urls(row.get(f["attachment"])):
                out.append({"dataId": rec.get("_id"),
                            "recordNo": rec.get(f["recordNo"]) if f.get("recordNo") else None,
                            "rowId": row.get("_id"), "url": url})
    return out


def file_urls(v):
    if not v:
        return []
    arr = v if isinstance(v, list) else [v]
    out = []
    for it in arr:
        if not it:
            continue
        if isinstance(it, str):
            out.append(it)
        elif isinstance(it, dict) and it.get("url"):
            out.append(it["url"])
    return out


# ---------------- LLM（OpenAI 兼容）----------------
def llm_compare(new_image, cand_images):
    if not cand_images:
        return {"scores": [], "similarity": 0, "sameDocument": False, "reason": ""}
    content = [{"type": "text", "text": PROMPT}]
    all_imgs = [new_image] + cand_images
    for i, img in enumerate(all_imgs):
        content.append({"type": "text", "text": "【上传图】" if i == 0 else "【历史图#%d】" % (i - 1)})
        data_url = "data:%s;base64,%s" % (img.get("mediaType", "image/jpeg"), img["base64"])
        content.append({"type": "image_url", "image_url": {"url": data_url}})
    body = {"model": CONFIG["llm"]["model"], "max_tokens": 500, "temperature": 0,
            "messages": [{"role": "user", "content": content}]}
    headers = {"Content-Type": "application/json", "Authorization": "Bearer " + CONFIG["llm"]["apiKey"]}
    raw = http_post_json(CONFIG["llm"]["endpoint"], body, headers, CONFIG["llm"]["timeoutMs"])
    text = ""
    try:
        text = raw["choices"][0]["message"]["content"]
    except Exception:
        text = ""
    return parse_json(text, len(cand_images))


def parse_json(text, n):
    fb = {"scores": [0] * n, "similarity": 0, "sameDocument": False, "reason": "模型未返回可解析结果"}
    if not text:
        return fb
    m = re.search(r"\{[\s\S]*\}", str(text))
    if not m:
        return fb
    try:
        obj = json.loads(m.group(0))
    except Exception:
        return fb
    scores = [_clamp01(x) for x in obj.get("scores", [])] if isinstance(obj.get("scores"), list) else []
    sim = _clamp01(obj.get("similarity"))
    if not sim and scores:
        sim = max(scores)
    return {"scores": scores, "similarity": sim,
            "sameDocument": bool(obj.get("sameDocument")),
            "reason": obj.get("reason") if isinstance(obj.get("reason"), str) else ""}


# ---------------- 工具 ----------------
def _clamp01(n):
    try:
        n = float(n)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, n))


def _to_float(n):
    try:
        return float(n)
    except (TypeError, ValueError):
        return 0.0


def _merge(d, **kw):
    out = dict(d)
    out.update(kw)
    return out


def _guess_media(url):
    u = str(url).lower()
    if ".png" in u:
        return "image/png"
    if ".webp" in u:
        return "image/webp"
    return "image/jpeg"


# ---------------- HTTP ----------------
def fetch_image(url):
    if _requests is not None:
        resp = _requests.get(url, timeout=CONFIG["llm"]["timeoutMs"] / 1000.0)
        if not (200 <= resp.status_code < 300):
            raise RuntimeError("HTTP %s" % resp.status_code)
        data = resp.content
    else:
        with urllib.request.urlopen(url, timeout=CONFIG["llm"]["timeoutMs"] / 1000.0) as r:  # noqa: S310
            data = r.read()
    return {"base64": base64.b64encode(data).decode("ascii"), "mediaType": _guess_media(url)}


def http_post_json(url, body, headers, timeout_ms=15000):
    timeout = (timeout_ms or 15000) / 1000.0
    data = json.dumps(body).encode("utf-8")
    if _requests is not None:
        resp = _requests.post(url, data=data, headers=headers, timeout=timeout)
        if not (200 <= resp.status_code < 300):
            raise RuntimeError("HTTP %s" % resp.status_code)
        return resp.json()
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
        return json.loads(r.read().decode("utf-8"))
