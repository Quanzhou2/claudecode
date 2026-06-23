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
    tax_amount: float | None = None
    description: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    llm_used: bool = False

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
            return float(v)
        # Strip currency symbols / thousands separators.
        cleaned = "".join(c for c in str(v) if c.isdigit() or c in ".-")
        try:
            return float(cleaned) if cleaned not in ("", "-", ".") else None
        except ValueError:
            return None
