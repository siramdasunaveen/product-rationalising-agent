import { useState } from "react";
import { ApiError } from "../api/client";
import { useAcknowledge, useConfiguration, useStartRun } from "../api/configuration";
import { WizardSteps } from "../components/WizardSteps";
import { Issues } from "./ScoringRouteScreen";

const OBJECTIVE_LABEL: Record<string, string> = {
  REDUCE_DUPLICATES: "Duplicates", BSS_SUNSET: "BSS sunset", REDUCE_FALLOUT: "Order fallout",
  IMPROVE_MARGIN: "Margin", MIGRATION_READINESS: "Migration", PORTFOLIO_STANDARDISATION: "Standardisation",
};
const errText = (e: unknown) =>
  e instanceof ApiError ? [e.message, ...e.fieldErrors.map((f) => f.message)].join(" ") : "Something went wrong.";
const months = (a: string, b: string) => {
  const s = new Date(a), e = new Date(b);
  return (e.getFullYear() - s.getFullYear()) * 12 + e.getMonth() - s.getMonth();
};

type Props = { runId: string; onEdit: (step: number) => void; onStarted: () => void; onExit: () => void };

/** Step 9 (screen 05, wizard step 4): confirm configuration, acknowledge limitations, start analysis. */
export function ReviewStartScreen({ runId, onEdit, onStarted, onExit }: Props) {
  const q = useConfiguration(runId);
  const ack = useAcknowledge(runId);
  const start = useStartRun(runId);
  const [error, setError] = useState<string | null>(null);
  const [ticked, setTicked] = useState<Set<string>>(new Set());
  const data = q.data;
  if (q.isLoading) return <div className="pra-page"><p className="pra-muted">Loading…</p></div>;
  if (!data) return <div className="pra-page"><div className="pra-alert">{errText(q.error)}</div></div>;

  const sel = (role: string) => data.selections.find((s) => s.role === role)?.ref ?? "not chosen";
  const set = (name: string) => data.settings.find((s) => s.name === name)?.value;
  const k = data.readiness?.kpis;
  const blocking = data.issues.filter((i) => i.severity === "BLOCKING");
  const pending = data.unacknowledged;
  const routes = Array.isArray(data.ai.routes) ? data.ai.routes : [];
  const models = Array.from(new Set(routes.flatMap((r) => r.chain)));
  const editable = !data.started;

  const doAck = async () => {
    setError(null);
    try {
      await ack.mutateAsync({ version: data.version, warning_ids: Array.from(ticked) });
      setTicked(new Set());
    } catch (e) {
      setError(errText(e));
    }
  };
  const doStart = async () => {
    setError(null);
    try {
      await start.mutateAsync(data.version);
      onStarted();
    } catch (e) {
      setError(errText(e));
    }
  };

  const cards = [
    { title: "Scope", tone: "green", step: 1, text: `${data.scope.markets.join(", ")} · ${data.scope.product_families
      .join(", ")} · ${months(data.scope.period_start, data.scope.period_end)} months · Owner ${data.scope.owner_name}` },
    { title: "Objectives", tone: "blue", step: 1,
      text: data.scope.objectives.map((o) => OBJECTIVE_LABEL[o] ?? o).join(" · ") },
    { title: "Evidence", tone: "orange", step: 2, text: data.snapshot
      ? `${data.snapshot.code} · ${k?.overall_completeness ?? 0}% complete · ${k?.domains_available}/${k?.domains_selected} domains available · metadata and aggregates only`
      : "No approved evidence snapshot yet" },
    { title: "Scoring", tone: "blue", step: 3, text: `${sel("scoringProfile")} · recommend ≥${set("recommendedThreshold")} · review ≥${set("reviewThreshold")} · detectors ${sel("detectorPack")}` },
    { title: "AI route", tone: "purple", step: 3,
      text: `${models.length ? models.join(", ") + " primary" : "Templates only"} · templates fallback · ${data.ai.profile_ref ?? ""}` },
    { title: "Governance", tone: "green", step: 3,
      text: `${sel("policyPack")} · priority ${sel("priorityPolicy")} · human review ${set("humanReviewRequired") ? "on" : "off"} · decision audit 7 years` },
  ];

  return (
    <div className="pra-page">
      <div className="pra-page__head">
        <div>
          <h1 className="pra-page__title">Review and Start</h1>
          <p className="pra-page__subtitle">Step 4 of 5 · Confirm configuration before analysis · <strong>{data.run_code}</strong></p>
        </div>
        <button type="button" className="pra-button pra-button--ghost" onClick={onExit}>Runs</button>
      </div>
      <WizardSteps current={4} available={[1, 2, 3, 4]} onSelect={onEdit} />
      {error && <div className="pra-alert" role="alert">{error}</div>}
      {data.started && (
        <div className="pra-notice" role="status">Analysis started. Configuration is locked for reproducibility
          (manifest <code>{data.manifest_hash?.slice(7, 23)}…</code>).</div>
      )}

      <div className="pra-review__grid">
        {cards.map((c) => (
          <section key={c.title} className="pra-card pra-review__card" aria-label={c.title}>
            <span className={`pra-review__dot pra-review__dot--${c.tone}`} aria-hidden="true" />
            <div>
              <h2 className="pra-cfg__title">{c.title}</h2>
              <p>{c.text}</p>
            </div>
            {editable && <button type="button" className="pra-link" onClick={() => onEdit(c.step)}>Edit</button>}
          </section>
        ))}
      </div>

      <Issues data={data}>
        {editable && pending.length > 0 && (
          <div className="pra-review__ack">
            <p><strong>Evidence limitations to acknowledge before starting</strong></p>
            {data.issues.filter((i) => pending.includes(i.id)).map((i) => (
              <label key={i.id} className="pra-review__ack-row">
                <input type="checkbox" checked={ticked.has(i.id)} onChange={(e) => {
                  const next = new Set(ticked);
                  if (e.target.checked) next.add(i.id); else next.delete(i.id);
                  setTicked(next);
                }} />
                <span>{i.message}</span>
              </label>
            ))}
            <button type="button" className="pra-button pra-button--ghost" disabled={ticked.size === 0 || ack.isPending}
              onClick={doAck}>Acknowledge selected</button>
          </div>
        )}
      </Issues>

      {editable && (
        <div className="pra-actions">
          <span className="pra-muted">
            {blocking.length ? `Start is blocked: ${blocking.map((b) => b.message).join("; ")}`
              : pending.length ? `${pending.length} limitation(s) still to acknowledge`
                : "All mandatory checks pass. Start freezes the configuration and runs analysis in the background."}
          </span>
          <button type="button" className="pra-button pra-button--ghost" onClick={onExit}>Save as draft</button>
          <button type="button" className="pra-button pra-button--primary"
            disabled={blocking.length > 0 || pending.length > 0 || start.isPending} onClick={doStart}>Start analysis</button>
        </div>
      )}
      {data.started && (
        <div className="pra-actions">
          <button type="button" className="pra-button pra-button--primary" onClick={onStarted}>Open run overview →</button>
        </div>
      )}
    </div>
  );
}
