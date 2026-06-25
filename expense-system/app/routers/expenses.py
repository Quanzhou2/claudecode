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
from ..imaging import perceptual_hash
from ..llm.extraction import extract_receipt, extract_receipt_from_text
from ..models import (
    STATUS_LABELS,
    TICKET_TYPE_LABELS,
    EInvoice,
    ExpenseStatus,
    User,
)
from ..schemas import ReceiptExtraction
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
_MAX_BATCH = 30  # max images processed per upload


def _at(values: list[str], i: int) -> str:
    """Safe positional access for parallel batch-form lists."""
    return values[i] if i < len(values) else ""


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
    ticket_type: str = Query("", alias="type"),
    page: int = Query(1),
):
    common = dict(
        status=status or None, category=category or None, q=q or None,
        date_from=_parse_date(date_from), date_to=_parse_date(date_to),
    )
    items, total = svc.list_expenses(
        db, user, ticket_type=ticket_type or None, page=page, page_size=20, **common
    )
    counts = svc.count_by_type(db, user, **common)
    pages = max(1, (total + 19) // 20)
    return render(
        request, "expenses_list.html", user=user,
        items=items, total=total, page=page, pages=pages, counts=counts,
        categories=svc.CATEGORIES, statuses=[s.value for s in ExpenseStatus],
        filters={"status": status, "category": category, "q": q,
                 "date_from": date_from, "date_to": date_to, "type": ticket_type},
    )


@router.get("/expenses/export.csv")
def export_csv(
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    status: str = Query(""),
    category: str = Query(""),
    q: str = Query(""),
    ticket_type: str = Query("", alias="type"),
):
    items, _ = svc.list_expenses(
        db, user, ticket_type=ticket_type or None, status=status or None,
        category=category or None, q=q or None, page=1, page_size=100000,
    )
    buf = io.StringIO()
    buf.write("﻿")  # UTF-8 BOM so Excel reads Chinese correctly
    writer = csv.writer(buf)
    writer.writerow(["编号", "类型", "提交人", "发票号码", "支付单号", "商户", "日期",
                     "金额", "币种", "分类", "支付方式", "税额", "状态", "描述"])
    for e in items:
        writer.writerow([
            e.id, TICKET_TYPE_LABELS.get(e.ticket_type, e.ticket_type),
            e.owner.username if e.owner else "",
            getattr(e, "invoice_number", "") or "", getattr(e, "payment_number", "") or "",
            e.vendor or "", e.expense_date or "", e.amount, e.currency,
            e.category or "", e.payment_method or "", e.tax_amount or "",
            STATUS_LABELS.get(e.status.value, e.status.value), e.description or "",
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


@router.get("/expenses/manual")
def manual_form(request: Request, user: User = Depends(require_user)):
    """Go straight to a blank review form for fully manual entry."""
    return render(
        request, "expense_review.html", user=user,
        data=ReceiptExtraction(), image_filename=None, raw="",
        duplicate=None, categories=svc.CATEGORIES, ticket_type="einvoice",
    )


def _process_upload(content: bytes, content_type: str, ticket_type: str, db: Session):
    """Save one uploaded image and extract a draft record. Returns (draft, error)."""
    if content_type not in _ALLOWED_IMAGE_TYPES:
        return None, "不支持的文件类型，请上传 JPG、PNG、WEBP 或 GIF 图片。"
    if len(content) > settings.max_upload_bytes:
        return None, f"文件过大（最大 {settings.max_upload_mb} MB）。"

    filename = f"{uuid.uuid4().hex}{_ALLOWED_IMAGE_TYPES[content_type]}"
    (settings.upload_path / filename).write_bytes(content)

    extraction = extract_receipt(content, content_type)
    duplicate = None
    match_score = None
    if ticket_type == "payment":
        match = svc.best_payment_match(db, perceptual_hash(content))
        if match:
            match_score = match[1]
            if match[1] >= settings.image_similarity_threshold:
                duplicate = match[0]
    else:
        duplicate = svc.find_by_invoice_number(db, extraction.receipt_number)

    return {
        "image_filename": filename, "data": extraction,
        "raw": extraction.model_dump_json(), "duplicate": duplicate,
        "match_score": match_score,
    }, None


@router.post("/expenses/extract")
async def extract(
    request: Request,
    files: list[UploadFile] = File(...),
    ticket_type: str = Form("einvoice"),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    # A single image keeps the focused single-review flow; multiple images go
    # to the batch review page (one editable row per image).
    contents = [(f.content_type, await f.read()) for f in files[:_MAX_BATCH]]

    if len(contents) == 1:
        content_type, content = contents[0]
        draft, error = _process_upload(content, content_type, ticket_type, db)
        if error:
            return render(request, "expense_new.html", user=user, error=error)
        return render(
            request, "expense_review.html", user=user,
            data=draft["data"], image_filename=draft["image_filename"],
            raw=draft["raw"], duplicate=draft["duplicate"],
            match_score=draft["match_score"], categories=svc.CATEGORIES,
            ticket_type=ticket_type,
        )

    drafts, skipped = [], 0
    for content_type, content in contents:
        draft, error = _process_upload(content, content_type, ticket_type, db)
        if draft:
            drafts.append(draft)
        else:
            skipped += 1
    if not drafts:
        return render(request, "expense_new.html", user=user,
                      error="没有可识别的有效图片。")
    return render(
        request, "expense_batch_review.html", user=user,
        drafts=drafts, skipped=skipped, categories=svc.CATEGORIES,
        ticket_type=ticket_type,
    )


@router.post("/expenses/extract-text")
def extract_text(
    request: Request,
    text: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if not text.strip():
        return render(request, "expense_new.html", user=user,
                      error="请先粘贴要识别的文字。")
    extraction = extract_receipt_from_text(text)
    duplicate = svc.find_by_invoice_number(db, extraction.receipt_number)
    return render(
        request, "expense_review.html", user=user,
        data=extraction, image_filename=None, raw=extraction.model_dump_json(),
        duplicate=duplicate, categories=svc.CATEGORIES, ticket_type="einvoice",
    )


@router.post("/expenses")
def create(
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    ticket_type: str = Form("einvoice"),
    number: str = Form(""),
    vendor: str = Form(""),
    expense_date: str = Form(""),
    amount: str = Form(""),
    currency: str = Form(""),
    category: str = Form(""),
    payment_method: str = Form(""),
    tax_amount: str = Form(""),
    description: str = Form(""),
    image_path: str = Form(""),
    raw: str = Form(""),
):
    image_name = _safe_upload_name(image_path) if image_path else None
    common = dict(
        vendor=vendor,
        expense_date=_parse_date(expense_date),
        amount=_parse_float(amount) or 0,
        currency=currency or settings.default_currency,
        category=category,
        payment_method=payment_method,
        tax_amount=_parse_float(tax_amount),
        description=description,
        image_path=image_name,
    )
    try:
        if ticket_type == "payment":
            # Hash the stored image server-side (don't trust a client value).
            phash = None
            if image_name:
                img = settings.upload_path / image_name
                if img.exists():
                    phash = perceptual_hash(img.read_bytes())
            expense = svc.create_payment(
                db, user, image_phash=phash, payment_number=number,
                raw=raw or None, **common,
            )
        else:
            expense = svc.create_einvoice(
                db, user, invoice_number=number, raw=raw or None, **common,
            )
    except svc.ExpenseError as exc:
        # Covers duplicates (invoice/image) and missing-dedup-key errors.
        return render(
            request, "expense_review.html", user=user,
            error=str(exc), duplicate=getattr(exc, "existing", None),
            match_score=getattr(exc, "similarity", None),
            categories=svc.CATEGORIES, image_filename=image_name, raw=raw,
            ticket_type=ticket_type,
            data={
                "receipt_number": number, "vendor": vendor,
                "expense_date": expense_date, "amount": amount, "currency": currency,
                "category": category, "payment_method": payment_method,
                "tax_amount": tax_amount, "description": description,
            },
        )
    audit.log(db, user, "create_expense", "expense", expense.id,
              f"{ticket_type} amount={expense.amount} {expense.currency}")
    return RedirectResponse(f"/expenses/{expense.id}", status_code=303)


@router.post("/expenses/batch")
def batch_create(
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    ticket_type: str = Form("einvoice"),
    number: list[str] = Form([]),
    image_path: list[str] = Form([]),
    vendor: list[str] = Form([]),
    expense_date: list[str] = Form([]),
    amount: list[str] = Form([]),
    currency: list[str] = Form([]),
    category: list[str] = Form([]),
    payment_method: list[str] = Form([]),
    tax_amount: list[str] = Form([]),
    description: list[str] = Form([]),
    action: list[str] = Form([]),
    raw: list[str] = Form([]),
):
    saved, skipped = [], []
    for i in range(len(number)):  # one row per submitted number field
        if _at(action, i) == "skip":
            skipped.append((_at(number, i) or _at(vendor, i) or f"第 {i + 1} 项", "已跳过"))
            continue
        image_name = _safe_upload_name(_at(image_path, i)) if _at(image_path, i) else None
        common = dict(
            vendor=_at(vendor, i), expense_date=_parse_date(_at(expense_date, i)),
            amount=_parse_float(_at(amount, i)) or 0,
            currency=_at(currency, i) or settings.default_currency,
            category=_at(category, i), payment_method=_at(payment_method, i),
            tax_amount=_parse_float(_at(tax_amount, i)), description=_at(description, i),
            image_path=image_name,
        )
        try:
            if ticket_type == "payment":
                phash = None
                if image_name:
                    img = settings.upload_path / image_name
                    if img.exists():
                        phash = perceptual_hash(img.read_bytes())
                e = svc.create_payment(
                    db, user, image_phash=phash, payment_number=_at(number, i),
                    raw=_at(raw, i) or None, **common,
                )
            else:
                e = svc.create_einvoice(
                    db, user, invoice_number=_at(number, i),
                    raw=_at(raw, i) or None, **common,
                )
            saved.append(e)
            audit.log(db, user, "create_expense", "expense", e.id,
                      f"{ticket_type} (batch)")
        except svc.ExpenseError as exc:
            skipped.append((_at(number, i) or _at(vendor, i) or f"第 {i + 1} 项", str(exc)))
    return render(
        request, "expense_batch_result.html", user=user,
        saved=saved, skipped=skipped, ticket_type=ticket_type,
    )


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
    number: str = Form(""), vendor: str = Form(""),
    expense_date: str = Form(""), amount: str = Form(""),
    currency: str = Form(""), category: str = Form(""),
    payment_method: str = Form(""),
    tax_amount: str = Form(""), description: str = Form(""),
):
    expense = svc.get_for_user(db, user, expense_id)
    number_field = (
        {"invoice_number": number} if isinstance(expense, EInvoice)
        else {"payment_number": number}
    )
    try:
        svc.update_expense(
            db, user, expense, **number_field, vendor=vendor,
            expense_date=_parse_date(expense_date), amount=_parse_float(amount),
            currency=currency, category=category, payment_method=payment_method,
            tax_amount=_parse_float(tax_amount), description=description,
        )
    except svc.PermissionDenied as exc:
        return render(request, "error.html", user=user,
                      title="拒绝访问", message=str(exc))
    except svc.ExpenseError as exc:  # duplicate invoice / missing number
        return render(request, "expense_edit.html", user=user, expense=expense,
                      categories=svc.CATEGORIES, error=str(exc))
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
