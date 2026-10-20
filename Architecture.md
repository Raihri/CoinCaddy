# CoinCaddy Proposed Architecture

## 1. System flow

Owner or bookkeeper → authenticated API → import staging and review → accepted ledger and obligations → immutable calculation snapshot → analysis, forecast, payment review, fraud rules, and financing match → proposal and permission check → owner approval → sandbox action → audit event.

The AI assistant reads approved structured results and can create drafts. It cannot write ledger rows, change reserves, bypass a hold, submit applications, or hold payment credentials. Every externally visible action passes the API policy service independently of the AI.

## 2. Modules and ownership

| Module | Owns | Boundary |
| --- | --- | --- |
| Identity and policy | Users, memberships, roles, action tiers, approvals | Determines business scope from authenticated membership; denies by default. |
| Ingestion | Files, parser versions, staged rows, mapping, duplicate candidates | No staged row affects official cash until reviewed posting commits. |
| Ledger and reconciliation | Accounts, canonical transactions, balance anchors, transfers, coverage | Maintains account- and currency-specific usable balances. |
| Obligations | Receivables, payables, allocation and reversal events | Computes outstanding amounts without mutating original obligations. |
| Analysis | Financial totals, trend summaries, evidence links | Reads only accepted records. |
| Cash planning | Frozen snapshots, 7/30-day and 13-week paths, scenario results | Executes deterministic arithmetic. |
| Decisions | Payment checks, rescue options, conditional funding triggers | Produces proposals, not real-world actions. |
| Matching | Versioned financing catalogue, eligibility, total cost, repayment paths | Distinguishes sample products, potential matches, and verified offers. |
| Fraud monitor | Rule hits, review cases, holds, reviewer outcomes | A flag is suspected activity, not a declaration of fraud. |
| Evidence and audit | Source links, versioned reports, approval and action events | Keeps event history and clearly marks deleted or unavailable evidence. |
| Agent adapter | Read-only query tools and drafts | Explains calculated results; no direct database writes or credentials. |

The first Financial Health screen is a **cash-basis operating summary**, not an audited profit-and-loss statement. Categorize settled operating receipts and operating payments once; keep unpaid obligations separate and exclude own-account transfers and financing proceeds from revenue. If a later product adds accrual accounting, it needs a separate accounting contract and tests.

## 3. Data model

Each financial table has a business_id. Composite business-scoped foreign keys prevent a record belonging to one business from referencing another business’s account. Important entities:

- Business, membership, role, policy version
- Cash account, source registry, balance anchor and coverage interval
- Import batch, raw source row, normalized row, review decision
- Canonical cash transaction, transfer group, correction/reversal
- Party, receivable/payable obligation, allocation
- Snapshot, forecast run, scenario, payment check and saved review report
- Financing product version, match result, conditional funding trigger
- Fraud case, action proposal, approval, sandbox execution and audit event

Store money as signed integer minor units with an explicit currency code. Store the business date separately from UTC event timestamps. Use an immutable source reference and parser/mapping version for every accepted transaction. A file hash detects repeated uploads but does not prove a document is authentic.

## 4. Import and financial correctness

1. Register the source, account, currency, supported period, and upload permission.
2. Quarantine the file. Enforce request, file, row, and decompression limits; inspect content type rather than trusting the extension. Reject executable formulas, macros, encrypted workbooks, and ambiguous date or amount formats.
3. Parse into staged rows and show mapping, errors, overlap, and possible duplicates. Identical-looking legitimate purchases remain separate until identity is established.
4. An authorized user accepts a selected manifest. In one database transaction, lock the relevant batch/account scope, validate revision and retry key, post canonical movements and lineage, write audit/outbox events, and mark the batch posted.
5. Reconcile an account period: opening anchor + accepted signed movements − closing anchor must match under an explicit tolerance policy. A zero residual does not establish complete source coverage or current cash.

An account without a usable anchor cannot assert an absolute current balance. A stale statement, restricted funds, missing source, or unmatched transfer is displayed explicitly. Cash in a different account cannot support a payment without a feasible dated transfer. Forecast only outstanding obligations; when a proposed payment refers to an existing payable, replace that projected event instead of counting both.

## 5. Calculation services

### Cash outlook

For each account, currency, scenario, and day:

Closing cash on day t = eligible opening cash + settled/expected inflows through t − required outflows through t.

Run an expected path and a cautious path for the owner dashboard. Store each input snapshot, business cut-off, event order, reserve schedule, calculation version, and output. A daily chart does not prove intraday availability; if timing is unknown, the payment check applies an outflows-before-inflows stress convention or returns insufficient timing evidence.

### Payment Review

Run four explicit paths on the same frozen snapshot: all on time, receipts delayed, largest eligible receipt missed, and owner-defined adverse case. For each path, report the first reserve breach date, minimum cash, shortfall, and causal inputs. The result has separate arithmetic, evidence, and permission states:

- Within reserve under stated assumptions
- Reserve shortfall
- More information needed

A missing critical funding balance, mandatory cost, or material disputed transaction blocks a positive result. Known breaches remain visible even if evidence is also incomplete.

### Evidence-Gated Cash Rescue Plan

Generate a small, reviewable set of options: collect an overdue invoice, move a discretionary cost only with permission, use one financing product, or combine them. Re-run the frozen forecast for each option. Compare minimum cash, total financing cost, cash needed by date, and unresolved dependencies. Do not calculate an automatic “safe amount” if the baseline already breaches or critical facts are unknown.

### Financing matching and conditional funding

Store versioned product terms: jurisdiction, currency, amount bounds, business age/revenue criteria, purpose, security requirements, funding time, fees, and repayment formula. First reject ineligible products with reasons. Next calculate their total cost and insert exact repayment events into the forecast. Rank by repayment fit, timing, cost, and readiness; keep the ranking explanation visible.

A conditional plan has states: awaiting receipt → receipt settled or trigger missed → forecast recalculated → owner reviews backup proposal → sandbox submission if approved. Only a verified settlement changes the first state. Retries use the same idempotency key.

### Transaction fraud rules

The first rules are duplicate payment candidates, repeated small payments near an approval limit, unusual amount relative to a payee’s history, changed destination details, and “paid” invoices without matched settlement. Each hit stores the compared record IDs and rule version. A material unresolved candidate creates two bounded cash paths when possible and holds a proposed sandbox payment. The reviewer can confirm, reject, or defer with a reason. The system does not silently delete or merge records.

## 6. Identity, privacy, and action policy

Roles: owner manages membership, reserves, sharing, and approvals; bookkeeper imports, reconciles, and proposes; staff submits records and drafts. Action tiers:

| Tier | Allowed result | Gate |
| --- | --- | --- |
| T0 | Read, analyse, forecast, compare | Authenticated and business-scoped |
| T1 | Draft reminder, plan, or application | Authorized role; draft stays inside system |
| T2 | Send reminder or other low-impact sandbox action | Explicit owner approval |
| T3 | New payee, destination change, application submission, sandbox payment | Owner re-authentication, documented verification, explicit approval; optional second reviewer under policy |

Use HTTPS; encrypted private storage and backups; short-lived scoped file links; secret management outside source code; data minimization; and redacted logs. PostgreSQL row-level security can add database enforcement to server-side authorization, provided the application uses a non-bypass role and sets verified business context per transaction. Test both API and worker/export paths. [PostgreSQL row-security documentation](https://www.postgresql.org/docs/16/ddl-rowsecurity.html) and [OWASP API authorization guidance](https://api-security.owasp.org/editions/2023/en/0x00-header/) describe the underlying controls.

Uploaded files and invoice text are untrusted. AI extraction, if used, runs without tools; the conversational assistant receives sanitized calculated outputs and narrow read-only tools. Tool arguments are validated by the server. Prompt instructions in a document never change the policy or calculations. [OWASP prompt-injection guidance](https://genai.owasp.org/llmrisk/llm01-prompt-injection/) describes this threat. Uploads follow [OWASP file-upload guidance](https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html).

The Funding Passport defaults to a preview. The owner chooses fields and recipient; the resulting packet is versioned, masked where possible, and shared only after consent. The prototype uses no live lender or payment credentials.

## 7. Proposed API surface

| API operation | Essential control |
| --- | --- |
| Create import batch / preview rows | Business membership, source/account scope, upload limits |
| Review and commit import batch | Role, expected revision, immutable selection manifest, retry key |
| Read cash position and analysis | Business scope, evidence and freshness metadata |
| Run forecast or Payment Review | Authorized snapshot, account/currency and scenario validation |
| List financing matches | Catalogue version, illustrative/verified status, no implicit sharing |
| Create fraud review decision | Reviewer role, reason, previous state revision |
| Create and approve proposal | Action tier, owner check, approval bound to exact proposal contents |
| Execute sandbox action | Re-check policy after approval, idempotency key, append audit result |
| Export Funding Passport or report | Owner consent, recipient and field selection, scoped download |

## 8. Verification and release gates

Use an independent calculator for frozen financial fixtures. Minimum adversarial cases: stale funding balance; cash in a second account that cannot transfer in time; duplicate-looking legitimate transactions; partial invoice allocation; same-day inflow/outflow with unknown ordering; delayed receipt causing a breach; double-post attempt on retry; concurrent allocation conflict.

Security tests must cover cross-business record and export access, staff calling an owner-only endpoint, a malicious invoice instruction, altered approval payload, suspicious transaction hold, and redacted logs. A passing test applies only to the tested scenario; the security self-assessment must record remaining risks and untested integrations.
