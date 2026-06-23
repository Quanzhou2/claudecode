import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import Role
from app.services import expenses as svc
from app.services.auth import create_user


def test_user_cannot_view_others_record(db):
    a = create_user(db, "alice", "secret1")
    b = create_user(db, "bob", "secret1")
    admin = create_user(db, "root", "secret1", role=Role.admin)
    e = svc.create_expense(db, a, receipt_number="R1", amount=50)

    # Owner and admin can view; the other user cannot.
    assert svc.get_for_user(db, a, e.id).id == e.id
    assert svc.get_for_user(db, admin, e.id).id == e.id
    with pytest.raises(svc.PermissionDenied):
        svc.get_for_user(db, b, e.id)


def test_list_is_scoped(db):
    a = create_user(db, "alice", "secret1")
    b = create_user(db, "bob", "secret1")
    admin = create_user(db, "root", "secret1", role=Role.admin)
    svc.create_expense(db, a, receipt_number="R1", amount=10)
    svc.create_expense(db, b, receipt_number="R2", amount=20)

    _, total_a = svc.list_expenses(db, a)
    _, total_b = svc.list_expenses(db, b)
    _, total_admin = svc.list_expenses(db, admin)
    assert total_a == 1
    assert total_b == 1
    assert total_admin == 2  # admin sees everyone's records


def test_owner_cannot_edit_after_review(db):
    a = create_user(db, "alice", "secret1")
    admin = create_user(db, "root", "secret1", role=Role.admin)
    e = svc.create_expense(db, a, receipt_number="R1", amount=10)
    from app.models import ExpenseStatus

    svc.review_expense(db, admin, e, ExpenseStatus.approved, "ok")
    assert not svc.can_edit(a, e)       # locked once approved
    assert svc.can_edit(admin, e)       # admin can always edit


def test_cross_user_http_access_returns_403(client):
    # client fixture has reset the DB; reuse it as user "owner".
    client.post("/register", data={"username": "owner", "password": "secret1"})
    r = client.post("/expenses", data={"receipt_number": "Z9", "amount": "12", "currency": "CNY"})
    expense_url = str(r.url)
    assert "/expenses/" in expense_url

    other = TestClient(app)  # separate session, same app + DB
    other.post("/register", data={"username": "intruder", "password": "secret1"})
    resp = other.get(expense_url, follow_redirects=False)
    assert resp.status_code == 403
