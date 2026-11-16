// API client used by all screens. The host (standalone shell or AI Studio) configures how the
// bearer token is obtained and what happens on 401.

export const API_BASE = "/api/rationalisation";

type ApiConfig = {
  getToken: () => string | null;
  onUnauthorized: () => void;
};

let config: ApiConfig = { getToken: () => null, onUnauthorized: () => {} };

export function configureApi(next: Partial<ApiConfig>): void {
  config = { ...config, ...next };
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const token = config.getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");

  const res = await fetch(`${API_BASE}${path}`, { ...init, headers });
  if (res.status === 401) config.onUnauthorized();
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}
