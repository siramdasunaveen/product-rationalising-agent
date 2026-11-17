import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "./client";

export type Connector = {
  id: string; key: string; name: string; system: string; status: "ACTIVE" | "DISABLED"; instanceUrl: string;
  apiVersion: string; authType: string; queries: Record<string, unknown>; domains: string[]; clientId: string | null;
  secretSet: boolean; lastCheck: { ok: boolean; problems: { code: string; message: string }[]; at: string; by: string } | null;
  createdAt: string; updatedAt: string; updatedBy: string;
};
export type ConnectorForm = { key: string; name: string; system: string; instanceUrl: string; apiVersion?: string;
  clientId?: string | null; clientSecret?: string | null; status?: string };
export type PullResult = { pullId: string; rows: number; soql: string; upload: { id: string; status: string; filename: string } };

const KEY = ["connectors"];

export const useConnectors = (enabled = true) =>
  useQuery({ queryKey: KEY, queryFn: () => apiFetch<Connector[]>("/connectors"), enabled });

export function useSaveConnector() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (a: { id?: string; form: ConnectorForm }) =>
      apiFetch<Connector>(a.id ? `/connectors/${a.id}` : "/connectors", { method: a.id ? "PUT" : "POST", body: JSON.stringify(a.form) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

export function useDeleteConnector() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (id: string) => apiFetch<void>(`/connectors/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }) });
}

export function useTestConnector() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiFetch<Connector["lastCheck"]>(`/connectors/${id}/test`, { method: "POST", body: "{}" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

export function usePullDomain(runId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (a: { domain: string; connectorId: string }) =>
      apiFetch<PullResult>(`/runs/${runId}/evidence/${a.domain}/pull`, { method: "POST", body: JSON.stringify({ connectorId: a.connectorId }) }),
    onSettled: () => qc.invalidateQueries({ queryKey: ["evidence", runId] }),
  });
}
