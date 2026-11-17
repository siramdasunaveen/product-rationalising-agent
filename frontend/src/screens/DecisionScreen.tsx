import { useState } from "react";
import { useCandidate } from "../api/candidates";
import { ApiError } from "../api/client";
import { useCandidateDecision, useSaveDecision, useSubmitDecision, type DecisionForm } from "../api/decisions";
import { usePermissions } from "../api/identity";
import { useMe } from "../api/identity";

const DISPOSITIONS = ["RETAIN", "SIMPLIFY", "CONSOLIDATE", "STANDARDISE", "MIGRATE", "RETIRE", "INVESTIGATE"];
const TARGET_ACTIONS = new Set(["CONSOLIDATE", "STANDARDISE", "MIGRATE"]);
const title = (s: string | null | undefined) => (s ? s.charAt(0) + s.slice(1).toLowerCase().replace(/_/g, " ") : "—");
const errText = (e: unknown) => (e instanceof ApiError ? e.message : "Something went wrong.");
const TONE: Record<string, string> = { DRAFT: "muted", SUBMITTED: "info", ON_HOLD: "warning", RETURNED: "warning",
  APPROVED: "success", REJECTED: "danger", SUPERSEDED: "muted", PASS: "success", PASS_WITH_WARNING: "warning", BLOCKED: "danger" };

type Props = { runId: string; candidateCode: string; initialAction?: string; initialTarget?: string;
  onBack: () => void; onOpenApproval: (code: string) => void };

/** Step 14 (screen 09): confirm disposition, target, date, owner and rationale; every save is an immutable
 *  version; submission is gated server-side (rules 5 and 6) and creates the approval route. */
export function DecisionScreen({ runId, candidateCode, initialAction, initialTarget, onBack, onOpenApproval }: Props) {
  const cand = useCandidate(runId, candidateCode);
  const dec = useCandidateDecision(runId, candidateCode);
  const me = useMe();
  const { has } = usePermissions();
  const save = useSaveDecision(runId, candidateCode);
  const submit = useSubmitDecision(runId, candidateCode);
  const [form, setForm] = useState<Partial<DecisionForm> | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [blockers, setBlockers] = useState<string[]>([]);
  const [note, setNote] = useState<string | null>(null);
  const d = cand.data;
  if (cand.isLoading || dec.isLoading) return <div className="pra-page"><p className="pra-muted">Loading…</p></div>;
  if (!d) return <div className="pra-page"><div className="pra-alert">{errText(cand.error)}</div></div>;

  const decision = dec.data ?? null;
  const current = decision?.current ?? null;
  const rec = d.recommendation;
  const base: DecisionForm = {
    disposition: current?.disposition ?? initialAction ?? rec.action ?? "INVESTIGATE",
    targetProductId: current?.targetProductId ?? initialTarget ?? rec.targetProductId ?? null,
    effectiveDate: current?.effectiveDate ?? "", ownerId: current?.ownerId ?? me.data?.user_id ?? "",
    rationale: current?.rationale ?? "", commercialTreatment: current?.commercialTreatment ?? null,
  };
  const f: DecisionForm = { ...base, ...(form ?? {}) };
  const locked = !!decision && ["SUBMITTED", "ON_HOLD", "APPROVED"].includes(decision.state);
  const canEdit = has("decision.submit") && !locked;
  const targets = d.alternatives.filter((a) => a.targetId).map((a) => ({ id: a.targetId!, name: a.targetName ?? a.targetId! }));
  const readiness = d.decisionReadiness;
  const set = (k: keyof DecisionForm, v: string | null) => {
    setForm({ ...(form ?? {}), [k]: v });
    if (k === "disposition" && !TARGET_ACTIONS.has(v ?? "")) setForm({ ...(form ?? {}), disposition: v ?? "", targetProductId: null });
  };
  const doSave = async () => {
    setError(null); setFieldErrors({}); setBlockers([]); setNote(null);
    try { const r = await save.mutateAsync(f); setForm(null); setNote(`Saved as version ${r.currentVersionNo} (${r.current?.contentHash.slice(0, 19)}…).`); return r; }
    catch (e) { if (e instanceof ApiError) setFieldErrors(e.byField()); setError(errText(e)); return null; }
  };
  const doSubmit = async () => {
    const saved = form ? await doSave() : decision;
    if (!saved?.current) return;
    setError(null); setBlockers([]);
    try {
      const r = await submit.mutateAsync({ versionId: saved.current.id, contentHash: saved.current.contentHash });
      setNote(`Submitted version ${r.currentVersionNo}; approvers: ${r.tasks.map((t) => t.scope).join(", ")}.`);
    } catch (e) {
      if (e instanceof ApiError && e.code === "submission_blocked") setBlockers(e.fieldErrors.map((x) => x.message));
      else setError(errText(e));
    }
  };

  return (
    <div className="pra-page">
      <div className="pra-page__head">
        <div>
          <h1 className="pra-page__title">Decision · {d.candidateId}</h1>
          <p className="pra-page__subtitle">Confirm the portfolio action before approval routing · {d.products.map((p) => p.name).join(" + ")}
            {decision && <> · {decision.code} v{decision.currentVersionNo} <span className={`pra-pill pra-pill--${TONE[decision.state]}`}>{title(decision.state)}</span></>}</p>
        </div>
        <div className="pra-ready__actions">
          <button type="button" className="pra-button pra-button--ghost" onClick={onBack}>Candidate</button>
          {decision && decision.state !== "DRAFT" && (
            <button type="button" className="pra-button pra-button--ghost" onClick={() => onOpenApproval(decision.code)}>Approval</button>
          )}
        </div>
      </div>
      {error && <div className="pra-alert" role="alert">{error}</div>}
      {note && <div className="pra-notice" role="status">{note}</div>}
      {blockers.length > 0 && (
        <div className="pra-alert" role="alert">Submission is blocked:<ul>{blockers.map((b) => <li key={b}>{b}</li>)}</ul></div>
      )}
      <div className="pra-notice pra-decision__status">
        <span>Evidence status: <strong>{d.status === "EVIDENCE_GAP" ? "INCOMPLETE" : "COMPLETE"}</strong></span>
        <span>Policy pre-check: <strong>{readiness ? (readiness.status === "PASS" ? "PASS" : "BLOCKED") : "—"}</strong></span>
        <span>Current recommendation: <strong>{rec.action ?? "none"}</strong>{rec.targetName && <> → {rec.targetName}</>}</span>
      </div>

      <fieldset className="pra-decision__dispositions" disabled={!canEdit}>
        <legend className="pra-overview__h2">Select disposition</legend>
        <div className="pra-decision__grid">
          {DISPOSITIONS.map((a) => {
            const allowed = d.alternatives.some((x) => x.action === a);
            return (
              <label key={a} className={`pra-decision__option${f.disposition === a ? " pra-decision__option--on" : ""}${allowed ? "" : " pra-decision__option--off"}`}>
                <input type="radio" name="disposition" value={a} checked={f.disposition === a} disabled={!allowed}
                  onChange={() => set("disposition", a)} /> {a === "STANDARDISE" ? "STANDARDIZE" : a}
              </label>
            );
          })}
        </div>
        {fieldErrors.disposition && <p className="pra-field__error">{fieldErrors.disposition}</p>}
      </fieldset>

      <div className="pra-form pra-decision__fields">
        <label>Target product {TARGET_ACTIONS.has(f.disposition) && "*"}
          <select className={`pra-input${fieldErrors.targetProductId ? " pra-input--invalid" : ""}`} value={f.targetProductId ?? ""}
            disabled={!canEdit || !TARGET_ACTIONS.has(f.disposition)} onChange={(e) => set("targetProductId", e.target.value || null)}>
            <option value="">—</option>
            {targets.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select>
          {fieldErrors.targetProductId && <span className="pra-field__error">{fieldErrors.targetProductId}</span>}
        </label>
        <label>Effective date *
          <input className={`pra-input${fieldErrors.effectiveDate ? " pra-input--invalid" : ""}`} type="date" value={f.effectiveDate}
            disabled={!canEdit} onChange={(e) => set("effectiveDate", e.target.value)} />
          {fieldErrors.effectiveDate && <span className="pra-field__error">{fieldErrors.effectiveDate}</span>}
        </label>
        <label>Decision owner *
          <input className={`pra-input${fieldErrors.ownerId ? " pra-input--invalid" : ""}`} value={f.ownerId} maxLength={128}
            disabled={!canEdit} onChange={(e) => set("ownerId", e.target.value)} />
          {fieldErrors.ownerId && <span className="pra-field__error">{fieldErrors.ownerId}</span>}
        </label>
      </div>
      <label className="pra-form pra-decision__rationale">Rationale * <span className="pra-muted">(50–4000 characters; you own the final text)</span>
        <textarea className={`pra-input${fieldErrors.rationale ? " pra-input--invalid" : ""}`} rows={4} maxLength={4000} value={f.rationale}
          disabled={!canEdit} onChange={(e) => set("rationale", e.target.value)}
          placeholder={rec.text ?? "Explain the decision with reference to the evidence."} />
        {fieldErrors.rationale && <span className="pra-field__error">{fieldErrors.rationale}</span>}
      </label>
      {(current?.conditions.length ?? 0) > 0 && (
        <p className="pra-muted">Conditions carried from the recommendation: {current!.conditions.map(title).join("; ")}
          {current?.commercialTreatment && <> · Commercial treatment: {current.commercialTreatment}</>}</p>
      )}

      <div className="pra-actions">
        {locked && <span className="pra-muted">This decision is {title(decision!.state)}; it cannot be edited.</span>}
        {!has("decision.submit") && <span className="pra-muted">You can view, but not submit decisions.</span>}
        <button type="button" className="pra-button pra-button--ghost" disabled={!canEdit || !form || save.isPending} onClick={doSave}>Save draft</button>
        <button type="button" className="pra-button pra-button--primary" disabled={!canEdit || submit.isPending || (!form && !current)}
          onClick={doSubmit}>Submit for approval</button>
      </div>

      <div className="pra-er__grid">
        <section className="pra-card">
          <h2 className="pra-overview__h2">Next action</h2>
          <ol className="pra-review__policies"><li>Confirm disposition, target, date and rationale.</li><li>Submit the decision for approval.</li></ol>
        </section>
        <section className="pra-card">
          <h2 className="pra-overview__h2">Agent outcome</h2>
          <ul className="pra-review__policies">
            <li>✓ A decision version and content hash are created{current && <> (v{current.versionNo} · {current.contentHash.slice(0, 19)}…)</>}.</li>
            <li>✓ Required approvers are calculated{decision?.tasks.length ? <>: {decision.tasks.map((t) => t.scope).join(", ")}</> : " on submission"}.</li>
          </ul>
          {decision && decision.versions.length > 1 && (
            <p className="pra-muted">Versions: {decision.versions.map((v) => `v${v.versionNo} (${v.reason ?? "edit"})`).join(" · ")}. Prior versions are read-only.</p>
          )}
        </section>
      </div>
    </div>
  );
}
