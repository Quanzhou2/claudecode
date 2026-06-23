"""User registration and authentication."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Role, User
from ..security import hash_password, verify_password


class AuthError(Exception):
    """Raised for registration/login problems with a user-facing message."""


def get_by_username(db: Session, username: str) -> User | None:
    return db.scalar(select(User).where(User.username == username))


def create_user(
    db: Session,
    username: str,
    password: str,
    *,
    full_name: str | None = None,
    email: str | None = None,
    role: Role = Role.user,
) -> User:
    username = (username or "").strip()
    if len(username) < 3:
        raise AuthError("Username must be at least 3 characters.")
    if len(password) < 6:
        raise AuthError("Password must be at least 6 characters.")
    if get_by_username(db, username):
        raise AuthError("That username is already taken.")
    if email:
        existing = db.scalar(select(User).where(User.email == email))
        if existing:
            raise AuthError("That email is already registered.")

    user = User(
        username=username,
        full_name=(full_name or "").strip() or None,
        email=(email or "").strip() or None,
        hashed_password=hash_password(password),
        role=role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate(db: Session, username: str, password: str) -> User:
    user = get_by_username(db, (username or "").strip())
    if not user or not verify_password(password, user.hashed_password):
        raise AuthError("Invalid username or password.")
    if not user.is_active:
        raise AuthError("This account has been disabled.")
    return user


def count_users(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(User)) or 0
