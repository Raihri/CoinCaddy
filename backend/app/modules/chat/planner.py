"""Map a question to one read-only tool and its arguments. Deterministic: the same question always picks the same tool.

The planner reads only the owner's question (never record text). Requests to act (approve, pay, send, change...) are
answered with a read-only notice instead of a tool call: Finance Chat has no tool that could do them.
"""

import re
from dataclasses import dataclass, field
from typing import Any

MONTHS = {m: i for i, m in enumerate(
    ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"), 1)}
_UUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")

# Imperative requests to change something. Questions such as "can I pay Delta?" are not actions: they go to Payment Review.
ACTION = re.compile(
    r"^\s*(please\s+|pls\s+|kindly\s+|go ahead and\s+|now\s+)?"
    r"(approve|release|execute|run|submit|send|share|transfer|move|delete|remove|edit|change|update|set|mark|cancel|void|apply|"
    r"pay|post|record|create|reject|clear|unhold|disable|ignore|forget|override)\b"
    r"|\b(mark|set)\b.{0,40}\b(as\s+)?(paid|settled|approved|cleared)\b"
    r"|\b(ignore|disregard|override)\b.{0,40}\b(rule|instruction|polic|limit|hold|reserve)",
    re.IGNORECASE | re.DOTALL,
)
_SELF_QUESTION = re.compile(r"^\s*(can|could|should|would|will|do|does|is|am|are|what|when|how|which|why|where)\b", re.IGNORECASE)

KEYWORDS: dict[str, tuple[str, ...]] = {
    "payment_review": ("payment review", "review", "decide", "afford", "can i pay", "should i pay", "pay ", "paying", "supplier payment", "this payment",
                       "proposed payment", "decision", "shortfall", "scenario", "bill payment", "payment"),
    "funding_matches": ("loan", "fund", "financ", "lender", "borrow", "credit", "overdraft", "capital", "repayment", "interest"),
    "cash_outlook": ("outlook", "forecast", "next week", "next month", "13 week", "weeks", "run out", "lowest", "reserve", "breach",
                     "future", "coming", "upcoming cash", "cash flow", "cashflow", "ahead", "low point", "will i"),
    "financial_health": ("revenue", "receipt", "income", "sales", "expense", "spend", "cost", "profit", "margin", "operating",
                         "health", "dues", "owe", "owed", "overdue", "this month", "last month", "month", "earn", "bill"),
}
ORDER = ("payment_review", "funding_matches", "cash_outlook", "financial_health")


@dataclass
class Plan:
    kind: str  # "tool", "read_only", or "not_supported"
    tool: str | None = None
    args: dict[str, Any] = field(default_factory=dict)


def is_action_request(question: str) -> bool:
    if _SELF_QUESTION.match(question) and not re.search(r"\b(ignore|disregard|override)\b", question, re.IGNORECASE):
        return False
    return bool(ACTION.search(question))


def _month(question: str, today_year: int) -> str | None:
    if m := re.search(r"\b(20\d{2})-(0[1-9]|1[0-2])\b", question):
        return f"{m.group(1)}-{m.group(2)}"
    q = question.lower()
    for name, num in MONTHS.items():
        short = "sept?" if name == "september" else name[:3]  # full name or exact abbreviation: "dec" is not "decide"
        if m := re.search(rf"\b(?:{name}|{short})\b(?:\s+(20\d{{2}}))?", q):
            if name == "may" and not m.group(1):
                continue  # "may" is far more often a verb
            return f"{m.group(1) or today_year}-{num:02d}"
    return None


def score(question: str) -> dict[str, int]:
    q = f" {question.lower()} "
    return {t: sum(1 for k in KEYWORDS[t] if k in q) for t in ORDER}


def plan(question: str, *, topic: str | None, review_id: str | None, run_id: str | None, month: str | None,
         this_month: str, last_month: str, today_year: int) -> Plan:
    if is_action_request(question):
        return Plan("read_only")
    tool = topic
    if tool is None:
        scores = score(question)
        if _month(question, today_year):  # a named month points to the monthly summary
            scores["financial_health"] += 1
        best = max(ORDER, key=lambda t: (scores[t], -ORDER.index(t)))
        tool = best if scores[best] > 0 else None
    ids = _UUID.findall(question)
    if tool is None and (review_id or run_id or ids):
        tool = "funding_matches" if run_id else "payment_review"  # a bare record ID is read as a saved review
    if tool is None:
        return Plan("not_supported")
    args: dict[str, Any] = {}
    if tool == "payment_review" and (review_id or ids):
        args["review_id"] = review_id or ids[0]
    elif tool == "funding_matches" and (run_id or ids):
        args["run_id"] = run_id or ids[0]
    elif tool == "financial_health":
        last = last_month if "last month" in question.lower() else None
        args["month"] = month or _month(question, today_year) or last or this_month
    return Plan("tool", tool, args)
