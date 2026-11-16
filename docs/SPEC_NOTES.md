# Spec notes (digest)

Condensed from the documents supplied on 16 Nov 2026. Keep the original PDFs in `docs/source/`:
- `DeepKorce_MappingRules_Developer_Guide_User_vs_System.pdf` (Mapping Rules guide)
- `DeepKorce_Scoring_AI_System_Value_Computation_Guide_With_Decision_Guardrails.pdf` (Scoring & AI guide)
- `DeepKorce_Telco_Product_Rationalisation_User_Guide_Updated.pdf` (updated user guide)
- Field specification workbook (14 tabs; shared as `ilovepdf_merged.pdf`)

Where this file and the PDFs differ, the PDFs win; record the resolution in DECISIONS.md.

## Screen list (workbook tabs)
1 Portfolio Runs · 2.1 CRR Scope · 2.2 CRR Evidence Hub · 2.3 CRR Scoring · 2.4 CRR Review & Start ·
3 Monitor Analysis · 4 Candidates Explorer · 5 Review Candidates · 6 Decision · 7 Approval ·
8 Evidence Readiness · 9 Candidate Review (evidence-aware) · 10 Evidence Gap (progressive collection) ·
11 Evidence Requests · 12 Evidence Request details · 13 AI Configuration. CRR = Create Rationalisation Run.

## Global rules
- Agent recommends and explains; people decide and approve. Approval never changes a product.
- LLM only for suggestions, explanations and drafts. Never for scores, validation pass/fail, counts,
  approvals, authoritative values, routes or defaults. Every default has a deterministic trace.
- No hard-coded models/providers; registries supply PUBLISHED, non-expired artifacts only.
- Published artifacts are immutable; change = new version (parentVersion). Runs pin exact ids + digests.

## Run states and stages
- Run state: CONFIGURING; INGESTING; VALIDATING; ANALYSING; REVIEW; APPROVAL; APPROVED; FAILED; CLOSED.
- Workflow stages: Discover, Normalize, Analyze, Assess, Recommend, Review, Approve.
  Stage status: NOT_STARTED; RUNNING; COMPLETE; FAILED; WAITING.

## 01 Portfolio Runs
KPIs: Active runs, Candidates (UI: Rationalisation Opportunities), Awaiting approval, Blocked.
Search by run, portfolio or owner; saved filters; reopen. Columns: Run, Portfolio scope, Stage, Owner, Risk, Updated.
Outcome: run ID and audit context created.

## 02 Define Scope (all user-entered, NO LLM; Run Service; event RunScopeDefined)
| Key | Label | Control | Rules |
|---|---|---|---|
| runName | Run Name | text | required, 3–120, trimmed, unique among active runs in tenant |
| portfolioId | Portfolio | searchable dropdown | required; entitled portfolio metadata |
| marketIds | Market / Operating Company | multi-select | required, 1–10; controls policy, timezone, connectors |
| productFamilyIds | Product Families | tree multi-select | required, 1–100; expand descendants when configured |
| periodStart | Period From | date | required; default today − 12 months; ≤ end |
| periodEnd | Period To | date | required; default today; ≥ start; max 36 months; market timezone |
| objectives | Business Objectives | checkbox group | required, 1–6 of REDUCE_DUPLICATES, BSS_SUNSET, REDUCE_FALLOUT, IMPROVE_MARGIN, MIGRATION_READINESS, PORTFOLIO_STANDARDISATION |
| ownerId | Accountable Owner | people picker | required; Portfolio Manager / Product Owner; default current user |
Continue is enabled only after validation. Outcome: scope validated, required evidence domains proposed.

## 03 Evidence Hub
- Domains: CRM_CATALOG; CPQ; PRICE_BOOK; LIFECYCLE; TECHNICAL_CATALOG; PROVISIONING; INVENTORY;
  ORDER_BILLING_ASSURANCE. Default domains from scope/objectives; user confirms.
- Source type: API_CONNECTOR; SECURE_QUERY; SCHEDULED_EXTRACT; EXCEL_UPLOAD (policy filters allowed modes).
- Connector: approved registry; store connector id + vault secret ref only.
- Excel: .xlsx only; reject macro/password/malformed; quarantine → malware scan → parse; event RawEvidenceLanded.
- Worksheet: discovered sheets; default first valid.
- Mapping rules grid (see Mapping section). Validation status: NOT_RUN; RUNNING; PASS; WARNING; BLOCKED.
- Conflict resolution: select source / manual (needs rationale) / defer; recommendation by authority + freshness;
  human confirms; event DataStewardDecisionRecorded.
- Completeness: AVAILABLE; PARTIAL; NOT_ASSESSED; BLOCKED; STALE; CONFLICTED (see DECISIONS on STALE/CONFLICTED).
- Approve snapshot: Data Steward; blockers = 0; server recheck; versions, materialises graph/lineage, hash;
  event EvidenceSnapshotApproved.
- Excel template columns (required | optional):
  - Product/Offer: Product ID, Product Name, Product Family, Status | Offer ID, Parent Product, Market, Segment, Channel, Description
  - CPQ: Product ID, Attribute/Option ID, Rule Type, Effective From | Required/Optional, Dependency, Eligibility, Cardinality, Effective To
  - Price Book: Price Book ID, Product ID, Currency, List Price, Effective From | Segment, Channel, Market, Effective To, Tax Class
  - Lifecycle: Product ID, Lifecycle Status, Effective From | End-of-Sale Date, Sunset Date, Replacement Product ID, Owner, Rationale
  - Technical Service Catalog: Product ID, Service Spec ID, Service Type, Relationship Type | CFS/RFS, Parent Service, Resource Spec ID, Effective Dates
  - Provisioning: Product ID or Service Spec ID, Workflow ID, Rule/Action, Target System | Version, Legacy Flag, Target Compatibility, Exception Code, Effective Dates
  - Inventory: Service Instance Aggregate Key, Service Spec ID, Resource Spec ID, Status | Active Count, Variant Count, Legacy Dependency, Region, Last Updated
- Reconciliation: identity on Product ID / Service Spec / Resource Spec / approved OSS keys via governed
  crosswalks; fuzzy = suggestion only; precedence per field (CRM name, CPQ config, ERP list price, PLM lifecycle);
  freshness SLA; conflicts show both values + source + update time; lineage = system/file, workbook+sheet,
  row/key, mapping version, transformation, snapshot id. No customer-level pricing by default.

## Mapping Rules
- Types: DIRECT, LOOKUP (code → enum), TRANSFORM (deterministic), CROSSWALK (keys → one canonical entity),
  RELATIONSHIP (graph edges, e.g. Product DECOMPOSES_TO CFS, CFS REALIZED_BY RFS), DERIVED (from approved
  fields; keeps input evidence ids + rule version). No user-authored code.
- Mapping creates staged canonical records; it never chooses between sources (that is reconciliation).
- Flow: Discover (SourceSchema version) → Suggest (deterministic; optional LLM for ambiguous labels) → Map
  (draft vN; backend rejects incompatible target/type) → Validate → Review Impact (deterministic counts) →
  Submit (freeze, contentHash, Data Steward task) → Approve (exact version/hash; comment for return/reject) →
  Publish (separate permission; supersede prior).
- Model: MappingDefinition(mappingId, sourceDomain, sourceId, owner); MappingVersion(mappingId+version,
  parentVersion, sourceSchemaVersion, canonicalSchemaVersion, status, contentHash, rules[]); MappingRule(ruleId,
  sourcePath, targetPath, mappingType, lookup/transform/crosswalkVersion, required, nullHandling,
  suggestionConfidence, proposedBy, approvedBy).
- Tables: mapping_definition, mapping_version, mapping_rule, mapping_validation_run, mapping_validation_issue,
  mapping_approval, mapping_publication, source_schema, canonical_schema, source_key_crosswalk,
  mapping_impact_analysis.
- States: DRAFT, VALIDATING, VALIDATION_FAILED, READY_FOR_REVIEW, IN_REVIEW, APPROVED, PUBLISHED (only one
  usable by new runs), DEPRECATED, SUPERSEDED (pinned runs only), RETIRED.
- Approval references mappingId, version, source schema version, canonical schema version, contentHash;
  any edit invalidates it.
- Validation layers: structural, type, canonical schema, required-field coverage (Product ID 100%), lookup
  coverage, crosswalk integrity (one key → one entity), relationship integrity (no prohibited cycles), transform,
  sample execution, security (no subscriber PII). Response: status, sampleRows, mappedRows,
  requiredFieldCoverage, blockingErrors, warnings, unknownLookupValues, unresolvedCrosswalks.
- Apply: POST …/versions/{v}/apply with Idempotency-Key → 202 {taskId, statusUrl}; idempotent per raw-object
  hash + mapping version; writes staged records + lineage; emits MappingExecutionCompleted.
- Lineage record: canonicalEntityId, canonicalField, canonicalValue, sourceSystem, sourceKey, sourceField,
  sourceValue, mappingRuleId, lookupVersion, mappingVersion, snapshotId.
- APIs: create, save draft (PATCH), validate, sample-execution, impact-analysis, submit, approve|reject,
  publish, clone, compatibility-check, apply. Events: MappingDraftCreated, MappingValidationRequested,
  MappingValidationCompleted, MappingSubmittedForReview, MappingApproved, MappingRejected, MappingPublished,
  MappingDeprecated, MappingSuperseded, SourceSchemaChanged, MappingCompatibilityFailed, MappingExecutionCompleted.

## 04 Scoring & AI Route (configures and pins; calculates nothing)
| Field | Values / rule | Default |
|---|---|---|
| detectorPack | published compatible Detector Registry (e.g. TELCO-RATIONALISATION-DETECTORS@4.2) | recommended |
| scoringProfile | published Scoring Registry; weights total 100% (e.g. TELCO-PORTFOLIO@2.1) | recommended |
| recommendedThreshold | 0–100, > review | profile/policy, e.g. 80 |
| reviewThreshold | 0–100, < recommended | e.g. 60 |
| belowReviewTreatment | NO_RECOMMENDATION; INVESTIGATE; HIDE_FROM_DEFAULT_VIEW (never deletes) | NO_RECOMMENDATION |
| minimumEvidenceCoverage | 0–100, policy-derived, read-only unless authorised | e.g. 80/85 |
| missingEvidenceTreatment | NOT_ASSESSED; REVIEW_REQUIRED; BLOCK (ASSUME_ZERO prohibited) | NOT_ASSESSED |
| criticalConflictTreatment | BLOCK; REQUIRE_STEWARD_REVIEW | BLOCK |
| staleEvidenceTreatment | REQUIRE_REFRESH; ALLOW_WITH_WARNING; BLOCK | REQUIRE_REFRESH |
| technicalEquivalenceGate | NOT_REQUIRED; REQUIRED_FOR_RECOMMENDATION; REQUIRED_FOR_CONSOLIDATE; REQUIRED_FOR_MIGRATE; REQUIRED_FOR_APPROVAL | policy |
| executionReadinessGate | INFORMATIONAL; REQUIRED_FOR_DECISION; REQUIRED_FOR_APPROVAL | policy |
| humanReviewRequired | ON/OFF | ON for consequential actions |
| policyPack | most specific applicable published policy (e.g. UK-CONSUMER-PORTFOLIO@3.2) | recommended |
| aiStrategy | CUSTOMER_REGISTRY; PLATFORM_MANAGED; BYO_ENDPOINT; NO_LLM | tenant default |
| registryId / deploymentId | dynamic approved entries; no credentials in UI | — |
| routingPolicy | QUALITY_FIRST; COST_OPTIMISED; CUSTOMER_HOSTED_ONLY; TASK_SPECIFIC; NO_LLM | TASK_SPECIFIC |
| fallback | DETERMINISTIC_TEMPLATE + eligible secondary deployments (max 2 models) | DETERMINISTIC_TEMPLATE |
| aiConfidenceThreshold | 0–100; affects explanation display only | e.g. 80 |
| aiFailureTreatment | DETERMINISTIC_FALLBACK; ALTERNATE_APPROVED_MODEL; HUMAN_REVIEW; BLOCK_AI_EXPLANATION | DETERMINISTIC_FALLBACK |
| compatibilityStatus | PASS; WARNING; BLOCKED (issues INFO/WARNING/BLOCKING) | derived |
Threshold precedence: authorised run override → market/portfolio policy → scoring profile → Telco default.
Task routes: PRODUCT_MATCHING; RECOMMENDATION_REASONING; TECHNICAL_EXPLANATION; DECISION_RATIONALE; TRANSITION_NARRATIVE.
Selection: filter PUBLISHED + schema range + market (or GLOBAL) + product families; objectiveCoverage and
dimensionCoverage (reject < 100% mandatory); rank objectiveCoverage, specificity, priority, semver; ties →
customer preferred profile, else present both (never invent a preference).
Guardrails: RECOMMENDED if score ≥ recommended AND coverage ≥ minimum AND mandatory dimensions ASSESSED AND
conflict/freshness gates pass; REVIEW_REQUIRED if review ≤ score < recommended and no blocking gate; below
review → treatment; mandatory NOT_ASSESSED/BLOCKED overrides score; human review ON prevents automatic
decisions; AI confidence never affects scoring/readiness/approval.

## 05 Review & Start
Scope, evidence and configuration summaries (no "latest" refs); limitation acknowledgement (persist actor, time,
warning ids); Start (blockers = 0, server recheck) freezes the manifest; events RunStarted, AnalysisRequested.

## 06 Run Overview
runState, stageProgress, sourceProducts, candidateCount, blockers (severity, subject, issue, owner, domain).

## 07 Candidate Explorer
Search; type filter DUPLICATE, OVERLAP, COMPLEXITY, LEGACY, MIGRATION, RETIREMENT, INVESTIGATE;
recommendedAction RETAIN, SIMPLIFY, CONSOLIDATE, STANDARDISE, MIGRATE, RETIRE, INVESTIGATE; score 0–100.

## 08 Candidate Review
Dimensions commercial / technical / execution: score 0–100 or null + ASSESSED, PARTIAL, NOT_ASSESSED, BLOCKED.
Impact via graph + approved aggregates (no AI). Explanation ≤ 4000 chars, cited, validated, deterministic
fallback. Preliminary action (changing it recalculates evidence requirements). Resolve evidence gaps → request.

## 09 Decision
disposition (enum above); targetProduct (conditional, eligible snapshot products); effectiveDate (future
business date, market timezone); rationale 50–4000 (AI may draft, user owns); decision version + SHA-256;
approval route by policy (action, market, impact, evidence, regulation, SoD).

## 10 Approval
approvalTask on exact version/hash; policyChecks PASS/WARNING/BLOCKED (recheck stale); comment 0–2000, required
for RETURN/HOLD/REJECT; action APPROVE/RETURN/HOLD/REJECT with role/scope/SoD/hash checks.

## 11 Evidence Readiness
overallCompleteness (weighted applicable coverage × quality/freshness); domain rows (status, %, consequence,
required action); approvalBlockers.
Policy matrix: CRM catalog Critical (assessment blocked); CPQ High (similarity partial); Price book Medium
(economics partial); Lifecycle High (retire restricted); Technical catalog Critical for Telco (technical
equivalence not assessed); Provisioning Critical for Consolidate/Migrate (migration readiness blocked);
Inventory Critical for Retire/Migrate (approval blocked).

## 12–13 Evidence requests
Request ER-*, trigger candidate + disposition, consequence, owner (default from ownership map), status DRAFT,
OPEN, IN_PROGRESS, RECEIVED, ACCEPTED, REJECTED, SUPERSEDED. Contract: purpose, metrics, groupBy (Product ID /
Service Spec ID), privacy (aggregate only, prohibited fields, min cohort), freshness; fulfilment SECURE_QUERY,
API_CONNECTOR, SCHEDULED_EXTRACT, EXCEL_AGGREGATE. Triggers per disposition: SIMPLIFY (CPQ variants, rule
counts, fallout), CONSOLIDATE (aggregates, service mapping, provisioning compatibility), STANDARDISE (service
specs, attribute/provisioning variants), MIGRATE (active instances, target compatibility, exceptions,
billing/order deps), RETIRE (installed base, footprint, contracts, revenue, billing/provisioning deps), RETAIN
(strategic relevance, activity, operations), INVESTIGATE (only what resolves the uncertainty).

## 14 AI Runtime
Strategy (CUSTOMER_MODEL_REGISTRY, PLATFORM_MANAGED, BYO_ENDPOINT, NO_LLM); registry; task routes (MATCHING,
REASONING, TECHNICAL_EXPLANATION, RATIONALE, TRANSITION_NARRATIVE); governance checks (approved, region, data
policy, structured output, fallback). Model/provider version and routing pinned per run.
