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


def test_extract_payment_screenshot():
    # WeChat Pay bill: outgoing amount shown as negative, transaction no as id.
    content = (
        '{"receipt_number": "4200000513202003095572442375", '
        '"vendor": "四川省财政厅", "expense_date": "2020-03-09", '
        '"amount": "-100.00", "currency": "CNY", "category": "其他", '
        '"payment_method": "微信支付·零钱", "description": "四川省非税微信缴费"}'
    )
    result = extract_receipt(b"img", client=FakeVisionClient(content))
    assert result.receipt_number == "4200000513202003095572442375"
    assert result.amount == 100.0  # negative sign normalized to a positive amount
    assert result.payment_method == "微信支付·零钱"
    assert result.vendor == "四川省财政厅"


def test_extract_order_uses_actual_paid_amount():
    # E-commerce order: model returns the 实付/合计 amount, not the discount.
    content = (
        '{"receipt_number": "285331455324", "vendor": "babycare京东自营官方旗舰店", '
        '"amount": 0.01, "payment_method": "京东·微信支付", "category": "其他"}'
    )
    result = extract_receipt(b"img", client=FakeVisionClient(content))
    assert result.amount == 0.01
    assert result.payment_method == "京东·微信支付"


def test_extract_offline_when_no_client():
    # conftest forces LLM_API_KEY="" so get_client() returns None.
    result = extract_receipt(b"img")
    assert result.llm_used is False
