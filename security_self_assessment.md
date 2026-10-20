# LedgerLift — Security Self-Assessment Report

**Version:** 2.0 · **Assessment date:** 20 October 2026
**System:** SME Finance Copilot / LedgerLift prototype, Topic E
**Assessment type:** Source review and local automated verification; self-assessment, not independent certification.

## Executive summary

The application implements business-scoped sessions, server-side action permissions, bounded CSV/XLSX import, frozen calculation snapshots, a proposal/approval/release workflow, sandbox-only action adapters, redacted audit events, and a hash-linked audit chain. Permission checks live in the FastAPI layer and are covered by automated tests. Owners control high-impact actions; staff are limited to selected read and staging functions.

On 20 October 2026, the backend suite passed **514 tests** and the frontend production build passed. This demonstrates the covered application behaviors only. No independent penetration test, external security audit, dependency vulnerability scan, production configuration audit, or real-world fraud validation is claimed.

The app is a synthetic-data prototype. Financial figures and matching results are decision support, not audited accounts, lender approvals, or proof of fraud. The action executor is designed for a mock service; do not configure it with a real provider.

## 1. Scope and evidence

| Evidence | Result / limitation |
| --- | --- |
| `backend/app/modules/identity/policy.py`, `identity/deps.py`, and `identity/router.py` | Reviewed for role actions, membership-derived business scope, session creation and revocation, cookie behavior, and authorization denials. |
| `backend/app/modules/approvals/` and `executor/` | Reviewed proposal hashing, tier gates, owner decision/release checks, sandbox adapters, idempotency handling, and redaction. |
| `backend/app/modules/records/files.py` and `records/normalize.py` | Reviewed accepted file types, parser bounds, formula/macro/encryption rejection, and normalization checks. |
| `backend/app/modules/audit/` | Reviewed redaction, append behavior, SHA-256 chain construction and verification. |
| `backend/app/modules/chat/` | Reviewed read-only tool set, per-feature authorization, untrusted-record handling, model-drafter validation, and absence of write tools. |
| `backend/tests/` | Local run: **514 passed**; 2,107 dependency deprecation warnings. Relevant suites include `test_authorization.py`, `test_records_import.py`, `test_approval_workflow.py`, `test_sandbox_executor.py`, `test_fraud_monitor.py`, `test_audit.py`, `test_finance_chat.py`, and `test_funding_passport.py`. |
| `frontend/` | `npm run build` passed on 20 October 2026. A frontend build is not a security test. |

No production secrets or customer data were reviewed. A live deployment configuration and its current security headers, database network rules, backups, log access, and service permissions were not independently inspected in this assessment.

## 2. Assets, actors, and trust boundaries

**Assets:** imported statements and source rows; account balances and restrictions; invoices, bills and customer contacts; payee destination fingerprints; financing profile and selected passport fields; sessions; calculation snapshots; proposals and approvals; audit and sandbox logs; deployment secrets.

**Actors:** owner, bookkeeper, staff, unauthenticated visitor, malicious/compromised user, malicious uploader, fraudulent or lookalike payee, database/service administrator, and the simulated mock-service boundary.

**Trust boundaries:**

1. Browser and uploaded file → authenticated API and staging.
2. Staging → reviewed/postable ledger.
3. Business-scoped records → deterministic calculation and saved snapshot.
4. Untrusted document text → safe display/read-only chat tools.
5. Proposed action → owner approval and release → mock-only executor.
6. Application process → PostgreSQL, private file storage, and configured mock-service URL.

## 3. Permission-tier implementation

This application enforces named **actions** through `require(action)` and `ROLE_MATRIX`; the following T0–T3 labels are a reporting summary, not an alternate runtime authorization mechanism. Unknown actions and unlisted roles are denied. Each route’s action remains the authoritative permission check.

### 3.1 Roles

| Role | Read access | Allowed writes / decisions |
| --- | --- | --- |
| **Owner** | Business records, financial reports, forecasts, obligations, alerts, funding and fraud workflows, approvals, audit, and own chat history, subject to each feature action. | Business settings and membership; import review/post; cash anchors/coverage/restrictions/transfers; classifications and obligations; run calculations/matches; maintain documents and passport; approve/reject/release actions; execute approved sandbox proposals; manage contacts/destinations and region pack; record conditional-funding decisions. |
| **Bookkeeper** | Most finance and operational screens, including records, reports, payment/funding analysis, fraud review, approval inbox, passport/document read access, and audit, subject to feature action. | Import review/post; cash and obligation management; classification and calculations; prepare/propose actions; manage contacts and destinations; perform permitted fraud review and business workflows. Cannot change membership/settings, approve/reject/release proposals, execute sandbox actions, write/void owner-only documents/passports, verify customer contacts, or choose a region pack. |
| **Staff** | Business profile, memberships list, records, obligations, alerts, region pack, and read-only chat. Underlying feature reads still enforce their own action. | Upload/stage import rows only. Staff cannot post/review imports, edit ledger classifications, manage cash or obligations, access payment/funding decision reports, manage fraud/approvals, or execute/share. |

### 3.2 Runtime tier summary

| Tier | Implemented meaning | Server-side gate |
| --- | --- | --- |
| **T0 — read and analysis** | Business-scoped viewing and reading stored calculation results. Some read permissions are intentionally unavailable to staff (for example Payment Review, Funding Matches, fraud cases, and audit). | Authenticated session, current membership, and the endpoint's feature-specific read action. Chat checks both `chat.ask` and the selected tool's own read action. |
| **T1 — draft or propose** | Internal planning, reminder drafts, and exact action proposals. Proposing does not send, pay, share, or apply. | Owner/bookkeeper for approval proposals; only the owner can create selected owner-controlled objects (including Funding Passport and conditional-funding backup decisions). Proposals are business-scoped, validated, hashed, and expire. |
| **T2 — lower-impact sandbox send** | Invoice reminder. | Owner approval of the exact proposal, current verified customer contact, valid invoice state, payload hash and expiry; owner-only release/execution. No second reviewer is required by the current T2 policy. |
| **T3 — sensitive sandbox action** | Supplier payment, financing application submission, and Funding Passport share. | Owner approval of exact proposal; password re-authentication; recorded verification method/reference/date; a second reviewer (proposer cannot approve); expiry and payload-hash checks; owner-only release/execution; executor rechecks before mock call. |

`T2` and `T3` do not authorize a live external action. The four adapters in `backend/app/modules/executor/adapters.py` use the configured `MOCK_SERVICES_URL`; current adapters are reminder, payment, application, and packet-share sandbox endpoints.

### 3.3 Key action matrix (runtime names)

| Capability | Owner | Bookkeeper | Staff |
| --- | :---: | :---: | :---: |
| `records.read`, `import.stage`, `obligations.read`, `alerts.read`, `chat.ask`, `chat.read`, `regionpack.read` | Yes | Yes | Yes |
| `import.review`, `import.commit`, `cash.manage`, `obligations.write`, `ledger.classify`, `outlook.run`, `alerts.run` | Yes | Yes | No |
| Payment Review, Funding Matches, Rescue, Readiness run/read | Yes | Yes | No |
| `membership.manage`, `business.settings.update`, `documents.write`, `passport.write`, `approvals.decide`, `approvals.release`, `executor.run`, `contact.verify`, `conditional.write`, `conditional.review`, `regionpack.select` | Yes | No | No |
| `approvals.propose`, fraud read/run/review, payee-destination write, executor read, Guardian draft/contact write, conditional read/evaluate, audit read | Yes | Yes | No |

The full source of truth is `backend/app/modules/identity/policy.py`; route dependencies declare which action each endpoint requires.

## 4. Security control assessment

| Control | Status | Evidence and limits |
| --- | --- | --- |
| Session authentication | **Implemented; tests pass** | Random session tokens, SHA-256 token hash in DB, eight-hour expiry, logout revocation, HttpOnly/SameSite=Strict cookie, Secure outside development. No MFA for sign-in. |
| Business/object authorization | **Implemented; tests pass** | Membership is looked up from session user; route action and business scope are server-derived; missing business returns 404; role denial returns 403 and is audited. No database RLS layer. |
| Role policy | **Implemented; tests pass** | Explicit deny-by-default action matrix. UI role-aware behavior is supplemental only. |
| Import safety | **Implemented; tests pass** | CSV/XLSX only; byte, row, column, cell and expanded archive bounds; formulas/macros/encrypted/legacy workbook rejection; normalization and staged review. No malware scanning is evidenced. |
| Data integrity and snapshots | **Implemented; tests pass** | Integer minor-unit calculations; explicit currency; source lineage; versioned frozen inputs and SHA-256 input digest for saved calculations; idempotent import/action patterns. Hashing is not a digital signature or proof that upstream records are genuine. |
| T2/T3 approval workflow | **Implemented; tests pass** | Exact payload hash, expiry, owner-only approval/release, T3 password re-auth, documented verification and proposer/approver separation. No second-person approval exists for T2. |
| Sandbox boundary | **Implemented by adapter design; tests pass** | Fixed adapter paths use `MOCK_SERVICES_URL`, no external provider credentials in adapters, redacted output, request idempotency. Runtime endpoint configuration must still be checked in each deployment. |
| Fraud monitoring | **Implemented as deterministic rules; tests pass** | Flags and evidence are reviewable; flags are not a fraud verdict. Detection coverage, thresholds, false-positive/negative rates, and adversarial robustness are not independently validated. |
| Finance Chat | **Implemented; tests pass** | One read-only tool at a time; per-tool authorization; no write/approval tools; cleaned record text and instruction-like text detection; model output constrained to cited fact placeholders with template fallback. Optional model use adds a third-party processing consideration. |
| Audit logging | **Implemented; tests pass** | Redacted fields and SHA-256 chained events, transactionally recorded with successful mutation; verification command detects chain mismatch. A DB administrator can rewrite the database and recompute the chain; external immutable storage/alerting is not implemented. |
| Secrets, encryption, backups, retention | **Partially assessed / deployment-dependent** | Runtime settings are environment-based and include local placeholders. This review did not verify production secret rotation, encryption-at-rest configuration, backup restore, retention schedule, or least-privilege infrastructure access. |
| Independent security testing | **Not performed** | No penetration test, independent code review, dependency-vulnerability report, or production threat simulation is attached. |

## 5. Known-risk register

| ID | Risk | Impact | Current mitigation | Remaining action |
| --- | --- | --- | --- | --- |
| R1 | Production runtime configuration can differ from repository defaults; especially the mock-service URL, demo quick-login setting, cookie/TLS configuration, and CORS origins. | A misconfigured demo could expose accounts or route sandbox traffic incorrectly. | Quick login defaults off and, when enabled, only selects users marked synthetic; adapters are designed around a mock-service boundary. | Before a public demo, verify production env values and service URLs; keep quick login restricted to synthetic identities; check TLS, CORS, and security headers. |
| R2 | Tenant isolation relies on route-level business-scoped SQL; there is no PostgreSQL RLS defense in depth. | A future unscoped query could expose another business's records. | Membership-derived `BusinessContext`, required action dependencies, scoped queries, and cross-business authorization tests. | Add independent multi-tenant query review and consider RLS after connection-role/context design is tested. |
| R3 | No MFA at sign-in; T3 re-authentication uses the account password. | A stolen active session/password may enable sensitive sandbox approvals. | Eight-hour sessions, logout revocation, owner-only decisions, re-authentication, second reviewer, and payload binding. | Add MFA for real deployment; review session/device revocation and login rate limiting. |
| R4 | SHA-256 hash-chained audit data resides in the same database as the application. | A privileged database operator can rewrite rows and recompute later hashes. | Serialized event insertion and a verifier detect accidental or partial tampering. | Export audit checkpoints to a separate access-controlled immutable store and alert on verification failure. |
| R5 | Financial records and forecast assumptions may be incomplete, delayed, misstated, or entered incorrectly. | Incorrect cash or eligibility guidance. | Coverage gaps, stale/missing balance states, source links, scenarios, calculation snapshots, and explicit caveats. | Keep owner review mandatory; validate against independent statements; do not market outputs as accounting, credit decisions, or guarantees. |
| R6 | Fraud rules are heuristics; payee-name matching and payment history can produce both false positives and missed fraud. | Genuine payments may be delayed; novel fraud may not be flagged. | Evidence-based flags, explainable rules, manual review/clear/defer, and proposal holds where applicable. | Test with representative data and adversarial cases; establish operating thresholds and reviewer process; avoid claiming fraud certainty. |
| R7 | Uploaded files are not shown to undergo malware/antivirus scanning; only structural/content checks are implemented. | A malicious file could exploit a parser or storage/runtime weakness despite current rejection checks. | Strict format support, file/zip limits, formula/macro rejection, isolated staging, private storage. | Add malware scanning and production upload monitoring before handling real statements; keep parser dependencies patched. |
| R8 | Optional Finance Chat model calls may send question and derived facts to the configured model provider. | Privacy, retention, residency, and provider dependency risk if enabled with real data. | Template mode is default; narrow calculated facts, untrusted record text is not passed as instructions, draft validation and fallback. | Keep template mode for public demo or document provider, data-processing terms, residency, retention and user notice before enabling on real data. |
| R9 | Dependency deprecation warnings indicate future compatibility work; no vulnerability scan was run. | Future package changes may introduce incompatibilities or known vulnerabilities. | Pinned top-level frontend packages and a Python requirements file; tests/build pass. | Run dependency vulnerability and license checks, update dependencies, and resolve deprecations on a scheduled cadence. |
| R10 | No independent penetration test, production backup-restore exercise, or formal incident response evidence was reviewed. | Unknown defects or slow recovery after compromise/outage. | Automated application tests and health endpoints. | Conduct pre-production review, test backup restore and recovery, define incident contacts and credential rotation steps. |

## 6. Security validation instructions

Run from the project root:

```bash
cd backend
.venv/bin/python -m pytest -q --tb=short
.venv/bin/python -m app.modules.audit.verify
cd ../frontend
npm run build
```

The audit verification step requires the intended database configured in `.env`. For role testing, sign in as synthetic owner, bookkeeper, and staff users; inspect the server response for permitted actions, then try the same write endpoint as a role that lacks that action. Expected behavior is a server-side 403 and a denial event in audit history. Also test cross-business IDs: the API should not reveal whether another business's object exists.

For action workflow testing, propose a sandbox action as owner/bookkeeper, verify that the exact proposal payload/hash is displayed, and confirm the owner-only gates. T3 must fail without password re-authentication, verification details, or a distinct second reviewer. Release and execute only against the mock service, then inspect redacted execution and audit logs. Never test against a real payment, lender, or customer messaging endpoint.

## 7. Release posture

**Assessment result: suitable for a synthetic-data competition demonstration with the stated controls and caveats. Not assessed as production-ready for real financial data or real payment execution.**

Before using real business records or integrating a live provider, require a production configuration review, independent security assessment, dependency scan, MFA, upload malware scanning, verified data-retention and backup controls, tenant-isolation review, and a redesigned/independently tested live-action authorization boundary. The current executor must remain pointed at the mock service.

## 8. Assessment record

**Prepared from:** repository source, tests, and local build on 20 October 2026.
**Automated evidence:** backend 514 passed; frontend production build passed.
**Reviewer:** project team self-assessment.
**Approval/sign-off:** pending team review before competition submission.
