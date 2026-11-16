# Product Rationalisation Agent (DeepKorce · Telco · Agentstudio)

Read this file fully before any work. Then read `docs/BUILD_PLAN.md` and `docs/DECISIONS.md`.

## What we are building
A governed, evidence-led agent that helps Telco portfolio teams rationalise commercial products.
Flow: create run → define scope → Evidence Hub (7 domains, Excel first, live connectors later) →
scoring & AI route → review & start → background analysis → candidates → evidence review →
evidence requests → decision → multi-approver approval.

The agent **recommends and explains; people decide and approve.** Approval never changes a product.
It only enables a later "transition package".

This agent is built **standalone** and handed to a colleague who deploys it into **AI Studio**
(a platform that hosts several agents). The integration contract with AI Studio is NOT decided yet.
Therefore everything the studio might provide sits behind a port (interface) with a local adapter.

## Source material (the spec)
- `docs/source/Telco_Product_Rationalisation_User_Guide.pdf`: the product spec. Text in
  `docs/source/Telco_User_Guide.txt`; UI screenshots in `docs/source/screens/` (open them when building a screen).
- `docs/source/MetaFlow_*.pdf`: an existing app by our team. **Reference only.** Reuse its patterns:
  schema-checked AI tasks with retries, CSV/Excel import pipeline (AI sees only column names + 5 sample rows),
  Fernet-encrypted secrets, server-side permission checks on every route, approval that stores exact ids and is
  revoked when anything changes, background jobs with progress, tests against in-memory fakes.

## Stack
- Backend: Python 3.12, FastAPI, Uvicorn, SQLAlchemy 2.x, Pydantic 2 + pydantic-settings, Alembic,
  PostgreSQL (psycopg 3), Redis + RQ for jobs, openpyxl (xlsx), cryptography (Fernet), PyJWT, jsonschema,
  httpx, Anthropic SDK (behind the AI port). Tests: pytest.
- Frontend: React 18 + TypeScript + Vite, React Router, TanStack Query. Plain CSS with CSS variables (no UI kit).
- Local infra: docker-compose with Postgres and Redis.
- The developer works on Windows with VS Code. Give PowerShell commands.

## Architecture (ports and adapters)
```
backend/app/
  domain/      pure business logic, no I/O (scoring, policy, reconciliation, decision rules)
  ports/       auth, ai, connectors (SourceAdapter), storage, jobs, audit, secrets, notify
  adapters/local/   standalone implementations (used now)
  adapters/studio/  stubs for AI Studio (colleague fills later); each has a docstring of what it needs
  api/         FastAPI routers under /api/rationalisation/...
  db/          SQLAlchemy models + Alembic migrations, schema "rationalisation"
  jobs/        background job functions
frontend/src/  screens only (no global header/login inside screens)
frontend/standalone/  dev shell: fake login + header, mounts the screens
manifest.json  agent id, name, industry, version, permissions, nav, required connectors
```
- `PRA_PLATFORM_MODE=standalone|studio` selects adapters.
- Every table has `tenant_id`; every query filters by it.
- The UI decides what to show from permissions returned by `GET /api/rationalisation/me`, never from role names.
- Health: `/healthz`, `/readyz`. Keep the OpenAPI spec clean (it is the handover contract).

## Non-negotiable product rules (enforce in code, in `domain/`, with tests)
1. Missing evidence is never scored low. The dimension is NOT_ASSESSED or BLOCKED, never 0.
2. Evidence domain status: AVAILABLE | PARTIAL | NOT_ASSESSED | BLOCKED (UI may label NOT_ASSESSED as "Missing").
3. Commercially obsolete + active service footprint → MIGRATE, never RETIRE.
4. Commercially duplicate + technically different (CFS/RFS or provisioning) → STANDARDIZE or MIGRATE before CONSOLIDATE.
5. Missing mandatory evidence → INVESTIGATE; decision submission is disabled with the reason shown.
6. Decision submission requires every domain marked critical for that disposition in the policy matrix to be
   AVAILABLE in the current approved snapshot (e.g. CONSOLIDATE needs Technical Service Catalog + Provisioning;
   MIGRATE/RETIRE need Service/Resource Inventory). The CAND-1042 "Evidence status: COMPLETE" screen is AFTER
   evidence requests ER-102/ER-103 were accepted.
7. Fuzzy matching is only a suggestion; never auto-merge. No bulk irreversible actions.
8. Snapshots, scoring profiles, policy profiles, mapping templates and decisions are versioned and immutable;
   an edit creates a new version. Runs pin the versions of scoring, policy, prompt and model they used.
9. Accepted evidence → new snapshot → reassessment with the run's pinned versions → if the outcome changes
   materially, a new decision version and a new approval route.
10. Approval: policy decides required approvers; Reject/Return/Hold need a comment; self-approval can be disabled;
    all approvers act on the same decision version. Audit: actor, role, timestamp, auth context, decision version,
    policy result, content hash (retain 7 years).
11. AI output must match a JSON schema and every factual claim must cite evidence/lineage ids that exist;
    otherwise send it back (max 2 retries), then fall back to template/no-LLM output.
12. Excel uploads: .xlsx only; reject macros (.xlsm), password-protected or malformed files; limit size and
    sheet count; quarantine until checked. No subscriber-level data; only metadata and aggregates.

Scoring profile "Telco Portfolio v2.1" weights: duplication 20, overlap 15, low adoption 15, legacy dependency 15,
operational burden 15, strategic misalignment 10, economic upside 10. Recommend ≥ 80, review ≥ 60,
near-duplicate similarity 0.88. Score ≠ model confidence ≠ approval.

## How to work
- Build one step of `docs/BUILD_PLAN.md` at a time. At the start of a step, state the plan briefly; at the end,
  run tests and say what was done and what is next. Update the step's status in BUILD_PLAN.md.
- Write tests with each feature (domain logic gets unit tests; APIs get pytest + httpx tests; fakes for outside systems).
- Don't change a rule in this file or DECISIONS.md without asking the developer.
- Ask when the spec is ambiguous; record the answer in DECISIONS.md.
- Never commit secrets. `.env` is git-ignored; keep `.env.example` updated.
