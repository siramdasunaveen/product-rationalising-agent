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

---
# Candidate guides (5 developer guides, supplied 16 Nov 2026, second batch)
Sources: Run Overview Metrics (expanded detectors/normalization), Run Overview (cross-screen), Candidate
Explorer (priority views), Candidate Explorer (cross-screen), Candidate Review (with E2E example CAND-1042).
Core rule: one approved snapshot + pinned detector/scoring/policy versions; **no LLM computes any metric,
detector score, grouping, priority, status, blocker or recommendation enum.**

## Run Overview metrics (screen 06)
- sourceProducts = COUNT(DISTINCT canonicalProductId) in the approved snapshot, in scope, not excluded
  (invalid / quarantined / superseded / explicitly excluded removed). Example 2,020 → 1,842.
- candidates = active normalized Candidate aggregates (raw findings − merged − invalid/out-of-scope − superseded;
  example 214 − 18 − 6 − 4 = 186).
- highPriority = Candidates with effectivePriority IN (HIGH, CRITICAL) (example 42). Never from Score.
- blocked = COUNT(blocker) WHERE state IN (OPEN, IN_PROGRESS) AND consequence = BLOCKED AND applicable AND NOT
  superseded (example 7). Blocker records, not candidates; a "blocked candidates" count would be a separate field.
- Attention Required = top N active actionable blockers ORDER BY severity DESC, dueDate ASC, createdAt ASC;
  shows severity, subject/candidate, deterministic message, blocker domain; row links to Candidate Review with
  blocker focus. Not a candidate table.
- API: {runId, snapshotId, metrics{sourceProducts, candidates, highPriority, blocked},
  detectorSummary{rawFindings, duplicate, overlap, legacy, complexity, operationalBurden}, projectionVersion}.
- Navigation: Open Candidates → Explorer (All Candidates); High Priority card → VIEW-HIGH-PRIORITY; Blocked
  card → VIEW-BLOCKED; Attention row → /candidates/{id}?focus=blocker&blockerId=….

## Detectors (findings, not candidates; thresholds live in a versioned Detector Pack)
- DUPLICATE: similarity = .30 service + .20 cpq + .15 eligibility + .10 pricingStructure + .15 technical
  + .10 lifecycle. Strong ≥ .90; probable review band .80–.899. Identity never inferred from similarity.
- OVERLAP: .40 capability + .25 segment + .15 channel/market + .15 useCase + .05 substitution; ≥ .70 and
  duplicateSimilarity < duplicate threshold.
- LEGACY: endOfSale OR criticalDependencySunset (within horizon) OR legacyProvisioningWorkflow OR
  (replacementAvailable AND legacyArchitectureFlag). Installed base > 0 → migrate, never silent retire.
- COMPLEXITY: .25 variant + .25 rule + .15 bundleDepth + .15 attributeEntropy + .20 manualException (each
  normalised to family P95, capped 1); ≥ .75 or a hard limit exceeded.
- OPERATIONAL_BURDEN: .30 fallout + .20 manualTouch + .20 incident + .15 provisioningException + .15 handling
  (family P95 normalised); ≥ .70. Only approved aggregates.
- Every finding persists detectorType, detectorVersion, factor values, threshold, Evidence IDs.
- Primary type precedence (pinned, policy may override): DUPLICATE > OVERLAP > LEGACY > COMPLEXITY >
  OPERATIONAL_BURDEN; supporting types retained.
- Explorer also has a LOW_ADOPTION view (no detector formula given).

## Candidate normalization
- Resolve subjects to canonicalProductIds in the snapshot; sort; dedupe; reject if a subject is invalid/out of
  scope.
- candidateGroupKey = SHA256(runId + snapshotId + groupingScope + sorted(productIds) + groupingPolicyVersion);
  groupingScope normally excludes detector type (optionally actionClass).
- One Candidate per key; attach all findings (ids, types, scores, factors); primaryType by precedence; union
  Evidence IDs; keep max detector signal separately.
- INVALID / OUT_OF_SCOPE / SUPERSEDED (child snapshot replacement); never physically deleted; lineage kept.

## Opportunity Score, Priority, Status, Blocker (all separate)
- Opportunity Score = round(100 × (.35 signal + .20 commercialValue + .20 technicalFeasibility
  + .15 operationalBenefit + .10 strategicAlignment)) from the pinned Scoring Profile (CAND-1042: 92).
- Similarity = primary detector similarity (CAND-1042: 0.93/0.94; persist one authoritative value).
- AI confidence = metadata of an accepted AI explanation only (CAND-1042: 0.87); null in NO_LLM; never blended
  into score, priority, readiness or approval.
- Priority: priorityIndex = .25 businessImpact + .20 exposure + .15 operationalBurden + .15 legacyUrgency
  + .10 regulatoryRisk + .10 dependencyCriticality + .05 strategicAlignment (each 0–100). Bands 0–39 LOW,
  40–64 MEDIUM, 65–84 HIGH, 85–100 CRITICAL. effectivePriority = MAX(base, policy overrides: regulatory,
  sunset, dependency, customer impact). Sunset urgency: 100 ≤ 90d, 80 ≤ 180d, 60 ≤ 365d.
- Candidate Status: REVIEW, BLOCKED, EVIDENCE_GAP, DECISION, APPROVAL, APPROVED (precedence
  BLOCKED/EVIDENCE_GAP/APPROVAL/DECISION/REVIEW).
- Blocker: separate persisted record; category EVIDENCE, VALIDATION, POLICY, DEPENDENCY, WORKFLOW; state OPEN,
  IN_PROGRESS, RESOLVED, WAIVED; consequence BLOCKED or WARNING_ONLY; severity LOW–CRITICAL (from type, policy
  consequence, exposure, SLA; not copied from priority); domain RISK, COMMERCIAL, ARCHITECTURE, DATA,
  PROVISIONING, BILLING, REGULATORY, SECURITY. Created/resolved by services as facts change.

## Candidate Explorer (screen 07)
- General workspace over all active candidates; columns ID, Products, Type, Score, Priority, Recommendation,
  Impact (compact text from structured facts, e.g. "£480k / 86k subs"), Status.
- Saved views (configuration, not copies; TEAM or personal; server-side filter metadata): All Candidates;
  High-priority (VIEW-HIGH-PRIORITY: priority IN HIGH,CRITICAL; sort priority desc, score desc); Blocked;
  Evidence Gaps; Near Duplicates; Legacy Dependencies; Operational Burden; Low Adoption; Ready for Decision
  (REVIEW + readiness PASS); In Approval.
- Filters shown == effectiveFilters executed (the mock-up's Score ≥ 60 showing 57 and Status=review showing
  blocked rows must NOT be reproduced). Response: inventoryTotal and filteredCount separately, projectionVersion.

## Candidate Review (screens 08/12)
- Header, recommendation (enum + target + conditions; deterministic template text), AI confidence, opportunity
  score, similarity, subscribers (exact 86,214; UI may show 86k), MRR (amount minor units/decimal + currency +
  period), billing systems (COUNT DISTINCT applicable billing dependencies, excluding deprecated).
- API returns exact values + formatting metadata; UI never recomputes rounded metrics.
- Recommendation rule (example): duplicateSignal ≥ threshold AND score ≥ recommendedThreshold AND
  targetEligible AND technicalGate = PASS → CONSOLIDATE; protectedPriceContracts > 0 → condition
  PRESERVE_CONTRACTED_PRICE_UNTIL_RENEWAL (condition, not a different action).
- Evidence tab rows = structured facts {evidenceId, sourceDomain, snapshotId, observedAt, freshness, validation}:
  product equivalence, customer impact, operational impact (falloutMultiple = product / family baseline;
  NOT_ASSESSED if baseline insufficient), dependency (legacy billing code).
- Tabs: Impact, Alternatives ({action, targetId, feasibilityStatus, evidenceCoverage, expectedImpact,
  blockingReasons, evidenceIds}; only actions allowed by type/targets/policy), Policies, Discussion (comments,
  never evidence).
- Decision readiness gates: actionPermitted, targetEligibility, minimumEvidenceCoverage,
  technicalEquivalenceGate, criticalConflicts, freshness, preDecisionPolicy. continueToDecisionEnabled =
  all PASS OR selectedAction = INVESTIGATE. Server-gated; fails closed if snapshot/candidate/policy changed.
  Continue opens the Decision screen; it never creates or approves a Decision.
- Request evidence: system pre-generates the logical evidence contract; user picks owner/due date/channel;
  POST /candidates/{id}/evidence-requests. Accepted return → child snapshot → targeted reassessment.
- Optional AI explanation: Context Envelope (facts EV-*, impact, policy limits) → AI policy → pinned prompt →
  JSON schema + citation validation → accepted or deterministic template.
- E2E example (use as acceptance test): RUN-CONSUMER-MOBILE-Q4, SNAP-005; PRD-V1/PRD-V2 Mobile Data 15GB;
  evidence EV-101..EV-111 (capabilities, eligibility .97, technical .92, 86,214 subs, 4,209 protected-price
  contracts, MRR GBP 480,000/month, fallout 8.4% vs 3.5% → 2.4×, billing BILL-A + BILL-LEGACY, legacy code);
  similarity .94; score factors .94/.90/.92/.86/.98 → 92; CONSOLIDATE to PRD-V2 + preserve-price condition;
  gap branch: provisioning compatibility missing → technical equivalence NOT_ASSESSED → readiness BLOCKED,
  Request evidence enabled, Continue disabled → SNAP-006 → reassess → enabled.
