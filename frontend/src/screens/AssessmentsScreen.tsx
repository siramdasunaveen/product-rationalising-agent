import { useEffect, useState } from "react";
import { usePermissions } from "../api/identity";
import { useRunSummary, useRuns } from "../api/runs";
import { GuidanceCards } from "../components/GuidanceCards";

const COLUMNS = ["Run", "Portfolio scope", "Stage", "Owner", "Risk", "Updated"];

function formatUpdated(iso: string) {
  const d = new Date(iso);
  return `${d.getDate()} ${d.toLocaleString("en-GB", { month: "short" })} ${d.toLocaleTimeString("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
  })}`;
}

function useDebounced<T>(value: T, ms = 250): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

/** Assessments home (screens/01 Portfolio Runs): KPIs, search, run list, reopen, start new run. */
export function AssessmentsScreen({
  onStartRun,
  onOpenRun,
}: {
  onStartRun: () => void;
  onOpenRun: (runId: string) => void;
}) {
  const { has } = usePermissions();
  const [query, setQuery] = useState("");
  const debounced = useDebounced(query.trim());
  const summary = useRunSummary();
  const runs = useRuns(debounced);

  const kpis = [
    { label: "Active runs", value: summary.data?.active_runs },
    { label: "Rationalisation Opportunities", value: summary.data?.opportunities },
    { label: "Awaiting approval", value: summary.data?.awaiting_approval },
    { label: "Blocked", value: summary.data?.blocked },
  ];
  const items = runs.data?.items ?? [];

  return (
    <div className="pra-page">
      <h1 className="pra-page__title">Assessments</h1>
      <p className="pra-page__subtitle">Start a new rationalisation assessment or continue existing work.</p>
      <div className="pra-progress" aria-hidden="true">
        <span className="pra-progress__fill" style={{ width: "15%" }} />
      </div>

      <section className="pra-kpis" aria-label="Summary">
        {kpis.map((k) => (
          <div key={k.label} className="pra-kpi">
            <div className="pra-kpi__value">{k.value ?? "–"}</div>
            <div className="pra-kpi__label">{k.label}</div>
          </div>
        ))}
      </section>

      <div className="pra-toolbar">
        <input
          className="pra-input pra-toolbar__search"
          placeholder="Search by run, portfolio or owner"
          aria-label="Search by run, portfolio or owner"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        {has("run.create") && (
          <button className="pra-button pra-button--primary pra-button--lg" onClick={onStartRun}>
            + Start new run
          </button>
        )}
      </div>

      <div className="pra-table" role="table" aria-label="Assessment runs">
        <div className="pra-table__head" role="row">
          {COLUMNS.map((c) => (
            <span key={c} role="columnheader">{c}</span>
          ))}
        </div>
        {items.map((r) => (
          <button
            key={r.id}
            type="button"
            role="row"
            className="pra-table__row pra-table__row--link"
            onClick={() => onOpenRun(r.id)}
            title={`Open ${r.run_code}`}
          >
            <span role="cell" className="pra-strong">{r.name}</span>
            <span role="cell">{r.portfolio_name}</span>
            <span role="cell">{r.stage_label}</span>
            <span role="cell">{r.owner_name}</span>
            <span role="cell">{r.risk ?? "—"}</span>
            <span role="cell">{formatUpdated(r.updated_at)}</span>
          </button>
        ))}
        {runs.isSuccess && items.length === 0 && (
          <div className="pra-table__empty" role="row">
            <span role="cell">
              {debounced ? `No runs match “${debounced}”.` : "No assessment runs yet. Start a new run to begin."}
            </span>
          </div>
        )}
        {runs.isError && (
          <div className="pra-table__empty" role="row"><span role="cell">Could not load runs.</span></div>
        )}
      </div>

      <GuidanceCards
        next={["Start a new run.", "Reopen an existing run when needed."]}
        outcome={["Run context is created.", "Stage and risk become visible."]}
      />
    </div>
  );
}
