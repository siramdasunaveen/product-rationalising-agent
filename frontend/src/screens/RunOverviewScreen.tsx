import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ApiError, apiFetch } from "../api/client";
import { usePermissions } from "../api/identity";
import { useRunOverview, type RunOverview, type StageStatus } from "../api/overview";

const STAGE_LABEL: Record<StageStatus, string> = {
  COMPLETE: "Done", RUNNING: "Running", WAITING: "Active", NOT_STARTED: "Waiting", FAILED: "Failed",
};
const SEVERITY_TONE: Record<string, string> = { CRITICAL: "danger", HIGH: "warning", MEDIUM: "info", LOW: "muted" };
const fmt = (n: number | null | undefined) => (n === null || n === undefined ? "—" : n.toLocaleString("en-GB"));
const title = (s: string) => s.charAt(0) + s.slice(1).toLowerCase().replace(/_/g, " ");
const errText = (e: unknown) => (e instanceof ApiError ? e.message : "Something went wrong.");

type Props = {
  runId: string;
  onOpenCandidates: (view?: string) => void;
  onOpenCandidate: (candidateId: string, blockerId?: string) => void;
  onOpenReview?: () => void;
  onExit: () => void;
};

/** Step 11 (screen 06): what the analysis found, stage progress and the blockers that need attention. */
export function RunOverviewScreen({ runId, onOpenCandidates, onOpenCandidate, onOpenReview, onExit }: Props) {
  const q = useRunOverview(runId);
  const { has } = usePermissions();
  const qc = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const retry = useMutation({
    mutationFn: () => apiFetch(`/runs/${runId}/analysis/retry`, { method: "POST", body: "{}" }),
    onSettled: () => qc.invalidateQueries({ queryKey: ["overview", runId] }),
  });
  const data = q.data;
  if (q.isLoading) return <div className="pra-page"><p className="pra-muted">Loading…</p></div>;
  if (!data) return <div className="pra-page"><div className="pra-alert">{errText(q.error)}</div></div>;

  const a = data.analysis;
  const busy = a?.status === "QUEUED" || a?.status === "RUNNING";
  const kpis: { value: string; label: string; view?: string; accent?: boolean }[] = [
    { value: fmt(data.metrics.sourceProducts), label: "Source products" },
    { value: fmt(data.metrics.candidates), label: "Candidates", view: "VIEW-ALL", accent: true },
    { value: fmt(data.metrics.highPriority), label: "High priority", view: "VIEW-HIGH-PRIORITY" },
    { value: fmt(data.metrics.blocked), label: "Blocked", view: "VIEW-BLOCKED" },
  ];

  return (
    <div className="pra-page">
      <div className="pra-page__head">
        <div>
          <h1 className="pra-page__title">{data.run_name}</h1>
          <p className="pra-page__subtitle">
            Run overview · {data.state_label}
            {data.snapshot && <> · {data.snapshot.code} {new Date(data.snapshot.approved_at).toLocaleString("en-GB",
              { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" })}</>}
            {" "}· <strong>{data.run_code}</strong>
          </p>
        </div>
        <div className="pra-ready__actions">
          {onOpenReview && <button type="button" className="pra-button pra-button--ghost" onClick={onOpenReview}>
            Configuration</button>}
          <button type="button" className="pra-button pra-button--ghost" onClick={onExit}>Runs</button>
        </div>
      </div>
      {error && <div className="pra-alert" role="alert">{error}</div>}
      {!a && <div className="pra-notice" role="status">Analysis has not been requested yet. Start the run from
        Review &amp; Start.</div>}
      {busy && <div className="pra-notice" role="status">Analysis is {a!.status.toLowerCase()}… this page updates by
        itself.</div>}
      {a?.status === "FAILED" && (
        <div className="pra-alert" role="alert">
          Analysis failed: {a.error ?? "unknown error"}
          {has("run.start") && (
            <button type="button" className="pra-button pra-button--ghost pra-overview__retry" disabled={retry.isPending}
              onClick={async () => {
                setError(null);
                try { await retry.mutateAsync(); } catch (e) { setError(errText(e)); }
              }}>Retry with the same configuration</button>
          )}
        </div>
      )}

      <section className="pra-kpis pra-overview__kpis" aria-label="Run metrics">
        {kpis.map((k) => {
          const body = (
            <>
              <div className={`pra-kpi__value${k.accent ? " pra-overview__accent" : ""}`}>{k.value}</div>
              <div className="pra-kpi__label">{k.label}</div>
            </>
          );
          return k.view && data.metrics.candidates !== null ? (
            <button key={k.label} type="button" className="pra-kpi pra-overview__kpi"
              onClick={() => onOpenCandidates(k.view)}>{body}</button>
          ) : <div key={k.label} className="pra-kpi">{body}</div>;
        })}
      </section>
      <Reconciliation data={data} />

      <ol className="pra-overview__stages" aria-label="Workflow stages">
        {data.stages.map((s) => (
          <li key={s.stage} className={`pra-overview__stage pra-overview__stage--${s.status.toLowerCase()}`}>
            <strong>{s.stage}</strong>
            <span>{STAGE_LABEL[s.status] ?? s.status}</span>
          </li>
        ))}
      </ol>

      <section aria-label="Attention required">
        <h2 className="pra-overview__h2">Attention required
          {data.attentionTotal > data.attention.length && (
            <span className="pra-muted pra-overview__more"> · top {data.attention.length} of {data.attentionTotal}</span>
          )}
        </h2>
        {data.attention.length === 0 ? (
          <p className="pra-muted">{a?.status === "SUCCEEDED" ? "No open blocking issues." : "Available after analysis."}</p>
        ) : (
          <ul className="pra-overview__attention">
            {data.attention.map((r) => (
              <li key={r.blocker_id}>
                <button type="button" className="pra-overview__row" disabled={!r.candidate_id}
                  onClick={() => r.candidate_id && onOpenCandidate(r.candidate_id, r.blocker_id)}>
                  <span className={`pra-overview__sev pra-overview__sev--${SEVERITY_TONE[r.severity] ?? "muted"}`}>
                    {r.severity}</span>
                  <strong>{r.subject}</strong>
                  <span>{r.message}</span>
                  <span className="pra-muted">{title(r.domain)}{r.owner_role ? ` · ${title(r.owner_role)}` : ""}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <div className="pra-actions">
        <button type="button" className="pra-button pra-button--primary" disabled={data.metrics.candidates === null}
          onClick={() => onOpenCandidates("VIEW-ALL")}>Open candidates</button>
      </div>
    </div>
  );
}

function Reconciliation({ data }: { data: RunOverview }) {
  const c = data.counts;
  const d = data.detectorSummary;
  const sp = data.sourceProducts;
  if (!c || !d || !sp) return null;
  const excluded = sp.excluded.invalid + sp.excluded.outOfScopeFamily + sp.excluded.outOfScopeMarket;
  return (
    <p className="pra-muted pra-overview__recon">
      {fmt(sp.snapshotProducts)} products in the snapshot − {fmt(excluded)} excluded = {fmt(sp.sourceProducts)} source
      products · {fmt(c.rawFindings)} findings (duplicate {d.duplicate}, overlap {d.overlap}, legacy {d.legacy},
      complexity {d.complexity}, operational burden {d.operationalBurden}) − {c.merged} merged − {c.invalid} invalid
      − {c.outOfScope} out of scope = {fmt(c.candidates)} candidates
    </p>
  );
}
