"""Lightweight append-only audit logging."""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import AuditLog, User


def log(
    db: Session,
    actor: User | None,
    action: str,
    entity_type: str | None = None,
    entity_id: int | None = None,
    detail: str | None = None,
) -> None:
    db.add(
        AuditLog(
            actor_id=actor.id if actor else None,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            detail=detail,
        )
    )
    db.commit()
