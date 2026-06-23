"""Shared Jinja2 templates instance, filters and a render helper."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates

from .config import get_settings
from .models import User

_TEMPLATE_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))

_STATUS_CLASS = {
    "pending": "badge-pending",
    "approved": "badge-approved",
    "rejected": "badge-rejected",
    "paid": "badge-paid",
}


def _money(value: Any) -> str:
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return "0.00"


def _status_class(status: Any) -> str:
    key = getattr(status, "value", status)
    return _STATUS_CLASS.get(str(key), "badge-pending")


templates.env.filters["money"] = _money
templates.env.filters["status_class"] = _status_class


def render(request: Request, name: str, user: User | None = None, **context: Any):
    """Render a template with common context injected."""
    settings = get_settings()
    base = {
        "request": request,
        "current_user": user,
        "app_name": settings.app_name,
        "llm_enabled": settings.llm_enabled,
        "default_currency": settings.default_currency,
    }
    base.update(context)
    return templates.TemplateResponse(request, name, base)
