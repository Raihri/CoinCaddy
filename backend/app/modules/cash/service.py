"""Trusted cash reconstruction: what each account's balance is, how far it is verified, and what is usable.

Definitions (calculation version cash-recon-v1):
- An anchor is a statement or count balance at the END of its business date.
- A coverage period declares that every movement on those dates has been posted.
- Reconciliation of two consecutive anchors: residual = opening + posted movements in (d0, d1] - closing.
  Tolerance is exact (zero). A zero residual is reported as complete ONLY if coverage spans every date in
  (d0, d1]; otherwise it is "balanced but incomplete".
- evidence_through: the last date reachable from the latest anchor through contiguous coverage. The
  verified balance is the latest anchor plus posted movements up to that date. Posted movements after it
  are shown separately as uncovered and are NOT part of the verified balance.
- Fresh if evidence_through is at most FRESH_MAX_AGE_DAYS before the as-of date; otherwise stale.
- Usable = verified balance - active restricted funds. Spendable totals are per currency and include
  only fresh accounts with a known balance; stale and anchor-less accounts are listed, never summed.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from itertools import pairwise
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

CALC_VERSION = "cash-recon-v1"
FRESH_MAX_AGE_DAYS = 2
TRANSFER_MATCH_DAYS = 3
TRANSFER_LINK_MAX_DAYS = 5


@dataclass(frozen=True)
class Txn:
    id: str
    account_id: str
    business_date: date
    amount_minor: int
    currency: str
    description: str = ""
    reference: str | None = None


@dataclass
class AccountInput:
    id: str
    label: str
    kind: str
    currency: str
    anchors: list[dict] = field(default_factory=list)      # {id, anchor_date, balance_minor, source_kind, source_reference}
    coverage: list[dict] = field(default_factory=list)     # {id, period_start, period_end, source_kind}
    restrictions: list[dict] = field(default_factory=list)  # {id, amount_minor, reason, effective_from, effective_to}
    txns: list[Txn] = field(default_factory=list)


# --- interval helpers ---------------------------------------------------------------------------------


def merge_intervals(intervals: list[tuple[date, date]]) -> list[tuple[date, date]]:
    """Merge overlapping or touching (adjacent-day) closed date intervals."""
    out: list[list[date]] = []
    for s, e in sorted(intervals):
        if out and s <= out[-1][1] + timedelta(days=1):
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def find_gaps(merged: list[tuple[date, date]], start: date, end: date) -> list[tuple[date, date]]:
    """Closed sub-intervals of [start, end] not covered by the merged intervals."""
    if end < start:
        return []
    gaps, cursor = [], start
    for s, e in merged:
        if e < cursor:
            continue
        if s > end:
            break
        if s > cursor:
            gaps.append((cursor, min(end, s - timedelta(days=1))))
        cursor = max(cursor, e + timedelta(days=1))
        if cursor > end:
            break
    if cursor <= end:
        gaps.append((cursor, end))
    return gaps


def contiguous_end(merged: list[tuple[date, date]], start: date) -> date | None:
    """Last date of the coverage run that contains `start`, or None if `start` is not covered."""
    for s, e in merged:
        if s <= start <= e:
            return e
    return None


# --- reconstruction (pure) ----------------------------------------------------------------------------


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def reconstruct_account(acc: AccountInput, as_of: date, unmatched_ids: set[str]) -> dict:
    anchors = sorted((a for a in acc.anchors if a["anchor_date"] <= as_of), key=lambda a: a["anchor_date"])
    merged = merge_intervals([(c["period_start"], min(c["period_end"], as_of)) for c in acc.coverage
                              if c["period_start"] <= as_of])
    txns = sorted((t for t in acc.txns if t.business_date <= as_of), key=lambda t: (t.business_date, t.id))

    periods = []
    for a0, a1 in pairwise(anchors):
        moves = [t for t in txns if a0["anchor_date"] < t.business_date <= a1["anchor_date"]]
        moved = sum(t.amount_minor for t in moves)
        residual = a0["balance_minor"] + moved - a1["balance_minor"]
        gaps = find_gaps(merged, a0["anchor_date"] + timedelta(days=1), a1["anchor_date"])
        status = "residual" if residual else ("balanced_incomplete" if gaps else "balanced")
        periods.append({
            "from_anchor_id": a0["id"], "to_anchor_id": a1["id"],
            "from_date": _iso(a0["anchor_date"]), "to_date": _iso(a1["anchor_date"]),
            "opening_minor": a0["balance_minor"], "movements_minor": moved, "movement_count": len(moves),
            "expected_closing_minor": a0["balance_minor"] + moved, "closing_minor": a1["balance_minor"],
            "residual_minor": residual, "coverage_gaps": [{"start": _iso(s), "end": _iso(e)} for s, e in gaps],
            "status": status, "transaction_ids": [t.id for t in moves],
        })

    restricted = [r for r in acc.restrictions
                  if r["effective_from"] <= as_of and (r["effective_to"] is None or r["effective_to"] >= as_of)]
    restricted_total = sum(r["amount_minor"] for r in restricted)
    latest = anchors[-1] if anchors else None
    issues: list[dict] = []
    result = {
        "account": {"id": acc.id, "label": acc.label, "kind": acc.kind, "currency": acc.currency},
        "anchors": [{**a, "anchor_date": _iso(a["anchor_date"])} for a in anchors],
        "coverage": [{"start": _iso(s), "end": _iso(e)} for s, e in merged],
        "periods": periods,
        "restricted_minor": restricted_total,
        "restrictions": [{**r, "effective_from": _iso(r["effective_from"]), "effective_to": _iso(r["effective_to"])} for r in restricted],
        "latest_anchor": None, "evidence_through": None, "age_days": None,
        "verified_balance_minor": None, "usable_minor": None,
        "uncovered_movements": {"count": 0, "sum_minor": 0, "transaction_ids": []},
        "freshness": "no_anchor",
    }
    if latest is None:
        issues.append({"code": "no_anchor", "message": "No statement or count balance has been entered, so the current balance is unknown."})
    else:
        run_end = contiguous_end(merged, latest["anchor_date"] + timedelta(days=1))
        evidence = min(run_end, as_of) if run_end else latest["anchor_date"]
        verified = latest["balance_minor"] + sum(t.amount_minor for t in txns if latest["anchor_date"] < t.business_date <= evidence)
        uncovered = [t for t in txns if t.business_date > evidence]
        age = (as_of - evidence).days
        result.update({
            "latest_anchor": {**latest, "anchor_date": _iso(latest["anchor_date"])},
            "evidence_through": _iso(evidence), "age_days": age,
            "verified_balance_minor": verified, "usable_minor": verified - restricted_total,
            "uncovered_movements": {"count": len(uncovered), "sum_minor": sum(t.amount_minor for t in uncovered),
                                    "transaction_ids": [t.id for t in uncovered]},
            "freshness": "fresh" if age <= FRESH_MAX_AGE_DAYS else "stale",
        })
        if age > FRESH_MAX_AGE_DAYS:
            issues.append({"code": "stale", "message": f"Balance is verified only through {_iso(evidence)} ({age} days old)."})
        if uncovered:
            issues.append({"code": "uncovered_movements",
                           "message": f"{len(uncovered)} posted movement(s) after {_iso(evidence)} are not covered by a declared statement period."})
        if restricted_total > max(verified, 0):
            issues.append({"code": "restricted_exceeds_balance", "message": "Restricted funds exceed the verified balance."})
    for p in periods:
        if p["status"] == "residual":
            issues.append({"code": "residual", "message": f"{p['from_date']} to {p['to_date']} does not reconcile: residual {p['residual_minor']} minor units."})
        elif p["status"] == "balanced_incomplete":
            issues.append({"code": "coverage_gap",
                           "message": f"{p['from_date']} to {p['to_date']} has a zero residual, but some dates have no declared source coverage, so completeness is not proven."})
    unmatched_here = sorted(i for i in unmatched_ids if any(t.id == i for t in acc.txns))
    if unmatched_here:
        issues.append({"code": "unmatched_transfer", "message": f"{len(unmatched_here)} own-account transfer(s) on this account have no matching other side."})
    codes = {i["code"] for i in issues}
    result["issues"] = issues
    result["status"] = ("missing" if "no_anchor" in codes
                        else "needs_review" if codes & {"residual", "coverage_gap", "unmatched_transfer", "restricted_exceeds_balance"}
                        else "stale" if codes & {"stale", "uncovered_movements"}
                        else "verified")
    return result


def suggest_transfers(txns: list[Txn], linked: set[str]) -> list[dict]:
    """One-to-one pairs: an outflow and an inflow of the same amount and currency on two different
    accounts within TRANSFER_MATCH_DAYS days. Suggestions only; a person must confirm."""
    outs = sorted((t for t in txns if t.amount_minor < 0 and t.id not in linked), key=lambda t: (t.business_date, t.id))
    ins = [t for t in txns if t.amount_minor > 0 and t.id not in linked]
    used: set[str] = set()
    out = []
    for o in outs:
        cands = [i for i in ins if i.id not in used and i.account_id != o.account_id and i.currency == o.currency
                 and i.amount_minor == -o.amount_minor and abs((i.business_date - o.business_date).days) <= TRANSFER_MATCH_DAYS]
        if cands:
            best = min(cands, key=lambda i: (abs((i.business_date - o.business_date).days), i.business_date, i.id))
            used.add(best.id)
            out.append({"out_txn_id": o.id, "in_txn_id": best.id, "amount_minor": -o.amount_minor, "currency": o.currency})
    return out


def reconstruct(accounts: list[AccountInput], transfers: list[dict], as_of: date) -> dict:
    txn_index = {t.id: t for a in accounts for t in a.txns}
    unmatched = {(tr["out_txn_id"] or tr["in_txn_id"]) for tr in transfers if not (tr["out_txn_id"] and tr["in_txn_id"])}
    per_account = [reconstruct_account(a, as_of, unmatched) for a in accounts]

    totals: dict[str, dict] = defaultdict(lambda: {"spendable_minor": 0, "restricted_minor": 0, "included": [], "stale": [], "missing": []})
    for r in per_account:
        t = totals[r["account"]["currency"]]
        entry = {"account_id": r["account"]["id"], "label": r["account"]["label"], "usable_minor": r["usable_minor"],
                 "evidence_through": r["evidence_through"]}
        if r["freshness"] == "no_anchor":
            t["missing"].append(entry)
        elif r["freshness"] == "stale":
            t["stale"].append(entry)
        else:
            t["spendable_minor"] += r["usable_minor"]
            t["restricted_minor"] += r["restricted_minor"]
            t["included"].append(entry)

    linked = {i for tr in transfers for i in (tr["out_txn_id"], tr["in_txn_id"]) if i}
    links = []
    for tr in transfers:
        o, i = txn_index.get(tr["out_txn_id"] or ""), txn_index.get(tr["in_txn_id"] or "")
        side = o or i
        links.append({
            "id": tr["id"], "status": "matched" if (o and i) else "unmatched",
            "amount_minor": abs(side.amount_minor) if side else None, "currency": side.currency if side else None,
            "out": _txn_json(o), "in": _txn_json(i), "note": tr.get("note"),
            "missing_side": None if (o and i) else ("incoming" if o else "outgoing"),
        })
    return {
        "as_of": as_of.isoformat(), "calc_version": CALC_VERSION, "fresh_max_age_days": FRESH_MAX_AGE_DAYS,
        "accounts": per_account,
        "totals": [{"currency": c, **v} for c, v in sorted(totals.items())],
        "transfers": links,
        "transfer_suggestions": suggest_transfers(list(txn_index.values()), linked),
    }


def _txn_json(t: Txn | None) -> dict | None:
    if t is None:
        return None
    return {"id": t.id, "account_id": t.account_id, "business_date": t.business_date.isoformat(),
            "amount_minor": t.amount_minor, "currency": t.currency, "description": t.description, "reference": t.reference}


# --- loading ------------------------------------------------------------------------------------------


def load_inputs(db: Session, business_id: UUID) -> tuple[list[AccountInput], list[dict]]:
    b = {"b": business_id}
    accounts = {str(r["id"]): AccountInput(str(r["id"]), r["label"], r["kind"], r["currency"]) for r in db.execute(
        text("SELECT id, label, kind, currency FROM cash_account WHERE business_id = :b ORDER BY created_at, external_ref"), b).mappings()}
    for r in db.execute(text("""SELECT id, account_id, anchor_date, balance_minor, source_kind, source_reference
                                FROM balance_anchor WHERE business_id = :b AND voided_at IS NULL"""), b).mappings():
        accounts[str(r["account_id"])].anchors.append({**dict(r), "id": str(r["id"]), "account_id": str(r["account_id"])})
    for r in db.execute(text("""SELECT id, account_id, period_start, period_end, source_kind
                                FROM coverage_period WHERE business_id = :b AND voided_at IS NULL"""), b).mappings():
        accounts[str(r["account_id"])].coverage.append({**dict(r), "id": str(r["id"]), "account_id": str(r["account_id"])})
    for r in db.execute(text("""SELECT id, account_id, amount_minor, reason, effective_from, effective_to
                                FROM restricted_fund WHERE business_id = :b AND voided_at IS NULL"""), b).mappings():
        accounts[str(r["account_id"])].restrictions.append({**dict(r), "id": str(r["id"]), "account_id": str(r["account_id"])})
    for r in db.execute(text("""SELECT id, account_id, business_date, amount_minor, currency, description, reference
                                FROM ledger_transaction WHERE business_id = :b"""), b).mappings():
        accounts[str(r["account_id"])].txns.append(Txn(str(r["id"]), str(r["account_id"]), r["business_date"], r["amount_minor"],
                                                       r["currency"], r["description"], r["reference"]))
    transfers = [{"id": str(r["id"]), "out_txn_id": str(r["out_txn_id"]) if r["out_txn_id"] else None,
                  "in_txn_id": str(r["in_txn_id"]) if r["in_txn_id"] else None, "note": r["note"]}
                 for r in db.execute(text("SELECT id, out_txn_id, in_txn_id, note FROM own_transfer WHERE business_id = :b AND voided_at IS NULL ORDER BY created_at"), b).mappings()]
    return list(accounts.values()), transfers


def own_transfer_txn_ids(db: Session, business_id: UUID) -> set[str]:
    """Ledger transactions declared as own-account transfers (matched or not). Later features exclude these from operating totals."""
    rows = db.execute(text("SELECT out_txn_id, in_txn_id FROM own_transfer WHERE business_id = :b AND voided_at IS NULL"), {"b": business_id}).all()
    return {str(x) for r in rows for x in r if x}
