"""Registration, login and logout routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User
from ..security import get_current_user, login_user, logout_user
from ..services import auth as auth_service
from ..services import audit
from ..templating import render

router = APIRouter()


@router.get("/login")
def login_form(request: Request, user: User | None = Depends(get_current_user)):
    if user:
        return RedirectResponse("/", status_code=303)
    return render(request, "login.html")


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    try:
        user = auth_service.authenticate(db, username, password)
    except auth_service.AuthError as exc:
        return render(request, "login.html", error=str(exc), username=username)
    login_user(request, user)
    audit.log(db, user, "login")
    return RedirectResponse("/", status_code=303)


@router.get("/register")
def register_form(request: Request, user: User | None = Depends(get_current_user)):
    if user:
        return RedirectResponse("/", status_code=303)
    return render(request, "register.html")


@router.post("/register")
def register_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    full_name: str = Form(""),
    email: str = Form(""),
    db: Session = Depends(get_db),
):
    try:
        user = auth_service.create_user(
            db, username, password, full_name=full_name, email=email
        )
    except auth_service.AuthError as exc:
        return render(
            request,
            "register.html",
            error=str(exc),
            username=username,
            full_name=full_name,
            email=email,
        )
    login_user(request, user)
    audit.log(db, user, "register")
    return RedirectResponse("/", status_code=303)


@router.get("/logout")
@router.post("/logout")
def logout(request: Request):
    logout_user(request)
    return RedirectResponse("/login", status_code=303)
