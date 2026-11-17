import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "./client";

export type StageStatus = "NOT_STARTED" | "RUNNING" | "COMPLETE" | "FAILED" | "WAITING";

export type AttentionRow = {
  blocker_id: string; code: string; severity: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"; subject: string;
  candidate_id: string | null; candidate_code: string | null; message: string; domain: string;
  owner_role: string | null; due_date: string | null; created_at: string;
};

export type RunOverview = {
  run_id: string; run_code: string; run_name: string; state: string; state_label: string;
  snapshot: { id: string; code: string; approved_at: string } | null;
  analysis: { id: string; seq: number; status: string; error: string | null; finished_at: string | null } | null;
  metrics: { sourceProducts: number | null; candidates: number | null; highPriority: number | null;
    blocked: number | null };
  detectorSummary: { rawFindings: number; duplicate: number; overlap: number; legacy: number; complexity: number;
    operationalBurden: number } | null;
  counts: { rawFindings: number; merged: number; invalid: number; outOfScope: number; superseded: number;
    candidates: number } | null;
  sourceProducts: { snapshotProducts: number; sourceProducts: number;
    excluded: { invalid: number; outOfScopeFamily: number; outOfScopeMarket: number } } | null;
  stages: { stage: string; status: StageStatus; started_at: string | null; finished_at: string | null }[];
  attention: AttentionRow[];
  attentionTotal: number;
  projectionVersion: number;
};

const POLL_MS = 2000;

export const useRunOverview = (runId: string) =>
  useQuery({
    queryKey: ["overview", runId],
    queryFn: () => apiFetch<RunOverview>(`/runs/${runId}/overview`),
    refetchInterval: (q) => {
      const s = q.state.data?.analysis?.status;
      return s === "QUEUED" || s === "RUNNING" ? POLL_MS : false;
    },
  });
