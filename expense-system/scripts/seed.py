"""Seed the database with demo accounts and sample reimbursement records.

Run from anywhere:  python -m scripts.seed

(The app also auto-seeds these accounts on first startup unless AUTO_SEED=false,
so running this manually is optional.)
"""
from __future__ import annotations

import sys
from pathlib import Path

# Allow running as a plain script (python scripts/seed.py).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.bootstrap import ensure_seed_data  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.database import init_db  # noqa: E402


def main() -> None:
    settings = get_settings()
    init_db()
    created = ensure_seed_data(force=True)
    if created:
        print(f"已创建账号：{', '.join(created)}")
    else:
        print("账号已存在，未做更改")
    print("管理员：admin / admin123")
    print("用户：  alice / alice123")
    print(f"数据库：{settings.database_url}")
    print("请访问 http://localhost:8000/login 登录")


if __name__ == "__main__":
    main()
