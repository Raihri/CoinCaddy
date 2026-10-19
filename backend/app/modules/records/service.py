"""Staging, mapping, duplicate detection, review, and posting for record imports.

Staged rows (import_row) are never official cash. Only commit_batch() writes ledger_transaction,
in one database transaction that locks the batch and its account, so concurrent or retried commits
cannot post the same rows twice.
"""

import json
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.modules.records.files import PARSER_VERSION
from app.modules.records.normalize import (
    DATE_FORMATS,
    Mapping,
    RowResult,
    date_format_candidates,
    fingerprint,
    match_key,
    normalize_row,
)

HEADER_HINTS: dict[str, tuple[str, ...]] = {
    "date": ("transaction date", "txn date", "value date", "posting date", "date"),
    "description": ("description", "details", "narration", "particulars", "memo", "remarks"),
    "amount": ("amount", "net amount", "value"),
    "debit": ("debit", "withdrawal", "withdrawals", "paid out", "dr"),
    "credit": ("credit", "deposit", "deposits", "paid in", "cr"),
    "counterparty": ("counterparty", "payee", "party", "beneficiary", "customer", "supplier"),
    "reference": ("reference", "ref", "ref no", "transaction id", "txn id", "cheque no"),
    "currency": ("currency", "ccy"),
}


class Conflict(Exception):
    def __init__(self, message: str, **extra):
        super().__init__(message)
        self.message, self.extra = message, extra


def business_today(timezone: str):
    return datetime.now(ZoneInfo(timezone)).date()


# --- profile and suggestion --------------------------------------------------------------------------


def column_profile(headers: list[str], rows: list[list]) -> list[dict]:
    profile = []
    for idx in range(len(headers)):
        cells = [r[idx] for r in rows if idx < len(r) and r[idx] is not None]
        kinds = {c["t"] for c in cells}
        sample = next((str(c["v"])[:40] for c in cells), None)
        has_text_dates = any(c["t"] == "text" and any(f.match(c["v"].strip()) for f in DATE_FORMATS.values()) for c in cells[:200])
        profile.append({
            "kinds": sorted(kinds),
            "sample": sample,
            "date_formats": date_format_candidates(rows, idx) if ("date" in kinds or has_text_dates) else [],
        })
    return profile


def suggest_mapping(headers: list[str], profile: list[dict]) -> dict:
    lowered = [h.casefold() for h in headers]
    found: dict[str, int] = {}
    for field_name, hints in HEADER_HINTS.items():
        for hint in hints:
            idx = next((i for i, h in enumerate(lowered) if (h == hint or h.startswith(hint + " ")) and i not in found.values()), None)
            if idx is not None:
                found[field_name] = idx
                break
    suggestion: dict = {
        "date_column": found.get("date"),
        "description_column": found.get("description"),
        "counterparty_column": found.get("counterparty"),
        "reference_column": found.get("reference"),
        "currency_column": found.get("currency"),
    }
    if "debit" in found and "credit" in found and "amount" not in found:
        suggestion.update(amount_mode="debit_credit", debit_column=found["debit"], credit_column=found["credit"], amount_column=None)
    else:
        suggestion.update(amount_mode="signed", amount_column=found.get("amount"), debit_column=None, credit_column=None)
    date_col = suggestion["date_column"]
    formats = profile[date_col]["date_formats"] if date_col is not None else []
    # Only pre-select a date format when exactly one fits; otherwise the user must choose.
    suggestion["date_format"] = formats[0] if len(formats) == 1 else None
    suggestion["date_format_ambiguous"] = len(formats) > 1
    return suggestion


# --- mapping and duplicates --------------------------------------------------------------------------


def _ledger_matches(db: Session, business_id: UUID, account_id: UUID, results: list[tuple[int, RowResult]]) -> dict[int, UUID]:
    """Row number -> ledger transaction it may duplicate: same account, date, and amount, and the same
    reference (when both have one) or the same normalized description."""
    valid = [(rn, r) for rn, r in results if r.ok]
    if not valid:
        return {}
    dates = sorted({r.business_date for _, r in valid})
    ledger = db.execute(
        text(
            """
            SELECT id, business_date, amount_minor, description, reference FROM ledger_transaction
            WHERE business_id = :b AND account_id = :a AND business_date = ANY(:dates)
            ORDER BY posted_at, source_row_number
            """
        ),
        {"b": business_id, "a": account_id, "dates": dates},
    ).mappings().all()
    by_key: dict[tuple, list] = {}
    for t in ledger:
        by_key.setdefault((t["business_date"], t["amount_minor"]), []).append(t)
    out = {}
    for rn, r in valid:
        for t in by_key.get((r.business_date, r.amount_minor), []):
            same_ref = bool(r.reference and t["reference"]) and r.reference.casefold() == t["reference"].casefold()
            if same_ref or match_key(r.description) == match_key(t["description"]):
                out[rn] = t["id"]
                break
    return out


def apply_mapping(db: Session, *, business_id: UUID, batch_id: UUID, account_id: UUID, currency: str,
                  timezone: str, mapping: Mapping) -> None:
    today = business_today(timezone)
    raw_rows = db.execute(
        text("SELECT row_number, raw FROM import_row WHERE batch_id = :id ORDER BY row_number"), {"id": batch_id}
    ).all()
    results = [(rn, normalize_row(raw, mapping, currency, today)) for rn, raw in raw_rows]
    in_ledger = _ledger_matches(db, business_id, account_id, results)
    first_seen: dict[str, int] = {}
    updates = []
    for rn, r in results:
        fp = fingerprint(str(account_id), r) if r.ok else None
        dup_kind = dup_row = dup_txn = None
        if r.ok:
            if fp in first_seen:
                dup_kind, dup_row = "in_file", first_seen[fp]
            else:
                first_seen[fp] = rn
            if rn in in_ledger:
                dup_kind, dup_txn, dup_row = "in_ledger", in_ledger[rn], None
        decision = "exclude" if not r.ok else ("pending" if dup_kind else "include")
        updates.append({
            "id": batch_id, "rn": rn, "status": "valid" if r.ok else "error", "errors": json.dumps(r.errors),
            "d": r.business_date, "amt": r.amount_minor, "ccy": currency if r.ok else None, "desc": r.description,
            "cp": r.counterparty, "ref": r.reference, "fp": fp, "dk": dup_kind, "dr": dup_row, "dt": dup_txn,
            "decision": decision,
        })
    db.execute(
        text(
            """
            UPDATE import_row SET status = :status, errors = CAST(:errors AS jsonb), business_date = :d,
                   amount_minor = :amt, currency = :ccy, description = :desc, counterparty = :cp, reference = :ref,
                   fingerprint = :fp, duplicate_kind = :dk, duplicate_of_row = :dr, duplicate_of_txn = :dt,
                   decision = :decision, decided_by = NULL
            WHERE batch_id = :id AND row_number = :rn
            """
        ),
        updates,
    )
    db.execute(
        text(
            """
            UPDATE import_batch SET mapping = CAST(:m AS jsonb), status = 'staged', revision = revision + 1,
                   updated_at = now() WHERE id = :id
            """
        ),
        {"m": mapping.model_dump_json(), "id": batch_id},
    )


def counts(db: Session, batch_id: UUID) -> dict:
    c = db.execute(
        text(
            """
            SELECT count(*) AS total,
                   count(*) FILTER (WHERE status = 'valid') AS valid,
                   count(*) FILTER (WHERE status = 'error') AS error,
                   count(*) FILTER (WHERE duplicate_kind IS NOT NULL) AS duplicates,
                   count(*) FILTER (WHERE decision = 'pending') AS pending,
                   count(*) FILTER (WHERE decision = 'include') AS included,
                   count(*) FILTER (WHERE status = 'valid' AND decision = 'exclude') AS excluded
            FROM import_row WHERE batch_id = :id
            """
        ),
        {"id": batch_id},
    ).mappings().one()
    return dict(c)


# --- commit ------------------------------------------------------------------------------------------


def commit_batch(db: Session, *, business_id: UUID, batch_id: UUID, user_id: UUID,
                 expected_revision: int, commit_key: UUID) -> dict:
    batch = db.execute(
        text("SELECT * FROM import_batch WHERE business_id = :b AND id = :id FOR UPDATE"),
        {"b": business_id, "id": batch_id},
    ).mappings().first()
    if batch is None:
        raise LookupError
    if batch["status"] == "posted":
        if batch["commit_key"] == commit_key:  # a retry of the same commit: same answer, nothing new posted
            return {"posted_count": batch["posted_count"], "replayed": True}
        raise Conflict("This batch has already been posted.")
    if batch["status"] == "discarded":
        raise Conflict("This batch was discarded.")
    if batch["status"] != "staged":
        raise Conflict("Map the columns before posting.")
    if batch["revision"] != expected_revision:
        raise Conflict("The preview changed since you loaded it. Reload and review again.", revision=batch["revision"])

    # Serialize commits per account so duplicate checks and inserts cannot interleave.
    account = db.execute(
        text("SELECT id FROM cash_account WHERE business_id = :b AND id = :a FOR UPDATE"),
        {"b": business_id, "a": batch["account_id"]},
    ).first()
    if account is None:
        raise LookupError
    pending = db.execute(
        text("SELECT row_number FROM import_row WHERE batch_id = :id AND decision = 'pending' ORDER BY row_number"),
        {"id": batch_id},
    ).scalars().all()
    if pending:
        raise Conflict("Review every possible duplicate before posting.", pending_rows=pending)

    selected = db.execute(
        text(
            """
            SELECT row_number, business_date, amount_minor, currency, description, counterparty, reference,
                   fingerprint, duplicate_kind
            FROM import_row WHERE batch_id = :id AND status = 'valid' AND decision = 'include'
            ORDER BY row_number
            """
        ),
        {"id": batch_id},
    ).mappings().all()
    if not selected:
        raise ValueError("No rows are selected for posting.")

    # Re-check against the ledger now: another batch may have been posted since the preview.
    fresh = [(r["row_number"], RowResult(business_date=r["business_date"], amount_minor=r["amount_minor"],
                                         description=r["description"], reference=r["reference"]))
             for r in selected if r["duplicate_kind"] != "in_ledger"]
    new_matches = _ledger_matches(db, business_id, batch["account_id"], fresh)
    if new_matches:
        db.execute(
            text(
                """
                UPDATE import_row SET duplicate_kind = 'in_ledger', duplicate_of_txn = :t, duplicate_of_row = NULL,
                       decision = 'pending', decided_by = NULL
                WHERE batch_id = :id AND row_number = :rn
                """
            ),
            [{"id": batch_id, "rn": rn, "t": t} for rn, t in new_matches.items()],
        )
        db.execute(text("UPDATE import_batch SET revision = revision + 1, updated_at = now() WHERE id = :id"), {"id": batch_id})
        db.commit()
        raise Conflict("New possible duplicates were found in the ledger. Review them before posting.",
                       pending_rows=sorted(new_matches))

    db.execute(
        text(
            """
            INSERT INTO ledger_transaction (business_id, account_id, business_date, amount_minor, currency, description,
                                            counterparty, reference, fingerprint, source_batch_id, source_row_number,
                                            parser_version, posted_by)
            VALUES (:b, :a, :d, :amt, :ccy, :desc, :cp, :ref, :fp, :batch, :rn, :pv, :u)
            """
        ),
        [
            {"b": business_id, "a": batch["account_id"], "d": r["business_date"], "amt": r["amount_minor"],
             "ccy": r["currency"], "desc": r["description"], "cp": r["counterparty"], "ref": r["reference"],
             "fp": r["fingerprint"], "batch": batch_id, "rn": r["row_number"], "pv": batch["parser_version"], "u": user_id}
            for r in selected
        ],
    )
    db.execute(
        text(
            """
            UPDATE import_batch SET status = 'posted', commit_key = :k, posted_count = :n, posted_at = now(),
                   posted_by = :u, revision = revision + 1, updated_at = now()
            WHERE id = :id
            """
        ),
        {"k": commit_key, "n": len(selected), "u": user_id, "id": batch_id},
    )
    return {"posted_count": len(selected), "replayed": False,
            "posted_rows": [r["row_number"] for r in selected], "parser_version": PARSER_VERSION}
