from app.services import expenses as svc
from app.services.auth import create_user


def test_list_filter_by_type_and_counts(db):
    u = create_user(db, "bob", "secret1")
    svc.create_einvoice(db, u, invoice_number="A1", amount=1)
    svc.create_einvoice(db, u, invoice_number="A2", amount=2)
    svc.create_payment(db, u, image_phash="0f0f0f0f0f0f0f0f", amount=3)

    assert svc.count_by_type(db, u) == {"all": 3, "einvoice": 2, "payment": 1}

    ei, ei_total = svc.list_expenses(db, u, ticket_type="einvoice")
    assert ei_total == 2 and all(e.ticket_type == "einvoice" for e in ei)

    pv, pv_total = svc.list_expenses(db, u, ticket_type="payment")
    assert pv_total == 1 and pv[0].ticket_type == "payment"

    _, all_total = svc.list_expenses(db, u)
    assert all_total == 3


def test_count_by_type_respects_other_filters(db):
    u = create_user(db, "bob", "secret1")
    svc.create_einvoice(db, u, invoice_number="A1", amount=1, category="餐饮")
    svc.create_einvoice(db, u, invoice_number="A2", amount=2, category="交通")
    svc.create_payment(db, u, image_phash="0f0f0f0f0f0f0f0f", amount=3, category="餐饮")

    counts = svc.count_by_type(db, u, category="餐饮")
    assert counts == {"all": 2, "einvoice": 1, "payment": 1}
