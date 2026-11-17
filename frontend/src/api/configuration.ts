import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "./client";

export type Option = { ref: string; eligible: boolean; reasons: string[]; objective_coverage: number;
  specificity: number; priority: number };
export type Selection = {
  role: string; label: string; ref: string | null; source: string | null; recommended: string | null; tie: string[];
  eligible: boolean | null; reasons: string[]; objective_coverage: number | null; description: string | null;
  options: Option[]; trace: string[];
};
export type Setting = { name: string; label: string; value: unknown; source: string; overridable: boolean;
  allowed: unknown[] | null };
export type Issue = { id: string; severity: "INFO" | "WARNING" | "BLOCKING"; area: string; message: string;
  acknowledged: boolean };
export type Configuration = {
  run_id: string; run_code: string; run_name: string; state: string; version: number; editable: boolean;
  started: boolean; can_override: boolean; selections: Selection[]; settings: Setting[];
  snapshot: { id: string; code: string; contentHash: string } | null;
  readiness: { kpis: { overall_completeness: number; domains_available: number; domains_selected: number;
    approval_blockers: number; products: number } } | null;
  ai: { profile_ref: string | null; strategy?: string | null;
    routes: { task: string; label: string; compliant: boolean; chain: string[] }[] | Record<string, unknown> };
  status: "PASS" | "WARNING" | "BLOCKED"; issues: Issue[]; unacknowledged: string[];
  scope: { markets: string[]; product_families: string[]; objectives: string[]; period_start: string;
    period_end: string; owner_name: string };
  manifest_hash: string | null;
};
export type Manifest = { run_id: string; manifest: Record<string, unknown>; manifest_hash: string; created_by: string;
  created_at: string };

const key = (runId: string) => ["configuration", runId] as const;

export const useConfiguration = (runId: string) =>
  useQuery({ queryKey: key(runId), queryFn: () => apiFetch<Configuration>(`/runs/${runId}/configuration`) });

export function useSaveConfiguration(runId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { version: number; selections?: Record<string, string | null>;
      overrides?: Record<string, unknown> }) =>
      apiFetch<Configuration>(`/runs/${runId}/configuration`, { method: "PUT", body: JSON.stringify(body) }),
    onSuccess: (data) => qc.setQueryData(key(runId), data),
  });
}

export function useAcknowledge(runId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { version: number; warning_ids: string[] }) =>
      apiFetch<Configuration>(`/runs/${runId}/configuration/acknowledgements`,
        { method: "POST", body: JSON.stringify(body) }),
    onSuccess: (data) => qc.setQueryData(key(runId), data),
  });
}

export function useStartRun(runId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (version: number) =>
      apiFetch<Manifest>(`/runs/${runId}/start`, { method: "POST", body: JSON.stringify({ version }) }),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: key(runId) });
      qc.invalidateQueries({ queryKey: ["runs"] });
    },
  });
}
