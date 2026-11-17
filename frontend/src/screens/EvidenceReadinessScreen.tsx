import { useState } from "react";
import { ApiError } from "../api/client";
import { useReadiness, type DomainReadiness, type ReadinessSource } from "../api/readiness";
import { useRuns } from "../api/runs";

const ASSESSMENT_ICON: Record<string, string> = { AVAILABLE: "✓", LIMITED: "⚠", NOT_ASSESSED: "—", BLOCKED: "✕" };
const ASSESSMENT_TEXT: Record<string, string> = {
  AVAILABLE: "available", LIMITED: "with limitations", NOT_ASSESSED: "not assessed", BLOCKED: "blocked",
};
const STATUS_TONE: Record<string, string> = {
  AVAILABLE: "success", PARTIAL: "warning", NOT_ASSESSED: "danger", BLOCKED: "danger",
};
const DISPOSITION_LABEL: Record<string, string> = {
  RETAIN: "Retain", SIMPLIFY: "Simplify", CONSOLIDATE: "Consolidate", STANDARDISE: "Standardise", MIGRATE: "Migrate",
  RETIRE: "Retire", INVESTIGATE: "Investigate",
};

type Props = {
  runId?: string;
  onSelectRun: (runId: string) => void;
  onOpenEvidence?: (runId: string) => void;
  onOpenReconciliation?: (runId: string) => void;
};

/** Step 7 (screen 11): evidence completeness and the consequence of missing evidence. */
export function EvidenceReadinessScreen({ runId, onSelectRun, onOpenEvidence, onOpenReconciliation }: Props) {
  const [source, setSource] = useState<ReadinessSource>("snapshot");
  const runs = useRuns("");
  const q = useReadiness(runId, source);
  const data = q.data;
  const labelOf = (d: string) => data?.domains.find((x) => x.domain === d)?.label ?? d;

  return (
    <div className="pra-page">
      <div className="pra-page__head">
        <div>
          <h1 className="pra-page__title">Evidence Readiness</h1>
          <p className="pra-page__subtitle">Evidence completeness and consequence of missing evidence</p>
        </div>
        <div className="pra-ready__actions">
          {runId && onOpenEvidence && (
            <button type="button" className="pra-button pra-button--ghost" onClick={() => onOpenEvidence(runId)}>
              Evidence Hub
            </button>
          )}
          {runId && onOpenReconciliation && (
            <button type="button" className="pra-button pra-button--ghost" onClick={() => onOpenReconciliation(runId)}>
              Reconcile &amp; approve
            </button>
          )}
        </div>
      </div>

      <div className="pra-toolbar">
        <label className="pra-ready__picker">
          <span className="pra-muted">Run</span>
          <select className="pra-input" value={runId ?? ""} onChange={(e) => e.target.value && onSelectRun(e.target.value)}>
            <option value="">Choose a run…</option>
            {(runs.data?.items ?? []).map((r) => (
              <option key={r.id} value={r.id}>{r.run_code} · {r.name}</option>
            ))}
          </select>
        </label>
        {runId && (
          <div className="pra-segmented" role="group" aria-label="Evidence source">
            <button type="button" aria-pressed={source === "snapshot"} onClick={() => setSource("snapshot")}>
              Approved snapshot
            </button>
            <button type="button" aria-pressed={source === "preview"} onClick={() => setSource("preview")}>
              Preview latest reconciliation
            </button>
          </div>
        )}
      </div>

      {!runId && (
        <div className="pra-card pra-empty"><strong>Choose a run</strong>Readiness is calculated per run from its
          approved evidence snapshot.</div>
      )}
      {runId && q.isLoading && <p className="pra-muted">Loading…</p>}
      {runId && q.error && (
        <div className="pra-alert" role="alert">
          {q.error instanceof ApiError ? q.error.message : "Could not load evidence readiness."}
        </div>
      )}

      {data && (
        <>
          <p className={`pra-ready__basis pra-ready__basis--${data.based_on.kind}`}>
            <strong>{data.based_on.label}</strong>
            {data.based_on.content_hash && <> · <code>{data.based_on.content_hash.slice(7, 19)}…</code></>}
            {data.based_on.at && <> · {new Date(data.based_on.at).toLocaleString()}</>}
            {" "}· {data.kpis.products} products · policy {data.policy_ref}
            {data.based_on.kind === "preview" && <> · decisions use the approved snapshot only</>}
          </p>

          <section className="pra-kpis pra-ready__kpis" aria-label="Readiness summary">
            <Kpi value={`${data.kpis.overall_completeness}%`} label="Overall evidence completeness" />
            <Kpi value={`${data.kpis.domains_available} / ${data.kpis.domains_selected}`} label="Domains available" />
            <Kpi value={data.kpis.assessment_limitations} label="Assessment limitations" />
            <Kpi value={data.kpis.approval_blockers} label="Approval blockers" />
          </section>

          <section aria-label="Evidence domains" className="pra-ready__domains">
            <div className="pra-ready__row pra-ready__row--head">
              <span>Evidence domain</span><span>Status</span><span>Completeness</span>
              <span>Assessment consequence</span><span>Required action</span>
            </div>
            {data.domains.filter((d) => d.selected).map((d) => <DomainRow key={d.domain} d={d} />)}
            {data.domains.some((d) => !d.selected) && (
              <p className="pra-muted pra-ready__unselected">
                Not selected for this run (counted as not assessed):{" "}
                {data.domains.filter((d) => !d.selected).map((d) => d.label).join(" · ")}
              </p>
            )}
          </section>

          <section className="pra-card pra-ready__panel" aria-label="Assessment availability">
            <h2 className="pra-hub__title">ASSESSMENT AVAILABILITY</h2>
            <ul className="pra-ready__assessments">
              {data.assessments.map((a) => (
                <li key={a.key} className={`pra-ready__assessment pra-ready__assessment--${a.status.toLowerCase()}`}
                  title={a.limited_by.length ? `Limited by: ${a.limited_by.map(labelOf).join(", ")}` : undefined}>
                  <span className="pra-ready__icon" aria-hidden="true">{ASSESSMENT_ICON[a.status]}</span>
                  <strong>{a.label}</strong> <span className="pra-muted">{ASSESSMENT_TEXT[a.status]}</span>
                </li>
              ))}
            </ul>
          </section>

          <section className="pra-card pra-ready__panel" aria-label="Dispositions">
            <h2 className="pra-hub__title">DISPOSITIONS THIS EVIDENCE SUPPORTS</h2>
            <p className="pra-muted">A decision can only be submitted when every domain it needs is AVAILABLE in the
              approved snapshot.</p>
            <table className="pra-uploads">
              <thead><tr><th>Disposition</th><th>Needs</th><th>Ready</th><th>Missing</th></tr></thead>
              <tbody>
                {data.dispositions.map((d) => (
                  <tr key={d.disposition}>
                    <td><strong>{DISPOSITION_LABEL[d.disposition] ?? d.disposition}</strong></td>
                    <td>{d.requires.length ? d.requires.map(labelOf).join(" · ") : "No mandatory evidence"}</td>
                    <td>
                      <span className={`pra-pill pra-pill--${d.ready ? "success" : "danger"}`}>
                        {d.ready ? "ready" : "blocked"}
                      </span>
                    </td>
                    <td>{d.missing.map((m) => `${labelOf(m.domain)} (${m.status === "NOT_ASSESSED" ? "missing"
                      : m.status.toLowerCase()})`).join(" · ") || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        </>
      )}
    </div>
  );
}

function Kpi({ value, label }: { value: string | number; label: string }) {
  return (
    <div className="pra-kpi">
      <div className="pra-kpi__value">{value}</div>
      <div className="pra-kpi__label">{label}</div>
    </div>
  );
}

function DomainRow({ d }: { d: DomainReadiness }) {
  const tone = STATUS_TONE[d.status];
  return (
    <div className={`pra-ready__row pra-ready__row--${d.status.toLowerCase()}`}>
      <span>
        <strong>{d.label}</strong>
        <span className="pra-muted pra-ready__importance">{d.importance_label}</span>
      </span>
      <span>
        <span className={`pra-ready__status pra-ready__status--${tone}`}>{d.status_label.toUpperCase()}</span>
        {d.stale && <span className="pra-pill pra-pill--warning">stale</span>}
        {d.conflicted_facts > 0 && <span className="pra-pill pra-pill--muted">{d.conflicted_facts} deferred</span>}
      </span>
      <span>
        <span className="pra-ready__pct">{d.coverage}%</span>
        <span className="pra-ready__bar" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={d.coverage}
          aria-label={`${d.label} completeness`}>
          <span className={`pra-ready__fill pra-ready__fill--${tone}`} style={{ width: `${d.coverage}%` }} />
        </span>
      </span>
      <span>{d.consequence}</span>
      <span>{d.action}</span>
    </div>
  );
}
