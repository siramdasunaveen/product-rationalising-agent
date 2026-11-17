# Test report

Generated 2026-11-17 17:50 UTC on Windows 11, Python 3.13.14, commit `051fea9`.

## Backend (pytest, PostgreSQL + fakeredis)

Result: **PASS after fix** — full run 17 Nov 2026 23:00 local: 504 passed, 2 failed (464 s). The two failures were stale expectations from Step 15 (`test_api::test_manifest_nav` did not list the new Connectors nav entry; `test_evidence_api::test_overview_proposes_domains_from_objectives` expected API_CONNECTOR to be unavailable). Both expectations were updated and the two tests re-run green; the table below is the saved full run. Regenerate with `backend\scripts	est_report.py` for a clean run.

| Test module | Tests | Failed | Skipped |
|---|---:|---:|---:|
| test_ai_runner | 7 | 0 | 0 |
| test_ai_runtime_api | 9 | 0 | 0 |
| test_ai_runtime_domain | 9 | 0 | 0 |
| test_analysis_api | 3 | 0 | 0 |
| test_api | 12 | 1 | 0 |
| test_artifact_content | 18 | 0 | 0 |
| test_artifacts_api | 19 | 0 | 0 |
| test_artifacts_db | 52 | 0 | 0 |
| test_artifacts_domain | 24 | 0 | 0 |
| test_assessment_api | 4 | 0 | 0 |
| test_assessment_domain | 19 | 0 | 0 |
| test_audit | 7 | 0 | 0 |
| test_auth_secrets_storage | 15 | 0 | 0 |
| test_candidates_api | 6 | 0 | 0 |
| test_candidates_domain | 14 | 0 | 0 |
| test_configuration_api | 4 | 0 | 0 |
| test_configuration_domain | 6 | 0 | 0 |
| test_connectors_api | 3 | 0 | 0 |
| test_connectors_domain | 4 | 0 | 0 |
| test_decisions_api | 3 | 0 | 0 |
| test_decisions_domain | 10 | 0 | 0 |
| test_detectors_domain | 12 | 0 | 0 |
| test_evidence_api | 32 | 1 | 0 |
| test_evidence_requests_api | 3 | 0 | 0 |
| test_evidence_requests_domain | 5 | 0 | 0 |
| test_excel_intake_domain | 41 | 0 | 0 |
| test_jobs_notify | 4 | 0 | 0 |
| test_mapping_api | 18 | 0 | 0 |
| test_mapping_domain | 46 | 0 | 0 |
| test_outbox | 5 | 0 | 0 |
| test_permissions | 8 | 0 | 0 |
| test_readiness_api | 4 | 0 | 0 |
| test_readiness_domain | 13 | 0 | 0 |
| test_reconcile_domain | 9 | 0 | 0 |
| test_reconciliation_api | 10 | 0 | 0 |
| test_runs_api | 18 | 0 | 0 |
| test_runs_domain | 15 | 0 | 0 |
| test_seed_upgrade | 2 | 0 | 0 |
| test_users | 13 | 0 | 0 |
| **Total** | **506** | **2** | **0** |

## Frontend

Type check (`tsc -b`): **PASS**
Production build (`vite build`): **PASS**

## What the suite covers

Pure domain rules (scoring, guardrails, detectors, readiness, decision readiness, evidence requests, decisions),
API behaviour with the real PostgreSQL schema and fakes for outside systems (Redis, AI provider, Salesforce,
malware scanner), the CAND-1042 happy path and gap branch end to end (evidence upload → mapping → reconciliation
→ snapshot → analysis → candidate review → evidence request → child snapshot → reassessment → decision →
multi-approver approval), permissions and tenancy on every router, append-only and immutability guarantees.
