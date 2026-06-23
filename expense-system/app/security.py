"""Password hashing and session-based authentication helpers."""
from __future__ import annotations

import bcrypt
from fastapi import Depends, Request
from sqlalchemy.orm import Session

from .database import get_db
from .models import Role, User

SESSION_USER_KEY = "user_id"


# --------------------------------------------------------------------------- #
# Password hashing (bcrypt directly — avoids passlib/bcrypt version friction)
# --------------------------------------------------------------------------- #
def hash_password(password: str) -> str:
    # bcrypt operates on the first 72 bytes; encode to bytes explicitly.
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# --------------------------------------------------------------------------- #
# Session helpers
# --------------------------------------------------------------------------- #
def login_user(request: Request, user: User) -> None:
    request.session[SESSION_USER_KEY] = user.id


def logout_user(request: Request) -> None:
    request.session.pop(SESSION_USER_KEY, None)


def get_current_user(
    request: Request, db: Session = Depends(get_db)
) -> User | None:
    """Return the logged-in user, or None. Never raises."""
    user_id = request.session.get(SESSION_USER_KEY)
    if not user_id:
        return None
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        # Stale / disabled session — clear it.
        request.session.pop(SESSION_USER_KEY, None)
        return None
    return user
