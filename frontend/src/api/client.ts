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

export type FieldError = { field: string; message: string };

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public code: string | null = null,
    public fieldErrors: FieldError[] = [],
  ) {
    super(message);
  }

  /** Field errors keyed by field name (first message wins). */
  byField(): Record<string, string> {
    const out: Record<string, string> = {};
    for (const e of this.fieldErrors) out[e.field] ??= e.message;
    return out;
  }
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const token = config.getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  // FormData sets its own multipart boundary; everything else we send is JSON.
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const res = await fetch(`${API_BASE}${path}`, { ...init, headers });
  if (res.status === 401) config.onUnauthorized();
  if (!res.ok) await throwApiError(res);
  return (await res.json()) as T;
}

/** GET a file (e.g. an Excel template) with the caller's token. */
export async function apiDownload(path: string): Promise<{ blob: Blob; filename: string }> {
  const headers = new Headers();
  const token = config.getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const res = await fetch(`${API_BASE}${path}`, { headers });
  if (res.status === 401) config.onUnauthorized();
  if (!res.ok) await throwApiError(res);
  const match = /filename="([^"]+)"/.exec(res.headers.get("Content-Disposition") ?? "");
  return { blob: await res.blob(), filename: match?.[1] ?? "download" };
}

async function throwApiError(res: Response): Promise<never> {
  let message = res.statusText;
  let code: string | null = null;
  let fieldErrors: FieldError[] = [];
  try {
    const body = await res.json();
    const d = body.detail;
    if (typeof d === "string") message = d;
    else if (d && typeof d === "object" && "code" in d) {
      // Our structured errors: { code, message, errors: [{field, message}] }
      message = d.message;
      code = d.code;
      fieldErrors = d.errors ?? [];
    } else if (Array.isArray(d)) {
      // FastAPI request-shape errors
      message = "Some values are not valid";
      fieldErrors = d.map((e: { loc: string[]; msg: string }) => ({
        field: String(e.loc[e.loc.length - 1]),
        message: e.msg,
      }));
    }
  } catch {
    /* non-JSON error body */
  }
  throw new ApiError(res.status, message, code, fieldErrors);
}

export function qs(params: Record<string, string | number | string[] | undefined>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === "") continue;
    if (Array.isArray(v)) v.forEach((x) => sp.append(k, x));
    else sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}
