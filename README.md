# Product Rationalisation Agent (DeepKorce · Telco · Agentstudio)

A governed, evidence-led agent that helps Telco portfolio teams rationalise commercial products.
The agent recommends and explains; people decide and approve.

- Spec and rules: [CLAUDE.md](CLAUDE.md)
- Build plan: [docs/BUILD_PLAN.md](docs/BUILD_PLAN.md)
- Decisions: [docs/DECISIONS.md](docs/DECISIONS.md)

## Layout

```
backend/            FastAPI app (Python 3.12+)
  app/domain/       pure business logic (permissions, later scoring/policy/decision rules)
  app/ports/        interfaces: auth, ai, storage, jobs, audit, secrets, notify
  app/adapters/local/   standalone implementations (used now)
  app/adapters/studio/  AI Studio stubs (to be implemented at deployment)
  app/api/          routers under /api/rationalisation, plus /healthz and /readyz
  app/db/           SQLAlchemy models; migrations in backend/alembic (schema "rationalisation")
  app/jobs/         background jobs + worker
  app/services/     orchestration (e.g. governed AI task runner)
frontend/
  src/              screens only (no global header/login)
  standalone/       dev shell: fake login + header + nav, mounts the screens
manifest.json       agent id, permissions, nav, connectors (AI Studio registration)
docker-compose.yml  Postgres 16 + Redis 7 (local infra)
```

`PRA_PLATFORM_MODE=standalone|studio` picks the adapter set.

## Prerequisites

Docker Desktop, Python 3.12 or newer, Node 20 or newer. Commands below are PowerShell, run from the repo folder (`pra-starter`).

## First-time setup

```powershell
# 1. Local infra (Postgres on 5432 with databases pra + pra_test, Redis on 6379)
docker compose up -d --wait

# 2. Backend virtualenv + dependencies
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
cd ..

# 3. Settings: copy the example, then fill in the two secrets
Copy-Item .env.example .env
.\backend\.venv\Scripts\python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"   # -> PRA_FERNET_KEY
.\backend\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"                               # -> PRA_JWT_SECRET
notepad .env     # also set PRA_DEMO_PASSWORD (at least 8 characters)

# 4. Database schema + demo users
cd backend
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\python.exe -m app.cli seed-demo-users
.\.venv\Scripts\python.exe -m app.cli seed-reference-data      # demo portfolios, markets, product families
.\.venv\Scripts\python.exe -m app.cli seed-artifacts           # TELCO-CANONICAL@1.0, lookups, transforms, Excel templates
cd ..

# 5. Frontend dependencies
cd frontend
npm install
cd ..
```

## Run (three terminals)

```powershell
# Terminal 1: API on http://localhost:8000 (OpenAPI docs at /docs)
cd backend; .\.venv\Scripts\uvicorn.exe app.main:create_app --factory --reload --port 8000

# Terminal 2: background job worker (works on Windows: no fork); also relays domain events (outbox)
cd backend; .\.venv\Scripts\python.exe -m app.jobs.worker

# Terminal 3: UI on http://localhost:5173 (proxies /api to :8000)
cd frontend; npm run dev
```

Open http://localhost:5173 and sign in with email + password. The nav shows only what that user's
permissions allow.

## Users (standalone mode)

Demo users (tenant `demo`) all use the password in `PRA_DEMO_PASSWORD`:

| Email | Role |
|---|---|
| pm@demo.local | Portfolio Manager |
| po@demo.local | Product Owner |
| steward@demo.local | Data Steward |
| evidence@demo.local | Evidence Owner |
| commercial@demo.local | Commercial Approver |
| product@demo.local | Product Approver |
| architecture@demo.local | Architecture Approver |
| ea@demo.local | Enterprise Architect |
| admin@demo.local | Agent Admin |
| viewer@demo.local | Viewer |

```powershell
cd backend
.\.venv\Scripts\python.exe -m app.cli create-user --email a.patel@example.com --name "A. Patel" --role portfolio_manager   # asks for the password
.\.venv\Scripts\python.exe -m app.cli set-active --email a.patel@example.com --inactive                                   # disable a user
.\.venv\Scripts\python.exe -m app.cli set-password --email a.patel@example.com                                            # change password, clear lockout
.\.venv\Scripts\python.exe -m app.cli seed-demo-users --reset-passwords                                                   # after changing PRA_DEMO_PASSWORD
```

There is no sign-up page. After 5 wrong passwords, an account is locked for 15 minutes. Every
sign-in attempt is audited. The page always says "Invalid email or password", so it never reveals
whether an email exists. In AI Studio, the studio's sign-in replaces this page and endpoint.

## Tests

```powershell
docker compose up -d --wait          # DB tests use the pra_test database
cd backend; .\.venv\Scripts\python.exe -m pytest
cd ..\frontend; npm run typecheck
```

DB tests drop and re-create schema `rationalisation` in `pra_test` and run the Alembic migrations.
Redis is faked (fakeredis), and the AI is faked per test.

## Useful commands

```powershell
docker compose down                   # stop infra (keeps data)
docker compose down -v                # stop infra and DELETE the data
cd backend; .\.venv\Scripts\alembic.exe revision --autogenerate -m "describe change"   # new migration
```

## Registries (versioned artifacts)

Canonical schemas, lookups, transforms and crosswalks live under `/api/rationalisation/registries/{kind}`.
Each version goes DRAFT → PUBLISHED → DEPRECATED / SUPERSEDED / RETIRED. Only drafts can change, and
the database enforces this with triggers. To change a published version, clone it, edit the draft and
publish it; the old version becomes SUPERSEDED. Runs pin `KEY@major.minor` plus a SHA-256 digest of the
content. Data Stewards and Agent Admins edit drafts; only Agent Admins publish.

## Evidence Hub (Excel intake)

Wizard step 2 (`/runs/{id}/evidence`). Confirm the proposed evidence domains, download a domain's governed
template, fill it in and upload it. Each .xlsx file goes through these stages:
1. The upload checks type, size, macros, password, damage and sheet count, and rejects a bad file at once.
2. The file waits in quarantine while the **worker** scans it and reads its sheets. It is rejected if it has
   too many rows or columns, or if it looks like subscriber or personal data. Rejected files are deleted.
3. You choose the sheet and header row. Confirming them records an immutable source schema version: the same
   headers reuse it, and changed headers create a new version.

Uploads stay "Scanning and reading…" until the worker (Terminal 2) is running. The malware scanner is a
local stub that only detects the EICAR test file; a real scanner plugs in behind the `MalwareScanner` port.

## Mapping Rules

From the Evidence Hub, open a confirmed upload and click **Map columns**. The system suggests rules. The
suggestions come from an earlier approved mapping with the same headers, the governed template, field names
or aliases; AI suggestions are optional and use only headers plus 5 sample rows. Every suggestion shows
where it came from. You can edit the rules, then:
1. **Validate**: runs in the worker against the confirmed file.
2. **Submit**: freezes the content hash.
3. **Approve**: another Data Steward or the Agent Admin approves; you can never approve your own submission.
4. **Publish**: the Agent Admin publishes.
5. **Apply**: stages canonical records with field lineage. Applying the same file with the same version
   again returns the same result.

`python -m app.cli seed-artifacts` also upgrades seed templates it published earlier, as a new minor version.

## Reconcile and approve evidence

From the Evidence Hub, click **Reconcile & approve evidence**. The screen shows the latest successful apply of every
source. **Reconcile** (worker) combines them field by field using the published precedence policy
(TELCO-PRECEDENCE): sources that agree keep the value; non-material differences are settled by policy and shown;
material differences wait in the **conflict queue** for a Data Steward (use a source's value, a manual value with a
rationale, or defer). **Approve snapshot** freezes the result as SNAP-001, SNAP-002 …: one fact per field with an
evidence ID (EV-…) and full provenance back to the source file, sheet and row. **Telco graph** walks a product to its
billing systems, offers, services and aggregates. A domain can have several sources (e.g. two CRM exports); add one
in the Excel panel and give each sheet a "data as of" date when confirming it.

## Evidence readiness

**Evidence Readiness** (top navigation, or the buttons on the Evidence Hub and Reconcile screens) shows, for a run,
how complete each evidence domain is in the approved snapshot: status (Available, Partial, Missing, Blocked), the
share of products covered, what cannot be assessed because of it and what to upload. Below that are the
assessments the evidence allows and the dispositions a decision could use (a decision needs every domain the
policy marks for it to be Available). Switch to **Preview latest reconciliation** to see the effect of new data
before approving it. The rules live in the registry as TELCO-READINESS (readiness policies).

## AI

Every AI task goes through the **AI Runtime** (tab visible to Agent Admins). Approved models and endpoints are
registry entries (AI deployments); the runtime profile TELCO-AI-RUNTIME picks a strategy (customer registry,
platform-managed, bring your own endpoint, or no-LLM), a routing policy, and per task a primary and optional
fallback model. Governance checks (approved, region, data class, structured output, budget) decide which models
may be used; after at most two models the deterministic template is used. Output must match the task's JSON schema
and every cited evidence id must exist; otherwise it is sent back (max 2 retries). Every call is logged.

The seeded profile uses the **platform-managed** model: `PRA_AI_PROVIDER=none` (default) means no-LLM, every task
uses its template. To use Claude either set `PRA_AI_PROVIDER=anthropic` + `PRA_ANTHROPIC_API_KEY`, or choose
**Customer model registry** on the AI Runtime screen and store the key as secret `anthropic-api-key` there.
