"""Records Import API: upload to staging, map columns, review rows, and post selected rows."""

import hashlib
import json
import unicodedata
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_session
from app.modules.audit.service import Actor, record_event
from app.modules.identity.deps import BusinessContext, require
from app.modules.records import storage
from app.modules.records.files import MAX_FILE_BYTES, PARSER_VERSION, ImportRejected, parse_upload
from app.modules.records.normalize import Mapping
from app.modules.records.service import Conflict, apply_mapping, column_profile, commit_batch, counts, suggest_mapping

router = APIRouter(prefix="/api/businesses/{business_id}", tags=["records import"])

STAGED_NOTICE = "Staged rows are a preview only. They are not official records and are not counted as cash until posted."


class MappingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: StrictInt = Field(ge=1)
    mapping: dict


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: StrictInt = Field(ge=1)
    decision: Literal["include", "exclude"]


class CommitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: StrictInt = Field(ge=1)
    idempotency_key: UUID


class DiscardRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: StrictInt = Field(ge=1)


def _actor(ctx: BusinessContext) -> Actor:
    return Actor(ctx.user.id, ctx.user.display_name, ctx.role)


def _err(status_code: int, message: str, **extra) -> HTTPException:
    return HTTPException(status_code, {"message": message, **extra})


def _safe_name(name: str | None) -> str:
    base = (name or "upload").replace("\\", "/").rsplit("/", 1)[-1]
    base = "".join(ch for ch in unicodedata.normalize("NFC", base) if unicodedata.category(ch)[0] != "C")
    base = " ".join(base.split()) or "upload"
    if len(base) > 120:
        stem, _, ext = base.rpartition(".")
        base = (stem[: 115 - len(ext)] + "…." + ext) if stem else base[:120]
    return base


def _account(db: Session, business_id: UUID, account_id: UUID):
    return db.execute(
        text("SELECT id, label, currency FROM cash_account WHERE business_id = :b AND id = :a"),
        {"b": business_id, "a": account_id},
    ).mappings().first()


def _batch(db: Session, business_id: UUID, batch_id: UUID, *, lock: bool = False):
    row = db.execute(
        text(
            f"""
            SELECT ib.*, ca.label AS account_label, ca.currency AS account_currency, b.timezone,
                   u.display_name AS uploaded_by_name
            FROM import_batch ib
            JOIN cash_account ca ON ca.business_id = ib.business_id AND ca.id = ib.account_id
            JOIN business b ON b.id = ib.business_id
            JOIN app_user u ON u.id = ib.uploaded_by
            WHERE ib.business_id = :b AND ib.id = :id {'FOR UPDATE OF ib' if lock else ''}
            """
        ),
        {"b": business_id, "id": batch_id},
    ).mappings().first()
    if row is None:  # includes a batch that belongs to another business
        raise _err(status.HTTP_404_NOT_FOUND, "Import not found")
    return row


def _detail(db: Session, business_id: UUID, batch_id: UUID) -> dict:
    b = _batch(db, business_id, batch_id)
    return {
        "id": str(b["id"]),
        "account": {"id": str(b["account_id"]), "label": b["account_label"], "currency": b["account_currency"]},
        "source_name": b["source_name"],
        "file_type": b["file_type"],
        "size_bytes": b["size_bytes"],
        "sha256": b["content_sha256"],
        "parser_version": b["parser_version"],
        "status": b["status"],
        "revision": b["revision"],
        "row_count": b["row_count"],
        "headers": b["headers"],
        "column_profile": b["column_profile"],
        "mapping": b["mapping"],
        "suggested_mapping": suggest_mapping(b["headers"], b["column_profile"]),
        "uploaded_by": b["uploaded_by_name"],
        "created_at": b["created_at"].isoformat(),
        "posted_at": b["posted_at"].isoformat() if b["posted_at"] else None,
        "posted_count": b["posted_count"],
        "counts": counts(db, b["id"]),
        "notice": STAGED_NOTICE,
    }


# --- summary and lists -------------------------------------------------------------------------------


@router.get("/records/summary")
def records_summary(ctx: BusinessContext = Depends(require("records.read")), db: Session = Depends(get_session)) -> dict:
    """Per-account posted-record status plus open imports. Staged rows are counted separately, never as cash."""
    accounts = db.execute(
        text(
            """
            SELECT ca.id, ca.label, ca.kind, ca.currency,
                   count(lt.id) AS posted_transactions,
                   max(lt.business_date) AS latest_business_date,
                   max(lt.posted_at) AS last_posted_at
            FROM cash_account ca
            LEFT JOIN ledger_transaction lt ON lt.business_id = ca.business_id AND lt.account_id = ca.id
            WHERE ca.business_id = :b
            GROUP BY ca.id ORDER BY ca.created_at, ca.external_ref
            """
        ),
        {"b": ctx.business_id},
    ).mappings().all()
    return {
        "accounts": [
            {"id": str(a["id"]), "label": a["label"], "kind": a["kind"], "currency": a["currency"],
             "posted_transactions": a["posted_transactions"],
             "latest_business_date": a["latest_business_date"].isoformat() if a["latest_business_date"] else None,
             "last_posted_at": a["last_posted_at"].isoformat() if a["last_posted_at"] else None}
            for a in accounts
        ],
        "open_imports": [i for i in list_imports(ctx, db) if i["status"] in ("needs_mapping", "staged")],
        "notice": STAGED_NOTICE,
    }


@router.get("/imports")
def list_imports(ctx: BusinessContext = Depends(require("records.read")), db: Session = Depends(get_session)) -> list[dict]:
    rows = db.execute(
        text(
            """
            SELECT ib.id, ib.source_name, ib.file_type, ib.status, ib.revision, ib.row_count, ib.created_at,
                   ib.posted_at, ib.posted_count, ca.label AS account_label, u.display_name AS uploaded_by,
                   count(ir.*) FILTER (WHERE ir.status = 'error') AS error_rows,
                   count(ir.*) FILTER (WHERE ir.decision = 'pending') AS pending_rows,
                   count(ir.*) FILTER (WHERE ir.decision = 'include') AS included_rows
            FROM import_batch ib
            JOIN cash_account ca ON ca.business_id = ib.business_id AND ca.id = ib.account_id
            JOIN app_user u ON u.id = ib.uploaded_by
            LEFT JOIN import_row ir ON ir.batch_id = ib.id
            WHERE ib.business_id = :b
            GROUP BY ib.id, ca.label, u.display_name
            ORDER BY ib.created_at DESC
            LIMIT 100
            """
        ),
        {"b": ctx.business_id},
    ).mappings().all()
    return [
        {**{k: r[k] for k in ("source_name", "file_type", "status", "revision", "row_count", "posted_count",
                              "account_label", "uploaded_by", "error_rows", "pending_rows", "included_rows")},
         "id": str(r["id"]), "created_at": r["created_at"].isoformat(),
         "posted_at": r["posted_at"].isoformat() if r["posted_at"] else None}
        for r in rows
    ]


@router.get("/ledger")
def ledger(
    account_id: UUID | None = None,
    offset: int = Query(0, ge=0, le=1_000_000),
    limit: int = Query(50, ge=1, le=200),
    ctx: BusinessContext = Depends(require("records.read")),
    db: Session = Depends(get_session),
) -> dict:
    params = {"b": ctx.business_id, "a": account_id, "limit": limit, "offset": offset}
    where = "lt.business_id = :b AND (CAST(:a AS uuid) IS NULL OR lt.account_id = :a)"
    total = db.execute(text(f"SELECT count(*) FROM ledger_transaction lt WHERE {where}"), params).scalar_one()
    rows = db.execute(
        text(
            f"""
            SELECT lt.id, lt.business_date, lt.amount_minor, lt.currency, lt.description, lt.counterparty, lt.reference,
                   lt.source_batch_id, lt.source_row_number, lt.parser_version, lt.posted_at, ca.label AS account_label
            FROM ledger_transaction lt JOIN cash_account ca ON ca.business_id = lt.business_id AND ca.id = lt.account_id
            WHERE {where}
            ORDER BY lt.business_date DESC, lt.posted_at DESC, lt.source_row_number
            LIMIT :limit OFFSET :offset
            """
        ),
        params,
    ).mappings().all()
    return {
        "total": total,
        "transactions": [
            {**{k: r[k] for k in ("amount_minor", "currency", "description", "counterparty", "reference",
                                  "source_row_number", "parser_version", "account_label")},
             "id": str(r["id"]), "business_date": r["business_date"].isoformat(),
             "source_batch_id": str(r["source_batch_id"]), "posted_at": r["posted_at"].isoformat()}
            for r in rows
        ],
    }


# --- upload ------------------------------------------------------------------------------------------


@router.post("/imports", status_code=status.HTTP_201_CREATED)
def upload(
    account_id: UUID = Form(...),
    file: UploadFile = File(...),
    ctx: BusinessContext = Depends(require("import.stage")),
    db: Session = Depends(get_session),
):
    account = _account(db, ctx.business_id, account_id)
    if account is None:
        raise _err(status.HTTP_404_NOT_FOUND, "Account not found")
    name = _safe_name(file.filename)
    data = file.file.read(MAX_FILE_BYTES + 1)
    audit = dict(action="import.upload", actor=_actor(ctx), business_id=ctx.business_id)
    try:
        parsed = parse_upload(name, data)
    except ImportRejected as rejected:
        record_event(db, outcome="failed", target_type="cash_account", target_id=account_id,
                     details={"source_name": name, "size_bytes": len(data), "reason": rejected.code}, **audit)
        db.commit()
        http = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE if rejected.code == "too_large" else status.HTTP_422_UNPROCESSABLE_ENTITY
        raise _err(http, rejected.message, code=rejected.code) from None

    sha = hashlib.sha256(data).hexdigest()
    existing = _existing_batch(db, ctx.business_id, account_id, sha)
    if existing:
        return _same_file(db, ctx, audit, existing, name)

    batch_id = uuid4()
    key = storage.save_original(ctx.business_id, batch_id, parsed.file_type, data)
    try:
        db.execute(
            text(
                """
                INSERT INTO import_batch (id, business_id, account_id, uploaded_by, source_name, file_type, content_sha256,
                                          size_bytes, storage_key, parser_version, status, headers, column_profile, row_count)
                VALUES (:id, :b, :a, :u, :name, :ft, :sha, :size, :key, :pv, 'needs_mapping',
                        CAST(:headers AS jsonb), CAST(:profile AS jsonb), :n)
                """
            ),
            {"id": batch_id, "b": ctx.business_id, "a": account_id, "u": ctx.user.id, "name": name,
             "ft": parsed.file_type, "sha": sha, "size": len(data), "key": key, "pv": PARSER_VERSION,
             "headers": _json(parsed.headers), "profile": _json(column_profile(parsed.headers, parsed.rows)),
             "n": len(parsed.rows)},
        )
        db.execute(
            text("INSERT INTO import_row (business_id, batch_id, row_number, raw) VALUES (:b, :id, :rn, CAST(:raw AS jsonb))"),
            [{"b": ctx.business_id, "id": batch_id, "rn": i + 2, "raw": _json(r)} for i, r in enumerate(parsed.rows)],
        )
        record_event(db, outcome="success", target_type="import_batch", target_id=batch_id,
                     details={"source_name": name, "file_type": parsed.file_type, "size_bytes": len(data),
                              "rows": len(parsed.rows), "sha256_prefix": sha[:12], "account": account["label"]}, **audit)
        db.commit()
    except IntegrityError:
        # Another request stored the same file for this account at the same moment.
        db.rollback()
        storage.remove_original(key)
        existing = _existing_batch(db, ctx.business_id, account_id, sha)
        if existing is None:
            raise
        return _same_file(db, ctx, audit, existing, name)
    except Exception:
        db.rollback()
        storage.remove_original(key)
        raise
    return _detail(db, ctx.business_id, batch_id) | {"duplicate_upload": False}


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def _existing_batch(db: Session, business_id: UUID, account_id: UUID, sha: str):
    return db.execute(
        text(
            """
            SELECT id, status, created_at FROM import_batch
            WHERE business_id = :b AND account_id = :a AND content_sha256 = :sha AND status <> 'discarded'
            """
        ),
        {"b": business_id, "a": account_id, "sha": sha},
    ).mappings().first()


def _same_file(db: Session, ctx: BusinessContext, audit: dict, existing, name: str) -> JSONResponse:
    record_event(db, outcome="success", target_type="import_batch", target_id=existing["id"],
                 details={"source_name": name, "duplicate_upload": True, "existing_status": existing["status"]}, **audit)
    db.commit()
    body = _detail(db, ctx.business_id, existing["id"]) | {"duplicate_upload": True}
    return JSONResponse(body, status_code=status.HTTP_200_OK)


# --- batch detail, rows, mapping -----------------------------------------------------------------------


@router.get("/imports/{batch_id}")
def get_import(batch_id: UUID, ctx: BusinessContext = Depends(require("records.read")), db: Session = Depends(get_session)) -> dict:
    return _detail(db, ctx.business_id, batch_id)


@router.get("/imports/{batch_id}/rows")
def import_rows(
    batch_id: UUID,
    view: Literal["all", "errors", "duplicates", "valid", "included", "excluded"] = "all",
    offset: int = Query(0, ge=0, le=10_000),
    limit: int = Query(100, ge=1, le=500),
    ctx: BusinessContext = Depends(require("records.read")),
    db: Session = Depends(get_session),
) -> dict:
    _batch(db, ctx.business_id, batch_id)
    filters = {
        "all": "TRUE", "errors": "ir.status = 'error'", "duplicates": "ir.duplicate_kind IS NOT NULL",
        "valid": "ir.status = 'valid'", "included": "ir.decision = 'include'",
        "excluded": "ir.status = 'valid' AND ir.decision = 'exclude'",
    }
    where = f"ir.business_id = :b AND ir.batch_id = :id AND {filters[view]}"
    params = {"b": ctx.business_id, "id": batch_id, "limit": limit, "offset": offset}
    total = db.execute(text(f"SELECT count(*) FROM import_row ir WHERE {where}"), params).scalar_one()
    rows = db.execute(
        text(
            f"""
            SELECT ir.*, lt.business_date AS dup_date, lt.amount_minor AS dup_amount, lt.description AS dup_description,
                   lt.reference AS dup_reference
            FROM import_row ir
            LEFT JOIN ledger_transaction lt ON lt.id = ir.duplicate_of_txn AND lt.business_id = ir.business_id
            WHERE {where} ORDER BY ir.row_number LIMIT :limit OFFSET :offset
            """
        ),
        params,
    ).mappings().all()
    return {
        "total": total,
        "rows": [
            {
                "row_number": r["row_number"], "raw": r["raw"], "status": r["status"], "errors": r["errors"],
                "business_date": r["business_date"].isoformat() if r["business_date"] else None,
                "amount_minor": r["amount_minor"], "currency": r["currency"], "description": r["description"],
                "counterparty": r["counterparty"], "reference": r["reference"], "decision": r["decision"],
                "duplicate": None if r["duplicate_kind"] is None else {
                    "kind": r["duplicate_kind"], "row_number": r["duplicate_of_row"],
                    "transaction": None if r["duplicate_of_txn"] is None else {
                        "id": str(r["duplicate_of_txn"]), "business_date": r["dup_date"].isoformat(),
                        "amount_minor": r["dup_amount"], "description": r["dup_description"], "reference": r["dup_reference"],
                    },
                },
            }
            for r in rows
        ],
    }


@router.put("/imports/{batch_id}/mapping")
def set_mapping(batch_id: UUID, body: MappingRequest, ctx: BusinessContext = Depends(require("import.stage")),
                db: Session = Depends(get_session)) -> dict:
    b = _batch(db, ctx.business_id, batch_id, lock=True)
    if b["status"] not in ("needs_mapping", "staged"):
        raise _err(status.HTTP_409_CONFLICT, f"A {b['status']} import cannot be re-mapped.")
    if b["revision"] != body.expected_revision:
        raise _err(status.HTTP_409_CONFLICT, "The import changed since you loaded it. Reload and try again.", revision=b["revision"])
    try:
        mapping = Mapping.model_validate(body.mapping)
        mapping.check_against(len(b["headers"]))
    except (ValidationError, ValueError) as exc:
        msg = exc.errors()[0]["msg"] if isinstance(exc, ValidationError) else str(exc)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            [{"loc": ["body", "mapping"], "msg": msg.replace("Value error, ", ""), "type": "value_error"}]) from None
    apply_mapping(db, business_id=ctx.business_id, batch_id=batch_id, account_id=b["account_id"],
                  currency=b["account_currency"], timezone=b["timezone"], mapping=mapping)
    c = counts(db, batch_id)
    headers = b["headers"]
    record_event(db, action="import.map", outcome="success", actor=_actor(ctx), business_id=ctx.business_id,
                 target_type="import_batch", target_id=batch_id,
                 details={"date_format": mapping.date_format, "amount_mode": mapping.amount_mode,
                          "date_column": headers[mapping.date_column], "valid_rows": c["valid"],
                          "error_rows": c["error"], "possible_duplicates": c["duplicates"]})
    db.commit()
    return _detail(db, ctx.business_id, batch_id)


# --- review, commit, discard ---------------------------------------------------------------------------


@router.patch("/imports/{batch_id}/rows/{row_number}")
def decide_row(batch_id: UUID, row_number: int, body: DecisionRequest,
               ctx: BusinessContext = Depends(require("import.review")), db: Session = Depends(get_session)) -> dict:
    b = _batch(db, ctx.business_id, batch_id, lock=True)
    if b["status"] != "staged":
        raise _err(status.HTTP_409_CONFLICT, "Only a staged import can be reviewed.")
    if b["revision"] != body.expected_revision:
        raise _err(status.HTTP_409_CONFLICT, "The import changed since you loaded it. Reload and try again.", revision=b["revision"])
    row = db.execute(
        text("SELECT status, decision, duplicate_kind FROM import_row WHERE batch_id = :id AND row_number = :rn"),
        {"id": batch_id, "rn": row_number},
    ).mappings().first()
    if row is None:
        raise _err(status.HTTP_404_NOT_FOUND, "Row not found")
    if row["status"] != "valid":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            [{"loc": ["body", "decision"], "msg": "a row with errors cannot be included; fix the file and upload again", "type": "value_error"}])
    db.execute(
        text("UPDATE import_row SET decision = :d, decided_by = :u WHERE batch_id = :id AND row_number = :rn"),
        {"d": body.decision, "u": ctx.user.id, "id": batch_id, "rn": row_number},
    )
    db.execute(text("UPDATE import_batch SET revision = revision + 1, updated_at = now() WHERE id = :id"), {"id": batch_id})
    record_event(db, action="import.row_decision", outcome="success", actor=_actor(ctx), business_id=ctx.business_id,
                 target_type="import_batch", target_id=batch_id,
                 details={"row_number": row_number, "from": row["decision"], "to": body.decision,
                          "possible_duplicate": row["duplicate_kind"]})
    db.commit()
    return _detail(db, ctx.business_id, batch_id)


@router.post("/imports/{batch_id}/commit")
def commit(batch_id: UUID, body: CommitRequest, ctx: BusinessContext = Depends(require("import.commit")),
           db: Session = Depends(get_session)) -> dict:
    audit = dict(action="import.commit", actor=_actor(ctx), business_id=ctx.business_id,
                 target_type="import_batch", target_id=batch_id)
    try:
        result = commit_batch(db, business_id=ctx.business_id, batch_id=batch_id, user_id=ctx.user.id,
                              expected_revision=body.expected_revision, commit_key=body.idempotency_key)
    except LookupError:
        db.rollback()
        raise _err(status.HTTP_404_NOT_FOUND, "Import not found") from None
    except Conflict as c:
        db.rollback()
        record_event(db, outcome="conflict", details={"reason": c.message, **c.extra}, **audit)
        db.commit()
        raise _err(status.HTTP_409_CONFLICT, c.message, **c.extra) from None
    except ValueError as v:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            [{"loc": ["body"], "msg": str(v), "type": "value_error"}]) from None
    if not result["replayed"]:
        record_event(db, outcome="success", details={"posted_count": result["posted_count"],
                                                     "parser_version": result["parser_version"]}, **audit)
    db.commit()
    return _detail(db, ctx.business_id, batch_id) | {"commit": {"posted_count": result["posted_count"], "replayed": result["replayed"]}}


@router.post("/imports/{batch_id}/discard")
def discard(batch_id: UUID, body: DiscardRequest, ctx: BusinessContext = Depends(require("import.review")),
            db: Session = Depends(get_session)) -> dict:
    b = _batch(db, ctx.business_id, batch_id, lock=True)
    if b["status"] == "posted":
        raise _err(status.HTTP_409_CONFLICT, "A posted import cannot be discarded. Post a correction instead.")
    if b["status"] == "discarded":
        return _detail(db, ctx.business_id, batch_id)
    if b["revision"] != body.expected_revision:
        raise _err(status.HTTP_409_CONFLICT, "The import changed since you loaded it. Reload and try again.", revision=b["revision"])
    db.execute(text("UPDATE import_batch SET status = 'discarded', revision = revision + 1, updated_at = now() WHERE id = :id"),
               {"id": batch_id})
    record_event(db, action="import.discard", outcome="success", actor=_actor(ctx), business_id=ctx.business_id,
                 target_type="import_batch", target_id=batch_id, details={"rows": b["row_count"]})
    db.commit()
    return _detail(db, ctx.business_id, batch_id)
