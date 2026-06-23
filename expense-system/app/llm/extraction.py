"""Extract structured fields from a receipt image using a vision LLM."""
from __future__ import annotations

import base64
import json

from ..config import get_settings
from ..schemas import ReceiptExtraction
from .client import get_client

_SYSTEM_PROMPT = """\
你是识别报销发票和收据的专家。请提取以下字段，并且只返回一个 JSON 对象，
不要输出任何其他内容。

字段说明：
- receipt_number：发票号码 / 收据号 / 发票代码（字符串，或 null）
- vendor：商户或公司名称（字符串，或 null）
- expense_date：发票上的日期，格式为 YYYY-MM-DD（或 null）
- amount：价税合计总金额，仅数字、不含货币符号（或 null）
- currency：ISO 4217 货币代码，如 CNY、USD、EUR（或 null）
- category：最匹配的分类，从以下中择一：餐饮、差旅、交通、住宿、办公、
  软件、娱乐、医疗、其他（或 null）
- tax_amount：税额 / 增值税金额，数字（或 null）
- description：对消费内容的简短中文描述
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


def extract_receipt(
    image_bytes: bytes,
    mime_type: str = "image/jpeg",
    *,
    client=None,
    model: str | None = None,
) -> ReceiptExtraction:
    """Return structured receipt fields.

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
    try:
        data = json.loads(_strip_json(content))
    except (json.JSONDecodeError, ValueError):
        data = {}

    if not isinstance(data, dict):
        data = {}
    data["llm_used"] = True
    return ReceiptExtraction(**data)
