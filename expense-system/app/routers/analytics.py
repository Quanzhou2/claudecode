"""Natural-language query & analysis routes (LLM-powered)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..deps import require_user
from ..llm.analysis import analyze, build_chart
from ..models import User
from ..services import expenses as svc
from ..templating import render

router = APIRouter()

SUGGESTED = [
    "我今年已通过报销的总金额是多少？",
    "按分类统计支出，从高到低排序。",
    "展示最近 6 个月的每月支出合计。",
    "在哪些商户的消费最多？",
    "列出金额超过 500 的待审核发票。",
]


@router.get("/analytics")
def analytics_form(request: Request, user: User = Depends(require_user)):
    return render(request, "analytics.html", user=user, suggested=SUGGESTED,
                  llm_model=get_settings().llm_model)


@router.post("/analytics")
def analytics_query(
    request: Request,
    question: str = Form(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    rows = svc.rows_for_analysis(db, user)
    result = analyze(question.strip(), rows) if question.strip() else None
    chart = build_chart(result.columns, result.rows) if result else None
    return render(
        request, "analytics.html", user=user,
        suggested=SUGGESTED, result=result, chart=chart, question=question,
        scope_count=len(rows), llm_model=get_settings().llm_model,
    )
