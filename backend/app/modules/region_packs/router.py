"""Region Pack API: see which pack applies to a business, let the owner switch it, and offer a pack's import templates.

The active pack is the owner's last explicit choice, otherwise the pack for the business's jurisdiction, otherwise
none (the app then keeps its built-in presentation and Funding Matches reads the default sample catalogue). Choosing
a pack writes one append-only history row and one audit event. It never touches the ledger, balances, obligations,
saved reports or the business's currency, and no figure is converted between currencies.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, StrictStr
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.contracts.reference import JURISDICTIONS
from app.db import get_session
from app.modules.audit.service import Actor, record_event
from app.modules.identity.deps import BusinessContext, CurrentUser, current_user, require
from app.modules.region_packs.registry import (
    DEFAULT_PACK_ID, HISTORY_NOTE, NO_CONVERSION_NOTE, LoadedPack, default_catalogue, get_pack, load_packs, pack_for_jurisdiction,
)

router = APIRouter(prefix="/api/businesses/{business_id}", tags=["region pack"])
reference_router = APIRouter(prefix="/api/reference", tags=["reference"])
HISTORY_LIMIT = 10


class SelectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pack_id: StrictStr = Field(pattern=r"^[a-z][a-z0-9-]{1,19}$")
    # What the owner was looking at. A stale screen is refused instead of silently overwriting a newer choice.
    expected_current_pack_id: StrictStr | None = Field(pattern=r"^[a-z][a-z0-9-]{1,19}$")


def resolve(db: Session, business_id: UUID) -> dict:
    """The pack that applies to this business now, how it was chosen, and the facts it is compared with."""
    biz = db.execute(text("SELECT jurisdiction, base_currency FROM business WHERE id = :b"), {"b": business_id}).mappings().one()
    sel = db.execute(
        text("SELECT pack_id, pack_version FROM region_pack_selection WHERE business_id = :b ORDER BY selected_at DESC, id DESC LIMIT 1"),
        {"b": business_id},
    ).mappings().first()
    chosen = get_pack(sel["pack_id"]) if sel else None
    if chosen is not None:
        loaded, source = chosen, "selected"
    else:
        loaded = pack_for_jurisdiction(biz["jurisdiction"])
        source = "default" if loaded is not None else "none"
    return {"loaded": loaded, "source": source, "jurisdiction": biz["jurisdiction"], "base_currency": biz["base_currency"],
            "selected_version": sel["pack_version"] if sel and chosen is not None else None}


def catalogue_for_business(db: Session, business_id: UUID) -> dict:
    """The catalogue Funding Matches reads for this business, stamped with the pack (or marked as the default when none applies)."""
    loaded = resolve(db, business_id)["loaded"]
    if loaded is not None:
        return loaded.catalogue_for_run()
    return default_catalogue() | {"region_pack": {"id": None, "version": None, "jurisdiction": None, "currency": None,
                                                   "note": f"No region pack for this jurisdiction; the {DEFAULT_PACK_ID.upper()} sample catalogue was used."}}


def _warnings(r: dict) -> list[str]:
    loaded: LoadedPack | None = r["loaded"]
    if loaded is None:
        name = JURISDICTIONS.get(r["jurisdiction"], (r["jurisdiction"],))[0]
        return [f"There is no region pack for {name}. The app keeps its built-in number and date display, and Funding Matches reads "
                f"the {get_pack(DEFAULT_PACK_ID).pack.name} sample catalogue, so its products show as not offered in {r['jurisdiction']}."]
    out = []
    p = loaded.pack
    if p.jurisdiction != r["jurisdiction"]:
        out.append(f"This pack is for {p.name}, but the business is set up in {JURISDICTIONS[r['jurisdiction']][0]}. "
                   f"Funding Matches will list the pack's sample products as not offered in {r['jurisdiction']}.")
    if p.currency != r["base_currency"]:
        out.append(f"The pack's currency is {p.currency}, but the business keeps its books in {r['base_currency']}. "
                   f"Saved amounts stay in {r['base_currency']}; nothing is converted.")
    return out


def _view(db: Session, ctx: BusinessContext) -> dict:
    r = resolve(db, ctx.business_id)
    loaded: LoadedPack | None = r["loaded"]
    history = db.execute(
        text("""SELECT s.pack_id, s.pack_version, s.previous_pack_id, s.selected_at, u.display_name
                FROM region_pack_selection s JOIN app_user u ON u.id = s.selected_by
                WHERE s.business_id = :b ORDER BY s.selected_at DESC, s.id DESC LIMIT :n"""),
        {"b": ctx.business_id, "n": HISTORY_LIMIT},
    ).mappings().all()
    return {
        "business_id": str(ctx.business_id),
        "source": r["source"],
        "active": loaded.detail() if loaded else None,
        "selected_version": r["selected_version"],
        "outdated": bool(loaded and r["selected_version"] and r["selected_version"] != loaded.pack.version),
        "business": {"jurisdiction": r["jurisdiction"], "base_currency": r["base_currency"]},
        "jurisdiction_matches": bool(loaded and loaded.pack.jurisdiction == r["jurisdiction"]),
        "currency_matches": bool(loaded and loaded.pack.currency == r["base_currency"]),
        "warnings": _warnings(r),
        "available": [lp.detail() for lp in load_packs().values()],
        "history": [{"pack_id": h["pack_id"], "pack_version": h["pack_version"], "previous_pack_id": h["previous_pack_id"],
                     "selected_at": h["selected_at"].isoformat(), "selected_by": h["display_name"]} for h in history],
        "can_select": ctx.role == "owner",
        "no_conversion_note": NO_CONVERSION_NOTE,
        "history_note": HISTORY_NOTE,
    }


@reference_router.get("/region-packs")
def list_packs(_: CurrentUser = Depends(current_user)) -> list[dict]:
    return [lp.summary() for lp in load_packs().values()]


@router.get("/region-pack")
def get_region_pack(ctx: BusinessContext = Depends(require("regionpack.read")), db: Session = Depends(get_session)) -> dict:
    return _view(db, ctx)


@router.put("/region-pack")
def select_region_pack(body: SelectIn, ctx: BusinessContext = Depends(require("regionpack.select")), db: Session = Depends(get_session)) -> dict:
    actor = Actor(ctx.user.id, ctx.user.display_name, ctx.role)
    audit = dict(action="regionpack.select", actor=actor, business_id=ctx.business_id, target_type="business", target_id=ctx.business_id)
    target = get_pack(body.pack_id)
    if target is None:
        record_event(db, outcome="failed", details={"pack_id": body.pack_id, "reason": "unknown pack"}, **audit)
        db.commit()
        known = ", ".join(load_packs())
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, [{"loc": ["body", "pack_id"], "msg": f"unknown region pack; choose one of {known}", "type": "value_error"}])
    db.execute(text("SELECT 1 FROM business WHERE id = :b FOR UPDATE"), {"b": ctx.business_id})  # one switch at a time per business
    current = resolve(db, ctx.business_id)
    current_id = current["loaded"].pack.id if current["loaded"] else None
    if current_id != body.expected_current_pack_id:
        record_event(db, outcome="conflict", details={"expected": body.expected_current_pack_id, "actual": current_id, "to": target.pack.id}, **audit)
        db.commit()
        raise HTTPException(status.HTTP_409_CONFLICT, "The active region pack changed since you opened this page. Reload and try again.")
    if current_id == target.pack.id and current["source"] == "selected" and current["selected_version"] == target.pack.version:
        record_event(db, outcome="conflict", details={"expected": body.expected_current_pack_id, "actual": current_id, "to": target.pack.id,
                                                      "reason": "already active"}, **audit)
        db.commit()
        raise HTTPException(status.HTTP_409_CONFLICT, f"{target.pack.name} is already the active region pack.")
    db.execute(
        text("""INSERT INTO region_pack_selection (business_id, pack_id, pack_version, previous_pack_id, selected_by)
                VALUES (:b, :p, :v, :prev, :u)"""),
        {"b": ctx.business_id, "p": target.pack.id, "v": target.pack.version, "prev": current_id, "u": ctx.user.id},
    )
    record_event(db, outcome="success", details={"from": current_id, "to": target.pack.id, "version": target.pack.version,
                                                 "jurisdiction": target.pack.jurisdiction, "currency": target.pack.currency}, **audit)
    db.commit()
    return _view(db, ctx)


@router.get("/imports/{batch_id}/region-templates")
def import_templates(batch_id: UUID, ctx: BusinessContext = Depends(require("records.read")), db: Session = Depends(get_session)) -> dict:
    """The active pack's import templates checked against this file's header row. Offered as suggestions; the user still applies one."""
    b = db.execute(
        text("""SELECT ib.headers, ca.currency FROM import_batch ib
                JOIN cash_account ca ON ca.business_id = ib.business_id AND ca.id = ib.account_id
                WHERE ib.business_id = :b AND ib.id = :i"""),
        {"b": ctx.business_id, "i": batch_id},
    ).mappings().first()
    if b is None:  # includes a batch that belongs to another business
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Import not found")
    r = resolve(db, ctx.business_id)
    loaded: LoadedPack | None = r["loaded"]
    if loaded is None:
        return {"pack": None, "templates": []}
    headers = [h for h in b["headers"] if isinstance(h, str)]
    out = []
    for t in loaded.pack.import_templates:
        try:
            mapping, reason = t.mapping_for(headers).model_dump(), None
        except ValueError as exc:
            mapping, reason = None, str(exc)
        currency_ok = loaded.pack.currency == b["currency"]
        if mapping is not None and not currency_ok:
            reason = f"This template is for {loaded.pack.currency} statements, but the account is in {b['currency']}."
        out.append({"id": t.id, "name": t.name, "description": t.description, "date_format": t.date_format,
                    "matches_file": mapping is not None, "usable": mapping is not None and currency_ok,
                    "mapping": mapping if currency_ok else None, "reason": reason})
    return {"pack": loaded.stamp(), "account_currency": b["currency"], "templates": out}
