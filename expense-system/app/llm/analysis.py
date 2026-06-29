"""LLM-powered natural-language query & analysis over expense data.

Safety model
------------
The user's question is never used to build SQL against the live database.
Instead we copy the rows the current user is *allowed to see* into a fresh
in-memory SQLite database that contains a single ``expenses`` table and
nothing else (no users, no password hashes, no other people's rows). The
LLM writes a read-only ``SELECT`` against that throwaway database, which is
additionally put into ``query_only`` mode and validated to reject anything
other than a single SELECT/CTE statement.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from ..config import get_settings
from .client import get_client

# Columns exposed to the analysis sandbox (and described to the LLM).
SCHEMA_COLUMNS = [
    ("id", "INTEGER"),
    ("owner", "TEXT"),  # username of the submitter
    ("ticket_type", "TEXT"),  # einvoice / payment
    ("invoice_number", "TEXT"),
    ("payment_number", "TEXT"),
    ("vendor", "TEXT"),
    ("expense_date", "TEXT"),  # ISO YYYY-MM-DD
    ("amount", "REAL"),
    ("currency", "TEXT"),
    ("category", "TEXT"),
    ("payment_method", "TEXT"),
    ("tax_amount", "REAL"),
    ("status", "TEXT"),  # pending / approved / rejected / paid
    ("description", "TEXT"),
    ("created_at", "TEXT"),
]

_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|attach|detach|pragma|"
    r"replace|vacuum|reindex|trigger|grant|revoke)\b",
    re.IGNORECASE,
)

_MAX_DISPLAY_ROWS = 200
_MAX_SUMMARY_ROWS = 40


@dataclass
class AnalysisResult:
    question: str
    used_llm: bool = False
    sql: str | None = None
    explanation: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    summary: str | None = None
    error: str | None = None


def build_chart(columns: list[str], rows: list[list], *, max_bars: int = 30) -> dict | None:
    """Turn a tabular result into a simple bar chart spec, or None.

    Picks the last all-numeric column as the value and the first non-numeric
    column as the label, so "category/month vs amount" style results plot well.
    """
    if not columns or not rows or len(rows) > max_bars:
        return None
    ncol = len(columns)

    def col_is_numeric(j: int) -> bool:
        vals = [r[j] for r in rows if j < len(r) and r[j] is not None]
        if not vals:
            return False
        for v in vals:
            try:
                float(v)
            except (TypeError, ValueError):
                return False
        return True

    numeric = [j for j in range(ncol) if col_is_numeric(j)]
    if not numeric:
        return None
    value_idx = numeric[-1]
    label_idx = next((j for j in range(ncol) if j not in numeric), None)
    if label_idx is None:
        label_idx = next((j for j in range(ncol) if j != value_idx), None)

    items = []
    for i, r in enumerate(rows):
        try:
            value = float(r[value_idx])
        except (TypeError, ValueError, IndexError):
            continue
        if label_idx is not None and label_idx < len(r) and r[label_idx] is not None:
            label = str(r[label_idx])
        else:
            label = f"#{i + 1}"
        items.append((label, value))
    if not items:
        return None

    mx = max((abs(v) for _, v in items), default=0.0) or 1.0
    return {
        "value_label": columns[value_idx],
        "label_label": columns[label_idx] if label_idx is not None else "",
        "bars": [
            {"label": lbl, "value": val, "pct": round(abs(val) / mx * 100, 1)}
            for lbl, val in items
        ],
    }


def is_safe_select(sql: str) -> bool:
    """Allow exactly one read-only SELECT/CTE statement."""
    stripped = sql.strip().rstrip(";").strip()
    if not stripped:
        return False
    if ";" in stripped:  # no statement chaining
        return False
    if not re.match(r"^(select|with)\b", stripped, re.IGNORECASE):
        return False
    if _FORBIDDEN.search(stripped):
        return False
    return True


def _build_sandbox(rows: list[dict]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    cols_ddl = ", ".join(f"{name} {ctype}" for name, ctype in SCHEMA_COLUMNS)
    conn.execute(f"CREATE TABLE expenses ({cols_ddl})")
    col_names = [c[0] for c in SCHEMA_COLUMNS]
    placeholders = ", ".join("?" for _ in col_names)
    conn.executemany(
        f"INSERT INTO expenses ({', '.join(col_names)}) VALUES ({placeholders})",
        [[r.get(c) for c in col_names] for r in rows],
    )
    conn.commit()
    conn.execute("PRAGMA query_only = ON")  # belt-and-braces: reject writes
    return conn


def _schema_description() -> str:
    lines = [f"  {name} {ctype}" for name, ctype in SCHEMA_COLUMNS]
    return "TABLE expenses (\n" + ",\n".join(lines) + "\n)"


def _llm_generate_sql(client, model: str, question: str) -> tuple[str, str]:
    prompt = f"""\
You are a careful data analyst. Write ONE read-only SQLite SELECT query that
answers the user's question against this schema:

{_schema_description()}

Rules:
- SQLite dialect. A single SELECT (or WITH ... SELECT) statement only.
- No INSERT/UPDATE/DELETE/DDL. Never modify data.
- Amounts are in the `amount` column; `currency` may vary, do not mix silently.
- Dates are ISO strings; use substr(expense_date,1,7) for month grouping.
- Always include a sensible LIMIT (<= {_MAX_DISPLAY_ROWS}) unless aggregating.
- Respond with a JSON object: {{"sql": "...", "explanation": "..."}}.
- Write the "explanation" value in Chinese.

Question: {question}"""
    resp = client.chat.completions.create(
        model=model,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    content = (resp.choices[0].message.content or "").strip()
    if content.startswith("```"):
        content = content.split("```", 2)[1]
        if content.lstrip().startswith("json"):
            content = content.lstrip()[4:]
    start, end = content.find("{"), content.rfind("}")
    data = json.loads(content[start : end + 1]) if start != -1 else {}
    return data.get("sql", ""), data.get("explanation", "")


def _llm_summarize(client, model: str, question: str, columns, rows) -> str:
    sample = rows[:_MAX_SUMMARY_ROWS]
    payload = {"columns": columns, "rows": sample, "row_count": len(rows)}
    prompt = (
        "根据下面的问题和查询结果，用中文写一段简洁、客观的分析（2-4 句），"
        "并引用具体数字。不要编造数据。\n\n"
        f"问题：{question}\n\n结果 JSON：\n{json.dumps(payload, default=str, ensure_ascii=False)}"
    )
    resp = client.chat.completions.create(
        model=model,
        temperature=0.2,
        messages=[{"role": "user", "content": prompt}],
    )
    return (resp.choices[0].message.content or "").strip()


def _fallback(question: str, rows: list[dict]) -> AnalysisResult:
    """Deterministic category breakdown when no LLM is available."""
    totals: dict[str, dict[str, float]] = {}
    grand = 0.0
    for r in rows:
        cat = r.get("category") or "未分类"
        amt = float(r.get("amount") or 0)
        bucket = totals.setdefault(cat, {"count": 0, "total": 0.0})
        bucket["count"] += 1
        bucket["total"] += amt
        grand += amt
    table = sorted(totals.items(), key=lambda kv: kv[1]["total"], reverse=True)
    result_rows = [
        [cat, int(v["count"]), round(v["total"], 2)] for cat, v in table
    ]
    top = table[0][0] if table else "无"
    summary = (
        f"共 {len(rows)} 条记录，合计金额 {grand:,.2f}。"
        f"占比最高的分类是「{top}」。"
        "（设置 LLM_API_KEY 后可使用自由文本的自然语言分析。）"
    )
    return AnalysisResult(
        question=question,
        used_llm=False,
        columns=["分类", "笔数", "合计金额"],
        rows=result_rows,
        summary=summary,
    )


def analyze(question: str, rows: list[dict], *, client=None, model: str | None = None) -> AnalysisResult:
    """Answer a natural-language question over the supplied (pre-scoped) rows."""
    settings = get_settings()
    client = client if client is not None else get_client()
    model = model or settings.llm_model

    if client is None:
        return _fallback(question, rows)

    try:
        sql, explanation = _llm_generate_sql(client, model, question)
    except Exception as exc:  # noqa: BLE001 — surface any provider error gracefully
        res = _fallback(question, rows)
        res.error = f"LLM query generation failed: {exc}"
        return res

    if not is_safe_select(sql):
        res = _fallback(question, rows)
        res.error = "Generated query was rejected by the safety validator."
        res.sql = sql
        return res

    conn = _build_sandbox(rows)
    try:
        cur = conn.execute(sql)
        columns = [d[0] for d in cur.description] if cur.description else []
        data_rows = [list(r) for r in cur.fetchmany(_MAX_DISPLAY_ROWS)]
    except sqlite3.Error as exc:
        res = _fallback(question, rows)
        res.error = f"Query execution failed: {exc}"
        res.sql = sql
        return res
    finally:
        conn.close()

    summary = None
    try:
        summary = _llm_summarize(client, model, question, columns, data_rows)
    except Exception:  # noqa: BLE001 — summary is best-effort
        summary = None

    return AnalysisResult(
        question=question,
        used_llm=True,
        sql=sql,
        explanation=explanation,
        columns=columns,
        rows=data_rows,
        summary=summary,
    )
