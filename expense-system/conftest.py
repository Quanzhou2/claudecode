"""Pytest configuration: isolated temp DB + shared fixtures.

Environment variables are set *before* importing the app so that the cached
settings and the module-level engine bind to a throwaway SQLite database.
"""
from __future__ import annotations

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="expense-test-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP}/test.db")
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("UPLOAD_DIR", f"{_TMP}/uploads")
os.environ["LLM_API_KEY"] = ""  # force offline mode unless a test injects a client

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Role, User  # noqa: E402
from app.services import auth as auth_service  # noqa: E402


def _reset_db() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


@pytest.fixture
def db():
    _reset_db()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client():
    _reset_db()
    with TestClient(app) as c:
        yield c


def make_user(db, username="bob", password="secret1", role=Role.user) -> User:
    return auth_service.create_user(db, username, password, role=role)


@pytest.fixture
def make_user_factory(db):
    def _factory(username="bob", password="secret1", role=Role.user):
        return auth_service.create_user(db, username, password, role=role)

    return _factory
