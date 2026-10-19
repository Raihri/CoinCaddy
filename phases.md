# SME Finance Copilot — Build Phases

**Planning assumption:** A competition prototype uses synthetic business data and mock bank/lender APIs. The stated submission deadline is **20 October 2026, 23:59 Beijing time / 21:59 Dhaka time**. These phases are dependency gates, not promises of how many hours a team will need.

## Immediate competition path

If submitting at the current deadline, the entry should demonstrate one working journey across the three Topic E requirements. Do not present design screens as completed functionality.

| Order | Minimum runnable slice | Evidence to retain |
| --- | --- | --- |
| C1 | Create one synthetic business, bank account, invoices, bills, reserve, and three illustrative financing products. Freeze independent expected cash calculations. | Fixture files and hand-checked expected results. |
| C2 | Expose accepted data through an API; render Financial Health, Cash Outlook, and a dated alert. | Request/response logs and passing arithmetic tests. |
| C3 | Run Payment Review and one Cash Gap Rescue comparison; show the exact record that changes the result. | Four scenario results, source references, “More information needed” test. |
| C4 | Rank two financing products after inserting repayments into the forecast. Label terms illustrative. | Eligibility reasons, total cost, and low-cash comparison. |
| C5 | Flag one suspected duplicate transaction; require owner approval for one sandbox reminder or application draft. | Denied/approved action logs, reviewer reason, and fraud case. |
| C6 | Finish technical documentation, security self-assessment with known risks, repository README/deployment/tests, a ≤10-minute deck, and a ≤5-minute demo video. | Final test report, sandbox operation logs, video, and presentation. |

The execution-evidence item calls for sandbox logs and a demo video for non-on-chain topics, even though the separate demo-video item is described as optional. Prepare both. Verify registration, team eligibility, and the organizer’s current submission channel independently.

## Full product sequence after the competition

### Phase 0 — Contracts and reference fixtures

Define money units, date/time rules, account boundaries, source formats, reserve semantics, roles, and failure states. Create an independent reference calculator and synthetic fixtures for delayed receipts, missing anchors, duplicates, transfers, partial allocations, and same-day timing.

**Exit gate:** Written input/output contracts and exact expected answers for all frozen fixtures.

### Phase 1 — Identity, sources, and safe import

Implement membership-based business scoping, an initial CSV/XLSX adapter, private upload storage, staging, mapping preview, duplicate review, atomic commit, idempotency, and audit events.

**Exit gate:** Repeated upload cannot double post; rejected rows remain visible; staff cannot access another business’s data.

### Phase 2 — Trusted ledger and analysis

Add balance anchors, reconciliation, coverage and freshness, transfers, receivables/payables, allocations, corrections, and Financial Health views.

**Exit gate:** All displayed totals tie to accepted records; known liabilities remain visible when a source is excluded; concurrency tests prevent over-allocation.

### Phase 3 — Cash decisions

Build 30-day and 13-week outlooks, reserve alerts, four-scenario Payment Review, evidence-impact queue, saved reports, and Rescue Plan comparison.

**Exit gate:** Independent fixture results match exactly; no positive payment result is returned for a known breach or material missing input.

### Phase 4 — Capital features

Implement the versioned illustrative financing catalogue, eligibility filters, total-cost calculation, repayment-fit ranking, readiness guide, Conditional Funding Plan, and owner-controlled Funding Passport.

**Exit gate:** Each ranking has reasons; repayment dates affect the forecast; no simulated match is labelled as an actual offer.

### Phase 5 — Fraud monitor and controlled agent

Add transaction-level fraud rules, review holds, role/action tiers, approval inbox, read-only finance assistant, sandbox reminder/application action, and redacted operation logs. Run prompt-injection and access tests.

**Exit gate:** Unauthorized mutations, cross-business access, and unapproved sandbox actions are denied in the frozen adversarial suite. Measure both caught cases and false flags.

### Phase 6 — Pilot and operational hardening

Validate lawful repeatable source access; run consented user tasks; compare against a bookkeeper/spreadsheet workflow; review privacy and financing rules in each target jurisdiction; rehearse restore and deletion; establish support and importer maintenance costs.

**Exit gate:** Correctness and security checks pass, real users understand the outputs, and continuation decisions use measured task time, support cost, and repeat use.

## Build discipline

- Security requirements apply from Phase 1 onward and are strengthened at each gate; Phase 5 does not defer basic authorization.
- Use a modular monolith until observed load justifies more services.
- Add OCR, live bank/lender integrations, multi-region packs, and predictive payment probabilities only after source access, correctness, and permission requirements are met.
- Track planned versus implemented versus tested features in [built_so_far.md](built_so_far.md).
