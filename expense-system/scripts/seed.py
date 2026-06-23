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
    ("FP20260115001", "蓝天咖啡", "餐饮", 86.50, 12, date.today() - timedelta(days=20)),
    ("FP20260118044", "城市地铁", "交通", 12.00, 0, date.today() - timedelta(days=17)),
    ("INV-7781", "云栖科技", "软件", 299.00, 0, date.today() - timedelta(days=12)),
    ("FP20260201777", "锦江大酒店", "住宿", 640.00, 38, date.today() - timedelta(days=6)),
    ("FP20260205213", "得力办公", "办公", 45.20, 6, date.today() - timedelta(days=3)),
]


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        if not auth_service.get_by_username(db, "admin"):
            auth_service.create_user(
                db, "admin", "admin123", full_name="系统管理员",
                email="admin@example.com", role=Role.admin,
            )
            print("已创建管理员 admin / admin123")
        else:
            print("管理员 admin 已存在")

        alice = auth_service.get_by_username(db, "alice")
        if not alice:
            alice = auth_service.create_user(
                db, "alice", "alice123", full_name="王爱丽", email="alice@example.com"
            )
            print("已创建用户 alice / alice123")
            for rn, vendor, cat, amt, tax, d in SAMPLES:
                expense_service.create_expense(
                    db, alice, receipt_number=rn, vendor=vendor, category=cat,
                    amount=amt, tax_amount=tax, expense_date=d, currency="CNY",
                    description=f"在{vendor}的{cat}消费",
                )
            print(f"已为 alice 添加 {len(SAMPLES)} 条示例记录")
        else:
            print("用户 alice 已存在")

        print("\n完成。请访问 http://localhost:8000/login 登录")
    finally:
        db.close()


if __name__ == "__main__":
    main()
