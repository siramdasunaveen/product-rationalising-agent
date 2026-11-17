# Security

Written for the security reviewer and the deploying engineer. Baseline: OWASP ASVS 4.0.3 Level 2, OWASP API
Security Top 10 (2023), GDPR data minimisation (DECISIONS.md, "Production security"). This file states what the
code enforces, what the platform must provide, and what is still open — honestly.

## Enforced in the code

| Area | Control |
|---|---|
| Authorisation | Server-side permission check on every route (`require_permission`); the UI only hides. Tenant filter on every query. Approval limited to the principal's scopes; separation of duties on mapping approval and decision self-approval; approvals act on an exact version + content hash. |
| Authentication (standalone) | scrypt password hashes, constant-time dummy check for unknown emails, 5 failures → 15-minute lockout, generic error message, every attempt audited; JWT with issuer, `jti`, expiry; sign-up disabled. |
| Input | Pydantic models with lengths and enums on every body/query; filters and sorts validated against allow-lists; comments stripped of control/bidi characters; AI output validated against JSON schema with citation checks; canonical JSON rejects floats. |
| Files | `.xlsx` only; macros, password-protected, malformed, oversized, too many sheets/rows/columns/parts rejected; quarantine → scan → checked storage; duplicate detection by SHA-256; no subscriber-level data (metadata and aggregates only). |
| Secrets | AI and connector credentials Fernet-encrypted at rest, write-only through the API, never logged or returned; connector hosts restricted to Salesforce domains. |
| Immutability and audit | Append-only tables with DB triggers: snapshots, run manifests, AI invocations, candidate explanations/comments, evidence request events, decision versions, approval actions. Audit records carry actor, roles, timestamp, auth context, entity, payload and hash chain; retention target 7 years. |
| Determinism | No LLM computes a metric, status, gate or approval; AI explanations are optional, cited and fall back to templates. |
| Observability | Request context (request id, client IP, user agent, tenant, user, job id) and a structured `pra.security` event sink for failures, denials, lockouts and configuration changes. |
| Transport/edge | nginx image sends `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy` and a strict CSP; body size capped. |

## Required from the platform

TLS everywhere, SSO/MFA and session revocation (AI Studio identity), WAF/rate limiting on login and uploads, a real
malware scanner, KMS-backed secrets, authenticated/TLS Redis on a private network, encrypted storage, backups,
log shipping to a SIEM, egress allow-listing. See `docs/DEPLOYMENT.md`.

## Known gaps (from the 17 Nov 2026 audit; to be closed in the security hardening branch)

| Id | Finding | Severity | Status |
|---|---|---|---|
| SEC-01 | A publicly known default `PRA_JWT_SECRET` is accepted; production settings are not validated at start-up | Critical | Open — add a settings validator that refuses placeholder secrets and dev defaults when `PRA_ENVIRONMENT` is not dev/test |
| SEC-02 | No MFA / step-up for privileged actions in standalone mode | High | By design: standalone login is for development; production authentication comes from AI Studio SSO |
| SEC-03 | JWTs are not revocable on logout, deactivation or password change | High | Open — short TTL plus a `jti` deny-list in Redis |
| SEC-04 | No rate limiting on `/auth/login`; lockout can be used as a denial-of-service lever | High | Open — edge rate limiting now; per-IP throttle in the API planned |
| SEC-05 | Request bodies are read before authentication and no global body limit is set | High | Mitigated at the proxy (`client_max_body_size`); API-level limit planned |
| SEC-06 | Redis job channel is unauthenticated (pickle → code execution in the worker) | High | Platform: Redis AUTH + TLS + private network; code: restrict job function paths to the `app.jobs` package |
| — | BYO AI endpoint and connector URLs could target internal hosts (SSRF) | High | Connector hosts are allow-listed to Salesforce domains; BYO endpoint allow-list planned |
| — | Excel discovery trusts the sheet `<dimension>`; unbounded cell/header lengths | High | Open — cap by streamed rows and truncate values |
| — | Audit hash chain is unkeyed and not externally anchored | High | Open — HMAC with a KMS key and periodic anchor |
| — | Registry routes should also require `config.*` permissions for policy/scoring/AI artifacts | High | Open |
| — | CLI user administration is not audited | High | Open |

The full raw audit (133 findings) is kept with the hardening branch; items above are those confirmed so far.

## Data protection

The agent stores product metadata, aggregates and user identifiers only. No MSISDNs, names of subscribers or raw
customer records are accepted (evidence contracts say so explicitly). Personal data: user id, name, email of
operators; audit records; comments. Retention: audit and approval evidence 7 years; everything else for the life of
the run. Right of access/erasure is handled by the identity provider for operators; evidence has no data subjects.
