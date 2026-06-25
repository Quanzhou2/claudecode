"""SQLAlchemy ORM models."""
from __future__ import annotations

import enum
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
    """A single reimbursement record."""

    __tablename__ = "expenses"
    # Receipt numbers are globally unique to prevent the same physical
    # receipt being reimbursed twice (even across different users).
    # The image hash blocks the same payment screenshot being submitted twice.
    __table_args__ = (
        UniqueConstraint("receipt_number", name="uq_expenses_receipt_number"),
        UniqueConstraint("image_hash", name="uq_expenses_image_hash"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    # "einvoice" (extract fields, dedup by receipt_number) or
    # "payment" (store the picture, dedup by image_hash).
    ticket_type: Mapped[str] = mapped_column(String(16), default="einvoice", index=True)
    receipt_number: Mapped[str | None] = mapped_column(String(128), index=True)
    vendor: Mapped[str | None] = mapped_column(String(255))
    expense_date: Mapped[date | None] = mapped_column(Date, index=True)
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    category: Mapped[str | None] = mapped_column(String(64), index=True)
    tax_amount: Mapped[float | None] = mapped_column(Float)
    # How it was paid, incl. platform + channel, e.g. "微信支付·零钱",
    # "支付宝·余额宝", "拼多多·多多支付", "京东·微信支付".
    payment_method: Mapped[str | None] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(Text)
    image_path: Mapped[str | None] = mapped_column(String(512))
    # SHA-256 of the uploaded image bytes; used to detect duplicate vouchers.
    image_hash: Mapped[str | None] = mapped_column(String(64), index=True)

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
