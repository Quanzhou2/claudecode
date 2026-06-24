"""Core reimbursement business logic: CRUD, scoping, duplicates, stats."""
from __future__ import annotations

from collections import OrderedDict
from datetime import date, datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models import Expense, ExpenseStatus, User

CATEGORIES = [
    "餐饮", "差旅", "交通", "住宿", "办公",
    "软件", "娱乐", "医疗", "其他",
]


class ExpenseError(Exception):
    """User-facing business-rule error."""


class DuplicateReceiptError(ExpenseError):
    def __init__(self, receipt_number: str, existing: Expense):
        self.receipt_number = receipt_number
        self.existing = existing
        super().__init__(
            f"发票号码 '{receipt_number}' 已被提交过，不能重复报销。"
        )


class PermissionDenied(ExpenseError):
    pass


# --------------------------------------------------------------------------- #
# Receipt-number normalization & duplicate detection
# --------------------------------------------------------------------------- #
def normalize_receipt_number(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = "".join(value.split()).upper()
    return cleaned or None


def find_by_receipt_number(db: Session, receipt_number: str | None) -> Expense | None:
    norm = normalize_receipt_number(receipt_number)
    if not norm:
        return None
    return db.scalar(select(Expense).where(Expense.receipt_number == norm))


# --------------------------------------------------------------------------- #
# Create / update
# --------------------------------------------------------------------------- #
def create_expense(db: Session, owner: User, *, raw: str | None = None, **fields: Any) -> Expense:
    receipt_number = normalize_receipt_number(fields.get("receipt_number"))

    if receipt_number:
        existing = find_by_receipt_number(db, receipt_number)
        if existing:
            raise DuplicateReceiptError(receipt_number, existing)

    expense = Expense(
        user_id=owner.id,
        receipt_number=receipt_number,
        vendor=(fields.get("vendor") or "").strip() or None,
        expense_date=fields.get("expense_date"),
        amount=float(fields.get("amount") or 0),
        currency=(fields.get("currency") or "CNY").strip().upper(),
        category=(fields.get("category") or "").strip() or None,
        payment_method=(fields.get("payment_method") or "").strip() or None,
        tax_amount=fields.get("tax_amount"),
        description=(fields.get("description") or "").strip() or None,
        image_path=fields.get("image_path"),
        extracted_raw=raw,
        status=ExpenseStatus.pending,
    )
    db.add(expense)
    db.commit()
    db.refresh(expense)
    return expense


def can_view(user: User, expense: Expense) -> bool:
    return user.is_admin or expense.user_id == user.id


def can_edit(user: User, expense: Expense) -> bool:
    if user.is_admin:
        return True
    # Owners may edit only while the record is still pending.
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

    if "receipt_number" in fields:
        new_rn = normalize_receipt_number(fields["receipt_number"])
        if new_rn and new_rn != expense.receipt_number:
            clash = find_by_receipt_number(db, new_rn)
            if clash and clash.id != expense.id:
                raise DuplicateReceiptError(new_rn, clash)
        expense.receipt_number = new_rn

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
# Listing (scoped, filtered, paginated)
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
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Expense.vendor.ilike(like),
                Expense.receipt_number.ilike(like),
                Expense.description.ilike(like),
            )
        )

    all_rows = list(db.scalars(stmt.order_by(Expense.created_at.desc())))
    total = len(all_rows)
    page = max(1, page)
    start = (page - 1) * page_size
    return all_rows[start : start + page_size], total


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
                "receipt_number": e.receipt_number,
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
        cat = e.category or "Uncategorized"
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
