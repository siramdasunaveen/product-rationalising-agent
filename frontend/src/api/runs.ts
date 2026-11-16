import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch, qs } from "./client";

export type Ref = { id: string; name: string };

export type Run = {
  id: string;
  run_code: string;
  name: string;
  state: string;
  stage_label: string;
  portfolio: Ref;
  markets: Ref[];
  product_families: Ref[];
  period_start: string;
  period_end: string;
  objectives: string[];
  owner: { user_id: string; name: string };
  risk: string | null;
  scope_editable: boolean;
  version: number;
  created_at: string;
  updated_at: string;
};

export type RunListItem = {
  id: string;
  run_code: string;
  name: string;
  state: string;
  stage_label: string;
  portfolio_name: string;
  owner_name: string;
  risk: string | null;
  updated_at: string;
};

export type RunSummary = { active_runs: number; opportunities: number; awaiting_approval: number; blocked: number };

export type RunScope = {
  name: string;
  portfolio_id: string | null;
  market_ids: string[];
  product_family_ids: string[];
  period_start: string | null;
  period_end: string | null;
  objectives: string[];
  owner_id: string | null;
};

export const useRuns = (query: string) =>
  useQuery({
    queryKey: ["runs", "list", query],
    queryFn: () => apiFetch<{ items: RunListItem[]; total: number }>(`/runs${qs({ query })}`),
    placeholderData: (prev) => prev,
  });

export const useRunSummary = () =>
  useQuery({ queryKey: ["runs", "summary"], queryFn: () => apiFetch<RunSummary>("/runs/summary") });

export const useRun = (id: string | undefined) =>
  useQuery({ queryKey: ["runs", "one", id], queryFn: () => apiFetch<Run>(`/runs/${id}`), enabled: !!id });

export function useSaveRunScope() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { id?: string; version?: number; scope: Partial<RunScope> }) =>
      args.id
        ? apiFetch<Run>(`/runs/${args.id}`, {
            method: "PATCH",
            body: JSON.stringify({ version: args.version, ...args.scope }),
          })
        : apiFetch<Run>("/runs", { method: "POST", body: JSON.stringify(args.scope) }),
    onSuccess: (run) => {
      qc.setQueryData(["runs", "one", run.id], run);
      qc.invalidateQueries({ queryKey: ["runs", "list"] });
      qc.invalidateQueries({ queryKey: ["runs", "summary"] });
    },
  });
}
