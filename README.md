# SME Finance Copilot — project package

This repository contains the LedgerLift SME finance copilot implementation, product and architecture documentation, build history, security self-assessment, test fixtures, and screenshots captured from the deployed app.

## Documents

| File | Purpose |
| --- | --- |
| [prd.md](prd.md) | Users, product scope, requirements, journeys, acceptance criteria, and screens |
| [architecture.md](architecture.md) | Data model, calculation and matching rules, AI boundaries, security, APIs, and tests |
| [phases.md](phases.md) | Competition demo path and later build phases with exit gates |
| [built_so_far.md](built_so_far.md) | What exists in this package and what remains unimplemented |
| [feature_security_matrix.md](feature_security_matrix.md) | Every feature, innovation flag, implementation approach, and built-in security |
| [technical_documentation.md](technical_documentation.md) | Implemented system architecture, core calculations, security design, and verification evidence |
| [security_self_assessment.md](security_self_assessment.md) | Implemented permission tiers, control assessment, test evidence, and known risks |

## Live app screenshots

These screenshots were captured from the deployed synthetic demo on 20 October 2026. The demo business currently has no posted statement transactions or verified account balances, so finance pages show empty or missing-evidence states.

| Page | Screenshot |
| --- | --- |
| Overview | ![Live overview](screenshots/live-overview.jpg) |
| Records & reconciliation | ![Live records and reconciliation](screenshots/live-records.jpg) |
| Financial Health | ![Live Financial Health](screenshots/live-financial-health.jpg) |
| Cash Outlook | ![Live Cash Outlook](screenshots/live-cash-outlook.jpg) |
| Fraud Monitor | ![Live Fraud Monitor](screenshots/live-fraud-monitor.jpg) |
| Account details | ![Live account details](screenshots/live-account-detail.jpg) |

The app uses synthetic demo identities and business data. Screenshots are illustrative captures of the current interface and can become outdated as the live app changes.

## Run the app locally

Requires Docker (for PostgreSQL 16), Python, and Node.js. Use synthetic demo data; the app does not connect to a live bank or lender.

```bash
# 1. Configuration (placeholders only - never commit .env)
cp .env.example .env

# 2. Database
docker compose up -d

# 3. API: install, migrate, seed the synthetic business
cd backend
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt      # macOS/Linux: .venv/bin/python
.venv/Scripts/python -m alembic upgrade head
.venv/Scripts/python -m app.fixtures.seed

# 4. Start the three processes (separate terminals)
#    from the repo root:  backend/.venv/Scripts/python -m uvicorn mock_services.app:app --port 8100
#    from backend/:       .venv/Scripts/python -m uvicorn app.main:app --port 8000
#    from frontend/:      npm install && npm run dev            -> http://localhost:5173

# 5. Tests (from backend/). They use a separate `smecopilot_test` database, created and migrated
#    automatically, so the demo data is never touched. Override with TEST_DATABASE_URL (name must end in _test).
.venv/Scripts/python -m pytest -q

# 6. Check the append-only audit hash chain (from backend/)
.venv/Scripts/python -m app.modules.audit.verify
```

Sign in at http://localhost:5173 with a synthetic demo user from [fixtures/demo_users.json](fixtures/demo_users.json) (`amina@northstar.example` owner, `bilal@northstar.example` bookkeeper, `chaya@northstar.example` staff, `tanvir@riverbend.example` owner of a second business). The password is `DEMO_USER_PASSWORD` from `.env`. Passwordless quick login is disabled by default and, when enabled, only accepts synthetic demo users. Re-running the seed restores demo roles.

To try Records import, upload the synthetic statements in [fixtures/imports/](fixtures/imports/) to "Operating bank account" on the Records page. `part1` has rejected rows, an in-file duplicate, and ambiguous dates, so choose DD/MM/YYYY. `part2` overlaps already-posted rows. Original uploads are kept privately under `storage/private/` (git-ignored, never served).

Health endpoints: `GET /api/health/live` (process) and `GET /api/health/ready` (database schema revision + mock-service boundary; returns 503 when degraded). The mock service exposes `GET http://127.0.0.1:8100/health`.

| Path | Contents |
| --- | --- |
| [backend/](backend/) | FastAPI modular monolith, Alembic migrations, calculation services, and tests |
| [mock_services/](mock_services/) | Local mock boundary for sandbox reminder, payment, application, and packet actions |
| [frontend/](frontend/) | React/TypeScript application |
| [fixtures/](fixtures/) | Synthetic business fixtures, hand-checked expected values, and demo import files |
| `storage/` | Private uploaded originals (created at runtime, git-ignored) |
