# Feature, Innovation, Implementation, and Security Matrix

**Legend:** ★ is a proposed product differentiator to demonstrate. It is not a claim of global novelty, patentability, or measured superiority. All rows are planned features; implementation status is tracked in [built_so_far.md](built_so_far.md).

| Feature | ★ Innovation | How to implement it | Security built into the feature |
| --- | --- | --- | --- |
| Business setup and records import |  | Capture business currency, timezone, accounts, reserve, and source register. Import one supported CSV/XLSX contract into staging; show mapping, errors, overlap, and duplicate candidates before atomic posting. | Authenticate uploaders; enforce file/content/row limits; keep originals private; never execute formulas; use revision and idempotency keys. |
| Trusted cash reconstruction |  | Maintain account-specific balance anchors, movement coverage, restrictions, own-account transfers, and freshness. Reconcile source periods and show gaps. | Do not turn a zero reconciliation residual into a completeness claim; exclude restricted cash from usable funds; preserve known liabilities and correction history. |
| Financial Health |  | Aggregate settled operating transactions into a labelled cash-basis summary; show outstanding obligations separately. Attach source IDs to displayed numbers. | Use server-side business scope; calculate in integer minor units; exclude transfers and financing proceeds from revenue; no AI-generated arithmetic. |
| Cash Outlook |  | Project 30 daily and 13 weekly balances from a frozen snapshot and known obligations; show expected and cautious assumptions. | Never treat a stale balance or unverified invoice as settled cash; keep currencies/accounts separate; version every forecast input. |
| Alerts |  | Run deterministic rules for reserve breach, overdue invoices, unusual spend, and repayment pressure; attach date, amount, and cause. | Send only minimal notification text; require login to inspect details; avoid duplicate alert spam; log trigger and version. |
| Payment Review | ★ Evidence-gated payment decision | Simulate one proposed payment from a named account in four scenarios; report first breach and exact missing facts. | Return “More information needed” for material unknowns; replace an existing payable event rather than double counting; never authorize payment from this result. |
| Cash Gap Rescue Plan | ★ No-borrow and borrow comparison | Re-run cash paths for invoice collection, permitted expense movement, financing, and selected combinations; calculate lowest cash and required funding. | Simulations do not mutate the ledger; supplier deferrals require permission evidence; owner approves any downstream action. |
| Funding Matches | ★ Repayment-fit ranking | Apply hard eligibility filters to a versioned product catalogue, compute total cost, and place repayments into the cash forecast before ranking. | Label illustrative matches; show exclusion reasons and source/update date for terms; do not present a match as lender approval or share data automatically. |
| Financing readiness |  | Show which required records and conditions are present, missing, or uncertain; offer specific verification steps. | Do not fabricate an approval probability from limited data; avoid sensitive traits in rules and review outcomes for unfair patterns before real use. |
| Receivables Guardian |  | Age outstanding invoices; draft reminders and track payment/settlement status separately. | Keep drafts internal until owner approval; use only verified customer contact details; log send outcome. |
| Transaction Fraud Monitor | ★ Fraud-aware cash fork | Detect duplicate candidates, unusual outflows, split payments, and paid-without-settlement cases; show both bounded cash outcomes until review. | Treat flags as suspicion; hold affected proposed sandbox payment; preserve the original record; require reviewer reason and audit event. |
| Conditional Funding Plan | ★ Event-triggered backup | Wait for an invoice settlement or owner-set deadline; recalculate the shortage; prepare a smaller backup match if needed. | Trigger produces a notification or draft only; owner approves data sharing and application; use idempotent execution and record the evidence for settlement. |
| Funding Passport | ★ Owner-controlled disclosure | Generate a versioned lender-ready packet with cash summary, obligations, and selected evidence. | Preview fields, mask unnecessary details, require owner consent, scope recipient and expiry, log exactly what was shared. |
| Finance Chat |  | Map a user question to validated read-only tools for analysis, forecast, review, or matches; explain structured results. | Give the AI no posting/payment credentials; treat documents as untrusted; render numeric values from code; deny unauthorized tool calls. |
| Approvals and Audit |  | Store an immutable proposal with exact payload, action tier, approval, sandbox outcome, actor, and timestamp. | Enforce role and tier at the API and again before execution; owner re-authenticates for sensitive actions; prevent self-approval and tampered-payload execution. |

## The innovation story to demonstrate

The differentiating workflow is **evidence-gated funding**: a cash shortage is detected; a suspect or missing record is shown with its decision impact; non-borrowing and financing paths are compared; repayments are tested on the same forecast; and an event-triggered backup is drafted only if cash does not arrive. The owner approves both action and information sharing.

## Proof expected in the demo

1. A source-linked financial figure and a dated shortfall alert.
2. A Payment Review that returns an honest shortfall or “More information needed.”
3. A fraud candidate that changes the forecast without silently removing a transaction.
4. Two illustrative financing options with different repayment effects.
5. A conditional plan that branches on settled versus late invoice data.
6. A staff action denied, an owner action approved, and both events visible in redacted sandbox logs.
