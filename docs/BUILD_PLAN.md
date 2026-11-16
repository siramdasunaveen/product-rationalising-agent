# Build plan

Status values: TODO · IN PROGRESS · DONE. Do one step at a time; each step ends with passing tests.
Field-level rules for every screen are in `docs/SPEC_NOTES.md` (digest of the spec documents).
Revised 16 Nov 2026 after the Mapping Rules guide, the Scoring & AI guide, the updated user guide and the
field specification were supplied.

## Step 1: Skeleton and foundations · DONE
- Repo layout, docker-compose (Postgres 16, Redis 7), settings, Alembic (schema `rationalisation`).
- Ports + local adapters (auth, ai, storage, jobs, audit hash chain, secrets, notify); studio stubs.
- manifest.json + permissions; `/me`, `/manifest`, `/healthz`, `/readyz`.
- Email + password sign-in (standalone), demo users, Assessments shell (new design).

## Step 2: Runs and Define Scope · DONE
- Reference data (tenant-scoped): portfolio, market, product_family (tree); seed command; metadata APIs;
  people picker (owners = Portfolio Manager / Product Owner).
- Run + run_scope + objectives; run code RUN-0001 per tenant; run states (SPEC_NOTES §Run states).
- APIs: create, list/search, get, patch scope (CONFIGURING only, optimistic version), summary KPIs.
- Screens: Assessments home with real KPIs/search/reopen; Create Run wizard shell + step 1 Scope (screen 02).
- Done when: create, list, search, reopen and edit a run with every field rule from the field spec enforced
  server-side; audit on create/update; tenant isolation and permission tests.

## Step 3: Versioned artifact registry + canonical schema · DONE
- One reusable pattern for immutable, versioned, digest-identified artifacts with a status lifecycle
  (DRAFT … PUBLISHED … SUPERSEDED/RETIRED); only PUBLISHED is selectable; a change = new version.
- Registries built on it: canonical schema (TELCO-CANONICAL@x), lookup, transform, crosswalk.
- Outbox table for domain events.
- Done when: create/clone/publish/supersede versions; immutability enforced in DB and service; digests stable.

## Step 4: Evidence Hub — sources and Excel intake · TODO
- Evidence domains (8, SPEC_NOTES), source definitions (API_CONNECTOR / SECURE_QUERY / SCHEDULED_EXTRACT /
  EXCEL_UPLOAD), `SourceAdapter` port (test_connection, discover_schema, extract).
- Excel: governed templates per domain (downloadable); upload .xlsx only; size/sheet limits;
  macro/password/malformed rejection; quarantine → scan (port, stub) → checked; sheet + header row
  discovery → immutable SourceSchema version (fingerprint).
- Screen: wizard step 2 Evidence Hub (screen 03) — domain cards + harmonisation pipeline.
- Done when: each domain template can be uploaded and its schema discovered; bad files rejected with clear reasons.

## Step 5: Mapping Rules · TODO
- MappingDefinition / MappingVersion / MappingRule; types DIRECT, LOOKUP, TRANSFORM, CROSSWALK,
  RELATIONSHIP, DERIVED; deterministic suggestions (names, aliases, types, prior mappings) with confidence +
  proposedBy; optional AI suggestion sees only headers + 5 sample rows.
- Validation layers (structural, type, schema, coverage, lookup, crosswalk, relationship, transform, sample,
  security); impact preview; submit → Data Steward approve/return/reject → publish (`mapping.publish`).
- Apply job (202 + task id, idempotent per raw-object hash + mapping version) → staged canonical records +
  field/relationship lineage.
- Done when: replaying the same file with the same mapping version reproduces identical staged values and lineage.

## Step 6: Reconciliation, snapshot, Telco graph · TODO
- Canonical tables (products, offers/bundles, CPQ rules, price books, lifecycle, CFS/RFS, resources,
  provisioning, inventory aggregates, relationships).
- Identity matching (Product ID / Service Spec / Resource Spec / approved crosswalk; fuzzy = suggestion only);
  field-level precedence; freshness; conflicts with both values for Data Steward resolution; lineage per field.
- Immutable snapshot; Data Steward approves (blockers = 0).
- Done when: two sources for the same product reconcile with visible lineage and a conflict queue; approval audited.

## Step 7: Evidence readiness · TODO
- Versioned policy matrix per domain (importance, missing treatment, consequence); completeness per domain and
  per candidate/disposition; AVAILABLE / PARTIAL / NOT_ASSESSED / BLOCKED (+ stale/conflicted flags).
- Screen: Evidence Readiness (screen 11).

## Step 8: AI runtime and model registry · TODO
- Registries/deployments as data (no hard-coded models or providers); AI strategy (CUSTOMER_REGISTRY,
  PLATFORM_MANAGED, BYO_ENDPOINT, NO_LLM); task routes; routing policy; fallback chain (max 2 models, then
  deterministic template); governance checks. Anthropic becomes one provider adapter behind a deployment.
- Screen: AI Configuration (screen 16).

## Step 9: Scoring & AI Route, Review & Start, Run Manifest · TODO
- Detector, scoring and policy registries (published, versioned); deterministic recommendation + trace
  (SPEC_NOTES §Selection); guardrail fields and precedence; configuration compatibility (PASS/WARNING/BLOCKED).
- Review & Start (screen 05): summaries, limitation acknowledgement, Start freezes the Run Manifest (exact ids,
  versions, digests; never "latest").
- Done when: reopening a run reproduces the same pinned configuration after newer artifacts are published.

## Step 10: Analysis run · TODO
- Background workflow with stages Discover → Normalize → Analyze → Assess → Recommend → Review → Approve;
  Run Overview (screen 06) with metrics and blockers.
- Candidate generation (deterministic similarity ≥ 0.88 etc.), three dimensions (commercial, technical,
  execution) with NOT_ASSESSED/BLOCKED instead of low scores, guardrail evaluation, disposition guards
  (CLAUDE.md rules 3–5), optional AI explanation with validated citations.

## Step 11: Candidate Explorer and Review · TODO
- Explorer with filters and saved views, no bulk irreversible actions (screen 07).
- Review: recommendation, KPIs, tabs Evidence / Impact / Alternatives / Policies / Discussion (screens 08, 12).

## Step 12: Evidence gaps and requests · TODO
- Gap detection per emerging disposition; logical evidence contract; fulfilment channels; lifecycle
  DRAFT → OPEN → IN_PROGRESS → RECEIVED → ACCEPTED/REJECTED/SUPERSEDED; one return can satisfy several
  requests; accept → new snapshot → reassess with pinned versions (screens 13, 14, 15).
- Seed scenario: CAND-1042 with ER-102 and ER-103.

## Step 13: Decisions and approvals · TODO
- Decision form (screen 09) with submission gating (rule 6); immutable versions + content hash.
- Policy-based approval routing; Approve / Reject / Return / Hold (comment required) (screen 10).
- End-to-end test of CAND-1042 before and after evidence acceptance.

## Step 14: Live connectors · TODO
- Salesforce adapter (catalog, price books, Revenue Cloud/CPQ) with OAuth; reconciliation with Excel data.

## Step 15: Handover package · TODO
- Docker images, integration guide, OpenAPI spec, manifest, seed demo data, test report.
