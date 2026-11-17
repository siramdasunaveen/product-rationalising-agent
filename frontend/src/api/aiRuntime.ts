import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch, qs } from "./client";

export type Check = { key: string; label: string; status: "PASS" | "WARNING" | "FAIL"; message: string };
export type Deployment = {
  key: string; ref: string; status: string; display_name: string; provider: string; model: string | null;
  hosting: string; region: string; quality_tier: number | null; cost_tier: number | null; secret_ref: string | null;
  secret_set: boolean | null;
};
export type Route = {
  task: string; label: string; mode: "LLM" | "DETERMINISTIC"; primary: string | null; fallback: string | null;
  chain: string[]; deterministic_fallback: boolean; checks: Check[]; compliant: boolean;
  rejected: Record<string, string[]>; trace: string[];
};
export type Runtime = {
  profile_ref: string | null; strategy: string | null; routing_policy: string | null;
  failure_treatment: string | null; ai_confidence_threshold: number | null;
  constraints: Record<string, unknown> | null;
  strategies: { key: string; label: string; description: string; selected: boolean }[];
  deployments: Deployment[]; routes: Route[]; can_edit: boolean;
};
export type RuntimeChange = {
  expected_ref: string; strategy?: string; routing_policy?: string; failure_treatment?: string;
  ai_confidence_threshold?: number;
  routes?: { task: string; mode: "LLM" | "DETERMINISTIC"; primary?: string | null; fallback?: string | null }[];
};
export type Invocation = {
  id: string; run_id: string | null; task: string; profile_ref: string | null; deployment_ref: string | null;
  model: string | null; source: "llm" | "fallback"; attempts: number; chain: string[]; errors: string[];
  needs_human_review: boolean; created_at: string;
};
export type TestResult = { task: string; source: string; deployment: string | null; model: string | null;
  attempts: number; errors: string[] };

const KEY = ["ai-runtime"] as const;

export const useAIRuntime = () => useQuery({ queryKey: KEY, queryFn: () => apiFetch<Runtime>("/ai-runtime") });

export const useAIInvocations = (enabled: boolean) =>
  useQuery({
    queryKey: [...KEY, "invocations"],
    queryFn: () => apiFetch<Invocation[]>(`/ai-runtime/invocations${qs({ limit: 10 })}`),
    enabled,
  });

export const useAISecrets = (enabled: boolean) =>
  useQuery({
    queryKey: [...KEY, "secrets"],
    queryFn: () => apiFetch<{ name: string; updated_by: string; updated_at: string }[]>("/ai-runtime/secrets"),
    enabled,
  });

export function useSaveAIRuntime() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: RuntimeChange) => apiFetch<Runtime>("/ai-runtime", { method: "PUT", body: JSON.stringify(body) }),
    onSuccess: (data) => qc.setQueryData(KEY, data),
  });
}

export function useSetAISecret() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (a: { name: string; value: string }) =>
      apiFetch(`/ai-runtime/secrets/${a.name}`, { method: "PUT", body: JSON.stringify({ value: a.value }) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

export function useTestRoute() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (task: string) => apiFetch<TestResult>(`/ai-runtime/test/${task}`, { method: "POST", body: "{}" }),
    onSettled: () => qc.invalidateQueries({ queryKey: [...KEY, "invocations"] }),
  });
}
