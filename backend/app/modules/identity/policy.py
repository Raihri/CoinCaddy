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
    # Approved Action Workflow: see the approval inbox / propose an exact T2 or T3 action (or withdraw your own) /
    # approve or reject it / re-check it and mark it executable. Staff take no part; nothing here executes an action.
    "approvals.read": frozenset({"owner", "bookkeeper"}),
    "approvals.propose": frozenset({"owner", "bookkeeper"}),
    "approvals.decide": frozenset({"owner"}),
    "approvals.release": frozenset({"owner"}),
    # Transaction Fraud Monitor: see flags, holds, and payee destinations / run the rules now / confirm, clear, or defer a
    # flag (clearing a flag that holds a payment is owner-only, checked in the route) / record a payee's bank or wallet
    # destination. Staff take no part. A flag is suspected activity, never a fraud verdict; nothing here pays or blocks a ledger row.
    "fraud.read": frozenset({"owner", "bookkeeper"}),
    "fraud.run": frozenset({"owner", "bookkeeper"}),
    "fraud.review": frozenset({"owner", "bookkeeper"}),
    "payees.destinations.write": frozenset({"owner", "bookkeeper"}),
    # Sandbox Action Executor: see the sandbox adapters and the redacted execution log / run an approved and released
    # proposal once through its mock adapter (owner only, like release). Nothing real is sent, submitted, paid, or shared.
    "executor.read": frozenset({"owner", "bookkeeper"}),
    "executor.run": frozenset({"owner"}),
    # Receivables Guardian: see aging, suggestions, contacts and drafts / draft a reminder / record a customer contact / verify or reject one.
    "guardian.read": frozenset({"owner", "bookkeeper"}),
    "guardian.draft": frozenset({"owner", "bookkeeper"}),
    "contact.write": frozenset({"owner", "bookkeeper"}),
    "contact.verify": frozenset({"owner"}),
    # Read the business's audit history.
    "audit.read": frozenset({"owner", "bookkeeper"}),
}


def is_allowed(role: str | None, action: str) -> bool:
    allowed = ROLE_MATRIX.get(action)
    return bool(allowed) and role in allowed
