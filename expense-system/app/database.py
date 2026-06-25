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
    """Create all tables. Import models first so they register on Base."""
    from . import models  # noqa: F401  (ensures models are imported)

    Base.metadata.create_all(bind=engine)
    _run_light_migrations()


# Columns that may be missing on databases created by an earlier version.
# (table, column, column DDL type)
_ADDED_COLUMNS = [
    ("expenses", "payment_method", "VARCHAR(64)"),
    ("expenses", "ticket_type", "VARCHAR(16) DEFAULT 'einvoice'"),
    ("expenses", "image_hash", "VARCHAR(64)"),
]

# Unique indexes to ensure on pre-existing tables. (name, table, column)
_ADDED_UNIQUE_INDEXES = [
    ("uq_expenses_image_hash", "expenses", "image_hash"),
]


def _run_light_migrations() -> None:
    """Add new nullable columns / indexes to pre-existing SQLite tables in place.

    SQLAlchemy's create_all never ALTERs existing tables, so without this an
    upgraded app would crash on the new column. Only runs for SQLite; for other
    backends use a real migration tool.
    """
    if engine.dialect.name != "sqlite":
        return
    with engine.begin() as conn:
        for table, column, ddl in _ADDED_COLUMNS:
            cols = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
            if cols and column not in cols:
                conn.exec_driver_sql(
                    f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"
                )
        for name, table, column in _ADDED_UNIQUE_INDEXES:
            # Multiple NULLs are allowed by SQLite unique indexes, which is what
            # we want (records without an image simply aren't image-deduped).
            conn.exec_driver_sql(
                f"CREATE UNIQUE INDEX IF NOT EXISTS {name} ON {table}({column})"
            )
