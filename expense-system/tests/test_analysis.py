from _fakes import FakeAnalysisClient

from app.llm.analysis import analyze, build_chart, is_safe_select


def test_build_chart_picks_label_and_value():
    chart = build_chart(["分类", "笔数", "合计金额"], [["餐饮", 3, 126.5], ["交通", 1, 12.0]])
    assert chart["label_label"] == "分类"
    assert chart["value_label"] == "合计金额"   # last numeric column
    assert chart["bars"][0] == {"label": "餐饮", "value": 126.5, "pct": 100.0}
    assert chart["bars"][1]["pct"] < 100.0


def test_build_chart_none_when_no_numeric_or_too_many_rows():
    assert build_chart(["a", "b"], [["x", "y"]]) is None
    assert build_chart(["c", "v"], [[f"c{i}", i] for i in range(40)]) is None

ROWS = [
    {"id": 1, "owner": "alice", "receipt_number": "R1", "vendor": "Sky Cafe",
     "expense_date": "2026-01-15", "amount": 86.5, "currency": "CNY",
     "category": "Meals", "tax_amount": 5, "status": "approved",
     "description": "lunch", "created_at": "2026-01-15T10:00:00"},
    {"id": 2, "owner": "alice", "receipt_number": "R2", "vendor": "MetroRail",
     "expense_date": "2026-01-18", "amount": 12.0, "currency": "CNY",
     "category": "Transport", "tax_amount": 0, "status": "pending",
     "description": "train", "created_at": "2026-01-18T09:00:00"},
    {"id": 3, "owner": "alice", "receipt_number": "R3", "vendor": "Sky Cafe",
     "expense_date": "2026-02-02", "amount": 40.0, "currency": "CNY",
     "category": "Meals", "tax_amount": 2, "status": "approved",
     "description": "dinner", "created_at": "2026-02-02T20:00:00"},
]


def test_is_safe_select():
    assert is_safe_select("SELECT * FROM expenses")
    assert is_safe_select("WITH x AS (SELECT 1) SELECT * FROM x")
    assert not is_safe_select("DROP TABLE expenses")
    assert not is_safe_select("DELETE FROM expenses")
    assert not is_safe_select("SELECT 1; DROP TABLE expenses")
    assert not is_safe_select("UPDATE expenses SET amount = 0")
    assert not is_safe_select("")


def test_analyze_runs_generated_sql():
    client = FakeAnalysisClient(
        "SELECT category, SUM(amount) AS total FROM expenses GROUP BY category ORDER BY total DESC"
    )
    result = analyze("spend by category", ROWS, client=client)
    assert result.used_llm is True
    assert result.error is None
    assert result.columns == ["category", "total"]
    # Meals 86.5 + 40 = 126.5 should be the top category.
    assert result.rows[0][0] == "Meals"
    assert result.rows[0][1] == 126.5
    assert result.summary == "Looks reasonable."
    assert len(client.calls) == 2  # one SQL gen + one summary


def test_analyze_rejects_unsafe_sql():
    client = FakeAnalysisClient("DROP TABLE expenses")
    result = analyze("delete everything", ROWS, client=client)
    assert result.error is not None
    assert "rejected" in result.error.lower()
    # Falls back to the deterministic category breakdown.
    assert result.columns == ["分类", "笔数", "合计金额"]


def test_analyze_sandbox_has_no_other_tables():
    # Even a syntactically-valid SELECT against a forbidden table fails safely.
    client = FakeAnalysisClient("SELECT * FROM users")
    result = analyze("read users", ROWS, client=client)
    assert result.error is not None
    assert result.columns == ["分类", "笔数", "合计金额"]  # fallback


def test_analyze_fallback_without_llm():
    result = analyze("anything", ROWS)  # no client, key empty -> fallback
    assert result.used_llm is False
    assert result.columns == ["分类", "笔数", "合计金额"]
    assert result.rows[0][0] == "Meals"  # highest total
