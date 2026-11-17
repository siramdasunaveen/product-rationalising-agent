import { useMemo, useState } from "react";
import { ApiError } from "../api/client";
import {
  useCandidateViews, useCandidates, useDeleteView, useSaveView, type Filters, type ListParams, type SavedView,
} from "../api/candidates";

const PRIORITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW"];
const STATUSES = ["REVIEW", "BLOCKED", "EVIDENCE_GAP", "DECISION", "APPROVAL", "APPROVED"];
const TYPES = ["DUPLICATE", "OVERLAP", "LEGACY", "COMPLEXITY", "OPERATIONAL_BURDEN"];
const ACTIONS = ["RETAIN", "SIMPLIFY", "CONSOLIDATE", "STANDARDISE", "MIGRATE", "RETIRE", "INVESTIGATE"];
const PRIORITY_TONE: Record<string, string> = { CRITICAL: "danger", HIGH: "warning", MEDIUM: "info", LOW: "muted" };
const STATUS_TONE: Record<string, string> = { BLOCKED: "danger", EVIDENCE_GAP: "warning", APPROVED: "success",
  APPROVAL: "info", DECISION: "info", REVIEW: "muted" };
const title = (s: string) => s.charAt(0) + s.slice(1).toLowerCase().replace(/_/g, " ");
const errText = (e: unknown) => (e instanceof ApiError ? e.message : "Something went wrong.");

type Props = { runId: string; initialView?: string; onOpenCandidate: (candidateId: string) => void;
  onOpenOverview: () => void };

/** Step 12 (screen 07): every active candidate of the run under saved views and filters. The filter bar always
 *  shows exactly what the server applied (effectiveFilters); inventory and filtered counts are shown apart. */
export function CandidateExplorerScreen({ runId, initialView, onOpenCandidate, onOpenOverview }: Props) {
  const [viewId, setViewId] = useState<string | undefined>(initialView ?? "VIEW-ALL");
  const [explicit, setExplicit] = useState<Filters>({});
  const [sort, setSort] = useState<string | undefined>();
  const [page, setPage] = useState(1);
  const [saveName, setSaveName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const views = useCandidateViews();
  const save = useSaveView();
  const remove = useDeleteView();
  const params = useMemo<ListParams>(() => ({ viewId, ...explicit, sort, page, pageSize: 50 }), [viewId, explicit, sort, page]);
  const q = useCandidates(runId, params);
  const data = q.data;

  const pick = (v: SavedView) => { setViewId(v.id); setExplicit({}); setSort(undefined); setPage(1); };
  const toggle = (key: keyof Filters, value: string) => {
    const current = (explicit[key] as string[] | undefined) ?? (data?.effectiveFilters[key] as string[] | undefined) ?? [];
    const next = current.includes(value) ? current.filter((x) => x !== value) : [...current, value];
    setExplicit({ ...explicit, [key]: next });
    setPage(1);
  };
  const ef = data?.effectiveFilters ?? {};
  const on = (key: keyof Filters, value: string) => ((ef[key] as string[] | undefined) ?? []).includes(value);

  return (
    <div className="pra-page">
      <div className="pra-page__head">
        <div>
          <h1 className="pra-page__title">Candidate Explorer</h1>
          <p className="pra-page__subtitle">
            {data ? <>{data.runCode}{data.snapshot && <> · {data.snapshot.code}</>} · <strong>{data.filteredCount}</strong> of{" "}
              {data.inventoryTotal} candidates{data.savedView && <> · {data.savedView.name}</>}</> : "Loading…"}
          </p>
        </div>
        <div className="pra-ready__actions">
          <button type="button" className="pra-button pra-button--ghost" onClick={onOpenOverview}>Run overview</button>
        </div>
      </div>
      {error && <div className="pra-alert" role="alert">{error}</div>}
      {q.error && <div className="pra-alert" role="alert">{errText(q.error)}</div>}
      {data && !data.analysed && <div className="pra-notice" role="status">No analysis has succeeded for this run yet.</div>}

      <div className="pra-explorer">
        <aside className="pra-explorer__views" aria-label="Saved views">
          <h2 className="pra-overview__h2">Views</h2>
          {(["TEAM", "PERSONAL"] as const).map((scope) => (
            <div key={scope}>
              <div className="pra-muted pra-explorer__scope">{scope === "TEAM" ? "Team" : "Personal"}</div>
              {(views.data ?? []).filter((v) => v.scope === scope).map((v) => (
                <div key={v.id} className="pra-explorer__view">
                  <button type="button" className={`pra-chip${viewId === v.id ? " pra-chip--on" : ""}`}
                    title={v.purpose} onClick={() => pick(v)}>{v.name}</button>
                  {scope === "PERSONAL" && (
                    <button type="button" className="pra-button pra-button--ghost pra-explorer__del" aria-label={`Delete ${v.name}`}
                      onClick={async () => { try { await remove.mutateAsync(v.id); if (viewId === v.id) setViewId("VIEW-ALL"); }
                        catch (e) { setError(errText(e)); } }}>×</button>
                  )}
                </div>
              ))}
              {scope === "PERSONAL" && (views.data ?? []).every((v) => v.scope !== "PERSONAL") && (
                <p className="pra-muted pra-explorer__none">None yet. Set filters, then save them as a view.</p>
              )}
            </div>
          ))}
          <form className="pra-explorer__save" onSubmit={async (e) => {
            e.preventDefault();
            setError(null);
            const { includeHidden, ...filters } = ef;
            try {
              const v = await save.mutateAsync({ name: saveName, filters: { ...filters, ...(includeHidden ? { includeHidden } : {}) },
                sort: data?.sort });
              setSaveName(""); pick(v);
            } catch (err) { setError(errText(err)); }
          }}>
            <input className="pra-input" value={saveName} maxLength={80} placeholder="Save current filters as…"
              onChange={(e) => setSaveName(e.target.value)} />
            <button type="submit" className="pra-button pra-button--ghost" disabled={!saveName.trim() || save.isPending}>Save</button>
          </form>
        </aside>

        <div className="pra-explorer__main">
          <div className="pra-explorer__filters" aria-label="Filters applied by the server">
            <input className="pra-input" placeholder="Search id or product" maxLength={100}
              value={(explicit.query ?? ef.query ?? "") as string}
              onChange={(e) => { setExplicit({ ...explicit, query: e.target.value || undefined }); setPage(1); }} />
            <FilterGroup label="Priority" values={PRIORITIES} on={(v) => on("priority", v)} toggle={(v) => toggle("priority", v)} />
            <FilterGroup label="Status" values={STATUSES} on={(v) => on("status", v)} toggle={(v) => toggle("status", v)} />
            <FilterGroup label="Type" values={TYPES} on={(v) => on("types", v)} toggle={(v) => toggle("types", v)} />
            <FilterGroup label="Recommendation" values={ACTIONS} on={(v) => on("recommendation", v)}
              toggle={(v) => toggle("recommendation", v)} />
            <label className="pra-explorer__hidden">
              <input type="checkbox" checked={!!ef.includeHidden}
                onChange={(e) => { setExplicit({ ...explicit, includeHidden: e.target.checked }); setPage(1); }} />
              {" "}Include candidates below the review threshold
            </label>
            <label className="pra-explorer__hidden">Sort{" "}
              <select className="pra-input" value={sort ?? ""} onChange={(e) => setSort(e.target.value || undefined)}>
                <option value="">View default (priority, score)</option>
                <option value="score:DESC">Score, high to low</option>
                <option value="score:ASC">Score, low to high</option>
                <option value="status:ASC">Status</option>
                <option value="id:ASC">Candidate id</option>
              </select>
            </label>
          </div>

          <div className="pra-table pra-explorer__table" role="table">
            <div className="pra-table__head pra-explorer__row" role="row">
              <span>Candidate</span><span>Products</span><span>Type</span><span>Score</span><span>Priority</span>
              <span>Recommendation</span><span>Impact</span><span>Status</span>
            </div>
            {data && data.items.length === 0 && <div className="pra-table__empty">No candidates match these filters.</div>}
            {(data?.items ?? []).map((c) => (
              <button key={c.id} type="button" role="row" className="pra-table__row pra-table__row--link pra-explorer__row"
                onClick={() => onOpenCandidate(c.candidateId)}>
                <strong>{c.candidateId}</strong>
                <span>{c.products.join(" + ")}</span>
                <span>{c.primaryTypeLabel}{c.supportingTypes.length > 0 && <span className="pra-muted"> +{c.supportingTypes.length}</span>}</span>
                <span>{c.score ?? <span className="pra-muted">Not assessed</span>}</span>
                <span>{c.effectivePriority
                  ? <span className={`pra-pill pra-pill--${PRIORITY_TONE[c.effectivePriority]}`}>{title(c.effectivePriority)}</span>
                  : <span className="pra-muted">—</span>}</span>
                <span>{c.recommendation ? title(c.recommendation) : <span className="pra-muted">None</span>}</span>
                <span>{c.impactSummary}</span>
                <span className={`pra-pill pra-pill--${STATUS_TONE[c.status] ?? "muted"}`}>{title(c.status)}</span>
              </button>
            ))}
          </div>
          {data && data.filteredCount > data.pageSize && (
            <div className="pra-actions">
              <span className="pra-muted">Page {data.page} of {Math.ceil(data.filteredCount / data.pageSize)}</span>
              <button type="button" className="pra-button pra-button--ghost" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button>
              <button type="button" className="pra-button pra-button--ghost"
                disabled={page * data.pageSize >= data.filteredCount} onClick={() => setPage(page + 1)}>Next</button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function FilterGroup({ label, values, on, toggle }: { label: string; values: string[]; on: (v: string) => boolean;
  toggle: (v: string) => void }) {
  return (
    <div className="pra-explorer__group" role="group" aria-label={label}>
      <span className="pra-muted">{label}</span>
      {values.map((v) => (
        <button key={v} type="button" className={`pra-chip${on(v) ? " pra-chip--on" : ""}`} aria-pressed={on(v)}
          onClick={() => toggle(v)}>{title(v)}</button>
      ))}
    </div>
  );
}
