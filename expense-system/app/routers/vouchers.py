"""Sales invoice → accounting voucher routes."""
from __future__ import annotations

import csv
import io
import uuid

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from ..accounting import COMMON_ACCOUNTS, DEBIT_ACCOUNTS, TAX_RATES
from ..config import get_settings
from ..database import get_db
from ..deps import require_admin, require_user
from ..llm.sales import extract_sales_invoice
from ..models import VOUCHER_STATUS_LABELS, User, VoucherStatus
from ..schemas import SalesInvoiceExtraction
from ..services import audit
from ..services import vouchers as svc
from ..templating import render
from .expenses import (
    _ALLOWED_IMAGE_TYPES,
    _parse_date,
    _parse_json_dict,
    _safe_upload_name,
)

router = APIRouter()
settings = get_settings()


def _review_ctx(**extra):
    return dict(debit_accounts=DEBIT_ACCOUNTS, tax_rates=TAX_RATES, **extra)


# --------------------------------------------------------------------------- #
# Upload sales invoice -> extract -> review -> generate voucher
# --------------------------------------------------------------------------- #
@router.get("/sales/new")
def sales_new(request: Request, user: User = Depends(require_user)):
    return render(request, "sales_new.html", user=user)


@router.get("/sales/manual")
def sales_manual(request: Request, user: User = Depends(require_user)):
    return render(request, "sales_review.html", user=user,
                  data=SalesInvoiceExtraction(), image_filename=None, raw="",
                  duplicate=None, preview=None, **_review_ctx())


@router.post("/sales/extract")
async def sales_extract(
    request: Request,
    file: UploadFile = File(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    content = await file.read()
    if file.content_type not in _ALLOWED_IMAGE_TYPES:
        return render(request, "sales_new.html", user=user,
                      error="不支持的文件类型，请上传 JPG、PNG、WEBP 或 GIF 图片。")
    if len(content) > settings.max_upload_bytes:
        return render(request, "sales_new.html", user=user,
                      error=f"文件过大（最大 {settings.max_upload_mb} MB）。")

    filename = f"{uuid.uuid4().hex}{_ALLOWED_IMAGE_TYPES[file.content_type]}"
    (settings.upload_path / filename).write_bytes(content)

    data = extract_sales_invoice(content, file.content_type)
    duplicate = svc.find_invoice_by_number(db, data.invoice_number)
    total, tax, net = svc.compute_amounts(data.total_amount, data.tax_amount,
                                          data.net_amount, data.tax_rate)
    preview = svc.lines_from_amounts(total, tax, net, data.goods, "应收账款")
    return render(request, "sales_review.html", user=user, data=data,
                  image_filename=filename, raw=data.model_dump_json(),
                  duplicate=duplicate, preview=preview, **_review_ctx())


@router.get("/sales/preview/{filename}")
def sales_preview(filename: str, user: User = Depends(require_user)):
    safe = _safe_upload_name(filename)
    if not safe:
        return Response(status_code=404)
    path = settings.upload_path / safe
    return FileResponse(path) if path.exists() else Response(status_code=404)


@router.post("/sales")
def sales_create(
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    invoice_number: str = Form(""),
    invoice_code: str = Form(""),
    invoice_date: str = Form(""),
    buyer: str = Form(""),
    buyer_tax_id: str = Form(""),
    seller: str = Form(""),
    seller_tax_id: str = Form(""),
    total_amount: str = Form(""),
    tax_amount: str = Form(""),
    net_amount: str = Form(""),
    tax_rate: str = Form(""),
    goods: str = Form(""),
    debit_account: str = Form("应收账款"),
    image_path: str = Form(""),
    extra_fields: str = Form(""),
    raw: str = Form(""),
):
    image_name = _safe_upload_name(image_path) if image_path else None
    try:
        _invoice, voucher = svc.create_invoice_and_voucher(
            db, user, debit_account=debit_account, raw=raw or None,
            extra=_parse_json_dict(extra_fields),
            invoice_number=invoice_number, invoice_code=invoice_code,
            invoice_date=_parse_date(invoice_date), buyer=buyer,
            buyer_tax_id=buyer_tax_id, seller=seller, seller_tax_id=seller_tax_id,
            total_amount=total_amount, tax_amount=tax_amount, net_amount=net_amount,
            tax_rate=tax_rate, goods=goods, image_path=image_name,
        )
    except svc.VoucherError as exc:
        return render(
            request, "sales_review.html", user=user, error=str(exc),
            duplicate=getattr(exc, "existing", None), image_filename=image_name,
            raw=raw, preview=None, **_review_ctx(),
            data={
                "invoice_number": invoice_number, "invoice_code": invoice_code,
                "invoice_date": invoice_date, "buyer": buyer,
                "buyer_tax_id": buyer_tax_id, "seller": seller,
                "seller_tax_id": seller_tax_id, "total_amount": total_amount,
                "tax_amount": tax_amount, "net_amount": net_amount,
                "tax_rate": tax_rate, "goods": goods,
            },
        )
    audit.log(db, user, "create_voucher", "voucher", voucher.id, voucher.code)
    return RedirectResponse(f"/vouchers/{voucher.id}", status_code=303)


# --------------------------------------------------------------------------- #
# Voucher list / export / detail / edit / post / image
# --------------------------------------------------------------------------- #
@router.get("/vouchers")
def vouchers_list(
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    status: str = Query(""),
    date_from: str = Query(""),
    date_to: str = Query(""),
    page: int = Query(1),
):
    items, total = svc.list_vouchers(
        db, user, status=status or None, date_from=_parse_date(date_from),
        date_to=_parse_date(date_to), page=page, page_size=20,
    )
    pages = max(1, (total + 19) // 20)
    return render(request, "vouchers_list.html", user=user, items=items,
                  total=total, page=page, pages=pages,
                  statuses=[s.value for s in VoucherStatus],
                  filters={"status": status, "date_from": date_from, "date_to": date_to})


@router.get("/vouchers/export.csv")
def vouchers_export(
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    status: str = Query(""),
):
    items, _ = svc.list_vouchers(db, user, status=status or None, page=1, page_size=100000)
    buf = io.StringIO()
    buf.write("﻿")
    writer = csv.writer(buf)
    writer.writerow(["凭证号", "日期", "凭证摘要", "行摘要", "会计科目",
                     "借方金额", "贷方金额", "状态", "制单人"])
    for v in items:
        label = VOUCHER_STATUS_LABELS.get(v.status.value, v.status.value)
        for e in v.entries:
            writer.writerow([v.code, v.voucher_date or "", v.summary or "",
                             e.summary or "", e.account, e.debit or 0, e.credit or 0,
                             label, v.owner.username if v.owner else ""])
    return Response(content=buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=vouchers.csv"})


@router.get("/vouchers/{voucher_id}")
def voucher_detail(request: Request, voucher_id: int,
                   user: User = Depends(require_user), db: Session = Depends(get_db)):
    voucher = svc.get_for_user(db, user, voucher_id)
    return render(request, "voucher_detail.html", user=user, voucher=voucher,
                  can_edit=svc.can_edit(user, voucher))


@router.get("/vouchers/{voucher_id}/edit")
def voucher_edit_form(request: Request, voucher_id: int,
                      user: User = Depends(require_user), db: Session = Depends(get_db)):
    voucher = svc.get_for_user(db, user, voucher_id)
    if not svc.can_edit(user, voucher):
        return render(request, "error.html", user=user, title="无法编辑",
                      message="该凭证已过账，无法再编辑。")
    rows = [{"summary": e.summary or "", "account": e.account,
             "debit": e.debit or "", "credit": e.credit or ""} for e in voucher.entries]
    return render(request, "voucher_edit.html", user=user, voucher=voucher,
                  rows=rows, accounts=COMMON_ACCOUNTS)


@router.post("/vouchers/{voucher_id}/edit")
def voucher_edit_submit(
    request: Request, voucher_id: int,
    user: User = Depends(require_user), db: Session = Depends(get_db),
    summary: str = Form(""), voucher_date: str = Form(""),
    line_summary: list[str] = Form([]), line_account: list[str] = Form([]),
    line_debit: list[str] = Form([]), line_credit: list[str] = Form([]),
):
    voucher = svc.get_for_user(db, user, voucher_id)
    lines = [
        {"summary": (line_summary[i] if i < len(line_summary) else ""),
         "account": line_account[i],
         "debit": (line_debit[i] if i < len(line_debit) else ""),
         "credit": (line_credit[i] if i < len(line_credit) else "")}
        for i in range(len(line_account))
    ]
    try:
        svc.update_voucher_entries(db, user, voucher, lines=lines,
                                   summary=summary, voucher_date=_parse_date(voucher_date))
    except svc.PermissionDenied as exc:
        return render(request, "error.html", user=user, title="拒绝访问", message=str(exc))
    except svc.VoucherError as exc:
        return render(request, "voucher_edit.html", user=user, voucher=voucher,
                      rows=lines, accounts=COMMON_ACCOUNTS, error=str(exc))
    audit.log(db, user, "update_voucher", "voucher", voucher.id)
    return RedirectResponse(f"/vouchers/{voucher.id}", status_code=303)


@router.post("/vouchers/{voucher_id}/post")
def voucher_post(request: Request, voucher_id: int,
                 admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    voucher = svc.get_for_user(db, admin, voucher_id)
    try:
        svc.post_voucher(db, admin, voucher)
    except svc.VoucherError as exc:
        return render(request, "voucher_detail.html", user=admin, voucher=voucher,
                      can_edit=svc.can_edit(admin, voucher), error=str(exc))
    audit.log(db, admin, "post_voucher", "voucher", voucher.id)
    return RedirectResponse(f"/vouchers/{voucher.id}", status_code=303)


@router.get("/vouchers/{voucher_id}/image")
def voucher_image(voucher_id: int, user: User = Depends(require_user),
                  db: Session = Depends(get_db)):
    voucher = svc.get_for_user(db, user, voucher_id)
    inv = voucher.sales_invoice
    if not inv or not inv.image_path:
        return Response(status_code=404)
    path = settings.upload_path / inv.image_path
    return FileResponse(path) if path.exists() else Response(status_code=404)
