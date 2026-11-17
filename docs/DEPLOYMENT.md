# Deployment

Written for the platform or DevOps engineer deploying the agent (standalone or inside AI Studio).

## Images

```powershell
# from the repository root
docker build -f backend/Dockerfile -t pra-backend .
docker build -t pra-web ./frontend
```

`pra-backend` runs as a non-root user, applies migrations on start and serves the API on 8000; the same image runs
the worker with `python -m app.jobs.worker`. `pra-web` serves the built UI with nginx (non-root, port 8080) and
proxies `/api`, `/healthz`, `/readyz` to `API_UPSTREAM`.

## Local full stack

```powershell
Copy-Item .env.example .env   # set PRA_JWT_SECRET, PRA_FERNET_KEY, PRA_DEMO_PASSWORD
docker compose --profile app up -d --build --wait
docker compose exec api python -m app.cli seed-demo-users
docker compose exec api python -m app.cli seed-reference-data
docker compose exec api python -m app.cli seed-artifacts
```

UI on http://localhost:8080, API on http://localhost:8000 (`/docs`).

## Production checklist

Controls the hosting platform must provide (the code assumes them; see `docs/SECURITY.md`):

- [ ] TLS termination in front of `web`/`api`; HSTS at the edge; only 443 exposed.
- [ ] `PRA_JWT_SECRET` (standalone) and `PRA_FERNET_KEY` from a secret manager, rotated; never the `.env.example` values.
- [ ] `PRA_ENVIRONMENT=prod`; `PRA_DEMO_PASSWORD` unset; no demo users seeded.
- [ ] PostgreSQL with TLS, a dedicated least-privilege role, backups and point-in-time recovery; audit tables retained 7 years.
- [ ] Redis with AUTH and TLS, reachable only from `api` and `worker` (the job channel executes code in the worker).
- [ ] Object storage (or `PRA_STORAGE_DIR` volume) encrypted at rest; quarantine area not served to users.
- [ ] A real malware scanner behind the `MalwareScanner` port (the local adapter only detects the EICAR test file).
- [ ] SSO/MFA and session revocation from the identity provider (AI Studio) — the standalone login is for development.
- [ ] Rate limiting / WAF at the edge for `/api/rationalisation/auth/login` and upload endpoints.
- [ ] Log shipping: the `pra.security` logger (structured security events) and audit tables to the SIEM.
- [ ] Egress allow-list: the API/worker only need PostgreSQL, Redis, the AI provider and registered connector hosts.
- [ ] Resource limits: `PRA_UPLOAD_MAX_*`, worker concurrency, request body size at the proxy (`client_max_body_size`).
- [ ] Run `docs/TEST_REPORT.md` suite against the target database before go-live; `/readyz` green.

## Upgrades

Migrations are forward-only in production (`alembic upgrade head`); versions, snapshots, decisions and approvals are
immutable, so upgrades never rewrite history. Deploy `api` first (migrations), then `worker`, then `web`.
