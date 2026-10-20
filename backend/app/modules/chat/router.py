"""Finance Chat API: ask a question about existing calculated results and get a cited, read-only answer.

Business scope comes from the session (`require`), never from the question. A question selects at most one read-only
tool (`chat.tools`); the tool checks the caller's role for its feature, validates its arguments, and reads the stored
result. Nothing here writes a ledger row, approves an action, or calls an external service; the only writes are the
append-only chat record and its audit event.
"""

import hashlib
import re
import unicodedata
from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, StrictStr, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.modules.audit.redaction import _DIGIT_RUN, mask_digits
from app.modules.audit.service import Actor, record_event
from app.modules.chat import drafter
from app.modules.chat.planner import plan
from app.modules.chat.tools import TOOLS, ToolArgError, ToolResult, run_tool, scan_untrusted
from app.modules.identity.deps import BusinessContext, require
from app.modules.identity.policy import is_allowed
from app.modules.outlook.service import canonical
from app.modules.records.service import business_today

router = APIRouter(prefix="/api/businesses/{business_id}/chat", tags=["finance chat"])
QUESTION_MAX = 500
SUGGESTIONS = [
    {"topic": "financial_health", "question": "How did operating cash do this month?"},
    {"topic": "cash_outlook", "question": "When is cash lowest in the next 13 weeks?"},
    {"topic": "payment_review", "question": "What did my latest Payment Review decide?"},
    {"topic": "funding_matches", "question": "Which funding match fits best, and what does it cost?"},
]


class AskIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: StrictStr
    topic: Literal["financial_health", "cash_outlook", "payment_review", "funding_matches"] | None = None
    review_id: UUID | None = None
    run_id: UUID | None = None
    month: StrictStr | None = None

    @field_validator("question")
    @classmethod
    def _question(cls, v: str) -> str:
        v = unicodedata.normalize("NFC", v)
        if any(unicodedata.category(ch) in ("Cc", "Cf", "Co", "Cs") and ch not in "\n\t" for ch in v):
            raise ValueError("must not contain control or invisible characters")
        v = re.sub(r"\s+", " ", v).strip()
        if len(v) < 2:
            raise ValueError("ask a question")
        if len(v) > QUESTION_MAX:
            raise ValueError(f"keep the question under {QUESTION_MAX} characters")
        return v

    @field_validator("month")
    @classmethod
    def _month(cls, v: str | None) -> str | None:
        if v is not None and not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", v):
            raise ValueError("month must be YYYY-MM")
        return v

    @model_validator(mode="after")
    def _ids_match_topic(self) -> "AskIn":
        if self.review_id and self.topic not in (None, "payment_review"):
            raise ValueError("review_id is only used with the payment_review topic")
        if self.run_id and self.topic not in (None, "funding_matches"):
            raise ValueError("run_id is only used with the funding_matches topic")
        if self.month and self.topic not in (None, "financial_health"):
            raise ValueError("month is only used with the financial_health topic")
        if self.review_id and self.run_id:
            raise ValueError("give either review_id or run_id, not both")
        return self


def _months(today: date) -> tuple[str, str]:
    last = date(today.year - (today.month == 1), (today.month - 2) % 12 + 1, 1)
    return f"{today.year:04d}-{today.month:02d}", f"{last.year:04d}-{last.month:02d}"


def _row(r) -> dict:
    return {"id": str(r["id"]), "created_at": r["created_at"].isoformat(), "question": r["question"], "status": r["status"],
            "tool": r["tool"], "assistant": r["assistant"], "tool_calls": r["tool_calls"], "answer": r["answer"]}


@router.get("/tools")
def tools(ctx: BusinessContext = Depends(require("chat.read"))) -> dict:
    return {"read_only": True, "assistant": "model" if drafter.model_drafter() else "template",
            "tools": [{"name": t.name, "title": t.title, "description": t.description, "allowed": is_allowed(ctx.role, t.action)}
                      for t in TOOLS.values()],
            "suggestions": [s for s in SUGGESTIONS if is_allowed(ctx.role, TOOLS[s["topic"]].action)],
            "question_max": QUESTION_MAX}


@router.post("/questions", status_code=status.HTTP_201_CREATED)
def ask(body: AskIn, ctx: BusinessContext = Depends(require("chat.ask")), db: Session = Depends(get_session)) -> dict:
    tz = db.execute(text("SELECT timezone FROM business WHERE id = :b"), {"b": ctx.business_id}).scalar_one()
    today = business_today(tz)
    this_month, last_month = _months(today)
    p = plan(body.question, topic=body.topic, review_id=str(body.review_id) if body.review_id else None,
             run_id=str(body.run_id) if body.run_id else None, month=body.month,
             this_month=this_month, last_month=last_month, today_year=today.year)
    result: ToolResult | None = None
    if p.kind == "tool":
        try:
            result = run_tool(db, ctx, p.tool, p.args)
        except ToolArgError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Invalid tool arguments: {exc}") from exc
        answer_status = result.status
    else:
        answer_status = p.kind

    actor = Actor(ctx.user.id, ctx.user.display_name, ctx.role)
    hits: list[str] = []
    if result is not None:
        hits = scan_untrusted(db, ctx.business_id, result)
        if hits:
            refs = [s["ref"] for s in result.sources if s["id"] in hits]
            result.caveat("record_text_ignored", "warning",
                          "A cited record contains text that reads like an instruction. Finance Chat treats record text as data "
                          "and did not act on it; nothing was approved, sent, or changed.", refs[0] if refs else None)
        if answer_status in ("not_allowed", "not_found"):
            record_event(db, action=TOOLS[p.tool].action, outcome="denied" if answer_status == "not_allowed" else "not_found",
                         actor=actor, business_id=ctx.business_id,
                         details={"via": "finance_chat", "tool": p.tool, **{k: str(v) for k, v in p.args.items()}})

    text_out, drafted_by = None, "template"
    model = drafter.model_drafter()
    if model is not None and result is not None and answer_status in ("answered", "missing_evidence"):
        text_out = model.draft(body.question, answer_status, result)
        drafted_by = "model" if text_out else "template"
    if text_out is None:
        text_out = drafter.template_draft(answer_status, result)

    answer = {"title": result.title if result else ("Read-only" if answer_status == "read_only" else "Finance Chat"),
              "text": text_out, "drafted_by": drafted_by, "read_only": True,
              "reason": result.summary.get("reason") if result else None,  # the caveat the text already states
              "facts": result.facts if result else [], "caveats": result.caveats if result else [],
              "sources": result.sources if result else []}
    calls = [{"tool": p.tool, "args": {k: str(v) for k, v in p.args.items()}, "outcome": answer_status}] if p.tool else []
    stored_question = _DIGIT_RUN.sub(lambda m: mask_digits(m.group(0)), body.question)
    xid = db.execute(text(
        """INSERT INTO chat_exchange (business_id, user_id, role, question, question_sha256, tool, status, assistant, tool_calls, answer)
           VALUES (:b, :u, :r, :q, :h, :t, :s, :a, CAST(:c AS jsonb), CAST(:ans AS jsonb)) RETURNING id"""),
        {"b": ctx.business_id, "u": ctx.user.id, "r": ctx.role, "q": stored_question,
         "h": hashlib.sha256(body.question.encode()).hexdigest(), "t": p.tool, "s": answer_status, "a": drafted_by,
         "c": canonical(calls), "ans": canonical(answer)}).scalar_one()
    record_event(db, action="chat.ask", outcome="success", actor=actor, business_id=ctx.business_id,
                 target_type="chat_exchange", target_id=xid,
                 details={"tool": p.tool, "status": answer_status, "assistant": drafted_by, "question_length": len(body.question),
                          "sources": len(answer["sources"]), "untrusted_instruction_records": len(hits)})
    db.commit()
    row = db.execute(text("SELECT * FROM chat_exchange WHERE business_id = :b AND id = :i"), {"b": ctx.business_id, "i": xid}).mappings().one()
    return _row(row)


@router.get("/questions")
def history(limit: int = Query(20, ge=1, le=50), ctx: BusinessContext = Depends(require("chat.read")),
            db: Session = Depends(get_session)) -> list[dict]:
    """Your own questions in this business, newest first."""
    rows = db.execute(text(
        "SELECT * FROM chat_exchange WHERE business_id = :b AND user_id = :u ORDER BY created_at DESC, id LIMIT :n"),
        {"b": ctx.business_id, "u": ctx.user.id, "n": limit}).mappings().all()
    return [_row(r) for r in rows]
