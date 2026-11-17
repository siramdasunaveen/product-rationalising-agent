import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch, qs } from "./client";

export type ReconInput = {
  source_id: string; source_name: string; domain: string; system: string; execution_id: string;
  output_hash: string; filename: string; as_of: string; records: number;
};

export type ConflictCounts = { OPEN: number; AUTO_RESOLVED: number; RESOLVED: number; DEFERRED: number };

export type Reconciliation = {
  id: string;
  status: "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED";
  policy_ref: string;
  canonical_ref: string;
  reconciled_on: string;
  inputs: ReconInput[];
  counts: {
    records: Record<string, number>; relationships: Record<string, number>; conflicts: ConflictCounts;
    open_material_conflicts: number; stale_sources: number; dangling_references: number; possible_duplicates: number;
  } | null;
  summary: {
    stale_sources: { source_id: string; name: string; domain: string; as_of: string; limit_days: number }[];
    dangling: { kind: string; name: string; entity: string; key: string; side?: string; from_record?: string }[];
    dangling_total: number;
    possible_duplicates: { entity: string; normalised_name: string; keys: string[] }[];
  } | null;
  result_hash: string | null;
  error: string | null;
  created_at: string;
  finished_at: string | null;
  inputs_current: boolean;
};

export type Snapshot = {
  id: string; code: string; snapshot_no: number; status: "APPROVED" | "SUPERSEDED"; parent_code: string | null;
  policy_ref: string; canonical_ref: string; inputs: ReconInput[];
  counts: { facts: number; relationships: number; records: Record<string, number>; stale_facts: number;
    conflicted_facts: number };
  content_hash: string; approved_by_name: string; approved_at: string;
};

export type ReconOverview = {
  run_id: string;
  current_inputs: ReconInput[];
  latest: Reconciliation | null;
  latest_succeeded: Reconciliation | null;
  approval_blockers: string[];
  snapshots: Snapshot[];
};

export type Candidate = {
  source_id: string; source_name: string; domain: string; system: string; as_of: string; stale: boolean;
  value: unknown; lineage_id: number | null;
};

export type Conflict = {
  id: string; conflict_key: string; entity: string; key: string; field: string; material: boolean;
  status: "OPEN" | "AUTO_RESOLVED" | "RESOLVED" | "DEFERRED";
  candidates: Candidate[]; recommended_source_id: string; reason: string;
  decision: { decision: string; source_id: string | null; value: unknown; rationale: string | null; by: string;
    at: string } | null;
};

export type GraphNode = {
  entity: string; key: string; values: Record<string, unknown>; evidence_ids: Record<string, string>;
  flags: { stale: string[]; conflicted: string[] };
};

export type Graph = {
  snapshot: string;
  product: GraphNode;
  nodes: GraphNode[];
  edges: { name: string; from_entity: string; from_key: string; to_entity: string; to_key: string; evidence_id: string }[];
  attached: Record<string, GraphNode[]>;
};

const POLL_MS = 1500;
const key = (runId: string) => ["reconciliation", runId] as const;

export const useReconciliation = (runId: string) =>
  useQuery({
    queryKey: key(runId),
    queryFn: () => apiFetch<ReconOverview>(`/runs/${runId}/reconciliation`),
    refetchInterval: (q) => {
      const s = q.state.data?.latest?.status;
      return s === "QUEUED" || s === "RUNNING" ? POLL_MS : false;
    },
  });

export const useConflicts = (runId: string, reconciliationId: string | undefined, status: string) =>
  useQuery({
    queryKey: [...key(runId), "conflicts", reconciliationId, status],
    queryFn: () => apiFetch<Conflict[]>(
      `/runs/${runId}/reconciliation/${reconciliationId}/conflicts${qs({ status: status || undefined })}`),
    enabled: !!reconciliationId,
  });

export function useReconcile(runId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiFetch<Reconciliation>(`/runs/${runId}/reconciliation`, { method: "POST", body: "{}" }),
    onSettled: () => qc.invalidateQueries({ queryKey: key(runId) }),
  });
}

export function useDecide(runId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (a: { conflictId: string; decision: "SELECT_SOURCE" | "MANUAL" | "DEFER"; source_id?: string;
      value?: string; rationale?: string }) =>
      apiFetch<Conflict>(`/runs/${runId}/reconciliation/conflicts/${a.conflictId}/decision`, {
        method: "POST",
        body: JSON.stringify({ decision: a.decision, source_id: a.source_id ?? null, value: a.value ?? null,
          rationale: a.rationale || null }),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: key(runId) }),
  });
}

export function useApproveSnapshot(runId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (reconciliationId: string) => apiFetch<Snapshot>(`/runs/${runId}/snapshots`, {
      method: "POST", body: JSON.stringify({ reconciliation_id: reconciliationId }),
    }),
    onSettled: () => qc.invalidateQueries({ queryKey: key(runId) }),
  });
}

export const useSnapshotProducts = (runId: string, snapshotId: string | null) =>
  useQuery({
    queryKey: [...key(runId), "products", snapshotId],
    queryFn: () => apiFetch<{ total: number; items: { key: string; field: string; value: unknown }[] }>(
      `/runs/${runId}/snapshots/${snapshotId}/facts${qs({ entity: "Product", limit: 500 })}`),
    enabled: !!snapshotId,
  });

export const useGraph = (runId: string, snapshotId: string | null, productId: string | null) =>
  useQuery({
    queryKey: [...key(runId), "graph", snapshotId, productId],
    queryFn: () => apiFetch<Graph>(`/runs/${runId}/snapshots/${snapshotId}/graph${qs({ product_id: productId ?? "" })}`),
    enabled: !!snapshotId && !!productId,
  });
