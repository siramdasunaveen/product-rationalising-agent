import { useState } from "react";
import { ApiError } from "../api/client";
import { useApprovalAction, useApprovalQueue, useDecision, type QueueRow } from "../api/decisions";
import { usePermissions } from "../api/identity";

const title = (s: string | null | undefined) => (s ? s.charAt(0) + s.slice(1).toLowerCase().replace(/_/g, " ") : "—");
const errText = (e: unknown) => (e instanceof ApiError ? e.message : "Something went wrong.");
const TONE: Record<string, string> = { DRAFT: "muted", SUBMITTED: "info", ON_HOLD: "warning", RETURNED: "warning",
  APPROVED: "success", REJECTED: "danger", SUPERSEDED: "muted", PASS: "success", WARNING: "warning", BLOCKED: "danger",
  PASS_WITH_WARNING: "warning", PENDING: "info", CANCELLED: "muted" };

/** Step 14 (screen 10, queue): decisions awaiting my approval scope. */
export function ApprovalsScreen({ onOpen }: { onOpen: (runId: string, code: string) => void }) {
  const { has } = usePermissions();
  const q = useApprovalQueue(!has("approval.act"));
  return (
    <div className="pra-page">
      <div className="pra-page__head">
        <div>
          <h1 className="pra-page__title">Decision Approvals</h1>
          <p className="pra-page__subtitle">{has("approval.act") ? "Decisions waiting for your approval scope." : "Decisions in approval."}</p>
        </div>
      </div>
      {q.error && <div className="pra-alert" role="alert">{errText(q.error)}</div>}
      <div className="pra-table" role="table">
        <div className="pra-table__head pra-approvals__row" role="row">
          <span>Decision</span><span>Candidate</span><span>Disposition</span><span>Effective</span><span>Pending scopes</span><span>Due</span>
        </div>
        {q.data && q.data.length === 0 && <div className="pra-table__empty">Nothing is waiting for you.</div>}
        {(q.data ?? []).map((r: QueueRow) => (
          <button key={r.code} type="button" role="row" className="pra-table__row pra-table__row--link pra-approvals__row"
            onClick={() => onOpen(r.runId, r.code)}>
            <strong>{r.code} <span className="pra-muted">· {r.runCode}</span></strong>
            <span>{r.candidateCode}</span>
            <span>{title(r.disposition)}{r.targetProductId && <span className="pra-muted"> → {r.targetProductId}</span>}</span>
            <span>{r.effectiveDate}</span>
            <span>{r.pendingScopes.join(", ")} <span className={`pra-pill pra-pill--${TONE[r.state]}`}>{title(r.state)}</span></span>
            <span>{r.dueDate ?? "—"}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

type Props = { runId: string; code: string; onBack: () => void; onOpenCandidate: (candidateCode: string) => void };

/** Step 14 (screen 10): the exact decision version, re-evaluated policy checks and the approval actions. */
export function ApprovalScreen({ runId, code, onBack, onOpenCandidate }: Props) {
  const q = useDecision(runId, code);
  const act = useApprovalAction(runId, code);
  const [comment, setComment] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const d = q.data;
  if (q.isLoading) return <div className="pra-page"><p className="pra-muted">Loading…</p></div>;
  if (!d || !d.current) return <div className="pra-page"><div className="pra-alert">{d ? "This decision has no version yet." : errText(q.error)}</div></div>;
  const v = d.current;
  const imp = (v.impact ?? {}) as { activeSubscriptions?: number; mrr?: { amount: number; currency: string } };
  const run = async (action: string) => {
    setError(null); setNote(null);
    try {
      const r = await act.mutateAsync({ action, comment: comment.trim() || undefined, contentHash: v.contentHash });
      setComment(""); setNote(`${title(action)} recorded · decision is now ${title(r.state)}.`);
    } catch (e) { setError(errText(e)); }
  };
  const warning = d.policyChecks.find((c) => c.status === "WARNING");

  return (
    <div className="pra-page">
      <div className="pra-page__head">
        <div>
          <h1 className="pra-page__title">Approval · {(v.products ?? []).map((p) => p.name).join(" → ") || d.candidateCode}</h1>
          <p className="pra-page__subtitle">{d.code} · {d.myScopes.length ? `${d.myScopes.map(title).join(", ")} approval` : "Read only"}
            {d.tasks.some((t) => t.state === "PENDING" && t.dueDate) && <> · due {d.tasks.find((t) => t.state === "PENDING")?.dueDate}</>}
            {" "}· <span className={`pra-pill pra-pill--${TONE[d.state]}`}>{title(d.state)}</span></p>
        </div>
        <div className="pra-ready__actions">
          <button type="button" className="pra-button pra-button--ghost" onClick={() => onOpenCandidate(d.candidateCode)}>Candidate</button>
          <button type="button" className="pra-button pra-button--ghost" onClick={onBack}>Approvals</button>
        </div>
      </div>
      {error && <div className="pra-alert" role="alert">{error}</div>}
      {note && <div className="pra-notice" role="status">{note}</div>}
      {d.stale && <div className="pra-alert" role="alert">The candidate was reassessed after this version; a new decision version is needed.</div>}

      <section className="pra-card pra-approval__band">
        <strong>Decision version {v.versionNo}</strong>
        <span>{imp.activeSubscriptions !== undefined ? `${imp.activeSubscriptions.toLocaleString("en-GB")} subscriptions` : "Impact not assessed"}
          {imp.mrr && <> · {imp.mrr.currency} {imp.mrr.amount.toLocaleString("en-GB")} MRR</>}</span>
        <span className={`pra-pill pra-pill--${TONE[d.overall ?? "PASS"]}`}>{title(d.overall)}</span>
        <span className="pra-muted">{v.contentHash}</span>
      </section>

      <div className="pra-er__grid">
        <section className="pra-card" aria-label="Decision summary">
          <h2 className="pra-overview__h2">Decision summary</h2>
          <dl className="pra-er__dl">
            <dt>Disposition</dt><dd><strong>{v.disposition}</strong>{v.recommendedAction && v.recommendedAction !== v.disposition && <span className="pra-muted"> (recommended {v.recommendedAction})</span>}</dd>
            <dt>Target</dt><dd>{v.targetName ?? v.targetProductId ?? "—"}</dd>
            <dt>Effective date</dt><dd>{v.effectiveDate}</dd>
            <dt>Owner</dt><dd>{v.ownerId}</dd>
            <dt>Snapshot</dt><dd>{v.snapshotCode}</dd>
            {v.commercialTreatment && <><dt>Commercial treatment</dt><dd><strong>{v.commercialTreatment}</strong></dd></>}
            {warning && <><dt>Open warning</dt><dd className="pra-approval__warn">{warning.detail}</dd></>}
            <dt>Rationale</dt><dd>{v.rationale}</dd>
            <dt>Route</dt><dd>{d.tasks.map((t) => `${title(t.scope)}: ${title(t.state)}${t.actedBy ? ` (${t.actedBy})` : ""}`).join(" · ") || "Not submitted"}</dd>
          </dl>
        </section>
        <section className="pra-card" aria-label="Policy checks">
          <h2 className="pra-overview__h2">Policy checks</h2>
          <ul className="pra-review__policies">
            {d.policyChecks.map((c) => (
              <li key={c.key} className="pra-approval__check"><span>{c.label}<br /><span className="pra-muted">{c.detail}</span></span>
                <span className={`pra-pill pra-pill--${TONE[c.status]}`}>{c.status}</span></li>
            ))}
          </ul>
        </section>
      </div>

      {d.canAct && (
        <section className="pra-card" aria-label="Approval actions">
          <p className="pra-muted">Comment required for Reject, Return or Hold. You act on version {v.versionNo} ({v.contentHash.slice(0, 19)}…).</p>
          <textarea className="pra-input" rows={2} maxLength={2000} value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Comment" />
          <div className="pra-actions pra-approval__buttons">
            <button type="button" className="pra-button pra-button--danger" disabled={act.isPending || !comment.trim()} onClick={() => run("REJECT")}>Reject</button>
            <button type="button" className="pra-button pra-button--ghost" disabled={act.isPending || !comment.trim()} onClick={() => run("RETURN")}>Return</button>
            <button type="button" className="pra-button pra-button--ghost" disabled={act.isPending || !comment.trim()} onClick={() => run("HOLD")}>Hold</button>
            <button type="button" className="pra-button pra-button--primary" disabled={act.isPending || d.overall === "BLOCKED"} onClick={() => run("APPROVE")}>Approve</button>
          </div>
        </section>
      )}

      <div className="pra-er__grid">
        <section className="pra-card">
          <h2 className="pra-overview__h2">Next action</h2>
          <ol className="pra-review__policies"><li>Review the decision version and policy results.</li><li>Approve, return, hold or reject with rationale.</li></ol>
        </section>
        <section className="pra-card">
          <h2 className="pra-overview__h2">Agent outcome</h2>
          <ul className="pra-review__policies">
            <li>✓ The action is immutably recorded (actor, role, time, auth context, version, hash, policy result).</li>
            <li>✓ Approval completes after all gates pass; it enables a transition package and changes no product.</li>
          </ul>
          <h3 className="pra-muted">History</h3>
          <ul className="pra-review__policies">
            {d.actions.map((a) => (
              <li key={a.id}><strong>{title(a.action)}</strong>{a.scope && <> · {title(a.scope)}</>} <span className="pra-muted">{new Date(a.at).toLocaleString("en-GB")} · {a.actorId}{a.comment ? ` · ${a.comment}` : ""} → {title(a.resultingState)}</span></li>
            ))}
          </ul>
        </section>
      </div>
    </div>
  );
}
