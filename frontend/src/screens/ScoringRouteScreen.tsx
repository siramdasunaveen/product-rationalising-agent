import { useState, type ReactNode } from "react";
import { ApiError } from "../api/client";
import { useConfiguration, useSaveConfiguration, type Configuration, type Setting } from "../api/configuration";
import { WizardSteps } from "../components/WizardSteps";

const ISSUE_TONE: Record<string, string> = { BLOCKING: "danger", WARNING: "warning", INFO: "muted" };
const STATUS_TONE: Record<string, string> = { PASS: "success", WARNING: "warning", BLOCKED: "danger" };
const nice = (v: unknown) =>
  typeof v === "boolean" ? (v ? "On" : "Off") : typeof v === "string" ? v.replace(/_/g, " ").toLowerCase() : String(v);
const errText = (e: unknown) =>
  e instanceof ApiError ? [e.message, ...e.fieldErrors.map((f) => f.message)].join(" ") : "Something went wrong.";

type Props = { runId: string; onBack: () => void; onContinue: () => void; onOpenAIRuntime?: () => void;
  onSelectStep?: (step: number) => void };

/** Step 9 (screen 04, wizard step 3): choose packs, see thresholds with their source, check compatibility. */
export function ScoringRouteScreen({ runId, onBack, onContinue, onOpenAIRuntime, onSelectStep }: Props) {
  const q = useConfiguration(runId);
  const save = useSaveConfiguration(runId);
  const [error, setError] = useState<string | null>(null);
  const data = q.data;
  if (q.isLoading) return <div className="pra-page"><p className="pra-muted">Loading…</p></div>;
  if (!data) return <div className="pra-page"><div className="pra-alert">{errText(q.error)}</div></div>;
  const locked = !data.editable || data.started;

  const run = async (body: { selections?: Record<string, string | null>; overrides?: Record<string, unknown> }) => {
    setError(null);
    try {
      await save.mutateAsync({ version: data.version, ...body });
    } catch (e) {
      setError(errText(e));
    }
  };
  const overrides = Object.fromEntries(data.settings.filter((s) => s.source === "Run override")
    .map((s) => [s.name, s.value]));
  const scoring = data.selections.find((s) => s.role === "scoringProfile");

  return (
    <div className="pra-page">
      <div className="pra-page__head">
        <div>
          <h1 className="pra-page__title">Create Rationalisation Run</h1>
          <p className="pra-page__subtitle">Step 3 of 5 · Choose scoring and AI runtime · <strong>{data.run_code}</strong> ·
            {" "}{data.run_name}</p>
        </div>
        <span className={`pra-pill pra-pill--${STATUS_TONE[data.status]}`}>Compatibility {data.status.toLowerCase()}</span>
      </div>
      <WizardSteps current={3} available={[1, 2, 3, 4]} onSelect={onSelectStep} />
      {data.started && <div className="pra-notice" role="status">Analysis has started: this configuration is pinned
        in the run manifest ({data.manifest_hash?.slice(7, 19)}…).</div>}
      {error && <div className="pra-alert" role="alert">{error}</div>}

      <div className="pra-cfg__grid">
        <section className="pra-card pra-cfg__profile" aria-label="Scoring profile">
          <h2 className="pra-cfg__title">Scoring profile · {scoring?.ref ?? "not chosen"}</h2>
          <p className="pra-muted">{scoring?.description}</p>
        </section>
        <section className="pra-card" aria-label="AI route">
          <h2 className="pra-cfg__title">AI route</h2>
          <AIRoute data={data} />
          {onOpenAIRuntime && <button type="button" className="pra-link" onClick={onOpenAIRuntime}>Open AI Runtime</button>}
        </section>
      </div>

      <section className="pra-card pra-map__section" aria-label="Packs">
        <h2 className="pra-hub__title">DETECTORS, SCORING AND POLICY</h2>
        <p className="pra-muted">Recommended deterministically from the run's markets, product families and objectives.
          Only published versions are offered; Start pins the exact version.</p>
        <div className="pra-cfg__packs">
          {data.selections.map((s) => (
            <div key={s.role} className="pra-cfg__pack">
              <label className="pra-field">
                <span>{s.label}</span>
                <select className="pra-input" value={s.source === "chosen" ? s.ref ?? "" : ""} disabled={locked}
                  onChange={(e) => run({ selections: { [s.role]: e.target.value || null } })}>
                  <option value="">{s.recommended ? `Recommended: ${s.recommended}` : "Choose…"}</option>
                  {s.options.map((o) => (
                    <option key={o.ref} value={o.ref} disabled={!o.eligible}>
                      {o.ref} · objectives {o.objective_coverage}%{o.eligible ? "" : " (not eligible)"}
                    </option>
                  ))}
                </select>
              </label>
              {s.ref && <div className="pra-muted pra-cfg__ref">Using <code>{s.ref}</code>
                {s.source === "recommended" ? " (recommended)" : s.source === "pinned" ? " (pinned)" : " (chosen)"}</div>}
              <details className="pra-cfg__trace"><summary>Why</summary>
                <ul>{s.trace.map((t, i) => <li key={i}>{t}</li>)}</ul></details>
            </div>
          ))}
        </div>
      </section>

      <section className="pra-card pra-map__section" aria-label="Safety thresholds">
        <h2 className="pra-hub__title">SAFETY THRESHOLDS AND EVIDENCE TREATMENT</h2>
        <p className="pra-muted">Precedence: authorised run override → policy pack → scoring profile → Telco default.
          Missing evidence is never assumed to be zero.</p>
        <div className="pra-cfg__settings">
          {data.settings.map((s) => (
            <SettingRow key={s.name} s={s} editable={!locked && data.can_override && s.overridable}
              onChange={(v) => run({ overrides: { ...overrides, [s.name]: v } })}
              onReset={s.source === "Run override" ? () => {
                const next = { ...overrides };
                delete next[s.name];
                return run({ overrides: next });
              } : undefined} />
          ))}
        </div>
      </section>

      <Issues data={data} />

      <div className="pra-actions">
        <button type="button" className="pra-button pra-button--ghost" onClick={onBack}>Back to Evidence Hub</button>
        <button type="button" className="pra-button pra-button--primary" onClick={onContinue}>Review configuration →</button>
      </div>
    </div>
  );
}

function AIRoute({ data }: { data: Configuration }) {
  const routes = Array.isArray(data.ai.routes) ? data.ai.routes : [];
  const models = Array.from(new Set(routes.flatMap((r) => r.chain)));
  return (
    <ul className="pra-cfg__ai">
      <li className="pra-cfg__ai-row pra-cfg__ai-row--on">
        <strong>{models.length ? models.join(", ") : "Templates only"}</strong>
        <span className="pra-muted">{models.length ? "Primary approved route" : "No model is called"}</span>
      </li>
      <li className="pra-cfg__ai-row"><strong>Deterministic templates</strong><span className="pra-muted">Automatic fallback</span></li>
      <li className="pra-muted">Profile {data.ai.profile_ref ?? "—"}{data.ai.strategy ? ` · ${nice(data.ai.strategy)}` : ""}</li>
    </ul>
  );
}

function SettingRow({ s, editable, onChange, onReset }: { s: Setting; editable: boolean;
  onChange: (v: unknown) => void; onReset?: () => void }) {
  const [draft, setDraft] = useState(String(s.value));
  return (
    <div className="pra-cfg__setting">
      <span>{s.label}</span>
      {editable ? (
        s.allowed ? (
          <select className="pra-input" value={String(s.value)} onChange={(e) => onChange(e.target.value)}>
            {s.allowed.map((a) => <option key={String(a)} value={String(a)}>{nice(a)}</option>)}
          </select>
        ) : (
          <input className="pra-input" type="number" min={0} max={100} value={draft}
            onChange={(e) => setDraft(e.target.value)} onBlur={() => draft !== String(s.value) && onChange(Number(draft))} />
        )
      ) : <strong>{typeof s.value === "number" ? `${s.value} / 100` : nice(s.value)}</strong>}
      <span className="pra-muted pra-cfg__source">{s.source}{onReset && editable &&
        <button type="button" className="pra-link" onClick={onReset}> Reset</button>}</span>
    </div>
  );
}

export function Issues({ data, children }: { data: Configuration; children?: ReactNode }) {
  return (
    <section className="pra-card pra-map__section" aria-label="Compatibility">
      <h2 className="pra-hub__title">COMPATIBILITY · {data.status}</h2>
      {data.issues.length === 0 ? <p className="pra-muted">All checks pass.</p> : (
        <ul className="pra-cfg__issues">
          {data.issues.map((i) => (
            <li key={i.id}>
              <span className={`pra-pill pra-pill--${ISSUE_TONE[i.severity]}`}>{i.severity.toLowerCase()}</span>
              {" "}<strong>{i.area}</strong> · {i.message}
              {i.severity === "WARNING" && i.acknowledged && <span className="pra-muted"> · acknowledged</span>}
            </li>
          ))}
        </ul>
      )}
      {children}
    </section>
  );
}
