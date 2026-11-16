# Build plan

Status values: TODO · IN PROGRESS · DONE. Do one step at a time; each step ends with passing tests.

## Step 1: Skeleton and foundations · DONE
- Repo layout per CLAUDE.md; `docker-compose.yml` (Postgres 16, Redis 7); `.env.example`; README with PowerShell commands.
- Settings (pydantic-settings, prefix `PRA_`), DB session, Alembic with schema `rationalisation`.
- Ports + local adapters: auth (dev JWT login), ai (Anthropic + "none"), storage (disk), jobs (RQ), audit (DB table,
  append-only, SHA-256 hash chain), secrets (Fernet), notify (log). `adapters/studio/` stubs with docstrings.
- `manifest.json` and permissions. Proposed personas → permissions:
  Portfolio Manager (run.create, run.start, candidate.view, decision.submit), Product Owner (candidate.view,
  decision.submit), Data Steward (evidence.upload, evidence.map, snapshot.approve, conflict.resolve),
  Evidence Owner (request.fulfil), Commercial/Product/Architecture Approver (approval.act),
  Enterprise Architect (candidate.view, approval.act for architecture), Agent Admin (config.scoring, config.policy,
  config.ai, connector.manage), Viewer (read).
- `GET /me`, `/healthz`, `/readyz`. Frontend: Vite app + standalone shell (header, nav from screenshots, dev login).
- Done when: `docker compose up`, backend and frontend start, tests pass, a dev user logs in and sees the empty shell.

## Step 2: Runs and scope · TODO
- Tables: runs (name, portfolio, market, product families, assessment period, owner, objectives, stage, pinned versions).
- Screens: Portfolio Runs (screens/01), Create Run step 1 Scope (screens/02). Run ID + audit event on create.
- Done when: create, list, search, reopen a run; validation of required fields; tests.

## Step 3: Connector framework + Excel adapter · TODO
- `SourceAdapter` port: test_connection, list_datasets, describe, extract.
- Governed Excel templates for 7 domains (columns in user guide §2), downloadable.
- Upload: .xlsx only, size/sheet limits, macro/password/malformed rejection, quarantine → checked.
- Preview: choose sheet + header row. Mapping to canonical fields; saved mapping templates with version;
  AI mapping suggestion sees only headers + 5 sample rows.
- Validation: required fields, types, references, duplicates, effective dates; errors block, warnings by policy.
- Screen: Evidence Hub (screens/03).
- Done when: each of the 7 templates can be uploaded, mapped, validated with clear row errors; tests with sample files.

## Step 4: Canonical model, reconciliation, snapshot · TODO
- Canonical tables: products, offers/bundles, cpq rules, price books, lifecycle, service specs (CFS/RFS),
  resource specs, provisioning workflows, inventory aggregates, relationships (the Telco graph).
- Matching on Product ID / Service Spec / Resource Spec / approved crosswalk; fuzzy = suggestion only.
- Field-level precedence (CRM name, CPQ config, ERP list price, PLM lifecycle), freshness SLA, conflicts with
  both values for Data Steward resolution, lineage per field.
- Immutable versioned snapshot; Data Steward approves.
- Done when: two sources for the same product reconcile with visible lineage and a conflict queue; snapshot approval audited.

## Step 5: Evidence readiness · TODO
- Policy matrix (versioned) per domain: importance, missing treatment, consequence (user guide p.34–35).
- Completeness per domain and per candidate/disposition; status AVAILABLE/PARTIAL/NOT_ASSESSED/BLOCKED.
- Screen: Evidence Readiness (screens/11).

## Step 6: Scoring, AI runtime, analysis run · TODO
- Versioned scoring profiles (Telco Portfolio v2.1), safety thresholds; Scoring & AI Route step (screens/04),
  Review & Start (screens/05) locks config and pins versions.
- Model registry + task routing (matching, reasoning, dependency analysis, explanation) + fallback + no-LLM mode (screens/16).
- Background job with stages Discover → Normalize → Analyze → Assess → Recommend → Review → Approve; Run Overview (screens/06).
- Candidate generation (deterministic similarity ≥ 0.88 etc.), three-dimension assessment, rule-based disposition
  guards (CLAUDE.md rules 3–5), AI explanation with evidence citations validated against lineage.

## Step 7: Candidate Explorer and Review · TODO
- Explorer with filters, saved views, no bulk irreversible actions (screens/07).
- Review: recommendation, KPIs, tabs Evidence / Impact / Alternatives / Policies / Discussion (screens/08, 12).

## Step 8: Evidence requests · TODO
- Generated from gaps; logical evidence contract (purpose, metrics, grouping, filters, return, freshness, privacy);
  fulfilment method (secure query, API, scheduled extract, Excel aggregate); lifecycle DRAFT → OPEN → IN_PROGRESS →
  RECEIVED → ACCEPTED/REJECTED/SUPERSEDED; one return can satisfy several requests; accept → new snapshot → reassess.
- Screens 13, 14, 15. Seed scenario: CAND-1042 with ER-102 and ER-103.

## Step 9: Decisions and approvals · TODO
- Decision form (screens/09) with submission gating (rule 6); immutable versions + content hash.
- Policy-based approval routing; Approve / Reject / Return / Hold (comment required) (screens/10); APPROVED when route completes.
- End-to-end test of the CAND-1042 scenario before and after evidence acceptance.

## Step 10: Live connectors · TODO
- Salesforce adapter (product catalog, price books, Revenue Cloud/CPQ) with OAuth; reconciliation with Excel data.
- Then other domains as access becomes available.

## Step 11: Handover package · TODO
- Docker image(s), integration guide (env vars, migrations, expected JWT claims, ports to implement),
  OpenAPI spec, manifest, seed demo data, test report.
