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
| 05 | Trusted Cash Reconstruction | **Done — verified locally** |
| 06 | Receivables and Payables Ledger | **Done — verified locally** |
| 07 | Financial Health | **Done — verified locally** |
| 08 | Cash Outlook | **Done — verified locally** |
| 09 | Smart Alerts | **Done — verified locally** |
| 10 | Payment Review | **Done — verified locally** |
| 11 | Decision-Impact Evidence Queue | **Done — verified locally** |
| 12 | Funding Matches | **Done — verified locally** |
| 13–22 | Cash Gap Rescue Plan through Region Pack | Not started |

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

## Prompt 05 — Trusted Cash Reconstruction

### What was built

| Area | What exists | Files |
| --- | --- | --- |
| Schema | Alembic revision `0006_cash_reconstruction` adds four insert-only tables, each tied by composite foreign keys to an account (or ledger transactions) of the **same** business:<br>• `balance_anchor`: a statement/count balance at the **end** of a business date, in signed minor units with currency, source kind, and reference. At most one active anchor per account per day.<br>• `coverage_period`: dates whose movements are declared complete, optionally backed by a posted import.<br>• `restricted_fund`: unspendable money with an effective date range.<br>• `own_transfer`: both sides of a transfer between own accounts, or one side when the other is missing.<br>A shared trigger rejects DELETE and any UPDATE except setting the void fields **once** (time, user, reason), so corrections are visible history, never silent edits. Partial unique indexes stop a ledger transaction from being in two active transfers | [backend/migrations/versions/0006_cash_reconstruction.py](backend/migrations/versions/0006_cash_reconstruction.py) |
| Reconstruction (calculation `cash-recon-v1`, pure function) | **Reconciliation:** for each pair of consecutive anchors, residual = opening + posted movements in (d0, d1] − closing. The tolerance is exactly zero. Status is **balanced** only when the residual is 0 **and** coverage spans every date. A zero residual with gaps is **"balanced_incomplete"**, with the gap dates listed: a zero residual never implies complete coverage. A non-zero residual is **"residual"**, with the exact amount.<br>**Verified balance:** the latest anchor plus posted movements through the end of the contiguous coverage after it (`evidence_through`). Posted movements after that date are shown as **uncovered** and are not added.<br>**Freshness:** fresh if verified within **2 days** of the as-of date; otherwise stale. No anchor → the balance is **unknown**.<br>**Usable** = verified balance − active restricted funds.<br>**Totals:** per currency, and they add up **only fresh accounts with a known balance**. Stale and missing accounts are listed by name and never summed. Currencies are never combined.<br>**Transfers:** suggested pairs (same amount and currency, different accounts, ≤ 3 days apart, one-to-one) are only suggestions until confirmed. One-sided declarations show as **unmatched** with the missing side named.<br>**Account status:** Verified / Stale / Missing balance / Needs review (residual, coverage gap, unmatched transfer, or restriction above balance). Positions can be computed **as of** any past date | [backend/app/modules/cash/service.py](backend/app/modules/cash/service.py) |
| API and validation | `GET …/cash/position?as_of=` (as_of between 2000-01-01 and business today).<br>`POST …/cash/anchors`, `…/coverage`, `…/restrictions`, `…/transfers`, and `POST …/cash/{kind}/{id}/void` with a required reason.<br>**Anchors:** date not in the future (business timezone) and not before 2000; integer minor units (floats, strings, out-of-range refused); currency must equal the account's (no conversion); a known source kind; a reference is required; a second anchor on the same day → 409 (void first).<br>**Coverage:** start ≤ end ≤ today; at most about a year; an import-backed period must use a **posted** import of the **same account** and include all of its rows' dates.<br>**Restrictions:** amount > 0, account currency, reason required, end ≥ start.<br>**Transfers:** out side negative, in side positive, different accounts, same currency, equal amounts, ≤ 5 days apart; a transaction already in a transfer → 409. Foreign accounts, anchors, and transactions → 404 | [backend/app/modules/cash/router.py](backend/app/modules/cash/router.py) |
| Permissions and audit | New role action `cash.manage` (owner, bookkeeper). Staff can read positions but not change them. Audited: anchor add, coverage add, restriction add, transfer add, every void (with its reason), and denials | [policy.py](backend/app/modules/identity/policy.py) |
| UI (Records mockup) | The **Records & reconciliation** page now has:<br>• **Usable cash by currency**: spendable total, restricted amount, and "Not included: … (stale) / (no balance)".<br>• **Connected records** with *Last verified*, *Usable*, and status badges (Verified / Stale / Missing balance / Needs review), as in the mockup.<br>• **Items to review**, combining staged imports, account issues, and transfer suggestions.<br>**Account page:** KPI cards (verified balance with date, restricted, usable, uncovered movements), "What needs attention", a reconciliation-periods table (opening, movements, closing, residual, gaps, status), and anchors, coverage, and restrictions, each with add forms and void/release with a reason (owner/bookkeeper only).<br>**Transfers page:** suggested pairs with "Link as transfer", declared transfers (matched/unmatched) with void, and a one-sided declaration | [frontend/src/cash/](frontend/src/cash/), [frontend/src/records/RecordsPage.tsx](frontend/src/records/RecordsPage.tsx) |
| Expected values | Hand-checked worked examples for all four cases | [fixtures/cash_reconstruction_expected.md](fixtures/cash_reconstruction_expected.md) |
| Test helper | `tests/helpers.py` creates a fresh business with the three fixture accounts and posts all eight Northstar fixture transactions **through the real import API**. Later prompts reuse it | [backend/tests/helpers.py](backend/tests/helpers.py) |

### Evidence (run on 20 Oct 2026)

- `alembic upgrade head` → `0005_records_import -> 0006_cash_reconstruction`. `/api/health/ready` → 200 at `0006_cash_reconstruction`.
- `pytest -q` → **209 passed**: 181 earlier plus 28 new in [backend/tests/test_cash_reconstruction.py](backend/tests/test_cash_reconstruction.py), with exact values from the expected-values file. (One older test, ledger TRUNCATE, was updated because the new foreign keys now refuse TRUNCATE before the trigger does. The ledger is still unchanged afterwards.)
  - **Balanced period:** bank 18,500,000 + (−2,275,025 over 4 transactions) = 16,224,975. Residual **0**, no gaps → **balanced**. Fresh (age 1). Usable **14,224,975** after the 2,000,000 restriction.
  - **Missing period:** wallet residual **0**, but coverage stops on 9 Oct → **balanced_incomplete** with gap **10–15 Oct**, status *Needs review*, message "…completeness is not proven".
  - **Stale anchor:** cash box verified only through **20 Sep**, age **26** → **stale**. T006 (−350,000) is reported as **1 uncovered movement**, and the cash box is **not** in the spendable total.
  - **Unmatched transfer:** the system suggests exactly **T004 → T005 (1,000,000)**. Declaring T004 alone → **unmatched, incoming side missing, 1,000,000**, and the bank is flagged. Linking both sides while T004 is still declared → 409. After voiding and linking: **matched**, no suggestion, bank *Verified*.
  - **Spendable BDT** = **20,525,010** (bank 14,224,975 + wallet 6,300,035), restricted 2,000,000. Also: a wrong closing gives residual **24,975**; accounts with no anchor are listed as missing with an unknown balance; four movements after a lone anchor are uncovered and not verified; an as-of date limits what counts.
  - **Validation (≈30 cases):** future, pre-2000, foreign-currency, float, and string anchors; same-day duplicate → 409; void requires a reason and works only once; bad and over-long coverage periods; an import from another account or not including its rows; zero or negative restrictions and bad dates; every transfer rule.
  - **Permissions and data integrity:** staff → 403 on all changes but can read; a bookkeeper can add. Another business → 404 on the position, foreign accounts, anchors, and transactions. Direct SQL UPDATE/DELETE on anchors, coverage, and transfers is rejected by the trigger. Interval merge/gap unit tests.
- **Browser run (real UI, demo business; today is 20 Oct 2026):**
  - Amina entered a balance dated 2999-01-01 and got the server error "must be between 2000-01-01 and today…". She then entered 01 Oct BDT 1,85,000.00 and 15 Oct BDT 1,79,549.75.
  - The period showed **"Zero residual, coverage incomplete"** with gap 02–15 Oct. After declaring coverage 02–15 Oct it became **Balanced** (movements −5,450.25 over 6 rows, residual 0.00).
  - A BDT 20,000 security deposit gave usable **BDT 1,59,549.75**.
  - The wallet got a 30 Sep balance (→ **Stale**). The bank's "Transfer to wallet" was declared one-sided → **Unmatched · incoming side missing**.
  - The Records page then showed bank *Needs review*, wallet *Stale*, cash box *Missing balance*. **Spendable now BDT 0.00**, because no account is verified within 2 days, and all three are listed as not included. Items to review listed all three.
  - Chaya (staff) saw the account page without any entry forms.
  - Screenshots checked on desktop and at 390×844. A small fix was made after review: links styled as buttons are no longer underlined.

### Known gaps and risks

- The freshness threshold (2 days) and the exact-zero tolerance are fixed constants, not per-business settings.
- Verified balances rely on people declaring coverage honestly. The system cannot prove a statement is complete; it only refuses to assume completeness.
- Transfer suggestions need exact amounts and ≤ 3 days. Transfers with fees (different amounts) must be linked manually; that is allowed only when amounts are equal, so fee-bearing transfers cannot be linked yet.
- Restricted funds are not checked against overlapping restrictions. Voided entries remain visible only in the audit trail, not on the account page.
- Demo database state after the browser run: Northstar bank anchors 01/15 Oct, coverage 02–15 Oct, a BDT 20,000 restriction, a wallet anchor of 30 Sep, and one unmatched "Transfer to wallet" (the wallet side was never imported).

## Prompt 06 — Receivables and Payables Ledger

### What was built

| Area | What exists | Files |
| --- | --- | --- |
| Schema | Alembic revision `0007_obligations` adds three tables:<br>• `party`: customer or supplier, unique name per business ignoring case.<br>• `obligation`: a customer invoice or supplier bill, holding direction, party, document number, issue and due dates, amount in minor units, currency, category, description, **source reference**, optional expected settlement account, **dispute flag + reason**, and cancellation.<br>• `allocation`: append-only. A payment is positive and may link to a posted bank transaction; a **reversal** is the exact negative of one earlier payment, linked to it, at most once.<br>A trigger makes an obligation's **original terms immutable**: only the dispute fields, cancellation (once), and settlement account can change, and DELETE is refused. Allocations reject UPDATE/DELETE/TRUNCATE. Database checks: amounts > 0 and ≤ 10^15; due ≥ issue and within 5 years; category valid for the direction; reversal shape; consistent dispute and cancel fields. Composite foreign keys keep parties, obligations, accounts, and bank transactions inside one business. Document numbers are unique per business + direction + party, ignoring case | [backend/migrations/versions/0007_obligations.py](backend/migrations/versions/0007_obligations.py) |
| Exact outstanding amounts | Outstanding = original amount − sum(allocations), with reversals negative. Status: open / partly paid / paid / cancelled. Overdue and days overdue are computed against the business date. **Customer dues**: due this week / overdue 1–30 / overdue 30+ / due later, with **disputed amounts on their own line and never inside the buckets**, plus overdue count and customers. **Upcoming supplier payments**: outstanding bills due within 30 days, overdue first. All per currency; currencies are never mixed | [backend/app/modules/obligations/service.py](backend/app/modules/obligations/service.py) |
| Over-allocation protection | The obligation row is locked (`FOR UPDATE`) before outstanding is computed, so concurrent payments cannot together exceed it. A payment linked to a bank transaction must have the right sign (receivable = money in, payable = money out), the same currency, and the transaction's own date. The transaction row is also locked, and the total allocated against it (net of reversals) cannot exceed its amount. Over-allocation attempts → 409 with the amount still available, and are audited as conflicts | [backend/app/modules/obligations/router.py](backend/app/modules/obligations/router.py) |
| API and validation | `GET …/obligations` (optional direction, as_of), `GET …/summary`, `GET …/{id}` (with allocations), `POST …/obligations`, `POST …/{id}/allocations`, `POST …/{id}/allocations/{aid}/reverse`, `POST …/{id}/dispute`, `POST …/{id}/cancel`.<br>**Obligations:** strict models (unknown fields refused); integer minor units only; supported currency; category must match the direction; issue date not in the future and not before 2000; due ≥ issue; text is NFC-cleaned, length-limited, and **formula-looking text is refused**; the settlement account must belong to the business and match the currency. Duplicate number for the same party → 409.<br>**Disputes:** need a reason; clearing must not have one; a repeat → 409.<br>**Reversals:** dated between the payment date and today; a second reversal → 409; a reversal of a reversal → 409.<br>**Cancel:** only with nothing net paid; once only; no payments afterwards | same |
| Permissions and audit | New role actions `obligations.read` (all roles) and `obligations.write` (owner, bookkeeper). Audited: create, allocate (success / over-allocation conflict), reverse (with reason), dispute set/clear, cancel, and denials | [policy.py](backend/app/modules/identity/policy.py) |
| UI | **Invoices & bills** page (from Records):<br>• **Customer dues** card styled after the Financial Health mockup (due this week, overdue 1–30, overdue 30+, due later, disputed shown separately, total).<br>• **Upcoming supplier payments** list styled after the Cash Outlook "Upcoming movements".<br>• Customer invoices / supplier bills tabs with status, "Nd overdue", and "Disputed" badges.<br>• A **New invoice or bill** form with field-level server errors.<br>**Detail page:** KPI cards (original amount "never changed", paid net of reversals, outstanding, details), a payments-and-reversals table, a **Record payment** form (optional link to an eligible posted bank transaction, which sets the date automatically), **Reverse** with a reason, **Mark as disputed / Clear dispute**, and **Cancel** (only when nothing is paid). Staff see everything read-only | [frontend/src/obligations/](frontend/src/obligations/) |

### Evidence (run on 20 Oct 2026)

- `alembic upgrade head` → `0006_cash_reconstruction -> 0007_obligations`.
- `pytest` → **42 new tests passed** in [backend/tests/test_obligations.py](backend/tests/test_obligations.py). The full suite was re-run after Prompt 07 (see there).
  - **Partial payment:** a 5,000,000 invoice paid 2,000,000 → partly paid, 3,000,000 outstanding. A 3,000,001 payment → 409 with `outstanding_minor 3,000,000` and an audited conflict. Paying 3,000,000 → paid, 0 outstanding. Paying 1 more → 409. The original amount stays 5,000,000.
  - **Disputed invoice:** marking without a reason → 422; marking twice → 409. The summary shows **disputed 5,000,000 and overdue 1–30 = 0**. A partial payment is still allowed. After clearing: disputed 0, overdue 1–30 = 4,000,000.
  - **Reversal:** a reversal dated before the payment → 422. Reversing 2,000,000 → outstanding back to 5,000,000 with the amount unchanged. Entries: `payment 2,000,000 (reversed)` + `reversal −2,000,000`. Reversing twice → 409; reversing a reversal → 409.
  - **Concurrent over-allocation:** 8 parallel payments of 300,000 on a 1,000,000 invoice → **exactly 3 × 201 and 5 × 409**, final paid 900,000 and outstanding 100,000.
  - **Bank links:** linking to fixture T001 (+6,500,000) requires its date. A payable cannot use money in. Two invoices of 4,000,000 against T001 → the second → 409 with `available 2,500,000`, then 2,500,000 succeeds. A supplier bill settled by T002. A foreign transaction → 404.
  - **Buckets** (as of 16 Oct): due this week 100, overdue 1–30 = 200, 30+ = 400, later 800, disputed 1,600, total 3,100; 3 overdue items from 1 customer. Upcoming payables 120, of which 70 overdue, with the item outside 30 days excluded.
  - **Validation and permissions:** 19 invalid obligation inputs and 7 invalid payment inputs → 422 with nothing stored; cancel rules. Staff → 403 on writes but can read; a bookkeeper can pay. Another business → 404 everywhere. Direct SQL changes to amount or due date, deleting an obligation, and changing or deleting an allocation are all rejected by triggers.
- **Real bug found by the tests and fixed:** PostgreSQL `sum()` over bigint returns `numeric`, so the "transaction has only N left" 409 failed to serialize (500). It is now converted to an integer.
- **Browser run (real UI, demo business):**
  - **Invoice entry:** Amina entered INV-104 with a due date before the issue date → the server error "due_date must not be before issue_date" appeared under the field. The corrected invoice opened its detail page.
  - **Payment and reversal:** a BDT 20,000 part payment → outstanding BDT 30,000. A BDT 40,000 payment was blocked ("More than the outstanding amount"). **Reverse** with the reason "Payment sent to wrong invoice" → outstanding back to BDT 50,000, and the original payment row is shown as "Payment (reversed)".
  - **Demo data:** further invoices and bills were entered through the API as Amina (audited). INV-101 and BILL-201 were settled by linking the posted bank transactions, and INV-099 was disputed.
  - **Records page:** Customer dues = this week BDT 50,000, overdue 1–30 BDT 56,000, 30+ BDT 0, later BDT 47,000, **disputed BDT 26,000 separately**, total BDT 1,79,000. Upcoming supplier payments listed BILL-210, PAY-OCT, RENT-NOV, LOAN-0507 (BDT 1,96,000).
  - **Staff:** Chaya had no "New" button, and an API create → 403.
  - **UI fix after review:** a stale payment-form error now clears when the balance changes.
- **Housekeeping (honest note):** my Prompt 06 browser script accidentally re-ran the Prompt 05 script, adding a second BDT 20,000 restriction and a duplicate coverage period to the demo bank (duplicate balances were refused with 409). Both duplicates were **voided through the API as Amina** with the reason "Duplicate entry created by a test script re-run", which is visible in the audit trail. The demo again has one restriction and one coverage period.

### Known gaps and risks

- No credit notes or partial write-offs: an obligation can only be paid, reversed, disputed, or cancelled (when nothing is paid).
- Payment-to-transaction matching is manual. There are no automatic suggestions, and a payment cannot be split across multiple bank transactions in one step (add two payments).
- Party records hold only a name. There are no contacts yet (Prompt 18 needs verified contacts).
- No reminders are drafted and no forecast is produced from these obligations (out of scope: Prompts 08 and 18).

## Prompt 07 — Financial Health

### What was built

| Area | What exists | Files |
| --- | --- | --- |
| Schema | Alembic revision `0008_classification` adds `txn_classification`, how a **posted** transaction is interpreted. The kind is one of operating receipt, operating payment, financing received/repaid, owner contribution/drawing, or other non-operating; operating kinds also have a category. The ledger itself stays append-only. A database check ties categories to kinds, and a composite foreign key keeps each classification inside the transaction's business | [backend/migrations/versions/0008_classification.py](backend/migrations/versions/0008_classification.py) |
| Cash-basis summary (calculation `fh-v1`) | For a month and currency, from posted records only:<br>• **operating receipts**, **operating payments**, and **net operating cash**, each by category;<br>• **operating cash margin** in basis points, with exact integer rounding half away from zero. It is shown only when there are operating receipts; otherwise it is "not available" with the reason;<br>• a **6-month trend**;<br>• customer dues and upcoming bills from Prompt 06.<br>**Own-account transfers** (Prompt 05) are excluded from every operating figure automatically and cannot be classified. **Financing and owner money** are reported under "not counted as revenue or expense". **Unclassified** records are counted and marked as incomplete. Source-coverage gaps (Prompt 05) are listed per account. A deterministic **"What changed"** sentence is built from the numbers; no AI computes or writes any figure | [backend/app/modules/health_summary/service.py](backend/app/modules/health_summary/service.py) |
| Links to source records | Every category, total, and excluded line carries its transaction IDs. `GET …/financial-health/transactions?month&kind&category` returns the exact posted rows with account, reference, source import, and row number | [backend/app/modules/health_summary/router.py](backend/app/modules/health_summary/router.py) |
| Classification API | `GET …/classifications` (optional month and unclassified filter), with **deterministic suggestions** from Prompt 06 payment links: a bank receipt that paid a sales invoice → operating receipt · sales; a loan-repayment bill → financing repaid. `POST …/classifications` is an all-or-nothing batch of 1–200 items with checks:<br>• the sign must match the kind (a receipt needs money in, a payment money out);<br>• the category must be valid for the kind; financing and owner kinds take no category;<br>• no duplicate IDs;<br>• an own-account transfer → 409;<br>• another business's transaction → 404.<br>New role action `ledger.classify` (owner, bookkeeper). Every batch is audited with from→to kinds | same |
| Validation | `month` must be `YYYY-MM` between 2000-01 and the current business month; currency must be supported; unknown drill-down kinds → 422 | same |
| UI (from `images/03-financial-health.png`) | **Financial health** page:<br>• month picker and the "cash-basis, not an audited P&L" notice;<br>• an **incomplete** banner when records are unclassified;<br>• KPI cards: operating receipts, payments (with change vs last month), operating cash margin, overdue invoices with disputed shown separately. Clicking a card opens its source records;<br>• a **receipts vs payments** grouped bar chart with hover tooltip, legend, and "Show table";<br>• **What changed**, customer dues, a by-category table (each row opens its records), and "Not counted as revenue or expense";<br>• a coverage warning, a **source-records drill-down**, and a **Classify posted records** table with suggestions (owner/bookkeeper only).<br>Chart colours: the validated categorical pair blue `#2347A5` / orange `#eb6834` (dataviz validator: all checks pass on the white surface, CVD ΔE 28.3) | [frontend/src/health/FinancialHealthPage.tsx](frontend/src/health/FinancialHealthPage.tsx), [frontend/src/charts/Charts.tsx](frontend/src/charts/Charts.tsx) |
| Expected values | Hand-checked worked example of the fixture's October summary | [fixtures/financial_health_expected.md](fixtures/financial_health_expected.md) |

### Evidence (run on 20 Oct 2026)

- `alembic upgrade head` → `0007_obligations -> 0008_classification`.
- Full suite after Prompts 05–07: **272 passed** (`pytest -q`). `/api/health/ready` → 200 at `0008_classification`; `npm run build` passes.
- **21 new tests passed** in [backend/tests/test_financial_health.py](backend/tests/test_financial_health.py):
  - **Totals against the independent fixture:** the test reads the fixture JSON's own expected values. Operating receipts **8,349,975**, payments **8,925,015**, net **−575,040**, margin **−689 bp**. Categories: rent 4,500,000 / inventory 4,075,015 / other 350,000 and sales 8,349,975. Own transfers in 1,000,000 / out 1,000,000 excluded. Complete with 8 records.
  - **Source links:** sales drill-down = exactly T001 + T003 (8,349,975, each with its import batch and row); rent = T007; all payments = 8,925,015; own transfers = T004 + T005.
  - **Financing proceeds are not revenue:** a +10,000,000 loan wrongly classified as sales raised receipts to 18,349,975. Reclassified as financing received, receipts went back to **8,349,975**, with financing in 10,000,000 reported separately.
  - **Incomplete and suggestions:** with nothing classified, 8 unclassified (in 9,349,975 / out 9,925,015) → complete = false, the margin is unavailable with its reason, the "What changed" text says the figures are incomplete, and coverage gaps are listed. The 6-month trend runs May–Oct, and "What changed" reads "rose by BDT 83,499.75 compared with 2026-09…". A suggestion comes from an invoice payment ("Paid INV-101").
  - **Classification rules and access:** 8 classification cases (sign, category, and kind rules); an all-or-nothing batch (one bad item → nothing saved); duplicate IDs, empty batch, own transfer → 409, a foreign transaction → 404, staff → 403, a bookkeeper allowed.
  - **Cross-business isolation:** another business gets 404 on the summary and drill-down, its own summary has 0 records (nothing leaks), and staff can read.
  - **Month validation:** 5 invalid months → 422.
- **Browser run (real UI, demo business):**
  - **Before classifying:** Financial health for Oct 2026 showed the **Incomplete** banner: "5 posted transaction(s) … not classified (BDT 86,500.00 in, BDT 81,950.25 out)". The margin was "Not available". Two suggestions were pre-filled ("Paid INV-101", "Paid BILL-201"). The transfer to the wallet showed as an own-account transfer.
  - **After choosing rent, utilities, and sales and saving:** receipts **BDT 86,500.00**, payments **BDT 81,950.25**, margin **5.26%** (net BDT 4,549.75), exactly as hand-computed.
  - Categories: sales 86,500 / rent 45,000 / inventory 32,750.25 / utilities 4,200. "Own-account transfers: out BDT 10,000.00" is listed as not counted. "View source transactions" listed INV-101 (row 2) and INV-103 (row 4).
  - Staff saw the same figures with no Save button.
- **Fixes made after reviewing the screenshots:**
  - The overdue-invoices card counted a disputed customer, while its amount excluded disputes. The counts now exclude disputed items, matching the buckets; the test was updated.
  - The chart now has the wide column.
  - A table total row inherited a flex layout and broke on mobile.
  - Axis labels are now readable on phones.

### Known gaps and risks

- Classification is manual apart from invoice-link suggestions. There are no rules engine and no learned categorisation (by design: no AI arithmetic or guessing).
- Monthly figures use business dates. There are no custom date ranges or quarter/year views yet.
- Trends compare calendar months only. The "change vs last month" percentage is a display calculation in the browser; the server figures are exact.
- Months with posted movements but incomplete coverage are flagged, not blocked.

## Prompt 08 — Cash Outlook

### What was built

| Area | What exists | Files |
| --- | --- | --- |
| Schema | Alembic revision `0009_forecast_snapshot` adds `forecast_snapshot`: business, calculation version, as-of business date, the **frozen inputs** (JSON), their **SHA-256**, the results, creator, and time. It is append-only (triggers reject UPDATE, DELETE, and TRUNCATE), so every figure shown can be traced to and recomputed from the exact inputs that produced it | [backend/migrations/versions/0009_forecast_snapshot.py](backend/migrations/versions/0009_forecast_snapshot.py) |
| Frozen input snapshot | Built from accepted records only:<br>• per-account usable balance, freshness, verified date, and uncovered posted movements (Prompt 05);<br>• restricted funds ending inside the horizon;<br>• **outstanding** invoices and bills (Prompt 06: partly paid → only the remainder; paid or cancelled → excluded);<br>• the reserve and base currency;<br>• unmatched transfers.<br>Nothing outside this object affects the result | [backend/app/modules/outlook/service.py](backend/app/modules/outlook/service.py) |
| Engine (calculation `outlook-v1`, pure) | **Daily closing balances for 30 days and weekly closes and minimums for 13 weeks**, computed **per account** and summed **per currency** (currencies are never combined). The reserve line applies to the base currency.<br>**Expected path:**<br>• opening includes fresh and stale balances (stale is flagged);<br>• receivables arrive on max(due, tomorrow);<br>• overdue receivables are assumed collected in 7 days and marked **uncertain**.<br>**Cautious path:**<br>• a stale positive balance is not relied on;<br>• receivables arrive **14 days late**;<br>• overdue receivables are left out.<br>**Both paths:**<br>• payables on max(due, tomorrow), disputed bills still counted;<br>• **disputed receivables are not counted**;<br>• accounts with no balance are excluded and reported;<br>• restriction releases are added back.<br>No future own-account transfer is modelled and only outstanding amounts are used, so nothing is counted twice. Every event names its source record (invoice/bill or restriction) and says when an account was **assumed**. Outputs: lowest point (13 weeks and 30 days) and **first reserve breach** (date, balance, gap) per path, and an **evidence list** (stale, missing, uncovered, disputed, assumed account, unmatched transfer, currency without an account) | [backend/app/modules/outlook/engine.py](backend/app/modules/outlook/engine.py) |
| API | `POST …/cash-outlook/snapshots` (new role action `outlook.run`: owner, bookkeeper; audited).<br>`GET …/latest`, `GET …/snapshots`, `GET …/snapshots/{id}` (records.read). Each response reports whether today's inputs still match the snapshot (hash compare) and whether it is today's.<br>`GET …/snapshots/{id}/verify` recomputes the stored results from the stored inputs and checks the hash. A snapshot of another business → 404 | [backend/app/modules/outlook/router.py](backend/app/modules/outlook/router.py) |
| UI: layout from `images/04-cash-outlook.png`, styling from `design.md` | **Top of the page, matching the mockup:**<br>• title and subtitle;<br>• a **warning banner** ("!" box, "Reserve shortfall projected for 21 October …", then "<path> balance · owner reserve · gap" in warning colour), or a success banner when there is no shortfall;<br>• a **13-week cash forecast** card with **Expected / Cautious legend pills** in the header and a **time-series line chart on a true date axis**: the as-of date, every day for the next 30 days, then each week's closing balance to week 13 (19 Jan). Points sit at their real dates, with date ticks every 2 weeks (every 4 on phones), point markers on week ends, an x-axis caption, and an orange **"Reserve floor"** line;<br>• an **Upcoming movements** card: date, bold party and number, and the amount right-aligned in green **+** / red **−**, each linking to its invoice or bill. **Uncertain** receipts are marked; disputed ones are listed as "Not counted".<br>**Chart styling (design.md):** 2px solid `#2347A5` for the expected path, dashed `#2876B8` for the cautious forecast, `#B76E00` (warning) for the reserve floor, no area fill. The axis carries the "BDT '000" currency identifier. Direct end labels, square vs round markers, a crosshair tooltip, and "View as table" are also there.<br>**Below the mockup layout** (required by the prompt, without changing the layout above): a **Daily balances · next 30 days** table with figures below the reserve in red; **By account** lowest expected and cautious per account; **Stale or missing evidence**; **How this was calculated** (time, user, as-of, `outlook-v1`, inputs SHA-256, rules) with **Verify figures** and **Recalculate**. A bar appears when records changed since the snapshot. Recalculate is for owner/bookkeeper only | [frontend/src/outlook/CashOutlookPage.tsx](frontend/src/outlook/CashOutlookPage.tsx), [frontend/src/charts/Charts.tsx](frontend/src/charts/Charts.tsx) |
| Hand-checked fixture | Frozen inputs with one **on-time** (INV-1), one **late** (INV-2, overdue), and one **missing** (INV-3, disputed) receipt, two payables, a stale wallet, an account with no balance, and a restriction release. Every daily, weekly, lowest, and breach value is worked by hand | [fixtures/cash_outlook_case.json](fixtures/cash_outlook_case.json), [fixtures/cash_outlook_expected.md](fixtures/cash_outlook_expected.md) |

### Evidence (run on 20 Oct 2026)

- `alembic upgrade head` → `0008_classification -> 0009_forecast_snapshot`. `/api/health/ready` → 200 at `0009_forecast_snapshot`.
- `pytest -q` → **285 passed**: 272 earlier plus 13 new in [backend/tests/test_cash_outlook.py](backend/tests/test_cash_outlook.py).
  - **Opening:** expected **11,000,000**, cautious **10,000,000** (the stale wallet contributes 0 on the cautious path; the cash box with no balance is left out).
  - **On time / late / missing:** INV-1 expected 25 Oct, cautious 8 Nov. INV-2 expected **27 Oct (uncertain)**, cautious **left out**. INV-3 (disputed) **left out of both**. The overdue rent is paid on 21 Oct with Bank assumed. The restriction release is on 15 Nov.
  - **Exact daily values:** 21 Oct 9,500,000 / 8,500,000; 25 Oct 14,500,000 / 8,500,000; 27 Oct 10,500,000 / **1,500,000**; 8 Nov 10,500,000 / 6,500,000; 19 Nov 11,000,000 / 7,000,000. 30 daily points, starting 21 Oct.
  - **Exact weekly values:** W1 (27 Oct) 10,500,000 / min 9,500,000 / 1,500,000 / 1,500,000 … W4 11,000,000 / 10,500,000 / 7,000,000 / 6,500,000 … W13 (19 Jan) 11,000,000 / 7,000,000.
  - **Lowest and breach:** expected lowest 9,500,000 on 21 Oct with **no breach**. Cautious lowest 1,500,000 on 27 Oct, with **first breach 27 Oct, gap 6,500,000**.
  - **Per-account paths and evidence** are exact. Currencies stay separate (a USD account is never added to BDT; a EUR invoice with no EUR account is reported). The engine is deterministic.
  - **Snapshot through the API:** a fresh balance of 9,000,000, a bill −2,000,000 on day 3, an invoice +4,000,000 on day 5 (cautious day 19) → day 3 7,000,000, day 5 11,000,000 / 7,000,000, cautious breach on day 3 with gap 1,000,000. **Verify** → inputs hash matches and results reproduce.
  - **Frozen:** after the invoice is paid, the stored snapshot's figures are unchanged, its status says inputs changed, and a new snapshot no longer counts the paid invoice. A partly paid bill counts only its 1,500,000 remainder.
  - **Permissions and integrity:** staff → 403 on calculate but can read; a bookkeeper can calculate. Another business → 404 on latest, snapshot, and verify. SQL UPDATE/DELETE → "append-only". One audit event per snapshot. A business with no balances → no paths and three "missing balance" notes, never a guessed figure.
- **Browser run (real UI, demo business, today 20 Oct 2026):**
  - Chaya (staff) first saw "No outlook has been calculated yet. An owner or bookkeeper can calculate one", with no calculate button.
  - Amina pressed **Calculate outlook**. The demo's bank (verified 15 Oct) and wallet (30 Sep) are stale, so:
    - **expected path:** opens at BDT 2,02,050.25, lowest **BDT 1,59,050.25 on 5 Nov** (above the BDT 80,000 reserve);
    - **cautious path:** opens at BDT 0, lowest **−BDT 1,81,000.00 on 31 Oct**;
    - banner: "Shortfall on the cautious path from 21 Oct 2026 … does not rely on stale balances; the expected path stays above the reserve".
  - All match a hand calculation (bank-only lowest BDT 1,16,549.75).
  - Upcoming movements listed INV-104 (assumed account; cautious 5 Nov), BILL-210, INV-108 (**Uncertain**, overdue 15 days), PAY-OCT, INV-105, RENT-NOV, LOAN-0507, and INV-099 "Not counted" (disputed).
  - Evidence listed the two stale balances, the missing cash box, the unmatched transfer, the disputed invoice, and the assumed account.
  - **Verify figures** → "Verified: the stored inputs match their hash and recalculating them gives exactly the same figures". The chart tooltip at W2 showed Expected BDT 1,74,050.25 / Cautious −BDT 1,81,000.00 / Reserve BDT 80,000.00.
  - Chaya then saw the outlook with no Recalculate button.
- **Fixes made during the work:**
  - **Engine bug:** a bill with no expected account was missing its "assumed account" evidence note.
  - **Banner wording:** it now names both cautious reasons (late receipts and stale balances).
  - **Chart:** ticks are now round (−200k…300k); the last x-labels no longer collide on mobile; the zero tick shows "0".
  - Screenshots checked on desktop and at 390×844.
- **UI redesign after user review (20 Oct 2026):** the first version had added a separate 30-day chart toggle, used colours that matched neither the mockup nor design.md, and let some amounts wrap.
  - **Layout:** the page was rebuilt on the mockup's layout with design.md styling, and the 30-day view became a table below.
  - **Colours:** the dataviz validator shows design.md's solid `#2347A5` vs dashed `#2876B8` are too close to tell apart by colour alone (normal-vision ΔE 13.0 < 15). As design.md intends, the two paths are therefore told apart by **line style** (solid vs dashed), **marker shape** (round vs square), direct end labels, and legend pills, not by colour. The reserve colour `#B76E00` passes all checks against `#2347A5`.
  - **Time series (user request, 20 Oct 2026):** following design.md, the chart was changed from week labels ("Now, W2…W13") to a true time series. Dates are on the x-axis; the 30 daily points show the 27 Oct payroll dip, and weekly points continue to 19 Jan. The tooltip shows the full date ("Tue, 27 Oct 2026 · Expected BDT 1,72,050.25 · Cautious −BDT 1,36,000.00"). On phones the chart is taller and uses fewer date ticks so labels never overlap.
  - **Layout audit:** an automated audit of **all 14 pages at 1440px and 390px** (page overflow, content spilling out of cards, clipped text, money or short labels wrapping) found and fixed amounts splitting across lines in the outlook banner and the Financial Health "not counted" list. Only ordinary sentences wrap now.
- `audit.verify` on the demo database → `Audit chain OK: 558 events checked`. The demo now has one outlook snapshot.

### Known gaps and risks

- The assumptions are fixed for `outlook-v1` (14-day cautious delay, 7-day assumed collection of overdue invoices). They are not per-business settings yet; a change needs a new calculation version.
- Daily figures are closing balances. Intraday order (money out before money in) is not modelled; Payment Review (Prompt 10) needs that.
- No recurring costs (payroll, rent) are generated automatically. Only entered bills appear, so later weeks may look better than reality if bills are not yet entered.
- No future own-account transfers are modelled, so a shortfall in one account is not covered by another account's cash.
- The outlook does not create alerts (Prompt 09) or run payment checks (Prompt 10). It only shows the projected breach.

## Prompt 09 — Smart Alerts

### What was built

| Area | What exists | Files |
| --- | --- | --- |
| Schema | Alembic revision `0010_alerts`: `alert` (type, severity, status open/resolved, dedup key, date, amount, currency, cause, supporting record IDs, details, detection count, first/last run) and `alert_run` (as-of date, rules version, SHA-256 of the frozen inputs, created/updated/unchanged/resolved counts, notification text). A **partial unique index allows one open alert per (business, dedup key)**; `alert_run` is append-only (triggers) | [backend/migrations/versions/0010_alerts.py](backend/migrations/versions/0010_alerts.py) |
| Rules (`alerts-v1`, pure) | Frozen inputs in, alert candidates out:<br>• **Reserve breach:** the Cash Outlook (recomputed from its frozen inputs) closes below the reserve. High if the expected path breaches, medium if only the cautious path does. Date = first breach, amount = gap.<br>• **Overdue invoice:** outstanding, not disputed, past due. Low 1–7 days, medium 8–30, high over 30. Amount = outstanding.<br>• **Unusual outflow:** a posted outflow in the last 7 days at least 3× the median of the previous 90 days of outflows in that currency (needs 5+ earlier outflows). Own-account transfers and payments already allocated to a bill are excluded. Medium; high at 10× or more.<br>• **Repayment pressure:** an outstanding `loan_repayment` bill due within 30 days that is overdue (high) or whose due day closes below the reserve after paying it (high on the expected path, medium on the cautious path only).<br>Cause texts contain no money amounts; the amount is its own field | [backend/app/modules/alerts/engine.py](backend/app/modules/alerts/engine.py) |
| Run and dedup | `POST …/alerts/run` freezes inputs, evaluates, and reconciles with stored alerts under a per-business advisory lock: a new key inserts an alert; a key still open updates the **same** alert (changed figures overwrite it; `detection_count` rises); an open alert whose cause is gone is marked **resolved** (never deleted); a returning cause becomes a new alert. The run row is written once with its final counts | [backend/app/modules/alerts/service.py](backend/app/modules/alerts/service.py) |
| API | `POST …/alerts/run` (new role action `alerts.run`: owner, bookkeeper; audited as `alerts.run`), `GET …/alerts?status=open|resolved|all`, `GET …/alerts/{id}` (new `alerts.read`: all members). Another business's ID → 404 | [backend/app/modules/alerts/router.py](backend/app/modules/alerts/router.py), [policy.py](backend/app/modules/identity/policy.py) |
| External notification text | Each run that creates alerts stores one minimal text: "SME Finance Copilot: N new alert(s) need your attention. Sign in to view details." No amount, party, document, date, or cause. **Nothing is actually sent** (no email/SMS channel exists); the text is stored and returned for a future sender | [backend/app/modules/alerts/service.py](backend/app/modules/alerts/service.py) |
| UI | **Overview** is now available (layout of the Priority actions list in `images/01-overview.png`, tokens from `design.md`). Each alert card shows title, severity badge, cause, amount (labelled Shortfall for reserve breaches), date, first-seen date, and supporting-record chips linking to the invoice, bill, or account. It also shows when alerts were last checked and warns if that was not today. Owners and bookkeepers get "Check for alerts now"; staff see the list read-only. The **KPI tiles and the outlook mini-chart from the mockup are not built**; the page contains only alerts | [frontend/src/overview/OverviewPage.tsx](frontend/src/overview/OverviewPage.tsx) |

### Evidence (run on 20 Oct 2026)

- `alembic upgrade head` → `0009_forecast_snapshot -> 0010_alerts`; `/api/health/ready` expects `0010_alerts`.
- `pytest -q` → **299 passed**: 285 earlier plus 14 new in [backend/tests/test_alerts.py](backend/tests/test_alerts.py). Hand-worked case (bank 10,000,000, reserve 8,000,000):
  - **True triggers, exact:** a 3,000,000 loan repayment due 25 Oct closes that day at 7,000,000 → reserve breach **high, 25 Oct, gap 1,000,000** and repayment pressure **high, 25 Oct, 3,000,000**. A 500,000 invoice 15 days overdue → **medium, due 5 Oct**. Outflows 90k/100k/105k/110k/120k → median 105,000; a 400,000 outflow → **medium, "3.8 times"**.
  - **Non-triggers:** disputed overdue invoice, invoice not yet due, a 300,000 outflow (below 3× median), the same loan with 50,000,000 cash, only 4 baseline outflows, an outflow just outside the 7-day window; exactly 3× median does trigger.
  - **Severity bands:** overdue 1/7/8/30/31 days → low/low/medium/medium/high. A breach on the cautious path only → medium (expected 8,500,000, cautious 7,500,000, gap 500,000).
  - **No duplicate on retry (API):** first run creates 3 alerts (reserve breach 2,000,000 gap on day 3, repayment pressure, overdue invoice); two retries create 0, change 0, return the same alert IDs; the DB has exactly 3 rows with `detection_count` 3. Six **concurrent** runs create 3 alerts in total.
  - **Lifecycle:** paying the invoice resolves its alert on the next run (visible under `status=resolved`); a partial payment updates the same alert (2,500,000 → 1,500,000) instead of adding one; an outflow later allocated to a bill resolves its unusual-outflow alert.
  - **Unusual outflow through the real import path** (CSV → staged → posted), not only the pure engine.
  - **Privacy and access:** notification text has no party, document number, currency, amount, or cause; no session → 401; staff → 403 on run but can read; another business → 404 for list, read, and run with the same answer as an unknown ID; a business never sees another's data in its run; the staff refusal is audited; `alert_run` UPDATE is rejected as append-only.
- **Browser run (real UI, demo business, Amina):** the Overview showed "Alerts have not been checked yet" → **Check for alerts now** → "1 new": **High, "The expected path closes below the owner reserve on 21 Oct 2026", shortfall ৳ 36,999.00**. This equals the recomputed outlook (bank BDT 43,001.00 vs reserve BDT 80,000.00 → gap 3,699,900 minor). A second run reported 0 new, 1 unchanged. The 375px phone view has no horizontal overflow.
- `audit.verify` → `Audit chain OK: 39 events checked`. The development database holds fewer events than at the end of Prompt 08 (558); it appears to have been reset outside this work.

### Known gaps and risks

- Alerts are evaluated only when someone presses the button (or calls the API). There is no scheduler or background worker, so nothing alerts on its own and the page warns when the last check was not today.
- No acknowledge, snooze, or dismiss. An alert goes away only when its cause is gone. Unusual-outflow alerts therefore disappear after the 7-day window even if nobody looked at them.
- Thresholds (3× median, 90/7-day windows, 5-outflow minimum, 30-day repayment horizon, severity bands) are fixed for `alerts-v1`, not per-business settings. The median rule can over- or under-trigger for businesses with few or very mixed outflows.
- Repayment pressure only recognizes bills categorised `loan_repayment`. A reserve breach and a repayment alert for the same shortfall are both shown.
- The reserve-breach alert is only as good as the outlook: stale or missing balances and unentered recurring bills change it (see Prompt 08 gaps).
- No real notification channel; the minimal text is stored only. Fraud-case review and action execution are not part of this prompt.

## Prompt 10 — Payment Review

### What was built

| Area | What exists | Files |
| --- | --- | --- |
| Schema | Alembic revision `0011_payment_review`: `payment_review` stores the request, the **frozen Cash Outlook inputs** and their SHA-256, the calculation version, the four-path results, and the decision. Append-only (triggers reject UPDATE, DELETE, TRUNCATE). It has no link to any payment or action | [backend/migrations/versions/0011_payment_review.py](backend/migrations/versions/0011_payment_review.py) |
| Request | Funding account, amount (integer minor units), payment date (after today, within 13 weeks), optional payee, optional **linked payable**, and a required **owner-defined adverse case**: receipts later by N days (0–90), receipts lower by N %, and/or one extra outflow with a date. An empty adverse case is refused. The payee is stripped of control characters and spreadsheet-formula starts | [backend/app/modules/payment_review/router.py](backend/app/modules/payment_review/router.py) |
| Engine (`payment-review-v1`, pure) | One frozen input object, four paths, one currency at a time:<br>• **All on time** = the Cash Outlook expected path.<br>• **Receipts delayed** = the cautious path (receipts 14 days late, overdue receipts left out).<br>• **Largest receipt missed** = the expected path without the single largest eligible receipt.<br>• **Owner-defined adverse** = the expected path with the owner's delay, reduction (rounded down), and extra outflow.<br>Per path: minimum cash and date, first reserve breach (date, balance, gap), shortfall, assumptions, source links, and every dated event. **Within a day, outflows are applied before inflows**, so a day that closes above the reserve can still breach. The funding account is also checked for going overdrawn. A **linked payable's own event is removed** and replaced by the payment; any unpaid remainder stays on its original date | [backend/app/modules/payment_review/engine.py](backend/app/modules/payment_review/engine.py) |
| Decision | Three separate states: **arithmetic** (within reserve / shortfall / not assessed), **evidence** (complete / incomplete), and **exactly one decision**. Any breach on any path → **Reserve shortfall**, even when evidence is also incomplete (both are shown). No breach but a material unknown → **More information needed**. Neither → **Within reserve under stated assumptions**. Material unknowns: funding account balance missing or stale, any other stale balance in that currency, an unmatched own-account transfer, a disputed linked payable, no reserve defined in that currency. If the funding balance is missing, no minimum or breach is stated at all ("not assessed") rather than invented | same |
| API | `POST …/payment-reviews` (new role action `paymentreview.run`: owner, bookkeeper; computes and **saves**; audited as `paymentreview.create`), `GET …/payment-reviews`, `GET …/{id}`, `GET …/{id}/verify` (recomputes from the stored inputs and checks the hash) (`paymentreview.read`: owner, bookkeeper; staff → 403). There is no route that pays, approves, or executes; the response says the review "does not approve, authorize, or initiate any payment" | [backend/app/modules/identity/policy.py](backend/app/modules/identity/policy.py) |
| UI | **Payment Review** is now available (layout from `images/05-payment-review.png`: proposed payment card with the decision pill, Four scenario check, Evidence that matters with "View supporting record" links; styling from `design.md`). Above it, the entry form (funding account, amount, date, payee, linked bill, adverse case). Below it, per-scenario assumptions, source links, and the dated movements, plus the calculation version and inputs hash and a list of saved reviews. The **Rescue options** section of the mockup is not built (a later prompt) | [frontend/src/review/PaymentReviewPage.tsx](frontend/src/review/PaymentReviewPage.tsx) |

### Evidence (run on 20 Oct 2026)

- `alembic upgrade head` → `0010_alerts -> 0011_payment_review`; `/api/health/ready` expects `0011_payment_review`.
- `pytest -q` → **314 passed**: 299 earlier plus 15 new in [backend/tests/test_payment_review.py](backend/tests/test_payment_review.py). Hand-worked case (bank 16,000,000, reserve 8,000,000, as of 20 Oct; pay 6,000,000 on 23 Oct, linked to a 6,000,000 bill due the same day; rent 2,000,000 on 22 Oct; a 1,000,000 bill on 28 Oct; invoices 5,000,000 due 25 Oct and 3,000,000 overdue; one disputed invoice):
  - **All on time:** lowest **8,000,000 on 23 Oct**, exactly the reserve, so **no breach** (the boundary is "below", not "at").
  - **Receipts delayed:** lowest **7,000,000 on 28 Oct**; first breach 28 Oct, gap **1,000,000**; the overdue invoice is not counted.
  - **Largest receipt missed:** the 5,000,000 invoice is removed (the 3,000,000 one remains); lowest 8,000,000 on 23 Oct, no breach.
  - **Owner-defined adverse** (receipts 10 days later and 20% lower, extra 500,000 on 30 Oct): receipts become 4,000,000 on 4 Nov and 2,400,000 on 6 Nov; first breach 28 Oct, lowest **6,500,000 on 30 Oct**, shortfall **1,500,000**.
  - **Decision:** Reserve shortfall; arithmetic shortfall, evidence complete.
  - **No double counting:** with the bill linked, 23 Oct nets to exactly −6,000,000 and the bill appears once. The same payment unlinked leaves the bill in, and the 23 Oct balance falls to 2,000,000 (gap 6,000,000). A 4,000,000 payment against the 6,000,000 bill leaves the 2,000,000 remainder on 23 Oct.
  - **Intraday convention:** balance 9,000,000, 5,000,000 received and 3,000,000 paid the same day closes at 11,000,000, but the review reports a breach at **6,000,000** (outflow first).
  - **Material unknowns:** with no breach, a stale funding balance, a missing funding balance, or an unmatched transfer each gives **More information needed** with the blocker and its record ID; a missing balance reports every path as "not assessed" with no figures; a breach plus a stale balance still returns **Reserve shortfall** with the evidence marked incomplete; a disputed linked payable is a blocker.
  - **Refusals:** a payment larger than the linked bill, today or a date beyond 13 weeks, another business's account or bill, a non-bill link, an empty adverse case, an extra outflow with no date, floats, a formula-like payee, and unknown fields.
  - **Saved report (API):** 9,000,000 balance, a 2,000,000 bill due day 3 linked to a 2,000,000 payment on day 4 → every path's lowest is **7,000,000 on day 4**, gap 1,000,000 (a double count would give 5,000,000). The saved report equals the response, **Verify** reproduces it, and it stays unchanged after the bill is paid.
  - **Access and audit:** staff → 403 (run and read); a bookkeeper can run; another business → 404 for list, read, and verify; the success and the staff refusal are audited; the payee name is not copied into the audit trail; the router exposes only GET and POST and no pay/execute route; UPDATE on a saved report is rejected as append-only.
- **Browser run (real UI, demo business, Amina):** a BDT 20,000 payment to "Delta Supplies" on 23 Oct from the operating bank account → **Reserve shortfall**. Lowest cash **৳ 23,001.00 on 23 Oct**, first below reserve 21 Oct, shortfall **৳ 56,999.00**. This equals the hand calculation (bank BDT 43,001.00 − 20,000.00; reserve BDT 80,000.00). The evidence panel listed the unmatched transfer ("View supporting record"), the cash box and wallet with no balance, and the notice that a shortfall is shown although evidence is incomplete.
- `audit.verify` → `Audit chain OK`. `npm run build` passes.

### Known gaps and risks

- Daily timing inside a day is a convention (outflows first), not real bank timing. It is stricter than the Cash Outlook chart, so a review can show a breach the chart does not.
- The decision uses the same fixed outlook assumptions (14-day delay, 7-day assumed collection of overdue invoices) and the same limits (no recurring costs unless entered, no future own-account transfers). A positive result is only "under stated assumptions".
- "Largest eligible receipt" means the biggest non-disputed receipt the expected path counts, including an overdue one assumed collected.
- Any unmatched own-account transfer blocks a positive result, because it cannot be tied to one account. Stale non-funding balances also block. This may be stricter than a human would be.
- Only one currency is reviewed at a time (the funding account's); other currencies are ignored, not converted.
- No preview without saving: every run creates a saved report. Saved reports cannot be deleted.
- The Rescue options from the mockup and the evidence-impact queue (Prompts 11 and 13) are not built. Nothing here approves or executes a payment (Prompts 15 and 17).

## Prompt 11 — Decision-Impact Evidence Queue

### What was built

| Area | What exists | Files |
| --- | --- | --- |
| Scope | A read-only queue for **one saved Payment Review**. It is computed on request from that review's own frozen inputs and request; **nothing is stored, no migration, and the Payment Review calculator was not changed** (the queue calls it unmodified) | [backend/app/modules/evidence_queue/engine.py](backend/app/modules/evidence_queue/engine.py) |
| What becomes a task | Every record that could change this review, in the payment's currency:<br>• **blockers** the review already reports: funding balance missing or stale, other stale balances, a disputed linked bill, an unmatched own-account transfer, no reserve defined;<br>• a **disputed receivable** (not counted on any path), a **disputed bill** (still counted), an **overdue receivable** (assumed collected on the on-time, largest-missed, and adverse paths);<br>• an **account with no balance** that is left out. | same |
| Effect, bounded only where defensible | For an invoice or bill the effect is measured by **re-running the Payment Review calculator on a copy of the frozen inputs with only that record changed**: best case, worst case, minimum cash on each of the four paths, the swing, and whether the decision would change. A missing or stale balance and an unmatched transfer carry no defensible amount, so they are shown as **unbounded** with no figure. A hypothetical result is labelled as what the review *would* show; it is never the result | same |
| Ranking | 1 **blocking** (funding balance, then disputed linked bill, other stale balances, transfers, no reserve) → 2 **decision-changing** → 3 **material** (moves lowest cash by at least 1% of the reserve, or 1% of the payment if there is none) → 4 **unbounded upside** (a missing balance that is left out) → 5 **immaterial**. Inside a tier: larger swing, then larger amount, then ID, so the order is deterministic. Each task shows rank, why it matters, what to verify, its source record link, tier, and effect | same |
| Never auto-resolves | The queue has **no write route** (GET only). The saved decision is returned unchanged as `decision_on_record`. Each task is also compared with today's records: if its record changed, the task shows "Record changed since this review" and asks for a **new** Payment Review; the old review and its decision stay as saved. The decision changes only when a person with permission corrects the record (for example an owner or bookkeeper entering a balance anchor) and a new review is run | [backend/app/modules/evidence_queue/router.py](backend/app/modules/evidence_queue/router.py) |
| API | `GET /api/businesses/{id}/payment-reviews/{review_id}/evidence-queue` (`paymentreview.read`: owner, bookkeeper; staff → 403; another business → 404) | same |
| UI | A **What to verify first** card under the review on the Payment Review screen: numbered tasks with a tier badge, why it matters, what to verify, best/worst effect, the amount on record, and a "View supporting record" link. Changed records are highlighted | [frontend/src/review/PaymentReviewPage.tsx](frontend/src/review/PaymentReviewPage.tsx) |

### Evidence (run on 20 Oct 2026)

- `pytest -q` → **322 passed**: 314 earlier plus 8 new in [backend/tests/test_evidence_queue.py](backend/tests/test_evidence_queue.py):
  - **Missing balance above an immaterial receipt:** funding account with no balance and a 50,000 overdue invoice (reserve 8,000,000, threshold 80,000) → order `balance:bank` (blocking, unbounded, rank 1), the unbounded cash box, then the invoice (**immaterial**, swing 0).
  - **Exact effects (hand-checked):** a disputed 1,000,000 bill is the only reason the delayed path reaches 7,000,000 on 28 Oct. If it were removed, every path would be exactly 8,000,000 and the decision would move from shortfall to within reserve, so it is the only **decision-changing** task (swing 1,000,000). The overdue 3,000,000 invoice is **material** (without it the largest-missed path drops to 7,000,000), and a disputed invoice that arrives after every low point has swing 0 and is **immaterial**.
  - **Materiality order:** three overdue invoices (3,000,000, 100,000, 50,000) with a payment after they arrive swing the on-time lowest cash by exactly their amounts: material, material, **immaterial** (50,000 < 80,000), with the unbounded balance task above the immaterial one.
  - **Blocker order:** funding balance, disputed linked bill, other stale balance, unmatched transfer. A transfer is unbounded because its amount is not in the frozen inputs.
  - **No invented result:** the inputs are untouched, the saved decision is unchanged, the queue is deterministic, and no single-record best case reports "within reserve" while a blocker remains.
  - **Changed records flagged, not resolved:** a balance that became fresh shows `record_changed` with the "run a new review" note while `decision_on_record` stays `more_information_needed`.
  - **API, end to end:** a payment from an account with no balance → **More information needed**, queue rank 1 = that account's balance. Reading the queue twice returns the same result and creates no review or audit event. Staff get 403 on the queue and on entering a balance. The owner then enters the balance anchor: the old review still says **More information needed** and its task shows "Record changed"; only a **new** review returns **Within reserve**, and its queue has no blocking task.
  - **Access:** another business → 404 (queue and review); POST → 405; the only queue route is GET.
- **Browser run (demo business):** the saved BDT 20,000 review showed "What to verify first" with two unmatched-transfer blockers (rank 1–2, no bound), then the cash box and the mobile wallet (left out, could only add cash), the threshold BDT 800.00, and the note that nothing is resolved automatically. `npm run build` passes.

### Known gaps and risks

- Each task changes one record at a time. Two records that matter only together are not combined, and the best case for one blocker does not clear the others (the decision stays "More information needed" until all are verified).
- Balances, stale balances, and transfers cannot be bounded, so the queue ranks them by whether they block a positive result, not by size.
- The queue reflects the review's frozen inputs. It compares with today's records only for "record changed"; it does not predict the new answer.
- The 1% materiality threshold and the tier order are fixed for `evidence-queue-v1`.
- No assignment, due dates, or completion tracking for tasks. Rescue options (Prompt 13) are not part of this.

## Prompt 12 — Funding Matches

### What was built

| Area | What exists | Files |
| --- | --- | --- |
| Catalogue | A **versioned, illustrative** catalogue (`catalogue-2026.10`, terms updated 1 Oct 2026) of six sample products: working capital line, invoice advance, term loan, wallet micro-loan, secured bank loan, and a Kenyan trade line. Each has jurisdictions, currencies, amount bounds, minimum months trading and monthly revenue, purposes, security, required documents, typical funding days, pricing, repayment rule, and a source label with an update date. It states it is written for this prototype and is not from any real lender | [backend/app/modules/funding/catalogue.json](backend/app/modules/funding/catalogue.json) |
| Hard filters (all reasons kept) | Jurisdiction, currency, amount, business age, revenue, purpose, security, documents, funding time. **Revenue** is the average monthly classified operating receipts over the last 90 days from the ledger. **Receivable security** is checked against your records (advance up to 80% of outstanding, undisputed invoices). **Business age, guarantor, collateral, and documents are owner-declared and labelled unverified.** A product that fails any filter is excluded with every reason and is never costed or ranked | [backend/app/modules/funding/engine.py](backend/app/modules/funding/engine.py) |
| Exact terms | Integer minor units, half-up rounding, any remainder on the last payment: processing fee (deducted from proceeds), flat-rate term-loan installments on calendar months, interest on a bullet line, and the invoice advance's fee per started 30 days. Each result shows net proceeds, total repayment, total cost, cost as a % of the amount, disbursement date, and every repayment date | same |
| Effect on the cash forecast | The product's proceeds and repayments are inserted into the **existing, unmodified Cash Outlook engine** on the owner's chosen account (proceeds on the funding date, each repayment on its date). Lowest cash and first reserve breach are shown for the expected and cautious paths and compared with the baseline, which equals the Cash Outlook's own figures. Only repayments inside the 13-week forecast can change these figures | same |
| Ranking | Cautious path stays above the reserve first, then higher lowest cash on the cautious path, then lower total cost, then faster funding, then ID. The reasons are shown on each product, plus badges (**Best repayment fit**, **Fastest funding**, **Lowest quoted cost**) | same |
| Labels | Every result carries "Illustrative · potential match, not an approval"; the response states `lender_approval: false` and `data_shared: false`, and the page shows the catalogue version, update date, and per-product terms source. No lender is contacted and no business data leaves the app | [backend/app/modules/funding/router.py](backend/app/modules/funding/router.py) |
| Saved runs | Alembic revision `0012_funding_matches`: `funding_match_run` stores the request, frozen inputs and SHA-256, the exact catalogue contents and version, and the results; append-only. `POST …/funding-matches` (new `funding.run`: owner, bookkeeper; audited), `GET …`, `GET …/{id}`, `GET …/{id}/verify` (recomputes from the stored inputs and catalogue), `GET …/catalogue` (`funding.read`: owner, bookkeeper; staff → 403). There is no apply, submit, share, or approve route | [backend/migrations/versions/0012_funding_matches.py](backend/migrations/versions/0012_funding_matches.py) |
| UI | **Funding Matches** is now available (layout from `images/06-funding-matches.png`: funding-need card with the "potential match" pill, Compare options with badges, total repayment, lowest cash, and a details drawer with fees and the repayment schedule). Below, a **Not eligible** list with every reason and terms source. The **Readiness** and **Funding Passport** cards of the mockup are not built (later prompts) | [frontend/src/funding/FundingMatchesPage.tsx](frontend/src/funding/FundingMatchesPage.tsx) |

### Evidence (run on 20 Oct 2026)

- `alembic upgrade head` → `0011_payment_review -> 0012_funding_matches`; `/api/health/ready` expects `0012_funding_matches`.
- `pytest -q` → **338 passed**: 322 earlier plus 16 new in [backend/tests/test_funding_matches.py](backend/tests/test_funding_matches.py). Hand-worked case: bank 10,000,000, reserve 8,000,000, a 5,000,000 bill on 27 Oct (so the baseline already falls to 5,000,000 on 27 Oct, gap 3,000,000); need 4,000,000 by 27 Oct:
  - **Working capital line:** fee 20,000, net 3,980,000, interest 118,356 (18% × 60/365), repaid 4,118,356 on 22 Dec, cost 138,356. Cautious lowest **4,861,644 on 22 Dec**, first breach that day, gap 3,138,356.
  - **Term loan:** 12% flat × 6 months = 240,000; installments 706,666 ×5 and 706,670 (the 4 minor-unit remainder on the last); dates 27th of each month; cost 6%. Cautious lowest **7,586,668 on 27 Dec**, gap 413,332.
  - **Invoice advance:** 2 started 30-day periods at 1.5% = 120,000, repaid 4,120,000 on 5 Dec. Lowest **4,880,000 on 5 Dec**.
  - **Two products with different repayment effects:** the lump repayment drops the line to 4,861,644 while the small monthly installments keep the term loan at 7,586,668.
  - **Ranking and badges:** term loan, invoice advance, working capital line; best repayment fit = term loan, fastest funding and lowest cost = invoice advance. With 12,500,000 cash and a 2,000,000 need the term loan alone stays above the reserve (8,793,334) and ranks first; the others fall to 7,440,000, 7,430,822, and 7,380,822.
  - **Excluded products:** the micro-loan (amount above its 3,000,000 maximum), the secured bank loan with **six reasons** (amount, business age 18 < 24, revenue, collateral not declared, tax certificate missing, 21 days to fund against 7), and the Kenyan line (not offered in BD, does not lend in BDT, and more). Excluded products have no terms or forecast and are never ranked. Each hard filter was also tested alone (purpose, age, documents, funding time, guarantor), plus the revenue and receivable-cover filters against records.
  - **Other checks:** calendar month-end clamping (31 Jan + 1 month = 28 Feb), refusals (unknown account, date out of range, account with no balance), deterministic output, inputs untouched, and the catalogue labelled illustrative with dates.
  - **API, end to end:** receipts imported and classified, a balance anchor, a bill, and an invoice → three eligible products; revenue comes from the classified records; the saved run equals the response, and Verify reproduces it. Staff → 403; another business → 404 on list, read, verify, and catalogue; unknown fields, floats, bad purposes, and another business's account are refused; the success and the staff refusal are audited; the router has only GET and POST and no apply/share route; UPDATE on a saved run is rejected as append-only.
- **Browser run:**
  - The demo business at first (no classified receipts in the last 90 days) honestly returned **no eligible product**, with the reasons, including "records show 0 over the last 90 days".
  - **Demo history file (demo only, not part of the feature):** the file is synthetic sample data that exists only so the demo business can show an eligible match; Funding Matches does not need it. After importing [fixtures/imports/northstar_bank_history_jul_sep.csv](fixtures/imports/northstar_bank_history_jul_sep.csv) (13 rows, 369,000.00 BDT of receipts) and classifying the receipts as sales, the demo business now shows an eligible product in Funding Matches (confirmed by the user in the browser; the exact product and figures were not recorded here).
  - On a throwaway test business with 30,000,000 of classified receipts, a BDT 40,000 need by 27 Oct returned invoice advance (3 badges), working capital line, and term loan, with the funding dates, totals, lowest cash, and the labels. This matches the hand calculation (term loan 10,000,000 + 4,000,000 − 5,000,000 − 706,666 = 8,293,334 on its first installment date). The 1024px page had no horizontal overflow.
- [backend/tests/test_demo_history_csv.py](backend/tests/test_demo_history_csv.py) (1 test; suite now **339 passed**) imports that CSV through the real import API (dates shifted to stay inside the 90-day window):
  - all 13 rows parse as valid;
  - before the receipts are classified nothing is eligible ("records show 0");
  - after classifying the 6 receipts as sales the stored 90-day revenue is exactly **36,900,000** minor units, and the **working capital line, term loan, and wallet micro-loan** are eligible;
  - the invoice advance, secured bank loan, and Kenyan line stay excluded (the first because this history has no invoices).
- `npm run build` passes.

### Known gaps and risks

- The catalogue is invented sample data. Real products, regional rules, and Prompt 22's region pack are not covered, and the jurisdictions listed are not a legal claim.
- Business age, guarantor, collateral, and documents are what the owner declares; nothing checks them. Revenue depends on transactions being classified as operating receipts.
- Interest is flat-rate for term loans and simple for lines. There is no APR/effective-rate figure, no late fees, and no early-repayment terms.
- Repayments beyond the 13-week forecast are listed in the schedule but do not change the lowest-cash figure.
- Funding time assumes the application is made today. No application is made and no lender is contacted; there is no readiness view, Funding Passport, Conditional Funding Plan, or Rescue Plan in this prompt.
- The match does not check whether the product's money covers the Cash Outlook gap, only how it changes the lowest cash. The smallest useful amount (Prompt 13) is not computed.

## Not built or verified yet

| Area | Current status |
| --- | --- |
| Owner approval, action tiers T1–T3 | Not implemented (Prompt 15). Login, roles, and API business isolation (Prompt 01) and the audit trail (Prompt 02) are built |
| Records foundation | Built: CSV/XLSX import (Prompt 04); balance reconstruction, reconciliation, restricted funds, and transfers (Prompt 05); receivables and payables with payments, reversals, and disputes (Prompt 06) |
| Financial analysis and cash forecast | Built: Financial Health (Prompt 07) and Cash Outlook (Prompt 08) |
| Alerts | Built: Smart Alerts (Prompt 09), manual run only |
| Payment Review | Built (Prompt 10): four scenarios, saved reports, never initiates payment |
| Evidence queue | Built (Prompt 11): read-only, ranked, per saved review |
| Rescue Plan | Not implemented (Prompt 13) |
| Financing catalogue and matching | Built (Prompt 12): illustrative catalogue, filters, forecast effect, ranking |
| Financing readiness, conditional plan, and Funding Passport | Not implemented (Prompts 14, 19, 20) |
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

Account balances can be reconciled against statement anchors with declared source coverage, restricted funds, and own-account transfer pairing. A zero residual is never treated as proof of completeness, and stale or missing balances are never added to spendable cash. Customer invoices and supplier bills keep their original terms, with exact outstanding amounts after payments and reversals, and concurrent over-allocation is refused.

A cash-basis operating summary (not an audited P&L) ties every figure to its source records, and it was checked against the independent fixture. A 13-week Cash Outlook with expected and cautious paths is calculated from frozen, hash-verified input snapshots, and its exact path values were checked against a hand-worked fixture.

Deterministic alerts (reserve breach, overdue invoice, unusual outflow, repayment pressure) are shown on the Overview with their date, amount, cause, severity, and supporting record IDs, and repeated runs do not duplicate them. They run only on request; nothing is sent externally.

A proposed payment can be checked in four scenarios on one frozen snapshot, with exactly one decision state (within reserve under stated assumptions, reserve shortfall, or more information needed); known breaches stay visible and material unknowns block a positive result. Reports are saved and never initiate a payment. For each saved review, a ranked queue lists the missing, stale, or disputed records that could change it, with bounded best/worst effects only where the record has an amount; it never resolves evidence or changes the decision.

Illustrative financing products can be filtered by documented eligibility, costed exactly, and tested on the cash forecast, with exclusion reasons and the source and date of every term; they are labelled potential matches, never approvals.

No approval workflow or lender connectivity exists yet.
