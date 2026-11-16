import { useState } from "react";
import { usePermissions } from "../api/identity";

type Kpi = { label: string; value: number; tone?: "info" };

// Real values come from the runs API in Step 2; until then there are no runs.
const KPIS: Kpi[] = [
  { label: "Active runs", value: 0 },
  { label: "Rationalisation Opportunities", value: 0, tone: "info" },
  { label: "Awaiting approval", value: 0 },
  { label: "Blocked", value: 0 },
];

const COLUMNS = ["Run", "Portfolio scope", "Stage", "Owner", "Risk", "Updated"];

/** Assessments home (screens/01): KPIs, search, run list, next action and agent outcome. */
export function AssessmentsScreen() {
  const { has } = usePermissions();
  const [query, setQuery] = useState("");

  return (
    <div className="pra-page">
      <h1 className="pra-page__title">Assessments</h1>
      <p className="pra-page__subtitle">Start a new rationalisation assessment or continue existing work.</p>
      <div className="pra-progress" aria-hidden="true">
        <span className="pra-progress__fill" style={{ width: "15%" }} />
      </div>

      <section className="pra-kpis" aria-label="Summary">
        {KPIS.map((k) => (
          <div key={k.label} className="pra-kpi">
            <div className={`pra-kpi__value${k.tone === "info" ? " pra-kpi__value--info" : ""}`}>{k.value}</div>
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
          <button className="pra-button pra-button--primary pra-button--lg" disabled title="Available in Step 2">
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
        <div className="pra-table__empty" role="row">
          <span role="cell">
            {query ? `No runs match “${query}”.` : "No assessment runs yet. Start a new run to begin."}
          </span>
        </div>
      </div>

      <section className="pra-guidance">
        <div>
          <h2 className="pra-guidance__title">Next action</h2>
          <div className="pra-guidance__card">
            <ol className="pra-steps">
              <li><span className="pra-steps__num">1</span>Start a new run.</li>
              <li><span className="pra-steps__num">2</span>Reopen an existing run when needed.</li>
            </ol>
          </div>
        </div>
        <div>
          <h2 className="pra-guidance__title">Agent outcome</h2>
          <div className="pra-guidance__card">
            <ul className="pra-steps pra-steps--outcome">
              <li><span className="pra-steps__num">✓</span>Run context is created.</li>
              <li><span className="pra-steps__num">✓</span>Stage and risk become visible.</li>
            </ul>
          </div>
        </div>
      </section>
    </div>
  );
}
