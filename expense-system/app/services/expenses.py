"""Core reimbursement business logic: CRUD, scoping, duplicates, stats.

Two voucher types live in their own tables:
- **EInvoice** — deduplicated by invoice number (exact).
- **PaymentVoucher** — deduplicated by image *similarity* (perceptual hash),
  reporting a similarity score.
"""
from __future__ import annotations

from collections import OrderedDict
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..imaging import similarity
from ..models import EInvoice, Expense, ExpenseStatus, PaymentVoucher, User

CATEGORIES = [
    "餐饮", "差旅", "交通", "住宿", "办公",
    "软件", "娱乐", "医疗", "其他",
]


class ExpenseError(Exception):
    """User-facing business-rule error."""


class DuplicateError(ExpenseError):
    """Base for duplicate-detection errors; carries the existing record."""

    def __init__(self, message: str, existing: Expense):
        self.existing = existing
        super().__init__(message)


class DuplicateInvoiceError(DuplicateError):
    def __init__(self, invoice_number: str, existing: Expense):
        self.invoice_number = invoice_number
        super().__init__(
            f"发票号码 '{invoice_number}' 已被提交过，不能重复报销。", existing
        )


class DuplicateImageError(DuplicateError):
    def __init__(self, existing: Expense, score: float):
        self.similarity = score
        super().__init__(
            f"该支付凭证与已有凭证高度相似（相似度 {round(score * 100)}%），疑似重复报销。",
            existing,
        )


class PermissionDenied(ExpenseError):
    pass


# --------------------------------------------------------------------------- #
# Numbers, image similarity & lookups
# --------------------------------------------------------------------------- #
def normalize_number(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = "".join(value.split()).upper()
    return cleaned or None


def record_number(e: Expense) -> str | None:
    """The dedup/display number for either voucher type."""
    return getattr(e, "invoice_number", None) or getattr(e, "payment_number", None)


def find_by_invoice_number(db: Session, invoice_number: str | None) -> EInvoice | None:
    norm = normalize_number(invoice_number)
    if not norm:
        return None
    return db.scalar(select(EInvoice).where(EInvoice.invoice_number == norm))


def best_payment_match(
    db: Session, phash: str | None
) -> tuple[PaymentVoucher, float] | None:
    """Return the most visually-similar existing payment voucher and its score."""
    if not phash:
        return None
    best: PaymentVoucher | None = None
    best_score = 0.0
    for pv in db.scalars(
        select(PaymentVoucher).where(PaymentVoucher.image_phash.is_not(None))
    ):
        score = similarity(phash, pv.image_phash)
        if score > best_score:
            best_score, best = score, pv
    return (best, best_score) if best is not None else None


# --------------------------------------------------------------------------- #
# Create (one path per voucher type)
# --------------------------------------------------------------------------- #
def _common_fields(fields: dict[str, Any]) -> dict[str, Any]:
    return dict(
        vendor=(fields.get("vendor") or "").strip() or None,
        expense_date=fields.get("expense_date"),
        amount=float(fields.get("amount") or 0),
        currency=(fields.get("currency") or "CNY").strip().upper(),
        category=(fields.get("category") or "").strip() or None,
        payment_method=(fields.get("payment_method") or "").strip() or None,
        tax_amount=fields.get("tax_amount"),
        description=(fields.get("description") or "").strip() or None,
        image_path=fields.get("image_path"),
    )


def create_einvoice(
    db: Session, owner: User, *, invoice_number: str | None,
    raw: str | None = None, **fields: Any,
) -> EInvoice:
    num = normalize_number(invoice_number)
    if not num:
        raise ExpenseError("电子发票需要填写发票号码（用于查重）。")
    existing = find_by_invoice_number(db, num)
    if existing:
        raise DuplicateInvoiceError(num, existing)

    e = EInvoice(
        user_id=owner.id, invoice_number=num, status=ExpenseStatus.pending,
        extracted_raw=raw, **_common_fields(fields),
    )
    db.add(e)
    db.commit()
    db.refresh(e)
    return e


def create_payment(
    db: Session, owner: User, *, image_phash: str | None, payment_number: str | None = None,
    raw: str | None = None, threshold: float | None = None, **fields: Any,
) -> PaymentVoucher:
    if not image_phash:
        raise ExpenseError("支付凭证需要上传图片（用于查重）。")
    if threshold is None:
        threshold = get_settings().image_similarity_threshold

    match = best_payment_match(db, image_phash)
    if match and match[1] >= threshold:
        raise DuplicateImageError(match[0], match[1])

    pv = PaymentVoucher(
        user_id=owner.id, payment_number=normalize_number(payment_number),
        image_phash=image_phash, status=ExpenseStatus.pending, extracted_raw=raw,
        **_common_fields(fields),
    )
    db.add(pv)
    db.commit()
    db.refresh(pv)
    return pv


# --------------------------------------------------------------------------- #
# Permissions / fetch / update / review / delete
# --------------------------------------------------------------------------- #
def can_view(user: User, expense: Expense) -> bool:
    return user.is_admin or expense.user_id == user.id


def can_edit(user: User, expense: Expense) -> bool:
    if user.is_admin:
        return True
    return expense.user_id == user.id and expense.status == ExpenseStatus.pending


def get_for_user(db: Session, user: User, expense_id: int) -> Expense:
    expense = db.get(Expense, expense_id)
    if expense is None:
        raise ExpenseError("未找到该记录。")
    if not can_view(user, expense):
        raise PermissionDenied("您无权访问该记录。")
    return expense


def update_expense(db: Session, user: User, expense: Expense, **fields: Any) -> Expense:
    if not can_edit(user, expense):
        raise PermissionDenied("您无权编辑该记录。")

    if isinstance(expense, EInvoice) and "invoice_number" in fields:
        new_num = normalize_number(fields["invoice_number"])
        if not new_num:
            raise ExpenseError("电子发票需要填写发票号码。")
        if new_num != expense.invoice_number:
            clash = find_by_invoice_number(db, new_num)
            if clash and clash.id != expense.id:
                raise DuplicateInvoiceError(new_num, clash)
        expense.invoice_number = new_num
    if isinstance(expense, PaymentVoucher) and "payment_number" in fields:
        expense.payment_number = normalize_number(fields["payment_number"])

    for attr in ("vendor", "category", "description", "payment_method"):
        if attr in fields:
            setattr(expense, attr, (fields[attr] or "").strip() or None)
    if "expense_date" in fields:
        expense.expense_date = fields["expense_date"]
    if "amount" in fields and fields["amount"] is not None:
        expense.amount = float(fields["amount"])
    if "tax_amount" in fields:
        expense.tax_amount = fields["tax_amount"]
    if "currency" in fields and fields["currency"]:
        expense.currency = fields["currency"].strip().upper()

    db.commit()
    db.refresh(expense)
    return expense


def review_expense(
    db: Session, admin: User, expense: Expense, status: ExpenseStatus, comment: str | None
) -> Expense:
    if not admin.is_admin:
        raise PermissionDenied("只有管理员可以审核记录。")
    expense.status = status
    expense.review_comment = (comment or "").strip() or None
    expense.reviewer_id = admin.id
    expense.reviewed_at = datetime.utcnow()
    db.commit()
    db.refresh(expense)
    return expense


def delete_expense(db: Session, user: User, expense: Expense) -> None:
    if not (user.is_admin or (expense.user_id == user.id and expense.status == ExpenseStatus.pending)):
        raise PermissionDenied("您无权删除该记录。")
    db.delete(expense)
    db.commit()


# --------------------------------------------------------------------------- #
# Listing (scoped, filtered, paginated) — base query returns both types
# --------------------------------------------------------------------------- #
def _scoped_query(user: User):
    stmt = select(Expense)
    if not user.is_admin:
        stmt = stmt.where(Expense.user_id == user.id)
    return stmt


def list_expenses(
    db: Session,
    user: User,
    *,
    status: str | None = None,
    category: str | None = None,
    q: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    owner_id: int | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Expense], int]:
    stmt = _scoped_query(user)
    if status:
        stmt = stmt.where(Expense.status == ExpenseStatus(status))
    if category:
        stmt = stmt.where(Expense.category == category)
    if owner_id and user.is_admin:
        stmt = stmt.where(Expense.user_id == owner_id)
    if date_from:
        stmt = stmt.where(Expense.expense_date >= date_from)
    if date_to:
        stmt = stmt.where(Expense.expense_date <= date_to)

    rows = list(db.scalars(stmt.order_by(Expense.created_at.desc())))
    if q:
        ql = q.strip().lower()
        rows = [
            e for e in rows
            if ql in " ".join(
                filter(None, [e.vendor, e.description, record_number(e)])
            ).lower()
        ]

    total = len(rows)
    page = max(1, page)
    start = (page - 1) * page_size
    return rows[start : start + page_size], total


# --------------------------------------------------------------------------- #
# Analysis rows (scoped) — shape matches llm.analysis.SCHEMA_COLUMNS
# --------------------------------------------------------------------------- #
def rows_for_analysis(db: Session, user: User) -> list[dict]:
    rows = list(db.scalars(_scoped_query(user)))
    out = []
    for e in rows:
        out.append(
            {
                "id": e.id,
                "owner": e.owner.username if e.owner else None,
                "ticket_type": e.ticket_type,
                "invoice_number": getattr(e, "invoice_number", None),
                "payment_number": getattr(e, "payment_number", None),
                "vendor": e.vendor,
                "expense_date": e.expense_date.isoformat() if e.expense_date else None,
                "amount": float(e.amount or 0),
                "currency": e.currency,
                "category": e.category,
                "payment_method": e.payment_method,
                "tax_amount": float(e.tax_amount) if e.tax_amount is not None else None,
                "status": e.status.value,
                "description": e.description,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
        )
    return out


# --------------------------------------------------------------------------- #
# Dashboard statistics (scoped, aggregated in Python for portability)
# --------------------------------------------------------------------------- #
def dashboard_stats(db: Session, user: User, *, months: int = 6) -> dict:
    rows = list(db.scalars(_scoped_query(user)))
    total_amount = sum(float(e.amount or 0) for e in rows)

    by_status: dict[str, int] = {s.value: 0 for s in ExpenseStatus}
    by_category: dict[str, float] = {}
    by_month: dict[str, float] = {}

    for e in rows:
        by_status[e.status.value] += 1
        cat = e.category or "未分类"
        by_category[cat] = by_category.get(cat, 0.0) + float(e.amount or 0)
        if e.expense_date:
            key = e.expense_date.strftime("%Y-%m")
            by_month[key] = by_month.get(key, 0.0) + float(e.amount or 0)

    category_sorted = OrderedDict(
        sorted(by_category.items(), key=lambda kv: kv[1], reverse=True)
    )
    month_sorted = OrderedDict(sorted(by_month.items())[-months:])

    return {
        "total_count": len(rows),
        "total_amount": round(total_amount, 2),
        "pending_count": by_status.get("pending", 0),
        "approved_count": by_status.get("approved", 0),
        "by_status": by_status,
        "by_category": category_sorted,
        "by_month": month_sorted,
    }
