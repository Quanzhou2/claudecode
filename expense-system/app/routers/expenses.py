"""Reimbursement record routes: dashboard, upload, CRUD, review, export."""
from __future__ import annotations

import csv
import io
import uuid
from datetime import date

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..deps import require_admin, require_user
from ..llm.extraction import extract_receipt
from ..models import STATUS_LABELS, ExpenseStatus, User
from ..services import audit
from ..services import expenses as svc
from ..templating import render

router = APIRouter()
settings = get_settings()

_ALLOWED_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def _parse_date(v: str | None) -> date | None:
    v = (v or "").strip()
    if not v:
        return None
    try:
        return date.fromisoformat(v)
    except ValueError:
        return None


def _parse_float(v: str | None) -> float | None:
    v = (v or "").strip()
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _safe_upload_name(name: str) -> str | None:
    """Only allow the basenames we generate (hex + known extension)."""
    if "/" in name or "\\" in name or ".." in name:
        return None
    stem, _, ext = name.rpartition(".")
    if stem and ext and all(c in "0123456789abcdef" for c in stem):
        return name
    return None


# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #
@router.get("/")
def dashboard(
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    stats = svc.dashboard_stats(db, user)
    recent, _ = svc.list_expenses(db, user, page=1, page_size=8)
    max_month = max(stats["by_month"].values(), default=0) or 1
    max_cat = max(stats["by_category"].values(), default=0) or 1
    return render(
        request,
        "dashboard.html",
        user=user,
        stats=stats,
        recent=recent,
        max_month=max_month,
        max_cat=max_cat,
    )


# --------------------------------------------------------------------------- #
# Listing + CSV export
# --------------------------------------------------------------------------- #
@router.get("/expenses")
def list_view(
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    status: str = Query(""),
    category: str = Query(""),
    q: str = Query(""),
    date_from: str = Query(""),
    date_to: str = Query(""),
    page: int = Query(1),
):
    items, total = svc.list_expenses(
        db, user,
        status=status or None,
        category=category or None,
        q=q or None,
        date_from=_parse_date(date_from),
        date_to=_parse_date(date_to),
        page=page,
        page_size=20,
    )
    pages = max(1, (total + 19) // 20)
    return render(
        request, "expenses_list.html", user=user,
        items=items, total=total, page=page, pages=pages,
        categories=svc.CATEGORIES, statuses=[s.value for s in ExpenseStatus],
        filters={"status": status, "category": category, "q": q,
                 "date_from": date_from, "date_to": date_to},
    )


@router.get("/expenses/export.csv")
def export_csv(
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    status: str = Query(""),
    category: str = Query(""),
    q: str = Query(""),
):
    items, _ = svc.list_expenses(
        db, user, status=status or None, category=category or None,
        q=q or None, page=1, page_size=100000,
    )
    buf = io.StringIO()
    buf.write("﻿")  # UTF-8 BOM so Excel reads Chinese correctly
    writer = csv.writer(buf)
    writer.writerow(["编号", "提交人", "发票号码", "商户", "日期", "金额",
                     "币种", "分类", "税额", "状态", "描述"])
    for e in items:
        writer.writerow([
            e.id, e.owner.username if e.owner else "", e.receipt_number or "",
            e.vendor or "", e.expense_date or "", e.amount, e.currency,
            e.category or "", e.tax_amount or "", STATUS_LABELS.get(e.status.value, e.status.value),
            e.description or "",
        ])
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=expenses.csv"},
    )


# --------------------------------------------------------------------------- #
# Upload -> extract -> review -> create
# --------------------------------------------------------------------------- #
@router.get("/expenses/new")
def new_form(request: Request, user: User = Depends(require_user)):
    return render(request, "expense_new.html", user=user)


@router.post("/expenses/extract")
async def extract(
    request: Request,
    file: UploadFile = File(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    content = await file.read()
    if file.content_type not in _ALLOWED_IMAGE_TYPES:
        return render(request, "expense_new.html", user=user,
                      error="不支持的文件类型，请上传 JPG、PNG、WEBP 或 GIF 图片。")
    if len(content) > settings.max_upload_bytes:
        return render(request, "expense_new.html", user=user,
                      error=f"文件过大（最大 {settings.max_upload_mb} MB）。")

    ext = _ALLOWED_IMAGE_TYPES[file.content_type]
    filename = f"{uuid.uuid4().hex}{ext}"
    (settings.upload_path / filename).write_bytes(content)

    extraction = extract_receipt(content, file.content_type)
    duplicate = svc.find_by_receipt_number(db, extraction.receipt_number)

    return render(
        request, "expense_review.html", user=user,
        data=extraction, image_filename=filename, raw=extraction.model_dump_json(),
        duplicate=duplicate, categories=svc.CATEGORIES,
    )


@router.post("/expenses")
def create(
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    receipt_number: str = Form(""),
    vendor: str = Form(""),
    expense_date: str = Form(""),
    amount: str = Form(""),
    currency: str = Form(""),
    category: str = Form(""),
    tax_amount: str = Form(""),
    description: str = Form(""),
    image_path: str = Form(""),
    raw: str = Form(""),
):
    image_name = _safe_upload_name(image_path) if image_path else None
    try:
        expense = svc.create_expense(
            db, user,
            raw=raw or None,
            receipt_number=receipt_number,
            vendor=vendor,
            expense_date=_parse_date(expense_date),
            amount=_parse_float(amount) or 0,
            currency=currency or settings.default_currency,
            category=category,
            tax_amount=_parse_float(tax_amount),
            description=description,
            image_path=image_name,
        )
    except svc.DuplicateReceiptError as exc:
        return render(
            request, "expense_review.html", user=user,
            error=str(exc), duplicate=exc.existing, categories=svc.CATEGORIES,
            image_filename=image_name, raw=raw,
            data={
                "receipt_number": receipt_number, "vendor": vendor,
                "expense_date": expense_date, "amount": amount, "currency": currency,
                "category": category, "tax_amount": tax_amount, "description": description,
            },
        )
    audit.log(db, user, "create_expense", "expense", expense.id,
              f"amount={expense.amount} {expense.currency}")
    return RedirectResponse(f"/expenses/{expense.id}", status_code=303)


# --------------------------------------------------------------------------- #
# Detail / edit / delete / review / image
# --------------------------------------------------------------------------- #
@router.get("/expenses/{expense_id}")
def detail(
    request: Request, expense_id: int,
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    expense = svc.get_for_user(db, user, expense_id)
    return render(request, "expense_detail.html", user=user, expense=expense,
                  can_edit=svc.can_edit(user, expense), statuses=ExpenseStatus)


@router.get("/expenses/{expense_id}/edit")
def edit_form(
    request: Request, expense_id: int,
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    expense = svc.get_for_user(db, user, expense_id)
    if not svc.can_edit(user, expense):
        return render(request, "error.html", user=user,
                      title="无法编辑",
                      message="该记录已被审核，无法再编辑。")
    return render(request, "expense_edit.html", user=user, expense=expense,
                  categories=svc.CATEGORIES)


@router.post("/expenses/{expense_id}/edit")
def edit_submit(
    request: Request, expense_id: int,
    user: User = Depends(require_user), db: Session = Depends(get_db),
    receipt_number: str = Form(""), vendor: str = Form(""),
    expense_date: str = Form(""), amount: str = Form(""),
    currency: str = Form(""), category: str = Form(""),
    tax_amount: str = Form(""), description: str = Form(""),
):
    expense = svc.get_for_user(db, user, expense_id)
    try:
        svc.update_expense(
            db, user, expense,
            receipt_number=receipt_number, vendor=vendor,
            expense_date=_parse_date(expense_date), amount=_parse_float(amount),
            currency=currency, category=category,
            tax_amount=_parse_float(tax_amount), description=description,
        )
    except svc.DuplicateReceiptError as exc:
        return render(request, "expense_edit.html", user=user, expense=expense,
                      categories=svc.CATEGORIES, error=str(exc))
    except svc.PermissionDenied as exc:
        return render(request, "error.html", user=user,
                      title="拒绝访问", message=str(exc))
    audit.log(db, user, "update_expense", "expense", expense.id)
    return RedirectResponse(f"/expenses/{expense.id}", status_code=303)


@router.post("/expenses/{expense_id}/review")
def review_submit(
    request: Request, expense_id: int,
    admin: User = Depends(require_admin), db: Session = Depends(get_db),
    status: str = Form(...), comment: str = Form(""),
):
    expense = svc.get_for_user(db, admin, expense_id)
    svc.review_expense(db, admin, expense, ExpenseStatus(status), comment)
    audit.log(db, admin, "review_expense", "expense", expense.id, f"status={status}")
    return RedirectResponse(f"/expenses/{expense.id}", status_code=303)


@router.post("/expenses/{expense_id}/delete")
def delete_submit(
    expense_id: int,
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    expense = svc.get_for_user(db, user, expense_id)
    svc.delete_expense(db, user, expense)
    audit.log(db, user, "delete_expense", "expense", expense_id)
    return RedirectResponse("/expenses", status_code=303)


@router.get("/expenses/{expense_id}/image")
def expense_image(
    expense_id: int,
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    expense = svc.get_for_user(db, user, expense_id)
    if not expense.image_path:
        return Response(status_code=404)
    path = settings.upload_path / expense.image_path
    if not path.exists():
        return Response(status_code=404)
    return FileResponse(path)


@router.get("/expenses/preview/{filename}")
def preview_image(
    filename: str,
    user: User = Depends(require_user),
):
    """Serve a just-uploaded (not yet saved) image during the review step."""
    safe = _safe_upload_name(filename)
    if not safe:
        return Response(status_code=404)
    path = settings.upload_path / safe
    if not path.exists():
        return Response(status_code=404)
    return FileResponse(path)
