# Decisions log

## Confirmed by the developer (16 Nov 2026)
- The agent is developed standalone and handed to a colleague who deploys it into AI Studio (a platform hosting
  several agents, e.g. MetaFlow). The developer does not build AI Studio.
- CAND-1042: the Decision screen showing "Evidence status: COMPLETE" comes after the provisioning and inventory
  evidence requests were fulfilled. Submission is gated until then.
- Connectors are in scope from the start, Excel first: build the connector framework with the Excel adapter
  first, then Salesforce, then other systems.

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
- CAND-1042 shows score 92 on some screens and 87/100 on the evidence-aware screen. Proposal: 92 = opportunity
  score, 87 = commercial rationalisation dimension score; AI confidence 0.87 is separate.
- Wizard step 2 is labelled both "Sources" and "Evidence Hub". Use "Evidence Hub".
- AI route screen shows "Now Assist" as primary route. Treat routes as configurable entries in the model registry.
- Persona/permission matrix not specified. Proposal in BUILD_PLAN step 1.
