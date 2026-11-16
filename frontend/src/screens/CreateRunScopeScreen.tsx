import { useEffect, useMemo, useState } from "react";
import { ApiError } from "../api/client";
import { useMe, usePermissions } from "../api/identity";
import { useMarkets, useObjectives, useOwners, usePortfolios, useProductFamilies } from "../api/metadata";
import { type Run, type RunScope, useRun, useSaveRunScope } from "../api/runs";
import { FamilyTree } from "../components/FamilyTree";
import { GuidanceCards } from "../components/GuidanceCards";
import { MultiSelect } from "../components/MultiSelect";
import { WizardSteps } from "../components/WizardSteps";
import { last12Months, normaliseName, validateScope } from "../domain/scope";

const EMPTY: RunScope = {
  name: "",
  portfolio_id: null,
  market_ids: [],
  product_family_ids: [],
  period_start: null,
  period_end: null,
  objectives: [],
  owner_id: null,
};

function fromRun(run: Run): RunScope {
  return {
    name: run.name,
    portfolio_id: run.portfolio.id,
    market_ids: run.markets.map((m) => m.id),
    product_family_ids: run.product_families.map((f) => f.id),
    period_start: run.period_start,
    period_end: run.period_end,
    objectives: run.objectives,
    owner_id: run.owner.user_id,
  };
}

function changedFields(a: RunScope, b: RunScope): Partial<RunScope> {
  const out: Partial<RunScope> = {};
  (Object.keys(a) as (keyof RunScope)[]).forEach((k) => {
    if (JSON.stringify(a[k]) !== JSON.stringify(b[k])) (out as Record<string, unknown>)[k] = a[k];
  });
  return out;
}

/** Create Rationalisation Run — step 1 of 5: Define scope and business intent (screen 02). */
export function CreateRunScopeScreen({
  runId,
  onContinue,
  onCancel,
}: {
  runId?: string;
  onContinue: (run: Run) => void;
  onCancel: () => void;
}) {
  const { has } = usePermissions();
  const { data: me } = useMe();
  const existing = useRun(runId);
  const portfolios = usePortfolios();
  const markets = useMarkets();
  const objectives = useObjectives();
  const owners = useOwners();
  const save = useSaveRunScope();

  const [form, setForm] = useState<RunScope | null>(null);
  const [touched, setTouched] = useState<Record<string, boolean>>({});
  const [serverErrors, setServerErrors] = useState<Record<string, string>>({});
  const [banner, setBanner] = useState<string | null>(null);

  // Initialise once: from the run when reopening, otherwise with spec defaults.
  useEffect(() => {
    if (form) return;
    if (runId) {
      if (existing.data) setForm(fromRun(existing.data));
      return;
    }
    if (!me || !owners.data) return;
    const { start, end } = last12Months();
    const iAmOwner = owners.data.some((p) => p.user_id === me.user_id);
    setForm({ ...EMPTY, period_start: start, period_end: end, owner_id: iAmOwner ? me.user_id : null });
  }, [form, runId, existing.data, me, owners.data]);

  const families = useProductFamilies(form?.portfolio_id ?? null);
  const run = existing.data;
  const readOnly = !has("run.create") || (run ? !run.scope_editable : false);
  const clientErrors = useMemo(() => (form ? validateScope(form) : {}), [form]);
  const errors = { ...clientErrors, ...serverErrors };
  const valid = Object.keys(clientErrors).length === 0;

  if (runId && existing.isError) {
    return <div className="pra-page"><div className="pra-card pra-empty"><strong>Run not found</strong></div></div>;
  }
  if (!form) return <div className="pra-page"><p className="pra-muted">Loading…</p></div>;

  const set = <K extends keyof RunScope>(key: K, value: RunScope[K]) => {
    setForm((f) => (f ? { ...f, [key]: value } : f));
    setTouched((t) => ({ ...t, [key]: true }));
    setServerErrors(({ [key]: _, ...rest }) => rest);
    setBanner(null);
  };
  const show = (key: string) => (touched[key] || save.isError) && errors[key];

  const changePortfolio = (id: string) => {
    // Families belong to a portfolio: clear them when the portfolio changes.
    setForm((f) => (f ? { ...f, portfolio_id: id || null, product_family_ids: [] } : f));
    setTouched((t) => ({ ...t, portfolio_id: true }));
  };

  const submit = async () => {
    setTouched(Object.fromEntries(Object.keys(form).map((k) => [k, true])));
    if (!valid || readOnly) {
      if (readOnly && run) onContinue(run);
      return;
    }
    const scope = { ...form, name: normaliseName(form.name) };
    try {
      const saved = run
        ? Object.keys(changedFields(scope, fromRun(run))).length === 0
          ? run
          : await save.mutateAsync({ id: run.id, version: run.version, scope: changedFields(scope, fromRun(run)) })
        : await save.mutateAsync({ scope });
      onContinue(saved);
    } catch (e) {
      if (e instanceof ApiError) {
        setServerErrors(e.byField());
        setBanner(e.message);
      } else setBanner("Could not save the run. Try again.");
    }
  };

  const ownerOptions = owners.data ?? [];

  return (
    <div className="pra-page">
      <div className="pra-page__head">
        <div>
          <h1 className="pra-page__title">Create Rationalisation Run</h1>
          <p className="pra-page__subtitle">
            Step 1 of 5 · Define scope and business intent
            {run && <> · <strong>{run.run_code}</strong> · {run.stage_label}</>}
          </p>
        </div>
        <button type="button" className="pra-button pra-button--ghost" onClick={onCancel}>Back to assessments</button>
      </div>
      <div className="pra-progress" aria-hidden="true"><span className="pra-progress__fill" style={{ width: "20%" }} /></div>

      {/* Step 2 behaves like the Continue button: saves edits (if allowed) and opens the Evidence Hub. */}
      <WizardSteps current={1} available={[1, 2]} onSelect={(step) => { if (step === 2) void submit(); }} />

      {readOnly && (
        <div className="pra-notice" role="status">
          {has("run.create")
            ? "Scope is locked because analysis has started."
            : "You can view this scope but not change it. Use “2 Evidence Hub” above (or Continue below) to work on evidence."}
        </div>
      )}
      {banner && <div className="pra-alert" role="alert">{banner}</div>}

      <form
        className="pra-form"
        noValidate
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <div className="pra-form__grid">
          <Field id="name" label="Run name" error={show("name")}>
            <input id="name" className={inputCls(show("name"))} value={form.name} disabled={readOnly}
              maxLength={200} placeholder="e.g. Consumer Mobile Rationalisation Q4"
              onChange={(e) => set("name", e.target.value)} />
          </Field>
          <Field id="portfolio_id" label="Portfolio" error={show("portfolio_id")}>
            <select id="portfolio_id" className={inputCls(show("portfolio_id"))} value={form.portfolio_id ?? ""}
              disabled={readOnly} onChange={(e) => changePortfolio(e.target.value)}>
              <option value="">Select portfolio…</option>
              {(portfolios.data ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </Field>
          <Field id="market_ids" label="Market / operating company" error={show("market_ids")}>
            <MultiSelect id="market_ids" disabled={readOnly} max={10} invalid={!!show("market_ids")}
              options={(markets.data ?? []).map((m) => ({ id: m.id, label: m.name }))}
              value={form.market_ids} onChange={(v) => set("market_ids", v)} placeholder="Select markets…" />
          </Field>

          <Field id="product_family_ids" label="Product families" error={show("product_family_ids")} wide>
            <FamilyTree families={families.data ?? []} value={form.product_family_ids} disabled={readOnly}
              invalid={!!show("product_family_ids")} onChange={(v) => set("product_family_ids", v)} />
          </Field>
          <Field id="period_start" label="Assessment period" error={show("period_start") || show("period_end")}>
            <div className="pra-period">
              <input id="period_start" type="date" aria-label="Period from" className={inputCls(show("period_start"))}
                value={form.period_start ?? ""} disabled={readOnly} onChange={(e) => set("period_start", e.target.value || null)} />
              <span aria-hidden="true">to</span>
              <input id="period_end" type="date" aria-label="Period to" className={inputCls(show("period_end"))}
                value={form.period_end ?? ""} disabled={readOnly} onChange={(e) => set("period_end", e.target.value || null)} />
            </div>
            {!readOnly && (
              <button type="button" className="pra-link" onClick={() => {
                const { start, end } = last12Months();
                set("period_start", start);
                set("period_end", end);
              }}>Last 12 months</button>
            )}
          </Field>
          <Field id="owner_id" label="Accountable owner" error={show("owner_id")}>
            <select id="owner_id" className={inputCls(show("owner_id"))} value={form.owner_id ?? ""} disabled={readOnly}
              onChange={(e) => set("owner_id", e.target.value || null)}>
              <option value="">Select owner…</option>
              {ownerOptions.map((p) => <option key={p.user_id} value={p.user_id}>{p.name}</option>)}
              {form.owner_id && !ownerOptions.some((p) => p.user_id === form.owner_id) && run && (
                <option value={form.owner_id}>{run.owner.name}</option>
              )}
            </select>
          </Field>
        </div>

        <fieldset className="pra-objectives">
          <legend>Business objectives <span className="pra-req">*</span></legend>
          <div className="pra-objectives__grid">
            {(objectives.data ?? []).map((o) => {
              const on = form.objectives.includes(o.key);
              return (
                <button key={o.key} type="button" aria-pressed={on} disabled={readOnly}
                  className={`pra-chip${on ? " pra-chip--on" : ""}`}
                  onClick={() => set("objectives", on ? form.objectives.filter((x) => x !== o.key) : [...form.objectives, o.key])}>
                  <span aria-hidden="true">{on ? "✓" : "☐"}</span> {o.label}
                </button>
              );
            })}
          </div>
          {show("objectives") && <p className="pra-field__error">{errors.objectives}</p>}
        </fieldset>

        <div className="pra-form__actions">
          <button type="submit" className="pra-button pra-button--primary pra-button--lg"
            disabled={(!valid && !readOnly) || save.isPending}
            title={!valid && !readOnly ? "Complete all required fields first" : undefined}>
            {save.isPending ? "Saving…" : "Continue to Evidence Hub"}
          </button>
        </div>
      </form>

      <GuidanceCards
        next={["Confirm portfolio, market, period and owner.", "Select the measurable business objectives."]}
        outcome={["Scope and ownership are validated.", "Required evidence domains are proposed."]}
      />
    </div>
  );
}

const inputCls = (err: unknown) => `pra-input${err ? " pra-input--invalid" : ""}`;

function Field({ id, label, error, wide, children }: {
  id: string; label: string; error?: string | false; wide?: boolean; children: React.ReactNode;
}) {
  return (
    <div className={`pra-field${wide ? " pra-field--wide" : ""}`}>
      <label htmlFor={id} className="pra-field__label">{label} <span className="pra-req">*</span></label>
      {children}
      {error && <p className="pra-field__error" id={`${id}-error`}>{error}</p>}
    </div>
  );
}
