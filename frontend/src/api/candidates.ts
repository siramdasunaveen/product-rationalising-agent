import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "./client";

export type Priority = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
export type CandidateItem = {
  id: string; candidateId: string; products: string[]; productIds: string[]; primaryType: string;
  primaryTypeLabel: string; supportingTypes: string[]; score: number | null; effectivePriority: Priority | null;
  recommendation: string | null; impactSummary: string; status: string; hidden: boolean;
  decisionReadiness: "PASS" | "BLOCKED" | null; similarity: number | null;
};
export type SavedView = { id: string; name: string; scope: "TEAM" | "PERSONAL"; filters: Filters;
  sort: SortSpec[]; visibleColumns: string[]; purpose: string };
export type SortSpec = { field: string; direction: "ASC" | "DESC" };
export type Filters = { priority?: string[]; status?: string[]; types?: string[]; recommendation?: string[];
  decisionReadiness?: string[]; minScore?: number; query?: string; includeHidden?: boolean };
export type CandidateList = {
  runId: string; runCode: string; snapshot: { id: string; code: string } | null; inventoryTotal: number;
  filteredCount: number; savedView: SavedView | null; effectiveFilters: Filters; sort: SortSpec[];
  items: CandidateItem[]; page: number; pageSize: number; projectionVersion: number; analysed: boolean;
};
export type Gate = { key: string; status: "PASS" | "FAIL" | "NOT_ASSESSED"; reason: string };
export type Readiness = { action: string; target: string | null; status: "PASS" | "BLOCKED"; gates: Gate[];
  continueToDecisionEnabled: boolean };
export type EvidenceRow = { key: string; label: string; value: string; assessed: boolean; evidenceIds: string[];
  sourceDomain: string; snapshotId: string; observedAt: string | null; freshness: string; validation: string };
export type Alternative = { action: string; targetId: string | null; targetName: string | null; recommended: boolean;
  feasibilityStatus: "FEASIBLE" | "BLOCKED" | "NOT_ASSESSED"; evidenceCoverage: number | null;
  expectedImpact: string; blockingReasons: string[]; evidenceIds: string[] };
export type PolicyItem = { key: string; label: string; value: string; status: string; source: string | null };
export type Sentence = { text: string; evidenceIds: string[] };
export type Explanation = { source: "llm" | "fallback"; sentences: Sentence[]; belowThreshold: boolean;
  model: string | null; createdAt: string };
export type CandidateDetail = {
  candidateId: string; id: string; runId: string; runCode: string; analysisId: string;
  snapshot: { id: string; code: string }; status: string; primaryType: string; primaryTypeLabel: string;
  supportingTypes: string[]; title: string; products: { id: string; name: string }[];
  recommendation: { action: string | null; targetProductId: string | null; targetName: string | null;
    sourceProductId: string | null; conditions: string[]; reasonCodes: string[]; preliminary: boolean;
    guardrail: string | null; text: string | null; opportunityScore: number | null; aiConfidence: number | null;
    explanation: Explanation | null };
  metrics: { similarity: number | null; opportunityScore: number | null; activeSubscriptions: number | null;
    mrr: { amount: number; currency: string } | null; billingSystems: number | null;
    display: Record<string, string | null> };
  dimensions: Record<string, { status: string; score: number | null; coverage: number }>;
  priority: { index: number | null; base: Priority | null; effective: Priority | null; overrides?: string[] };
  evidence: EvidenceRow[]; impact: Record<string, unknown>; alternatives: Alternative[]; policies: PolicyItem[];
  blockers: { id: string; code: string; category: string; state: string; consequence: string; severity: string;
    domain: string; message: string; ownerRole: string | null; dueDate: string | null; resolution: string | null }[];
  decisionReadiness: Readiness | null; openEvidenceNeeds: number;
  evidenceRequests: { code: string; domain: string; status: string; ownerRole: string; dueDate: string | null;
    decisionImpact: string }[];
  evidenceIds: string[]; projectionVersion: number;
  etag: string;
};
export type Comment = { id: string; authorId: string; body: string; createdAt: string };

export type ListParams = { viewId?: string; query?: string; types?: string[]; priority?: string[]; status?: string[];
  recommendation?: string[]; decisionReadiness?: string[]; minScore?: number; includeHidden?: boolean;
  sort?: string; page?: number; pageSize?: number };

function qs(p: ListParams): string {
  const s = new URLSearchParams();
  for (const [k, v] of Object.entries(p)) {
    if (v === undefined || v === null || v === "" || (Array.isArray(v) && v.length === 0)) continue;
    s.set(k, Array.isArray(v) ? v.join(",") : String(v));
  }
  const out = s.toString();
  return out ? `?${out}` : "";
}

export const useCandidates = (runId: string, params: ListParams) =>
  useQuery({ queryKey: ["candidates", runId, params],
    queryFn: () => apiFetch<CandidateList>(`/runs/${runId}/candidates${qs(params)}`), placeholderData: (d) => d });

export const useCandidateViews = () =>
  useQuery({ queryKey: ["candidate-views"], queryFn: () => apiFetch<SavedView[]>("/candidate-views") });

export function useSaveView() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { name: string; filters: Filters; sort?: SortSpec[]; visibleColumns?: string[] }) =>
      apiFetch<SavedView>("/candidate-views", { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["candidate-views"] }),
  });
}

export function useDeleteView() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiFetch<void>(`/candidate-views/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["candidate-views"] }),
  });
}

export const useCandidate = (runId: string, ref: string) =>
  useQuery({ queryKey: ["candidate", runId, ref],
    queryFn: () => apiFetch<CandidateDetail>(`/runs/${runId}/candidates/${ref}`) });

export const useComments = (runId: string, ref: string) =>
  useQuery({ queryKey: ["candidate-comments", runId, ref],
    queryFn: () => apiFetch<Comment[]>(`/runs/${runId}/candidates/${ref}/comments`) });

export function useAddComment(runId: string, ref: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: string) =>
      apiFetch<Comment>(`/runs/${runId}/candidates/${ref}/comments`, { method: "POST", body: JSON.stringify({ body }) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["candidate-comments", runId, ref] }),
  });
}

export function useExplain(runId: string, ref: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiFetch<Explanation>(`/runs/${runId}/candidates/${ref}/explanation`, { method: "POST", body: "{}" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["candidate", runId, ref] }),
  });
}

export const checkReadiness = (runId: string, ref: string, body: { action: string; targetProductId?: string | null;
  analysisId: string }) =>
  apiFetch<Readiness>(`/runs/${runId}/candidates/${ref}/decision-readiness`, { method: "POST", body: JSON.stringify(body) });
