"""SQLAlchemy ORM models."""
from __future__ import annotations

import enum
import json
from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class Role(str, enum.Enum):
    user = "user"
    admin = "admin"


class ExpenseStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    paid = "paid"


# Chinese display labels (keyed by the stored string value).
STATUS_LABELS = {
    "pending": "待审核",
    "approved": "已通过",
    "rejected": "已驳回",
    "paid": "已支付",
}
# Ticket / voucher types: e-invoice (dedup by invoice number) vs payment
# screenshot (stored as a picture, dedup by image content hash).
TICKET_TYPES = ("einvoice", "payment")
TICKET_TYPE_LABELS = {"einvoice": "电子发票", "payment": "支付凭证"}
ROLE_LABELS = {"user": "用户", "admin": "管理员"}
ACTION_LABELS = {
    "login": "登录",
    "register": "注册",
    "create_expense": "新建报销",
    "update_expense": "修改报销",
    "review_expense": "审核报销",
    "delete_expense": "删除报销",
    "set_role": "修改角色",
    "set_active": "启用/禁用",
}
ENTITY_LABELS = {"expense": "报销", "user": "用户"}


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(255), unique=True)
    full_name: Mapped[str | None] = mapped_column(String(128))
    hashed_password: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(Enum(Role), default=Role.user)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    expenses: Mapped[list["Expense"]] = relationship(
        back_populates="owner", foreign_keys="Expense.user_id"
    )

    @property
    def is_admin(self) -> bool:
        return self.role == Role.admin


class Expense(Base):
    """Base reimbursement record holding fields common to both ticket types.

    Joined-table inheritance: the two voucher kinds live in their own tables —
    ``e_invoices`` (deduped by invoice number) and ``payment_vouchers``
    (deduped by image similarity) — while sharing the common reimbursement and
    review-workflow fields here.
    """

    __tablename__ = "expenses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Discriminator: "einvoice" | "payment".
    ticket_type: Mapped[str] = mapped_column(String(16), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    vendor: Mapped[str | None] = mapped_column(String(255))
    expense_date: Mapped[date | None] = mapped_column(Date, index=True)
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    category: Mapped[str | None] = mapped_column(String(64), index=True)
    tax_amount: Mapped[float | None] = mapped_column(Float)
    # How it was paid, incl. platform + channel, e.g. "微信支付·零钱".
    payment_method: Mapped[str | None] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(Text)
    image_path: Mapped[str | None] = mapped_column(String(512))

    status: Mapped[ExpenseStatus] = mapped_column(
        Enum(ExpenseStatus), default=ExpenseStatus.pending, index=True
    )
    reviewer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    review_comment: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)

    # Raw JSON returned by the extraction LLM, kept for audit / debugging.
    extracted_raw: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    owner: Mapped["User"] = relationship(
        back_populates="expenses", foreign_keys=[user_id]
    )
    reviewer: Mapped["User | None"] = relationship(foreign_keys=[reviewer_id])

    __mapper_args__ = {
        "polymorphic_on": ticket_type,
        "polymorphic_identity": "expense",
        # Always load subclass columns so base queries (list/dashboard/analytics)
        # return fully-populated EInvoice / PaymentVoucher instances.
        "with_polymorphic": "*",
    }


class EInvoice(Expense):
    """E-invoice: fields extracted from the image, deduped by invoice number."""

    __tablename__ = "e_invoices"
    __table_args__ = (
        UniqueConstraint("invoice_number", name="uq_einvoice_invoice_number"),
    )

    id: Mapped[int] = mapped_column(ForeignKey("expenses.id"), primary_key=True)
    invoice_number: Mapped[str | None] = mapped_column(String(128), index=True)
    # JSON map of all other fields read off the invoice.
    extra_fields: Mapped[str | None] = mapped_column(Text)

    __mapper_args__ = {"polymorphic_identity": "einvoice"}

    @property
    def extra_fields_dict(self) -> dict:
        if not self.extra_fields:
            return {}
        try:
            data = json.loads(self.extra_fields)
            return data if isinstance(data, dict) else {}
        except (ValueError, TypeError):
            return {}


class PaymentVoucher(Expense):
    """Payment screenshot: the picture is stored and deduped by visual similarity."""

    __tablename__ = "payment_vouchers"

    id: Mapped[int] = mapped_column(ForeignKey("expenses.id"), primary_key=True)
    payment_number: Mapped[str | None] = mapped_column(String(128), index=True)
    # Perceptual (dHash) hash of the image for similarity comparison.
    image_phash: Mapped[str | None] = mapped_column(String(32), index=True)

    __mapper_args__ = {"polymorphic_identity": "payment"}


class AuditLog(Base):
    """Append-only record of notable actions for traceability."""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(64))
    entity_type: Mapped[str | None] = mapped_column(String(32))
    entity_id: Mapped[int | None] = mapped_column(Integer)
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    actor: Mapped["User | None"] = relationship()


# --------------------------------------------------------------------------- #
# Sales-invoice → accounting voucher (记账凭证) module
# --------------------------------------------------------------------------- #
class VoucherStatus(str, enum.Enum):
    draft = "draft"
    posted = "posted"


VOUCHER_STATUS_LABELS = {"draft": "草稿", "posted": "已过账"}


class SalesInvoice(Base):
    """A sales (VAT) invoice — the source document for a voucher."""

    __tablename__ = "sales_invoices"
    __table_args__ = (
        UniqueConstraint("invoice_number", name="uq_sales_invoice_number"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    invoice_number: Mapped[str | None] = mapped_column(String(128), index=True)
    invoice_code: Mapped[str | None] = mapped_column(String(64))
    invoice_date: Mapped[date | None] = mapped_column(Date, index=True)
    buyer: Mapped[str | None] = mapped_column(String(255))
    buyer_tax_id: Mapped[str | None] = mapped_column(String(64))
    seller: Mapped[str | None] = mapped_column(String(255))
    seller_tax_id: Mapped[str | None] = mapped_column(String(64))
    net_amount: Mapped[float] = mapped_column(Float, default=0.0)    # 不含税
    tax_amount: Mapped[float] = mapped_column(Float, default=0.0)    # 税额（销项）
    tax_rate: Mapped[str | None] = mapped_column(String(16))
    total_amount: Mapped[float] = mapped_column(Float, default=0.0)  # 价税合计
    goods: Mapped[str | None] = mapped_column(Text)
    image_path: Mapped[str | None] = mapped_column(String(512))
    extracted_raw: Mapped[str | None] = mapped_column(Text)
    extra_fields: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    owner: Mapped["User"] = relationship(foreign_keys=[user_id])

    @property
    def extra_fields_dict(self) -> dict:
        if not self.extra_fields:
            return {}
        try:
            data = json.loads(self.extra_fields)
            return data if isinstance(data, dict) else {}
        except (ValueError, TypeError):
            return {}


class Voucher(Base):
    """An accounting voucher (记账凭证) — a balanced journal entry."""

    __tablename__ = "vouchers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    sales_invoice_id: Mapped[int | None] = mapped_column(ForeignKey("sales_invoices.id"))
    voucher_word: Mapped[str] = mapped_column(String(8), default="记")
    voucher_no: Mapped[int] = mapped_column(Integer, default=1)
    period: Mapped[str | None] = mapped_column(String(7), index=True)  # YYYY-MM
    voucher_date: Mapped[date | None] = mapped_column(Date, index=True)
    summary: Mapped[str | None] = mapped_column(String(255))
    attachments: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[VoucherStatus] = mapped_column(
        Enum(VoucherStatus), default=VoucherStatus.draft, index=True
    )
    reviewer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    owner: Mapped["User"] = relationship(foreign_keys=[user_id])
    reviewer: Mapped["User | None"] = relationship(foreign_keys=[reviewer_id])
    sales_invoice: Mapped["SalesInvoice | None"] = relationship(
        foreign_keys=[sales_invoice_id]
    )
    entries: Mapped[list["VoucherEntry"]] = relationship(
        back_populates="voucher", cascade="all, delete-orphan",
        order_by="VoucherEntry.line_no",
    )

    @property
    def code(self) -> str:
        return f"{self.voucher_word}-{self.voucher_no:04d}"

    @property
    def total_debit(self) -> float:
        return round(sum(e.debit or 0 for e in self.entries), 2)

    @property
    def total_credit(self) -> float:
        return round(sum(e.credit or 0 for e in self.entries), 2)

    @property
    def is_balanced(self) -> bool:
        return self.total_debit > 0 and abs(self.total_debit - self.total_credit) < 0.01


class VoucherEntry(Base):
    """One debit/credit line of a voucher."""

    __tablename__ = "voucher_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    voucher_id: Mapped[int] = mapped_column(ForeignKey("vouchers.id"), index=True)
    line_no: Mapped[int] = mapped_column(Integer, default=1)
    summary: Mapped[str | None] = mapped_column(String(255))
    account: Mapped[str] = mapped_column(String(128))
    account_code: Mapped[str | None] = mapped_column(String(32))
    debit: Mapped[float] = mapped_column(Float, default=0.0)
    credit: Mapped[float] = mapped_column(Float, default=0.0)

    voucher: Mapped["Voucher"] = relationship(back_populates="entries")
