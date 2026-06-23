from datetime import date

from _fakes import FakeVisionClient

from app.llm.extraction import extract_receipt


def test_extract_parses_json():
    content = (
        '{"receipt_number": "FP-123", "vendor": "Sky Cafe", '
        '"expense_date": "2026-01-15", "amount": "86.50", "currency": "CNY", '
        '"category": "Meals", "tax_amount": 5, "confidence": 0.9}'
    )
    result = extract_receipt(b"img", client=FakeVisionClient(content))
    assert result.llm_used is True
    assert result.receipt_number == "FP-123"
    assert result.amount == 86.5
    assert result.expense_date == date(2026, 1, 15)
    assert result.category == "Meals"


def test_extract_handles_code_fence():
    content = '```json\n{"vendor": "ACME", "amount": "10"}\n```'
    result = extract_receipt(b"img", client=FakeVisionClient(content))
    assert result.vendor == "ACME"
    assert result.amount == 10.0


def test_extract_handles_garbage():
    result = extract_receipt(b"img", client=FakeVisionClient("not json at all"))
    assert result.llm_used is True
    assert result.amount is None
    assert result.vendor is None


def test_extract_offline_when_no_client():
    # conftest forces LLM_API_KEY="" so get_client() returns None.
    result = extract_receipt(b"img")
    assert result.llm_used is False
