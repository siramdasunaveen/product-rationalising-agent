import { useState } from "react";
import { ApiError } from "../api/client";
import { useDetectRequests, useRequestQueue, type EvidenceRequest, type QueueParams } from "../api/evidenceRequests";
import { usePermissions } from "../api/identity";

const STATUS_TONE: Record<string, string> = { DRAFT: "muted", OPEN: "danger", IN_PROGRESS: "warning", RECEIVED: "success",
  ACCEPTED: "info", REJECTED: "danger", SUPERSEDED: "muted" };
const STATUS_LABEL: Record<string, string> = { DRAFT: "Draft", OPEN: "Open", IN_PROGRESS: "Awaiting", RECEIVED: "Received",
  ACCEPTED: "Accepted", REJECTED: "Rejected", SUPERSEDED: "Superseded" };
const CHIPS: { label: string; params: QueueParams }[] = [
  { label: "Blocking", params: { status: "DRAFT,OPEN,IN_PROGRESS,RECEIVED" } },
  { label: "Provisioning", params: { domain: "PROVISIONING" } },
  { label: "Inventory", params: { domain: "INVENTORY" } },
  { label: "My requests", params: { mine: true } },
];
const STEPS = [
  ["01", "Ground", "Core product evidence", "CRM · CPQ · lifecycle · technical catalog"],
  ["02", "Discover", "Preliminary candidates", "Find overlap, complexity and legacy patterns"],
  ["03", "Diagnose", "Evidence gap", "Determine which assessment cannot proceed"],
  ["04", "Request", "Minimum aggregate", "Create a decision-specific evidence contract"],
  ["05", "Fulfil", "Customer-controlled", "Secure query · API · extract · Excel"],
  ["06", "Reassess", "Updated decision", "Refresh completeness, scoring and gates"],
];
const title = (s: string | null) => (s ? s.charAt(0) + s.slice(1).toLowerCase().replace(/_/g, " ") : "—");
const errText = (e: unknown) => (e instanceof ApiError ? e.message : "Something went wrong.");

type Props = { runId?: string; onOpenRequest: (runId: string, code: string) => void;
  onOpenCandidate?: (runId: string, candidateCode: string) => void };

/** Step 13 (screens 13 and 14): progressive evidence collection and the role-aware evidence request queue. */
export function EvidenceRequestsScreen({ runId, onOpenRequest, onOpenCandidate }: Props) {
  const [params, setParams] = useState<QueueParams>({});
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);
  const { has } = usePermissions();
  const q = useRequestQueue(runId, { ...params, query: query || undefined });
  const detect = useDetectRequests(runId ?? "");
  const m = q.data?.metrics;
  const same = (p: QueueParams) => JSON.stringify(p) === JSON.stringify(params);

  return (
    <div className="pra-page">
      <div className="pra-page__head">
        <div>
          <h1 className="pra-page__title">Evidence Requests</h1>
          <p className="pra-page__subtitle">Dynamically generated from candidate assessments and missing evidence.
            {runId ? " This run only." : " All runs."}</p>
        </div>
        {runId && has("decision.submit") && (
          <button type="button" className="pra-button pra-button--ghost" disabled={detect.isPending}
            onClick={async () => { setError(null); try { await detect.mutateAsync(undefined); } catch (e) { setError(errText(e)); } }}>
            Detect gaps</button>
        )}
      </div>
      {error && <div className="pra-alert" role="alert">{error}</div>}
      {q.error && <div className="pra-alert" role="alert">{errText(q.error)}</div>}

      <section className="pra-kpis" aria-label="Queue metrics">
        {[["Open", m?.open, `Across ${m?.openCandidates ?? 0} candidates`], ["Blocking", m?.blocking, "Preventing approval"],
          ["Awaiting", m?.awaiting, "Customer fulfilment"], ["Ready", m?.ready, "Reassess now"]].map(([l, v, h]) => (
          <div key={String(l)} className="pra-kpi">
            <div className="pra-kpi__value">{v === undefined ? "—" : String(v).padStart(2, "0")}</div>
            <div className="pra-kpi__label">{l}<br /><span className="pra-muted">{h}</span></div>
          </div>
        ))}
      </section>

      <div className="pra-explorer__filters">
        <div className="pra-explorer__group">
          <input className="pra-input" placeholder="Search request, candidate or owner" maxLength={100} value={query}
            onChange={(e) => setQuery(e.target.value)} />
          {CHIPS.map((c) => (
            <button key={c.label} type="button" className={`pra-chip${same(c.params) ? " pra-chip--on" : ""}`}
              aria-pressed={same(c.params)} onClick={() => setParams(same(c.params) ? {} : c.params)}>{c.label}</button>
          ))}
        </div>
      </div>

      <div className="pra-table" role="table">
        <div className="pra-table__head pra-er__row" role="row">
          <span>Request</span><span>Trigger</span><span>Evidence needed</span><span>Decision impact</span><span>Owner / status</span>
        </div>
        {q.data && q.data.items.length === 0 && (
          <div className="pra-table__empty">No evidence requests. {runId ? "Use Detect gaps or Request evidence on a candidate." : ""}</div>
        )}
        {(q.data?.items ?? []).map((r: EvidenceRequest) => (
          <button key={r.id} type="button" role="row" className="pra-table__row pra-table__row--link pra-er__row"
            onClick={() => onOpenRequest(r.runId, r.code)}>
            <strong>{r.code}{!runId && r.runCode && <span className="pra-muted"> · {r.runCode}</span>}</strong>
            <span>
              {onOpenCandidate ? (
                <a href="#candidate" onClick={(e) => { e.preventDefault(); e.stopPropagation(); onOpenCandidate(r.runId, r.candidateCode); }}>
                  {r.trigger}</a>
              ) : r.trigger}
            </span>
            <span>{r.evidenceNeeded}</span>
            <span className={r.consequence === "BLOCKED" ? undefined : "pra-muted"}>{r.decisionImpact}</span>
            <span>
              <span className="pra-muted">{r.ownerId ?? title(r.ownerRole)}</span>{" "}
              <span className={`pra-pill pra-pill--${STATUS_TONE[r.status] ?? "muted"}`}>{STATUS_LABEL[r.status] ?? r.status}</span>
              {r.reassessmentStatus === "QUEUED" && <span className="pra-pill pra-pill--info">Reassessing</span>}
            </span>
          </button>
        ))}
      </div>

      <section className="pra-card pra-er__note" aria-label="One return, many requests">
        <strong>One accepted evidence return can satisfy multiple requests.</strong>
        <span className="pra-muted"> Compatible requests are linked to the new snapshot and queued for reassessment together.</span>
      </section>

      <section aria-label="Progressive evidence collection">
        <h2 className="pra-overview__h2">Progressive evidence collection</h2>
        <p className="pra-muted">Minimum evidence first. Additional aggregates only when required. Missing evidence is never
          scored as poor performance: the affected dimension stays NOT ASSESSED or BLOCKED until the minimum evidence is accepted.</p>
        <ol className="pra-overview__stages pra-er__steps">
          {STEPS.map(([n, k, t, d]) => (
            <li key={n} className="pra-overview__stage">
              <span className="pra-muted">{n} · {k}</span><strong>{t}</strong><span className="pra-muted">{d}</span>
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}
