"""Database engine, session factory and declarative base."""
from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings

settings = get_settings()

# check_same_thread is only relevant for SQLite; harmless to pass conditionally.
_connect_args = (
    {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
)

engine = create_engine(
    settings.database_url,
    connect_args=_connect_args,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create all tables and migrate any legacy single-table schema in place."""
    from . import models  # noqa: F401  (ensures models are imported)

    legacy_rows = _legacy_extract_and_drop()
    Base.metadata.create_all(bind=engine)
    if legacy_rows:
        _legacy_insert(legacy_rows)
    _add_missing_columns()


# New nullable columns to add to existing tables. (table, column, DDL type)
_ADDED_COLUMNS = [
    ("e_invoices", "extra_fields", "TEXT"),
]


def _add_missing_columns() -> None:
    """Add newly-introduced nullable columns to existing SQLite tables."""
    if not _is_sqlite():
        return
    with engine.begin() as conn:
        for table, column, ddl in _ADDED_COLUMNS:
            exists = conn.exec_driver_sql(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=:n", {"n": table}
            ).first()
            if not exists:
                continue
            cols = {r[1] for r in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
            if column not in cols:
                conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def _is_sqlite() -> bool:
    return engine.dialect.name == "sqlite"


def _legacy_extract_and_drop() -> list[dict] | None:
    """Read rows from an old single-table `expenses` then drop it (SQLite only).

    Dropping the table (rather than renaming it) clears its indexes too, so the
    fresh joined-inheritance tables can be created without index-name clashes.
    Detected by the presence of the old `receipt_number` column.
    """
    if not _is_sqlite():
        return None

    def _table_exists(conn, name: str) -> bool:
        return bool(conn.exec_driver_sql(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=:n", {"n": name}
        ).first())

    def _has_col(conn, table: str, col: str) -> bool:
        return col in {r[1] for r in conn.exec_driver_sql(f"PRAGMA table_info({table})")}

    with engine.begin() as conn:
        # The legacy single-table data is in `expenses` (clean upgrade) or in
        # `expenses_legacy` (recovering from an aborted earlier migration).
        if _table_exists(conn, "expenses") and _has_col(conn, "expenses", "receipt_number"):
            source = "expenses"
        elif _table_exists(conn, "expenses_legacy"):
            source = "expenses_legacy"
        else:
            return None
        rows = [dict(r) for r in conn.exec_driver_sql(f"SELECT * FROM {source}").mappings()]
        # Drop the old table(s) so create_all can build the new schema cleanly.
        conn.exec_driver_sql("DROP TABLE IF EXISTS expenses_legacy")
        if source == "expenses":
            conn.exec_driver_sql("DROP TABLE expenses")
        elif _table_exists(conn, "expenses"):
            # Half-created new base table from an aborted run — start it fresh.
            conn.exec_driver_sql("DROP TABLE IF EXISTS e_invoices")
            conn.exec_driver_sql("DROP TABLE IF EXISTS payment_vouchers")
            conn.exec_driver_sql("DROP TABLE expenses")
    return rows


def _legacy_insert(rows: list[dict]) -> None:
    """Insert previously-extracted single-table rows into the new tables."""
    base_cols = (
        "ticket_type", "user_id", "vendor", "expense_date", "amount", "currency",
        "category", "tax_amount", "payment_method", "description", "image_path",
        "status", "reviewer_id", "review_comment", "reviewed_at", "extracted_raw",
        "created_at", "updated_at",
    )
    placeholders = ", ".join(f":{c}" for c in base_cols)
    insert_base = (
        f"INSERT INTO expenses ({', '.join(base_cols)}) VALUES ({placeholders})"
    )
    from datetime import datetime

    now = datetime.utcnow().isoformat(sep=" ", timespec="seconds")
    with engine.begin() as conn:
        for r in rows:
            ticket_type = r.get("ticket_type") or "einvoice"
            params = {c: r.get(c) for c in base_cols}
            # Backfill NOT NULL columns in case the legacy row had gaps.
            params["ticket_type"] = ticket_type
            params["amount"] = r.get("amount") or 0
            params["currency"] = r.get("currency") or "CNY"
            params["status"] = r.get("status") or "pending"
            params["created_at"] = r.get("created_at") or now
            params["updated_at"] = r.get("updated_at") or now
            new_id = conn.exec_driver_sql(insert_base, params).lastrowid
            if ticket_type == "payment":
                conn.exec_driver_sql(
                    "INSERT INTO payment_vouchers (id, payment_number, image_phash) "
                    "VALUES (:id, :pn, NULL)",
                    {"id": new_id, "pn": r.get("receipt_number")},
                )
            else:
                conn.exec_driver_sql(
                    "INSERT INTO e_invoices (id, invoice_number) VALUES (:id, :inv)",
                    {"id": new_id, "inv": r.get("receipt_number")},
                )
