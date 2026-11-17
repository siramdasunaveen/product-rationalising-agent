import { useState } from "react";
import { ApiError } from "../api/client";
import {
  useAcceptRequest, useAssignRequest, useEvidenceRequest, useRejectRequest, useStartRequest,
} from "../api/evidenceRequests";
import { usePermissions } from "../api/identity";

const title = (s: string | null | undefined) => (s ? s.charAt(0) + s.slice(1).toLowerCase().replace(/_/g, " ") : "—");
const errText = (e: unknown) => (e instanceof ApiError ? e.message : "Something went wrong.");
const TONE: Record<string, string> = { DRAFT: "muted", OPEN: "danger", IN_PROGRESS: "warning", RECEIVED: "success",
  ACCEPTED: "info", REJECTED: "danger", SUPERSEDED: "muted" };

type Props = { runId: string; code: string; onBack: () => void; onOpenCandidate: (candidateCode: string) => void;
  onOpenEvidenceHub: () => void };

/** Step 13 (screen 15): the logical evidence contract, the fulfilment channel and the lifecycle actions. */
export function EvidenceRequestScreen({ runId, code, onBack, onOpenCandidate, onOpenEvidenceHub }: Props) {
  const q = useEvidenceRequest(runId, code);
  const { has } = usePermissions();
  const assign = useAssignRequest(runId, code);
  const start = useStartRequest(runId, code);
  const accept = useAcceptRequest(runId, code);
  const reject = useRejectRequest(runId, code);
  const [owner, setOwner] = useState<string | null>(null);
  const [due, setDue] = useState<string | null>(null);
  const [method, setMethod] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const r = q.data;
  if (q.isLoading) return <div className="pra-page"><p className="pra-muted">Loading…</p></div>;
  if (!r) return <div className="pra-page"><div className="pra-alert">{errText(q.error)}</div></div>;

  const canManage = has("decision.submit") || has("evidence.map");
  const canFulfil = has("request.fulfil") || has("evidence.upload") || has("evidence.map");
  const canAccept = has("snapshot.approve");
  const editable = ["DRAFT", "OPEN", "IN_PROGRESS", "REJECTED"].includes(r.status);
  const c = r.contract;
  const run = async (fn: () => Promise<unknown>, ok?: string) => {
    setError(null); setNote(null);
    try { await fn(); if (ok) setNote(ok); } catch (e) { setError(errText(e)); }
  };

  return (
    <div className="pra-page">
      <div className="pra-page__head">
        <div>
          <h1 className="pra-page__title">Evidence Request · {r.code}</h1>
          <p className="pra-page__subtitle">
            Potential {title(r.action)}: <a href="#candidate" onClick={(e) => { e.preventDefault(); onOpenCandidate(r.candidateCode); }}>{r.candidateCode}</a>
            {" "}· {r.domainLabel} · {r.snapshot?.code} · <span className={`pra-pill pra-pill--${TONE[r.status]}`}>{title(r.status)}</span>
            {r.reassessmentStatus === "QUEUED" && <> <span className="pra-pill pra-pill--info">Reassessing</span></>}
          </p>
        </div>
        <div className="pra-ready__actions">
          <button type="button" className="pra-button pra-button--ghost" onClick={onBack}>Evidence requests</button>
        </div>
      </div>
      {error && <div className="pra-alert" role="alert">{error}</div>}
      {note && <div className="pra-notice" role="status">{note}</div>}
      {r.statusReason && <div className="pra-notice" role="status">{r.statusReason}</div>}

      <section className={`pra-card pra-er__impact pra-er__impact--${r.consequence === "BLOCKED" ? "blocking" : "warning"}`}>
        <span className="pra-pill pra-pill--danger">{r.consequence === "BLOCKED" ? "Blocking" : "Warning"}</span>
        <strong>{r.decisionImpact}</strong>
        <span className="pra-muted">{r.evidenceNeeded} · priority {title(r.priority)} · unlocks {c.unlocks}</span>
      </section>

      <div className="pra-er__grid">
        <section className="pra-card" aria-label="Logical evidence contract">
          <h2 className="pra-overview__h2">Logical evidence contract <span className="pra-muted">v{c.version}</span></h2>
          <div className="pra-notice"><strong>Purpose</strong><br />{c.purpose}</div>
          <dl className="pra-er__dl">
            <dt>Metrics</dt><dd>{c.metrics.join(" · ")}</dd>
            <dt>Grouping</dt><dd>{c.grouping}</dd>
            <dt>Filters</dt><dd>{c.filters}</dd>
            <dt>Return</dt><dd>{c.returns}</dd>
            <dt>Freshness</dt><dd>≤ {c.freshnessDays} days</dd>
            <dt>Products</dt><dd>{c.products.join(", ")}</dd>
            <dt>Privacy</dt><dd><strong>{c.privacy}</strong></dd>
          </dl>
          <p className="pra-muted">The contract is owned by the policy; only owner, due date and fulfilment channel can be chosen.</p>
        </section>

        <section className="pra-card" aria-label="Fulfilment method">
          <h2 className="pra-overview__h2">Fulfilment method</h2>
          <p className="pra-muted">Choose the safest available route.</p>
          <div className="pra-er__methods" role="radiogroup">
            {r.fulfilmentMethods.map((fm) => {
              const on = (method ?? r.fulfilmentMethod ?? "SECURE_QUERY") === fm.key;
              return (
                <button key={fm.key} type="button" role="radio" aria-checked={on} disabled={!editable || !canManage}
                  className={`pra-er__method${on ? " pra-er__method--on" : ""}`} onClick={() => setMethod(fm.key)}>
                  <strong>{fm.label}</strong><span className="pra-muted">{fm.note}</span>
                  <span className="pra-pill pra-pill--info">{fm.recommended ? "Recommended" : "Available"}</span>
                </button>
              );
            })}
          </div>
          <div className="pra-form pra-er__assign">
            <label>Owner<input className="pra-input" value={owner ?? r.ownerId ?? ""} disabled={!editable || !canManage}
              placeholder={title(r.ownerRole)} maxLength={128} onChange={(e) => setOwner(e.target.value)} /></label>
            <label>Due date<input className="pra-input" type="date" value={due ?? r.dueDate ?? ""} disabled={!editable || !canManage}
              onChange={(e) => setDue(e.target.value)} /></label>
          </div>
          <div className="pra-actions">
            {canManage && editable && (
              <button type="button" className="pra-button pra-button--primary" disabled={assign.isPending}
                onClick={() => run(() => assign.mutateAsync({ ownerId: owner ?? r.ownerId, dueDate: due ?? r.dueDate,
                  fulfilmentMethod: method ?? r.fulfilmentMethod ?? "SECURE_QUERY" }), "Request assigned and open.")}>
                {r.status === "DRAFT" ? "Assign owner" : "Update assignment"}</button>
            )}
            {canFulfil && r.status === "OPEN" && (
              <button type="button" className="pra-button pra-button--ghost" disabled={start.isPending}
                onClick={() => run(() => start.mutateAsync(undefined), "Fulfilment started.")}>Start fulfilment</button>
            )}
          </div>
        </section>
      </div>

      {r.fulfilmentJob && (
        <section className="pra-card" aria-label="Fulfilment job">
          <h2 className="pra-overview__h2">Fulfilment · {title(String(r.fulfilmentJob.channel))}</h2>
          <p>{String(r.fulfilmentJob.instructions ?? "")}</p>
          {r.fulfilmentJob.channel === "EXCEL_AGGREGATE" && (
            <button type="button" className="pra-button pra-button--ghost" onClick={onOpenEvidenceHub}>Open the Evidence Hub</button>
          )}
        </section>
      )}

      <div className="pra-er__grid">
        <section className="pra-card" aria-label="Your next action">
          <h2 className="pra-overview__h2">Your next action</h2>
          <ol className="pra-review__policies">
            {r.status === "DRAFT" && <li>Select the fulfilment method and the customer data owner, then assign.</li>}
            {r.status === "OPEN" && <li>Start fulfilment through the chosen channel; confirm scope, privacy and due date.</li>}
            {r.status === "IN_PROGRESS" && <li>Track the return. When it is approved as a new snapshot, this request becomes Received.</li>}
            {r.status === "RECEIVED" && <li>Validate the return ({r.receivedSnapshot?.code}) and accept it, or reject it with a reason.</li>}
            {r.status === "REJECTED" && <li>Correct and resubmit the evidence; a corrected return is received again.</li>}
            {r.status === "ACCEPTED" && <li>{r.reassessmentStatus === "DONE" ? "Reassessment complete: review the candidate." : "Reassessment queued."}</li>}
            {r.status === "SUPERSEDED" && <li>Use the latest approved snapshot; this request was replaced.</li>}
          </ol>
          {canAccept && r.status === "RECEIVED" && (
            <div className="pra-er__decide">
              <button type="button" className="pra-button pra-button--primary" disabled={accept.isPending}
                onClick={() => run(() => accept.mutateAsync(undefined), "Accepted: reassessment queued on the new snapshot.")}>
                Accept and reassess</button>
              <textarea className="pra-input" rows={2} maxLength={2000} placeholder="Reason for rejection (required)" value={reason}
                onChange={(e) => setReason(e.target.value)} />
              <button type="button" className="pra-button pra-button--ghost" disabled={!reason.trim() || reject.isPending}
                onClick={() => run(() => reject.mutateAsync(reason), "Rejected. A corrected return can be received later.")}>Reject</button>
            </div>
          )}
        </section>
        <section className="pra-card" aria-label="System-generated outcome">
          <h2 className="pra-overview__h2">System-generated outcome</h2>
          <ul className="pra-review__policies">
            <li>✓ Machine-readable aggregate request created and tracked ({r.blockerCode}).</li>
            <li>✓ Accepted evidence triggers a new snapshot and a reassessment with the run's pinned versions.</li>
            {r.acceptedSnapshot && <li>✓ Accepted into {r.acceptedSnapshot.code}; reassessment {title(r.reassessmentStatus)}.</li>}
          </ul>
          <h3 className="pra-muted">History</h3>
          <ul className="pra-review__policies">
            {(r.history ?? []).map((h, i) => (
              <li key={i}><strong>{title(h.to)}</strong> <span className="pra-muted">{new Date(h.at).toLocaleString("en-GB")} · {h.actorId}{h.note ? ` · ${h.note}` : ""}</span></li>
            ))}
          </ul>
        </section>
      </div>
    </div>
  );
}
