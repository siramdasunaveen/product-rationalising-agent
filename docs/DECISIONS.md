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

## Step 10: analysis workflow and detectors (built 17 Nov 2026 in continuous mode; developer to review)
| Topic | Decision | Status |
|---|---|---|
| Trigger | Start queues the analysis job; stages Discover, Normalize, Analyze, Assess, Recommend run in the worker, then Review is WAITING. Success moves the run to REVIEW; a failure marks the stage and run FAILED and can be retried with the same pinned configuration | Proposed |
| Source products | Products of the pinned snapshot whose family (name or code) is a selected family or one of its descendants, and whose market (if given) is a selected market. Excluded and counted: invalid (no name/family, or a disputed name), out-of-scope family, out-of-scope market | Proposed |
| Detector date | "Today" for detectors is the snapshot approval date, so the same snapshot and packs always reproduce the same findings | Proposed |
| Factor definitions | Duplicate: service = name tokens without version markers, cpq = shared configuration/eligibility options, eligibility = segment/market/channel, pricing = list price closeness, technical = shared services and resources, lifecycle = same status or one replaces the other. Overlap: capability = same service (1) or service type (0.5), segment, channel/market, use case = name tokens, substitution = replacement or parent. Legacy: end of sale, legacy billing or sunset in horizon, legacy workflow, replacement with legacy architecture; signal 0.6 + 0.1 per condition | Proposed |
| Missing factors | A factor without evidence is NOT_ASSESSED and left out with the weights re-normalised (never 0). A pair needs at least 60% of the factor weight assessed, otherwise no finding | Proposed |
| P95 baseline | Complexity and operational burden normalise to the scope family's P95 only with at least 10 products that have the value; below that they are not assessed for that family (a P95 of fewer values is just the maximum) | Proposed |
| Pairs | Duplicate and overlap pairs are only compared within the same scope family | Proposed |
| Candidate codes | CAND-0001 … per run; a reassessment that finds the same product set keeps the code and the earlier candidate becomes SUPERSEDED with a pointer to its successor | Proposed |

## Step 11: assessment, priority, blockers, Run Overview (built 17 Nov 2026 in continuous mode; developer to review)
The guides give the Opportunity Score and priority formulas and the CAND-1042 values, but not how most inputs are computed. These definitions fill the gaps; all are deterministic and evidence-based.
| Topic | Decision | Status |
|---|---|---|
| Score inputs | detectorSignal = strongest finding of the primary type; commercialValue = percentile of the changed (source) product's MRR, else its subscriptions, among the run's source products; technicalFeasibility = mean of shared services+resources (pairs) and provisioning compatibility (COMPATIBLE 1, PARTIAL 0.5, INCOMPATIBLE 0, worst workflow); operationalBenefit = max of burden/complexity finding signals and min(1, fallout multiple / 3); strategicAlignment = 1.0 if the primary type serves a run objective, 0.7 if a supporting type does, else 0.4 | Proposed |
| Missing inputs and rounding | An input without evidence is left out and the weights re-normalise; at least 60% of the weight must be assessed, otherwise no score (NOT_ASSESSED). Score = ROUND half up of 100 x weighted value; guardrails compare the integer score. CAND-1042 inputs give 92 | Proposed |
| Dimensions | COMMERCIAL: CRM, CPQ, price book, lifecycle, customer/revenue (CRM mandatory). TECHNICAL: technical catalog, provisioning (both mandatory), inventory. EXECUTION: inventory, customer/revenue (mandatory), order/billing, lifecycle. Missing mandatory evidence -> missingEvidenceTreatment (NOT_ASSESSED, BLOCK -> BLOCKED, REVIEW_REQUIRED -> PARTIAL); stale/conflicted evidence per the stale and conflict treatments; else ASSESSED when coverage >= minimum evidence coverage, otherwise PARTIAL. Scores: commercial similarity (pairs) or signal; technical measure as above; execution = mean of compatibility, legacy billing (0.5), protected contracts (0.8), legacy inventory (0.5) | Proposed |
| Technical equivalence | PASS when TECHNICAL is ASSESSED with score >= 80 (dimensionRules in the Scoring Profile may change it) | Proposed |
| Guardrail | Mandatory dimensions: COMMERCIAL, plus TECHNICAL when the technical equivalence gate applies to the action. BLOCKED / NOT_ASSESSED when a mandatory dimension is; RECOMMENDED when score >= recommended and mandatory dimensions ASSESSED; REVIEW_REQUIRED when score >= review; else BELOW_REVIEW | Proposed |
| Recommendation | Pairs: target = declared replacement, else the only eligible (ACTIVE, not end-of-sale) product, else the larger base. Duplicate + technical equivalence PASS -> CONSOLIDATE; technically different -> MIGRATE (legacy source with footprint) or STANDARDISE (rule 4); technical NOT_ASSESSED -> preliminary CONSOLIDATE with an evidence blocker (CAND-1042 gap branch; only INVESTIGATE can be submitted until resolved, rule 5). Overlap -> SIMPLIFY keeping the target. Legacy: obsolete + footprint -> MIGRATE (rule 3), obsolete without footprint -> RETIRE, footprint unknown -> INVESTIGATE. Complexity/burden -> SIMPLIFY. Missing commercial evidence -> INVESTIGATE | Proposed |
| Conditions | PRESERVE_CONTRACTED_PRICE_UNTIL_RENEWAL when protected-price contracts > 0; MIGRATE_LEGACY_BILLING_DEPENDENCY; MIGRATE_ACTIVE_BASE. Conditions never change the action | Proposed |
| Below review | NO_RECOMMENDATION: no action, reason codes kept; INVESTIGATE: action INVESTIGATE; HIDE_FROM_DEFAULT_VIEW: action kept, candidate hidden from default views but still counted | Proposed |
| Priority factors | businessImpact = mean of portfolio reduction by action (CONSOLIDATE/RETIRE 100, MIGRATE 80, STANDARDISE/SIMPLIFY 60) and the strongest complexity/burden signal; exposure = max percentile of the source product's subscriptions, MRR and protected contracts; operationalBurden = 100 x burden finding; legacyUrgency from the earliest sunset (else end-of-sale) date vs snapshot date (<=90d 100, <=180 80, <=365 60, <=730 40, later 20; no date 0); regulatoryRisk not assessed (no regulated-service field yet); dependencyCriticality = 100 blocked dependency, 70 legacy billing, 40 legacy workflow, 0 none. At least 50% of the weight must be assessed. Overrides raise only: SUNSET (<=90d), DEPENDENCY (>=70), CUSTOMER_IMPACT (source subscriptions >= 50,000), REGULATORY | Proposed |
| Signal and overlap | The score's signal is the primary finding's signal (the maximum is kept separately). OVERLAP is not raised when the pair is already a probable duplicate (section 2.2), so detectorSummary.overlap excludes those pairs | Proposed |
| Impact facts | Facts of the product being changed (CAND-1042 reports V1's 86,214 subscriptions and £480k MRR); billing systems = distinct non-deprecated billing dependencies; fallout multiple = product fallout rate / family rate, needing at least 3 products with orders | Proposed |
| Blockers | Persisted records, codes BLK-0001 per run. EVIDENCE: a domain the action needs (readiness disposition matrix, rule 6, plus the technical gate) is missing; stale evidence (BLOCK -> blocking, REQUIRE_REFRESH -> warning). VALIDATION: unresolved material conflict (BLOCK -> blocking, steward review -> warning). DEPENDENCY: incompatible provisioning workflow; legacy billing with no target billing system. Severity: dependency/validation/evidence on a critical domain HIGH, others MEDIUM, warnings LOW; one level higher when the source has >= 50,000 subscriptions; never copied from priority. Due date by severity (5/10/20/30 days), owner role by domain | Proposed |
| Blocker lifecycle | Re-raised blockers keep their code; ones no longer raised for a reassessed candidate become RESOLVED; ones whose candidate disappeared are superseded and not applicable | Proposed |
| Candidate Status | BLOCKED (any active blocking non-evidence blocker) > EVIDENCE_GAP (active blocking evidence blocker or mandatory dimension not assessed) > APPROVAL > DECISION > REVIEW; APPROVED after approval | Proposed |
| Run Overview | Attention Required = top 5 active, applicable, blocking blockers (warnings stay on Candidate Review) with the total count; metrics are null until an analysis succeeds; projectionVersion increases with every assessment | Proposed |

## Step 12: Candidate Explorer and Candidate Review (built 17 Nov 2026 in continuous mode; developer to review)
| Topic | Decision | Status |
|---|---|---|
| Saved views | Nine built-in team views (All, High priority, Blocked, Evidence gaps, Near duplicates, Legacy, Operational burden, Ready for decision, In approval) are code, not data; personal views are configuration only (filters, sort, visible columns) owned by one user, never shared, never copies of candidates. Explicit query parameters replace the same key of the view; the response reports exactly the filters applied (effectiveFilters) and inventoryTotal separately from filteredCount | Proposed |
| Hidden candidates | Candidates hidden by the below-review treatment stay out of every view unless includeHidden is set; they are always counted in inventoryTotal | Proposed |
| Sorting | Priority and status sort by severity order, never alphabetically; candidate id breaks every tie | Proposed |
| Decision readiness | Seven server-side gates per selected action: actionPermitted (candidate type plus rules 3 and 4), targetEligibility, minimumEvidenceCoverage (commercial, plus technical where the technical gate applies), technicalEquivalenceGate (NOT_ASSESSED is never a FAIL), criticalConflicts, freshness, preDecisionPolicy (rule 6 disposition domains plus blocking dependency/policy blockers). Continue to Decision is enabled only when every gate passes or the action is INVESTIGATE; the pre-check fails closed (409 stale) when the analysis changed since the client loaded the candidate | Proposed |
| Gate facts | Persisted with the candidate at assessment (coverage per product, stale domains, dimension status/score/coverage, footprint, target eligibility, technical pass mark) so gates never reload the graph; a candidate assessed before gate facts existed is BLOCKED until reassessed | Proposed |
| Evidence tab | Six fixed rows (product equivalence, customer impact, operational impact, dependency, technical fulfilment, lifecycle) with the evidence ids, source domain, snapshot, earliest observed date, freshness (STALE if any fact is stale) and validation (CONFLICTED if any fact is conflicted). Rows without evidence show "Not assessed", never a value | Proposed |
| Alternatives | Every action the candidate type allows, each evaluated with the same gates; FEASIBLE only when all pass, NOT_ASSESSED when a gate is not assessed, else BLOCKED. Choosing an alternative changes nothing until the decision is submitted | Proposed |
| AI explanation | Optional, on request, task RECOMMENDATION_REASONING through the AI runtime; the data block is marked untrusted; the schema requires 1-8 sentences each citing evidence ids that must exist for the candidate; otherwise retry then the deterministic template. AI confidence (per mille) is stored only with an accepted LLM explanation, flagged below threshold when under the runtime profile's aiConfidenceThreshold, shown as a percentage and never used in any score or gate. Explanations and comments are append-only | Proposed |
| Discussion | Comments are collaboration only (never evidence), 1-2000 characters, control and bidi characters stripped, audited | Proposed |
| Candidate references | A candidate is addressed by its stable code (CAND-0001) or id; the id of a superseded version resolves to the current candidate with the same code | Proposed |

## Step 13: Evidence gaps and requests (built 17 Nov 2026 in continuous mode; developer to review)
| Topic | Decision | Status |
|---|---|---|
| Gap = blocker | An evidence gap is an active, applicable EVIDENCE blocker (EVIDENCE_REQUIRED from the disposition matrix and the technical gate, EVIDENCE_STALE from the freshness rule). One request per candidate + domain; asking again opens the existing request (never a duplicate). Codes ER-101, ER-102 ... per run | Proposed |
| Contract | The logical contract (purpose, metrics, grouping, filters, return, freshness days from the readiness policy else 30, privacy, products, what it unlocks) is generated per domain and disposition; users choose only owner, due date and fulfilment channel (SECURE_QUERY default, API_CONNECTOR, SCHEDULED_EXTRACT, EXCEL_AGGREGATE) | Proposed |
| Lifecycle | DRAFT -> OPEN (assign) -> IN_PROGRESS (start creates the channel job; Excel points at the governed template and the Evidence Hub upload) -> RECEIVED (system, when a new snapshot is approved that carries a new execution of the request's domain) -> ACCEPTED (Data Steward) or REJECTED (reason required; the same return is never received again, a corrected one is) -> SUPERSEDED when another accepted return or a reassessment closed the gap. Every change is an append-only event | Proposed |
| Evidence lock | After Start, evidence (sources, uploads, mappings, reconciliation, snapshot approval) is editable again only while the run is in REVIEW or APPROVAL with an OPEN, IN_PROGRESS or REJECTED request; otherwise it stays locked. Returned evidence goes through the same validation, mapping, reconciliation and approval as the original | Proposed |
| One return, many requests | Accepting one request accepts every RECEIVED request that the same snapshot satisfies (coverage checked per product with the readiness measures) and queues one reassessment on that child snapshot with the run's pinned manifest; the run goes ANALYSING -> REVIEW | Proposed |
| Targeted reassessment | The reassessment re-runs the deterministic pipeline on the child snapshot; candidate codes are kept, unchanged candidates reproduce the same assessment hash, and only the candidates whose facts changed get new values. Blockers no longer raised are RESOLVED and their requests are completed (reassessment DONE) or superseded | Proposed |
| Permissions | Create/assign: decision.submit or evidence.map. Start: request.fulfil, evidence.upload or evidence.map. Accept/reject: snapshot.approve. Read: candidate.view, request.fulfil, evidence.map or snapshot.approve | Proposed |
| Seed scenario | The CAND-1042 gap branch is the test `test_gap_branch_end_to_end` (PROVISIONING missing -> ER-101 -> SNAP-002 -> reassessment); the guide's ER-102/ER-103 numbering is illustrative | Proposed |

## Step 14: Decisions and approvals (built 17 Nov 2026 in continuous mode; developer to review)
| Topic | Decision | Status |
|---|---|---|
| Decision aggregate | One decision per candidate and run (DEC-0001 ...). Content lives only in immutable versions (append-only table): disposition, target, effective date, owner, rationale (50-4000), commercial treatment, conditions carried from the recommendation, the analysis/snapshot/manifest it was taken on, the recommendation it was taken on and a SHA-256 content hash. Every save, including a return, is a new version; prior versions stay read-only | Proposed |
| Form rules | Disposition must be one the candidate type allows; target required only for CONSOLIDATE/STANDARDISE/MIGRATE and must be an active product of the snapshot; effective date a future business day (Monday-Friday); owner required (defaults to the submitter). The commercial treatment defaults from the condition (PRESERVE_CONTRACTED_PRICE -> "Grandfather price until renewal") | Proposed |
| Submission gate | Submit re-checks the current version (id and hash) against the candidate's current assessment, then decision readiness for the disposition (rule 6 and the technical gate); INVESTIGATE always submits (rule 5); executionReadinessGate REQUIRED_FOR_DECISION needs EXECUTION assessed; REQUIRED_FOR_APPROVAL is checked at approval instead. Blocked submissions return 409 submission_blocked with every reason | Proposed |
| Approval route | Scopes from the policy pack route of the disposition, escalated by customer impact (source subscriptions >= the CUSTOMER_IMPACT override threshold adds commercial) and by a legacy billing dependency on consolidate/migrate/retire (adds architecture). One approval task per scope, SLA 10 days. The run moves REVIEW -> APPROVAL on the first submission | Proposed |
| Policy checks | Re-evaluated on every read of the Approval screen: decision version (BLOCKED when the candidate was reassessed since), commercial treatment (BLOCKED when protected-price contracts exist without a treatment), customer notice (WARNING: 30-day notice for N contracts), revenue impact, architecture (BLOCKED when technical equivalence is not assessed and the disposition needs it), execution readiness (REQUIRED_FOR_APPROVAL), evidence and policy gates. Overall PASS / PASS_WITH_WARNING / BLOCKED; nothing BLOCKED can be approved | Proposed |
| Actions | APPROVE, RETURN, HOLD, REJECT by a holder of a pending scope on the exact version and hash; RETURN/HOLD/REJECT need a comment; self-approval (owner, author or submitter) is refused when the policy pack disallows it; reject/return cancel the other pending tasks; the decision is APPROVED when every scope approved. Each action is an append-only record with actor, roles, auth context, version, hash, policy result and comment (audit retention 7 years) | Proposed |
| Candidate Status | Follows the decision: DRAFT/RETURNED -> DECISION, SUBMITTED/ON_HOLD -> APPROVAL, APPROVED -> APPROVED; blockers and evidence gaps keep precedence | Proposed |
| Material change | After a reassessment, an open or approved decision taken on an earlier assessment is SUPERSEDED (pending tasks cancelled, reason recorded) when the recommended action changed, the suggested target changed, the candidate became BLOCKED/EVIDENCE_GAP, or readiness no longer passes for its disposition; the owner saves a new version and a new route is created on submission | Proposed |
| Home KPIs | Rationalisation Opportunities = active candidates of the latest successful analysis of in-progress runs; Awaiting approval = decisions SUBMITTED/ON_HOLD; Blocked = active blocking blockers of in-progress runs | Proposed |

## Production security (developer's request, 17 Nov 2026)
| Topic | Decision | Status |
|---|---|---|
| Goal | The agent must be deployable to live production with the security controls any production application follows | Adopted |
| Baseline | OWASP ASVS 4.0.3 Level 2 + OWASP API Security Top 10 (2023) + GDPR data minimisation. No certification (SOC 2, ISO 27001, PCI) is specified; if one is required later it mainly adds process and documentation | Adopted |
| Responsibilities | Controls in the code are fixed in the code; controls of the hosting platform (TLS, WAF, SSO/MFA, key vault, backups, network isolation, log monitoring) are listed as deployment requirements and checked at startup where possible | Adopted |
| Process | A whole-application security audit with adversarial verification now; a security review of every remaining build step before it is committed | Adopted |

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
