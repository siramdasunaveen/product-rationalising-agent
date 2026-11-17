// Public surface of the agent's UI: screens only, no global header/login.
// AI Studio (or the standalone shell) mounts these and configures the API client.
import "./styles/tokens.css";
import "./styles/screens.css";

export { configureApi, apiFetch, ApiError, API_BASE } from "./api/client";
export { useMe, useManifest, usePermissions } from "./api/identity";
export type { Run, RunListItem } from "./api/runs";
export type { Me, Manifest, NavItem } from "./api/identity";
export { AssessmentsScreen } from "./screens/AssessmentsScreen";
export { CreateRunScopeScreen } from "./screens/CreateRunScopeScreen";
export { EvidenceHubScreen } from "./screens/EvidenceHubScreen";
export { MappingRulesScreen } from "./screens/MappingRulesScreen";
export { ReconciliationScreen } from "./screens/ReconciliationScreen";
export { EvidenceReadinessScreen } from "./screens/EvidenceReadinessScreen";
export { AIRuntimeScreen } from "./screens/AIRuntimeScreen";
export { ScoringRouteScreen } from "./screens/ScoringRouteScreen";
export { ReviewStartScreen } from "./screens/ReviewStartScreen";
export { RunOverviewScreen } from "./screens/RunOverviewScreen";
export { CandidateExplorerScreen } from "./screens/CandidateExplorerScreen";
export { CandidateReviewScreen } from "./screens/CandidateReviewScreen";
export { PlaceholderScreen } from "./screens/PlaceholderScreen";
