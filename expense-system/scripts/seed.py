"""Seed the database with demo accounts and sample reimbursement records.

Run from the project root:  python -m scripts.seed
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

# Allow running as a plain script (python scripts/seed.py).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal, init_db  # noqa: E402
from app.models import Role  # noqa: E402
from app.services import auth as auth_service  # noqa: E402
from app.services import expenses as expense_service  # noqa: E402

SAMPLES = [
    ("FP20260115001", "Sky Cafe", "Meals", 86.50, 12, date.today() - timedelta(days=20)),
    ("FP20260118044", "MetroRail", "Transport", 12.00, 0, date.today() - timedelta(days=17)),
    ("INV-7781", "CloudHost Inc", "Software", 299.00, 0, date.today() - timedelta(days=12)),
    ("FP20260201777", "Grand Hotel", "Lodging", 640.00, 38, date.today() - timedelta(days=6)),
    ("FP20260205213", "Office Depot", "Office", 45.20, 6, date.today() - timedelta(days=3)),
]


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        if not auth_service.get_by_username(db, "admin"):
            auth_service.create_user(
                db, "admin", "admin123", full_name="System Administrator",
                email="admin@example.com", role=Role.admin,
            )
            print("Created admin / admin123")
        else:
            print("admin already exists")

        alice = auth_service.get_by_username(db, "alice")
        if not alice:
            alice = auth_service.create_user(
                db, "alice", "alice123", full_name="Alice Wong", email="alice@example.com"
            )
            print("Created alice / alice123")
            for rn, vendor, cat, amt, tax, d in SAMPLES:
                expense_service.create_expense(
                    db, alice, receipt_number=rn, vendor=vendor, category=cat,
                    amount=amt, tax_amount=tax, expense_date=d, currency="CNY",
                    description=f"{cat} expense at {vendor}",
                )
            print(f"Added {len(SAMPLES)} sample records for alice")
        else:
            print("alice already exists")

        print("\nDone. Log in at http://localhost:8000/login")
    finally:
        db.close()


if __name__ == "__main__":
    main()
