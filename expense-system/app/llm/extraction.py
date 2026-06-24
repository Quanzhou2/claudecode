"""Extract structured fields from a receipt image using a vision LLM."""
from __future__ import annotations

import base64
import json

from ..config import get_settings
from ..schemas import ReceiptExtraction
from .client import get_client

_SYSTEM_PROMPT = """\
你是识别各类报销凭证的专家。图片可能是以下任意一种：
- 纸质发票 / 增值税发票 / 收据；
- 电商订单截图：拼多多、淘宝、天猫、京东、抖音商城、微信小店 / 视频号、
  微信"百亿补贴"等；
- 移动支付账单截图：微信支付、支付宝、云闪付 / 银联、银行转账。

请仔细阅读图片，提取以下字段，并且只返回一个 JSON 对象，不要输出其他内容。

字段说明：
- receipt_number：唯一的交易号 / 订单号，用于去重。按以下优先级取其一：
  交易单号 / 微信交易号 / 交易流水号 > 订单编号 > 订单号 > 商户单号 >
  发票号码（字符串，或 null）
- vendor：商户 / 店铺 / 收款方名称（如"商户全称""收款方全称""店铺名"或
  频道名；或 null）
- expense_date：交易日期，格式 YYYY-MM-DD。优先取"支付时间 / 付款时间"，
  其次"下单时间 / 创建时间 / 交易时间"（或 null）
- amount：实际支付的总金额，仅数字、不含货币符号或负号。
  订单请取"实付 / 实付款 / 合计"，不要取商品旁的"原价 / 划线价 / 标价"
  或"共减 / 优惠"（例如商品标价 106.27 而实付款 88 时，应取 88）。
  账单中的负号（如 -100.00）表示支出，金额取其绝对值（或 null）
- currency：货币代码，人民币填 CNY（或 null）
- category：最匹配的分类，从：餐饮、差旅、交通、住宿、办公、软件、娱乐、医疗、其他（或 null）
- payment_method：支付方式，尽量包含平台与渠道，例如
  "微信支付·零钱""支付宝·余额宝""拼多多·多多支付""京东·微信支付"（或 null）
- tax_amount：税额 / 增值税金额（或 null）
- description：对消费内容（商品或事项）的简短中文描述
- confidence：你对本次识别正确性的信心，取值 0.0 到 1.0

只返回合法的 JSON。无法识别的字段请填 null。\
"""


def _strip_json(text: str) -> str:
    """Pull the JSON object out of a model response (handles code fences)."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text


def _to_extraction(content: str) -> ReceiptExtraction:
    """Parse an LLM JSON response into a ReceiptExtraction (best-effort)."""
    try:
        data = json.loads(_strip_json(content))
    except (json.JSONDecodeError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data["llm_used"] = True
    return ReceiptExtraction(**data)


def extract_receipt(
    image_bytes: bytes,
    mime_type: str = "image/jpeg",
    *,
    client=None,
    model: str | None = None,
) -> ReceiptExtraction:
    """Return structured receipt fields from an image.

    Falls back to an empty (manual-entry) result when no LLM is configured,
    so the upload flow always works.
    """
    settings = get_settings()
    client = client if client is not None else get_client()
    if client is None:
        return ReceiptExtraction(llm_used=False)

    model = model or settings.llm_vision_model
    data_uri = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode()}"

    resp = client.chat.completions.create(
        model=model,
        temperature=0,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "请将这张发票/收据的字段提取为 JSON。"},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            },
        ],
    )
    content = resp.choices[0].message.content or "{}"
    return _to_extraction(content)


def extract_receipt_from_text(
    text: str, *, client=None, model: str | None = None
) -> ReceiptExtraction:
    """Return structured receipt fields from pasted/typed text.

    When no LLM is configured, the pasted text is kept in the description so it
    isn't lost and the user can finish the record manually.
    """
    text = (text or "").strip()
    settings = get_settings()
    client = client if client is not None else get_client()
    if client is None:
        return ReceiptExtraction(description=text[:1000] or None, llm_used=False)

    model = model or settings.llm_model
    resp = client.chat.completions.create(
        model=model,
        temperature=0,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": "以下是一段交易 / 支付 / 发票信息文本，请提取为 JSON：\n\n" + text,
            },
        ],
    )
    content = resp.choices[0].message.content or "{}"
    return _to_extraction(content)

