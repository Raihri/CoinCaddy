# SME Finance Copilot — Product Requirements Document

**Status:** Proposed product; static mockups only as of 20 October 2026  
**Primary audience:** Small-business owners, with bookkeeper and staff support  
**Competition fit:** Topic E — financing-option matching, cash-flow management with alerts, and business financial analysis

## 1. Product promise

Help an owner understand what the business earned and spent, know when cash may run short, and compare a safe response. The product must show which records support every important figure. It must ask for verification when a missing or suspicious record could change a payment or financing decision.

The flagship experience is an **Evidence-Gated Cash Rescue Plan**: detect a specific shortage, review its evidence, compare a collection or spending change with financing, and show the smallest useful funding amount and repayment effect. The owner remains in control.

## 2. Target users and jobs

| User | Job |
| --- | --- |
| Owner | Understand business health; check whether a payment is affordable; compare financing; approve sharing and actions. |
| Bookkeeper | Import, reconcile, correct, and explain records; maintain obligations and source quality. |
| Staff member | Submit documents and proposed records for review without changing approved financial conclusions. |

The initial demo persona is an owner-operated trading business with a bank account, mobile-wallet activity, customer invoices, supplier bills, and a cash reserve. The region and currency shown in the mockups are illustrative and configurable.

## 3. User-visible features

| ID | Feature | Minimum behavior |
| --- | --- | --- |
| F01 | Records | Import supported CSV/XLSX transactions and enter invoices/bills; preview errors and duplicates; post accepted rows once. |
| F02 | Financial Health | Show a clearly labelled cash-basis operating summary, customer dues, upcoming supplier payments, and source references. |
| F03 | Cash Outlook | Show daily 30-day and weekly 13-week views with confirmed, expected, and uncertain items distinguished. |
| F04 | Alerts | State the date, amount, and cause of a reserve shortfall, overdue invoice, or unusual outflow. |
| F05 | Payment Review | Compare four explicit scenarios for a proposed payment from a named account; return within reserve, shortfall, or more information needed. |
| F06 | Cash Gap Rescue Plan | Compare collection, permitted payment rescheduling, financing, and combinations; show their effect on minimum cash. |
| F07 | Funding Matches | Filter a product catalogue by documented eligibility, amount, timing, required documents, cost, and repayment fit. |
| F08 | Receivables Guardian | Track overdue invoices and draft reminders for owner review. |
| F09 | Transaction Fraud Monitor | Flag possible duplicates and unusual outgoing transactions; show why and hold a proposed suspicious sandbox payment. |
| F10 | Conditional Funding Plan | Prepare a no-borrow path and a backup funding path, then re-evaluate when an expected receipt settles or misses its trigger date. |
| F11 | Funding Passport | Create an owner-reviewed summary with selected supporting fields for a simulated lender. |
| F12 | Finance Chat | Answer questions using approved read-only calculations and records; ask for missing facts. |
| F13 | Approvals & Audit | Apply role and action-tier checks, require owner approval for external actions, and log each decision. |

Detailed implementation and security requirements for every feature are in [feature_security_matrix.md](feature_security_matrix.md).

## 4. Primary journeys

### Journey A — understand the business

The bookkeeper imports records, resolves mapping errors, reviews duplicate candidates, and confirms account coverage. The owner opens Financial Health and can trace any displayed total to its supporting transactions.

### Journey B — prevent a cash shortage

The Cash Outlook finds a dated reserve breach. The owner opens Payment Review for a proposed supplier payment. Four scenarios show whether the payment remains within the reserve. A material missing or suspicious record produces “More information needed” and an explicit verification task.

### Journey C — choose financing only when needed

The Rescue Plan compares collecting an invoice, moving an optional payment with permission, and financing. Funding Matches filters products and inserts each repayment schedule into the forecast. A Conditional Funding Plan waits for a receipt to settle before presenting a backup application draft. The owner may prepare a Funding Passport and decide exactly what to share.

### Journey D — investigate a suspicious transaction

The monitor flags a possible duplicate or unusual proposed payment, shows the two source transactions, and calculates both plausible cash outcomes. The proposed sandbox payment is held until an authorized reviewer records the decision.

## 5. Screens

| Screen | Main content | Mockup |
| --- | --- | --- |
| Overview | Key metrics, forecast thumbnail, priority actions | [Image](images/01-overview.png) |
| Records | Imports, account freshness, reconciliation, review queue | [Image](images/02-records.png) |
| Financial Health | Revenue, expenses, margin, customer dues | [Image](images/03-financial-health.png) |
| Cash Outlook | Forecast paths, reserve line, upcoming cash movements | [Image](images/04-cash-outlook.png) |
| Payment Review | Proposed payment, four scenarios, evidence blocker, rescue options | [Image](images/05-payment-review.png) |
| Funding Matches | Ranked illustrative products, repayment effect, readiness, passport | [Image](images/06-funding-matches.png) |
| Fraud Monitor | Flagged transactions, comparison evidence, review actions | [Image](images/07-fraud-monitor.png) |
| Approvals & Audit | Tiered approvals, selective sharing, event history | [Image](images/08-approvals-and-audit.png) |

## 6. Acceptance criteria

1. A user can import a synthetic supported file, see rejected rows, review a duplicate candidate, and commit accepted records without double posting on retry.
2. Financial totals and cash paths match an independent reference calculation on frozen examples; each key figure has a source reference or an explicit unavailable-evidence state.
3. A known reserve breach is shown as a breach. Missing critical data cannot produce a positive payment result.
4. Financing matches display eligibility reasons, illustrative terms, total repayment, and the resulting lowest cash balance. They are never labelled as offers or approvals unless verified by a lender.
5. A suspicious proposed transaction can be held and reviewed; the system records both the rule and the reviewer’s reason.
6. Staff cannot approve owner actions. A user from one business cannot access another business’s records through a changed ID or export request.
7. The end-to-end simulated journey produces timestamped operation logs without raw account numbers or sensitive document contents.

## 7. Success measures to report, not assume

Report task completion per demo task, exact financial fixture results, false positive and missed fraud flags in a labelled test set, blocked unauthorized actions, time to a correct decision, and owner understanding in any later pilot. Do not present synthetic demonstrations as customer outcomes or promise lower defaults.

## 8. Scope boundaries

The competition build uses synthetic records, an illustrative financing catalogue, and mock bank/lender services. It does not issue loans, decide credit, certify fraud, or move real money. Real financial data and real integrations require permission, jurisdiction-specific review, source-access validation, stronger operational testing, and a separate release decision.
