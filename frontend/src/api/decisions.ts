import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "./client";

export type DecisionState = "DRAFT" | "SUBMITTED" | "ON_HOLD" | "RETURNED" | "APPROVED" | "REJECTED" | "SUPERSEDED";
export type PolicyCheck = { key: string; label: string; status: "PASS" | "WARNING" | "BLOCKED"; detail: string };
export type DecisionVersion = {
  id: string; versionNo: number; candidateId: string; analysisId: string; snapshotId: string; snapshotCode: string;
  manifestHash: string; recommendation: Record<string, unknown>; disposition: string; recommendedAction: string | null;
  targetProductId: string | null; targetName?: string | null; effectiveDate: string; ownerId: string; rationale: string;
  commercialTreatment: string | null; conditions: string[]; readiness: Record<string, unknown> | null;
  route: { scopes: string[]; reasons: string[] } | null; contentHash: string; createdBy: string; createdAt: string;
  reason: string | null; products?: { id: string; name: string }[]; impactSummary?: string | null;
  impact?: Record<string, unknown>;
};
export type ApprovalTask = { id: string; scope: string; state: string; dueDate: string | null; actedBy: string | null;
  actedAt: string | null };
export type ApprovalActionRow = { id: string; action: string; scope: string | null; actorId: string; comment: string | null;
  resultingState: string; contentHash: string; at: string };
export type Decision = {
  id: string; code: string; runId: string; runCode: string; candidateCode: string; state: DecisionState;
  currentVersionNo: number; approvedVersionId: string | null; versions: DecisionVersion[];
  current: DecisionVersion | null; policyChecks: PolicyCheck[]; overall: "PASS" | "PASS_WITH_WARNING" | "BLOCKED" | null;
  stale?: boolean; tasks: ApprovalTask[]; actions: ApprovalActionRow[]; myScopes: string[]; canAct: boolean;
};
export type QueueRow = { code: string; runId: string; runCode: string; candidateCode: string; state: DecisionState;
  disposition: string; targetProductId: string | null; versionNo: number; effectiveDate: string;
  pendingScopes: string[]; dueDate: string | null };
export type DecisionForm = { disposition: string; targetProductId: string | null; effectiveDate: string;
  ownerId: string; rationale: string; commercialTreatment?: string | null };

export const useCandidateDecision = (runId: string, candidateCode: string) =>
  useQuery({ queryKey: ["decision", runId, candidateCode],
    queryFn: () => apiFetch<Decision | null>(`/runs/${runId}/candidates/${candidateCode}/decision`) });

export const useDecision = (runId: string, code: string) =>
  useQuery({ queryKey: ["decision-by-code", runId, code],
    queryFn: () => apiFetch<Decision>(`/runs/${runId}/decisions/${code}`) });

export const useApprovalQueue = (all = false) =>
  useQuery({ queryKey: ["approvals", all], queryFn: () => apiFetch<QueueRow[]>(`/approvals${all ? "?all=true" : ""}`) });

function invalidate(qc: ReturnType<typeof useQueryClient>, runId: string) {
  qc.invalidateQueries({ queryKey: ["decision", runId] });
  qc.invalidateQueries({ queryKey: ["decision-by-code", runId] });
  qc.invalidateQueries({ queryKey: ["approvals"] });
  qc.invalidateQueries({ queryKey: ["candidate", runId] });
  qc.invalidateQueries({ queryKey: ["runs", "summary"] });
}

export function useSaveDecision(runId: string, candidateCode: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (form: DecisionForm) => apiFetch<Decision>(`/runs/${runId}/candidates/${candidateCode}/decision`,
      { method: "POST", body: JSON.stringify(form) }),
    onSuccess: () => invalidate(qc, runId),
  });
}

export function useSubmitDecision(runId: string, candidateCode: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { versionId: string; contentHash: string }) =>
      apiFetch<Decision>(`/runs/${runId}/candidates/${candidateCode}/decision/submit`, { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => invalidate(qc, runId),
  });
}

export function useApprovalAction(runId: string, code: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { action: string; comment?: string; contentHash: string }) =>
      apiFetch<Decision>(`/runs/${runId}/decisions/${code}/actions`, { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => invalidate(qc, runId),
  });
}
