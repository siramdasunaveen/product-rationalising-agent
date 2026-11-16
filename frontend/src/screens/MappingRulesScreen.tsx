import { useEffect, useMemo, useState } from "react";
import { ApiError } from "../api/client";
import { useEvidence } from "../api/evidence";
import { useMe, usePermissions } from "../api/identity";
import {
  EDITABLE,
  useAISuggest,
  useApply,
  useClone,
  useCreateMapping,
  useDomainMapping,
  useMappingVersion,
  usePublish,
  useReview,
  useSaveDraft,
  useStagedRecords,
  useSubmit,
  useValidate,
  type Execution,
  type MappingType,
  type MappingVersion,
  type NullHandling,
  type Rule,
  type ValidationResult,
} from "../api/mapping";

const STATUS_TONE: Record<string, string> = {
  DRAFT: "muted", VALIDATING: "info", VALIDATION_FAILED: "danger", READY_FOR_REVIEW: "info", IN_REVIEW: "warning",
  APPROVED: "success", PUBLISHED: "success", SUPERSEDED: "muted", DEPRECATED: "muted", RETIRED: "muted",
  PASS: "success", WARNING: "warning", BLOCKED: "danger", FAILED: "danger", RUNNING: "info", QUEUED: "info",
  SUCCEEDED: "success",
};
const BY_LABEL: Record<Rule["proposed_by"], string> = {
  TEMPLATE: "Template", PRIOR_MAPPING: "Earlier mapping", NAME_MATCH: "Name match", ALIAS: "Alias", AI: "AI",
  USER: "You",
};
const FIELD_TYPES: MappingType[] = ["DIRECT", "LOOKUP", "TRANSFORM", "CROSSWALK"];

const Pill = ({ s }: { s: string }) => (
  <span className={`pra-pill pra-pill--${STATUS_TONE[s] ?? "muted"}`}>{s.replace(/_/g, " ").toLowerCase()}</span>
);
const short = (h: string | null) => (h ? `${h.slice(7, 19)}…` : "—");
const errText = (e: unknown) =>
  e instanceof ApiError ? [e.message, ...e.fieldErrors.map((f) => f.message)].join(" ") : "Something went wrong.";

/** Mapping Rules (step 5): map a confirmed source to the canonical schema, validate, review, publish, apply. */
export function MappingRulesScreen({ runId, domain, onBack }: { runId: string; domain: string; onBack: () => void }) {
  const head = useDomainMapping(runId, domain);
  const create = useCreateMapping(runId, domain);
  const { has } = usePermissions();
  const [version, setVersion] = useState<number | undefined>();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (head.data && version === undefined) setVersion(head.data.version);
  }, [head.data, version]);

  const detail = useMappingVersion(runId, head.data?.mapping_id, version);
  const m = detail.data ?? (head.data && head.data.version === version ? head.data : undefined);

  if (head.isLoading) return <div className="pra-page"><p className="pra-muted">Loading mapping…</p></div>;

  return (
    <div className="pra-page">
      <div className="pra-page__head">
        <div>
          <h1 className="pra-page__title">Mapping Rules</h1>
          <p className="pra-page__subtitle">
            Map the confirmed source columns of <strong>{domain.replace(/_/g, " ").toLowerCase()}</strong> to the
            canonical Telco schema. Mapping stages records; it never chooses between sources.
          </p>
        </div>
        <button type="button" className="pra-button pra-button--ghost" onClick={onBack}>Back to Evidence Hub</button>
      </div>
      {error && <div className="pra-alert" role="alert">{error}</div>}

      {head.data === null && (
        <div className="pra-card pra-empty">
          <strong>No mapping yet</strong>
          The system suggests rules from the governed template, earlier approved mappings, field names and aliases.
          <div style={{ marginTop: 16 }}>
            {has("evidence.map") ? (
              <button type="button" className="pra-button pra-button--primary" disabled={create.isPending}
                onClick={async () => {
                  setError(null);
                  try {
                    const created = await create.mutateAsync(undefined);
                    setVersion(created.version);
                    head.refetch();
                  } catch (e) {
                    setError(errText(e));
                  }
                }}>
                {create.isPending ? "Suggesting…" : "Create mapping with suggestions"}
              </button>
            ) : <span className="pra-muted">A Data Steward creates the mapping.</span>}
          </div>
        </div>
      )}

      {m && <MappingVersionView key={`${m.mapping_id}-${m.version}`} m={m} onVersion={setVersion} onError={setError} />}
    </div>
  );
}

function MappingVersionView({ m, onVersion, onError }: {
  m: MappingVersion; onVersion: (v: number) => void; onError: (e: string | null) => void;
}) {
  const { has } = usePermissions();
  const { data: me } = useMe();
  const editable = EDITABLE.includes(m.status) && has("evidence.map");
  const [rules, setRules] = useState<Rule[]>(m.rules);
  const [ack, setAck] = useState(false);
  const save = useSaveDraft(m.run_id);
  const validate = useValidate(m.run_id);
  const submit = useSubmit(m.run_id);
  const publish = usePublish(m.run_id);
  const clone = useClone(m.run_id);
  const ai = useAISuggest(m.run_id);
  const [aiNote, setAiNote] = useState<string | null>(null);

  useEffect(() => setRules(m.rules), [m.lock_version]); // eslint-disable-line react-hooks/exhaustive-deps
  const dirty = JSON.stringify(rules) !== JSON.stringify(m.rules);
  const v = m.validation;
  const result = v?.result ?? null;
  const canSubmit = m.status === "READY_FOR_REVIEW" && v?.matches_current_rules && !dirty &&
    (v.status === "PASS" || (v.status === "WARNING" && ack));

  const run = async (fn: () => Promise<unknown>) => {
    onError(null);
    try {
      await fn();
    } catch (e) {
      onError(errText(e));
    }
  };

  const byColumn = useMemo(() => {
    const out: Record<string, Rule> = {};
    for (const r of rules) if (r.source_column && r.mapping_type !== "RELATIONSHIP" && r.mapping_type !== "DERIVED") out[r.source_column] = r;
    return out;
  }, [rules]);
  const otherRules = rules.filter((r) => r.mapping_type === "RELATIONSHIP" || r.mapping_type === "DERIVED");

  const nextKey = () => `R${Math.max(0, ...rules.map((r) => Number(r.key.slice(1)) || 0)) + 1}`;
  const setColumnRule = (column: string, rule: Rule | null) =>
    setRules((prev) => {
      const rest = prev.filter((r) => !(r.source_column === column && r.mapping_type !== "RELATIONSHIP" && r.mapping_type !== "DERIVED"));
      return rule ? [...rest, rule] : rest;
    });

  return (
    <>
      <div className="pra-card pra-map__head">
        <div className="pra-map__versions">
          {m.versions.map((x) => (
            <button key={x.version} type="button" onClick={() => onVersion(x.version)}
              className={`pra-chip${x.version === m.version ? " pra-chip--on" : ""}`}>
              v{x.version} · {x.status.replace(/_/g, " ").toLowerCase()}
            </button>
          ))}
        </div>
        <div className="pra-map__facts">
          <span>Status <Pill s={m.status} /></span>
          <span>Canonical schema <strong>{m.canonical_ref}</strong></span>
          <span>Source schema <strong>v{m.source_schema.version}</strong> · sheet “{m.source_schema.sheet_name}”, header row {m.source_schema.header_row}</span>
          <span title={m.content_hash ?? m.current_hash}>Content hash <code>{short(m.content_hash ?? m.current_hash)}</code>
            {m.content_hash ? " (frozen)" : ""}</span>
          {m.status_reason && <span>Note: {m.status_reason}</span>}
        </div>
        <div className="pra-map__actions">
          {editable && (
            <>
              <button type="button" className="pra-button pra-button--ghost" disabled={!dirty || save.isPending}
                onClick={() => run(() => save.mutateAsync({ m, rules }))}>{save.isPending ? "Saving…" : "Save draft"}</button>
              <button type="button" className="pra-button pra-button--ghost" disabled={dirty || ai.isPending}
                title={dirty ? "Save first" : "Suggest targets for unmapped columns (AI sees headers + 5 sample rows only)"}
                onClick={() => run(async () => {
                  const out = await ai.mutateAsync(m);
                  setAiNote(out.source === "llm" ? `AI suggested ${out.added} rule(s) — review them before validating.`
                    : out.source === "none" ? "Every column is mapped already." : "AI is not available; no suggestions were added.");
                })}>{ai.isPending ? "Asking AI…" : "Suggest with AI"}</button>
              <button type="button" className="pra-button pra-button--primary" disabled={dirty || validate.isPending}
                title={dirty ? "Save first" : undefined}
                onClick={() => run(() => validate.mutateAsync(m))}>Validate</button>
            </>
          )}
          {m.status === "READY_FOR_REVIEW" && has("evidence.map") && (
            <>
              {v?.status === "WARNING" && (
                <label className="pra-map__ack"><input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} />
                  I accept the {result?.warnings} warning(s)</label>
              )}
              <button type="button" className="pra-button pra-button--primary" disabled={!canSubmit || submit.isPending}
                onClick={() => run(() => submit.mutateAsync({ m, acknowledge: ack }))}>Submit for review</button>
            </>
          )}
          {m.status === "APPROVED" && has("mapping.publish") && (
            <button type="button" className="pra-button pra-button--primary" disabled={publish.isPending}
              onClick={() => run(() => publish.mutateAsync(m))}>Publish</button>
          )}
          {["PUBLISHED", "SUPERSEDED", "RETIRED", "DEPRECATED"].includes(m.status) && has("evidence.map") &&
            !m.versions.some((x) => EDITABLE.includes(x.status) || x.status === "VALIDATING") && (
            <button type="button" className="pra-button pra-button--ghost" disabled={clone.isPending}
              onClick={() => run(async () => onVersion((await clone.mutateAsync({ m })).version))}>New version</button>
          )}
        </div>
        {aiNote && <p className="pra-map__note">{aiNote}</p>}
        {m.status === "VALIDATING" && <div className="pra-notice" role="status">Validating against the confirmed file… this updates by itself.</div>}
      </div>

      {m.status === "IN_REVIEW" && (
        <ReviewPanel m={m} isSubmitter={me?.user_id === m.submitted_by} canReview={has("mapping.approve")} onError={onError} />
      )}

      <section className="pra-card pra-map__grid-card" aria-label="Column rules">
        <h2 className="pra-hub__title">COLUMN RULES</h2>
        <p className="pra-hub__sub">One row per source column. Suggestions show where they came from; nothing is approved until a reviewer approves the version.</p>
        <div className="pra-preview">
          <table className="pra-map__grid">
            <thead>
              <tr><th>Source column</th><th>Samples</th><th>Target field</th><th>Rule</th><th>Reference</th><th>Required</th><th>If empty</th><th>Suggested by</th></tr>
            </thead>
            <tbody>
              {m.columns.map((c) => (
                <ColumnRow key={c.header} m={m} column={c} rule={byColumn[c.header] ?? null} editable={editable}
                  onChange={(r) => setColumnRule(c.header, r)} newKey={nextKey} />
              ))}
            </tbody>
          </table>
        </div>
        {otherRules.length > 0 && (
          <>
            <h3 className="pra-map__sub">Relationship and derived rules</h3>
            <ul className="pra-map__other">
              {otherRules.map((r) => (
                <li key={r.key}>
                  <code>{r.key}</code>{" "}
                  {r.mapping_type === "RELATIONSHIP" && r.relationship
                    ? <>{r.relationship.fromEntity} ({r.relationship.fromColumn}) — <strong>{r.relationship.name ?? `name from “${r.relationship.nameColumn}”`}</strong> → {r.relationship.toEntity} ({r.relationship.toColumn})</>
                    : <>{r.target_entity}.{r.target_field} = {r.derived?.op}({(r.derived?.inputs ?? [r.derived?.value]).join(", ")})</>}
                  <span className="pra-map__by">{BY_LABEL[r.proposed_by]}</span>
                  {editable && (
                    <button type="button" className="pra-link" onClick={() => setRules((p) => p.filter((x) => x.key !== r.key))}>Remove</button>
                  )}
                </li>
              ))}
            </ul>
          </>
        )}
      </section>

      {v && <ValidationPanel v={v} />}
      {m.approvals.length > 0 && (
        <section className="pra-card pra-map__section">
          <h2 className="pra-hub__title">REVIEW HISTORY</h2>
          <ul className="pra-map__other">
            {m.approvals.map((a) => (
              <li key={a.created_at}><Pill s={a.decision === "APPROVE" ? "APPROVED" : a.decision === "RETURN" ? "DRAFT" : "RETIRED"} />{" "}
                {a.decision.toLowerCase()} by {a.actor_name} · {new Date(a.created_at).toLocaleString()} · hash <code>{short(a.content_hash)}</code>
                {a.comment && <> — “{a.comment}”</>}</li>
            ))}
          </ul>
        </section>
      )}
      {m.status === "PUBLISHED" && <ApplyPanel m={m} onError={onError} />}
      {m.executions.length > 0 && <ExecutionsPanel m={m} />}
    </>
  );
}

function ColumnRow({ m, column, rule, editable, onChange, newKey }: {
  m: MappingVersion; column: MappingVersion["columns"][number]; rule: Rule | null; editable: boolean;
  onChange: (r: Rule | null) => void; newKey: () => string;
}) {
  const target = rule ? `${rule.target_entity}.${rule.target_field}` : "";
  const field = rule ? m.targets.entities.find((e) => e.name === rule.target_entity)?.fields.find((f) => f.name === rule.target_field) : undefined;
  const update = (patch: Partial<Rule>) => rule && onChange({ ...rule, ...patch, proposed_by: "USER", confidence: null });
  const refOptions = rule?.mapping_type === "LOOKUP"
    ? m.options.lookups.filter((o) => o.target.entity === rule.target_entity && o.target.field === rule.target_field).map((o) => o.ref)
    : rule?.mapping_type === "TRANSFORM" ? m.options.transforms.map((o) => o.ref)
    : rule?.mapping_type === "CROSSWALK" ? m.options.crosswalks.filter((o) => o.target.entity === rule.target_entity).map((o) => o.ref)
    : [];
  const refField = rule?.mapping_type === "LOOKUP" ? "lookup_ref" : rule?.mapping_type === "TRANSFORM" ? "transform_ref" : "crosswalk_ref";
  const refValue = rule ? (rule[refField as keyof Rule] as string | null) : null;

  return (
    <tr className={rule ? undefined : "pra-map__unmapped"}>
      <td><strong>{column.header}</strong><span className="pra-preview__type">{column.type}</span></td>
      <td className="pra-map__samples">
        {column.samples.filter(Boolean).length
          ? [...new Set(column.samples.filter(Boolean))].slice(0, 3).map((s) => <div key={s}>{s}</div>)
          : <span className="pra-muted">—</span>}
      </td>
      <td>
        <select className="pra-input" value={target} disabled={!editable} aria-label={`Target for ${column.header}`}
          onChange={(e) => {
            if (!e.target.value) return onChange(null);
            const [entity, f] = e.target.value.split(".");
            const fd = m.targets.entities.find((x) => x.name === entity)?.fields.find((x) => x.name === f);
            const lookup = m.options.lookups.find((o) => o.target.entity === entity && o.target.field === f);
            onChange({
              key: rule?.key ?? newKey(), mapping_type: fd?.type === "enum" && lookup ? "LOOKUP" : "DIRECT",
              source_column: column.header, target_entity: entity, target_field: f,
              lookup_ref: fd?.type === "enum" && lookup ? lookup.ref : null, transform_ref: null, crosswalk_ref: null,
              source_system: null, derived: null, relationship: null, required: !!fd?.required, null_handling: "KEEP_NULL",
              default_value: null, confidence: null, proposed_by: "USER",
            });
          }}>
          <option value="">— not mapped —</option>
          {m.targets.entities.map((e) => (
            <optgroup key={e.name} label={e.label}>
              {e.fields.map((f) => (
                <option key={f.name} value={`${e.name}.${f.name}`}>{e.name}.{f.name} ({f.type}{f.required ? ", required" : ""})</option>
              ))}
            </optgroup>
          ))}
        </select>
      </td>
      <td>
        {rule && (
          <select className="pra-input" value={rule.mapping_type} disabled={!editable} aria-label="Rule type"
            onChange={(e) => update({ mapping_type: e.target.value as MappingType, lookup_ref: null, transform_ref: null, crosswalk_ref: null })}>
            {FIELD_TYPES.map((t) => <option key={t} value={t}>{t.toLowerCase()}</option>)}
          </select>
        )}
      </td>
      <td>
        {rule && rule.mapping_type !== "DIRECT" && (
          <>
            <select className="pra-input" value={refValue ?? ""} disabled={!editable} aria-label="Reference"
              onChange={(e) => update({ [refField]: e.target.value || null } as Partial<Rule>)}>
              <option value="">— choose —</option>
              {[...new Set([...(refValue ? [refValue] : []), ...refOptions])].map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
            {rule.mapping_type === "CROSSWALK" && (
              <input className="pra-input" placeholder="Source system" value={rule.source_system ?? ""} disabled={!editable}
                onChange={(e) => update({ source_system: e.target.value.toUpperCase() || null })} />
            )}
          </>
        )}
        {rule && field?.type === "enum" && rule.mapping_type === "DIRECT" && (
          <span className="pra-preview__type">values must already be {field.values?.slice(0, 4).join(", ")}…</span>
        )}
      </td>
      <td>{rule && <input type="checkbox" checked={rule.required} disabled={!editable} aria-label="Required"
        onChange={(e) => update({ required: e.target.checked })} />}</td>
      <td>
        {rule && (
          <>
            <select className="pra-input" value={rule.null_handling} disabled={!editable} aria-label="If empty"
              onChange={(e) => update({ null_handling: e.target.value as NullHandling })}>
              <option value="KEEP_NULL">leave empty</option>
              <option value="DEFAULT">use default</option>
              <option value="REJECT_ROW">reject row</option>
            </select>
            {rule.null_handling === "DEFAULT" && (
              <input className="pra-input" placeholder="Default" value={rule.default_value ?? ""} disabled={!editable}
                onChange={(e) => update({ default_value: e.target.value || null })} />
            )}
          </>
        )}
      </td>
      <td>{rule && <span className={`pra-map__by pra-map__by--${rule.proposed_by.toLowerCase()}`}>
        {BY_LABEL[rule.proposed_by]}{rule.confidence != null ? ` · ${rule.confidence}` : ""}</span>}</td>
    </tr>
  );
}

function ValidationPanel({ v }: { v: NonNullable<MappingVersion["validation"]> }) {
  const r = v.result;
  return (
    <section className="pra-card pra-map__section" aria-label="Validation">
      <h2 className="pra-hub__title">VALIDATION <Pill s={v.status} /></h2>
      {!v.matches_current_rules && <div className="pra-notice">These results are for an earlier set of rules. Validate again.</div>}
      {r?.error && <div className="pra-alert">{r.error}</div>}
      {r?.counts && (
        <div className="pra-map__stats">
          <div><strong>{r.counts.mapped_rows}</strong> of {r.counts.total_rows} rows mapped</div>
          <div><strong>{r.counts.rejected_rows}</strong> rows rejected</div>
          <div><strong>{r.required_field_coverage}%</strong> required-field coverage</div>
          <div>Records: {Object.entries(r.counts.records).map(([k, n]) => `${k} ${n}`).join(" · ") || "none"}</div>
          <div>Relationships: {Object.entries(r.counts.relationships).map(([k, n]) => `${k} ${n}`).join(" · ") || "none"}</div>
          {r.impact?.rule_diff && (
            <div>Changes from v{r.impact.parent_version}: {r.impact.rule_diff.added.length} added,
              {" "}{r.impact.rule_diff.changed.length} changed, {r.impact.rule_diff.removed.length} removed</div>
          )}
        </div>
      )}
      {r && r.issues.length > 0 && (
        <ul className="pra-map__issues">
          {r.issues.map((i, n) => (
            <li key={n} className={`pra-map__issue pra-map__issue--${i.severity.toLowerCase()}`}>
              <strong>{i.severity === "BLOCKING" ? "Blocking" : "Warning"}</strong> · {i.layer.toLowerCase()} · {i.message}
              {i.rule_key && <> (rule <code>{i.rule_key}</code>)</>}
              {i.examples.length > 0 && <div className="pra-muted">{i.examples.join("; ")}</div>}
            </li>
          ))}
        </ul>
      )}
      {r?.status === "PASS" && <p className="pra-muted">No problems found.</p>}
      {r?.sample_rows && r.sample_rows.length > 0 && <SampleRows r={r} />}
    </section>
  );
}

function SampleRows({ r }: { r: ValidationResult }) {
  return (
    <details className="pra-map__details">
      <summary>Sample output (first {r.sample_rows!.length} rows)</summary>
      <ul className="pra-map__other">
        {r.sample_rows!.map((s) => (
          <li key={s.row}>Row {s.row}: {s.records.map((x) => `${x.entity} ${x.key} ${JSON.stringify(x.values)}`).join("; ")}</li>
        ))}
      </ul>
    </details>
  );
}

function ReviewPanel({ m, isSubmitter, canReview, onError }: {
  m: MappingVersion; isSubmitter: boolean; canReview: boolean; onError: (e: string | null) => void;
}) {
  const review = useReview(m.run_id);
  const [comment, setComment] = useState("");
  const decide = async (decision: "APPROVE" | "RETURN" | "REJECT") => {
    onError(null);
    try {
      await review.mutateAsync({ m, decision, comment });
      setComment("");
    } catch (e) {
      onError(errText(e));
    }
  };
  return (
    <section className="pra-card pra-map__section pra-map__review" aria-label="Review">
      <h2 className="pra-hub__title">REVIEW</h2>
      <p>You are reviewing version {m.version} with content hash <code title={m.content_hash ?? ""}>{short(m.content_hash)}</code>.
        Approval applies to exactly this content; any later change needs a new review.</p>
      {!canReview ? <p className="pra-muted">Waiting for a Data Steward to review.</p>
        : isSubmitter ? <p className="pra-muted">You submitted this version, so another reviewer must decide.</p>
        : (
          <>
            <textarea className="pra-input" rows={3} maxLength={2000} placeholder="Comment (required to return or reject)"
              value={comment} onChange={(e) => setComment(e.target.value)} />
            <div className="pra-map__actions">
              <button type="button" className="pra-button pra-button--primary" disabled={review.isPending} onClick={() => decide("APPROVE")}>Approve</button>
              <button type="button" className="pra-button pra-button--ghost" disabled={review.isPending || comment.trim().length < 3} onClick={() => decide("RETURN")}>Return for changes</button>
              <button type="button" className="pra-button pra-button--ghost" disabled={review.isPending || comment.trim().length < 3} onClick={() => decide("REJECT")}>Reject</button>
            </div>
          </>
        )}
    </section>
  );
}

function ApplyPanel({ m, onError }: { m: MappingVersion; onError: (e: string | null) => void }) {
  const { has } = usePermissions();
  const evidence = useEvidence(m.run_id);
  const apply = useApply(m.run_id);
  const uploads = evidence.data?.domains.find((d) => d.key === m.domain)?.uploads
    .filter((u) => u.status === "READY" && u.schema_version != null) ?? [];
  const applied = new Set(m.executions.filter((e) => e.status !== "FAILED").map((e) => e.upload_id));
  return (
    <section className="pra-card pra-map__section" aria-label="Apply">
      <h2 className="pra-hub__title">APPLY</h2>
      <p className="pra-hub__sub">Applying stages canonical records with field lineage. The same file and version always give the same result (applying again returns the earlier run).</p>
      {uploads.length === 0 ? <p className="pra-muted">No confirmed files.</p> : (
        <ul className="pra-map__other">
          {uploads.map((u) => (
            <li key={u.id}>
              {u.filename} · schema v{u.schema_version} · {new Date(u.uploaded_at).toLocaleString()}{" "}
              {has("evidence.map") && (
                <button type="button" className="pra-button pra-button--ghost" disabled={apply.isPending}
                  onClick={async () => {
                    onError(null);
                    try {
                      await apply.mutateAsync({ uploadId: u.id, versionId: m.version_id, idempotencyKey: `${u.id}:${m.version_id}` });
                    } catch (e) {
                      onError(errText(e));
                    }
                  }}>{applied.has(u.id) ? "Applied — view" : "Apply"}</button>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function ExecutionsPanel({ m }: { m: MappingVersion }) {
  const firstDone = m.executions.find((e) => e.status === "SUCCEEDED")?.id ?? null;
  const [open, setOpen] = useState<string | null>(firstDone);
  useEffect(() => {
    if (open === null && firstDone) setOpen(firstDone); // an apply just finished: show its records
  }, [firstDone, open]);
  return (
    <section className="pra-card pra-map__section" aria-label="Applied runs">
      <h2 className="pra-hub__title">STAGED OUTPUT</h2>
      <table className="pra-uploads">
        <thead><tr><th>Applied</th><th>Status</th><th>Rows</th><th>Records</th><th>Output hash</th><th /></tr></thead>
        <tbody>
          {m.executions.map((e: Execution) => (
            <tr key={e.id}>
              <td>{new Date(e.created_at).toLocaleString()}</td>
              <td><Pill s={e.status} />{e.error && <div className="pra-uploads__reason">{e.error}</div>}</td>
              <td>{e.counts ? `${e.counts.mapped_rows} mapped, ${e.counts.rejected_rows} rejected` : "—"}</td>
              <td>{e.counts ? Object.entries(e.counts.records).map(([k, n]) => `${k} ${n}`).join(" · ") : "—"}</td>
              <td><code title={e.output_hash ?? ""}>{short(e.output_hash)}</code></td>
              <td>{e.status === "SUCCEEDED" && <button type="button" className="pra-link" onClick={() => setOpen(e.id)}>Records</button>}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {open && <StagedRecords runId={m.run_id} executionId={open} />}
    </section>
  );
}

function StagedRecords({ runId, executionId }: { runId: string; executionId: string }) {
  const [entity, setEntity] = useState("");
  const recs = useStagedRecords(runId, executionId, entity);
  const [lineageOf, setLineageOf] = useState<string | null>(null);
  const items = recs.data?.items ?? [];
  const entities = [...new Set(items.map((i) => i.entity))];
  return (
    <div className="pra-map__records">
      <label className="pra-field" style={{ maxWidth: 260 }}>
        <span className="pra-field__label">Entity</span>
        <select className="pra-input" value={entity} onChange={(e) => setEntity(e.target.value)}>
          <option value="">All</option>
          {[...new Set([...entities, entity].filter(Boolean))].map((e) => <option key={e} value={e}>{e}</option>)}
        </select>
      </label>
      <p className="pra-muted">{recs.data?.total ?? 0} record(s){(recs.data?.total ?? 0) > items.length ? ` (first ${items.length})` : ""}</p>
      <ul className="pra-map__other">
        {items.map((r) => (
          <li key={r.id}>
            <strong>{r.entity}</strong> {r.key} · rows {r.source_rows.join(", ")} · <code>{JSON.stringify(r.values)}</code>{" "}
            <button type="button" className="pra-link" onClick={() => setLineageOf(lineageOf === r.id ? null : r.id)}>
              {lineageOf === r.id ? "Hide lineage" : "Lineage"}</button>
            {lineageOf === r.id && (
              <table className="pra-uploads">
                <thead><tr><th>Field</th><th>Source</th><th>Rule</th><th>Reference</th></tr></thead>
                <tbody>
                  {r.lineage.map((l) => (
                    <tr key={l.field}>
                      <td>{l.field}</td>
                      <td>“{l.source_value ?? "—"}” from {l.source_field ?? "—"} · sheet {l.sheet} row {l.row}</td>
                      <td>{l.rule} ({l.mapping_type.toLowerCase()})</td>
                      <td>{l.lookup_ref ?? l.transform_ref ?? l.crosswalk_ref ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
