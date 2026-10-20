"""Role matrix for business-scoped actions.

Deny by default: an action that is not listed here is refused for every role, and a role that is not
listed for an action is refused. Later features add their actions to this table explicitly.
"""

from typing import Literal

Role = Literal["owner", "bookkeeper", "staff"]
ROLES: tuple[Role, ...] = ("owner", "bookkeeper", "staff")

ROLE_MATRIX: dict[str, frozenset[str]] = {
    # Read the business profile the user belongs to.
    "business.read": frozenset({"owner", "bookkeeper", "staff"}),
    # See who has access and with which role.
    "membership.read": frozenset({"owner", "bookkeeper", "staff"}),
    # Owner setting: change another member's role.
    "membership.manage": frozenset({"owner"}),
    # Download a business-scoped data export.
    "business.export": frozenset({"owner", "bookkeeper"}),
    # Owner setting: edit the business profile, reserve, and account labels; add accounts.
    "business.settings.update": frozenset({"owner"}),
    # See imported batches, staged previews, and posted ledger transactions.
    "records.read": frozenset({"owner", "bookkeeper", "staff"}),
    # Upload a file and map its columns. Staged rows are not official records.
    "import.stage": frozenset({"owner", "bookkeeper", "staff"}),
    # Include/exclude staged rows (including possible duplicates) and discard batches.
    "import.review": frozenset({"owner", "bookkeeper"}),
    # Post selected staged rows to the canonical ledger.
    "import.commit": frozenset({"owner", "bookkeeper"}),
    # Enter balance anchors, coverage periods, restricted funds, and own-account transfer links.
    "cash.manage": frozenset({"owner", "bookkeeper"}),
    # See invoices, bills, payments, customer dues, and upcoming supplier payments.
    "obligations.read": frozenset({"owner", "bookkeeper", "staff"}),
    # Enter invoices and bills, allocate and reverse payments, mark disputes, cancel.
    "obligations.write": frozenset({"owner", "bookkeeper"}),
    # Classify posted transactions (operating receipt/payment category, financing, owner money).
    "ledger.classify": frozenset({"owner", "bookkeeper"}),
    # Calculate a new Cash Outlook snapshot (reading outlooks only needs records.read).
    "outlook.run": frozenset({"owner", "bookkeeper"}),
    # See alerts and their supporting record IDs (all members; the same data the outlook and ledger show them).
    "alerts.read": frozenset({"owner", "bookkeeper", "staff"}),
    # Evaluate the alert rules now (reading alerts only needs alerts.read).
    "alerts.run": frozenset({"owner", "bookkeeper"}),
    # Run a Payment Review (saves a report; never pays) / read saved reports. Staff do not see payment decisions.
    "paymentreview.run": frozenset({"owner", "bookkeeper"}),
    "paymentreview.read": frozenset({"owner", "bookkeeper"}),
    # Funding Matches: run a match (saves an illustrative result; applies to no lender) / read the catalogue and saved runs.
    "funding.run": frozenset({"owner", "bookkeeper"}),
    "funding.read": frozenset({"owner", "bookkeeper"}),
    # Rescue plans (proposals only; nothing is changed) for a saved Payment Review.
    "rescue.run": frozenset({"owner", "bookkeeper"}),
    "rescue.read": frozenset({"owner", "bookkeeper"}),
    # Financing Readiness checklist for one product of a saved Funding Match (saves a check; applies to no lender).
    "readiness.run": frozenset({"owner", "bookkeeper"}),
    "readiness.read": frozenset({"owner", "bookkeeper"}),
    # Document register (owner ID, trade licence, ...): bookkeepers may see the entries; only the owner records or voids them.
    "documents.read": frozenset({"owner", "bookkeeper"}),
    "documents.write": frozenset({"owner"}),
    # Read the business's audit history.
    "audit.read": frozenset({"owner", "bookkeeper"}),
}


def is_allowed(role: str | None, action: str) -> bool:
    allowed = ROLE_MATRIX.get(action)
    return bool(allowed) and role in allowed
