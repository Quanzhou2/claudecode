"""Extract structured fields from a receipt image using a vision LLM."""
from __future__ import annotations

import base64
import json

from ..config import get_settings
from ..schemas import ReceiptExtraction
from .client import get_client

_SYSTEM_PROMPT = """\
You are an expert at reading expense receipts and invoices. Extract the
following fields and respond with a SINGLE JSON object and nothing else.

Fields:
- receipt_number: the invoice / receipt / fapiao number (string, or null)
- vendor: the merchant or company name (string, or null)
- expense_date: the date on the receipt in YYYY-MM-DD format (or null)
- amount: the TOTAL amount as a number, no currency symbol (or null)
- currency: ISO 4217 code, e.g. CNY, USD, EUR (or null)
- category: best-fit category, one of: Meals, Travel, Transport, Lodging,
  Office, Software, Entertainment, Medical, Other (or null)
- tax_amount: tax/VAT amount as a number (or null)
- description: a short human-readable summary of what was purchased
- confidence: your confidence from 0.0 to 1.0 that the extraction is correct

Return only valid JSON. Use null for anything you cannot read.\
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
                    {"type": "text", "text": "Extract the receipt fields as JSON."},
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
