import { useMemo, useState } from "react";
import { ApiError } from "../api/client";
import { usePermissions } from "../api/identity";
import {
  useApproveSnapshot,
  useConflicts,
  useDecide,
  useGraph,
  useReconcile,
  useReconciliation,
  useSnapshotProducts,
  type Conflict,
  type GraphNode,
  type Reconciliation,
} from "../api/reconciliation";

const TONE: Record<string, string> = {
  QUEUED: "info", RUNNING: "info", SUCCEEDED: "success", FAILED: "danger", OPEN: "warning", AUTO_RESOLVED: "muted",
  RESOLVED: "success", DEFERRED: "muted", APPROVED: "success", SUPERSEDED: "muted",
};
const Pill = ({ s, label }: { s: string; label?: string }) => (
  <span className={`pra-pill pra-pill--${TONE[s] ?? "muted"}`}>{label ?? s.replace(/_/g, " ").toLowerCase()}</span>
);
const show = (v: unknown) => (v === null || v === undefined ? "—" : typeof v === "string" ? v : JSON.stringify(v));
const keyText = (k: string) => {
  try {
    return (JSON.parse(k) as unknown[]).join(" · ");
  } catch {
    return k;
  }
};
const errText = (e: unknown) =>
  e instanceof ApiError ? [e.message, ...e.fieldErrors.map((f) => f.message)].join(" ") : "Something went wrong.";
const entries = (o: Record<string, number> | undefined) =>
  Object.entries(o ?? {}).map(([k, n]) => `${k} ${n}`).join(" · ") || "none";

/** Step 6: reconcile every source's staged records, settle conflicts, approve an immutable evidence snapshot. */
export function ReconciliationScreen({ runId, onBack, onOpenReadiness }: {
  runId: string; onBack: () => void; onOpenReadiness?: () => void;
}) {
  const { has } = usePermissions();
  const ov = useReconciliation(runId);
  const reconcile = useReconcile(runId);
  const approve = useApproveSnapshot(runId);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const data = ov.data;
  const latest = data?.latest ?? null;
  const ok = data?.latest_succeeded ?? null;

  const run = async (fn: () => Promise<unknown>, done?: string) => {
    setError(null);
    setNote(null);
    try {
      await fn();
      if (done) setNote(done);
    } catch (e) {
      setError(errText(e));
    }
  };

  if (ov.isLoading) return <div className="pra-page"><p className="pra-muted">Loading…</p></div>;
  if (!data) return <div className="pra-page"><div className="pra-alert">Could not load reconciliation.</div></div>;
  const busy = latest?.status === "QUEUED" || latest?.status === "RUNNING";

  return (
    <div className="pra-page">
      <div className="pra-page__head">
        <div>
          <h1 className="pra-page__title">Reconcile &amp; approve evidence</h1>
          <p className="pra-page__subtitle">
            Combine every source field by field, let a Data Steward settle conflicts, and freeze the result as an
            approved evidence snapshot. Similar names are only suggestions; nothing is merged automatically.
          </p>
        </div>
        <div className="pra-ready__actions">
          {onOpenReadiness && (
            <button type="button" className="pra-button pra-button--ghost" onClick={onOpenReadiness}>
              Evidence readiness →
            </button>
          )}
          <button type="button" className="pra-button pra-button--ghost" onClick={onBack}>Back to Evidence Hub</button>
        </div>
      </div>
      {error && <div className="pra-alert" role="alert">{error}</div>}
      {note && <div className="pra-notice" role="status">{note}</div>}

      <section className="pra-card pra-map__section" aria-label="Inputs">
        <div className="pra-excel__head">
          <div>
            <h2 className="pra-hub__title">INPUTS</h2>
            <p className="pra-hub__sub">The latest successful mapping apply of each source.</p>
          </div>
          {has("evidence.map") && (
            <button type="button" className="pra-button pra-button--primary" disabled={busy || reconcile.isPending ||
              data.current_inputs.length === 0}
              title={data.current_inputs.length === 0 ? "Apply at least one published mapping first" : undefined}
              onClick={() => run(() => reconcile.mutateAsync())}>
              {busy ? "Reconciling…" : "Reconcile"}
            </button>
          )}
        </div>
        {data.current_inputs.length === 0 ? (
          <p className="pra-muted">No applied mappings yet. Map and apply at least one source in the Evidence Hub.</p>
        ) : (
          <table className="pra-uploads">
            <thead><tr><th>Source</th><th>Domain</th><th>System</th><th>File</th><th>Data as of</th><th>Records</th></tr></thead>
            <tbody>
              {data.current_inputs.map((i) => (
                <tr key={i.source_id}>
                  <td>{i.source_name}</td><td>{i.domain.replace(/_/g, " ").toLowerCase()}</td><td>{i.system}</td>
                  <td>{i.filename}</td><td>{i.as_of}</td><td>{i.records}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {ok && !ok.inputs_current && !busy && (
          <div className="pra-notice">New data has been applied since the last reconciliation. Reconcile again.</div>
        )}
      </section>

      {latest && <StatusCard r={latest} />}

      {ok && (
        <>
          <ApprovalCard blockers={data.approval_blockers} canApprove={has("snapshot.approve")}
            pending={approve.isPending}
            onApprove={() => run(async () => {
              const s = await approve.mutateAsync(ok.id);
              return s;
            }, "Snapshot approved.")} />
          <ConflictQueue runId={runId} rec={ok} canDecide={has("conflict.resolve")} onError={setError} />
          <Warnings r={ok} />
        </>
      )}

      {data.snapshots.length > 0 && <Snapshots runId={runId} snapshots={data.snapshots} />}
    </div>
  );
}

function StatusCard({ r }: { r: Reconciliation }) {
  const c = r.counts;
  return (
    <section className="pra-card pra-map__section" aria-label="Reconciliation">
      <h2 className="pra-hub__title">RECONCILIATION <Pill s={r.status} /></h2>
      <p className="pra-hub__sub">
        Policy <strong>{r.policy_ref}</strong> · schema <strong>{r.canonical_ref}</strong> · freshness measured on
        {" "}{r.reconciled_on}{r.result_hash && <> · result <code title={r.result_hash}>{r.result_hash.slice(7, 19)}…</code></>}
      </p>
      {r.status === "FAILED" && <div className="pra-alert">{r.error}</div>}
      {(r.status === "QUEUED" || r.status === "RUNNING") && <div className="pra-notice">Reconciling in the background… this updates by itself.</div>}
      {c && (
        <div className="pra-map__stats">
          <div>Records: {entries(c.records)}</div>
          <div>Relationships: {entries(c.relationships)}</div>
          <div><strong>{c.conflicts.OPEN}</strong> open · {c.conflicts.RESOLVED} resolved · {c.conflicts.DEFERRED} deferred ·
            {" "}{c.conflicts.AUTO_RESOLVED} settled by policy</div>
          <div>{c.stale_sources} stale source(s) · {c.dangling_references} dangling link(s) · {c.possible_duplicates} possible duplicate(s)</div>
        </div>
      )}
    </section>
  );
}

function ApprovalCard({ blockers, canApprove, pending, onApprove }: {
  blockers: string[]; canApprove: boolean; pending: boolean; onApprove: () => void;
}) {
  return (
    <section className="pra-card pra-map__section" aria-label="Approve snapshot">
      <h2 className="pra-hub__title">APPROVE SNAPSHOT</h2>
      {blockers.length === 0
        ? <p>Everything is reconciled and every material conflict is decided. Approving freezes this result as a new
          immutable evidence snapshot with an evidence ID for every fact.</p>
        : <ul className="pra-map__issues">{blockers.map((b) => (
          <li key={b} className="pra-map__issue pra-map__issue--blocking">{b}</li>))}</ul>}
      {canApprove
        ? <button type="button" className="pra-button pra-button--primary" disabled={blockers.length > 0 || pending}
            onClick={onApprove}>{pending ? "Approving…" : "Approve snapshot"}</button>
        : <p className="pra-muted">A Data Steward approves the snapshot.</p>}
    </section>
  );
}

function ConflictQueue({ runId, rec, canDecide, onError }: {
  runId: string; rec: Reconciliation; canDecide: boolean; onError: (e: string | null) => void;
}) {
  const [filter, setFilter] = useState("OPEN");
  const conflicts = useConflicts(runId, rec.id, filter);
  const list = conflicts.data ?? [];
  return (
    <section className="pra-card pra-map__section" aria-label="Conflicts">
      <div className="pra-excel__head">
        <div>
          <h2 className="pra-hub__title">CONFLICT QUEUE</h2>
          <p className="pra-hub__sub">Sources disagree on these values. Material fields need a Data Steward decision; the
            others were settled by the precedence policy and are shown for transparency.</p>
        </div>
        <div className="pra-map__versions">
          {[["OPEN", "Needs decision"], ["", "All"]].map(([v, l]) => (
            <button key={l} type="button" className={`pra-chip${filter === v ? " pra-chip--on" : ""}`}
              onClick={() => setFilter(v)}>{l}</button>
          ))}
        </div>
      </div>
      {list.length === 0 && <p className="pra-muted">{filter === "OPEN" ? "No conflicts need a decision." : "No conflicts."}</p>}
      <ul className="pra-recon__list">
        {list.map((c) => <ConflictItem key={c.id} runId={runId} c={c} canDecide={canDecide} onError={onError} />)}
      </ul>
    </section>
  );
}

function ConflictItem({ runId, c, canDecide, onError }: {
  runId: string; c: Conflict; canDecide: boolean; onError: (e: string | null) => void;
}) {
  const decide = useDecide(runId);
  const [manual, setManual] = useState("");
  const [rationale, setRationale] = useState("");
  const editable = canDecide && (c.status === "OPEN" || c.material);
  const act = async (a: Parameters<typeof decide.mutateAsync>[0]) => {
    onError(null);
    try {
      await decide.mutateAsync(a);
    } catch (e) {
      onError(errText(e));
    }
  };
  return (
    <li className={`pra-recon__item${c.status === "OPEN" ? " pra-recon__item--open" : ""}`}>
      <div className="pra-recon__head">
        <strong>{c.entity} {keyText(c.key)}</strong> · field <code>{c.field}</code>{" "}
        {c.material ? <Pill s="OPEN" label="material" /> : <Pill s="AUTO_RESOLVED" label="not material" />}{" "}
        <Pill s={c.status} />
      </div>
      <p className="pra-muted pra-recon__reason">Recommendation: {c.reason}</p>
      <table className="pra-uploads">
        <thead><tr><th>Source</th><th>System</th><th>Data as of</th><th>Value</th><th /></tr></thead>
        <tbody>
          {c.candidates.map((x) => (
            <tr key={x.source_id} className={x.source_id === c.recommended_source_id ? "pra-uploads__row--on" : undefined}>
              <td>{x.source_name}{x.source_id === c.recommended_source_id && <span className="pra-map__by">recommended</span>}</td>
              <td>{x.system}</td>
              <td>{x.as_of}{x.stale && <> <Pill s="FAILED" label="stale" /></>}</td>
              <td><strong>{show(x.value)}</strong></td>
              <td>{editable && (
                <button type="button" className="pra-button pra-button--ghost" disabled={decide.isPending}
                  onClick={() => act({ conflictId: c.id, decision: "SELECT_SOURCE", source_id: x.source_id })}>
                  Use this value</button>)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {c.decision && (
        <p className="pra-recon__decision">
          Decision: <strong>{c.decision.decision.replace(/_/g, " ").toLowerCase()}</strong>
          {c.decision.decision === "MANUAL" && <> = “{show(c.decision.value)}”</>} by {c.decision.by}
          {c.decision.rationale && <> — “{c.decision.rationale}”</>}
        </p>
      )}
      {editable && (
        <div className="pra-recon__manual">
          <input className="pra-input" placeholder="Other value (manual)" value={manual}
            onChange={(e) => setManual(e.target.value)} aria-label="Manual value" />
          <input className="pra-input" placeholder="Rationale (required for manual or defer)" value={rationale}
            onChange={(e) => setRationale(e.target.value)} aria-label="Rationale" />
          <button type="button" className="pra-button pra-button--ghost"
            disabled={!manual.trim() || rationale.trim().length < 3 || decide.isPending}
            onClick={() => act({ conflictId: c.id, decision: "MANUAL", value: manual, rationale })}>Use manual value</button>
          <button type="button" className="pra-button pra-button--ghost"
            disabled={rationale.trim().length < 3 || decide.isPending}
            onClick={() => act({ conflictId: c.id, decision: "DEFER", rationale })}>Defer</button>
        </div>
      )}
    </li>
  );
}

function Warnings({ r }: { r: Reconciliation }) {
  const s = r.summary;
  if (!s || (!s.stale_sources.length && !s.dangling_total && !s.possible_duplicates.length)) return null;
  return (
    <section className="pra-card pra-map__section" aria-label="Warnings">
      <h2 className="pra-hub__title">WARNINGS</h2>
      {s.stale_sources.length > 0 && (
        <details className="pra-map__details" open>
          <summary>{s.stale_sources.length} stale source(s): older than their freshness limit</summary>
          <ul className="pra-map__other">{s.stale_sources.map((x) => (
            <li key={x.source_id}>{x.name} · as of {x.as_of} · limit {x.limit_days} days</li>))}</ul>
        </details>
      )}
      {s.dangling_total > 0 && (
        <details className="pra-map__details">
          <summary>{s.dangling_total} link(s) point to records no source supplied</summary>
          <ul className="pra-map__other">{s.dangling.slice(0, 50).map((d, i) => (
            <li key={i}>{d.name}: {d.entity} {keyText(d.key)}{d.from_record && <> (from {keyText(d.from_record)})</>}</li>))}</ul>
        </details>
      )}
      {s.possible_duplicates.length > 0 && (
        <details className="pra-map__details">
          <summary>{s.possible_duplicates.length} possible duplicate(s): same name, different IDs (suggestion only)</summary>
          <ul className="pra-map__other">{s.possible_duplicates.map((d) => (
            <li key={d.entity + d.normalised_name}>{d.entity} “{d.normalised_name}”: {d.keys.map(keyText).join(", ")}</li>))}</ul>
        </details>
      )}
    </section>
  );
}

function Snapshots({ runId, snapshots }: { runId: string; snapshots: import("../api/reconciliation").Snapshot[] }) {
  const current = snapshots.find((s) => s.status === "APPROVED") ?? snapshots[0];
  const [selected, setSelected] = useState(current.id);
  return (
    <section className="pra-card pra-map__section" aria-label="Snapshots">
      <h2 className="pra-hub__title">EVIDENCE SNAPSHOTS</h2>
      <table className="pra-uploads">
        <thead><tr><th>Snapshot</th><th>Status</th><th>Approved</th><th>Facts</th><th>Records</th><th>Content hash</th><th /></tr></thead>
        <tbody>
          {snapshots.map((s) => (
            <tr key={s.id} className={s.id === selected ? "pra-uploads__row--on" : undefined}>
              <td><strong>{s.code}</strong>{s.parent_code && <span className="pra-muted"> (after {s.parent_code})</span>}</td>
              <td><Pill s={s.status} /></td>
              <td>{s.approved_by_name} · {new Date(s.approved_at).toLocaleString()}</td>
              <td>{s.counts.facts}{s.counts.conflicted_facts > 0 && <> · {s.counts.conflicted_facts} deferred</>}</td>
              <td>{entries(s.counts.records)}</td>
              <td><code title={s.content_hash}>{s.content_hash.slice(7, 19)}…</code></td>
              <td><button type="button" className="pra-link" onClick={() => setSelected(s.id)}>Explore</button></td>
            </tr>
          ))}
        </tbody>
      </table>
      <GraphExplorer runId={runId} snapshotId={selected} />
    </section>
  );
}

function GraphExplorer({ runId, snapshotId }: { runId: string; snapshotId: string }) {
  const products = useSnapshotProducts(runId, snapshotId);
  const options = useMemo(() => {
    const names: Record<string, string> = {};
    for (const f of products.data?.items ?? []) {
      const id = keyText(f.key);
      if (f.field === "productName") names[id] = String(f.value ?? "");
      else names[id] ??= "";
    }
    return Object.entries(names).sort();
  }, [products.data]);
  const [product, setProduct] = useState<string | null>(null);
  const chosen = product ?? options[0]?.[0] ?? null;
  const graph = useGraph(runId, snapshotId, chosen);
  const g = graph.data;
  return (
    <div className="pra-recon__graph">
      <h3 className="pra-map__sub">Telco graph</h3>
      {options.length === 0 ? <p className="pra-muted">This snapshot has no products.</p> : (
        <label className="pra-field" style={{ maxWidth: 420 }}>
          <span className="pra-field__label">Product</span>
          <select className="pra-input" value={chosen ?? ""} onChange={(e) => setProduct(e.target.value)}>
            {options.map(([id, name]) => <option key={id} value={id}>{id}{name && ` · ${name}`}</option>)}
          </select>
        </label>
      )}
      {g && (
        <div className="pra-recon__nodes">
          <NodeCard n={g.product} title="Product" />
          {g.edges.length > 0 && (
            <div className="pra-recon__node">
              <strong>Links</strong>
              <ul className="pra-map__other">{g.edges.map((e) => (
                <li key={e.evidence_id}>{e.from_entity} {keyText(e.from_key)} — <strong>{e.name}</strong> → {e.to_entity}
                  {" "}{keyText(e.to_key)} <code className="pra-recon__ev">{e.evidence_id}</code></li>))}</ul>
            </div>
          )}
          {g.nodes.map((n) => <NodeCard key={n.entity + n.key} n={n} title={n.entity} />)}
          {Object.entries(g.attached).map(([entity, list]) => list.map((n) => (
            <NodeCard key={entity + n.key} n={n} title={entity} />)))}
        </div>
      )}
    </div>
  );
}

function NodeCard({ n, title }: { n: GraphNode; title: string }) {
  return (
    <div className="pra-recon__node">
      <strong>{title}</strong> <span className="pra-muted">{keyText(n.key)}</span>
      <table className="pra-recon__facts">
        <tbody>
          {Object.entries(n.values).map(([f, v]) => (
            <tr key={f}>
              <td>{f}</td>
              <td>{show(v)}{n.flags.stale.includes(f) && <> <Pill s="FAILED" label="stale" /></>}
                {n.flags.conflicted.includes(f) && <> <Pill s="OPEN" label="conflicted" /></>}</td>
              <td><code className="pra-recon__ev">{n.evidence_ids[f]}</code></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
