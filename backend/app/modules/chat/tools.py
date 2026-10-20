"""Finance Chat's read-only tools: each one reads an existing, already calculated result and turns it into facts and citations.

Rules every tool follows:
- It calls the existing read function of its feature (the same code the feature's GET route runs) with a business context
  derived from the session, so scope and calculation are exactly those of the feature page. Nothing is recalculated here
  and no money value is added, subtracted, or estimated: every amount is copied from the stored result.
- It checks the caller's role for the feature's own read action first (deny by default, `identity.policy`).
- Arguments are validated by strict Pydantic models (extra fields refused) before anything is read.
- Text that comes from records (party names, invoice numbers, descriptions) is untrusted data: it is cleaned, shortened,
  marked `untrusted`, never sent to a language model, and scanned for embedded instructions, which are reported and ignored.
- No tool writes anything. There are no write, approval, payment, or sharing tools.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Callable, Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, StrictStr, ValidationError, field_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.modules.funding import router as funding_api
from app.modules.health_summary import router as health_api
from app.modules.identity.deps import BusinessContext
from app.modules.identity.policy import is_allowed
from app.modules.outlook import router as outlook_api
from app.modules.payment_review import router as review_api

ToolName = Literal["financial_health", "cash_outlook", "payment_review", "funding_matches"]
Status = Literal["answered", "missing_evidence", "not_found", "not_allowed"]
MAX_SOURCES = 15

# Text in a record that reads like an instruction to an assistant. Matching text is still only data: it is never followed;
# the answer just says that such text was found and ignored.
INJECTION = re.compile(
    r"(ignore|disregard|forget|override)\b.{0,40}\b(instruction|previous|above|earlier|rule|polic|prompt|guard)"
    r"|system\s*prompt|you are now|\bact as\b|developer mode|jailbreak"
    r"|\b(approve|release|execute)\b.{0,30}\b(payment|proposal|transfer|action|all|this|it)\b"
    r"|\b(send|share|email|upload|export|leak|reveal)\b.{0,30}\b(data|records|statement|packet|password|credential|account)"
    r"|\btransfer\b.{0,40}\bto\b|\bmark\b.{0,30}\b(as )?paid\b|<\s*/?\s*(system|assistant|instruction|tool)",
    re.IGNORECASE | re.DOTALL,
)


def clean_untrusted(value: str | None, limit: int = 80) -> str | None:
    """Untrusted record text, made safe to display: no control or invisible format characters, collapsed spaces, shortened."""
    if value is None:
        return None
    v = unicodedata.normalize("NFC", value)
    v = "".join(ch if not unicodedata.category(ch).startswith("C") else " " for ch in v)
    v = re.sub(r"\s+", " ", v).strip()
    return v if len(v) <= limit else v[: limit - 1].rstrip() + "…"


class ToolArgError(ValueError):
    pass


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _check_currency(v: str | None) -> str | None:
    if v is not None and not re.fullmatch(r"[A-Z]{3}", v):
        raise ValueError("currency must be a three-letter code")
    return v


class HealthArgs(_Args):
    month: StrictStr | None = None
    currency: StrictStr | None = None

    @field_validator("month")
    @classmethod
    def _month(cls, v: str | None) -> str | None:
        if v is not None and not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", v):
            raise ValueError("month must be YYYY-MM")
        return v

    validate_currency = field_validator("currency")(_check_currency)


class OutlookArgs(_Args):
    currency: StrictStr | None = None
    validate_currency = field_validator("currency")(_check_currency)


class ReviewArgs(_Args):
    review_id: UUID | None = None


class FundingArgs(_Args):
    run_id: UUID | None = None


@dataclass
class ToolResult:
    tool: str
    status: Status
    title: str
    facts: list[dict] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)
    caveats: list[dict] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)  # small, trusted, code-made hints for the drafter (enums and fact ids only)
    obligation_ids: list[str] = field(default_factory=list)  # records whose text is scanned for embedded instructions

    def fact(self, label: str, kind: str, *, source: str | None = None, **value: Any) -> str:
        fid = f"F{len(self.facts) + 1}"
        self.facts.append({"id": fid, "label": label, "kind": kind, "source": source, **value})
        return fid

    def money(self, label: str, amount_minor: int, currency: str, source: str | None = None) -> str:
        return self.fact(label, "money", source=source, amount_minor=amount_minor, currency=currency)

    def date(self, label: str, iso: str | None, source: str | None = None, *, none_text: str = "None") -> str:
        return self.fact(label, "date", source=source, value=iso) if iso else self.fact(label, "text", source=source, value=none_text)

    def source(self, kind: str, rid: str, label: str, href: str, *, untrusted: bool = False, **extra: Any) -> str:
        for s in self.sources:
            if s["kind"] == kind and s["id"] == rid:
                return s["ref"]
        if len(self.sources) >= MAX_SOURCES:
            return self.sources[-1]["ref"]
        ref = f"S{len(self.sources) + 1}"
        self.sources.append({"ref": ref, "kind": kind, "id": rid, "label": label, "href": href, "untrusted_label": untrusted, **extra})
        return ref

    def caveat(self, code: str, severity: Literal["warning", "info"], message: str, source: str | None = None) -> None:
        self.caveats.append({"code": code, "severity": severity, "message": message, "source": source})


@dataclass(frozen=True)
class Tool:
    name: ToolName
    action: str  # the feature's own read action; the caller's role must allow it
    title: str
    description: str
    args: type[_Args]
    run: Callable[[Session, BusinessContext, Any], ToolResult]


def _call(fn: Callable[..., Any], **kw: Any) -> Any:
    """Run a feature's read function and turn its HTTP errors into (status, message)."""
    try:
        return fn(**kw), None
    except HTTPException as exc:
        return None, exc


def _obligation_source(r: ToolResult, oid: str, number: str | None) -> str:
    r.obligation_ids.append(oid)
    return r.source("obligation", oid, clean_untrusted(number, 40) or "Invoice or bill", f"/records/obligations/{oid}", untrusted=True)


# --- Financial Health ---------------------------------------------------------------------------------------------------

def run_financial_health(db: Session, ctx: BusinessContext, args: HealthArgs) -> ToolResult:
    data, err = _call(health_api.financial_health, month=args.month, currency=args.currency, ctx=ctx, db=db)
    r = ToolResult("financial_health", "answered", "Financial Health")
    if err is not None:
        r.status = "missing_evidence"
        r.caveat("invalid_period", "warning", "That month or currency cannot be shown. Choose a month up to the current one and a supported currency.")
        r.summary = {"reason": "invalid_period"}
        return r
    s, cur = data["summary"], data["summary"]["currency"]
    src = r.source("financial_health", f"{s['month']}/{cur}", f"Financial Health · {s['month']} · {cur}", "/financial-health",
                   calc_version=data["calc_version"], as_of=data["as_of"])
    r.fact("Month", "month", value=s["month"], source=src)
    if s["transaction_count"] == 0:
        r.status = "missing_evidence"
        r.caveat("no_posted_records", "warning", f"No settled, posted records in {cur} for {s['month']}. Import and post statements on Records first.", src)
        r.summary = {"reason": "no_posted_records"}
    else:
        ids = sorted({i for c in s["receipt_categories"] + s["payment_categories"] for i in c["transaction_ids"]})
        txn = r.source("ledger_transactions", f"{s['month']}/{cur}", f"{len(ids)} posted operating transactions", "/financial-health",
                       count=len(ids), ids=ids[:50])
        r.summary["receipts"] = r.money("Operating receipts", s["operating_receipts_minor"], cur, txn)
        r.summary["payments"] = r.money("Operating payments", s["operating_payments_minor"], cur, txn)
        r.summary["net"] = r.money("Net operating cash", s["net_operating_cash_minor"], cur, txn)
        r.summary["net_sign"] = "negative" if s["net_operating_cash_minor"] < 0 else "zero_or_positive"
        if s["margin"]["available"]:
            r.summary["margin"] = r.fact("Operating cash margin", "percent_bp", value=s["margin"]["basis_points"], source=src)
        if s["unclassified"]["count"]:
            r.caveat("unclassified", "warning",
                     f"{s['unclassified']['count']} posted transaction(s) this month are not classified, so these totals are incomplete. Classify them on Financial Health.", src)
    for gap in data["coverage"]:
        if gap.get("uncovered_days"):
            r.caveat("coverage_gap", "warning",
                     f"{gap['label']}: {gap['uncovered_days']} day(s) this month have no declared statement coverage, so figures may be incomplete.", src)
    dues = data["customer_dues"]
    if dues and dues["total_outstanding"]:
        r.summary["dues"] = r.money("Customer dues outstanding", dues["total_outstanding"], cur, src)
        overdue = dues["overdue_1_30"] + dues["overdue_31_plus"]  # two stored buckets read together, as on the Financial Health page
        r.money("Of which overdue (not disputed)", overdue, cur, src)
        if dues["disputed"]:
            r.money("Disputed (not counted as due)", dues["disputed"], cur, src)
    up = data["upcoming_payables"]
    if up and up["total"]:
        r.summary["payables"] = r.money("Supplier bills due in the next 30 days", up["total"], cur, src)
        for item in up["items"][:8]:
            _obligation_source(r, item["id"], item["document_number"])
    if r.status == "answered" and not s["complete"]:
        r.summary["incomplete"] = True
    return r


# --- Cash Outlook -------------------------------------------------------------------------------------------------------

def run_cash_outlook(db: Session, ctx: BusinessContext, args: OutlookArgs) -> ToolResult:
    r = ToolResult("cash_outlook", "answered", "Cash Outlook")
    snap, err = _call(outlook_api.latest, ctx=ctx, db=db)
    if err is not None:
        r.status = "missing_evidence"
        r.caveat("no_outlook", "warning", "No Cash Outlook has been calculated yet. An owner or bookkeeper can calculate one on the Cash Outlook page.")
        r.summary = {"reason": "no_outlook"}
        return r
    res = snap["results"]
    base = db.execute(text("SELECT base_currency FROM business WHERE id = :b"), {"b": ctx.business_id}).scalar_one()
    cur = args.currency or base
    src = r.source("forecast_snapshot", snap["id"], f"Cash Outlook snapshot · as of {snap['as_of']}", "/cash-outlook",
                   calc_version=snap["calc_version"], inputs_sha256=snap["inputs_sha256"][:16], as_of=snap["as_of"])
    c = next((x for x in res["currencies"] if x["currency"] == cur), None)
    if c is None:
        r.status = "missing_evidence"
        r.caveat("no_balance_in_currency", "warning", f"The latest outlook has no account with a verified {cur} balance, so no {cur} path exists.", src)
        r.summary = {"reason": "no_balance_in_currency"}
        return r
    if not snap["current"]["inputs_unchanged"]:
        r.caveat("outlook_out_of_date", "warning",
                 "Records or the date changed after this outlook was calculated. Recalculate it on Cash Outlook before relying on it.", src)
        r.summary["out_of_date"] = True
    r.date("Outlook as of", snap["as_of"], src)
    r.money("Opening cash (expected path)", c["opening_expected_minor"], cur, src)
    r.summary["low_expected"] = r.money("Lowest cash, expected path", c["lowest"]["expected"]["amount_minor"], cur, src)
    r.summary["low_expected_date"] = r.date("Lowest expected on", c["lowest"]["expected"]["date"], src)
    r.summary["low_cautious"] = r.money("Lowest cash, cautious path", c["lowest"]["cautious"]["amount_minor"], cur, src)
    r.summary["low_cautious_date"] = r.date("Lowest cautious on", c["lowest"]["cautious"]["date"], src)
    r.summary["same_low"] = c["lowest"]["expected"] == c["lowest"]["cautious"]
    if c["reserve_minor"] is not None:
        r.summary["reserve"] = r.money("Reserve requirement", c["reserve_minor"], cur, src)
        for p in ("expected", "cautious"):
            b = c["first_breach"][p]
            r.summary[f"breach_{p}"] = r.date(f"First reserve breach, {p} path", b["date"] if b else None, src, none_text="None in 13 weeks")
            r.summary[f"breach_{p}_found"] = b is not None
    else:
        r.caveat("no_reserve", "info", f"No reserve is set in {cur}, so reserve breaches cannot be judged.", src)
    uncertain = [e for e in res["events"] if e["currency"] == cur and e["uncertain"] and e["amount_minor"] > 0 and e["source"] == "obligation"]
    r.fact("Uncertain receipts (overdue or disputed)", "count", value=len(uncertain), source=src)
    numbers = _document_numbers(db, ctx.business_id, [e["source_id"] for e in res["events"] if e["source"] == "obligation"])
    for e in sorted((e for e in res["events"] if e["currency"] == cur and e["source"] == "obligation"),
                    key=lambda e: (not e["uncertain"], e["expected_date"] or "9999"))[:10]:
        _obligation_source(r, e["source_id"], numbers.get(e["source_id"]))
    for ev in res["evidence"]:
        if ev["code"] == "missing_balance":
            r.caveat("missing_balance", "warning", "An account has no verified balance and is left out of both paths.", src)
        elif ev["code"] == "stale_balance":
            r.caveat("stale_balance", "warning", "An account balance is stale: the cautious path does not rely on it.", src)
        elif ev["code"] == "unmatched_transfer":
            r.caveat("unmatched_transfer", "warning", "An own-account transfer has a missing side, so a balance may be incomplete.", src)
    r.caveats = _dedupe(r.caveats)
    return r


# --- Payment Review -----------------------------------------------------------------------------------------------------

DECISIONS = {"within_reserve": "Within reserve under stated assumptions", "reserve_shortfall": "Reserve shortfall",
             "more_information_needed": "More information needed"}


def _latest_id(rows: list[dict]) -> str | None:
    return rows[0]["id"] if rows else None


def run_payment_review(db: Session, ctx: BusinessContext, args: ReviewArgs) -> ToolResult:
    r = ToolResult("payment_review", "answered", "Payment Review")
    rid = args.review_id or _latest_id(review_api.list_reviews(ctx=ctx, db=db))
    if rid is None:
        r.status = "missing_evidence"
        r.caveat("no_review", "warning", "No Payment Review has been saved yet. Run one on the Payment Review page first.")
        r.summary = {"reason": "no_review"}
        return r
    rev, err = _call(review_api.get_review, review_id=UUID(str(rid)), ctx=ctx, db=db)
    if err is not None:  # includes a review that belongs to another business
        r.status = "not_found"
        r.caveat("review_not_found", "warning", "That Payment Review was not found in this business.")
        r.summary = {"reason": "review_not_found"}
        return r
    res, cur = rev["results"], rev["results"]["currency"]
    src = r.source("payment_review", rev["id"], f"Payment Review · saved {rev['created_at'][:10]}", "/payment-review",
                   calc_version=rev["calc_version"], inputs_sha256=rev["inputs_sha256"][:16], as_of=rev["as_of"])
    pay = res["payment"]
    r.summary["decision"] = r.fact("Decision", "status", value=DECISIONS.get(res["decision"], res["decision"]), source=src)
    r.summary["decision_code"] = res["decision"]
    r.summary["amount"] = r.money("Proposed payment", pay["amount_minor"], cur, src)
    r.summary["date"] = r.date("Payment date", pay["date"], src)
    if pay["payee"]:
        r.fact("Payee", "text", value=clean_untrusted(pay["payee"]), untrusted=True, source=src)
    if pay["linked_payable"]:
        _obligation_source(r, pay["linked_payable"]["id"], pay["linked_payable"]["document_number"])
    for p in res["paths"]:
        if p["status"] == "not_assessed":
            continue
        r.money(f"{p['label']}: lowest cash", p["minimum"]["amount_minor"], cur, src)
        if p["first_breach"]:
            fid = r.date(f"{p['label']}: first reserve breach", p["first_breach"]["date"], src)
            short = r.money(f"{p['label']}: shortfall", p["shortfall_minor"], cur, src)
            r.summary.setdefault("first_breach_path", p["label"])
            r.summary.setdefault("first_breach", fid)
            r.summary.setdefault("shortfall", short)
        for link in p["links"]:
            if link["kind"] == "obligation":
                _obligation_source(r, link["id"], None)
    numbers = _document_numbers(db, ctx.business_id, r.obligation_ids)
    for s in r.sources:
        if s["kind"] == "obligation" and s["id"] in numbers:
            s["label"] = clean_untrusted(numbers[s["id"]], 40) or s["label"]
    if res["decision"] == "more_information_needed" or res["blockers"]:
        r.status = "missing_evidence" if res["decision"] == "more_information_needed" else "answered"
        for b in res["blockers"]:
            r.caveat(b["code"], "warning", BLOCKERS.get(b["code"], "A material record is missing or unresolved; see the Payment Review."), src)
    r.caveat("information_only", "info", "A Payment Review is information only. It does not approve or initiate any payment.", src)
    return r


# Code-written blocker explanations; the engine's own messages embed record names, which are untrusted.
BLOCKERS = {
    "no_reserve_in_currency": "No reserve is defined in this currency, so \"within reserve\" cannot be judged.",
    "funding_balance_missing": "The funding account has no verified balance. Enter a balance anchor before relying on this review.",
    "funding_balance_stale": "The funding account balance is stale. Verify its current balance before relying on this review.",
    "account_balance_stale": "An account in this currency has a stale balance. Verify it before relying on this review.",
    "linked_payable_disputed": "The linked bill is disputed. Resolve the dispute before paying it.",
    "unmatched_transfer": "An own-account transfer has a missing side, so a balance may be incomplete.",
}


# --- Funding Matches ----------------------------------------------------------------------------------------------------

def run_funding_matches(db: Session, ctx: BusinessContext, args: FundingArgs) -> ToolResult:
    r = ToolResult("funding_matches", "answered", "Funding Matches")
    rid = args.run_id or _latest_id(funding_api.list_runs(ctx=ctx, db=db))
    if rid is None:
        r.status = "missing_evidence"
        r.caveat("no_match_run", "warning", "No Funding Match has been saved yet. Run a comparison on the Funding Matches page first.")
        r.summary = {"reason": "no_match_run"}
        return r
    run, err = _call(funding_api.get_run, run_id=UUID(str(rid)), ctx=ctx, db=db)
    if err is not None:
        r.status = "not_found"
        r.caveat("match_not_found", "warning", "That Funding Match was not found in this business.")
        r.summary = {"reason": "match_not_found"}
        return r
    res, cur = run["results"], run["results"]["currency"]
    src = r.source("funding_match_run", run["id"], f"Funding Match · saved {run['created_at'][:10]}", "/funding-matches",
                   calc_version=run["calc_version"], catalogue_version=run["catalogue_version"], catalogue_updated=res["catalogue_updated"],
                   inputs_sha256=run["inputs_sha256"][:16], as_of=run["as_of"])
    r.summary["need"] = r.money("Amount needed", res["need"]["amount_minor"], cur, src)
    r.date("Needed by", res["need"]["needed_by"], src)
    r.summary["eligible"] = r.fact("Eligible illustrative products", "count", value=len(res["eligible"]), source=src)
    r.fact("Excluded products", "count", value=len(res["excluded"]), source=src)
    r.summary["eligible_count"] = len(res["eligible"])
    if res["eligible"]:
        top = res["eligible"][0]
        r.summary["top"] = r.fact("Top-ranked match", "text", value=f"{top['name']} ({top['provider']})", source=src)
        r.money("Its net proceeds", top["terms"]["net_proceeds_minor"], cur, src)
        r.summary["top_cost"] = r.money("Its total cost (fees and interest)", top["terms"]["total_cost_minor"], cur, src)
        r.summary["top_repay"] = r.money("Its total repayment", top["terms"]["total_repayment_minor"], cur, src)
        r.date("Its first repayment", (top["terms"]["schedule"] or [{}])[0].get("date"), src)
        cautious = top["forecast"].get("cautious")
        if cautious:
            r.summary["top_low"] = r.money("Lowest cash with it, cautious path", cautious["lowest"]["amount_minor"], cur, src)
            r.summary["top_breach_found"] = cautious["first_breach"] is not None
            r.date("Reserve breach with it, cautious path", cautious["first_breach"]["date"] if cautious["first_breach"] else None, src,
                   none_text="None in 13 weeks")
    else:
        r.caveat("no_eligible", "warning", "No illustrative product passed the eligibility filters; the reasons are listed on Funding Matches.", src)
    r.caveat("illustrative", "info", "Matches are illustrative: they are not a lender approval, and no business data was shared.", src)
    return r


# --- Shared helpers -----------------------------------------------------------------------------------------------------

def _document_numbers(db: Session, business_id: UUID, ids: list[str]) -> dict[str, str]:
    valid = [UUID(i) for i in ids if _is_uuid(i)]
    if not valid:
        return {}
    rows = db.execute(text("SELECT id, document_number FROM obligation WHERE business_id = :b AND id = ANY(:ids)"),
                      {"b": business_id, "ids": valid})
    return {str(r.id): r.document_number for r in rows}


def _is_uuid(v: str) -> bool:
    try:
        UUID(str(v))
        return True
    except ValueError:
        return False


def _dedupe(items: list[dict]) -> list[dict]:
    seen, out = set(), []
    for c in items:
        if c["code"] not in seen:
            seen.add(c["code"])
            out.append(c)
    return out


def scan_untrusted(db: Session, business_id: UUID, result: ToolResult) -> list[str]:
    """Look for instruction-like text in every record the answer cites (and the payee). Returns the matching record IDs."""
    hits: list[str] = []
    ids = sorted({i for i in result.obligation_ids if _is_uuid(i)})
    if ids:
        rows = db.execute(text(
            """SELECT o.id, o.document_number, o.description, o.source_reference, o.dispute_reason, p.name AS party
               FROM obligation o JOIN party p ON p.business_id = o.business_id AND p.id = o.party_id
               WHERE o.business_id = :b AND o.id = ANY(:ids)"""), {"b": business_id, "ids": [UUID(i) for i in ids]})
        for row in rows:
            blob = " ".join(x for x in (row.document_number, row.description, row.source_reference, row.dispute_reason, row.party) if x)
            if INJECTION.search(blob):
                hits.append(str(row.id))
    for f in result.facts:
        if f.get("untrusted") and INJECTION.search(f.get("value") or ""):
            hits.append(f["id"])
    return hits


TOOLS: dict[str, Tool] = {t.name: t for t in (
    Tool("financial_health", "records.read", "Financial Health",
         "Cash-basis operating receipts, payments, net operating cash, margin, customer dues and upcoming bills for one month.",
         HealthArgs, run_financial_health),
    Tool("cash_outlook", "records.read", "Cash Outlook",
         "The latest saved 13-week Cash Outlook: lowest cash on the expected and cautious paths, reserve breaches, uncertain receipts.",
         OutlookArgs, run_cash_outlook),
    Tool("payment_review", "paymentreview.read", "Payment Review",
         "A saved Payment Review (latest by default): decision, lowest cash and shortfall on each of the four paths, blockers.",
         ReviewArgs, run_payment_review),
    Tool("funding_matches", "funding.read", "Funding Matches",
         "A saved illustrative Funding Match (latest by default): eligible and excluded products, top match cost and cash effect.",
         FundingArgs, run_funding_matches),
)}


def validate_args(name: str, raw: dict[str, Any]) -> _Args:
    tool = TOOLS.get(name)
    if tool is None:
        raise ToolArgError(f"unknown tool {name!r}")
    if not isinstance(raw, dict):
        raise ToolArgError("tool arguments must be an object")
    try:
        return tool.args.model_validate(raw)
    except ValidationError as exc:
        raise ToolArgError("; ".join(f"{'.'.join(str(p) for p in e['loc']) or 'arguments'}: {e['msg']}" for e in exc.errors())) from exc


def run_tool(db: Session, ctx: BusinessContext, name: str, raw_args: dict[str, Any]) -> ToolResult:
    """Validate, authorize, and run one read-only tool for the session's business. Never writes."""
    tool = TOOLS[name]
    args = validate_args(name, raw_args)
    if not is_allowed(ctx.role, tool.action):
        r = ToolResult(name, "not_allowed", tool.title)  # the answer text says why; no figures, sources, or caveats
        r.summary = {"reason": "role_not_allowed"}
        return r
    scoped = BusinessContext(ctx.user, ctx.business_id, ctx.membership_id, ctx.role, tool.action)
    return tool.run(db, scoped, args)
