# SME Finance Copilot — Built So Far

**Status date:** 20 October 2026
**Meaning of status:** "Built" means code present in this folder that was run and checked as described below. Nothing here is security-certified or production-ready. All data is synthetic.

## Build prompt progress

| Prompt | Scope | Status |
| --- | --- | --- |
| 00 | Runnable foundation (setup only, no product feature) | **Done — verified locally** |
| 01 | Business Access and permission tiers | **Done — verified locally** |
| 02 | Audit Trail | **Done — verified locally** |
| 03 | Business Setup | **Done — verified locally** |
| 04 | Safe Records Import | **Done — verified locally** |
| 05–22 | Trusted Cash Reconstruction through Region Pack | Not started |

## Prompt 00 — runnable foundation

### What was built

| Area | What exists | Files |
| --- | --- | --- |
| Local database | PostgreSQL 16 in Docker (port 5433), named volume, healthcheck | [docker-compose.yml](docker-compose.yml) |
| Configuration | Environment example with placeholders only; `.env` is git-ignored | [.env.example](.env.example), [.gitignore](.gitignore) |
| API | FastAPI modular monolith entry point; feature modules register routers | [backend/app/main.py](backend/app/main.py), [backend/app/config.py](backend/app/config.py), [backend/app/db.py](backend/app/db.py) |
| Health checks | `GET /api/health/live`; `GET /api/health/ready` checks DB connectivity, expected Alembic revision, and the mock-service boundary; returns 503 `degraded` on any failure without leaking connection details | [backend/app/modules/health/router.py](backend/app/modules/health/router.py) |
| Migrations | Alembic revision `0001_foundation`: `business` (jurisdiction, base currency, IANA timezone, reserve as BIGINT minor units + currency, synthetic flag) and `cash_account` (business-scoped, `UNIQUE (business_id, id)` ready for composite foreign keys) | [backend/migrations/versions/0001_foundation.py](backend/migrations/versions/0001_foundation.py) |
| Money contract | `Money(amount_minor: int, currency)`; rejects floats/bools and unknown currencies; refuses to combine different currencies | [backend/app/contracts/money.py](backend/app/contracts/money.py) |
| Time contract | UTC event timestamps; business date derived from the business's explicit IANA timezone; naive timestamps and unknown zones rejected | [backend/app/contracts/business_time.py](backend/app/contracts/business_time.py) |
| Mock-service boundary | Separate local process (port 8100) standing in for bank/lender APIs; `/health` only, no credentials, no money movement | [mock_services/app.py](mock_services/app.py) |
| Synthetic fixture | "Northstar Trading (synthetic)", BDT, Asia/Dhaka, reserve 8,000,000 minor units, 3 accounts, 8 transactions (incl. one own-account transfer pair and one UTC/business-date boundary case) with hand-checked expected values | [fixtures/northstar_trading.json](fixtures/northstar_trading.json), [fixtures/northstar_trading_expected.md](fixtures/northstar_trading_expected.md) |
| Seed | Idempotent loader for the business and its accounts only (transactions stay as reference data until the ledger exists) | [backend/app/fixtures/seed.py](backend/app/fixtures/seed.py) |
| Web shell | React/TypeScript (Vite) responsive shell using design.md tokens: 240px sidebar, sticky top bar with SANDBOX label, hamburger drawer below 900px. The only working page is **System status** (live health table). All eight product destinations are marked **Not built** and open an "Unavailable" placeholder with no figures | [frontend/src/shell/](frontend/src/shell/), [frontend/src/styles.css](frontend/src/styles.css) |
| Start instructions | Setup, migrate, seed, run, and test commands | [README.md](README.md#run-the-development-foundation-locally) |

### Evidence (run on 20 Oct 2026, Windows 11, Python 3.12.10, Node 24.16.0, Docker 29.5.3)

- `alembic upgrade head` → `Running upgrade -> 0001_foundation`.
- Seed run twice → same business id both times; DB query shows 1 business (`northstar-trading`, `BDT`, `Asia/Dhaka`, reserve `8000000`) with 3 accounts.
- `pytest -q` → **14 passed**:
  - money/time contract (6): float and bool amounts rejected, unknown currency rejected, BDT+USD refused, exact integer arithmetic, 18:30 UTC 9 Oct → business date 10 Oct in Dhaka, naive timestamp and unknown zone rejected;
  - fixture self-check (5): every amount is an int with explicit currency; every business date matches its UTC timestamp in Asia/Dhaka; per-account closings 16,224,975 / 6,300,035 / 850,000; operating receipts 8,349,975, payments 8,925,015, net −575,040, transfer pair nets to 0; opening 23,950,050, closing 23,375,010, lowest end-of-day total on 12 Oct — all re-derived independently and matching the hand-checked values;
  - health (3): live OK; ready OK with real DB at revision `0001_foundation` and mock boundary `sandbox`; ready returns 503 when the mock boundary is unreachable.
- Live servers: `/api/health/live` → `{"status":"ok"}`; `/api/health/ready` → HTTP 200 with database and mock_services `ok` (also through the Vite `/api` proxy); mock `/health` → `{"status":"ok","mode":"sandbox"}`.
- `npm run build` (tsc + vite) succeeds.
- Headless Chrome screenshots: desktop 1440×900 System status page with all three checks "Healthy"; Cash Outlook route shows the Unavailable placeholder; 600px-wide view shows the hamburger layout with no horizontal overflow.

### Known gaps and risks

- (At the end of Prompt 00) there was no sign-in or business scoping; Prompt 01 below adds them.
- No finance feature exists: no import, ledger, balances, analysis, forecast, alerts, payment review, financing, fraud, approvals, audit, or chat.
- Fixture transactions are not loaded into the database; the expected values are verified only by an independent in-test calculation, not by product code (there is none yet).
- PostgreSQL row-level security, private object storage, the background worker, and an outbox are not set up.
- Phone-width layout was checked at 600px in Prompt 00. A real 390px viewport was verified later in Prompt 01.
- The shell follows design.md (light sidebar) rather than the dark sidebar in the mockup images.
- Development settings only: the local DB password is a placeholder, CORS allows only `http://localhost:5173` and `GET`, and no HTTPS.

## Prompt 01 — Business Access and permission tiers

### What was built

| Area | What exists | Files |
| --- | --- | --- |
| Schema | Alembic revision `0002_business_access`: `app_user` (lower-case unique email, scrypt password hash), `membership` (business, user, role `owner`/`bookkeeper`/`staff`, one row per business+user), `auth_session` (only the SHA-256 hash of the token is stored; expiry and revocation) | [backend/migrations/versions/0002_business_access.py](backend/migrations/versions/0002_business_access.py) |
| Local demo sign-in | `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me`. 8-hour server-side session in an `HttpOnly`, `SameSite=Strict` cookie scoped to `/api` (`Secure` outside development). Unknown email and wrong password return the same 401 and both run a password hash | [backend/app/modules/identity/router.py](backend/app/modules/identity/router.py), [passwords.py](backend/app/modules/identity/passwords.py) |
| Role matrix (deny by default) | `business.read`, `membership.read` → all roles; `business.export` → owner and bookkeeper; `membership.manage` (owner setting) → owner only. Any action not listed is refused for every role | [backend/app/modules/identity/policy.py](backend/app/modules/identity/policy.py) |
| Business scope from session | `require(action)` dependency resolves the signed-in user from the session, then looks up that user's membership for the `business_id` in the path. Not signed in → 401. Not a member, or the business does not exist → identical 404. Member without the role → 403 | [backend/app/modules/identity/deps.py](backend/app/modules/identity/deps.py) |
| Business-scoped API | `GET /api/businesses` (only your memberships; no "list all" route), `GET /api/businesses/{id}`, `GET …/memberships`, `PATCH …/memberships/{membership_id}` (owner only; rows locked; the last owner cannot be demoted → 409), `GET …/export` (JSON with profile, accounts, member names and roles; no emails or hashes; every query filtered by the authorized business) | [backend/app/modules/identity/router.py](backend/app/modules/identity/router.py) |
| Demo data | Second synthetic business "Riverbend Crafts" for isolation tests. Four demo users: Amina (Northstar owner), Bilal (Northstar bookkeeper), Chaya (Northstar staff), Tanvir Ahmed (`tanvir@riverbend.example`, Riverbend owner). On 20 Oct 2026 this account was renamed in place from Rafi Karim (`rafi@riverbend.example`), so audit events recorded before the rename still show the name Rafi Karim, as an append-only history should. Shared local password `DEMO_USER_PASSWORD` from `.env`. Re-running the seed restores these roles | [fixtures/riverbend_crafts.json](fixtures/riverbend_crafts.json), [fixtures/demo_users.json](fixtures/demo_users.json), [backend/app/fixtures/seed.py](backend/app/fixtures/seed.py) |
| UI | Sign-in page; signed-out users see only this page. Shell shows business name, role pill, user name and role, and a Sign out button, plus a business selector when the user has more than one membership. **Users & roles** screen: an owner gets role dropdowns; bookkeeper and staff see read-only badges with "Only an owner can change roles". The screen also shows a role/permission table. The UI hides controls, but the server enforces the rules | [frontend/src/auth/](frontend/src/auth/), [frontend/src/shell/AppShell.tsx](frontend/src/shell/AppShell.tsx) |
| Wiring | Health readiness now expects schema `0002_business_access`. CORS allows credentials and only `GET/POST/PATCH` with `Content-Type` | [backend/app/main.py](backend/app/main.py), [backend/app/modules/health/router.py](backend/app/modules/health/router.py) |

### Evidence (run on 20 Oct 2026)

- `alembic upgrade head` → `0001_foundation -> 0002_business_access`.
- `pytest -q` → **37 passed** (14 from Prompt 00 + 23 new in [backend/tests/test_authorization.py](backend/tests/test_authorization.py)):
  - **Deny by default:** unknown actions, an empty action, role `None`, and role `admin` are refused. Every `/api/businesses/{business_id}…` route declares exactly one known action. Every non-public `/api` route requires sign-in. These two route checks scan the real app routes, so a new unguarded route fails the test.
  - **Sign-in:** wrong password and unknown email give the same 401 body. No cookie → 401. Cookie is HttpOnly and SameSite=Strict. A token replayed after logout → 401. An expired session → 401.
  - **Owner settings:** staff cannot demote the owner (403, DB role still `owner`). Staff cannot promote themselves (403). Bookkeeper cannot change roles (403). Owner can change a role (200, DB updated). The last owner cannot be demoted (409). Role `admin` → 422.
  - **Cross-business isolation:** the Northstar owner swaps in Riverbend's ID for profile, memberships, and **export** → 404, with the same body as a random UUID and no "Riverbend" in the response. Changing a Riverbend membership via either business path → 404, and the Riverbend owner's role is unchanged. Bookkeeper export contains only Northstar data (no Riverbend, no emails, no passwords). Staff export → 403. `/api/businesses` never lists a foreign business.
- **Live run through the web proxy (curl):**
  - Staff `PATCH` on the owner's membership → `{"detail":"Your role does not allow this action"}` [403], and the DB role is still `owner`.
  - Staff export → 403.
  - Northstar owner with Riverbend's ID → `{"detail":"Business not found"}` [404] for both profile and export, identical to a random ID.
  - Own export → 200 with Northstar data only.
  - No session → 401.
  - `/api/health/ready` → 200 at `0002_business_access`.
- **Headless Chrome screenshots (Chrome DevTools Protocol, real sign-in):** sign-in page; owner Users & roles with dropdowns; staff Users & roles read-only with the notice; staff System status at a real 390×844 phone viewport. A sidebar-footer overflow found in the screenshots was fixed and re-checked.
- `npm run build` passes. The seed was re-run after the tests, so the demo roles are back to the table above.

### Known gaps and risks

- (At the end of Prompt 01) audit events were not recorded; Prompt 02 below adds them.
- No approval inbox, owner re-authentication, or action tiers T1–T3 (Prompt 15). No inviting or removing members; only role changes on existing memberships.
- (At the end of Prompt 01) the owner's role dropdown had not been used in a browser; it was exercised in Prompt 02.
- Local demo authentication only: one shared demo password, no login rate limiting or lockout, no MFA, no password reset. CSRF protection relies on `SameSite=Strict` plus JSON bodies; there is no CSRF token.
- No PostgreSQL row-level security yet. Isolation is enforced in the API and tested there only. No worker exists yet to test.
- Business scope is re-derived from membership on each request. The selected business in the UI is only a display choice.
- Expired or revoked session rows are not cleaned up.

## Prompt 02 — Audit Trail

### What was built

| Area | What exists | Files |
| --- | --- | --- |
| Schema | Alembic revision `0003_audit_trail`: `audit_event` stores who (user ID, name, role at the time), what (`action` code), when (`occurred_at`, UTC), which business (`business_id`), outcome (`success`/`denied`/`not_found`/`conflict`/`failed`), target, redacted `details` (JSON object), `prev_hash`, and `event_hash`. CHECK constraints cover the action format, outcome values, role values, and that details is an object | [backend/migrations/versions/0003_audit_trail.py](backend/migrations/versions/0003_audit_trail.py) |
| Append-only | Database triggers reject `UPDATE`, `DELETE`, and `TRUNCATE` on `audit_event`. The API has no create, edit, or delete route for events (POST/PUT/PATCH/DELETE → 405/404) | same migration, [backend/app/modules/audit/router.py](backend/app/modules/audit/router.py) |
| Tamper evidence | Each event is chained by SHA-256 to the previous one. A transaction-scoped advisory lock serializes appends, so concurrent writers cannot fork the chain. `python -m app.modules.audit.verify` re-computes the whole chain | [backend/app/modules/audit/service.py](backend/app/modules/audit/service.py), [verify.py](backend/app/modules/audit/verify.py) |
| Redaction | Applied before storage. Password, token, secret, cookie, document, content, file, and raw-row keys → `[REDACTED]`. Account-number, IBAN, card, and wallet keys → last 4 digits. Any 8+ digit run in free text → `••••` + last 4 (whole UUIDs and ISO dates exempt). Strings truncated at 200 chars. Float values refused. Failed sign-in emails masked (`a***@domain`) | [backend/app/modules/audit/redaction.py](backend/app/modules/audit/redaction.py) |
| Events recorded (existing sensitive operations) | `auth.sign_in` success/failed; `auth.sign_out`; `membership.role_change` success (from/to role and member name), conflict (last owner), not_found; `business.export` success (section names and counts only, never content); every **denied** role check (403) for any action, with the actor's role. A probe on a business the user does not belong to is stored as a user-level event (`business_id` NULL), never in the other business's history. Successful role changes write the event **in the same transaction** as the change | [backend/app/modules/identity/deps.py](backend/app/modules/identity/deps.py), [backend/app/modules/identity/router.py](backend/app/modules/identity/router.py) |
| Read API | `GET /api/businesses/{id}/audit-events?limit=1..100&before_seq=N` (cursor paging, newest first). New role action `audit.read` for owner and bookkeeper only; staff → 403; non-member → 404 | [backend/app/modules/audit/router.py](backend/app/modules/audit/router.py), [policy.py](backend/app/modules/identity/policy.py) |
| UI | **Approvals & Audit** is now available in the nav. Following the mockup layout: an "Approval inbox" card marked Unavailable (Prompt 15), and an **Activity history** timeline. Each row has a plain-language description, outcome badge, time in the business timezone (Asia/Dhaka), actor name and role, and event number with its hash on hover, plus "Load older events". Staff see "Only owners and bookkeepers can view the audit history". Users & roles permission table gained the audit row | [frontend/src/audit/AuditPage.tsx](frontend/src/audit/AuditPage.tsx) |

### Evidence (run on 20 Oct 2026)

- `alembic upgrade head` → `0002_business_access -> 0003_audit_trail`.
- `pytest -q` → **55 passed** (37 earlier + 18 new in [backend/tests/test_audit.py](backend/tests/test_audit.py)):
  - **Redaction:** password, token, document, file, and raw-row values removed; `0123-4567-8901` → `••••8901`; `"Paid to A/C 1234 5678 9012 3456 today"` → `"Paid to A/C ••••3456 today"`; IBAN masked; `INV-108` and integer amounts kept; UUIDs and dates kept; 500-char text truncated; float amounts rejected.
  - **Recording:** sign-in success, wrong password, unknown email, and sign-out recorded in order. The typed password does not appear anywhere in the stored events. Role change event has the actor, role, business, target, from/to roles, and a timezone-aware time. Staff denial and last-owner conflict are recorded. The export event has counts but no account labels.
  - **Atomicity:** if the audit write fails, the API returns 500 and the role change is rolled back.
  - **Cannot edit/delete:** PUT, PATCH, and DELETE on an event and on the collection, and POST of a fake event, are refused, and the stored hash is unchanged. Direct SQL `UPDATE`, `DELETE`, and `TRUNCATE` are rejected with "audit_event is append-only".
  - **Cannot read another business's events:** the Northstar owner on Riverbend's audit URL → 404. The probe is stored with no business, and Tanvir Ahmed (Riverbend owner) sees no Amina events. Northstar's page contains only Northstar rows. Staff → 403; bookkeeper → 200. Paging and limit validation work (0, 101, and `before_seq=0` → 422).
  - **Chain:** verifies over all events. 16 concurrent appends keep one unbroken chain (each event has a distinct predecessor). A simulated out-of-band edit (trigger disabled inside a rolled-back transaction) is detected.
- **Real event from the demo flow (browser, headless Chrome via DevTools Protocol):**
  - Chaya (staff) signed in through the real form and tried an export → 403. It shows as "Tried to export business data without permission · Denied".
  - Amina (owner) changed Chaya from Staff to Bookkeeper **using the real Users & roles dropdown** (this closes the Prompt 01 gap). The page showed "Chaya Das is now Bookkeeper."
  - The top row of Activity history was "Changed Chaya Das's role from Staff to Bookkeeper · Success · 20 Oct 2026, 03:03:54 · Amina Rahman (Owner)".
  - The stored row:
    `seq 427 | 2026-10-19 21:04:29 UTC | Amina Rahman | owner | membership.role_change | success | {"to_role":"bookkeeper","from_role":"staff","member_name":"Chaya Das"}`.
  - Screenshots checked: owner desktop, staff desktop, owner at 390×844.
- `python -m app.modules.audit.verify` → `Audit chain OK: 448 events checked`.
- A DB scan found **0** stored events containing the demo password. The only 8+ digit runs are inside UUID record IDs, which are exempt by design.
- `/api/health/ready` → 200 at `0003_audit_trail`. `npm run build` passes. The seed was re-run afterwards, so demo roles are back to owner/bookkeeper/staff.

### Known gaps and risks

- (At the end of Prompt 02) tests wrote into the development database, which is why it contains older test events. Prompt 03 moved tests to a separate `smecopilot_test` database.
- Successful reads (profile, member list, audit page) are not recorded; only sensitive operations and refusals are. Requests with no session (401) are not recorded because there is no actor.
- The seed script changes roles directly in the database. Those changes are not audited, because it is an admin tool outside the application.
- Append-only is enforced by triggers. The local PostgreSQL user owns the table and is a superuser, so it could disable them. A separate non-owner application role is not set up yet. The hash chain detects such edits, but it cannot prevent them or detect deleting the newest events.
- No IP address or request ID is stored. There is no retention or export policy for audit data.
- Denied and refused attempts commit their audit event before the error response. A failure while writing that event turns the refusal into a 500, so the request is still refused.
- No approvals or action execution (Prompt 15/17). The approval inbox card is a placeholder.

## Prompt 03 — Business Setup

### What was built

| Area | What exists | Files |
| --- | --- | --- |
| Schema | Alembic revision `0004_business_setup` adds a `business.version` column used to reject stale edits, plus `updated_at` on `business` and `cash_account`. Database CHECK constraints back up the API: name 1–120 characters, reserve currency must equal the base currency, reserve ≤ 10^15 minor units (stays exact in JavaScript), timezone non-empty, account label 1–60 characters. A unique index makes account labels unique per business, ignoring case | [backend/migrations/versions/0004_business_setup.py](backend/migrations/versions/0004_business_setup.py) |
| Reference data | Supported jurisdictions BD, IN, US, JP, DE, each with a suggested currency and timezone. Currencies come from the money contract with their minor-unit exponent (BDT 2, JPY 0, …). The full IANA timezone list comes from `zoneinfo`. Limits: 20 accounts per business, 10 businesses per user. Listing a jurisdiction is not a compliance claim | [backend/app/contracts/reference.py](backend/app/contracts/reference.py) |
| Server-side validation | Strict Pydantic models. **Unknown fields and explicit `null` are rejected.** Money must be `StrictInt` minor units, so floats, numeric strings, booleans, negatives, and amounts over the maximum are refused. Jurisdiction and currency must be supported (lower case is normalized). Timezone must be an exact IANA name, so `UTC+6`, `asia/dhaka`, and path-like values are refused. Text is NFC-normalized, trimmed, and whitespace-collapsed, with control and zero-width characters rejected. Reserve currency must equal base currency. Changing base currency requires restating the reserve. Account labels must be unique ignoring case. An edit must change at least one setting | [backend/app/modules/business_setup/schemas.py](backend/app/modules/business_setup/schemas.py) |
| API | `GET /api/reference/settings-options` (signed in).<br>`POST /api/businesses` (any signed-in user; the creator becomes **owner**).<br>`PATCH /api/businesses/{id}/settings`: requires `expected_version`; a stale version → 409; a no-op does not bump the version or write an audit event.<br>`POST /api/businesses/{id}/accounts` and `PATCH …/accounts/{account_id}` (relabel only; kind and currency are fixed once created). Duplicate label → 409; account limit → 409; account of another business → 404.<br>The `GET /api/businesses/{id}` profile now includes version, accounts, `is_synthetic`, and jurisdiction name | [backend/app/modules/business_setup/router.py](backend/app/modules/business_setup/router.py), [service.py](backend/app/modules/business_setup/service.py) |
| Permissions | New role action `business.settings.update` for **owner only**. Bookkeeper and staff get 403, and the refusal is audited | [backend/app/modules/identity/policy.py](backend/app/modules/identity/policy.py) |
| Audit | `business.create`, `business.settings_update` (success with a from/to diff per field and the new version, plus conflict), `account.create` (success/conflict), and `account.relabel` (success/conflict/not_found). Each is written in the same transaction as the change. The Activity history page shows readable sentences for them | [router.py](backend/app/modules/business_setup/router.py), [frontend/src/audit/AuditPage.tsx](frontend/src/audit/AuditPage.tsx) |
| UI | **Business settings** page (sidebar footer). Profile card shows name, jurisdiction, base currency, timezone, reserve as **BDT 80,000.00** with lakh grouping (shown with the Taka currency sign), settings version, and a "Synthetic demo business" badge. Owners get **Edit settings**, inline account relabel, and **Add account**; others see a read-only notice. Field-level error messages come from both the client and the server. **New business** page: picking a jurisdiction suggests its currency and timezone, and the new business becomes active. The top bar shows a "BDT · Bangladesh" pill as in the Overview mockup. Typed amounts are converted to minor units with exact string/BigInt arithmetic, never floats. The Taka currency sign is loaded from the Noto Sans Bengali font (Inter has no Taka glyph) | [frontend/src/settings/](frontend/src/settings/), [frontend/src/money.ts](frontend/src/money.ts) |
| Synthetic demo business | Northstar Trading (synthetic) remains the seeded demo business (`is_synthetic = true`). Businesses created through the app are marked `is_synthetic = false` | [backend/app/fixtures/seed.py](backend/app/fixtures/seed.py) |
| Separate test database (fixes a Prompt 02 gap) | Tests now run against `smecopilot_test`, which is created and migrated automatically and refuses any database whose name does not end in `_test`. The demo database is no longer touched by tests | [backend/tests/conftest.py](backend/tests/conftest.py) |

### Evidence (run on 20 Oct 2026)

- `alembic upgrade head` → `0003_audit_trail -> 0004_business_setup`. `/api/health/ready` → 200 at `0004_business_setup`.
- `pytest -q` → **118 passed**, and **118 passed again** on a second run against the same persistent test database. That is 55 earlier tests plus 63 new in [backend/tests/test_business_setup.py](backend/tests/test_business_setup.py). Each test uses a fresh throwaway owner.
  - **Invalid inputs rejected and nothing created or changed:**
    - 28 create cases: empty, blank, 121-character, NUL, zero-width-space, and numeric names; `XX` and empty jurisdiction; `XYZ` currency; `Mars/Olympus`, `UTC+6`, `asia/dhaka`, and `../../etc/passwd` timezones; reserve `100.5`, `"1000"`, `true`, `-1`, and 10^15+1; USD reserve on a BDT business; missing reserve currency; extra fields; duplicate labels ignoring case; empty, 61-character, bad-kind, and BTC accounts; 21 accounts.
    - 11 edit cases: only a version, missing, zero, or string version, `null`, bad timezone, float or negative reserve, `ZZ`, and attempts to set `slug` or `is_synthetic`. The stored row is byte-for-byte unchanged afterwards.
  - **Staff and bookkeeper edits denied:** settings edit, add account, and relabel all return 403, the Northstar row is unchanged, and a denied audit event is recorded with the actor's role.
  - **Persists after reload:** an owner edit (name, timezone, reserve 7,550,025) read back by a new session has version 2.
  - **Audit recorded:** exact diff `{"name":{"from":"Test Traders","to":"Test Traders Ltd"},"timezone":{…},"reserve_amount_minor":{"from":5000000,"to":7550025}}`. Create, account create, and relabel events are also checked.
  - **Concurrency, exactness, and isolation:**
    - A stale version → 409 and the first edit is kept.
    - Changing base currency without restating the reserve → 422; with it → 200.
    - Reserve 10^15 is stored exactly.
    - The membership cap stops the 11th business. The account cap stops the 21st account.
    - Northstar's owner cannot edit Riverbend's settings or accounts (404), including with a foreign account ID under their own business path.
    - Database constraints reject a mismatched reserve currency, a negative reserve, and an empty name even if the API were bypassed.
- **Browser run (headless Chrome via DevTools Protocol, real forms):**
  - Chaya (staff): no Edit button, notice "Only an owner can change these settings."
  - Amina (owner), edit form:
    - Reserve `85,000.505` → client error "Use at most 2 decimal places."
    - Timezone `Mars/Olympus` → server error under the field "must be an IANA timezone name such as Asia/Dhaka".
    - A valid save, then a **full page reload**, shows `BDT 85,000.50` (with the Taka sign) and `v2`.
  - Amina, accounts: relabel "Cash box" → "Shop cash box" works. Relabel to "operating BANK account" → "An account with this label already exists".
  - The audit page lists the new events as readable sentences.
  - A QA user created "QA Browser Traders" with reserve `1,00,000`. It opened on its settings page showing `BDT 1,00,000.00` (with the Taka sign).
  - Screenshots were checked on desktop and at 390×844.
- Afterwards Northstar's demo values were restored **through the API as Amina** (reserve 8,000,000, label "Cash box"), so the restore is in the audit trail too. Northstar is now at settings version 3.
- `audit.verify`: demo DB `OK: 467 events`; test DB `OK: 844 events`.

### Known gaps and risks

- Base currency and account currency are freely editable/creatable because no records exist yet. Once Records import (Prompt 04) adds transactions, changing a base currency with history must be restricted.
- Accounts cannot be deleted or archived, and kind and currency cannot be changed after creation (deliberate until the ledger exists).
- The supported jurisdiction list is small and hard-coded, with no region-specific rules. The Region Pack is Prompt 22.
- Validation failures (422) are not audited because nothing changed. Denials (403) and conflicts (409) are.
- Creating a business requires only sign-in. Anyone with a demo account can create up to 10 businesses. There is no invitation flow to add members to a new business.
- The dev database has a QA user `qa.creator@test.example` and its business "QA Browser Traders" from the browser check. They cannot be removed cleanly because audit events reference them (append-only).
- During this work the seed was re-run on API restart, which reset Bilal and Chaya to bookkeeper/staff after they had been made owners in the UI. The seed always restores the demo roles. Those changes and the reset are visible in the audit trail (reset only indirectly, since the seed itself is not audited).
- The Taka-sign glyph loads from Google Fonts; offline it falls back to a system font.

## Prompt 04 — Safe Records Import

### What was built

| Area | What exists | Files |
| --- | --- | --- |
| Schema | Alembic revision `0005_records_import` adds three tables:<br>• `import_batch` (one file for one account; SHA-256, size, private storage key, parser version, status `needs_mapping → staged → posted/discarded`, revision, commit key).<br>• `import_row` (staged rows: raw cells, parsed fields, errors, duplicate link, decision).<br>• `ledger_transaction` (canonical posted movements in signed BIGINT minor units with currency and business date, plus lineage: source batch, row number, parser version, poster).<br>Business-scoped composite foreign keys tie every batch, row, and transaction to an account of the **same** business. A partial unique index makes the same file for the same account a single batch. `UNIQUE (source_batch_id, source_row_number)` makes double posting a row impossible. The ledger is append-only (triggers block UPDATE/DELETE/TRUNCATE). CHECK constraints keep staged error rows excluded, valid rows complete and non-zero, and posted status consistent with commit fields | [backend/migrations/versions/0005_records_import.py](backend/migrations/versions/0005_records_import.py) |
| Whole-file safety checks | The type is decided from **content**, and the extension must agree.<br>**Rejected:** empty files; extensions other than .csv/.xlsx; a .csv that is really a zip and vice versa; legacy .xls and **encrypted** workbooks (OLE container or encrypted zip entries); binary/NUL content; non-UTF-8 text; an **ambiguous column separator**; fewer than two columns; empty, duplicate, or over-long headers; formula-like headers; malformed CSV quoting; header-only files; **formulas** anywhere in an .xlsx; **macros** (`vbaProject.bin` / macro-enabled content type); data on **more than one sheet**; **zip bombs** (compression ratio > 200, uncompressed > 25 MB, > 200 parts); corrupt workbooks.<br>**Limits:** 2 MB, 5,000 rows, 30 columns, 500 characters per cell. An ASGI middleware refuses oversized request bodies (413) **before** multipart parsing, even without a Content-Length.<br>XML is parsed with `defusedxml` installed | [backend/app/modules/records/files.py](backend/app/modules/records/files.py), [backend/app/middleware.py](backend/app/middleware.py) |
| Row-level validation (rejected rows) | **Text cells:** any cell that looks like a formula (`=`, `@`, a leading tab/CR, or `+`/`-` not followed by a plain number) is rejected, which guards against CSV injection.<br>**Dates:** use the **explicitly chosen** format; 2-digit years are never accepted; dates before 2000 or in the **future in the business timezone** are rejected.<br>**Amounts:** "." is the decimal separator; commas are accepted only as valid thousands or **lakh** grouping (`1,00,000`). `1.234,50` and `12,5` are refused as ambiguous, not guessed. Parentheses mean negative; the Taka sign, `Tk`, and `BDT` are allowed as currency markers. Decimal places may not exceed the currency's minor unit, and zero, infinite, and oversized amounts are refused. Debit/credit mode needs exactly one of the two, unsigned.<br>**Other columns:** a currency column must equal the account currency (no conversion); descriptions are 1–200 characters with no control or zero-width characters | [backend/app/modules/records/normalize.py](backend/app/modules/records/normalize.py) |
| Field mapping | The mapping is explicit (date + format, description, signed amount **or** debit/credit, optional reference/counterparty/currency). It is a strict model: unknown fields are rejected, column indexes must be in range, and a column cannot map twice. The server suggests columns from header names and pre-selects a date format **only when exactly one fits**. When dates fit both DD/MM and MM/DD, the UI says it is ambiguous and requires a choice | [normalize.py](backend/app/modules/records/normalize.py), [service.py](backend/app/modules/records/service.py) |
| Duplicate review | **In-file:** an identical later row → "Same as row N". **Against the ledger:** same account, date, and amount **and** the same bank reference or the same normalized description → "Matches posted …".<br>Every candidate starts as `pending`, and posting is **blocked** until a reviewer chooses "Exclude" or "Keep both". Identical-looking legitimate payments can be kept.<br>At commit the ledger is **re-checked**: if another import posted a matching row meanwhile, the affected rows go back to pending and the commit is refused | [service.py](backend/app/modules/records/service.py) |
| Explicit, atomic, idempotent commit | `POST …/commit` takes `expected_revision` + an `idempotency_key` (UUID). In **one transaction** it locks the batch and the account, refuses stale revisions, pending duplicates, and empty selections, inserts only valid rows marked "Post", and marks the batch posted.<br>A retry with the **same key** returns the same result without posting again (`replayed: true`). A different key on a posted batch → 409.<br>**Re-uploading the same file** returns the existing batch (`duplicate_upload: true`), whether it is staged or already posted | [service.py](backend/app/modules/records/service.py), [router.py](backend/app/modules/records/router.py) |
| Staged ≠ official cash | Staged rows live only in `import_row`. The ledger, the Records summary, and the "Connected records" counts read posted rows only. Every staged preview shows "Staged rows are a preview only. They are not official records and are not counted as cash until posted." No balances are computed (Prompt 05) | [router.py](backend/app/modules/records/router.py) |
| Private originals | Original bytes are saved to `STORAGE_DIR` (default `storage/private/`, git-ignored, outside anything served), named `<business>/<batch>.<ext>` instead of the user's filename. Writes are atomic and checked against path traversal. The storage key is never returned by the API, and **no route serves originals**. User filenames are sanitized (path stripped, control characters removed, ≤ 120 characters) | [backend/app/modules/records/storage.py](backend/app/modules/records/storage.py) |
| Permissions | New role actions:<br>• `records.read` and `import.stage` (upload, map): owner, bookkeeper, staff.<br>• `import.review` (row decisions, discard) and `import.commit` (post): owner, bookkeeper.<br>So staff can submit records for review but cannot approve them | [backend/app/modules/identity/policy.py](backend/app/modules/identity/policy.py) |
| Audit | `import.upload` (success / reused same file / failed with reason code), `import.map` (counts), `import.row_decision`, `import.commit` (success with count / conflict with reason), and `import.discard`. Events never contain cell contents | [router.py](backend/app/modules/records/router.py) |
| Records page (from `images/02-records.png`) | **Import new records:** account selector, file input, the limits explained, and client pre-checks for type and size. **Connected records:** per account, posted count, latest record date, and "Imported · not reconciled" or "No records". **Items to review:** open imports with "Map columns", "N rejected", "N possible duplicates", "N selected". **Posted transactions:** the latest 20, with signed BDT amounts | [frontend/src/records/RecordsPage.tsx](frontend/src/records/RecordsPage.tsx) |
| Import review page | Status badge and the staged notice. **Field mapping:** column dropdowns with sample values; the ambiguous date-format warning; collapses to a one-line summary with "Change mapping" after staging. **Preview** tabs (All / Rejected / Possible duplicates / Selected / Excluded): per-row errors, duplicate badges, Exclude / Keep both buttons, and Post/Skip checkboxes. A commit bar shows "Post N rows" (disabled while duplicates are pending; one idempotency key per previewed revision, so a double-click posts once), plus Discard. Staff see "Only an owner or bookkeeper can post." The audit page describes all import events | [frontend/src/records/ImportDetailPage.tsx](frontend/src/records/ImportDetailPage.tsx) |
| Demo files | `fixtures/imports/northstar_bank_oct_part1.csv`: 7 rows; one formula row, one invalid date (31/09), one exact duplicate, and dates that fit both DD/MM and MM/DD. `…part2.csv`: 3 rows, one overlapping an already-posted payment | [fixtures/imports/](fixtures/imports/) |

### Evidence (run on 20 Oct 2026)

- `alembic upgrade head` → `0004_business_setup -> 0005_records_import`. `/api/health/ready` → 200 at `0005_records_import`.
- `pytest -q` → **181 passed**, twice in a row on the persistent test database. That is 118 earlier tests plus 63 new in [backend/tests/test_records_import.py](backend/tests/test_records_import.py):
  - **22 unsafe or ambiguous files rejected.** Each returns 422 with its reason code, creates no batch, and writes a `failed` audit event. Oversized → 413, both from the file check and from the body-size middleware.
  - **Valid import:** part1 stages 7 rows with nothing in the ledger. After mapping DD/MM/YYYY the counts are exactly `{total 7, valid 5, error 2, duplicates 1, pending 1, included 4}`, and amounts are exact (6,500,000 / −3,275,025 / −1,000,000 / −4,500,000).
  - **Rejected rows:** row 7 is a formula; row 8 is `31/09/2026`.
  - **Duplicate candidate:** row 4 = "Same as row 3", pending, and posting is blocked with `pending_rows: [4]`. After "Exclude", 4 rows post. Their sum is **−2,275,025**, exactly the hand-checked bank movement in `fixtures/northstar_trading_expected.md`, with full lineage.
  - **Retry without double posting:** the same key → `replayed: true` with 4 ledger rows. A different key → 409. **6 concurrent commits** with one key → all 200, 4 ledger rows. Re-uploading the same file (renamed or not) returns the same batch, and after posting it cannot post again.
  - **Ledger and commit-time duplicates:** part2 after part1 flags "Matches posted" with the transaction's date, amount, and reference. Two overlapping staged files: after the first posts, the second's commit is refused with `pending_rows: [2, 3, 4]` (row 3 matched by bank reference despite different text).
  - **Other behaviour:** "Keep both" posts two identical legitimate payments. Rejected rows cannot be included (422). An empty selection is refused. Stale revisions → 409 on map, decide, and commit. A discarded batch frees the same file to upload again. A posted batch cannot be discarded. Invalid mappings (no format, `DD/MM/YY`, double-mapped column, out-of-range, string index, wrong mode, missing field, extra key) → 422.
  - **XLSX:** native dates and numbers; `650.5` → 65,050 minor; `"1,00,000"` → 10,000,000.
  - **Permissions:** staff upload and map, but decide, commit, and discard → 403; a bookkeeper can post. Another business gets 404 on the batch, rows, commit, and ledger, and an upload to a foreign account → 404.
  - **Append-only and private storage:** UPDATE/DELETE/TRUNCATE on the ledger raise "append-only". The original is stored byte-for-byte at `<business>/<batch>.csv`, and no storage path appears in any response.
  - **Row validation:** 11 unit cases (future date, pre-2000, impossible date, zero, European format, 3 decimals, foreign currency, zero-width character, missing description, `@SUM`, `+cmd|…` injection). Common formats `(45,000.00)`, `1,00,000.50` with a Taka-sign prefix, debit/credit, and `bdt` currency are accepted.
- **Browser run (headless Chrome via DevTools Protocol, real file input):**
  - Chaya (staff) uploaded part1 to "Operating bank account". The mapping showed "Ambiguous: dates fit DD/MM/YYYY and MM/DD/YYYY", and applying without a format was blocked. With DD/MM she saw "5 valid · 2 rejected · 1 possible duplicates" and no Post button ("Only an owner or bookkeeper can post.").
  - Amina saw it under Items to review ("2 rejected | 1 possible duplicate | 4 selected"). Rejected rows showed their reasons, and Post was disabled until the duplicate was excluded.
  - A **double-click on "Post 4 rows"** posted once. The database shows 4 ledger rows for the batch and one `import.commit success` event.
  - Re-uploading part1 showed "This exact file was already uploaded…". Part2 pre-selected DD/MM/YYYY (unambiguous), flagged "Matches posted 2026-10-10 · -BDT 45,000.00" (shown with the Taka sign), and posted 2 after Exclude.
  - A wallet .xlsx with `=100*2` → "Formulas are not accepted (cell C2)…".
  - **A real bug was found and fixed:** the UI sent a display-only hint inside the mapping, the strict server rejected it, and the page only said "request failed". The client now sends exact fields, and errors show the server message.
  - Screenshots were checked on desktop and at 390×844.
- Demo database state: 6 posted rows on Operating bank account. Part1 is −2,275,025 (= fixture T001+T002+T004+T007) and part2 is +1,730,000 (14–15 Oct electricity and receipt). The audit chain verifies: demo DB `OK: 488`, test DB `OK: 3206`.

### Known gaps and risks

- **Demo ledger vs fixture:** the 2 rows posted from part2 are not in the Prompt 00 fixture, so the demo bank ledger now differs from the fixture's expected closing balance by +1,730,000. The ledger is append-only, so this stays. Prompt 05 tests should use their own fixtures or account for it.
- No balances, anchors, reconciliation, transfer pairing, or freshness: "Imported · not reconciled" is all that is claimed (Prompt 05). Own-account transfers are imported as ordinary rows.
- No corrections or reversals of posted rows yet (append-only). A wrong post needs a future correction feature.
- Duplicate matching is exact on date and amount. Near-duplicates (a day apart, slightly different amount) are not flagged. Possible-duplicate decisions are per row; there is no bulk action.
- CSV must be UTF-8. Excel serial-number dates in a text column, 2-digit years, and European decimal commas are rejected rather than converted. Descriptions starting with "-" or "+" followed by text are rejected as formula-like (conservative).
- The file hash proves only that two uploads are identical, not that the file is authentic. There is no malware scanning, and storage is not encrypted at rest (local disk).
- Only the first 500 preview rows are shown in the UI (the API pages up to 5,000). Very large files are slower to map (one UPDATE per row, batched).
- The posted batch's original file stays on disk. Discarded batches keep their file and staged rows for traceability.

## Not built or verified yet

| Area | Current status |
| --- | --- |
| Owner approval, action tiers T1–T3 | Not implemented (Prompt 15). Login, roles, and API business isolation (Prompt 01) and the audit trail (Prompt 02) are built |
| Reconciliation, balances, transfers, receivables/payables | Not implemented (Prompts 05–06). CSV/XLSX import into an append-only ledger is built (Prompt 04) |
| Financial analysis, cash forecast, alerts, Payment Review, and Rescue Plan | Not implemented (Prompts 07–11, 13) |
| Financing catalogue, matching, readiness, conditional plan, and Funding Passport | Not implemented (Prompts 12, 14, 19, 20) |
| Fraud rules and payment hold | Not implemented (Prompt 16) |
| Sandbox action executor and operation logs | Not implemented (Prompt 17) |
| AI assistant and tool boundaries | Not implemented (Prompt 21) |
| Deployment, presentation deck, demo video, security self-assessment | Not created |
| Real customer interviews, pilots, financial outcomes | Not supplied or verified |

## What can be claimed now

A runnable local development foundation exists: database with a versioned schema, an API with health checks, a mock external-service boundary, a synthetic fixture with independently checked money values, and a responsive application shell.

Local demo sign-in with owner/bookkeeper/staff roles is enforced in the API, and unknown actions are denied. The tested scenarios show that staff cannot change owner settings and that changing a business ID (including for exports) reveals nothing about another business. This is API-level enforcement tested on these scenarios only, not a security certification.

Sensitive operations and refused attempts are recorded in an append-only, hash-chained, redacted audit trail. Owners and bookkeepers can read their business's audit history and nobody else's.

An owner can create and edit a business profile (name, jurisdiction, base currency, timezone, reserve in integer minor units, account labels). Strict server-side validation, owner-only edits, stale-edit protection, and audit events were all tested.

Bank and wallet statements can be imported safely from CSV/XLSX. Unsafe or ambiguous files are rejected, rows are staged with per-row errors, and possible duplicates must be reviewed. Selected rows are then posted exactly once to an append-only ledger. Staged rows are never treated as cash.

No balances, reconciliation, analysis, forecast, approval workflow, or lender connectivity exists yet.
