import { useEffect, useMemo, useState } from "react";
import { ApiError } from "../api/client";
import {
  useAIInvocations,
  useAIRuntime,
  useSaveAIRuntime,
  useSetAISecret,
  useTestRoute,
  type Route,
  type Runtime,
  type TestResult,
} from "../api/aiRuntime";

const ROUTING = [
  ["TASK_SPECIFIC", "Task-specific routes"], ["QUALITY_FIRST", "Quality first"], ["COST_OPTIMISED", "Cost optimised"],
  ["CUSTOMER_HOSTED_ONLY", "Customer-hosted only"], ["NO_LLM", "No LLM"],
];
const FAILURE = [
  ["DETERMINISTIC_FALLBACK", "Deterministic template"], ["ALTERNATE_APPROVED_MODEL", "Alternate approved model"],
  ["HUMAN_REVIEW", "Template + human review"], ["BLOCK_AI_EXPLANATION", "Block AI explanation"],
];
const STRATEGY_HOSTING: Record<string, string> = {
  CUSTOMER_REGISTRY: "CUSTOMER_REGISTRY", PLATFORM_MANAGED: "PLATFORM", BYO_ENDPOINT: "CUSTOMER_HOSTED", NO_LLM: "",
};
const STRATEGY_DOT: Record<string, string> = {
  CUSTOMER_REGISTRY: "pra-ai__dot--registry", PLATFORM_MANAGED: "pra-ai__dot--platform",
  BYO_ENDPOINT: "pra-ai__dot--byo", NO_LLM: "pra-ai__dot--none",
};

type Draft = {
  strategy: string; routing_policy: string; failure_treatment: string; ai_confidence_threshold: number;
  routes: Record<string, { mode: "LLM" | "DETERMINISTIC"; primary: string; fallback: string }>;
};

const draftOf = (r: Runtime): Draft => ({
  strategy: r.strategy ?? "PLATFORM_MANAGED",
  routing_policy: r.routing_policy ?? "TASK_SPECIFIC",
  failure_treatment: r.failure_treatment ?? "DETERMINISTIC_FALLBACK",
  ai_confidence_threshold: r.ai_confidence_threshold ?? 80,
  routes: Object.fromEntries(r.routes.map((x) => [x.task, {
    mode: x.mode, primary: (x.primary ?? "").split("@")[0], fallback: (x.fallback ?? "").split("@")[0],
  }])),
});

const errText = (e: unknown) =>
  e instanceof ApiError ? [e.message, ...e.fieldErrors.map((f) => `${f.field}: ${f.message}`)].join(" ")
    : "Something went wrong.";

/** Step 8 (screen 16): customer-controlled model strategy and task-based routing. */
export function AIRuntimeScreen() {
  const q = useAIRuntime();
  const save = useSaveAIRuntime();
  const test = useTestRoute();
  const setSecret = useSetAISecret();
  const data = q.data;
  const canEdit = !!data?.can_edit;
  const calls = useAIInvocations(canEdit);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [tests, setTests] = useState<Record<string, TestResult>>({});
  const [secretFor, setSecretFor] = useState<string | null>(null);
  const [secretValue, setSecretValue] = useState("");

  useEffect(() => { if (data) setDraft(draftOf(data)); }, [data]);
  const dirty = useMemo(() => !!data && !!draft && JSON.stringify(draftOf(data)) !== JSON.stringify(draft), [data, draft]);

  if (q.isLoading || !draft) return <div className="pra-page"><p className="pra-muted">Loading…</p></div>;
  if (!data) return <div className="pra-page"><div className="pra-alert">{errText(q.error)}</div></div>;
  if (!data.profile_ref) {
    return (
      <div className="pra-page"><div className="pra-alert">No AI runtime profile is published. Run
        <code> python -m app.cli seed-artifacts</code>.</div></div>
    );
  }

  const noLLM = draft.strategy === "NO_LLM";
  const fits = (strategy: string) => data.deployments.filter(
    (d) => d.status === "PUBLISHED" && d.hosting === STRATEGY_HOSTING[strategy]
      && (draft.routing_policy !== "CUSTOMER_HOSTED_ONLY" || d.hosting !== "PLATFORM"));
  const setRoute = (task: string, patch: Partial<Draft["routes"][string]>) =>
    setDraft({ ...draft, routes: { ...draft.routes, [task]: { ...draft.routes[task], ...patch } } });
  const pickStrategy = (key: string) => {
    // Re-point each model route at a deployment that fits the new strategy (or leave it to the template).
    const allowed = new Set(fits(key).map((d) => d.key));
    const first = fits(key)[0]?.key ?? "";
    const routes = Object.fromEntries(Object.entries(draft.routes).map(([task, r]) => [task, {
      ...r,
      primary: allowed.has(r.primary) ? r.primary : first,
      fallback: allowed.has(r.fallback) ? r.fallback : "",
      mode: r.mode === "LLM" && key !== "NO_LLM" && !first ? "DETERMINISTIC" as const : r.mode,
    }]));
    setDraft({
      ...draft, strategy: key, routes,
      routing_policy: key === "NO_LLM" ? "NO_LLM" : draft.routing_policy === "NO_LLM" ? "TASK_SPECIFIC" : draft.routing_policy,
    });
  };

  const publish = async () => {
    setError(null);
    setNote(null);
    try {
      const out = await save.mutateAsync({
        expected_ref: data.profile_ref!, strategy: draft.strategy, routing_policy: draft.routing_policy,
        failure_treatment: draft.failure_treatment, ai_confidence_threshold: draft.ai_confidence_threshold,
        routes: Object.entries(draft.routes).map(([task, r]) => ({
          task, mode: r.mode, primary: r.primary || null, fallback: r.fallback || null })),
      });
      setNote(`Published ${out.profile_ref}. New runs pin this version; running and past runs keep theirs.`);
    } catch (e) {
      setError(errText(e));
    }
  };

  const runTest = async (task: string) => {
    setError(null);
    try {
      const r = await test.mutateAsync(task);
      setTests((t) => ({ ...t, [task]: r }));
    } catch (e) {
      setError(errText(e));
    }
  };

  const storeSecret = async () => {
    if (!secretFor) return;
    setError(null);
    try {
      await setSecret.mutateAsync({ name: secretFor, value: secretValue });
      setNote(`Secret ${secretFor} stored (encrypted). It is never shown again.`);
      setSecretFor(null);
      setSecretValue("");
    } catch (e) {
      setError(errText(e));
    }
  };

  return (
    <div className="pra-page">
      <div className="pra-page__head">
        <div>
          <h1 className="pra-page__title">AI Runtime</h1>
          <p className="pra-page__subtitle">Customer-controlled model strategy and task-based routing.</p>
        </div>
        <span className="pra-chip" title="Published runtime profile">{data.profile_ref}</span>
      </div>
      {error && <div className="pra-alert" role="alert">{error}</div>}
      {note && <div className="pra-notice" role="status">{note}</div>}

      <section className="pra-ai__strategies" aria-label="AI strategy">
        {data.strategies.map((s) => (
          <button key={s.key} type="button" disabled={!canEdit} aria-pressed={draft.strategy === s.key}
            className={`pra-ai__strategy${draft.strategy === s.key ? " pra-ai__strategy--on" : ""}`}
            onClick={() => pickStrategy(s.key)}>
            <span className={`pra-ai__dot ${STRATEGY_DOT[s.key] ?? ""}`} aria-hidden="true" />
            <span><strong>{s.label}</strong><span className="pra-muted">{s.description}</span></span>
          </button>
        ))}
      </section>

      <section className="pra-card pra-map__section" aria-label="Routing">
        <h2 className="pra-hub__title">ROUTING AND FALLBACK</h2>
        <div className="pra-ai__settings">
          <label className="pra-field">
            <span>Routing policy</span>
            <select className="pra-input" value={draft.routing_policy} disabled={!canEdit || noLLM}
              onChange={(e) => setDraft({ ...draft, routing_policy: e.target.value })}>
              {ROUTING.filter(([k]) => noLLM || k !== "NO_LLM").map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </select>
          </label>
          <label className="pra-field">
            <span>When AI fails</span>
            <select className="pra-input" value={draft.failure_treatment} disabled={!canEdit}
              onChange={(e) => setDraft({ ...draft, failure_treatment: e.target.value })}>
              {FAILURE.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </select>
          </label>
          <label className="pra-field">
            <span>AI confidence shown from (%)</span>
            <input className="pra-input" type="number" min={0} max={100} value={draft.ai_confidence_threshold}
              disabled={!canEdit}
              onChange={(e) => setDraft({ ...draft, ai_confidence_threshold: Number(e.target.value) })} />
          </label>
        </div>
        <p className="pra-muted pra-ai__rule">AI only explains and drafts. Scores, counts, validation, routes and
          approvals are always deterministic. AI confidence affects what is displayed, never a score or a decision.</p>
      </section>

      <section className="pra-card pra-map__section" aria-label="Task routing">
        <h2 className="pra-hub__title">TASK-BASED MODEL ROUTING</h2>
        <p className="pra-muted">Route models by task, sensitivity, capability, residency or cost. At most two approved
          models per task, then the deterministic template.</p>
        <div className="pra-ai__table">
          <table className="pra-uploads">
            <thead><tr><th>Task</th><th>Mode</th><th>Primary</th><th>Fallback</th><th>Will use</th><th>Governance</th><th /></tr></thead>
            <tbody>
              {data.routes.map((r) => (
                <RouteRow key={r.task} route={r} draft={draft.routes[r.task]} deployments={fits(draft.strategy)}
                  editable={canEdit && !noLLM && draft.routing_policy !== "QUALITY_FIRST"
                    && draft.routing_policy !== "COST_OPTIMISED"}
                  onChange={(p) => setRoute(r.task, p)} test={tests[r.task]}
                  onTest={canEdit && !dirty ? () => runTest(r.task) : undefined} testing={test.isPending} />
              ))}
            </tbody>
          </table>
        </div>
        {canEdit && (
          <div className="pra-hub__confirm">
            <span className="pra-muted">{dirty ? "Unpublished changes. Publishing creates a new profile version."
              : "The table shows the published version."}</span>
            <span className="pra-ready__actions">
              {dirty && <button type="button" className="pra-button pra-button--ghost"
                onClick={() => setDraft(draftOf(data))}>Discard</button>}
              <button type="button" className="pra-button pra-button--primary" disabled={!dirty || save.isPending}
                onClick={publish}>Publish new version</button>
            </span>
          </div>
        )}
      </section>

      <section className="pra-card pra-map__section" aria-label="Model registry">
        <h2 className="pra-hub__title">APPROVED MODELS AND ENDPOINTS</h2>
        <p className="pra-muted">Deployments come from the registry (AI deployments); add or retire them there.
          Credentials are stored encrypted and never shown.</p>
        <table className="pra-uploads">
          <thead><tr><th>Deployment</th><th>Provider / model</th><th>Hosting</th><th>Region</th><th>Status</th><th>Credential</th></tr></thead>
          <tbody>
            {data.deployments.map((d) => (
              <tr key={d.key}>
                <td><strong>{d.display_name}</strong><div className="pra-muted"><code>{d.ref}</code></div></td>
                <td>{d.provider}{d.model ? ` · ${d.model}` : ""}</td>
                <td>{d.hosting.replace(/_/g, " ").toLowerCase()}</td>
                <td>{d.region}</td>
                <td><span className={`pra-pill pra-pill--${d.status === "PUBLISHED" ? "success" : "muted"}`}>
                  {d.status === "PUBLISHED" ? "approved" : d.status.toLowerCase()}</span></td>
                <td>
                  {d.secret_ref ? (
                    <>
                      <span className={`pra-pill pra-pill--${d.secret_set ? "success" : "warning"}`}>
                        {d.secret_set ? "set" : "not set"}</span>
                      {canEdit && <button type="button" className="pra-link" onClick={() => setSecretFor(d.secret_ref)}>
                        {d.secret_set ? " Replace" : " Set"}</button>}
                    </>
                  ) : <span className="pra-muted">not needed</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {secretFor && (
          <div className="pra-ai__secret">
            <label className="pra-field">
              <span>Value for <code>{secretFor}</code></span>
              <input className="pra-input" type="password" autoComplete="off" value={secretValue}
                onChange={(e) => setSecretValue(e.target.value)} />
            </label>
            <button type="button" className="pra-button pra-button--primary" disabled={!secretValue || setSecret.isPending}
              onClick={storeSecret}>Store secret</button>
            <button type="button" className="pra-button pra-button--ghost"
              onClick={() => { setSecretFor(null); setSecretValue(""); }}>Cancel</button>
          </div>
        )}
      </section>

      {canEdit && (
        <section className="pra-card pra-map__section" aria-label="Recent AI calls">
          <h2 className="pra-hub__title">RECENT AI CALLS</h2>
          {(calls.data ?? []).length === 0 ? <p className="pra-muted">No AI calls yet.</p> : (
            <table className="pra-uploads">
              <thead><tr><th>When</th><th>Task</th><th>Answered by</th><th>Attempts</th><th>Notes</th></tr></thead>
              <tbody>
                {calls.data!.map((c) => (
                  <tr key={c.id}>
                    <td>{new Date(c.created_at).toLocaleString()}</td>
                    <td>{c.task.replace(/_/g, " ").toLowerCase()}</td>
                    <td>{c.source === "llm" ? <><code>{c.deployment_ref}</code> · {c.model}</>
                      : <span className="pra-pill pra-pill--muted">deterministic template</span>}</td>
                    <td>{c.attempts}</td>
                    <td className="pra-muted">{c.needs_human_review ? "Needs human review. " : ""}{c.errors.slice(0, 2).join("; ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}
    </div>
  );
}

function RouteRow({ route, draft, deployments, editable, onChange, onTest, testing, test }: {
  route: Route; draft: Draft["routes"][string]; deployments: Runtime["deployments"]; editable: boolean;
  onChange: (p: Partial<Draft["routes"][string]>) => void; onTest?: () => void; testing: boolean; test?: TestResult;
}) {
  const options = deployments;
  const det = draft.mode === "DETERMINISTIC";
  return (
    <tr>
      <td><strong>{route.label}</strong></td>
      <td>
        <select className="pra-input" value={draft.mode} disabled={!editable}
          onChange={(e) => onChange({ mode: e.target.value as "LLM" | "DETERMINISTIC" })}>
          <option value="LLM">Model</option>
          <option value="DETERMINISTIC">Deterministic</option>
        </select>
      </td>
      <td>
        <select className="pra-input" value={draft.primary} disabled={!editable || det}
          onChange={(e) => onChange({ primary: e.target.value, fallback: e.target.value ? draft.fallback : "" })}>
          <option value="">—</option>
          {options.map((d) => <option key={d.key} value={d.key}>{d.display_name}</option>)}
        </select>
      </td>
      <td>
        <select className="pra-input" value={draft.fallback} disabled={!editable || det || !draft.primary}
          onChange={(e) => onChange({ fallback: e.target.value })}>
          <option value="">Deterministic only</option>
          {options.filter((d) => d.key !== draft.primary).map((d) => <option key={d.key} value={d.key}>{d.display_name}</option>)}
        </select>
      </td>
      <td>
        {route.chain.length ? route.chain.map((c) => <div key={c}><code>{c}</code></div>)
          : <span className="pra-muted">Deterministic template</span>}
        {Object.keys(route.rejected).length > 0 && (
          <details className="pra-ai__rejected"><summary>{Object.keys(route.rejected).length} not eligible</summary>
            <ul>{Object.entries(route.rejected).map(([k, v]) => <li key={k}><code>{k}</code>: {v.join("; ")}</li>)}</ul>
          </details>
        )}
      </td>
      <td>
        <ul className="pra-ai__checks">
          {route.checks.map((c) => (
            <li key={c.key} title={c.message} className={`pra-ai__check pra-ai__check--${c.status.toLowerCase()}`}>
              {c.label} {c.status === "PASS" ? "✓" : c.status === "WARNING" ? "⚠" : "✕"}
            </li>
          ))}
        </ul>
        <span className={`pra-pill pra-pill--${route.compliant ? "success" : "danger"}`}>
          {route.compliant ? "Policy compliant" : "Not compliant"}</span>
      </td>
      <td>
        {onTest && <button type="button" className="pra-link" disabled={testing} onClick={onTest}>Test</button>}
        {test && (
          <div className="pra-muted pra-ai__test">
            {test.source === "llm" ? `Answered by ${test.deployment}` : "Template used"}
            {test.errors.length > 0 && <div>{test.errors[test.errors.length - 1]}</div>}
          </div>
        )}
      </td>
    </tr>
  );
}
