import json

from _fakes import FakeVisionClient

from app.llm.extraction import extract_receipt


def test_extraction_parses_extra_fields():
    content = (
        '{"receipt_number": "FP1", "amount": "100", '
        '"extra_fields": {"购买方名称": "ACME", "校验码": "1234", "空值": ""}}'
    )
    r = extract_receipt(b"img", client=FakeVisionClient(content))
    # Empty values are dropped; the rest are kept as strings.
    assert r.extra_fields == {"购买方名称": "ACME", "校验码": "1234"}


def test_extraction_extra_fields_default_empty():
    r = extract_receipt(b"img", client=FakeVisionClient('{"amount": "5"}'))
    assert r.extra_fields == {}


def test_einvoice_stores_and_shows_extra_fields(client):
    client.post("/register", data={"username": "alice", "password": "secret1"})
    extra = {"购买方名称": "蓝天公司", "销售方纳税人识别号": "91510000ABC"}
    r = client.post("/expenses", data={
        "ticket_type": "einvoice", "number": "FP-EX", "amount": "100",
        "currency": "CNY", "extra_fields": json.dumps(extra, ensure_ascii=False),
    })
    detail = client.get(str(r.url)).text
    assert "其他字段" in detail
    assert "购买方名称" in detail and "蓝天公司" in detail
    assert "销售方纳税人识别号" in detail


def test_edit_updates_extra_fields(client):
    client.post("/register", data={"username": "alice", "password": "secret1"})
    r = client.post("/expenses", data={
        "ticket_type": "einvoice", "number": "FP-ED", "amount": "10", "currency": "CNY",
        "extra_fields": json.dumps({"备注": "old"}, ensure_ascii=False),
    })
    url = str(r.url)
    eid = url.rstrip("/").split("/")[-1]
    client.post(f"/expenses/{eid}/edit", data={
        "number": "FP-ED", "amount": "10", "currency": "CNY",
        "extra_fields": json.dumps({"备注": "new", "开票人": "张三"}, ensure_ascii=False),
    })
    detail = client.get(url).text
    assert "开票人" in detail and "张三" in detail and "new" in detail
