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

## Step 3: artifact registry (developer OK'd 16 Nov 2026)
| Topic | Decision | Status |
|---|---|---|
| Registry permissions | `registry.read` for every persona except Viewer; `registry.manage` (drafts, clone) for Data Steward + Agent Admin; `registry.publish` (publish, deprecate, retire) for Agent Admin | Adopted |
| Review before publish | Canonical schema, lookup, transform and crosswalk publish directly with `registry.publish`; the submit → review → approve flow comes with Mapping Rules (Step 5) | Adopted |
| Scope of artifacts | Tenant-scoped; each tenant is seeded with its own `TELCO-CANONICAL@1.0` (`python -m app.cli seed-artifacts`) | Adopted |
| Version numbers | `major.minor`; a new version is numbered after the highest existing one (minor or major bump), so numbers never collide; refs are `KEY@1.0` | Adopted |
| Artifact keys | Unique per tenant across all kinds, so `KEY@1.0` is unambiguous | Adopted |
| Digest | `sha256:` over canonical JSON (sorted keys, compact, UTF-8) of kind + key + content; version number not included, so unchanged content keeps its digest. Fractional numbers are rejected in content (write decimals as strings) because their text form is not stable | Adopted |
| Drafts | One draft per artifact at a time; drafts may hold invalid content; validation runs on validate and on publish. A draft can be discarded (deleted); published versions never | Adopted |
| Lookup / crosswalk targets | Point to a canonical schema by key; checked on publish against the version that is selectable at that moment. Exact pinning of schema versions happens in mappings (Step 5) | Adopted |
| Validity window | Optional `valid_from` / `valid_to` per version, immutable once published; "selectable" = PUBLISHED and today inside the window | Adopted |
| Domain events | Outbox table written in the same transaction; relayed by a thread in the worker to the `EventPublisher` port (log locally); at-least-once, dedupe by `event_id` | Adopted |

## Step 4: Evidence Hub (developer OK'd 16 Nov 2026)
| Topic | Decision | Status |
|---|---|---|
| Number of evidence domains | 7 (as on the screen, in the user guide and in CLAUDE.md). ORDER_BILLING_ASSURANCE from the field spec is added once its template columns are specified | Adopted |
| Who uploads | `evidence.upload` for Data Steward **and Portfolio Manager**; choosing domains needs `run.create` | Adopted |
| Proposed domains | Product Catalog + Lifecycle always; objectives add: duplicates → CPQ, Price Book, Technical; BSS sunset → Technical, Provisioning, Inventory; fallout → Provisioning, Inventory; margin → Price Book, CPQ; migration → Technical, Provisioning, Inventory; standardisation → CPQ, Technical. User confirms; stored as `run.evidence_domains` (NULL = proposal applies) | Adopted |
| Personal data | Upload is **rejected** if a header looks like subscriber/personal data (MSISDN, IMSI, e-mail, phone, customer name/id, address, postcode, date of birth, account number, …) or values look like e-mail addresses. Aggregates such as "Subscriber Count" are allowed | Adopted |
| Limits | 10 MB, 20 sheets, 100,000 data rows per sheet, 500 columns, 200 MB unpacked, 2,000 parts (all `PRA_UPLOAD_*` settings) | Adopted |
| Rejected files | Deleted from storage; the upload record and reasons are kept (audit). Quick-check failures are not stored at all; they are audited and answered with 422 | Adopted |
| Duplicates | The same file (SHA-256) uploaded again to the same run + domain returns the existing upload (`duplicate: true`), unless the earlier one was rejected | Adopted |
| Templates | Governed templates are registry artifacts (kind EVIDENCE_TEMPLATE, `TPL-…@1.0`): columns → canonical fields, lookups for accepted values; downloadable as .xlsx with a Data and an Instructions sheet | Adopted |
| Source schema identity | Fingerprint = SHA-256 of the ordered, whitespace-normalised header names. Column types are stored for information but are not part of the identity | Adopted |
| Header row | Detected automatically in the first 20 rows; the user may choose any row in the first 50 | Adopted |
| Live connectors | Source types API_CONNECTOR, SECURE_QUERY and SCHEDULED_EXTRACT are shown as "available later" until Step 14 | Adopted |
| Scanner outage | If the scanner is unreachable the job fails and the upload stays in quarantine (PROCESSING); automatic retry is to be added with the real scanner | Open |

## Candidate guides (developer OK'd 16 Nov 2026)
| Topic | Decision | Status |
|---|---|---|
| Scoring formula | Opportunity Score of the guides replaces the CLAUDE.md weights (CLAUDE.md updated); thresholds 80 / 60 kept | Adopted |
| Duplicate type | One type NEAR_DUPLICATE with strength STRONG (≥ 0.90) or PROBABLE (0.80–0.899) | Adopted |
| CAND-1042 0.87 | AI confidence of the accepted explanation | Adopted |
| Aggregate evidence | Two new evidence domains with templates in Step 6: ORDER_BILLING_ASSURANCE (fallout, manual touch, incidents, billing dependencies) and CUSTOMER_REVENUE_AGGREGATES (active subscriptions, protected-price contracts, MRR); aggregates only | Adopted |
| Home "Blocked" KPI | Counts active blocker records (consequence BLOCKED), as on Run Overview | Adopted |
| Low Adoption | Saved view exists but stays empty until a detector formula is specified | Adopted |

## Step 5: Mapping Rules (developer OK'd 16 Nov 2026)
| Topic | Decision | Status |
|---|---|---|
| Who maps | `evidence.map` (Data Steward): create, edit, validate, submit, apply | Adopted |
| Approval | New `mapping.approve` for Data Steward and Agent Admin; the submitter can never approve their own version (separation of duties); Return and Reject need a comment | Adopted |
| Publish | `mapping.publish` for Agent Admin; publishing supersedes the previous published version of the same mapping | Adopted |
| Warnings | A version with warnings can be submitted only with an explicit acknowledgement; blocking errors prevent submission | Adopted |
| Reuse | Suggestions reuse the latest published mapping of the tenant with the same domain and header fingerprint | Adopted |
| Return / Reject | Return sends the version back to DRAFT (editable again, content hash cleared); Reject retires the version (clone to start again) | Adopted |
| Rows with errors at apply time | Rejected individually and listed (row + reasons); other rows are staged. Conflicting values for the same entity key inside one file reject that record | Adopted |
| Apply compatibility | A published mapping can be applied to any confirmed upload of the same source whose schema contains every column the rules use | Adopted |
| Staged data | Staged records, relationships and field lineage are append-only (database triggers); a new apply creates a new execution | Adopted |

## Step 6: reconciliation and snapshots (developer OK'd 16 Nov 2026; built 17 Nov 2026)
| Topic | Decision | Status |
|---|---|---|
| Sources per domain | Several Excel sources per domain, each with a system code (e.g. SALESFORCE, DYNAMICS). The first upload creates the default "Excel" source; more are added in the Excel panel | Adopted |
| Data as of | Entered when confirming a sheet (default today, never in the future); freshness is measured from it | Adopted |
| Freshness limits | Catalog, CPQ 90 days; price book, lifecycle, inventory, order/billing/assurance, customer/revenue 30 days; technical catalog, provisioning 180 days (in TELCO-PRECEDENCE@1.0) | Adopted |
| Precedence policy | Versioned registry artifact TELCO-PRECEDENCE (kind PRECEDENCE_POLICY): per field (or Entity.*) an ordered list of DOMAIN or DOMAIN:SYSTEM; then freshness; then newer as-of; then source name | Adopted |
| Material fields | Product name/family/status, lifecycle status and dates, list price and currency, inventory status and count, subscriptions, protected-price contracts, MRR, legacy billing flag, target compatibility. Material conflicts need a Data Steward; other conflicts are settled by policy and shown | Adopted |
| Decisions | SELECT_SOURCE, MANUAL (typed like the field; rationale required), DEFER (rationale required; field left empty and flagged conflicted; not a blocker). Decisions are append-only and re-applied while the conflicting values are unchanged | Adopted |
| Snapshot approval | Data Steward (`snapshot.approve`), no separation of duties. Blocked while: material conflicts are open, newer applies exist, a newer reconciliation exists, the run is not configuring, or the reconciliation was already approved | Adopted |
| Snapshot numbering and evidence ids | SNAP-001 … per run; parent pointer; the previous snapshot becomes SUPERSEDED. Evidence id = "EV-" + 12 hex of SHA-256(entity, key, field, value): unchanged facts keep their id in later snapshots | Adopted |
| New domains | ORDER_BILLING_ASSURANCE (one row per product + period + billing system) and CUSTOMER_REVENUE_AGGREGATES (one row per product + period), with templates. Proposed by objectives: duplicates → customer/revenue; BSS sunset and migration → both; fallout → order/billing; margin → customer/revenue | Adopted |
| Minimum group size | Field rule `minCohort` (10 on activeSubscriptions): counts of 1 to 9 are rejected at mapping as a privacy finding | Adopted |
| Canonical schema 1.1 | Adds CustomerRevenueAggregate, OrderAssuranceAggregate, BillingSystem, BillingDependency and relationship BILLED_BY. Existing tenants are upgraded by the seed; runs pinned to 1.0 keep it | Adopted |
| Suggestions and data quality | Suggestions only check that a column's type fits a field; value problems (minimum, group size, pattern, length) are left to validation so they are reported, not hidden | Adopted |
| Template columns feeding several fields | Template columns may list `alsoTargets` (e.g. Product ID feeds the aggregate and the billing dependency); the mapping grid shows the extra rules below the grid | Adopted |

## Step 7: evidence readiness (developer OK'd and built 17 Nov 2026)
| Topic | Decision | Status |
|---|---|---|
| Subjects | Coverage is measured over every Product in the snapshot. Filtering by the run's product families comes with candidate analysis (Step 10) | Adopted |
| Thresholds | AVAILABLE at 80% coverage or more; PARTIAL from 1% to 79%; NOT_ASSESSED (shown as "Missing") at 0%. Coverage is rounded down | Adopted |
| BLOCKED | Only when every source of a CRITICAL domain is older than its freshness limit. Stale evidence in other domains and deferred (conflicted) facts are shown as flags only | Adopted |
| Overall completeness | Weighted average coverage of the selected domains: CRITICAL 3, HIGH 2, MEDIUM 1, LOW 1. Stale and conflicted flags do not reduce it | Adopted |
| Importance | From the spec's evidence policy matrix: CRM, technical catalog, provisioning, inventory and customer/revenue aggregates are critical; CPQ, lifecycle and order/billing/assurance are high; price book is medium. Order/billing missing: "operational burden and billing dependencies partial". Customer/revenue missing: "customer impact unknown; retire/migrate approval blocked" | Adopted |
| Dispositions (rule 6) | Domains that must be AVAILABLE: RETAIN CRM; SIMPLIFY CRM + CPQ; CONSOLIDATE and STANDARDISE CRM + technical catalog + provisioning; MIGRATE CRM + provisioning + inventory + customer/revenue; RETIRE CRM + lifecycle + inventory + customer/revenue; INVESTIGATE none. Domains not selected for the run count as not assessed | Adopted |
| Readiness policy | Versioned registry artifact TELCO-READINESS (kind READINESS_POLICY): importance, coverage measures, consequence and action text per status, assessments, dispositions. Calculated from the current approved snapshot; a preview of the latest reconciliation is shown but never used for decisions | Adopted |

## Step 8: AI runtime (built 17 Nov 2026 in continuous mode; developer to review)
| Topic | Decision | Status |
|---|---|---|
| Registry, not code | Approved models and endpoints are registry artifacts (kind AI_DEPLOYMENT); the tenant's runtime is the artifact TELCO-AI-RUNTIME (kind AI_RUNTIME_PROFILE). Changing it on the AI Runtime screen publishes a new version | Proposed |
| Strategies | CUSTOMER_REGISTRY uses deployments hosted in the customer's registry; PLATFORM_MANAGED uses the platform's configured model (standalone: PRA_AI_PROVIDER; AI Studio: the studio's AI service); BYO_ENDPOINT uses customer-hosted endpoints; NO_LLM uses templates only | Proposed |
| Bring-your-own endpoint contract | `POST {endpoint}` with JSON {task, system, prompt, schema, max_tokens, model} and a Bearer secret; reply {output} or {text}. https only (http only for localhost) | Proposed |
| Routing | TASK_SPECIFIC: per task a primary and an optional fallback; QUALITY_FIRST / COST_OPTIMISED: ranked by quality or cost tier; CUSTOMER_HOSTED_ONLY: never the platform model. At most 2 models per task, then the deterministic template | Proposed |
| Governance checks | A deployment is eligible only if published, not expired, fits the strategy, its region is allowed, it is approved for the data class (AGGREGATES by default), it supports JSON-schema output, tool use is off unless allowed, and it is within the cost/latency budget | Proposed |
| Fail closed | A profile is not published while any model route has no eligible deployment; a missing credential is a warning (the template is used until it is set) | Proposed |
| Failure treatment | DETERMINISTIC_FALLBACK and ALTERNATE_APPROVED_MODEL both end in the template; HUMAN_REVIEW also flags the result for review; BLOCK_AI_EXPLANATION shows only the template | Proposed |
| Secrets | API keys are stored Fernet-encrypted per tenant (`ai_secret`), write-only through the API; deployments reference them by name (`secretRef`). PRA_ANTHROPIC_API_KEY stays as a local fallback | Proposed |
| Audit | Every AI call is logged append-only (`ai_invocation`): task, profile and deployment refs, model, attempts, errors, request and output hashes. Runs pin the profile and per-task deployment refs (used by the Run Manifest, Step 9) | Proposed |
| Permissions | AI Runtime tab and changes need `config.ai` (Agent Admin); others with registry read may view the configuration via the API | Proposed |

## Step 9: scoring configuration and Run Manifest (built 17 Nov 2026 in continuous mode; developer to review)
| Topic | Decision | Status |
|---|---|---|
| Registries | Four new kinds: DETECTOR_PACK (TELCO-RATIONALISATION-DETECTORS), SCORING_PROFILE (TELCO-PORTFOLIO-SCORING), PRIORITY_POLICY (TELCO-PRIORITY), POLICY_PACK (UK-CONSUMER-PORTFOLIO and the global TELCO-DEFAULT-POLICY). Seeds start at 1.0 (the spec's @4.2 / @2.1 / @3.2 are example numbers) | Proposed |
| Grouping policy | Folded into the detector pack (`grouping.scope`), so the candidate group key uses the pinned detector pack version | Proposed |
| Numbers in registries | Weights are integer percentages that total 100; detector thresholds are per mille (900 = 0.90), because registry content is hashed as canonical JSON without floats | Proposed |
| Applicability | Every pack declares markets (or GLOBAL), product families (or ALL), objectives, dimensions, canonical schema and a priority. Recommendation: eligible packs ranked by objective coverage, specificity (market, then family), priority, version; a tie is shown and the user chooses | Proposed |
| Precedence | Authorised run override, then policy pack, then scoring profile, then Telco default; each value shows its source | Proposed |
| Who may change what | Portfolio Manager (`run.create`) chooses packs; overriding a threshold needs `config.scoring` and the policy pack must list the setting as overridable. Human review and missing-evidence treatment are not overridable in the seeded policies; ASSUME_ZERO is not a valid value anywhere | Proposed |
| Compatibility | BLOCKING: no/ineligible pack, recommended ≤ review, human review off against policy, no approved snapshot, an AI route with no eligible model. WARNING (acknowledge before Start): selected evidence domains that are not Available, packs not covering all objectives. INFO: templates-only AI | Proposed |
| Start | Needs `run.start`, zero blocking issues and every warning acknowledged (actor and time stored). Freezes an immutable Run Manifest (exact refs and digests of detector, scoring, priority, policy, readiness, precedence and canonical artifacts; snapshot code and hash; resolved settings with source; AI profile and per-task routes; recommendation trace; acknowledgements) and sets the run to ANALYSING; events RunStarted and AnalysisRequested | Proposed |
| Approval routing | Lives in the policy pack: RETAIN/INVESTIGATE need product; SIMPLIFY product + commercial; CONSOLIDATE/STANDARDISE/MIGRATE/RETIRE product + commercial + architecture; self-approval off | Proposed |

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
- CAND-1042 shows score 92 and 0.87. Decided (candidate guides, 16 Nov 2026): 92 = Opportunity Score,
  0.87 = AI confidence of the accepted explanation (never part of any score). Superseded earlier proposal.
- Wizard step 2 is labelled both "Sources" and "Evidence Hub". Use "Evidence Hub".
- AI route screen shows "Now Assist" as primary route. Treat routes as configurable entries in the model registry.
- Persona/permission matrix not specified. Proposal in BUILD_PLAN step 1.
