"""Chart-of-accounts constants for sales-invoice vouchers (记账凭证)."""
from __future__ import annotations

# Accounts used by the built-in standard sales (revenue) voucher template.
DEFAULT_ACCOUNTS = {
    "ar": "应收账款",
    "bank": "银行存款",
    "cash": "库存现金",
    "revenue": "主营业务收入",
    "output_vat": "应交税费——应交增值税（销项税额）",
}

# Debit-side choices offered on the review screen (which account receives the
# total incl. tax): accounts receivable for credit sales, bank/cash otherwise.
DEBIT_ACCOUNTS = ["应收账款", "银行存款", "库存现金", "预收账款"]

# Accounts offered in the voucher-entry editor (a small common chart).
COMMON_ACCOUNTS = [
    "应收账款", "银行存款", "库存现金", "预收账款", "其他应收款",
    "主营业务收入", "其他业务收入",
    "应交税费——应交增值税（销项税额）", "应交税费——应交增值税（进项税额）",
    "主营业务成本", "库存商品", "销售费用", "管理费用", "财务费用", "应付账款",
]

# Common VAT rates for the rate selector.
TAX_RATES = ["13%", "9%", "6%", "3%", "1%", "0%"]
