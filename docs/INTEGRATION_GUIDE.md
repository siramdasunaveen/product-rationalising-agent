# Integration guide: deploying the Product Rationalisation Agent into AI Studio

Written for the engineer who hosts this agent inside AI Studio. The agent was built standalone; everything the
studio may provide sits behind a port (interface) with a local adapter, so integration means implementing a
handful of adapters and wiring the manifest. Nothing in `app/domain`, `app/services` or the screens needs to change.

## 1. What you receive

| Item | Where |
|---|---|
| Backend (FastAPI, Python 3.12) | `backend/` — image: `docker build -f backend/Dockerfile -t pra-backend .` |
| Background worker | same image, command `python -m app.jobs.worker` |
| UI (React 18, screens only) | `frontend/src/` (library surface in `frontend/src/index.ts`); standalone shell in `frontend/standalone/`; image `frontend/Dockerfile` |
| API contract | `docs/openapi.json` (regenerate: `backend\scripts\export_openapi.py`); live at `/docs` |
| Agent manifest | `manifest.json`: id, name, industry, version, permissions, suggested roles, nav, required connectors |
| Database | PostgreSQL 16, schema `rationalisation`, Alembic migrations `backend/alembic` (0001 … 0017) |
| Seed data | `python -m app.cli seed-reference-data`, `seed-artifacts`; sample evidence in `docs/samples/`; demo script `backend/scripts/demo_readiness.py` |
| Tests | `backend/tests` (pytest), `frontend` type check; last report in `docs/TEST_REPORT.md` |
| Security | `docs/SECURITY.md` (controls, platform requirements, known gaps), `docs/DEPLOYMENT.md` |

## 2. Runtime shape

```
browser ──► web (nginx, static UI, proxies /api) ──► api (uvicorn :8000) ──► PostgreSQL
                                                         │                  └─► Redis (RQ queue + job meta)
                                                         └─► worker (RQ): uploads, mapping, reconciliation,
                                                             analysis, outbox relay of domain events
```

- Every request goes through `/api/rationalisation/...`; `/healthz` (liveness) and `/readyz` (DB + Redis).
- The API is stateless; files live in object storage (disk adapter by default), everything else in PostgreSQL.
- Background work is queued by the API and executed by the worker; progress is read back through job meta.
  Jobs: `app.jobs.evidence.process_upload`, `app.jobs.mapping.validate_mapping`, `app.jobs.mapping.execute_mapping`,
  `app.jobs.reconciliation.reconcile_run`, `app.jobs.analysis.analyse_run`.

## 3. Platform mode and ports

`PRA_PLATFORM_MODE=standalone|studio` selects the adapter set in `backend/app/adapters/registry.py`.
`studio` mode instantiates the stubs in `backend/app/adapters/studio/adapters.py`; each stub's docstring says
exactly what the studio must supply. Implement them in that file (or a sibling module) and keep the port contract:

| Port (`backend/app/ports`) | Standalone adapter | Studio must provide |
|---|---|---|
| `auth.AuthProvider` | local JWT after email + password sign-in | verify the studio's JWT and return a `Principal` (user id, tenant id, name, roles, permissions, approval scopes, auth context). Permissions come from `manifest.json` `suggestedRoles` or the studio's own role mapping; the UI reads `GET /me`, never role names |
| `ai.AIProvider` | Anthropic SDK or no-LLM | the studio's model gateway (optional: the AI runtime can also use a customer registry or a BYO endpoint) |
| `storage.ObjectStorage` | disk under `PRA_STORAGE_DIR` with quarantine/checked areas | object store with the same two areas |
| `jobs.JobQueue` | Redis + RQ | any queue that can run the dotted job functions above and report status |
| `audit.AuditLog` | DB table with hash chain | central audit service (keep: actor, roles, timestamp, auth context, action, entity, payload, content hash) |
| `secrets.SecretCipher` | Fernet with `PRA_FERNET_KEY` | KMS / secret store (used for AI and connector credentials) |
| `people.PeopleDirectory` | app users table | tenant directory for owner and assignee pickers |
| `notify.Notifier` | log | inbox / email (approvals due, evidence requests) |
| `events.EventPublisher` | log | the studio event bus; events are relayed from the transactional outbox (at-least-once, ordered per aggregate) |
| `scanner.MalwareScanner` | EICAR test scanner | the real anti-malware service |
| `connectors.SourceAdapter` | Excel upload, Salesforce (REST/SOQL) | nothing required; add adapters for other systems here |

## 4. Authentication contract

The standalone adapter issues JWTs with the claims the studio is expected to send (see
`backend/app/adapters/local/auth.py`): `sub` (user id), `tenant` (tenant id), `name`, `roles` (list), `jti`, `iat`,
`exp`, `iss`. Permissions are derived from roles by `app/domain/permissions.py` (or send them explicitly). The UI
sends `Authorization: Bearer <token>` on every call (`frontend/src/api/client.ts` → `configureApi({getToken,
onUnauthorized})`); in the studio, mount the screens from `frontend/src` and call `configureApi` with the studio's
token source. The login page and `POST /auth/*` exist only in standalone mode.

Multi-tenancy: every table has `tenant_id`; every query filters by the principal's tenant. A tenant is whatever
the studio puts in the token.

## 5. Domain events (outbox)

Published through `EventPublisher` with `tenant_id`, `event_type`, `aggregate_type`, `aggregate_id`, payload and
sequence: `RunStarted`, `EvidenceDomainsConfirmed`, `RawEvidenceLanded`, `EvidenceUploadRejected`,
`AnalysisRequested/Queued/Completed/Failed`, `BlockerCreated/Resolved`, `RecommendationExplained`,
`EvidenceRequestCreated`, `EvidenceRequestStatusChanged`, `EvidenceFulfilmentStarted`, `EvidenceReceived`,
`EvidenceAccepted`, `EvidenceRejected`, `DecisionVersionCreated`, `ApprovalTasksCreated`, `DecisionApproved`,
`DecisionRejected`, `DecisionReturned`, `DecisionHeld`, `DecisionSuperseded`, `ConnectorPulled`, plus the mapping,
reconciliation and registry events of steps 5–7. Consumers must be idempotent (at-least-once).

## 6. Configuration

All settings are environment variables with the `PRA_` prefix (`.env.example` lists them). Required in any
non-dev environment: `PRA_JWT_SECRET` (standalone only), `PRA_FERNET_KEY`, `PRA_DATABASE_URL`, `PRA_REDIS_URL`,
`PRA_STORAGE_DIR` (or the studio storage adapter), `PRA_CORS_ORIGINS`. Optional: `PRA_AI_PROVIDER=anthropic` +
`PRA_ANTHROPIC_API_KEY`, upload limits, `PRA_DEMO_PASSWORD` (never set in production).

## 7. Step-by-step

1. Build the two images; run `alembic upgrade head` against the tenant database (the API image does it on start).
2. Seed reference data and artifacts per tenant: `python -m app.cli seed-reference-data --tenant <id>` and
   `seed-artifacts --tenant <id>` (canonical schema, lookups, transforms, Excel templates, readiness policy,
   detector/scoring/priority/policy packs, AI runtime profile).
3. Implement `StudioAuth` (mandatory) and whichever other stubs the studio replaces; set `PRA_PLATFORM_MODE=studio`.
4. Register the agent with `manifest.json`; map studio roles to the manifest permissions; mount the screens or
   the standalone shell behind the studio's navigation (`nav` in the manifest lists paths and permissions).
5. Point the UI at the API (`/api` proxy or `API_UPSTREAM`); confirm `/readyz` is green.
6. Optional: register live connectors (Agent Admin → Connectors) and switch the AI runtime strategy.
7. Run the demo script against the deployment to validate the full flow, then `docs/DEPLOYMENT.md` checklist.

## 8. Things the agent will never do

Change a product, approve anything itself, compute a metric with an LLM, merge records on fuzzy matches, accept
macros or raw subscriber data, or bypass human review. Approval only enables a later transition package.
