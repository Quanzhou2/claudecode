"""Reusable FastAPI dependencies for authentication and authorization."""
from __future__ import annotations

from fastapi import Depends

from .models import User
from .security import get_current_user


class NotAuthenticatedError(Exception):
    """Raised when a protected route is accessed without a session."""


class NotAuthorizedError(Exception):
    """Raised when a user lacks permission for a route/resource."""


def require_user(user: User | None = Depends(get_current_user)) -> User:
    if user is None:
        raise NotAuthenticatedError()
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if not user.is_admin:
        raise NotAuthorizedError()
    return user
