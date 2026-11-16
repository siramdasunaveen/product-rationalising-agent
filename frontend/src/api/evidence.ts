import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiDownload, apiFetch } from "./client";

export type Problem = { code: string; message: string };

export type UploadSummary = {
  id: string;
  domain: string;
  filename: string;
  size_bytes: number;
  status: "PROCESSING" | "READY" | "REJECTED";
  reasons: Problem[];
  uploaded_by_name: string;
  uploaded_at: string;
  processed_at: string | null;
  default_sheet: string | null;
  schema_version: number | null;
  confirmed_at: string | null;
};

export type Column = { index: number; letter: string; header: string; type: string; empty_pct?: number };

export type TemplateMatch = {
  complete: boolean;
  required_found: number;
  required_total: number;
  required_missing: string[];
  one_of_missing: string[];
  unknown_columns: string[];
};

export type SheetInfo = {
  name: string;
  hidden: boolean;
  header_row: number | null;
  data_rows: number;
  columns: Column[];
  samples: (string | null)[][];
  warnings: string[];
  problems: string[];
  valid: boolean;
  template_match?: TemplateMatch;
  template_ref?: string;
};

export type SourceSchema = {
  id: string;
  version: number;
  fingerprint: string;
  sheet_name: string;
  header_row: number;
  columns: Column[];
  template_ref: string | null;
  template_match: TemplateMatch | null;
  created_at: string;
};

export type UploadDetail = UploadSummary & {
  sha256: string;
  scan: { clean: boolean; engine: string; signature: string | null } | null;
  discovery: { default_sheet: string | null; template_ref?: string; sheets: SheetInfo[] } | null;
  schema: SourceSchema | null;
  duplicate: boolean;
};

export type DomainStatus = "NO_SOURCE" | "CHECKING" | "NEEDS_CONFIRMATION" | "READY" | "REJECTED";

export type EvidenceDomain = {
  key: string;
  label: string;
  live_system: string;
  typical_systems: string;
  selected: boolean;
  proposed: boolean;
  proposal_reasons: string[];
  status: DomainStatus;
  template_ref: string | null;
  source_types: { type: string; label: string; available: boolean }[];
  uploads: UploadSummary[];
};

export type EvidenceOverview = {
  run_id: string;
  run_code: string;
  run_name: string;
  version: number;
  editable: boolean;
  selection_confirmed: boolean;
  domains: EvidenceDomain[];
};

const POLL_MS = 2000;
const key = (runId: string) => ["evidence", runId] as const;

export const useEvidence = (runId: string) =>
  useQuery({
    queryKey: key(runId),
    queryFn: () => apiFetch<EvidenceOverview>(`/runs/${runId}/evidence`),
    // Keep polling while a file is being scanned and read in the background.
    refetchInterval: (q) =>
      q.state.data?.domains.some((d) => d.uploads.some((u) => u.status === "PROCESSING")) ? POLL_MS : false,
  });

export const useUploadDetail = (runId: string, uploadId: string | null) =>
  useQuery({
    queryKey: ["evidence", runId, "upload", uploadId],
    queryFn: () => apiFetch<UploadDetail>(`/runs/${runId}/evidence/uploads/${uploadId}`),
    enabled: !!uploadId,
    refetchInterval: (q) => (q.state.data?.status === "PROCESSING" ? POLL_MS : false),
  });

export function useSetDomains(runId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { domains: string[]; version: number }) =>
      apiFetch<EvidenceOverview>(`/runs/${runId}/evidence/domains`, { method: "PUT", body: JSON.stringify(args) }),
    onSuccess: (data) => {
      qc.setQueryData(key(runId), data);
      qc.invalidateQueries({ queryKey: ["runs"] });
    },
  });
}

export function useUploadFile(runId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ domain, file }: { domain: string; file: File }) => {
      const body = new FormData();
      body.append("file", file);
      return apiFetch<UploadDetail>(`/runs/${runId}/evidence/${domain}/uploads`, { method: "POST", body });
    },
    onSettled: () => qc.invalidateQueries({ queryKey: key(runId) }),
  });
}

export const usePreviewSheet = (runId: string) =>
  useMutation({
    mutationFn: (a: { uploadId: string; sheet: string; header_row: number | null }) =>
      apiFetch<SheetInfo>(`/runs/${runId}/evidence/uploads/${a.uploadId}/preview`, {
        method: "POST",
        body: JSON.stringify({ sheet: a.sheet, header_row: a.header_row }),
      }),
  });

export function useConfirmSheet(runId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (a: { uploadId: string; sheet: string; header_row: number }) =>
      apiFetch<UploadDetail>(`/runs/${runId}/evidence/uploads/${a.uploadId}/confirm`, {
        method: "POST",
        body: JSON.stringify({ sheet: a.sheet, header_row: a.header_row }),
      }),
    onSuccess: (u) => {
      qc.setQueryData(["evidence", runId, "upload", u.id], u);
      qc.invalidateQueries({ queryKey: key(runId) });
    },
  });
}

/** Download the governed Excel template of a domain and hand it to the browser as a file. */
export async function downloadTemplate(domain: string): Promise<void> {
  const { blob, filename } = await apiDownload(`/evidence/domains/${domain}/template`);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
