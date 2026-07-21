# -*- coding: utf-8 -*-
"""
简道云 · 自建插件 · 后端函数（Python）：发票识别 · 去重 · 验真
=====================================================================
安装：简道云 → 插件管理 → 新建自建插件 → 新建函数（后端函数 / Python）→
      把本文件整段粘进代码框。入口函数为 main(params, context)。

入参声明（请求参数，类型 any）：
    imageUrl(必填) 发票图片 URL；dataId(选填) 当前报销单 dataId；
    rowId(选填) 子表单行 id；priorVerifyCount(选填) 已有查验次数
出参声明（返回参数，类型 any）：
    ok, status, invoiceType, invoiceCode, invoiceNumber, invoiceDate,
    invoiceAmount, taxAmount, amountWithTax, checkCode, sellerTaxNo,
    verifyCount, note, duplicate, matchedRecord

顺序：OCR 识别 → 先去重（命中即拦截，省一次验真）→ 不重复才验真。
运行时假设：入口 main(params, context)；HTTP 用 requests（无则回退 urllib）。
若你的入口签名不同（如只有 params），改 main 的参数即可。
=====================================================================
"""

import base64
import json
import re

try:
    import requests as _requests
except Exception:  # pragma: no cover - 运行环境无 requests 时回退
    _requests = None
import urllib.request

# ===================== 配置：按你的表单与服务填写 =====================
CONFIG = {
    "jdy": {
        "apiKey": "FILL_简道云APIKey",
        "appId": "68ca0e2fb59e070714b68aa0",
        "entryId": "6899902c9582f683ab885f8d",
        "listUrl": "https://api.jiandaoyun.com/api/v5/app/entry/data/list",
    },
    "fields": {
        "subform": "FILL_票据录入_widget",
        "invoiceNumber": "FILL_发票号码_widget",
        "invoiceCode": "FILL_发票代码_widget",
        "recordNo": "FILL_报销单编号_widget",   # 主表字段，回显命中记录
        "flowStatus": "FILL_流程状态_widget",    # 主表字段，去重只比对已报销
    },
    "dedup": {
        "statusIncludes": ["已完成", "审批通过", "已报销"],  # 参与去重的流程状态；空则不过滤
        "alsoMatchCode": True,   # 发票代码是否纳入去重键
        "scanLimit": 5000,
        "pageSize": 100,
        "excludeSelf": True,
    },
    "ocr": {  # 发票识别：多模态 LLM（OpenAI 兼容视觉接口）直接从图片抽取要素
        "endpoint": "https://api.openai.com/v1/chat/completions",  # 可指向任意 OpenAI 兼容服务
        "apiKey": "FILL_LLM_OCR_APIKey",
        "model": "gpt-4o",
        "timeoutMs": 20000,
    },
    "verify": {
        "requireVerify": True,
        "endpoint": "FILL_验真接口地址",
        "appKey": "FILL_验真_AppKey",
        "appSecret": "FILL_验真_AppSecret",
        "timeoutMs": 15000,
    },
    "status": {
        "verified": "验证通过",
        "duplicateInvoice": "发票重复",
        "verifyFailed": "验真失败",
        "ocrFailed": "识别失败",
    },
}


# ============================ 入口 ============================
def main(params, context=None):
    p = params or {}
    return run_invoice_guard(
        image_url=p.get("imageUrl"),
        data_id=p.get("dataId"),
        row_id=p.get("rowId"),
        prior_verify_count=_to_int(p.get("priorVerifyCount"), 0),
    )


def run_invoice_guard(image_url, data_id=None, row_id=None, prior_verify_count=0):
    S = CONFIG["status"]
    prior = _to_int(prior_verify_count, 0)
    base = {
        "ok": False, "status": S["ocrFailed"], "invoiceType": "", "invoiceCode": "",
        "invoiceNumber": "", "invoiceDate": "", "invoiceAmount": None, "taxAmount": None,
        "amountWithTax": None, "checkCode": "", "sellerTaxNo": "", "verifyCount": prior,
        "note": "", "duplicate": False, "matchedRecord": None,
    }
    if not image_url:
        return _merge(base, note="未取到发票图片地址")

    # 1) OCR 识别
    try:
        inv = ocr_recognize(image_url)
    except Exception as e:
        return _merge(base, note="发票识别失败：%s" % e)
    if not inv.get("invoiceNumber"):
        return _merge(base, note="未能识别到有效发票号码，请上传清晰的发票图片")

    filled = _merge(
        base,
        invoiceType=inv["invoiceType"], invoiceCode=inv["invoiceCode"],
        invoiceNumber=inv["invoiceNumber"], invoiceDate=inv["invoiceDate"],
        invoiceAmount=inv["invoiceAmount"], taxAmount=inv["taxAmount"],
        amountWithTax=inv["amountWithTax"], checkCode=inv["checkCode"],
        sellerTaxNo=inv["sellerTaxNo"], verifyCount=prior,
    )

    # 2) 去重（先查重：命中即拦截，省去验真调用）
    try:
        history = query_history_invoices()
        dup = check_duplicate(inv, history, data_id)
    except Exception as e:
        return _merge(filled, status=S["duplicateInvoice"],
                      note="去重查询失败，暂不能提交：%s" % e)
    if dup["duplicate"]:
        m = dup["matched"] or {}
        return _merge(
            filled, status=S["duplicateInvoice"], duplicate=True,
            matchedRecord={"dataId": m.get("dataId"), "recordNo": m.get("recordNo"), "rowId": m.get("rowId")},
            note="发票重复：号码 %s 已在历史报销单%s中报销过，不能重复提交"
                 % (inv["invoiceNumber"], ("「%s」" % m["recordNo"]) if m.get("recordNo") else ""),
        )

    # 3) 验真（仅对不重复的发票，查验次数 +1）
    filled["verifyCount"] = prior + 1
    try:
        v = verify_invoice(inv)
        if CONFIG["verify"]["requireVerify"] and not v["authentic"]:
            return _merge(filled, status=S["verifyFailed"],
                          note="未发现重复；但发票验真未通过：%s（状态：%s）" % (v["message"], v["invoiceStatus"]))
        filled["note"] = "未发现重复；验真通过：%s" % v["invoiceStatus"]
    except Exception as e:
        return _merge(filled, status=S["verifyFailed"],
                      note="未发现重复；但发票验真调用失败：%s" % e)

    return _merge(filled, ok=True, status=S["verified"], note=filled["note"] + "，可提交")


# ---------------- OCR（多模态 LLM）/ 验真 ----------------
OCR_PROMPT = (
    "你是发票识别助手。请从这张发票图片中提取关键信息，只输出一个 JSON，不要解释、不要代码块围栏。"
    "字段（识别不到就留空字符串或 null）："
    '{"invoiceType":"发票类型","invoiceCode":"发票代码","invoiceNumber":"发票号码",'
    '"invoiceDate":"开票日期(YYYY-MM-DD)","invoiceAmount":不含税金额数字,"taxAmount":税额数字,'
    '"amountWithTax":价税合计数字,"checkCode":"校验码","sellerTaxNo":"销方税号"}'
)


def ocr_recognize(image_url):
    c = CONFIG["ocr"]
    if not c.get("apiKey") or c["apiKey"].startswith("FILL_"):
        raise RuntimeError("未配置 LLM OCR 密钥")
    img = fetch_image(image_url, c["timeoutMs"])
    content = [
        {"type": "text", "text": OCR_PROMPT},
        {"type": "image_url", "image_url": {"url": "data:%s;base64,%s" % (img["mediaType"], img["base64"])}},
    ]
    body = {"model": c["model"], "max_tokens": 800, "temperature": 0,
            "messages": [{"role": "user", "content": content}]}
    headers = {"Content-Type": "application/json", "Authorization": "Bearer " + c["apiKey"]}
    raw = http_post_json(c["endpoint"] or "https://api.openai.com/v1/chat/completions", body, headers, c["timeoutMs"])
    text = ""
    try:
        text = raw["choices"][0]["message"]["content"]
    except Exception:
        text = ""
    m = re.search(r"\{[\s\S]*\}", str(text))
    obj = {}
    if m:
        try:
            obj = json.loads(m.group(0))
        except Exception:
            obj = {}
    return map_ocr(obj)


def fetch_image(url, timeout_ms=20000):
    mt = "image/png" if ".png" in url.lower() else ("image/webp" if ".webp" in url.lower() else "image/jpeg")
    if _requests is not None:
        resp = _requests.get(url, timeout=(timeout_ms or 20000) / 1000.0)
        if not (200 <= resp.status_code < 300):
            raise RuntimeError("HTTP %s" % resp.status_code)
        data = resp.content
    else:
        with urllib.request.urlopen(url, timeout=(timeout_ms or 20000) / 1000.0) as r:  # noqa: S310
            data = r.read()
    return {"base64": base64.b64encode(data).decode("ascii"), "mediaType": mt}


def _svc_headers(c):
    h = {"Content-Type": "application/json"}
    if c.get("appKey"):
        h["X-App-Key"] = c["appKey"]
    if c.get("appSecret"):
        h["X-App-Secret"] = c["appSecret"]
    return h


def map_ocr(raw):
    r = _unwrap(raw)
    number = norm_no(_pick(r, ["invoiceNumber", "InvoiceNum", "InvoiceNumber", "fphm", "发票号码", "number"]))
    return {
        "invoiceType": _pick(r, ["invoiceType", "InvoiceType", "fplx", "发票类型", "type"]) or "",
        "invoiceCode": norm_no(_pick(r, ["invoiceCode", "InvoiceCode", "fpdm", "发票代码", "code"])),
        "invoiceNumber": number,
        "invoiceDate": norm_date(_pick(r, ["invoiceDate", "InvoiceDate", "kprq", "开票日期", "票据日期", "date"])),
        "invoiceAmount": norm_amt(_pick(r, ["invoiceAmount", "AmountWithoutTax", "je", "金额", "不含税金额"])),
        "taxAmount": norm_amt(_pick(r, ["taxAmount", "TaxAmount", "se", "税额"])),
        "amountWithTax": norm_amt(_pick(r, ["amountWithTax", "AmountWithTax", "jshj", "价税合计", "total"])),
        "checkCode": str(_pick(r, ["checkCode", "CheckCode", "jym", "校验码"]) or "").strip(),
        "sellerTaxNo": str(_pick(r, ["sellerTaxNo", "SellerTaxID", "xfsh", "销方税号"]) or "").strip(),
    }


def verify_invoice(inv):
    c = CONFIG["verify"]
    if not c["requireVerify"]:
        return {"authentic": True, "invoiceStatus": "未查验", "message": "未开启验真"}
    if not c["endpoint"] or c["endpoint"].startswith("FILL_"):
        raise RuntimeError("未配置验真接口地址")
    body = {
        "invoiceCode": inv.get("invoiceCode") or "", "invoiceNumber": inv["invoiceNumber"],
        "invoiceDate": inv.get("invoiceDate") or "", "checkCode": inv.get("checkCode") or "",
        "amount": str(inv["invoiceAmount"]) if inv.get("invoiceAmount") is not None else "",
    }
    raw = http_post_json(c["endpoint"], body, _svc_headers(c), c["timeoutMs"])
    r = (raw.get("data") or raw.get("result") or raw.get("Response") or raw) if isinstance(raw, dict) else {}
    st = str(r.get("invoiceStatus") or r.get("status") or r.get("state")
             or r.get("checkResult") or r.get("result") or "")
    code_ok = r.get("code") in (0, "0000") or r.get("success") is True or r.get("errcode") == 0
    abnormal = re.search(r"作废|红冲|异常|查无|不一致|失败|无效", st) is not None
    normal = re.search(r"正常|一致|已开|成功|验证通过", st) is not None
    authentic = False if abnormal else (normal or code_ok)
    return {
        "authentic": bool(authentic),
        "invoiceStatus": st or ("正常" if authentic else "未知"),
        "message": str(r.get("message") or r.get("msg") or st or ("查验通过" if authentic else "查验未通过")),
    }


# ---------------- 去重（自研）----------------
def query_history_invoices():
    f, d = CONFIG["fields"], CONFIG["dedup"]
    wanted = [w for w in [f["subform"], f["recordNo"], f["flowStatus"]] if w and not w.startswith("FILL_")]
    flt = None
    if f["flowStatus"] and not f["flowStatus"].startswith("FILL_") and d.get("statusIncludes"):
        flt = {"rel": "and", "cond": [{"field": f["flowStatus"], "type": "text", "method": "in", "value": d["statusIncludes"]}]}
    out, cursor = [], ""
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
            out.append(row)
            if len(out) >= scan_limit:
                return out
        if len(rows) < limit:
            break
        cursor = rows[-1].get("_id")
        if not cursor:
            break
    return out


def check_duplicate(inv, records, self_data_id=None):
    f, also = CONFIG["fields"], CONFIG["dedup"]["alsoMatchCode"]
    index = {}
    for rec in records:
        sub = rec.get(f["subform"]) or []
        if not isinstance(sub, list):
            continue
        for row in sub:
            entry = {"dataId": rec.get("_id"),
                     "recordNo": rec.get(f["recordNo"]) if f.get("recordNo") else None,
                     "rowId": row.get("_id")}
            key = dedup_key({"invoiceNumber": row.get(f["invoiceNumber"]),
                             "invoiceCode": row.get(f["invoiceCode"])}, also)
            if key and key not in index:
                index[key] = entry
            num_only = norm_no(row.get(f["invoiceNumber"]))
            if also and num_only and num_only not in index:
                index[num_only] = entry
    cand_key = dedup_key(inv, also)
    hit = index.get(cand_key) if cand_key else None
    if not hit and also:
        hit = index.get(norm_no(inv.get("invoiceNumber")))
    if hit and CONFIG["dedup"]["excludeSelf"] and self_data_id and hit["dataId"] == self_data_id:
        hit = None
    return {"duplicate": bool(hit), "matched": hit}


def dedup_key(inv, also_match_code):
    num = norm_no((inv or {}).get("invoiceNumber"))
    if not num:
        return ""
    if also_match_code:
        code = norm_no((inv or {}).get("invoiceCode"))
        return (code + ":" + num) if code else num
    return num


# ---------------- 归一化工具 ----------------
def norm_no(v):
    if v is None:
        return ""
    s = "".join(chr(ord(ch) - 0xFEE0) if "０" <= ch <= "ｚ" else ch for ch in str(v))
    return re.sub(r"[\s　\-_.]", "", s).strip().upper()


def norm_amt(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return v
    c = re.sub(r"[^0-9.\-]", "", str(v))
    if c in ("", "-", "."):
        return None
    try:
        return float(c)
    except ValueError:
        return None


def norm_date(v):
    if not v:
        return ""
    s = str(v).strip()
    digits = re.sub(r"[^0-9]", "", s)
    if len(digits) == 8:
        return "%s-%s-%s" % (digits[0:4], digits[4:6], digits[6:8])
    m = re.search(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})", s)
    if m:
        return "%s-%02d-%02d" % (m.group(1), int(m.group(2)), int(m.group(3)))
    return s


def _unwrap(raw):
    if not isinstance(raw, dict):
        return {}
    cands = [raw.get("data", {}).get("result") if isinstance(raw.get("data"), dict) else None,
             raw.get("data"), raw.get("result"), raw.get("words_result"),
             raw.get("Response"), raw.get("invoice"), raw]
    for c in cands:
        if isinstance(c, dict):
            return _flatten_words(c)
    return raw


def _flatten_words(obj):
    out = {}
    for k, v in obj.items():
        out[k] = v["words"] if isinstance(v, dict) and isinstance(v.get("words"), str) else v
    return out


def _pick(obj, keys):
    if not isinstance(obj, dict):
        return None
    for k in keys:
        if obj.get(k) not in (None, ""):
            return obj[k]
    return None


def _to_int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _merge(d, **kw):
    out = dict(d)
    out.update(kw)
    return out


# ---------------- HTTP（requests 优先，回退 urllib）----------------
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
