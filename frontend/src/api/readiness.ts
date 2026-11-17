import { useQuery } from "@tanstack/react-query";
import { apiFetch, qs } from "./client";

export type ReadinessSource = "snapshot" | "preview";
export type DomainStatus = "AVAILABLE" | "PARTIAL" | "NOT_ASSESSED" | "BLOCKED";
export type AssessmentStatus = "AVAILABLE" | "LIMITED" | "NOT_ASSESSED" | "BLOCKED";

export type DomainReadiness = {
  domain: string; label: string; selected: boolean; importance: string; importance_label: string;
  status: DomainStatus; status_label: string; coverage: number; covered: number; total: number; stale: boolean;
  conflicted_facts: number; consequence: string; action: string;
};

export type Readiness = {
  run_id: string;
  source: ReadinessSource;
  based_on: { kind: "snapshot" | "preview" | "none"; label: string; code?: string; content_hash?: string;
    at?: string; reconciliation_id?: string };
  policy_ref: string;
  kpis: { overall_completeness: number; domains_available: number; domains_selected: number;
    assessment_limitations: number; approval_blockers: number; products: number };
  domains: DomainReadiness[];
  assessments: { key: string; label: string; status: AssessmentStatus; limited_by: string[] }[];
  dispositions: { disposition: string; requires: string[]; ready: boolean;
    missing: { domain: string; status: DomainStatus }[] }[];
  result_hash: string;
};

export const useReadiness = (runId: string | undefined, source: ReadinessSource) =>
  useQuery({
    queryKey: ["readiness", runId, source],
    queryFn: () => apiFetch<Readiness>(`/runs/${runId}/evidence-readiness${qs({ source })}`),
    enabled: !!runId,
  });
