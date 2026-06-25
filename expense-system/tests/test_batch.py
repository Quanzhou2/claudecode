"""Batch (multi-image) upload flow."""
import io

from PIL import Image, ImageDraw


def _png(seed: int) -> bytes:
    img = Image.new("RGB", (180, 280), "white")
    d = ImageDraw.Draw(img)
    for i, y in enumerate(range(15, 260, 20)):
        d.rectangle([10, y, 10 + ((i * seed) % 140) + 15, y + 11], fill=(30 + i * 9 % 200,) * 3)
    b = io.BytesIO()
    img.save(b, "PNG")
    return b.getvalue()


def test_multi_upload_goes_to_batch_review(client):
    client.post("/register", data={"username": "alice", "password": "secret1"})
    r = client.post(
        "/expenses/extract",
        data={"ticket_type": "einvoice"},
        files=[("files", ("a.png", _png(7), "image/png")),
               ("files", ("b.png", _png(19), "image/png"))],
    )
    assert r.status_code == 200
    assert "批量核对" in r.text  # batch review page, not the single review


def test_batch_save_creates_records_and_skips(client):
    client.post("/register", data={"username": "alice", "password": "secret1"})
    # Two save rows + one explicitly skipped row.
    r = client.post("/expenses/batch", data={
        "ticket_type": "einvoice",
        "number": ["FP-1", "FP-2", "FP-3"],
        "image_path": ["", "", ""],
        "amount": ["10", "20", "30"],
        "currency": ["CNY", "CNY", "CNY"],
        "action": ["save", "save", "skip"],
        "vendor": ["a", "b", "c"],
    })
    assert r.status_code == 200
    assert "已保存" in r.text
    # Two saved e-invoices show up in the list.
    listing = client.get("/expenses?type=einvoice").text
    assert "FP-1" in listing and "FP-2" in listing
    assert "FP-3" not in listing  # skipped


def test_batch_blocks_duplicate_invoice_within_batch(client):
    client.post("/register", data={"username": "alice", "password": "secret1"})
    r = client.post("/expenses/batch", data={
        "ticket_type": "einvoice",
        "number": ["DUP1", "DUP1"],   # same number twice
        "image_path": ["", ""],
        "amount": ["10", "20"],
        "currency": ["CNY", "CNY"],
        "action": ["save", "save"],
        "vendor": ["x", "y"],
    })
    # First saves, second is reported as a duplicate.
    assert "已保存" in r.text
    from app.database import SessionLocal
    from app.models import EInvoice
    with SessionLocal() as db:
        count = db.query(EInvoice).filter(EInvoice.invoice_number == "DUP1").count()
    assert count == 1
