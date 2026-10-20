"""Freeze alert inputs, run the rules, and reconcile the result with stored alerts (dedup on retry)."""

from datetime import date, timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.modules.alerts.engine import OUTFLOW_BASELINE_DAYS, OUTFLOW_RECENT_DAYS, RULES_VERSION, SEVERITY_ORDER, evaluate
from app.modules.obligations.service import list_obligations
from app.modules.outlook.service import canonical, freeze_inputs, sha256

NOTIFICATION_TEXT = "SME Finance Copilot: {n} new alert(s) need your attention. Sign in to view details."


def build_inputs(db: Session, business_id: UUID, as_of: date) -> dict:
    """Everything the rules need as plain JSON: the frozen outlook inputs plus obligations and recent outflows."""
    obligations = [o for o in list_obligations(db, business_id, as_of) if o["status"] in ("open", "partially_settled")]
    rows = db.execute(text(
        """SELECT t.id, t.account_id, t.business_date, -t.amount_minor AS amount_minor, t.currency, t.description
           FROM ledger_transaction t
           WHERE t.business_id = :b AND t.amount_minor < 0 AND t.business_date BETWEEN :lo AND :hi
             AND NOT EXISTS (SELECT 1 FROM own_transfer x WHERE x.voided_at IS NULL AND (x.out_txn_id = t.id OR x.in_txn_id = t.id))
             AND NOT EXISTS (SELECT 1 FROM allocation a WHERE a.ledger_txn_id = t.id)
           ORDER BY t.business_date, t.id"""),
        {"b": business_id, "hi": as_of, "lo": as_of - timedelta(days=OUTFLOW_RECENT_DAYS - 1 + OUTFLOW_BASELINE_DAYS)}).mappings().all()
    outflows = [{"id": str(r["id"]), "account_id": str(r["account_id"]), "business_date": r["business_date"].isoformat(),
                 "amount_minor": r["amount_minor"], "currency": r["currency"], "description": r["description"]} for r in rows]
    return {"rules_version": RULES_VERSION, "as_of": as_of.isoformat(), "outlook": freeze_inputs(db, business_id, as_of),
            "obligations": obligations, "outflows": outflows}


_FIELDS = ("severity", "alert_date", "amount_minor", "currency", "cause", "record_ids", "details")


def _row_dict(r) -> dict:
    return {"id": str(r["id"]), "type": r["alert_type"], "severity": r["severity"], "status": r["status"],
            "date": r["alert_date"].isoformat(), "amount_minor": r["amount_minor"], "currency": r["currency"],
            "cause": r["cause"], "record_ids": r["record_ids"], "details": r["details"],
            "detection_count": r["detection_count"], "first_detected_at": r["first_detected_at"].isoformat(),
            "last_detected_at": r["last_detected_at"].isoformat(),
            "resolved_at": r["resolved_at"].isoformat() if r["resolved_at"] else None}


def run_alerts(db: Session, business_id: UUID, user_id: UUID, as_of: date) -> dict:
    # One run per business at a time; the partial unique index is the backstop.
    db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"alerts:{business_id}"})
    inputs = build_inputs(db, business_id, as_of)
    candidates = {c["key"]: c for c in evaluate(inputs)}
    digest = sha256(inputs)
    existing = {r["dedup_key"]: r for r in db.execute(text(
        "SELECT * FROM alert WHERE business_id = :b AND status = 'open' FOR UPDATE"), {"b": business_id}).mappings().all()}
    def unchanged_since(old, c) -> bool:
        return (old["severity"], old["alert_date"].isoformat(), old["amount_minor"], old["currency"], old["cause"],
                canonical(old["record_ids"]), canonical(old["details"])) == (
            c["severity"], c["alert_date"], c["amount_minor"], c["currency"], c["cause"],
            canonical(c["record_ids"]), canonical(c["details"]))

    new_keys = [k for k in candidates if k not in existing]
    same_keys = [k for k in candidates if k in existing and unchanged_since(existing[k], candidates[k])]
    gone = [r["id"] for k, r in existing.items() if k not in candidates]
    counts = {"created": len(new_keys), "updated": len(candidates) - len(new_keys) - len(same_keys),
              "unchanged": len(same_keys), "resolved": len(gone)}
    note = NOTIFICATION_TEXT.format(n=len(new_keys)) if new_keys else None
    # alert_run is append-only, so the run is written once, with its final counts, before the alerts it touched.
    run_id = db.execute(text(
        """INSERT INTO alert_run (business_id, as_of, rules_version, inputs_sha256, created_count, updated_count, unchanged_count,
                                  resolved_count, notification_text, created_by)
           VALUES (:b, :d, :v, :h, :cr, :up, :un, :re, :n, :u) RETURNING id"""),
        {"b": business_id, "d": as_of, "v": RULES_VERSION, "h": digest, "cr": counts["created"], "up": counts["updated"],
         "un": counts["unchanged"], "re": counts["resolved"], "n": note, "u": user_id}).scalar_one()
    for key, c in candidates.items():
        params = {"b": business_id, "k": key, "t": c["type"], "sev": c["severity"], "d": c["alert_date"], "a": c["amount_minor"],
                  "c": c["currency"], "cause": c["cause"], "rec": canonical(c["record_ids"]), "det": canonical(c["details"]), "run": run_id}
        old = existing.get(key)
        if old is None:
            db.execute(text(
                """INSERT INTO alert (business_id, alert_type, severity, dedup_key, alert_date, amount_minor, currency, cause,
                                      record_ids, details, first_run_id, last_run_id)
                   VALUES (:b, :t, :sev, :k, :d, :a, :c, :cause, CAST(:rec AS jsonb), CAST(:det AS jsonb), :run, :run)"""), params)
        else:
            db.execute(text(
                """UPDATE alert SET severity = :sev, alert_date = :d, amount_minor = :a, currency = :c, cause = :cause,
                                    record_ids = CAST(:rec AS jsonb), details = CAST(:det AS jsonb), last_run_id = :run,
                                    detection_count = detection_count + 1, last_detected_at = now()
                   WHERE id = :id"""), params | {"id": old["id"]})
    for alert_id in gone:
        db.execute(text("UPDATE alert SET status = 'resolved', resolved_at = now(), last_run_id = :run WHERE id = :id"),
                   {"id": alert_id, "run": run_id})
    return {"run_id": str(run_id), **counts, "notification_text": note, "inputs_sha256": digest}


def list_alerts(db: Session, business_id: UUID, status: str = "open", limit: int = 100) -> list[dict]:
    where = "" if status == "all" else "AND status = :s"
    rows = db.execute(text(f"SELECT * FROM alert WHERE business_id = :b {where} ORDER BY alert_date, id LIMIT 500"),
                      {"b": business_id, "s": status}).mappings().all()
    items = [_row_dict(r) for r in rows]
    return sorted(items, key=lambda a: (a["status"] != "open", SEVERITY_ORDER[a["severity"]], a["date"], a["id"]))[:limit]


def get_alert(db: Session, business_id: UUID, alert_id: UUID) -> dict | None:
    r = db.execute(text("SELECT * FROM alert WHERE business_id = :b AND id = :i"), {"b": business_id, "i": alert_id}).mappings().first()
    return _row_dict(r) if r else None


def last_run(db: Session, business_id: UUID) -> dict | None:
    r = db.execute(text(
        """SELECT r.*, u.display_name FROM alert_run r JOIN app_user u ON u.id = r.created_by
           WHERE r.business_id = :b ORDER BY r.created_at DESC LIMIT 1"""), {"b": business_id}).mappings().first()
    if r is None:
        return None
    return {"id": str(r["id"]), "as_of": r["as_of"].isoformat(), "rules_version": r["rules_version"],
            "inputs_sha256": r["inputs_sha256"], "created_at": r["created_at"].isoformat(), "created_by": r["display_name"],
            "created": r["created_count"], "updated": r["updated_count"], "unchanged": r["unchanged_count"],
            "resolved": r["resolved_count"], "notification_text": r["notification_text"]}
