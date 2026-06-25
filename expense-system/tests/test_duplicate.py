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


def test_distinct_numbers_ok(db):
    user = create_user(db, "bob", "secret1")
    svc.create_expense(db, user, receipt_number="A1", amount=1)
    svc.create_expense(db, user, receipt_number="A2", amount=2)
    _, total = svc.list_expenses(db, user)
    assert total == 2


def test_einvoice_requires_receipt_number(db):
    user = create_user(db, "bob", "secret1")
    with pytest.raises(svc.ExpenseError):
        svc.create_expense(db, user, ticket_type="einvoice", amount=5)  # no number


def test_payment_requires_image(db):
    user = create_user(db, "bob", "secret1")
    with pytest.raises(svc.ExpenseError):
        svc.create_expense(db, user, ticket_type="payment", amount=5)  # no image hash


def test_payment_duplicate_image_blocked(db):
    a = create_user(db, "alice", "secret1")
    b = create_user(db, "bob", "secret1")
    svc.create_expense(db, a, ticket_type="payment", image_hash="abc123", amount=10)
    # The same screenshot (identical bytes -> identical hash) cannot be reused.
    with pytest.raises(svc.DuplicateImageError):
        svc.create_expense(db, b, ticket_type="payment", image_hash="abc123", amount=10)


def test_payment_distinct_images_ok(db):
    user = create_user(db, "bob", "secret1")
    e1 = svc.create_expense(db, user, ticket_type="payment", image_hash="h1", amount=1)
    e2 = svc.create_expense(db, user, ticket_type="payment", image_hash="h2", amount=2)
    assert e1.ticket_type == "payment"
    assert e2.image_hash == "h2"
