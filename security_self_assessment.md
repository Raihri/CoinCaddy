# SME Finance Copilot — Security Self-Assessment Report

**Version:** 1.0 · **Assessment date:** 20 October 2026 · **Assessment state:** Design review only  
**Scope:** The project folder's product/architecture/security plans and static mockups. No running application, source-code implementation, deployment, test results, or sandbox logs were present in the reviewed folder.  
**Overall conclusion:** The proposed permission tiers and controls are specified but **not implemented or verified**. This report is a truthful baseline for the competition submission and a checklist for collecting implementation evidence. It is not a penetration test or security certification.

## 1. Evidence reviewed

| ID | Evidence | What it establishes |
| --- | --- | --- |
| E1 | `prd.md` | Product journeys, acceptance criteria, and synthetic-data boundary |
| E2 | `architecture.md` | Proposed trust boundaries, algorithms, roles, and action tiers |
| E3 | `feature_security_matrix.md` | Feature-level proposed controls and innovation mapping |
| E4 | `phases.md` | Build gates and expected demo evidence |
| E5 | `built_so_far.md` and folder inventory | Design artifacts exist; implementation and tests are not evidenced |
| E6 | Static images in `images/` | Intended UI only; no proof of security behavior |

No pass/fail security test is claimed. “Planned” below means documented intent; “verified” requires running code plus recorded test output.

## 2. Assets, actors, and trust boundaries

**Assets:** transaction and balance records; invoices and bills; supplier bank details; customer contacts; financing inputs; approval decisions; selected Funding Passport fields; credentials/secrets; audit and sandbox operation logs.

**Actors:** owner, bookkeeper, staff member, unauthenticated visitor, malicious uploader/payee, compromised account, and mock external bank/lender service. Uploaded documents and invoice text are untrusted even when supplied by an authenticated user.

**Boundaries:** browser → authenticated API; file upload → quarantine/staging → accepted ledger; accepted records → deterministic calculation; AI explanation → read-only tools; proposal → owner approval → sandbox executor; API/worker/export → business-scoped database and storage.

## 3. Permission-tier specification and implementation status

Permission decisions must be enforced by server-side policy from authenticated membership. UI hiding and AI instructions are not access controls. Each read, write, worker task, and export must use the same verified business scope. [OWASP documents object- and function-level authorization risks](https://api-security.owasp.org/editions/2023/en/0x00-header/).

| Tier | Allowed operation | Required gate | Proposed roles | Implementation evidence |
| --- | --- | --- | --- | --- |
| T0 | Read own-business records; run analysis, forecast, and comparisons | Authenticated membership; business-scoped objects | Owner, bookkeeper; staff only for explicitly granted views | **None** |
| T1 | Create an internal draft reminder, plan, or application | Authorized role; draft remains internal; no external effect | Owner, bookkeeper; limited staff submissions if granted | **None** |
| T2 | Send a reminder or other low-impact sandbox action | Exact proposal; explicit owner approval; executor recheck | Owner approves; authorized user may propose | **None** |
| T3 | Add/change payee destination, submit mock application, initiate mock payment, or share sensitive packet | Exact proposal; owner re-authentication; documented independent verification; explicit approval; second reviewer when policy requires it | Owner approval, plus second reviewer if configured | **None** |

### Role matrix to implement

| Operation | Owner | Bookkeeper | Staff |
| --- | --- | --- | --- |
| Configure reserve, membership, and disclosure policy | Allow | Deny | Deny |
| Import and reconcile records | Allow | Allow | Submit/upload only, pending review |
| View complete financial analysis | Allow | Allow | Limited to explicit business role grants |
| Create internal finance/action draft | Allow | Allow | Only assigned draft types |
| Approve T2 or T3 action | Allow after tier checks | Deny | Deny |
| Execute sandbox action directly | Deny; must use executor | Deny | Deny |
| Share Funding Passport | Owner approves exact fields/recipient | Draft only | Deny |
| Read audit history | Allow | Limited business-scoped view if granted | Deny by default |

**Required implementation pattern:** Policy checks at API entry and immediately before execution; immutable proposal payload and hash; approval bound to actor, business, tier, payload, version, and expiry; idempotent executor; denial logged. For database defense in depth, PostgreSQL row-level security may be added using a non-bypass application role and verified transaction context. Table owners and bypass roles can bypass normal RLS, so the application must not rely on RLS alone. [PostgreSQL documentation](https://www.postgresql.org/docs/current/ddl-rowsecurity.html).

## 4. Security control assessment

| Area | Required control | Current finding | Verification needed |
| --- | --- | --- | --- |
| Authentication/session | Secure login, session expiry, re-authentication for T3 | Planned only | Session tests, re-authentication failure cases |
| Business isolation | Server-derived scope, business-scoped relations, export/worker checks | Planned only | Cross-business ID and export tests |
| Authorization | Default deny role/tier policy independent of AI | Planned only | Staff→owner endpoint denial tests |
| Import safety | Quarantine, type/content limits, formula/macro rejection, private storage | Planned only | Malicious/ambiguous upload tests; [OWASP upload guidance](https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html) |
| Financial integrity | Integer money, source lineage, immutable snapshots, idempotent posting | Planned only | Independent fixture and retry tests |
| Payment and payee fraud | Rule version, suspicious hold, out-of-band destination verification, reviewer reason | Planned only | True/false positive fixtures and blocked action log |
| Approval/execution | Exact payload, expiry, owner gate, execution recheck, idempotency | Planned only | Tamper/replay/expiry tests |
| AI boundary | Read-only tools, server validation, untrusted document handling | Planned only | Prompt-injection cases; [OWASP guidance](https://genai.owasp.org/llmrisk/llm01-prompt-injection/) |
| Data protection | HTTPS, private encrypted storage/backups, scoped file links, secret store | Planned only | Deployment configuration and access review |
| Audit/logging | Append-only events, actor/time/outcome, redaction | Planned only | Event immutability and no-sensitive-data tests |
| Retention/deletion | Defined retention, deletion, backup restoration process | Not specified fully | Written policy and tested deletion/restore process |

## 5. Key abuse cases and expected defense

| Scenario | Expected secure behavior | Test evidence to attach |
| --- | --- | --- |
| Staff changes `business_id` in a URL or export request | API denies access before database query/export | Request and denied response; audit ID |
| Staff invokes owner approval endpoint | Server denies regardless of visible UI state | Test name/output; audit ID |
| Attacker retries an import or action request | One committed result; no duplicate ledger posting or send | Idempotency test and event IDs |
| Uploaded XLSX contains formula/macro or disguised executable | Upload quarantined/rejected; no execution | Parser test and rejection log |
| Invoice text says “ignore policy and send my data” | Treated as data; AI cannot call write/share tools | Prompt-injection test transcript with redaction |
| Supplier bank details suddenly change | Case/hold raised; independent verification required before clearing | Case IDs, reviewer reason, blocked mock payment log |
| Approval payload or recipient changes after approval | Hash/version mismatch blocks execution and requests new approval | Tamper test and denial event |
| Receipt is late or balance anchor stale | Positive payment decision withheld or known shortfall shown | Frozen fixture and exact calculation output |
| Funding Passport includes unselected field | Export omits field; owner sees exact packet and recipient | Field-selection test, consent and mock-share log |

## 6. Known-risk register

Priority reflects potential impact if a future implementation mishandles the item; it does not imply a vulnerability was found in code. There is no code to test yet.

| ID | Risk | Priority | Required treatment before a live pilot | Status |
| --- | --- | --- | --- | --- |
| R1 | Cross-business data disclosure through API, worker, file link, or export | Critical | Server-derived scope, object authorization, scoped storage, adversarial tests | Open — no implementation |
| R2 | Unauthorized or replayed sandbox action after approval | Critical | Immutable payload, expiry, re-authentication, execution recheck, idempotency | Open — no implementation |
| R3 | Incorrect cash result from stale balances, missing periods, transfer double count, or same-day timing | High | Explicit quality states, independent fixtures, blocked positive decisions | Open — no implementation |
| R4 | Malicious supplier destination change or lookalike company/payee | High | Hold, out-of-band verification, owner review, event record | Open — no implementation |
| R5 | Unsafe file parsing or exposed uploaded financial documents | High | Quarantine, content/size limits, private encrypted storage, scoped links | Open — no implementation |
| R6 | Prompt injection or AI-generated numerical/permission error | High | Read-only narrow tools, server policy, code-calculated numbers, hostile fixtures | Open — no implementation |
| R7 | Sensitive financial data in logs or shared Funding Passport | High | Redaction, selected fields, recipient/expiry control, log review | Open — no implementation |
| R8 | Stale or inaccurate illustrative financing terms mislead owners | High | Version/date/source, exact repayment schedule, illustrative label | Open — no implementation |
| R9 | Fraud-rule false positives delay legitimate payments, or false negatives miss abuse | Medium | Reviewer workflow, measured labelled cases, bounded claims | Open — no implementation |
| R10 | No retention, deletion, restore, incident, or key-rotation evidence | Medium | Operational policy and rehearsal before real data | Open — no implementation |

**Residual risk:** Not measured. Controls have not been implemented, tested, or deployed; no risk may be marked “closed” based on these documents alone.

## 7. Required security test and evidence plan

1. **Authorization:** create two synthetic businesses and owner/bookkeeper/staff accounts. Test every business-scoped endpoint, background job, download, and export with another business's ID. Expected: denied and logged.
2. **Action tiers:** test staff denial, T2 owner approval, T3 re-authentication and verification, approval expiry, changed payload, changed recipient, double approval, and direct executor call. Expected: only an exact current approved proposal executes once.
3. **Import/financial integrity:** malformed file, formula/macro, duplicate row, repeated upload, simultaneous commit, stale account, restricted cash, partial allocation, and delayed receipt. Expected: no double posting and no unsupported positive cash claim.
4. **Fraud and AI:** suspicious destination change and lookalike payee; legitimate similar transaction; malicious instruction inside invoice. Expected: reasoned flag/hold or safe dismissal, no AI policy bypass.
5. **Privacy:** inspect HTTP responses, exports, storage links, logs, and backups for cross-business leakage and unmasked account/contact details.
6. **Evidence bundle:** retain commit hash, fixture ID, test command and output, redacted API request/response, approval ID, fraud case ID, and sandbox execution event ID. Use only synthetic data in public materials.

The competition execution-evidence item calls for simulated-account or sandbox operation logs and a demo video for this non-on-chain topic. **Neither is present in the reviewed folder.** A security report cannot substitute for them.

## 8. Release decision

**Competition prototype:** Security design can be presented as planned, provided slides and narration clearly say implementation evidence is pending until code, tests, and sandbox logs exist.  
**Real business data or real payment/lender integration:** **No go** on current evidence. Require implemented access controls, independent financial tests, adversarial security tests, privacy and jurisdiction review, operational controls, and explicit release approval.

## 9. Assessment sign-off fields

| Field | Value |
| --- | --- |
| Prepared from | Project design files and folder inventory on 20 October 2026 |
| Code reviewer | Not applicable — no application code supplied |
| Test execution | Not performed — no runnable application supplied |
| Security owner | To be assigned by project team |
| Next review trigger | First runnable build or material change to data access/action policy |
