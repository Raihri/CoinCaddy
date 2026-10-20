"""Freeze Cash Outlook inputs from the database and store snapshots."""

import hashlib
import json
from datetime import date
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.modules.cash.service import load_inputs, reconstruct
from app.modules.obligations.service import list_obligations
from app.modules.outlook.engine import CALC_VERSION, compute


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256(obj) -> str:
    return hashlib.sha256(canonical(obj).encode()).hexdigest()


def freeze_inputs(db: Session, business_id: UUID, as_of: date) -> dict:
    """Everything the engine needs, as plain JSON. Nothing outside this object affects the result."""
    biz = db.execute(text("SELECT base_currency, reserve_amount_minor, reserve_currency FROM business WHERE id = :b"),
                     {"b": business_id}).one()
    accounts_in, transfers = load_inputs(db, business_id)
    pos = reconstruct(accounts_in, transfers, as_of)
    restrictions = {a.id: a.restrictions for a in accounts_in}
    accounts = []
    for a in pos["accounts"]:
        accounts.append({
            "id": a["account"]["id"], "label": a["account"]["label"], "currency": a["account"]["currency"],
            "usable_minor": a["usable_minor"], "freshness": a["freshness"], "evidence_through": a["evidence_through"],
            "uncovered_minor": a["uncovered_movements"]["sum_minor"], "uncovered_count": a["uncovered_movements"]["count"],
            "anchor_id": a["latest_anchor"]["id"] if a["latest_anchor"] else None,
            "restriction_releases": [{"id": r["id"], "amount_minor": r["amount_minor"], "reason": r["reason"],
                                      "effective_to": r["effective_to"].isoformat()}
                                     for r in restrictions[a["account"]["id"]]
                                     if r["effective_to"] is not None and r["effective_from"] <= as_of <= r["effective_to"]],
        })
    obligations = [{
        "id": o["id"], "direction": o["direction"], "party": o["party"]["name"], "document_number": o["document_number"],
        "due_date": o["due_date"], "outstanding_minor": o["outstanding_minor"], "currency": o["currency"],
        "disputed": o["disputed"], "settlement_account_id": o["settlement_account"]["id"] if o["settlement_account"] else None,
    } for o in list_obligations(db, business_id, as_of) if o["status"] in ("open", "partially_settled")]
    return {
        "calc_version": CALC_VERSION, "as_of": as_of.isoformat(), "base_currency": biz.base_currency,
        "reserve": {"amount_minor": biz.reserve_amount_minor, "currency": biz.reserve_currency},
        "accounts": accounts, "obligations": obligations,
        "unmatched_transfers": [{"id": t["id"]} for t in pos["transfers"] if t["status"] == "unmatched"],
    }


def create_snapshot(db: Session, business_id: UUID, user_id: UUID, as_of: date) -> UUID:
    inputs = freeze_inputs(db, business_id, as_of)
    results = compute(inputs)
    return db.execute(text(
        """INSERT INTO forecast_snapshot (business_id, calc_version, as_of, inputs, inputs_sha256, results, created_by)
           VALUES (:b, :v, :d, CAST(:i AS jsonb), :h, CAST(:r AS jsonb), :u) RETURNING id"""),
        {"b": business_id, "v": CALC_VERSION, "d": as_of, "i": canonical(inputs), "h": sha256(inputs),
         "r": canonical(results), "u": user_id}).scalar_one()


def load_snapshot(db: Session, business_id: UUID, snapshot_id: UUID | None = None) -> dict | None:
    where = "AND s.id = :s" if snapshot_id else ""
    row = db.execute(text(
        f"""SELECT s.*, u.display_name AS created_by_name FROM forecast_snapshot s JOIN app_user u ON u.id = s.created_by
            WHERE s.business_id = :b {where} ORDER BY s.created_at DESC LIMIT 1"""),
        {"b": business_id, "s": snapshot_id}).mappings().first()
    if row is None:
        return None
    return {"id": str(row["id"]), "calc_version": row["calc_version"], "as_of": row["as_of"].isoformat(),
            "inputs": row["inputs"], "inputs_sha256": row["inputs_sha256"], "results": row["results"],
            "created_by": row["created_by_name"], "created_at": row["created_at"].isoformat()}
