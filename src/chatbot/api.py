"""FastAPI application — chatbot, health, feedback, and evals endpoints."""
import time
import uuid
from collections import defaultdict
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.chatbot import handler as chatbot_handler
from src.config import settings
from src.db.connection import get_session
from src.db.orm_models import DraftSkillORM, FalloutFeedbackORM, FalloutRunORM, SkillORM
from src.ehap.client import EHAPClient
from src.models.skill import ChatRequest, ChatResponse

app = FastAPI(title="Fallout Agent — Business Chatbot", version="0.1.0")

# Per-session conversation history (in-memory; replace with Redis for multi-instance)
_sessions: dict[str, list[dict[str, Any]]] = defaultdict(list)

# Shared EHAP client
_ehap = EHAPClient()


@app.get("/health")
async def health(session: AsyncSession = Depends(get_session)) -> dict:
    db_ok = False
    ehap_ok = False
    try:
        await session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        pass
    try:
        # Light-weight connectivity check — just HEAD or a stub ping
        import httpx
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(settings.ehap_base_url + "/health")
            ehap_ok = r.status_code < 500
    except Exception:
        pass
    return {"db": "ok" if db_ok else "error", "ehap": "ok" if ehap_ok else "error"}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, session: AsyncSession = Depends(get_session)) -> ChatResponse:
    history = _sessions[req.session_id]
    reply, updated_history = await chatbot_handler.handle_message(
        message=req.message,
        history=history,
        ehap=_ehap,
        session=session,
    )
    _sessions[req.session_id] = updated_history
    return ChatResponse(session_id=req.session_id, message=reply)


class FeedbackRequest:
    def __init__(self, transaction_id: str, run_id: str | None, resolution_steps: str, outcome: str):
        self.transaction_id = transaction_id
        self.run_id = run_id
        self.resolution_steps = resolution_steps
        self.outcome = outcome


from pydantic import BaseModel


class FeedbackPayload(BaseModel):
    transaction_id: str
    run_id: str | None = None
    resolution_steps: str
    outcome: str


@app.post("/feedback")
async def feedback(payload: FeedbackPayload, session: AsyncSession = Depends(get_session)) -> dict:
    run_id = uuid.UUID(payload.run_id) if payload.run_id else None
    fb = FalloutFeedbackORM(
        transaction_id=payload.transaction_id,
        run_id=run_id,
        resolution_steps=payload.resolution_steps,
        outcome=payload.outcome,
    )
    session.add(fb)
    await session.commit()
    return {"status": "recorded"}


@app.get("/evals")
async def evals_dashboard(session: AsyncSession = Depends(get_session)) -> dict:
    """Return skill effectiveness metrics for the last 7 days."""
    rows = await session.execute(
        text("""
            SELECT
                s.id,
                s.name,
                COUNT(fr.id) AS total_runs,
                SUM(CASE WHEN fr.status = 'resolved' THEN 1 ELSE 0 END) AS resolved,
                SUM(CASE WHEN fr.status = 'escalated' THEN 1 ELSE 0 END) AS escalated,
                AVG(fr.tool_call_count) AS avg_tool_calls,
                AVG(fr.duration_ms) AS avg_duration_ms
            FROM skills s
            LEFT JOIN fallout_runs fr
                ON fr.skill_id = s.id AND fr.created_at >= NOW() - INTERVAL '7 days'
            GROUP BY s.id, s.name
            ORDER BY s.name
        """)
    )
    skills = []
    for row in rows:
        total = row.total_runs or 0
        resolved = row.resolved or 0
        rate = round(resolved / total, 3) if total else None
        skills.append({
            "skill_id": str(row.id),
            "name": row.name,
            "total_runs": total,
            "resolved": resolved,
            "escalated": row.escalated or 0,
            "resolution_rate": rate,
            "needs_review": rate is not None and rate < settings.skill_effectiveness_min_rate,
            "avg_tool_calls": round(float(row.avg_tool_calls or 0), 1),
            "avg_duration_ms": round(float(row.avg_duration_ms or 0)),
        })

    drafts_result = await session.execute(
        select(DraftSkillORM).where(DraftSkillORM.status == "pending").order_by(DraftSkillORM.trigger_count.desc())
    )
    drafts = [
        {"error_code": d.error_code, "trigger_count": d.trigger_count, "id": str(d.id)}
        for d in drafts_result.scalars()
    ]

    return {"skills": skills, "pending_draft_skills": drafts}


@app.get("/", response_class=HTMLResponse)
async def index() -> FileResponse:
    return FileResponse("src/chatbot/static/index.html")
