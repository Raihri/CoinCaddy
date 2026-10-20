"""The short summary sentence(s) of a Finance Chat answer.

The text never carries a number: it refers to the tool's facts by placeholder (`[[F3]]`) and the browser renders each
value from the calculated fact. Two drafters exist:

- **template** (default): fixed sentences chosen by code from the tool result. No model is involved.
- **model** (optional, `FINANCE_CHAT_ASSISTANT=claude` with the `anthropic` package and a key): Claude is given the
  question, fact IDs with labels, and caveat codes. It gets no tools, no credentials, no record text, and no amounts.
  Its text is accepted only if `validate_draft` passes (placeholders only, no digits or currency, no links, no claim
  that anything was done, at most three sentences); otherwise the template is used. Every failure falls back silently.
"""

import json
import re
from typing import Any

from app.config import get_settings
from app.modules.chat.tools import ToolResult

PLACEHOLDER = re.compile(r"\[\[(F\d{1,3})\]\]")
MAX_CHARS = 500
MAX_SENTENCES = 3
_FORBIDDEN = re.compile(
    r"[0-9০-৯৳$€£¥<>`*#|\\{}]|\bBDT\b|\bTk\b|\btaka\b|https?:|www\.|@|[\[\]]"
    r"|\b(approved|authori[sz]ed|released|executed|submitted|shared|sent|transferred|initiated|scheduled|i have|i've|i will|i'll)\b",
    re.IGNORECASE,
)
_FILLER = re.compile(r"^(as an ai|i am an ai|i'm an ai|i hope|let me know|feel free|great question|sure\b|certainly\b|of course\b|"
                     r"happy to help|i'm happy|hello|hi\b|thanks|thank you|here is|here's)", re.IGNORECASE)

SYSTEM = (
    "You write the short summary for a read-only finance chat in an SME finance app. You receive the owner's question and "
    "facts that the app has already calculated, each with an id such as F2. Answer the question in at most three short, "
    "plain sentences. Refer to every amount, date, count, or name only by its placeholder written exactly like [[F2]]; "
    "never write digits, currency symbols, or dates yourself. Do not recommend approving, paying, sending, or changing "
    "anything, and never say that an action was taken. If the status is missing_evidence, say what is missing. "
    "Write no greeting, sign-off, or remark about yourself."
)


def validate_draft(draft: str | None, fact_ids: set[str]) -> str | None:
    """Return the cleaned draft, or None if it breaks any rule (the caller then uses the template)."""
    if not isinstance(draft, str):
        return None
    t = re.sub(r"\s+", " ", draft).strip()
    if not t or len(t) > 2 * MAX_CHARS:
        return None
    used = PLACEHOLDER.findall(t)
    if any(u not in fact_ids for u in used) or (fact_ids and not used):
        return None
    if _FORBIDDEN.search(PLACEHOLDER.sub("", t)):
        return None
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", t) if s and not _FILLER.match(s)]
    t = " ".join(sentences[:MAX_SENTENCES]).strip()
    if not t or len(t) > MAX_CHARS or (fact_ids and not PLACEHOLDER.search(t)):
        return None
    return t


def template_draft(status: str, result: ToolResult | None) -> str:
    if status == "read_only":
        return ("Finance Chat is read-only. It cannot approve, pay, send, share, or change records; "
                "use the relevant page, where the approval workflow applies.")
    if status == "not_supported":
        return "Finance Chat answers questions about Financial Health, Cash Outlook, Payment Review, and Funding Matches."
    assert result is not None
    s = result.summary
    if status == "not_allowed":
        return f"Your role cannot see {result.title} results, so this question cannot be answered here."
    if status == "not_found":
        return f"That {result.title} record was not found in this business."
    return {"financial_health": _health, "cash_outlook": _outlook, "payment_review": _review, "funding_matches": _funding}[result.tool](status, s)


def _health(status: str, s: dict) -> str:
    if status == "missing_evidence":
        return ("Financial Health has no settled, posted records for [[F1]]. Import and post statements on Records first."
                if s.get("reason") == "no_posted_records"
                else "That month or currency cannot be shown in Financial Health.")
    t = f"In [[F1]], operating receipts were [[{s['receipts']}]] and operating payments [[{s['payments']}]], so net operating cash was [[{s['net']}]]."
    if "margin" in s:
        t += f" The operating cash margin was [[{s['margin']}]]."
    if s.get("incomplete"):
        t += " Some transactions are not classified yet, so these totals are incomplete."
    return t


def _outlook(status: str, s: dict) -> str:
    if status == "missing_evidence":
        return {"no_outlook": "No Cash Outlook has been calculated yet. An owner or bookkeeper can calculate one on the Cash Outlook page.",
                "no_balance_in_currency": "The latest Cash Outlook has no verified balance in that currency."}.get(s.get("reason"), "The Cash Outlook is missing evidence.")
    if s.get("same_low"):
        t = f"On the latest outlook, cash is lowest at [[{s['low_expected']}]] on [[{s['low_expected_date']}]] on both the expected and cautious paths."
    else:
        t = (f"On the latest outlook, cash is lowest at [[{s['low_expected']}]] on [[{s['low_expected_date']}]] on the expected path, "
             f"and at [[{s['low_cautious']}]] on [[{s['low_cautious_date']}]] on the cautious path.")
    if "reserve" in s:
        if s.get("breach_expected_found"):
            t += f" Cash falls below the reserve of [[{s['reserve']}]] from [[{s['breach_expected']}]] even on the expected path."
        elif s.get("breach_cautious_found"):
            t += f" The expected path stays above the reserve of [[{s['reserve']}]], but the cautious path falls below it from [[{s['breach_cautious']}]]."
        else:
            t += f" Neither path falls below the reserve of [[{s['reserve']}]] within 13 weeks."
    if s.get("out_of_date"):
        t += " Records changed after it was calculated, so recalculate it before relying on it."
    return t


def _review(status: str, s: dict) -> str:
    if s.get("reason") == "no_review":
        return "No Payment Review has been saved yet. Run one on the Payment Review page first."
    if status == "missing_evidence":
        return (f"The latest Payment Review for [[{s['amount']}]] on [[{s['date']}]] cannot give a positive result: [[{s['decision']}]]. "
                "Resolve the items listed below first.")
    t = f"The latest Payment Review for [[{s['amount']}]] on [[{s['date']}]] says: [[{s['decision']}]]."
    if "first_breach" in s:
        t += f" In the “{s['first_breach_path']}” scenario, cash first falls below the reserve on [[{s['first_breach']}]], a shortfall of [[{s['shortfall']}]]."
    elif s.get("decision_code") == "within_reserve":
        t += " Every assessed path stays within the reserve under the stated assumptions."
    return t


def _funding(status: str, s: dict) -> str:
    if status == "missing_evidence":
        return "No Funding Match has been saved yet. Run a comparison on the Funding Matches page first."
    t = f"The latest Funding Match for [[{s['need']}]] found [[{s['eligible']}]] eligible illustrative product(s)."
    if "top" in s:
        t += f" The top-ranked is [[{s['top']}]], with total repayment of [[{s['top_repay']}]] and total cost of [[{s['top_cost']}]]."
        if "top_low" in s:
            t += (" Even with it, the cautious path still falls below the reserve." if s.get("top_breach_found")
                  else " With it, the cautious path stays above the reserve.")
    return t + " This is not a lender approval."


class ModelDrafter:
    """Asks Claude for the summary sentence. `client` is an `anthropic.Anthropic` (or a test double with `.messages.create`)."""

    def __init__(self, client: Any, model: str):
        self.client, self.model = client, model

    def draft(self, question: str, status: str, result: ToolResult) -> str | None:
        facts = [{"id": f["id"], "label": f["label"], "kind": f["kind"], **({"value": f["value"]} if f["kind"] == "status" else {})}
                 for f in result.facts if not f.get("untrusted")]
        payload = {"question": question, "topic": result.title, "status": status, "facts": facts,
                   "caveats": [c["code"] for c in result.caveats]}
        try:
            msg = self.client.messages.create(
                model=self.model, max_tokens=2000, output_config={"effort": "low"}, system=SYSTEM,
                messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            )
        except Exception:  # noqa: BLE001 - any API or network failure falls back to the template; answers never depend on the model
            return None
        if getattr(msg, "stop_reason", None) not in ("end_turn", "stop_sequence"):
            return None  # refusal, max_tokens, or anything unexpected
        text = " ".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
        return validate_draft(text, {f["id"] for f in result.facts})


_override: Any = None  # tests install a drafter here


def model_drafter() -> ModelDrafter | None:
    if _override is not None:
        return _override
    settings = get_settings()
    if settings.finance_chat_assistant != "claude":
        return None
    try:
        import anthropic  # optional dependency: pip install anthropic
    except ImportError:
        return None
    return ModelDrafter(anthropic.Anthropic(timeout=20.0, max_retries=1), settings.finance_chat_model)
