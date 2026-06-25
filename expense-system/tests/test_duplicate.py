import pytest

from app.services import expenses as svc
from app.services.auth import create_user


def test_normalize_number():
    assert svc.normalize_number("  fp 123 ") == "FP123"
    assert svc.normalize_number("") is None
    assert svc.normalize_number(None) is None


# --- E-invoice: dedup by invoice number ---------------------------------- #
def test_duplicate_invoice_blocked(db):
    user = create_user(db, "bob", "secret1")
    svc.create_einvoice(db, user, invoice_number="FP-001", amount=10)
    with pytest.raises(svc.DuplicateInvoiceError):
        svc.create_einvoice(db, user, invoice_number="  fp-001 ", amount=20)


def test_duplicate_invoice_across_users(db):
    a = create_user(db, "alice", "secret1")
    b = create_user(db, "bob", "secret1")
    svc.create_einvoice(db, a, invoice_number="INV9", amount=10)
    with pytest.raises(svc.DuplicateInvoiceError):
        svc.create_einvoice(db, b, invoice_number="INV9", amount=10)


def test_einvoice_requires_invoice_number(db):
    user = create_user(db, "bob", "secret1")
    with pytest.raises(svc.ExpenseError):
        svc.create_einvoice(db, user, invoice_number="", amount=5)


# --- Payment voucher: dedup by image similarity -------------------------- #
def test_payment_requires_image(db):
    user = create_user(db, "bob", "secret1")
    with pytest.raises(svc.ExpenseError):
        svc.create_payment(db, user, image_phash=None, amount=5)


def test_payment_similar_image_blocked(db):
    a = create_user(db, "alice", "secret1")
    b = create_user(db, "bob", "secret1")
    svc.create_payment(db, a, image_phash="ffffffffffffffff", amount=10)
    # 4 differing bits out of 64 -> 93.75% similar -> over the 80% threshold.
    with pytest.raises(svc.DuplicateImageError) as exc:
        svc.create_payment(db, b, image_phash="fffffffffffffff0", amount=10)
    assert exc.value.similarity >= 0.8
    assert exc.value.existing is not None


def test_payment_distinct_images_ok(db):
    user = create_user(db, "bob", "secret1")
    p1 = svc.create_payment(db, user, image_phash="ffffffffffffffff", amount=1)
    p2 = svc.create_payment(db, user, image_phash="0000000000000000", amount=2)
    assert p1.ticket_type == "payment"
    assert p2.payment_number is None


def test_invoice_and_payment_numbers_are_independent(db):
    user = create_user(db, "bob", "secret1")
    # Same string as an invoice number and a payment number does NOT clash.
    svc.create_einvoice(db, user, invoice_number="X1", amount=1)
    pv = svc.create_payment(db, user, image_phash="abcabcabcabcabc0",
                            payment_number="X1", amount=2)
    assert pv.payment_number == "X1"
