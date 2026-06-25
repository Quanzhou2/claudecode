"""Admin submitter override + duplicate record display."""
from app.database import SessionLocal
from app.models import EInvoice, Role
from app.services.auth import create_user


def test_admin_can_submit_on_behalf_of_user(client):
    with SessionLocal() as db:
        create_user(db, "boss", "secret1", role=Role.admin)
        create_user(db, "emp", "secret1")
    client.post("/login", data={"username": "boss", "password": "secret1"})
    r = client.post("/expenses", data={
        "ticket_type": "einvoice", "number": "INV-X", "amount": "50",
        "currency": "CNY", "submitter": "emp",
    })
    assert "记录 #" in client.get(str(r.url)).text
    with SessionLocal() as db:
        e = db.query(EInvoice).filter(EInvoice.invoice_number == "INV-X").one()
        assert e.owner.username == "emp"  # owned by the chosen submitter


def test_non_admin_cannot_spoof_submitter(client):
    with SessionLocal() as db:
        create_user(db, "emp", "secret1")
        create_user(db, "victim", "secret1")
    client.post("/login", data={"username": "emp", "password": "secret1"})
    client.post("/expenses", data={
        "ticket_type": "einvoice", "number": "INV-Y", "amount": "5",
        "currency": "CNY", "submitter": "victim",
    })
    with SessionLocal() as db:
        e = db.query(EInvoice).filter(EInvoice.invoice_number == "INV-Y").one()
        assert e.owner.username == "emp"  # spoofed submitter ignored


def test_admin_sees_submitter_selector(client):
    with SessionLocal() as db:
        create_user(db, "boss", "secret1", role=Role.admin)
    client.post("/login", data={"username": "boss", "password": "secret1"})
    assert 'name="submitter"' in client.get("/expenses/manual").text


def test_duplicate_shows_existing_record_link(client):
    client.post("/register", data={"username": "alice", "password": "secret1"})
    client.post("/expenses", data={
        "ticket_type": "einvoice", "number": "DUP-9", "amount": "5", "currency": "CNY"})
    r = client.post("/expenses", data={
        "ticket_type": "einvoice", "number": "DUP-9", "amount": "6", "currency": "CNY"})
    assert "检测到重复" in r.text
    assert 'href="/expenses/1"' in r.text   # links to the conflicting record
    assert "alice" in r.text                # owner shown (alice can view her own)
