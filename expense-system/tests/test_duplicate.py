import pytest

from app.services import expenses as svc
from app.services.auth import create_user


def test_normalize_receipt_number():
    assert svc.normalize_receipt_number("  fp 123 ") == "FP123"
    assert svc.normalize_receipt_number("") is None
    assert svc.normalize_receipt_number(None) is None


def test_duplicate_receipt_blocked(db):
    user = create_user(db, "bob", "secret1")
    svc.create_expense(db, user, receipt_number="FP-001", amount=10)

    with pytest.raises(svc.DuplicateReceiptError):
        svc.create_expense(db, user, receipt_number="  fp-001 ", amount=20)  # same once normalized


def test_duplicate_blocked_across_users(db):
    a = create_user(db, "alice", "secret1")
    b = create_user(db, "bob", "secret1")
    svc.create_expense(db, a, receipt_number="INV9", amount=10)
    # A different user cannot reuse the same physical receipt number.
    with pytest.raises(svc.DuplicateReceiptError):
        svc.create_expense(db, b, receipt_number="INV9", amount=10)


def test_distinct_numbers_ok_and_null_allowed(db):
    user = create_user(db, "bob", "secret1")
    svc.create_expense(db, user, receipt_number="A1", amount=1)
    svc.create_expense(db, user, receipt_number="A2", amount=2)
    # Receipts without a number are allowed (multiple NULLs permitted).
    svc.create_expense(db, user, receipt_number="", amount=3)
    svc.create_expense(db, user, receipt_number=None, amount=4)
    items, total = svc.list_expenses(db, user)
    assert total == 4
