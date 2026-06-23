"""Administrator-only routes: user management and audit log."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import require_admin
from ..models import AuditLog, Expense, Role, User
from ..services import audit
from ..templating import render

router = APIRouter(prefix="/admin")


@router.get("/users")
def users_list(
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    users = list(db.scalars(select(User).order_by(User.created_at)))
    # Per-user record counts in one grouped query.
    counts = dict(
        db.execute(
            select(Expense.user_id, func.count()).group_by(Expense.user_id)
        ).all()
    )
    return render(request, "admin_users.html", user=admin, users=users, counts=counts)


@router.post("/users/{user_id}/role")
def toggle_role(
    user_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    target = db.get(User, user_id)
    if target and target.id != admin.id:  # cannot demote yourself
        target.role = Role.user if target.role == Role.admin else Role.admin
        db.commit()
        audit.log(db, admin, "set_role", "user", target.id, target.role.value)
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/users/{user_id}/active")
def toggle_active(
    user_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    target = db.get(User, user_id)
    if target and target.id != admin.id:  # cannot disable yourself
        target.is_active = not target.is_active
        db.commit()
        audit.log(db, admin, "set_active", "user", target.id, str(target.is_active))
    return RedirectResponse("/admin/users", status_code=303)


@router.get("/audit")
def audit_log(
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    logs = list(
        db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(200))
    )
    return render(request, "admin_audit.html", user=admin, logs=logs)
