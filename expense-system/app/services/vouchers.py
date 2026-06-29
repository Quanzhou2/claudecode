"""Sales invoice → accounting voucher (记账凭证) business logic."""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..accounting import DEFAULT_ACCOUNTS
from ..models import (
    SalesInvoice,
    User,
    Voucher,
    VoucherEntry,
    VoucherStatus,
)
from .expenses import normalize_number


class VoucherError(Exception):
    """User-facing voucher business-rule error."""


class DuplicateInvoiceError(VoucherError):
    def __init__(self, existing: SalesInvoice):
        self.existing = existing
        super().__init__(f"销售发票号码 '{existing.invoice_number}' 已存在，不能重复录入。")


class UnbalancedError(VoucherError):
    pass


class PermissionDenied(VoucherError):
    pass


# --------------------------------------------------------------------------- #
# Amount helpers
# --------------------------------------------------------------------------- #
def _f(x: Any) -> float | None:
    if x in (None, ""):
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        s = "".join(c for c in str(x) if c.isdigit() or c in ".-")
        try:
            return float(s) if s not in ("", "-", ".") else None
        except ValueError:
            return None


def _s(x: Any) -> str | None:
    return (str(x).strip() or None) if x not in (None, "") else None


def rate_to_float(rate_str: str | None) -> float | None:
    if not rate_str:
        return None
    s = str(rate_str).replace("%", "").strip()
    try:
        v = float(s)
        return v / 100 if v > 1 else v
    except ValueError:
        return None


def compute_amounts(total=None, tax=None, net=None, rate_str=None) -> tuple[float, float, float]:
    """Return (total, tax, net) — derive whichever is missing, keep net+tax==total."""
    total, tax, net = _f(total), _f(tax), _f(net)
    rate = rate_to_float(rate_str)

    if total is None and net is not None and tax is not None:
        total = net + tax
    if total is None and net is not None and rate is not None:
        tax = round(net * rate, 2)
        total = net + tax
    if total is None:
        total = net or 0.0
    if net is None:
        if tax is not None:
            net = total - tax
        elif rate is not None:
            net = round(total / (1 + rate), 2)
        else:
            net = total
    if tax is None:
        tax = total - net

    total, net, tax = round(total, 2), round(net, 2), round(max(tax, 0.0), 2)
    if abs(net + tax - total) >= 0.01:  # keep the entry balanced
        net = round(total - tax, 2)
    return total, tax, net


def lines_from_amounts(total: float, tax: float, net: float, goods: str | None,
                       debit_account: str | None) -> list[dict]:
    """Standard sales template: 借 应收/银行 = 价税合计; 贷 收入 + 销项税额."""
    summary = ("销售" + (goods or "商品/服务"))[:50]
    lines = [
        {"summary": summary, "account": debit_account or DEFAULT_ACCOUNTS["ar"],
         "debit": round(total, 2), "credit": 0.0},
        {"summary": summary, "account": DEFAULT_ACCOUNTS["revenue"],
         "debit": 0.0, "credit": round(net, 2)},
    ]
    if round(tax, 2) > 0:
        lines.append({"summary": "销项税额", "account": DEFAULT_ACCOUNTS["output_vat"],
                      "debit": 0.0, "credit": round(tax, 2)})
    return lines


# --------------------------------------------------------------------------- #
# Sales invoice + voucher creation
# --------------------------------------------------------------------------- #
def find_invoice_by_number(db: Session, number: str | None) -> SalesInvoice | None:
    norm = normalize_number(number)
    if not norm:
        return None
    return db.scalar(select(SalesInvoice).where(SalesInvoice.invoice_number == norm))


def create_sales_invoice(db: Session, owner: User, *, raw: str | None = None,
                         extra: dict | None = None, **fields: Any) -> SalesInvoice:
    num = normalize_number(fields.get("invoice_number"))
    if not num:
        raise VoucherError("请填写销售发票号码（用于查重）。")
    existing = find_invoice_by_number(db, num)
    if existing:
        raise DuplicateInvoiceError(existing)

    total, tax, net = compute_amounts(
        fields.get("total_amount"), fields.get("tax_amount"),
        fields.get("net_amount"), fields.get("tax_rate"),
    )
    inv = SalesInvoice(
        user_id=owner.id, invoice_number=num,
        invoice_code=_s(fields.get("invoice_code")),
        invoice_date=fields.get("invoice_date"),
        buyer=_s(fields.get("buyer")), buyer_tax_id=_s(fields.get("buyer_tax_id")),
        seller=_s(fields.get("seller")), seller_tax_id=_s(fields.get("seller_tax_id")),
        net_amount=net, tax_amount=tax, tax_rate=_s(fields.get("tax_rate")),
        total_amount=total, goods=_s(fields.get("goods")),
        image_path=fields.get("image_path"), extracted_raw=raw,
        extra_fields=json.dumps(extra, ensure_ascii=False) if extra else None,
    )
    db.add(inv)
    db.commit()
    db.refresh(inv)
    return inv


def _next_voucher_no(db: Session, word: str, period: str) -> int:
    m = db.scalar(
        select(func.max(Voucher.voucher_no)).where(
            Voucher.voucher_word == word, Voucher.period == period
        )
    )
    return (m or 0) + 1


def generate_voucher(db: Session, owner: User, invoice: SalesInvoice, *,
                     debit_account: str | None = None,
                     voucher_date: date | None = None) -> Voucher:
    vdate = voucher_date or invoice.invoice_date or date.today()
    period = vdate.strftime("%Y-%m")
    summary = ("销售" + (invoice.goods or "商品/服务"))[:50]
    voucher = Voucher(
        user_id=owner.id, sales_invoice_id=invoice.id, voucher_word="记",
        voucher_no=_next_voucher_no(db, "记", period), period=period,
        voucher_date=vdate, summary=summary,
        attachments=1 if invoice.image_path else 0, status=VoucherStatus.draft,
    )
    lines = lines_from_amounts(invoice.total_amount, invoice.tax_amount,
                               invoice.net_amount, invoice.goods, debit_account)
    for i, line in enumerate(lines, start=1):
        voucher.entries.append(VoucherEntry(
            line_no=i, summary=line["summary"], account=line["account"],
            debit=line["debit"], credit=line["credit"],
        ))
    db.add(voucher)
    db.commit()
    db.refresh(voucher)
    return voucher


def create_invoice_and_voucher(db: Session, owner: User, *, debit_account: str | None,
                               raw: str | None = None, extra: dict | None = None,
                               **fields: Any) -> tuple[SalesInvoice, Voucher]:
    invoice = create_sales_invoice(db, owner, raw=raw, extra=extra, **fields)
    voucher = generate_voucher(db, owner, invoice, debit_account=debit_account)
    return invoice, voucher


# --------------------------------------------------------------------------- #
# Permissions / fetch / edit / post / list
# --------------------------------------------------------------------------- #
def can_view(user: User, voucher: Voucher) -> bool:
    return user.is_admin or voucher.user_id == user.id


def can_edit(user: User, voucher: Voucher) -> bool:
    if user.is_admin:
        return True
    return voucher.user_id == user.id and voucher.status == VoucherStatus.draft


def get_for_user(db: Session, user: User, voucher_id: int) -> Voucher:
    voucher = db.get(Voucher, voucher_id)
    if voucher is None:
        raise VoucherError("未找到该凭证。")
    if not can_view(user, voucher):
        raise PermissionDenied("您无权访问该凭证。")
    return voucher


def _validate_balance(lines: list[dict]) -> tuple[float, float]:
    td = round(sum((_f(l.get("debit")) or 0) for l in lines), 2)
    tc = round(sum((_f(l.get("credit")) or 0) for l in lines), 2)
    if td <= 0 or abs(td - tc) >= 0.01:
        raise UnbalancedError(f"借贷不平衡：借方 {td:.2f}，贷方 {tc:.2f}。")
    return td, tc


def update_voucher_entries(db: Session, user: User, voucher: Voucher, *,
                           lines: list[dict], summary: str | None = None,
                           voucher_date: date | None = None) -> Voucher:
    if not can_edit(user, voucher):
        raise PermissionDenied("该凭证不可编辑。")
    clean = [l for l in lines if (l.get("account") or "").strip()]
    _validate_balance(clean)

    voucher.entries.clear()
    for i, l in enumerate(clean, start=1):
        voucher.entries.append(VoucherEntry(
            line_no=i, summary=_s(l.get("summary")), account=l["account"].strip(),
            debit=round(_f(l.get("debit")) or 0, 2), credit=round(_f(l.get("credit")) or 0, 2),
        ))
    if summary is not None:
        voucher.summary = summary.strip() or None
    if voucher_date:
        voucher.voucher_date = voucher_date
    db.commit()
    db.refresh(voucher)
    return voucher


def post_voucher(db: Session, admin: User, voucher: Voucher) -> Voucher:
    if not admin.is_admin:
        raise PermissionDenied("只有管理员可以过账。")
    if not voucher.is_balanced:
        raise UnbalancedError("凭证借贷不平衡，无法过账。")
    voucher.status = VoucherStatus.posted
    voucher.reviewer_id = admin.id
    voucher.reviewed_at = datetime.utcnow()
    db.commit()
    db.refresh(voucher)
    return voucher


def _scoped(user: User):
    stmt = select(Voucher)
    if not user.is_admin:
        stmt = stmt.where(Voucher.user_id == user.id)
    return stmt


def list_vouchers(db: Session, user: User, *, status: str | None = None,
                  date_from: date | None = None, date_to: date | None = None,
                  page: int = 1, page_size: int = 20) -> tuple[list[Voucher], int]:
    stmt = _scoped(user)
    if status:
        stmt = stmt.where(Voucher.status == VoucherStatus(status))
    if date_from:
        stmt = stmt.where(Voucher.voucher_date >= date_from)
    if date_to:
        stmt = stmt.where(Voucher.voucher_date <= date_to)
    rows = list(db.scalars(stmt.order_by(Voucher.created_at.desc())))
    total = len(rows)
    page = max(1, page)
    start = (page - 1) * page_size
    return rows[start : start + page_size], total
