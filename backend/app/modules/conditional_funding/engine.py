"""Conditional Funding Plan scenarios, built on the existing Cash Outlook calculation."""

from __future__ import annotations

import copy
from datetime import date, timedelta

from app.modules.funding.engine import _cash_view, _schedule
from app.modules.outlook.engine import CALC_VERSION as OUTLOOK_VERSION, compute

CALC_VERSION = "conditional-funding-v1"


def receipt_case(outlook: dict, obligation_id: str, amount_minor: int, expected_date: date, *, include_receipt: bool) -> dict:
    """Replace the planned portion of the invoice's ordinary forecast event with the plan assumption."""
    changed = copy.deepcopy(outlook)
    found = next((o for o in changed["obligations"] if o["id"] == obligation_id), None)
    if found is not None:
        planned = min(amount_minor, found["outstanding_minor"])
        found["outstanding_minor"] -= planned
        if found["outstanding_minor"] == 0:
            changed["obligations"].remove(found)
    else:
        planned = 0
    if include_receipt and planned:
        original = next(o for o in outlook["obligations"] if o["id"] == obligation_id)
        changed["obligations"].append(original | {"id": f"conditional-receipt:{obligation_id}",
                                                  "outstanding_minor": planned,
                                                  "due_date": expected_date.isoformat(), "disputed": False})
    return changed


def backup_case(outlook: dict, *, obligation_id: str, expected_amount_minor: int, include_receipt: bool,
                receipt_date: date, as_of: date, disbursement_date: date, funding_run: dict, product: dict) -> dict:
    """Recalculate a no-borrow or illustrative-backup path without changing the source outlook."""
    changed = receipt_case(outlook, obligation_id, expected_amount_minor, receipt_date, include_receipt=include_receipt)
    req = funding_run["request"]
    terms = _schedule(product, req["amount_minor"], disbursement_date)
    account = next((a for a in changed["accounts"] if a["id"] == req["receiving_account_id"]), None)
    if account is None:
        return {"calc_version": CALC_VERSION, "outlook_version": OUTLOOK_VERSION, "currency": req["currency"],
                "forecast": {}, "note": "The receiving account is no longer available in the current cash forecast."}
    account["restriction_releases"].append({"id": "conditional-funding-proceeds", "amount_minor": terms["net_proceeds_minor"],
                                           "reason": f"Illustrative {product['name']} proceeds", "effective_to": (disbursement_date - timedelta(days=1)).isoformat()})
    for index, row in enumerate(terms["schedule"], start=1):
        changed["obligations"].append({"id": f"conditional-repayment-{index}", "direction": "payable", "party": product["provider"],
                                       "document_number": f"Illustrative repayment {index}", "due_date": row["date"],
                                       "outstanding_minor": row["amount_minor"], "currency": req["currency"], "disputed": False,
                                       "settlement_account_id": req["receiving_account_id"]})
    return {"calc_version": CALC_VERSION, "outlook_version": OUTLOOK_VERSION, "currency": req["currency"],
            "as_of": as_of.isoformat(), "assumed_disbursement_date": disbursement_date.isoformat(),
            "terms": terms, "forecast": _cash_view(compute(changed), req["currency"]),
            "note": "Illustrative scenario only; it does not apply for or receive financing."}


def no_borrow_case(outlook: dict, *, obligation_id: str, expected_amount_minor: int, receipt_date: date,
                   as_of: date, include_receipt: bool) -> dict:
    changed = receipt_case(outlook, obligation_id, expected_amount_minor, receipt_date, include_receipt=include_receipt)
    return {"calc_version": CALC_VERSION, "outlook_version": OUTLOOK_VERSION,
            "currency": next((o["currency"] for o in outlook["obligations"] if o["id"] == obligation_id), None),
            "as_of": as_of.isoformat(), "forecast": _cash_view(compute(changed),
                next((o["currency"] for o in outlook["obligations"] if o["id"] == obligation_id), "")),
            "note": "No financing assumed."}
