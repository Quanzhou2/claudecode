"""Shared Jinja2 templates instance, filters and a render helper."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates

from .config import get_settings
from .models import (
    ACTION_LABELS,
    ENTITY_LABELS,
    ROLE_LABELS,
    STATUS_LABELS,
    TICKET_TYPE_LABELS,
    VOUCHER_STATUS_LABELS,
    User,
)

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


def _status_label(status: Any) -> str:
    key = getattr(status, "value", status)
    return STATUS_LABELS.get(str(key), str(key))


def _role_label(role: Any) -> str:
    key = getattr(role, "value", role)
    return ROLE_LABELS.get(str(key), str(key))


def _action_label(action: Any) -> str:
    return ACTION_LABELS.get(str(action), str(action))


def _entity_label(entity: Any) -> str:
    if not entity:
        return ""
    return ENTITY_LABELS.get(str(entity), str(entity))


def _ticket_type_label(value: Any) -> str:
    return TICKET_TYPE_LABELS.get(str(value), str(value))


def _voucher_status_label(value: Any) -> str:
    key = getattr(value, "value", value)
    return VOUCHER_STATUS_LABELS.get(str(key), str(key))


def _json_zh(value: Any) -> str:
    """Pretty JSON with Chinese kept readable (no \\uXXXX escaping)."""
    import json

    if not value:
        return ""
    return json.dumps(value, ensure_ascii=False, indent=2)


templates.env.filters["money"] = _money
templates.env.filters["status_class"] = _status_class
templates.env.filters["status_label"] = _status_label
templates.env.filters["role_label"] = _role_label
templates.env.filters["action_label"] = _action_label
templates.env.filters["entity_label"] = _entity_label
templates.env.filters["ticket_type_label"] = _ticket_type_label
templates.env.filters["voucher_status_label"] = _voucher_status_label
templates.env.filters["json_zh"] = _json_zh


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
