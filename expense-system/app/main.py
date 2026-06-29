"""FastAPI application entry point."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .config import get_settings
from .database import init_db
from .deps import NotAuthenticatedError, NotAuthorizedError
from .routers import admin, analytics, auth, expenses, vouchers
from .security import get_current_user
from .services.expenses import ExpenseError, PermissionDenied
from .templating import render

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    settings.upload_path  # ensure upload dir exists
    if settings.auto_seed:
        try:
            from .bootstrap import ensure_seed_data

            created = ensure_seed_data()
            if created:
                print(f"[bootstrap] created demo accounts: {', '.join(created)} "
                      "(admin/admin123, alice/alice123) — change these in production")
        except Exception as exc:  # noqa: BLE001 — never block startup on seeding
            print(f"[bootstrap] skipped auto-seed: {exc}")
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, same_site="lax")

_STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


# --------------------------------------------------------------------------- #
# Auth exception handling: redirect anonymous users, 403 page for forbidden.
# --------------------------------------------------------------------------- #
@app.exception_handler(NotAuthenticatedError)
async def _not_authenticated(request: Request, exc: NotAuthenticatedError):
    return RedirectResponse("/login", status_code=303)


def _error_page(request: Request, status: int, title: str, message: str):
    """Render the friendly error template with the current user's navbar."""
    try:
        from .database import SessionLocal

        with SessionLocal() as db:
            user = get_current_user(request, db)
        response = render(request, "error.html", user=user, title=title, message=message)
        response.status_code = status
        return response
    except Exception:  # noqa: BLE001
        return JSONResponse({"detail": message}, status_code=status)


@app.exception_handler(NotAuthorizedError)
async def _not_authorized(request: Request, exc: NotAuthorizedError):
    return _error_page(
        request, 403, "拒绝访问", "您需要管理员权限才能访问该页面。"
    )


@app.exception_handler(PermissionDenied)
async def _permission_denied(request: Request, exc: PermissionDenied):
    return _error_page(request, 403, "拒绝访问", str(exc) or "没有权限。")


@app.exception_handler(ExpenseError)
async def _expense_error(request: Request, exc: ExpenseError):
    return _error_page(request, 404, "未找到", str(exc) or "未找到该记录。")


@app.get("/healthz")
def healthz():
    return {"status": "ok", "llm_enabled": settings.llm_enabled}


app.include_router(auth.router)
app.include_router(expenses.router)
app.include_router(analytics.router)
app.include_router(vouchers.router)
app.include_router(admin.router)
