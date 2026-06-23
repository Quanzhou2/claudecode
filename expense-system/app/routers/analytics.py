"""Natural-language query & analysis routes (LLM-powered)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import require_user
from ..llm.analysis import analyze
from ..models import User
from ..services import expenses as svc
from ..templating import render

router = APIRouter()

SUGGESTED = [
    "What is my total approved spend this year?",
    "Break down spending by category, highest first.",
    "Show monthly totals for the last 6 months.",
    "Which vendors did I spend the most on?",
    "List pending receipts over 500.",
]


@router.get("/analytics")
def analytics_form(request: Request, user: User = Depends(require_user)):
    return render(request, "analytics.html", user=user, suggested=SUGGESTED)


@router.post("/analytics")
def analytics_query(
    request: Request,
    question: str = Form(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    rows = svc.rows_for_analysis(db, user)
    result = analyze(question.strip(), rows) if question.strip() else None
    return render(
        request, "analytics.html", user=user,
        suggested=SUGGESTED, result=result, question=question, scope_count=len(rows),
    )
