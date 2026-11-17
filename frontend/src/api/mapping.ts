import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, apiFetch, qs } from "./client";

export type MappingType = "DIRECT" | "LOOKUP" | "TRANSFORM" | "CROSSWALK" | "RELATIONSHIP" | "DERIVED";
export type NullHandling = "KEEP_NULL" | "DEFAULT" | "REJECT_ROW";

export type Rule = {
  key: string;
  mapping_type: MappingType;
  source_column: string | null;
  target_entity: string | null;
  target_field: string | null;
  lookup_ref: string | null;
  transform_ref: string | null;
  crosswalk_ref: string | null;
  source_system: string | null;
  derived: { op: string; inputs?: string[]; separator?: string; value?: string } | null;
  relationship: {
    name?: string; nameColumn?: string; fromEntity: string; fromColumn: string; toEntity: string; toColumn: string;
  } | null;
  required: boolean;
  null_handling: NullHandling;
  default_value: string | null;
  confidence: number | null;
  proposed_by: "TEMPLATE" | "PRIOR_MAPPING" | "NAME_MATCH" | "ALIAS" | "AI" | "USER";
};

export type FieldDef = { name: string; label?: string; type: string; required?: boolean; values?: string[] };
export type EntityDef = { name: string; label: string; keyFields: string[]; fields: FieldDef[] };

export type Issue = {
  layer: string;
  severity: "BLOCKING" | "WARNING";
  code: string;
  message: string;
  rule_key: string | null;
  count: number;
  examples: string[];
};

export type ValidationResult = {
  status: "PASS" | "WARNING" | "BLOCKED";
  blocking_errors: number;
  warnings: number;
  issues: Issue[];
  counts?: {
    total_rows: number; mapped_rows: number; rejected_rows: number;
    records: Record<string, number>; relationships: Record<string, number>; conflicts: number;
  };
  required_field_coverage?: number;
  unknown_lookup_values?: Record<string, { value: string; count: number }[]>;
  unresolved_crosswalks?: Record<string, { value: string; count: number }[]>;
  sample_rows?: { row: number; records: { entity: string; key: string; values: Record<string, unknown> }[] }[];
  rejected_rows?: { row: number; reasons: string[] }[];
  impact?: { rule_diff: { added: string[]; removed: string[]; changed: string[] } | null; parent_version: number | null };
  error?: string;
};

export type Execution = {
  id: string;
  upload_id: string;
  mapping_version_id: string;
  status: "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED";
  execution_key: string;
  counts: ValidationResult["counts"] | null;
  rejected_rows: { row: number; reasons: string[] }[] | null;
  conflicts: { entity: string; key: string; rows: number[]; fields: string[] }[] | null;
  output_hash: string | null;
  error: string | null;
  created_at: string;
  finished_at: string | null;
  existing: boolean;
};

export type MappingVersion = {
  version_id: string;
  mapping_id: string;
  run_id: string;
  domain: string;
  version: number;
  parent_version: number | null;
  status: string;
  status_reason: string | null;
  lock_version: number;
  content_hash: string | null;
  current_hash: string;
  canonical_ref: string;
  canonical_digest: string;
  source_schema: { id: string; version: number; fingerprint: string; sheet_name: string; header_row: number };
  columns: { header: string; type: string; samples: (string | null)[] }[];
  rules: Rule[];
  validation: {
    id: string; status: string; content_hash: string; matches_current_rules: boolean;
    result: ValidationResult | null; created_at: string; finished_at: string | null;
  } | null;
  approvals: { decision: string; comment: string | null; content_hash: string; actor_name: string; created_at: string }[];
  executions: Execution[];
  warnings_acknowledged: boolean;
  submitted_by: string | null;
  submitted_at: string | null;
  approved_by: string | null;
  published_at: string | null;
  targets: { entities: EntityDef[]; relationships: { name: string; from: string[]; to: string }[] };
  options: {
    lookups: { key: string; ref: string; target: { entity: string; field: string } }[];
    transforms: { key: string; ref: string; input: string; output: string }[];
    crosswalks: { key: string; ref: string; target: { entity: string } }[];
  };
  versions: { version: number; status: string; content_hash: string | null; created_at: string; published_at: string | null }[];
};

export const EDITABLE = ["DRAFT", "VALIDATION_FAILED", "READY_FOR_REVIEW"];
const POLL_MS = 1500;

const vKey = (runId: string, mappingId: string, version: number) => ["mapping", runId, mappingId, version] as const;

/** The domain's mapping (newest version), or null when none exists yet. */
const srcQ = (sourceId?: string | null) => (sourceId ? `?source_id=${sourceId}` : "");

export const useDomainMapping = (runId: string, domain: string, sourceId?: string | null) =>
  useQuery({
    queryKey: ["mapping", runId, "domain", domain, sourceId ?? ""],
    queryFn: async () => {
      try {
        return await apiFetch<MappingVersion>(`/runs/${runId}/evidence/${domain}/mapping${srcQ(sourceId)}`);
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) return null;
        throw e;
      }
    },
    retry: false,
  });

export const useMappingVersion = (runId: string, mappingId: string | undefined, version: number | undefined) =>
  useQuery({
    queryKey: vKey(runId, mappingId ?? "", version ?? 0),
    queryFn: () => apiFetch<MappingVersion>(`/runs/${runId}/mappings/${mappingId}/versions/${version}`),
    enabled: !!mappingId && !!version,
    refetchInterval: (q) => {
      const d = q.state.data;
      const busy = d?.status === "VALIDATING" || d?.executions.some((e) => e.status === "QUEUED" || e.status === "RUNNING");
      return busy ? POLL_MS : false;
    },
  });

function useMappingMutation<A>(runId: string, fn: (a: A) => Promise<MappingVersion>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (m) => {
      qc.setQueryData(vKey(runId, m.mapping_id, m.version), m);
      qc.invalidateQueries({ queryKey: ["mapping", runId] });
      qc.invalidateQueries({ queryKey: ["evidence", runId] });
    },
  });
}

const base = (m: { run_id: string; mapping_id: string; version: number }) =>
  `/runs/${m.run_id}/mappings/${m.mapping_id}/versions/${m.version}`;
const post = <T,>(path: string, body: unknown = {}) => apiFetch<T>(path, { method: "POST", body: JSON.stringify(body) });

export const useCreateMapping = (runId: string, domain: string, sourceId?: string | null) =>
  useMappingMutation(runId, () => post<MappingVersion>(`/runs/${runId}/evidence/${domain}/mapping${srcQ(sourceId)}`));

export const useSaveDraft = (runId: string) =>
  useMappingMutation(runId, (a: { m: MappingVersion; rules: Rule[] }) =>
    apiFetch<MappingVersion>(base(a.m), {
      method: "PATCH",
      body: JSON.stringify({ lock_version: a.m.lock_version, rules: a.rules }),
    }));

export const useValidate = (runId: string) =>
  useMappingMutation(runId, (m: MappingVersion) => post<MappingVersion>(`${base(m)}/validate`));

export const useSubmit = (runId: string) =>
  useMappingMutation(runId, (a: { m: MappingVersion; acknowledge: boolean }) =>
    post<MappingVersion>(`${base(a.m)}/submit`, { lock_version: a.m.lock_version, acknowledge_warnings: a.acknowledge }));

export const useReview = (runId: string) =>
  useMappingMutation(runId, (a: { m: MappingVersion; decision: "APPROVE" | "RETURN" | "REJECT"; comment: string }) =>
    post<MappingVersion>(`${base(a.m)}/review`, {
      decision: a.decision, content_hash: a.m.content_hash, comment: a.comment || null,
    }));

export const usePublish = (runId: string) =>
  useMappingMutation(runId, (m: MappingVersion) => post<MappingVersion>(`${base(m)}/publish`, { lock_version: m.lock_version }));

export const useClone = (runId: string) =>
  useMappingMutation(runId, (a: { m: MappingVersion; schemaVersionId?: string }) =>
    post<MappingVersion>(`${base(a.m)}/clone`, { schema_version_id: a.schemaVersionId ?? null }));

export function useAISuggest(runId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (m: MappingVersion) =>
      post<{ added: number; source: string; mapping: MappingVersion }>(`${base(m)}/ai-suggestions`, {
        lock_version: m.lock_version,
      }),
    onSuccess: (out) => {
      qc.setQueryData(vKey(runId, out.mapping.mapping_id, out.mapping.version), out.mapping);
      qc.invalidateQueries({ queryKey: ["mapping", runId] });
    },
  });
}

export function useApply(runId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (a: { uploadId: string; versionId: string; idempotencyKey: string }) =>
      apiFetch<Execution>(`/runs/${runId}/evidence/uploads/${a.uploadId}/apply`, {
        method: "POST",
        headers: { "Idempotency-Key": a.idempotencyKey },
        body: JSON.stringify({ mapping_version_id: a.versionId }),
      }),
    onSettled: () => qc.invalidateQueries({ queryKey: ["mapping", runId] }),
  });
}

export type StagedRecord = {
  id: string;
  entity: string;
  key: string;
  values: Record<string, unknown>;
  source_rows: number[];
  lineage: {
    field: string; source_field: string | null; source_value: string | null; row: number; sheet: string;
    rule: string; mapping_type: string; lookup_ref: string | null; transform_ref: string | null;
    crosswalk_ref: string | null; source_system: string;
  }[];
};

export const useStagedRecords = (runId: string, executionId: string | null, entity: string) =>
  useQuery({
    queryKey: ["mapping", runId, "records", executionId, entity],
    queryFn: () =>
      apiFetch<{ total: number; items: StagedRecord[] }>(
        `/runs/${runId}/mapping-executions/${executionId}/records${qs({ entity: entity || undefined, limit: 50 })}`,
      ),
    enabled: !!executionId,
  });
