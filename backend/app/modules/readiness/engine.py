"""Financing Readiness engine, calculation version readiness-v1. Pure: frozen inputs in, a checklist out.

For ONE illustrative product of a saved Funding Match, every condition the product publishes is checked against
the business's own records and its document register, and reported as exactly one of:
- present:   the records or a register entry show it is met; the source is named.
- missing:   it is not met, or the document is absent or expired; a concrete improvement is given.
- uncertain: it cannot be confirmed from what is held (declared only, records incomplete, figures that do not
             reconcile, a document expiring soon); the verification step is given.

Overall state: 'ready' only when every condition is present; 'not_ready' when any is missing; otherwise
'needs_verification'. A declaration in the funding form is never enough for 'present'.

Rules that need a source:
- Revenue: average monthly classified operating receipts over the last 90 days (the Funding Matches figure).
  It is present only if it meets the minimum AND no account with movements in the window has a statement
  coverage gap AND no inflow in the window is unclassified. Accounts with no records in the window are treated
  as unused and say so.
- Bank statements / wallet history: every bank (or wallet) account must have declared statement coverage for the
  whole window up to two days ago; an account whose figures do not reconcile makes it uncertain.
- Invoices: outstanding customer invoices in Receivables.
- Business age: the earliest posted transaction or a registered trade licence's issue date; a declaration alone
  is uncertain.
- Guarantor / collateral: a registered guarantor letter or collateral valuation; a declaration alone is uncertain.
- ID, trade licence, tax certificate: a register entry that has not expired before the funds are needed.

Not used, by design: any personal trait of the owner or staff (age, gender, religion, family, location beyond
the business's jurisdiction, and so on), the product's matching rank, and any approval probability or score.
Readiness is a checklist of published conditions, not a prediction.
"""

from datetime import date, timedelta

from app.contracts.money import MINOR_UNIT_EXPONENT
from app.modules.cash.service import find_gaps, merge_intervals

CALC_VERSION = "readiness-v1"
WINDOW_DAYS = 90
STATEMENT_LAG_DAYS = 2
EXPIRY_SOON_DAYS = 30
REGISTER_KINDS = ("id", "trade_licence", "tax_certificate", "guarantor_letter", "collateral_valuation")
EXPIRY_EXPECTED = ("trade_licence", "tax_certificate")
DOC_LABEL = {"bank_statements": "Bank statements", "wallet_history": "Mobile wallet history", "invoices": "Customer invoices",
             "id": "Owner ID", "trade_licence": "Trade licence", "tax_certificate": "Tax certificate",
             "guarantor_letter": "Guarantor letter", "collateral_valuation": "Collateral valuation"}
SECURITY_DOC = {"guarantor": "guarantor_letter", "collateral": "collateral_valuation"}
PURPOSE_LABEL = {"working_capital": "working capital", "inventory": "inventory", "invoice_gap": "an invoice gap", "equipment": "equipment", "other": "other"}
# The only facts the rules read. Kept explicit so a test can prove no personal trait is an input.
INPUT_FIELDS = ("as_of", "jurisdiction", "run", "product", "records", "documents")
NOTICE = ("Readiness lists the product's published conditions and what your records show. It is not an approval, a probability, or a score, "
          "it is separate from the match ranking, and nothing has been sent to any lender.")


class ReadinessError(ValueError):
    """The check cannot be made on these inputs (shown as a 422)."""


def _d(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


def fmt(minor: int, cur: str) -> str:
    """The same display as the app's formatMoney: BDT as "৳ 1,00,000.00" (South Asian grouping, design.md), others "USD 100,000.00"."""
    e = MINOR_UNIT_EXPONENT.get(cur, 2)
    sign, v = ("-" if minor < 0 else ""), abs(minor)
    whole, frac = divmod(v, 10**e) if e else (v, 0)
    w = str(whole)
    if cur == "BDT" and len(w) > 3:
        head, tail = w[:-3], w[-3:]
        w = ",".join([head[max(0, i - 2):i] for i in range(len(head), 0, -2)][::-1]) + "," + tail
    else:
        w = f"{whole:,}"
    return f"{sign}{'৳' if cur == 'BDT' else cur} {w}" + (f".{frac:0{e}d}" if e else "")


def months_between(a: date, b: date) -> int:
    """Whole calendar months from a to b."""
    return max(0, (b.year - a.year) * 12 + b.month - a.month - (b.day < a.day))


def _span(gaps: list[tuple[date, date]]) -> str:
    return "; ".join(s.isoformat() if s == e else f"{s.isoformat()} to {e.isoformat()}" for s, e in gaps)


def _c(code, label, requirement, status, finding, source, verification, improvement=None):
    return {"code": code, "label": label, "requirement": requirement, "status": status, "finding": finding,
            "source": source, "verification": verification, "improvement": None if status == "present" else improvement}


def _window(inputs: dict) -> tuple[date, date]:
    as_of = _d(inputs["as_of"])
    return as_of - timedelta(days=WINDOW_DAYS - 1), as_of - timedelta(days=STATEMENT_LAG_DAYS)


def _account_gaps(acc: dict, start: date, end: date) -> list[tuple[date, date]]:
    merged = merge_intervals([(_d(c["start"]), _d(c["end"])) for c in acc["coverage"]])
    return find_gaps(merged, start, end)


def _live_docs(inputs: dict, kind: str) -> list[dict]:
    return sorted((x for x in inputs["documents"] if x["kind"] == kind), key=lambda x: (x["expires_on"] or "9999-12-31", x["recorded_at"]), reverse=True)


def _register_doc(inputs: dict, kind: str, code: str, label: str, requirement: str, declared: bool) -> dict:
    """A document the register must hold, valid on the date the funds are needed."""
    as_of, needed = _d(inputs["as_of"]), _d(inputs["run"]["request"]["needed_by"])
    docs = _live_docs(inputs, kind)
    noun = label[0].lower() + label[1:]   # "owner ID", "trade licence"
    if not docs:
        if declared:
            return _c(code, label, requirement, "uncertain", f"Declared in the funding need, but no {noun} is recorded in the document register.",
                      "Owner declaration in the saved funding need (not verified).",
                      f"Record the {noun} in the document register with where it is kept and its expiry date.",
                      f"Record the {noun} in the document register so it can be checked.")
        return _c(code, label, requirement, "missing", f"No {noun} is recorded in the document register.", "Document register: no entry.",
                  f"Find the {noun} and record it in the document register.",
                  f"Obtain the {noun} and record it in the document register before applying.")
    doc = docs[0]
    src = f"Document register: \"{doc['reference']}\", recorded by {doc['recorded_by']} on {doc['recorded_at'][:10]}" + \
          (f", issued {doc['issued_on']}" if doc["issued_on"] else "") + (f", expires {doc['expires_on']}" if doc["expires_on"] else "") + \
          ". Recorded by the business; the lender will check the original."
    exp = _d(doc["expires_on"])
    if exp is not None and exp < as_of:
        return _c(code, label, requirement, "missing", f"The recorded {noun} expired on {doc['expires_on']}.", src,
                  "Check the expiry date on the original.", f"Renew the {noun} and record the new one (then void the expired entry).")
    if exp is not None and exp < needed:
        return _c(code, label, requirement, "missing", f"The recorded {noun} expires on {doc['expires_on']}, before the funds are needed on {needed.isoformat()}.", src,
                  "Check the expiry date on the original.", f"Renew the {noun} before {doc['expires_on']} and record the renewed one.")
    if exp is not None and exp <= needed + timedelta(days=EXPIRY_SOON_DAYS):
        return _c(code, label, requirement, "uncertain", f"The recorded {noun} expires on {doc['expires_on']}, within {EXPIRY_SOON_DAYS} days after the funds are needed.", src,
                  "A lender may ask for a document that is valid through the review; confirm its renewal date.",
                  f"Renew the {noun} early, or confirm with the lender that it is acceptable until {doc['expires_on']}.")
    if exp is None and kind in EXPIRY_EXPECTED:
        return _c(code, label, requirement, "uncertain", f"A {noun} is recorded but without an expiry date; it is usually renewed each year.", src,
                  "Check the expiry date on the original.", f"Void the entry and record the {noun} again with its expiry date.")
    return _c(code, label, requirement, "present", f"Recorded and valid on {needed.isoformat()}" + ("." if exp is None else f" (expires {doc['expires_on']})."), src,
              "The lender will check the original document.")


def _statements(inputs: dict, kind: str, code: str, label: str, requirement: str) -> dict:
    start, end = _window(inputs)
    cur = inputs["run"]["request"]["currency"]
    accs = [a for a in inputs["records"]["accounts"] if a["kind"] == kind and a["currency"] == cur]
    noun = "bank" if kind == "bank" else "mobile wallet"
    if not accs:
        return _c(code, label, requirement, "missing", f"The business has no {noun} account in {cur} set up.", "Business setup: accounts.",
                  f"Add the {noun} account in Business setup.", f"Add the {noun} account, import its statements for {start.isoformat()} to {end.isoformat()}, and declare the coverage.")
    gaps = {a["id"]: _account_gaps(a, start, end) for a in accs}
    src = "; ".join(f"{a['label']}: declared statement coverage " + (", ".join(f"{c['start']} to {c['end']}" for c in a["coverage"]) or "none") for a in accs) + "."
    short = [a for a in accs if gaps[a["id"]]]
    if short:
        detail = "; ".join(f"{a['label']} has no statement coverage for {_span(gaps[a['id']])}" for a in short)
        return _c(code, label, requirement, "missing", f"Statements do not cover the last {WINDOW_DAYS} days ({start.isoformat()} to {end.isoformat()}): {detail}.", src,
                  "Compare the imported statements with the bank or wallet's own statement list.",
                  " ".join(f"Import {a['label']} statements for {_span(gaps[a['id']])} and declare the coverage in Records." for a in short))
    unreconciled = [a for a in accs if set(a["issue_codes"]) & {"residual", "coverage_gap", "unmatched_transfer"}]
    if unreconciled:
        return _c(code, label, requirement, "uncertain", "Statements cover the window, but " + ", ".join(a["label"] for a in unreconciled) + " do not fully reconcile with the recorded balances.",
                  src, "Open Cash in Records and resolve the reconciliation issues listed for each account.",
                  "Resolve the reconciliation issues (residuals, coverage gaps, or unmatched transfers) so the statements match the balances.")
    return _c(code, label, requirement, "present", f"Every {noun} account has statements covering {start.isoformat()} to {end.isoformat()} and they reconcile.", src,
              "A lender will usually ask for the original statements; keep the files you imported.")


def _revenue(inputs: dict, p: dict) -> dict:
    start, end = _window(inputs)
    cur = inputs["run"]["request"]["currency"]
    rev = inputs["records"]["revenue"]
    need = p["min_monthly_revenue_minor"]
    monthly = rev["classified_receipts_minor"] // 3
    potential = (rev["classified_receipts_minor"] + rev["unclassified_inflow_minor"]) // 3
    req = f"Average monthly operating receipts of at least {fmt(need, cur)}."
    active = [a for a in inputs["records"]["accounts"] if a["currency"] == cur and a["has_movements_in_window"]]
    gaps = [(a, _account_gaps(a, start, end)) for a in active]
    gaps = [(a, g) for a, g in gaps if g]
    unused = [a["label"] for a in inputs["records"]["accounts"] if a["currency"] == cur and not a["has_movements_in_window"]]
    src = (f"{rev['classified_count']} classified operating receipt(s) totalling {fmt(rev['classified_receipts_minor'], cur)} from "
           f"{(_d(inputs['as_of']) - timedelta(days=WINDOW_DAYS - 1)).isoformat()} to {inputs['as_of']}, divided by 3.")
    if unused:
        src += f" No records in this window for {', '.join(unused)} (treated as unused)."
    problems = []
    if gaps:
        problems.append("statement coverage is missing for " + "; ".join(f"{a['label']} ({_span(g)})" for a, g in gaps))
    if rev["unclassified_inflow_count"]:
        problems.append(f"{rev['unclassified_inflow_count']} inflow(s) totalling {fmt(rev['unclassified_inflow_minor'], cur)} are not classified")
    steps = []
    if gaps:
        steps.append(" ".join(f"Import {a['label']} statements for {_span(g)} and declare the coverage." for a, g in gaps))
    if rev["unclassified_inflow_count"]:
        steps.append(f"Classify the {rev['unclassified_inflow_count']} unclassified inflow(s) in Financial Health (only real sales count as operating receipts).")
    verification = "Lenders recompute revenue from bank statements; the figure here uses only your classified records."
    if rev["classified_count"] == 0 and rev["unclassified_inflow_count"] == 0 and not gaps:
        return _c("revenue", "Average monthly revenue", req, "missing", f"No operating receipts are recorded in the last {WINDOW_DAYS} days.", src, verification,
                  f"Import statements covering {start.isoformat()} to {end.isoformat()} and classify the sales receipts.")
    if monthly >= need and not problems:
        return _c("revenue", "Average monthly revenue", req, "present", f"Records show {fmt(monthly, cur)} a month, meeting the minimum, and the records are complete for the window.", src, verification)
    if monthly >= need:
        return _c("revenue", "Average monthly revenue", req, "uncertain",
                  f"Records show {fmt(monthly, cur)} a month, which meets the minimum, but {' and '.join(problems)}, so the figure is not confirmed.", src, verification, " ".join(steps))
    if problems and (gaps or potential >= need):
        return _c("revenue", "Average monthly revenue", req, "uncertain",
                  f"Records show {fmt(monthly, cur)} a month, below the minimum, but {' and '.join(problems)}, so the true figure is not known.", src, verification, " ".join(steps))
    short = need - monthly
    return _c("revenue", "Average monthly revenue", req, "missing",
              f"Records show {fmt(monthly, cur)} a month, {fmt(short, cur)} a month below the minimum.", src, verification,
              (" ".join(steps) + " " if steps else "") + f"Average sales would need to rise by {fmt(short, cur)} a month over 90 days, or choose a product with a lower revenue minimum.")


def _age(inputs: dict, p: dict, declared: int) -> dict:
    as_of, needm = _d(inputs["as_of"]), p["min_months_trading"]
    req = f"At least {needm} months trading."
    earliest = _d(inputs["records"]["earliest_record_date"])
    rec_m = months_between(earliest, as_of) if earliest else 0
    lic = [d for d in _live_docs(inputs, "trade_licence") if d["issued_on"]]
    lic_d = min((_d(d["issued_on"]) for d in lic), default=None)
    lic_m = months_between(lic_d, as_of) if lic_d else 0
    parts = [f"earliest posted transaction {earliest.isoformat()} ({rec_m} months)" if earliest else "no posted transactions",
             f"trade licence issued {lic_d.isoformat()} ({lic_m} months, document register)" if lic_d else "no trade licence issue date recorded",
             f"{declared} months declared in the funding need"]
    src = "Records: " + "; ".join(parts) + "."
    verification = "A lender checks the trade licence or registration date."
    if max(rec_m, lic_m) >= needm:
        how = "records" if rec_m >= needm else "the trade licence issue date"
        return _c("business_age", "Business age", req, "present", f"{max(rec_m, lic_m)} months shown by {how}.", src, verification)
    if declared >= needm:
        return _c("business_age", "Business age", req, "uncertain", f"{declared} months declared, but records show only {max(rec_m, lic_m)} months.", src, verification,
                  "Record the trade licence with its issue date, or import older statements, so the business age can be shown.")
    return _c("business_age", "Business age", req, "missing", f"{max(rec_m, lic_m, declared)} months, below the {needm}-month minimum.", src, verification,
              f"This product needs {needm} months of trading; wait until then or choose a product with a lower minimum.")


def _security(inputs: dict, p: dict) -> dict:
    run = inputs["run"]["request"]
    cur, amount, sec = run["currency"], run["amount_minor"], p["security"]
    if sec == "none":
        return _c("security", "Security", "No security required.", "present", "No security required.", "Product terms in the illustrative catalogue.", "None needed.")
    if sec == "receivables":
        rec = inputs["records"]["receivables"]
        cap = rec["eligible_minor"] * p["advance_rate_bps"] // 10000
        req = f"Outstanding, undisputed invoices; advances up to {p['advance_rate_bps'] // 100}% of them."
        src = f"Receivables: {rec['count']} outstanding, undisputed invoice(s) totalling {fmt(rec['eligible_minor'], cur)}" + \
              (f" ({', '.join(rec['document_numbers'])})." if rec["document_numbers"] else ".")
        if amount <= cap:
            return _c("security", "Invoice security", req, "present", f"{fmt(cap, cur)} can be advanced against your invoices; {fmt(amount, cur)} is requested.", src,
                      "The lender will confirm each invoice with the customer.")
        return _c("security", "Invoice security", req, "missing", f"Only {fmt(cap, cur)} can be advanced against your invoices; {fmt(amount, cur)} is requested.", src,
                  "The lender will confirm each invoice with the customer.",
                  f"Reduce the amount to {fmt(cap, cur)} or less, or record further undisputed customer invoices.")
    kind = SECURITY_DOC[sec]
    return _register_doc(inputs, kind, "security", DOC_LABEL[kind], f"A {sec}, shown by a {DOC_LABEL[kind].lower()}.", sec in run["security_available"])


def assess(inputs: dict) -> dict:
    if set(inputs) != set(INPUT_FIELDS):
        raise ReadinessError("Unexpected readiness inputs.")
    p, run = inputs["product"], inputs["run"]["request"]
    as_of, needed = _d(inputs["as_of"]), _d(run["needed_by"])
    cur, amount = run["currency"], run["amount_minor"]
    out = []
    jur = inputs["jurisdiction"]
    out.append(_c("jurisdiction", "Where the business operates", f"Offered in {', '.join(p['jurisdictions'])}.",
                  "present" if jur in p["jurisdictions"] else "missing", f"The business is set up in {jur}.", "Business setup: jurisdiction.",
                  "None needed.", "This product is not offered where the business operates; choose another product."))
    out.append(_c("currency", "Currency", f"Lends in {', '.join(p['currencies'])}.", "present" if cur in p["currencies"] else "missing",
                  f"The funding need is in {cur}.", "Saved funding need.", "None needed.", f"This product does not lend in {cur}; choose another product."))
    lo, hi = p["min_amount_minor"], p["max_amount_minor"]
    out.append(_c("amount", "Amount", f"Between {fmt(lo, cur)} and {fmt(hi, cur)}." if cur in p["currencies"] else f"Between {lo} and {hi} minor units of {', '.join(p['currencies'])}.",
                  "present" if cur in p["currencies"] and lo <= amount <= hi else "missing", f"{fmt(amount, cur)} is requested.", "Saved funding need.", "None needed.",
                  f"Change the amount to between {fmt(lo, cur)} and {fmt(hi, cur)}, or choose another product." if cur in p["currencies"] else "Choose a product in your currency."))
    out.append(_c("purpose", "Purpose", "Supports " + ", ".join(PURPOSE_LABEL.get(x, x) for x in p["purposes"]) + ".",
                  "present" if run["purpose"] in p["purposes"] else "missing", f"The purpose is {PURPOSE_LABEL.get(run['purpose'], run['purpose'])}.", "Saved funding need.",
                  "The lender will ask how the money will be used.", "This product does not fund this purpose; choose a product that does."))
    days = (needed - as_of).days
    if days <= 0:
        out.append(_c("funding_time", "Time to funds", f"Typically {p['funding_days']} day(s) from application.", "missing",
                      f"The date the funds were needed ({needed.isoformat()}) has passed.", "Saved funding need.", "None needed.",
                      "Run Funding Matches again with a new date."))
    else:
        apply_by = needed - timedelta(days=p["funding_days"])
        ok = p["funding_days"] <= days
        out.append(_c("funding_time", "Time to funds", f"Typically {p['funding_days']} day(s) from application.", "present" if ok else "missing",
                      (f"Funds are needed in {days} day(s); apply by {apply_by.isoformat()}." if ok else f"Funds are needed in {days} day(s), sooner than the typical {p['funding_days']}."),
                      "Typical time from the illustrative catalogue; real lenders vary.", "Ask the lender for its current processing time.",
                      f"Plan the need at least {p['funding_days']} days ahead, or choose a faster product."))
    out.append(_age(inputs, p, run["months_trading"]))
    out.append(_revenue(inputs, p))
    out.append(_security(inputs, p))
    for doc in p["documents"]:
        label = DOC_LABEL.get(doc, doc)
        req = f"{label} required by the product."
        if doc == "bank_statements":
            out.append(_statements(inputs, "bank", "doc:bank_statements", label, f"{label} for the last {WINDOW_DAYS} days."))
        elif doc == "wallet_history":
            out.append(_statements(inputs, "mobile_wallet", "doc:wallet_history", label, f"{label} for the last {WINDOW_DAYS} days."))
        elif doc == "invoices":
            rec = inputs["records"]["receivables"]
            out.append(_c("doc:invoices", label, req, "present" if rec["count"] else "missing",
                          f"{rec['count']} outstanding, undisputed customer invoice(s) recorded." if rec["count"] else "No outstanding, undisputed customer invoices are recorded.",
                          "Receivables" + (f": {', '.join(rec['document_numbers'])}." if rec["document_numbers"] else ": none."),
                          "Keep copies of the invoices; the lender may confirm them with the customer.", "Record your outstanding customer invoices in Receivables."))
        else:
            out.append(_register_doc(inputs, doc, f"doc:{doc}", label, req, doc in run["documents_available"]))
    counts = {s: sum(1 for c in out if c["status"] == s) for s in ("present", "missing", "uncertain")}
    state = "not_ready" if counts["missing"] else "needs_verification" if counts["uncertain"] else "ready"
    headline = {"ready": "Every published condition is shown as met by your records. This is not an approval.",
                "not_ready": f"Not ready: {counts['missing']} condition(s) missing" + (f" and {counts['uncertain']} uncertain." if counts["uncertain"] else "."),
                "needs_verification": f"Not ready yet: {counts['uncertain']} condition(s) cannot be confirmed from your records."}[state]
    order = {"missing": 0, "uncertain": 1}
    improvements = [{"code": c["code"], "label": c["label"], "status": c["status"], "step": c["improvement"]}
                    for c in sorted((c for c in out if c["status"] != "present"), key=lambda c: order[c["status"]])]
    return {"calc_version": CALC_VERSION, "as_of": inputs["as_of"], "run_id": inputs["run"]["id"], "catalogue_version": inputs["run"]["catalogue_version"],
            "product": {"id": p["id"], "name": p["name"], "provider": p["provider"], "source": p["source"]},
            "need": {k: run[k] for k in ("amount_minor", "currency", "needed_by", "purpose")},
            "state": state, "counts": counts, "headline": headline, "conditions": out, "improvements": improvements,
            "window": {"start": _window(inputs)[0].isoformat(), "end": _window(inputs)[1].isoformat()},
            "notice": NOTICE, "application_submitted": False, "data_shared": False}
