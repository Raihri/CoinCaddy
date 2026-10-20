# CoinCaddy: Security Self-Assessment Report


## Executive summary

This report distinguishes controls implemented and tested in the current synthetic-data prototype from controls that remain unverified, incomplete, or required before production use. Existing implemented controls are retained; remediation needs are recorded as actions rather than represented as completed fixes.

The recorded evidence states that the backend suite passed 514 tests and the frontend production build passed on 20 October 2026. These results demonstrate only the application behaviors covered by those tests and the build. They are not evidence of an independent security audit, penetration test, dependency vulnerability scan, production configuration review, or real-world fraud validation.

The application is a synthetic-data prototype. Financial figures and matching results are decision support, not audited accounts, lender approvals, or proof of fraud. The action executor must remain connected only to a mock service. Do not use real customer or financial data, live payment providers, or live lender integrations until the release gates in this report have been independently verified.

Release posture: conditionally suitable for a synthetic-data competition demonstration after verifying the deployment configuration and mock-service boundary. Not assessed as production-ready for real financial data or live financial actions.

## 1. Scope and evidence

| Evidence | Result / limitation |
| --- | --- |
| Identity and policy modules | The original assessment reports review of role actions, membership-derived business scope, session creation/revocation, cookie behavior, and authorization denials. This is a project-team review, not an independent audit. |
| Approvals and executor modules | The original assessment reports review of proposal hashing, tier gates, owner decision/release checks, sandbox adapters, idempotency, and redaction. |
| Records import modules | The original assessment reports review of accepted file types, parser bounds, formula/macro/encryption rejection, and normalization checks. Malware scanning is not evidenced. |
| Audit modules | The original assessment reports review of redaction, append behavior, SHA-256 chain construction, and verification. The chain is not independently immutable. |
| Finance Chat modules | The original assessment reports review of read-only tools, per-feature authorization, untrusted-record handling, model-drafter validation, and absence of write tools. |
| Automated tests | 514 backend tests passed according to the project team's local run; 2,107 dependency deprecation warnings were reported. Test coverage and independent validation are not established by the pass count alone. |
| Frontend | Production build passed on 20 October 2026. A successful frontend build is not a security test. |
| Not reviewed independently | No production deployment configuration, independent penetration test, dependency vulnerability report, production security-header review, database network-rule review, backup-restore test, or service-permission audit was supplied. |

No production secrets or customer data were reviewed. Findings below are based on the supplied self-assessment and the limitations it states; this revision does not claim that source code or a live deployment has been independently retested.

## 2. Assets, actors, and trust boundaries

Assets include imported statements and source rows; balances and restrictions; invoices, bills and customer contacts; payee destination fingerprints; financing profile and selected passport fields; sessions; calculation snapshots; proposals and approvals; audit and sandbox logs; and deployment secrets.

Actors include owner, bookkeeper, staff, unauthenticated visitor, malicious or compromised user, malicious uploader, fraudulent or lookalike payee, database/service administrator, and the simulated mock-service boundary.

Trust boundaries: (1) browser/uploaded file to authenticated API and staging; (2) staging to reviewed/postable ledger; (3) business-scoped records to deterministic calculation and saved snapshot; (4) untrusted document text to safe display/read-only chat tools; (5) proposed action to owner approval/release and mock-only executor; (6) application process to PostgreSQL, private file storage, and configured mock-service URL.

## 3. Permission-tier implementation

The application reportedly enforces named actions through require(action) and ROLE_MATRIX. T0–T3 are reporting labels, not a separate runtime authorization mechanism. Unknown actions and unlisted roles are denied. Each route's declared action remains the authoritative permission check.

| Role | Read access | Allowed writes / decisions |
| --- | --- | --- |
| Owner | Business records, financial reports, forecasts, obligations, alerts, funding and fraud workflows, approvals, audit, and own chat history, subject to each feature action. | Business settings and membership; import review/post; cash anchors/coverage/restrictions/transfers; classifications and obligations; calculations/matches; documents and passport; approve/reject/release actions; execute approved sandbox proposals; manage contacts/destinations and region pack; record conditional-funding decisions. |
| Bookkeeper | Most finance and operational screens, including records, reports, payment/funding analysis, fraud review, approval inbox, passport/document read access, and audit, subject to feature action. | Import review/post; cash and obligation management; classification and calculations; prepare/propose actions; manage contacts/destinations; permitted fraud review and business workflows. Cannot change membership/settings, approve/reject/release proposals, execute sandbox actions, write/void owner-only documents/passports, verify customer contacts, or choose a region pack. |
| Staff | Business profile, memberships list, records, obligations, alerts, region pack, and read-only chat; underlying feature reads still enforce their own action. | Upload/stage import rows only. Cannot post/review imports, edit ledger classifications, manage cash or obligations, access payment/funding decision reports, manage fraud/approvals, or execute/share. |

| Tier | Meaning | Reported server-side gate |
| --- | --- | --- |
| T0 — read and analysis | Business-scoped viewing and stored calculation results. | Authenticated session, current membership, and feature-specific read action. Chat checks chat.ask and the selected tool's read action. |
| T1 — draft or propose | Planning, reminder drafts, and exact action proposals. Proposing does not send, pay, share, or apply. | Owner/bookkeeper for approval proposals; selected owner-controlled objects remain owner-only. Proposals are business-scoped, validated, hashed, and expire. |
| T2 — lower-impact sandbox send | Invoice reminder. | Owner approval of exact proposal, current verified customer contact, valid invoice state, payload hash and expiry; owner-only release/execution. No second reviewer is required under the current T2 policy. |
| T3 — sensitive sandbox action | Supplier payment, financing application submission, and Funding Passport share. | Owner approval of exact proposal; password re-authentication; recorded verification method/reference/date; distinct second reviewer; expiry and payload-hash checks; owner-only release/execution; executor rechecks before mock call. |

T2 and T3 do not authorize a live external action. The four adapters reportedly use MOCK_SERVICES_URL for reminder, payment, application, and packet-share sandbox endpoints. However, deployment-time endpoint configuration has not been independently verified; the mock-only boundary is therefore a release gate, not a proven production guarantee.

## 4. Security control assessment

| Control | Current status | Evidence and limitation / required follow-up |
| --- | --- | --- |
| Session authentication | Implemented; tests reported passing | Random session tokens, SHA-256 token hash in DB, eight-hour expiry, logout revocation, HttpOnly/SameSite=Strict cookie, Secure outside development. No MFA at sign-in. Add MFA for privileged accounts, login throttling, suspicious-login monitoring, and tested session/device revocation before real-data use. |
| Business/object authorization | Implemented at route/application layer; tests reported passing | Membership-derived BusinessContext, required action dependencies, scoped queries, 404 for missing business, 403 for role denial. No PostgreSQL RLS. Conduct a complete tenant-query review and cross-tenant tests across reads, writes, exports, nested resources, chat, and background tasks; evaluate RLS as defense in depth. |
| Role policy | Implemented; tests reported passing | Explicit deny-by-default action matrix. UI role-aware behavior is supplemental. Preserve server-side checks and test each sensitive endpoint against every role. |
| Import safety | Structural controls implemented; malware scanning not evidenced | CSV/XLSX only, byte/row/column/cell/archive bounds, formula/macro/encrypted/legacy workbook rejection, normalization and staged review. Add malware scanning, parser isolation, patched dependencies, upload monitoring, and hostile-file tests before real statements are accepted. |
| Data integrity and snapshots | Implemented; tests reported passing | Integer minor-unit calculations, explicit currency, source lineage, versioned frozen inputs and SHA-256 input digest, idempotent patterns. Hashing is not a digital signature and does not prove upstream records are genuine. |
| T2/T3 approval workflow | Implemented in reported tests; policy limitation remains | Exact payload hash, expiry, owner-only approval/release, T3 password re-authentication, verification details, proposer/approver separation. T2 has no second reviewer under current policy; assess whether this is acceptable for the intended demo and require stronger approval controls for real external actions. |
| Sandbox boundary | Adapter design is mock-oriented; deployment safety unverified | Fixed adapter paths reportedly use MOCK_SERVICES_URL and contain no external provider credentials. Before every deployment, enforce an allowlist of mock hosts/paths, fail closed on invalid configuration, restrict network egress, and test that no workflow can reach a live provider. |
| Fraud monitoring | Deterministic rules implemented; effectiveness unvalidated | Flags and evidence are reviewable; flags are not a fraud verdict. Evaluate thresholds, false-positive/negative rates, representative data, adversarial cases, and human-review procedures before making effectiveness claims. |
| Finance Chat | Read-only design reported; privacy assessment incomplete | One read-only tool at a time, per-tool authorization, no write/approval tools, cleaned record text and instruction-like text detection, validated output and template fallback. Keep template mode for public demos unless provider processing, retention, residency, access, and user notice are reviewed. |
| Audit logging | Hash-linked audit implemented; independent tamper resistance absent | Redacted fields, SHA-256 chained events, transactional mutation logging and verifier are reported. A privileged DB operator could rewrite the database and recompute the chain. Export checkpoints/events to independently controlled immutable storage and alert on verification failures. |
| Secrets, encryption, backups, retention | Partially assessed; production state unverified | Environment-based settings and local placeholders are reported. Verify production secret handling/rotation, encryption-at-rest configuration, least-privilege infrastructure access, retention, backup restoration, and database network rules. |
| Independent security testing | Not performed / evidence not supplied | No independent penetration test, independent code review, dependency vulnerability report, or production threat simulation is attached. Commission these before production use. |

Severity and release priority are recommendations in this revised report, not evidence that a vulnerability has been exploited. A missing control is described as missing or unverified; it is not represented as fixed.

## 5. Risk register and required actions

| ID / priority | Risk and impact | Existing mitigation reported | Required action / exit criterion |
| --- | --- | --- | --- |
| R1 — P0 release gate | Runtime configuration could differ from repository defaults, including mock URL, demo quick login, TLS/cookies, and CORS. Misconfiguration could expose demo accounts or route actions incorrectly. | Quick login reportedly defaults off and selects synthetic users when enabled; adapters are designed around a mock boundary. | Validate deployment variables; allowlist mock hosts and paths; fail closed; restrict network egress; verify TLS, CORS, security headers, and synthetic-only login. Evidence: configuration review and negative tests showing live endpoints cannot be reached. |
| R2 — P0 release gate | Tenant isolation relies on application-scoped queries without PostgreSQL RLS; an unscoped query could expose another business's records. | Membership-derived context, required action dependencies, scoped queries, and cross-business tests are reported. | Review every tenant-owned query, export, nested resource, chat tool, and background task. Add cross-tenant read/write tests and consider RLS. Exit criterion: documented review and passing negative tests across all access paths. |
| R3 — P1 | No MFA at sign-in; stolen password or session may enable sensitive sandbox approvals. | Eight-hour sessions, logout revocation, owner-only decisions, re-authentication and second reviewer for T3 are reported. | Add MFA for owners/privileged users, login rate limiting, suspicious-login monitoring, session/device revocation, and password-change invalidation. Test account-compromise recovery. |
| R4 — P1 | Audit chain stored in the same database can be rewritten by a privileged DB operator. | Serialized insertion and verifier reportedly detect accidental or partial tampering. | Export signed checkpoints/events to separately controlled immutable storage; restrict administration; alert on verification failures. Test deletion, alteration, and reordering scenarios. |
| R5 — P1 | Incomplete, delayed, misstated, or incorrectly entered financial records may produce incorrect cash or eligibility guidance. | Coverage gaps, stale/missing balance states, source links, scenarios, snapshots, and caveats are reported. | Require owner review; validate against independent statements; document data freshness and assumptions. Do not market outputs as audited accounts, credit decisions, or guarantees. |
| R6 — P1 | Heuristic fraud rules and payee matching can create false positives or miss new fraud. | Explainable flags, manual review/clear/defer, and proposal holds are reported. | Test representative and adversarial cases; measure false-positive/negative rates; establish review thresholds and escalation. Do not claim fraud certainty. |
| R7 — P1 | No malware scanning is evidenced for uploaded files; parser or storage weaknesses may be exploited. | Format restrictions, file/archive limits, formula/macro rejection, staged review, and private storage are reported. | Add malware scanning, isolated parsing, dependency patching, and upload monitoring. Test malformed and resource-exhaustion inputs before real statements are accepted. |
| R8 — P1 before real-data AI use | Optional model calls may send user questions and derived facts to a third-party provider. | Template mode is default; narrow facts, read-only tools, output validation, and fallback are reported. | Keep template mode for public demo. Before enabling real data, document provider, purpose, data fields, retention, residency, access, contractual terms, and user notice. |
| R9 — P1 | Dependency deprecation warnings exist; no vulnerability scan was run. | Pinned top-level frontend packages and Python requirements file are reported. | Run dependency vulnerability and license scans, prioritize exploitable findings, patch packages, and resolve deprecations on a schedule. Deprecation warnings alone are not proof of vulnerabilities. |
| R10 — P1 | No independent penetration test, backup-restore exercise, or formal incident-response evidence was reviewed. | Automated tests and health endpoints are reported. | Commission independent testing; test backup restoration and recovery objectives; document incident contacts, credential rotation, containment, and notification steps. |
| R11 — P1 before production | Production secrets, encryption, network rules, retention, and service permissions remain unverified. | Environment-based configuration is reported. | Review secret rotation, TLS, encryption at rest, database/service least privilege, private storage, retention/deletion, backup access, and production logs. Record evidence and sign-off. |
| R12 — P2 / policy decision | T2 reminder release does not require a second reviewer under the current policy. | Exact-proposal approval and owner-only release/execution are reported. | Document why single-reviewer release is proportionate for a mock reminder. Reassess before any real messaging; require additional review where the impact or abuse risk warrants it. |

P0 means a release-blocking gate for the stated deployment. P1 means high-priority remediation before real financial data or live integrations. P2 means a policy decision or hardening task. These labels are proposed by this revision.

## 6. Validation and release checklist

The following existing local validation commands were recorded in the original report. Run them from the project root and retain the output with the release evidence:

```bash
cd backend
.venv/bin/python -m pytest -q --tb=short
.venv/bin/python -m app.modules.audit.verify
cd ../frontend
npm run build
```

The audit verification command requires the intended database configured in .env. The commands above are not a substitute for the additional release checks below.

- Verify production configuration, including mock-service URL, demo quick-login, TLS, CORS, security headers, secrets, and network egress.
- Test owner, bookkeeper, and staff permissions at the API boundary; do not rely on frontend hiding.
- Test cross-business IDs and every tenant-owned read/write/export/background path; ensure other-business object existence is not disclosed.
- Test T3 failure without password re-authentication, verification details, and a distinct second reviewer.
- Test invalid/hostile mock-service URLs and demonstrate that the executor cannot reach a live provider.
- Test malicious and malformed uploads; add malware scanning and isolated parsing before real files are accepted.
- Run dependency vulnerability and license scans; review and triage findings.
- Test audit alteration/deletion detection and independently preserved audit checkpoints.
- Perform backup restoration and document incident response and credential rotation.
- Obtain independent penetration-test results and record remediation of findings before production approval.

Never test action workflows against a real payment, lender, or customer messaging endpoint. Execute only against the approved mock service.

## 7. Release posture

Current assessment: conditionally suitable for a synthetic-data competition demonstration, provided the deployment configuration and mock-only boundary are verified before the demo. The project team's reported tests and build are useful evidence of covered behavior but do not establish production security.

Not assessed as production-ready for real financial data or real payment execution. Before real business records or live providers are introduced, require production configuration review, independent security testing, dependency scanning, MFA, upload malware scanning, tenant-isolation review, independent audit preservation, verified backup/retention controls, and a redesigned and independently tested live-action authorization boundary.

Do not describe controls listed as required actions as completed. Update the status and attach evidence only after each action has actually been implemented and verified.

## 8. Assessment record

Prepared from: project-team self-assessment supplied for review, including its reported source review, tests, and local build on 20 October 2026.

Automated evidence reported by the project team: backend 514 tests passed; frontend production build passed; 2,107 dependency deprecation warnings.

Reviewer: project team self-assessment; revised wording and prioritization prepared as a documentation revision, not an independent technical audit.

Approval/sign-off: pending team review. Production release approval: not granted by this report.
