// Public surface of the agent's UI: screens only, no global header/login.
// AI Studio (or the standalone shell) mounts these and configures the API client.
import "./styles/tokens.css";
import "./styles/screens.css";

export { configureApi, apiFetch, ApiError, API_BASE } from "./api/client";
export { useMe, useManifest, usePermissions } from "./api/identity";
export type { Me, Manifest, NavItem } from "./api/identity";
export { AssessmentsScreen } from "./screens/AssessmentsScreen";
export { PlaceholderScreen } from "./screens/PlaceholderScreen";
