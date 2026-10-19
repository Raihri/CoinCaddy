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
    # Read the business's audit history.
    "audit.read": frozenset({"owner", "bookkeeper"}),
}


def is_allowed(role: str | None, action: str) -> bool:
    allowed = ROLE_MATRIX.get(action)
    return bool(allowed) and role in allowed
