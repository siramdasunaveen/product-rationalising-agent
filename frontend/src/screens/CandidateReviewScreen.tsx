import { useState } from "react";
import { ApiError } from "../api/client";
import { usePermissions } from "../api/identity";
import { useDetectRequests } from "../api/evidenceRequests";
import {
  checkReadiness, useAddComment, useCandidate, useComments, useExplain, type CandidateDetail, type Readiness,
} from "../api/candidates";

type Tab = "evidence" | "impact" | "alternatives" | "policies" | "discussion";
const TABS: { key: Tab; label: string }[] = [
  { key: "evidence", label: "Evidence" }, { key: "impact", label: "Impact" }, { key: "alternatives", label: "Alternatives" },
  { key: "policies", label: "Policies" }, { key: "discussion", label: "Discussion" },
];
const title = (s: string | null | undefined) => (s ? s.charAt(0) + s.slice(1).toLowerCase().replace(/_/g, " ") : "—");
const errText = (e: unknown) => (e instanceof ApiError ? e.message : "Something went wrong.");
const TONE: Record<string, string> = { PASS: "success", FAIL: "danger", NOT_ASSESSED: "warning", FEASIBLE: "success",
  BLOCKED: "danger", WARNING: "warning", INFO: "muted", STALE: "warning", FRESH: "success", UNKNOWN: "muted",
  CONFLICTED: "danger", VALIDATED: "success", CRITICAL: "danger", HIGH: "warning", MEDIUM: "info", LOW: "muted" };
const pill = (s: string | null | undefined) => <span className={`pra-pill pra-pill--${TONE[s ?? ""] ?? "muted"}`}>{title(s)}</span>;

type Props = { runId: string; candidateRef: string; focusBlockerId?: string; onBack: () => void;
  onOpenEvidenceRequest?: (code: string) => void;
  onContinueToDecision: (candidateId: string, action: string, targetProductId: string | null) => void };

/** Step 12 (screens 08 and 12): one candidate end to end. Numbers are exact server values with the server's
 *  display strings; Continue to Decision is enabled only by the server-side readiness check (fails closed). */
export function CandidateReviewScreen({ runId, candidateRef, focusBlockerId, onBack, onOpenEvidenceRequest,
  onContinueToDecision }: Props) {
  const q = useCandidate(runId, candidateRef);
  const detect = useDetectRequests(runId);
  const { has } = usePermissions();
  const [tab, setTab] = useState<Tab>("evidence");
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const [action, setAction] = useState<string | null>(null);
  const explain = useExplain(runId, candidateRef);
  const d = q.data;
  if (q.isLoading) return <div className="pra-page"><p className="pra-muted">Loading…</p></div>;
  if (!d) return <div className="pra-page"><div className="pra-alert">{errText(q.error)}</div></div>;

  const rec = d.recommendation;
  const chosen = action ?? rec.action ?? "INVESTIGATE";
  const chosenAlt = d.alternatives.find((a) => a.action === chosen);
  const readiness: Readiness | null = chosen === rec.action ? d.decisionReadiness : null;
  const m = d.metrics.display;
  const cards = [
    { label: "Similarity", value: m.similarity ?? "—", hint: "Detector similarity, not the score" },
    { label: "Opportunity Score", value: m.opportunityScore ?? "—", hint: rec.guardrail ? title(rec.guardrail) : "" },
    { label: "Active subscriptions", value: m.subscribers ?? "Unknown", hint: m.subscribersExact ?? "" },
    { label: "MRR", value: m.mrr ?? "Unknown", hint: "Monthly recurring revenue of the changed product" },
    { label: "Billing systems", value: m.billingSystems ?? "Unknown", hint: "Distinct billing dependencies" },
    { label: "Priority", value: title(d.priority.effective), hint: d.priority.index !== null ? `Index ${d.priority.index}` : "Not assessed" },
  ];

  const continueClick = async () => {
    setError(null); setChecking(true);
    try {
      const r = await checkReadiness(runId, d.candidateId, { action: chosen, targetProductId: chosenAlt?.targetId ?? null,
        analysisId: d.analysisId });
      if (r.continueToDecisionEnabled) onContinueToDecision(d.candidateId, chosen, chosenAlt?.targetId ?? null);
      else setError(`Not ready for decision: ${r.gates.filter((g) => g.status !== "PASS").map((g) => g.reason).join("; ")}`);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) { setError(e.message); q.refetch(); } else setError(errText(e));
    } finally { setChecking(false); }
  };

  return (
    <div className="pra-page">
      <div className="pra-page__head">
        <div>
          <h1 className="pra-page__title">{d.title}</h1>
          <p className="pra-page__subtitle">
            {d.primaryTypeLabel}{d.supportingTypes.length > 0 && <> + {d.supportingTypes.map(title).join(", ")}</>} · {d.runCode} ·{" "}
            {d.snapshot.code} · {pill(d.status)}
          </p>
        </div>
        <div className="pra-ready__actions">
          <button type="button" className="pra-button pra-button--ghost" onClick={onBack}>Candidates</button>
        </div>
      </div>
      {error && <div className="pra-alert" role="alert">{error}</div>}
      {rec.preliminary && <div className="pra-notice" role="status">Preliminary recommendation: evidence is missing. Only
        Investigate can be submitted until it is provided.</div>}
      {(d.evidenceRequests.length > 0 || d.blockers.some((b) => b.category === "EVIDENCE" && b.state !== "RESOLVED")) && (
        <section className="pra-card" aria-label="Evidence needs">
          <h2 className="pra-overview__h2">Evidence needs <span className="pra-muted">· {d.openEvidenceNeeds} open</span></h2>
          <ul className="pra-review__policies">
            {d.evidenceRequests.map((r) => (
              <li key={r.code}>
                <a href="#request" onClick={(e) => { e.preventDefault(); onOpenEvidenceRequest?.(r.code); }}><strong>{r.code}</strong></a>
                {" "}{title(r.domain)} · {r.decisionImpact} <span className="pra-muted">· {title(r.status)}{r.dueDate ? ` · due ${r.dueDate}` : ""}</span>
              </li>
            ))}
          </ul>
          {has("decision.submit") && (
            <button type="button" className="pra-button pra-button--ghost" disabled={detect.isPending}
              onClick={async () => { setError(null); try { await detect.mutateAsync(d.candidateId); } catch (e) { setError(errText(e)); } }}>
              Request evidence</button>
          )}
        </section>
      )}

      <section className="pra-kpis pra-review__cards" aria-label="Key metrics">
        {cards.map((c) => (
          <div key={c.label} className="pra-kpi" title={c.hint}>
            <div className="pra-kpi__value">{c.value}</div>
            <div className="pra-kpi__label">{c.label}</div>
          </div>
        ))}
      </section>

      <section className="pra-card pra-review__rec" aria-label="Recommendation">
        <h2 className="pra-overview__h2">Recommendation: {rec.action ? title(rec.action) : "none (below review threshold)"}
          {rec.targetName && <span className="pra-muted"> → {rec.targetName}</span>}</h2>
        {rec.text && <p>{rec.text}</p>}
        {rec.conditions.length > 0 && <p className="pra-muted">Conditions: {rec.conditions.map(title).join("; ")}</p>}
        <div className="pra-review__dims">
          {Object.entries(d.dimensions).map(([k, v]) => (
            <span key={k} className="pra-review__dim">{title(k)}: {v.score ?? "—"} {pill(v.status)} <span className="pra-muted">{v.coverage}% coverage</span></span>
          ))}
        </div>
        <div className="pra-review__explain">
          {rec.explanation ? (
            <div>
              <div className="pra-muted">
                {rec.explanation.source === "llm" ? `AI explanation (${rec.explanation.model ?? "model"})` : "Explanation (deterministic template)"}
                {rec.aiConfidence !== null && <> · AI confidence {Math.round(rec.aiConfidence * 100)}%{rec.explanation.belowThreshold && " (below threshold, review)"}</>}
              </div>
              <ol className="pra-review__sentences">
                {rec.explanation.sentences.map((s, i) => (
                  <li key={i}>{s.text} <span className="pra-muted">[{s.evidenceIds.join(", ")}]</span></li>
                ))}
              </ol>
            </div>
          ) : <p className="pra-muted">No explanation generated yet.</p>}
          <button type="button" className="pra-button pra-button--ghost" disabled={explain.isPending}
            onClick={async () => { setError(null); try { await explain.mutateAsync(); } catch (e) { setError(errText(e)); } }}>
            {rec.explanation ? "Regenerate explanation" : "Explain this recommendation"}</button>
        </div>
      </section>

      {d.blockers.length > 0 && (
        <section className="pra-card" aria-label="Blockers">
          <h2 className="pra-overview__h2">Blockers</h2>
          <ul className="pra-overview__attention">
            {d.blockers.map((b) => (
              <li key={b.id} className={b.id === focusBlockerId ? "pra-review__focus" : undefined}>
                <span className={`pra-overview__sev pra-overview__sev--${TONE[b.severity] ?? "muted"}`}>{b.severity}</span>{" "}
                <strong>{b.code}</strong> {b.message} <span className="pra-muted">· {title(b.category)} · {title(b.state)}
                  {b.consequence === "BLOCKED" ? " · blocking" : " · warning"}{b.resolution ? ` · ${b.resolution}` : ""}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <div className="pra-tabs" role="tablist">
        {TABS.map((t) => (
          <button key={t.key} type="button" role="tab" aria-selected={tab === t.key}
            className={`pra-chip${tab === t.key ? " pra-chip--on" : ""}`} onClick={() => setTab(t.key)}>{t.label}</button>
        ))}
      </div>
      {tab === "evidence" && <EvidenceTab d={d} />}
      {tab === "impact" && <ImpactTab d={d} />}
      {tab === "alternatives" && (
        <div className="pra-table" role="table">
          <div className="pra-table__head pra-review__alt" role="row">
            <span>Action</span><span>Target</span><span>Feasibility</span><span>Expected impact</span><span>Blocking reasons</span>
          </div>
          {d.alternatives.map((a) => (
            <button key={a.action} type="button" role="row" aria-pressed={chosen === a.action}
              className={`pra-table__row pra-table__row--link pra-review__alt${chosen === a.action ? " pra-review__alt--on" : ""}`}
              onClick={() => setAction(a.action)}>
              <strong>{title(a.action)}{a.recommended && <span className="pra-muted"> (recommended)</span>}</strong>
              <span>{a.targetName ?? "—"}</span>
              <span>{pill(a.feasibilityStatus)}</span>
              <span>{a.expectedImpact}</span>
              <span className="pra-muted">{a.blockingReasons.join("; ") || "—"}</span>
            </button>
          ))}
        </div>
      )}
      {tab === "policies" && (
        <ul className="pra-review__policies">
          {d.policies.map((p) => (
            <li key={p.key}>{pill(p.status)} <strong>{p.label}</strong>: {p.value}{p.source && <span className="pra-muted"> · {p.source}</span>}</li>
          ))}
        </ul>
      )}
      {tab === "discussion" && <Discussion runId={runId} candidateRef={d.candidateId} canPost={has("candidate.view")} />}

      <section className="pra-card" aria-label="Decision readiness">
        <h2 className="pra-overview__h2">Decision readiness for {title(chosen)}{chosenAlt?.targetName && <> → {chosenAlt.targetName}</>}</h2>
        {readiness ? (
          <ul className="pra-review__gates">
            {readiness.gates.map((g) => <li key={g.key}>{pill(g.status)} <strong>{title(g.key)}</strong> <span className="pra-muted">{g.reason}</span></li>)}
          </ul>
        ) : (
          <p className="pra-muted">{chosenAlt ? chosenAlt.blockingReasons.join("; ") || "All gates pass for this alternative." : "Choose an action under Alternatives."}
            {" "}The server re-checks every gate when you continue.</p>
        )}
        <div className="pra-actions">
          {!has("decision.submit") && <span className="pra-muted">You can review, but not submit decisions.</span>}
          <button type="button" className="pra-button pra-button--primary"
            disabled={checking || !has("decision.submit") || (readiness ? !readiness.continueToDecisionEnabled
              : chosenAlt ? chosenAlt.feasibilityStatus !== "FEASIBLE" && chosen !== "INVESTIGATE" : true)}
            onClick={continueClick}>Continue to Decision</button>
        </div>
      </section>
    </div>
  );
}

function EvidenceTab({ d }: { d: CandidateDetail }) {
  return (
    <div className="pra-table" role="table">
      <div className="pra-table__head pra-review__ev" role="row">
        <span>Fact</span><span>Value</span><span>Source domain</span><span>Snapshot</span><span>Observed</span><span>Freshness</span><span>Validation</span>
      </div>
      {d.evidence.map((r) => (
        <div key={r.key} role="row" className="pra-table__row pra-review__ev">
          <strong>{r.label}</strong>
          <span className={r.assessed ? undefined : "pra-muted"} title={r.evidenceIds.join(", ")}>{r.value}</span>
          <span>{r.sourceDomain}</span><span>{r.snapshotId}</span>
          <span>{r.observedAt ?? "—"}</span><span>{pill(r.freshness)}</span><span>{pill(r.validation)}</span>
        </div>
      ))}
    </div>
  );
}

function ImpactTab({ d }: { d: CandidateDetail }) {
  const rows = Object.entries(d.impact).filter(([k, v]) => k !== "evidenceIds" && v !== null && typeof v !== "object");
  return (
    <ul className="pra-review__policies">
      {rows.length === 0 && <li className="pra-muted">Impact not assessed.</li>}
      {rows.map(([k, v]) => <li key={k}><strong>{title(k.replace(/([A-Z])/g, "_$1"))}</strong>: {String(v)}</li>)}
      {d.metrics.mrr !== null && <li><strong>MRR</strong>: {d.metrics.display.mrr}</li>}
    </ul>
  );
}

function Discussion({ runId, candidateRef, canPost }: { runId: string; candidateRef: string; canPost: boolean }) {
  const q = useComments(runId, candidateRef);
  const add = useAddComment(runId, candidateRef);
  const [body, setBody] = useState("");
  const [error, setError] = useState<string | null>(null);
  return (
    <div className="pra-card">
      <p className="pra-muted">Discussion is collaboration only; it is never evidence and never changes a score.</p>
      {error && <div className="pra-alert" role="alert">{error}</div>}
      <ul className="pra-review__comments">
        {(q.data ?? []).map((c) => (
          <li key={c.id}><strong>{c.authorId}</strong> <span className="pra-muted">{new Date(c.createdAt).toLocaleString("en-GB")}</span><br />{c.body}</li>
        ))}
        {q.data && q.data.length === 0 && <li className="pra-muted">No comments yet.</li>}
      </ul>
      {canPost && (
        <form className="pra-review__comment" onSubmit={async (e) => {
          e.preventDefault(); setError(null);
          try { await add.mutateAsync(body); setBody(""); } catch (err) { setError(errText(err)); }
        }}>
          <textarea className="pra-input" rows={3} maxLength={2000} value={body} onChange={(e) => setBody(e.target.value)}
            placeholder="Add a comment" />
          <button type="submit" className="pra-button pra-button--ghost" disabled={!body.trim() || add.isPending}>Post</button>
        </form>
      )}
    </div>
  );
}
