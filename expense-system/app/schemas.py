"""Pydantic schemas for LLM extraction and structured payloads."""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field, field_validator


class ReceiptExtraction(BaseModel):
    """Structured fields extracted from a receipt image."""

    receipt_number: str | None = None
    vendor: str | None = None
    expense_date: date | None = None
    amount: float | None = None
    currency: str | None = None
    category: str | None = None
    payment_method: str | None = None
    tax_amount: float | None = None
    description: str | None = None
    # Any other fields read off the document (e.g. 购买方名称, 销售方纳税人识别号,
    # 发票代码, 校验码, 价税合计大写, 开票人, 备注, line items, ...).
    extra_fields: dict[str, str] = Field(default_factory=dict)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    llm_used: bool = False

    @field_validator("extra_fields", mode="before")
    @classmethod
    def _coerce_extra(cls, v):
        if not isinstance(v, dict):
            return {}
        out: dict[str, str] = {}
        for key, val in v.items():
            if key is None or val is None or val == "":
                continue
            if isinstance(val, (dict, list)):
                import json
                val = json.dumps(val, ensure_ascii=False)
            out[str(key)] = str(val)
        return out

    @field_validator("expense_date", mode="before")
    @classmethod
    def _parse_date(cls, v):
        if v in (None, "", "null"):
            return None
        if isinstance(v, date):
            return v
        # Accept common formats produced by LLMs.
        from datetime import datetime

        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%m/%d/%Y", "%Y.%m.%d"):
            try:
                return datetime.strptime(str(v).strip(), fmt).date()
            except ValueError:
                continue
        return None

    @field_validator("amount", "tax_amount", mode="before")
    @classmethod
    def _parse_amount(cls, v):
        if v in (None, "", "null"):
            return None
        if isinstance(v, (int, float)):
            return abs(float(v))
        # Strip currency symbols / thousands separators.
        cleaned = "".join(c for c in str(v) if c.isdigit() or c in ".-")
        try:
            # Payment bills show outgoing amounts as negative (e.g. -100.00);
            # an expense amount is always the positive magnitude.
            return abs(float(cleaned)) if cleaned not in ("", "-", ".") else None
        except ValueError:
            return None
