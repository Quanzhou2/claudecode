"""Sales invoice → accounting voucher module."""
import pytest
from _fakes import FakeVisionClient

from app.llm.sales import extract_sales_invoice
from app.models import Role, VoucherStatus
from app.services import vouchers as svc
from app.services.auth import create_user


# --- pure accounting helpers ------------------------------------------------ #
def test_compute_amounts_variants():
    assert svc.compute_amounts(total=113, tax=13) == (113.0, 13.0, 100.0)
    assert svc.compute_amounts(total=113, rate_str="13%") == (113.0, 13.0, 100.0)
    assert svc.compute_amounts(net=100, tax=13) == (113.0, 13.0, 100.0)
    assert svc.compute_amounts(total=100, tax=0) == (100.0, 0.0, 100.0)


def test_lines_from_amounts_balanced():
    lines = svc.lines_from_amounts(113.0, 13.0, 100.0, "钢材", "应收账款")
    assert len(lines) == 3
    assert lines[0]["account"] == "应收账款" and lines[0]["debit"] == 113.0
    assert round(sum(l["debit"] for l in lines), 2) == round(sum(l["credit"] for l in lines), 2)


def test_lines_from_amounts_no_tax():
    lines = svc.lines_from_amounts(100.0, 0.0, 100.0, None, "银行存款")
    assert len(lines) == 2  # no VAT line when tax == 0


# --- invoice + voucher creation -------------------------------------------- #
def test_create_sales_invoice_dedup(db):
    u = create_user(db, "user1", "secret1")
    svc.create_sales_invoice(db, u, invoice_number="FP-1", total_amount=113, tax_amount=13)
    with pytest.raises(svc.DuplicateInvoiceError):
        svc.create_sales_invoice(db, u, invoice_number=" fp-1 ", total_amount=113, tax_amount=13)


def test_create_sales_invoice_requires_number(db):
    u = create_user(db, "user1", "secret1")
    with pytest.raises(svc.VoucherError):
        svc.create_sales_invoice(db, u, total_amount=100)


def test_generate_voucher_is_balanced(db):
    u = create_user(db, "user1", "secret1")
    inv, voucher = svc.create_invoice_and_voucher(
        db, u, debit_account="应收账款", invoice_number="FP-2",
        total_amount=226, tax_amount=26, goods="设备",
    )
    assert voucher.voucher_no == 1
    assert voucher.code.startswith("记-")
    assert voucher.is_balanced
    assert voucher.total_debit == 226.0 and voucher.total_credit == 226.0
    assert len(voucher.entries) == 3


def test_voucher_no_increments_per_period(db):
    u = create_user(db, "user1", "secret1")
    _, v1 = svc.create_invoice_and_voucher(db, u, debit_account="应收账款",
                                           invoice_number="A1", total_amount=113,
                                           tax_amount=13, invoice_date=None)
    _, v2 = svc.create_invoice_and_voucher(db, u, debit_account="应收账款",
                                           invoice_number="A2", total_amount=113,
                                           tax_amount=13, invoice_date=None)
    assert {v1.voucher_no, v2.voucher_no} == {1, 2}


def test_update_voucher_rejects_unbalanced(db):
    u = create_user(db, "user1", "secret1")
    _, v = svc.create_invoice_and_voucher(db, u, debit_account="应收账款",
                                          invoice_number="B1", total_amount=113, tax_amount=13)
    bad = [{"account": "应收账款", "debit": 113, "credit": 0},
           {"account": "主营业务收入", "debit": 0, "credit": 50}]
    with pytest.raises(svc.UnbalancedError):
        svc.update_voucher_entries(db, u, v, lines=bad)
    good = [{"account": "银行存款", "debit": 113, "credit": 0},
            {"account": "主营业务收入", "debit": 0, "credit": 113}]
    svc.update_voucher_entries(db, u, v, lines=good)
    assert v.total_debit == 113.0 and v.is_balanced


def test_post_requires_admin_and_balance(db):
    u = create_user(db, "user1", "secret1")
    admin = create_user(db, "root", "secret1", role=Role.admin)
    _, v = svc.create_invoice_and_voucher(db, u, debit_account="应收账款",
                                          invoice_number="C1", total_amount=113, tax_amount=13)
    with pytest.raises(svc.PermissionDenied):
        svc.post_voucher(db, u, v)
    svc.post_voucher(db, admin, v)
    assert v.status == VoucherStatus.posted and v.reviewer_id == admin.id


def test_list_vouchers_scoped(db):
    a = create_user(db, "alice", "secret1")
    b = create_user(db, "bob", "secret1")
    admin = create_user(db, "root", "secret1", role=Role.admin)
    svc.create_invoice_and_voucher(db, a, debit_account="应收账款",
                                   invoice_number="S1", total_amount=113, tax_amount=13)
    svc.create_invoice_and_voucher(db, b, debit_account="应收账款",
                                   invoice_number="S2", total_amount=113, tax_amount=13)
    assert svc.list_vouchers(db, a)[1] == 1
    assert svc.list_vouchers(db, b)[1] == 1
    assert svc.list_vouchers(db, admin)[1] == 2


# --- extraction ------------------------------------------------------------- #
def test_extract_sales_invoice_parses():
    content = (
        '{"invoice_number":"012001","invoice_date":"2026-06-01","buyer":"甲公司",'
        '"seller":"乙公司","total_amount":"1130","tax_amount":"130","tax_rate":"13%",'
        '"goods":"钢材","extra_fields":{"校验码":"998877"}}'
    )
    r = extract_sales_invoice(b"img", client=FakeVisionClient(content))
    assert r.llm_used and r.invoice_number == "012001"
    assert r.total_amount == 1130.0 and r.tax_amount == 130.0
    assert r.extra_fields == {"校验码": "998877"}


# --- HTTP end-to-end -------------------------------------------------------- #
def test_sales_to_voucher_http(client):
    client.post("/register", data={"username": "alice", "password": "secret1"})
    r = client.post("/sales", data={
        "invoice_number": "HTTP-1", "total_amount": "1130", "tax_amount": "130",
        "tax_rate": "13%", "goods": "服务费", "debit_account": "应收账款",
    })
    detail = client.get(str(r.url)).text
    assert "记账凭证" in detail and "借贷平衡" in detail
    assert "应收账款" in detail and "主营业务收入" in detail
    csv = client.get("/vouchers/export.csv").text
    assert "凭证号" in csv and "应收账款" in csv
