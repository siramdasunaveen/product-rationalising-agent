import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "./client";

export type RequestStatus = "DRAFT" | "OPEN" | "IN_PROGRESS" | "RECEIVED" | "ACCEPTED" | "REJECTED" | "SUPERSEDED";
export type Contract = { version: string; purpose: string; domain: string; metrics: string[]; grouping: string;
  filters: string; returns: string; freshnessDays: number; privacy: string; products: string[]; unlocks: string };
export type FulfilmentMethod = { key: string; label: string; note: string; recommended: boolean; available: boolean };
export type EvidenceRequest = {
  id: string; code: string; runId: string; runCode: string | null; candidateCode: string; candidateId: string | null;
  blockerCode: string | null; domain: string; domainLabel: string; action: string | null; trigger: string;
  evidenceNeeded: string; decisionImpact: string; consequence: "BLOCKED" | "WARNING_ONLY"; priority: string;
  contract: Contract; ownerRole: string; ownerId: string | null; dueDate: string | null;
  fulfilmentMethod: string | null; fulfilmentJob: Record<string, unknown> | null; status: RequestStatus;
  statusReason: string | null; snapshot: { id: string; code: string } | null;
  receivedSnapshot: { id: string; code: string } | null; acceptedSnapshot: { id: string; code: string } | null;
  reassessmentAnalysisId: string | null; reassessmentStatus: "NONE" | "QUEUED" | "DONE" | "FAILED";
  createdAt: string; updatedAt: string; closedAt: string | null; fulfilmentMethods: FulfilmentMethod[];
  history?: { from: string | null; to: string; actorId: string; note: string | null; detail: Record<string, unknown>; at: string }[];
};
export type Queue = { metrics: { open: number; openCandidates: number; blocking: number; awaiting: number; ready: number;
  reassessing: number }; items: EvidenceRequest[] };
export type QueueParams = { status?: string; domain?: string; mine?: boolean; query?: string };

const qs = (p: QueueParams) => {
  const s = new URLSearchParams();
  for (const [k, v] of Object.entries(p)) if (v !== undefined && v !== "" && v !== false) s.set(k, String(v));
  const out = s.toString();
  return out ? `?${out}` : "";
};
const base = (runId?: string) => (runId ? `/runs/${runId}/evidence-requests` : "/evidence-requests");

export const useRequestQueue = (runId: string | undefined, params: QueueParams) =>
  useQuery({ queryKey: ["evidence-requests", runId ?? "all", params],
    queryFn: () => apiFetch<Queue>(`${base(runId)}${qs(params)}`), placeholderData: (d) => d });

export const useEvidenceRequest = (runId: string, code: string) =>
  useQuery({ queryKey: ["evidence-request", runId, code],
    queryFn: () => apiFetch<EvidenceRequest>(`${base(runId)}/${code}`),
    refetchInterval: (q) => (q.state.data?.reassessmentStatus === "QUEUED" ? 2000 : false) });

function useRequestMutation<TArgs>(runId: string, code: string, fn: (a: TArgs) => Promise<unknown>) {
  const qc = useQueryClient();
  return useMutation({ mutationFn: fn, onSuccess: () => {
    qc.invalidateQueries({ queryKey: ["evidence-request", runId, code] });
    qc.invalidateQueries({ queryKey: ["evidence-requests"] });
    qc.invalidateQueries({ queryKey: ["candidate", runId] });
  } });
}

export const useAssignRequest = (runId: string, code: string) =>
  useRequestMutation(runId, code, (body: { ownerId?: string | null; dueDate?: string | null; fulfilmentMethod?: string | null }) =>
    apiFetch<EvidenceRequest>(`${base(runId)}/${code}/assign`, { method: "POST", body: JSON.stringify(body) }));
export const useStartRequest = (runId: string, code: string) =>
  useRequestMutation(runId, code, () => apiFetch<EvidenceRequest>(`${base(runId)}/${code}/start`, { method: "POST", body: "{}" }));
export const useAcceptRequest = (runId: string, code: string) =>
  useRequestMutation(runId, code, () => apiFetch<{ accepted: string[]; analysisId: string; snapshot: string }>(
    `${base(runId)}/${code}/accept`, { method: "POST", body: "{}" }));
export const useRejectRequest = (runId: string, code: string) =>
  useRequestMutation(runId, code, (reason: string) =>
    apiFetch<EvidenceRequest>(`${base(runId)}/${code}/reject`, { method: "POST", body: JSON.stringify({ reason }) }));

export function useDetectRequests(runId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (candidateCode?: string) => apiFetch<EvidenceRequest[]>(
      candidateCode ? `/runs/${runId}/candidates/${candidateCode}/evidence-requests` : `${base(runId)}/detect`,
      { method: "POST", body: "{}" }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["evidence-requests"] }); qc.invalidateQueries({ queryKey: ["candidate", runId] }); },
  });
}
