"""Extract structured fields from a sales (VAT) invoice image using a vision LLM."""
from __future__ import annotations

import base64
import json

from ..config import get_settings
from ..schemas import SalesInvoiceExtraction
from .client import get_client
from .extraction import _strip_json

_SYSTEM_PROMPT = """\
你是识别增值税销售发票的专家。请从发票图片中提取以下字段，并且只返回一个
JSON 对象，不要输出其他内容。

字段说明：
- invoice_number：发票号码（字符串，或 null）
- invoice_code：发票代码（字符串，或 null）
- invoice_date：开票日期，格式 YYYY-MM-DD（或 null）
- buyer：购买方名称（或 null）
- buyer_tax_id：购买方纳税人识别号（或 null）
- seller：销售方名称（或 null）
- seller_tax_id：销售方纳税人识别号（或 null）
- net_amount：金额（不含税合计），仅数字（或 null）
- tax_amount：税额（销项税额）合计，仅数字（或 null）
- tax_rate：税率，如 "13%"（或 null）
- total_amount：价税合计（小写），仅数字（或 null）
- goods：货物或应税劳务、服务名称（多项可用、分隔；或 null）
- extra_fields：一个对象，包含发票上其余所有可识别字段（如 校验码、机器编号、
  开票人、复核、收款人、价税合计大写、备注、规格型号、单价、数量 等）。
- confidence：识别信心，0.0 到 1.0

只返回合法 JSON。无法识别的字段填 null；extra_fields 无内容时填 {}。\
"""


def _to_sales(content: str) -> SalesInvoiceExtraction:
    try:
        data = json.loads(_strip_json(content))
    except (json.JSONDecodeError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data["llm_used"] = True
    return SalesInvoiceExtraction(**data)


def extract_sales_invoice(
    image_bytes: bytes,
    mime_type: str = "image/jpeg",
    *,
    client=None,
    model: str | None = None,
) -> SalesInvoiceExtraction:
    """Return structured sales-invoice fields; empty result when no LLM is set."""
    settings = get_settings()
    client = client if client is not None else get_client()
    if client is None:
        return SalesInvoiceExtraction(llm_used=False)

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
                    {"type": "text", "text": "请将这张销售发票的字段提取为 JSON。"},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            },
        ],
    )
    return _to_sales(resp.choices[0].message.content or "{}")
