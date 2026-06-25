"""First-run data bootstrap (shared by startup auto-seed and scripts/seed.py)."""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func, select

from .database import SessionLocal
from .models import Expense, Role
from .services import auth as auth_service
from .services import expenses as expense_service

# (invoice_number, vendor, category, amount, tax, date)
SAMPLES = [
    ("FP20260115001", "蓝天咖啡", "餐饮", 86.50, 12, date.today() - timedelta(days=20)),
    ("FP20260118044", "城市地铁", "交通", 12.00, 0, date.today() - timedelta(days=17)),
    ("INV-7781", "云栖科技", "软件", 299.00, 0, date.today() - timedelta(days=12)),
    ("FP20260201777", "锦江大酒店", "住宿", 640.00, 38, date.today() - timedelta(days=6)),
    ("FP20260205213", "得力办公", "办公", 45.20, 6, date.today() - timedelta(days=3)),
]


def _add_samples(db, owner) -> None:
    for inv, vendor, cat, amt, tax, d in SAMPLES:
        try:
            expense_service.create_einvoice(
                db, owner, invoice_number=inv, vendor=vendor, category=cat,
                amount=amt, tax_amount=tax, expense_date=d, currency="CNY",
                description=f"在{vendor}的{cat}消费",
            )
        except expense_service.DuplicateInvoiceError:
            pass  # already exists from a prior run


def ensure_seed_data(*, force: bool = False, with_samples: bool = True) -> list[str]:
    """Create the demo accounts (and alice's sample e-invoices) if missing.

    Users are seeded only when the database has no users (or ``force=True``).
    Sample records are (re)added whenever alice exists but has no records — this
    also re-populates the demo after a schema migration. Returns created users.
    """
    created: list[str] = []
    db = SessionLocal()
    try:
        if force or auth_service.count_users(db) == 0:
            if not auth_service.get_by_username(db, "admin"):
                auth_service.create_user(
                    db, "admin", "admin123", full_name="系统管理员",
                    email="admin@example.com", role=Role.admin,
                )
                created.append("admin")
            if not auth_service.get_by_username(db, "alice"):
                auth_service.create_user(
                    db, "alice", "alice123", full_name="王爱丽", email="alice@example.com"
                )
                created.append("alice")

        if with_samples:
            alice = auth_service.get_by_username(db, "alice")
            if alice:
                count = db.scalar(
                    select(func.count()).select_from(Expense).where(Expense.user_id == alice.id)
                )
                if not count:
                    _add_samples(db, alice)
        return created
    finally:
        db.close()
