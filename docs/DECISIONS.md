# Decisions log

## Confirmed by the developer (16 Nov 2026)
- The agent is developed standalone and handed to a colleague who deploys it into AI Studio (a platform hosting
  several agents, e.g. MetaFlow). The developer does not build AI Studio.
- CAND-1042: the Decision screen showing "Evidence status: COMPLETE" comes after the provisioning and inventory
  evidence requests were fulfilled. Submission is gated until then.
- Connectors are in scope from the start, Excel first: build the connector framework with the Excel adapter
  first, then Salesforce, then other systems.

## Step 1 working agreements (16 Nov 2026, developer OK'd the plan)
- Python: `requires-python >= 3.12`; developer machine runs 3.13. Docker images will pin 3.12.
- DB tests run against real Postgres (`pra_test` database in docker-compose), not SQLite, because
  we rely on schemas and triggers. Redis is faked (fakeredis) in tests.
- Work happens on a branch per step (`step-1-skeleton`); the developer commits.
- Default AI model: `claude-opus-5-5` (configurable via `PRA_ANTHROPIC_MODEL`), with JSON-schema
  structured outputs and server-side refusal fallback; no-LLM mode is the default provider.
- Audit hash chain is per tenant (each tenant's chain verifies independently); DB triggers block
  UPDATE/DELETE/TRUNCATE on `audit_event`.
- Standalone sign-in is **email + password** (developer's request, 16 Nov 2026), replacing the
  persona/tenant picker. Users live in `app_user` (tenant comes from the user record, email unique
  and case-insensitive). Passwords are hashed with stdlib scrypt. After 5 failures, the account is
  locked for 15 minutes. Success and failure are audited (`auth.login_succeeded` /
  `auth.login_failed` with a reason). The error message is always generic. No sign-up page: users
  come from `python -m app.cli seed-demo-users` / `create-user`. `POST /auth/login` is mounted only
  in standalone mode; in AI Studio the studio's sign-in replaces it, and the JWT claims stay the same.
- The RQ worker uses a non-forking worker with thread timeouts so it runs on Windows.
- The UI builds its nav from `GET /manifest`, filtered by the permissions in `GET /me`.
- New UI design supplied by the developer (16 Nov 2026) renames the navigation. The new names are
  **UI labels only**; internal names, permissions and API paths keep the user guide's terms:
  | UI label | Spec / internal term |
  |---|---|
  | Assessments (home; sidebar "Assessment Overview") | Portfolio Runs / runs |
  | Evidence Readiness | Evidence / evidence readiness |
  | Evidence Needed | Evidence Requests |
  | Rationalisation Opportunities | Candidates (`candidate.view`) |
  | Decision Approvals | Approvals |
  | Transition Plans | Packages (still not specified) |
  | Outcomes & Benefits | Benefits (still not specified) |
  Layout: header, top tabs with purple underline, WORKSPACE sidebar (items with `sidebarLabel` in
  manifest.json), KPI tiles, run table, Next action / Agent outcome cards. KPIs and the run table show
  real data only (empty until Step 2).
- Minimum password length is 8 (NIST SP 800-63B). Changing `PRA_DEMO_PASSWORD` does not change
  existing users; run `python -m app.cli seed-demo-users --reset-passwords`.

## Spec documents of 16 Nov 2026: proposed resolutions (developer OK'd the plan; change any time)
| Topic | Resolution | Status |
|---|---|---|
| Evidence status STALE / CONFLICTED (field spec) vs CLAUDE.md rule 2 (4 statuses) | Keep the 4 statuses; stale and conflicted are separate flags on a domain/evidence item | Proposed |
| STANDARDISE vs STANDARDIZE | `STANDARDISE` everywhere (field spec spelling) | Proposed |
| Spec API paths (`/api/runs`, `/api/mappings`) | Keep our prefix: `/api/rationalisation/runs`, `/api/rationalisation/mappings`, … | Proposed |
| Portfolios, markets, product families | Tenant reference tables, seeded by `python -m app.cli seed-reference-data`; later fed from the CRM catalog | Proposed |
| Portfolio entitlement | Until defined: every user of a tenant may use all its portfolios | Proposed |
| Mapping "separate Publish permission" | New permission `mapping.publish`, given to Agent Admin | Proposed |
| Malware scanning of uploads | Behind a scanner port; local stub now, ClamAV (or studio service) later | Proposed |
| Domain events | Outbox table + polling; no message broker, no SSE for now | Proposed |
| Hard-coded model (`PRA_ANTHROPIC_MODEL`) | Replaced in Step 8 by a model registry (deployments as data) | Proposed |
| Define Scope period | Start/end dates (field spec), not free text as in the screenshot; "Last 12 months" quick option | Proposed |
| Market on Define Scope | Multi-select 1–10 (field spec), not single as in the screenshot | Proposed |

## Proposed defaults (AI Studio contract not decided yet; confirm with the colleague)
| Topic | Default | Status |
|---|---|---|
| Deployment | Separate Docker service with its own API | Proposed |
| Database | Postgres, schema `rationalisation`, Alembic | Proposed |
| Sign-in | Studio issues JWT (sub, tenant_id, roles); agent verifies with studio public keys. Local adapter issues dev JWTs | Proposed, discuss early |
| Tenancy | `tenant_id` on every table | Adopted |
| Permissions | Declared in manifest.json, studio maps roles to them | Proposed |
| UI embedding | Screens without global layout; packaged later as micro-frontend or components | Proposed, discuss early |
| AI | AIProvider port; Anthropic/BYOK locally; no-LLM fallback | Adopted |
| Files / jobs / audit / secrets | Agent's own (disk, Redis+RQ, DB table, Fernet) behind ports | Adopted |

## Open spec questions (from reviewing the user guide)
- Packages and Benefits tabs are in the navigation but not specified. Out of scope until specified.
- "Context Fabric", "Product Knowledge Graph", "Integration Knowledge Graph" are named but not defined.
  Treat as: the canonical Telco graph (product → CFS/RFS → resource → provisioning) in our DB.
- Candidate detection algorithm is not specified beyond similarity threshold 0.88. Proposal: deterministic
  weighted feature similarity over canonical attributes; AI only explains.
- CAND-1042 shows score 92 on some screens and 87/100 on the evidence-aware screen. Proposal: 92 = opportunity
  score, 87 = commercial rationalisation dimension score; AI confidence 0.87 is separate.
- Wizard step 2 is labelled both "Sources" and "Evidence Hub". Use "Evidence Hub".
- AI route screen shows "Now Assist" as primary route. Treat routes as configurable entries in the model registry.
- Persona/permission matrix not specified. Proposal in BUILD_PLAN step 1.
