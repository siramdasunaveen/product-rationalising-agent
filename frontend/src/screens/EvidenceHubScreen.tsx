import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ApiError } from "../api/client";
import {
  downloadTemplate,
  useAddSource,
  useConfirmSheet,
  useEvidence,
  usePreviewSheet,
  useSetDomains,
  useUploadDetail,
  useUploadFile,
  type DomainStatus,
  type EvidenceDomain,
  type SheetInfo,
  type UploadDetail,
  type UploadSummary,
} from "../api/evidence";
import { usePermissions } from "../api/identity";
import { useReconciliation, type ReconOverview } from "../api/reconciliation";
import { GuidanceCards } from "../components/GuidanceCards";
import { WizardSteps } from "../components/WizardSteps";

const STATUS: Record<DomainStatus, { label: string; tone: string }> = {
  NO_SOURCE: { label: "No source", tone: "muted" },
  CHECKING: { label: "Checking…", tone: "info" },
  NEEDS_CONFIRMATION: { label: "Confirm sheet", tone: "warning" },
  READY: { label: "Ready", tone: "success" },
  REJECTED: { label: "Rejected", tone: "danger" },
};

const UPLOAD_STATUS: Record<UploadSummary["status"], { label: string; tone: string }> = {
  PROCESSING: { label: "Scanning and reading…", tone: "info" },
  READY: { label: "Checked", tone: "success" },
  REJECTED: { label: "Rejected", tone: "danger" },
};

const PIPELINE = [
  { name: "Ingest", text: "API / file upload / scheduled drop" },
  { name: "Map", text: "Template or field mapping to canonical schema" },
  { name: "Validate", text: "Required fields, types, duplicates and reference checks" },
  { name: "Match", text: "Product ID, source key and configurable matching rules" },
  { name: "Reconcile", text: "Precedence, freshness and steward-approved conflict rules" },
  { name: "Telco Graph", text: "Commercial product → service → resource → provisioning" },
];

const MAX_MB = 10;

function pipelineState(step: string, ready: number, selected: number, r: ReconOverview | undefined): string {
  const applied = r?.current_inputs.length ?? 0;
  const ok = r?.latest_succeeded;
  const snap = r?.snapshots.find((x) => x.status === "APPROVED");
  switch (step) {
    case "Ingest": return `${ready} of ${selected} ready`;
    case "Map": return applied ? `${applied} source(s) applied` : "No mapping applied";
    case "Validate": return applied ? "Checked before publishing" : "With each mapping";
    case "Match": return ok ? "Matched on canonical keys" : "Not run";
    case "Reconcile":
      if (!ok) return r?.latest ? r.latest.status.toLowerCase() : "Not run";
      return ok.counts && ok.counts.open_material_conflicts ? `${ok.counts.open_material_conflicts} conflict(s) open`
        : "Reconciled";
    case "Telco Graph": return snap ? `${snap.code} approved` : "No snapshot yet";
    default: return "";
  }
}

function fmtSize(bytes: number) {
  return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1048576).toFixed(1)} MB`;
}
const fmtTime = (iso: string) => new Date(iso).toLocaleString();

function Pill({ tone, children }: { tone: string; children: ReactNode }) {
  return <span className={`pra-pill pra-pill--${tone}`}>{children}</span>;
}

/** Wizard step 2 (screen 03): pick evidence domains, upload Excel, confirm sheet + header row. */
export function EvidenceHubScreen({
  runId,
  onBack,
  onContinue,
  onExit,
  onOpenMapping,
  onOpenReconciliation,
  onOpenReadiness,
}: {
  runId: string;
  onBack: () => void;
  onContinue: () => void;
  onExit: () => void;
  onOpenMapping?: (domain: string, sourceId?: string) => void;
  onOpenReconciliation?: () => void;
  onOpenReadiness?: () => void;
}) {
  const { has } = usePermissions();
  const evidence = useEvidence(runId);
  const recon = useReconciliation(runId);
  const setDomains = useSetDomains(runId);
  const data = evidence.data;

  const [draft, setDraft] = useState<Set<string>>(new Set());
  const [open, setOpen] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);

  // Reset the local selection whenever the server copy changes (new version).
  useEffect(() => {
    if (data) setDraft(new Set(data.domains.filter((d) => d.selected).map((d) => d.key)));
  }, [data?.version, data?.run_id]); // eslint-disable-line react-hooks/exhaustive-deps

  if (evidence.isLoading) return <div className="pra-page"><p className="pra-muted">Loading evidence…</p></div>;
  if (evidence.error || !data) {
    return <div className="pra-page"><div className="pra-alert" role="alert">Could not load this run's evidence.</div></div>;
  }

  const canSelect = data.editable && has("run.create");
  const canUpload = data.editable && has("evidence.upload");
  const saved = new Set(data.domains.filter((d) => d.selected).map((d) => d.key));
  const dirty = draft.size !== saved.size || [...draft].some((k) => !saved.has(k));
  const needsConfirm = !data.selection_confirmed || dirty;
  const selected = data.domains.filter((d) => d.selected);
  const ready = selected.filter((d) => d.status === "READY").length;
  const openDomain = data.domains.find((d) => d.key === open) ?? null;

  const toggle = (key: string) =>
    setDraft((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  const saveSelection = async () => {
    setBanner(null);
    try {
      await setDomains.mutateAsync({ domains: [...draft], version: data.version });
    } catch (e) {
      setBanner(e instanceof ApiError && e.code === "version_conflict"
        ? "Someone else changed this run. The latest version has been loaded."
        : e instanceof ApiError ? e.message : "Could not save the selection.");
      evidence.refetch();
    }
  };

  return (
    <div className="pra-page">
      <div className="pra-page__head">
        <div>
          <h1 className="pra-page__title">Create Rationalisation Run</h1>
          <p className="pra-page__subtitle">
            Step 2 of 5 · Connect and reconcile product evidence · <strong>{data.run_code}</strong> · {data.run_name}
          </p>
        </div>
        <button type="button" className="pra-button pra-button--ghost" onClick={onExit}>Back to assessments</button>
      </div>
      <div className="pra-progress" aria-hidden="true"><span className="pra-progress__fill" style={{ width: "40%" }} /></div>

      <WizardSteps current={2} available={[1, 2]} onSelect={(s) => s === 1 && onBack()} />

      {!data.editable && (
        <div className="pra-notice" role="status">Evidence is locked because analysis has started.</div>
      )}
      {banner && <div className="pra-alert" role="alert">{banner}</div>}

      <div className="pra-hub">
        <section className="pra-card pra-hub__domains" aria-labelledby="domains-title">
          <h2 id="domains-title" className="pra-hub__title">EVIDENCE DOMAINS</h2>
          <p className="pra-hub__sub">Each domain can use one or more live systems or Excel files.</p>
          <ul className="pra-domains">
            {data.domains.map((d) => (
              <DomainRow
                key={d.key}
                domain={d}
                checked={draft.has(d.key)}
                canSelect={canSelect}
                isOpen={open === d.key}
                onToggle={() => toggle(d.key)}
                onOpenExcel={() => setOpen(open === d.key ? null : d.key)}
              />
            ))}
          </ul>
          {canSelect && needsConfirm && (
            <div className="pra-hub__confirm">
              <span className="pra-muted">
                {dirty ? "You changed the selection." : "Proposed from the run's objectives."} Confirm to save it.
              </span>
              <button type="button" className="pra-button pra-button--primary" onClick={saveSelection}
                disabled={draft.size === 0 || setDomains.isPending}
                title={draft.size === 0 ? "Select at least one domain" : undefined}>
                {setDomains.isPending ? "Saving…" : "Confirm domains"}
              </button>
            </div>
          )}
        </section>

        <div className="pra-hub__arrow" aria-hidden="true">→</div>

        <section className="pra-card pra-hub__pipeline" aria-labelledby="pipeline-title">
          <h2 id="pipeline-title" className="pra-hub__title">MULTI-SOURCE HARMONISATION</h2>
          <p className="pra-hub__sub">Normalize, map and reconcile before analysis.</p>
          <ol className="pra-pipeline">
            {PIPELINE.map((p, i) => (
              <li key={p.name} className="pra-pipeline__step pra-pipeline__step--live">
                <span className="pra-pipeline__name">{i + 1} {p.name}</span>
                <span className="pra-pipeline__text">{p.text}</span>
                <span className="pra-pipeline__state">{pipelineState(p.name, ready, selected.length, recon.data)}</span>
              </li>
            ))}
          </ol>
          {onOpenReconciliation && (
            <div className="pra-hub__confirm">
              <span className="pra-muted">Combine all sources, settle conflicts and approve the evidence snapshot.</span>
              {onOpenReadiness && (
                <button type="button" className="pra-button pra-button--ghost" onClick={onOpenReadiness}>
                  Evidence readiness
                </button>
              )}
              <button type="button" className="pra-button pra-button--primary" onClick={onOpenReconciliation}>
                Reconcile &amp; approve evidence →
              </button>
            </div>
          )}
        </section>
      </div>

      <div className="pra-security">
        <strong>SECURITY</strong>
        <span>CRM metadata by default · Aggregates where sufficient · No subscriber or personal data ·
          Excel scanned and quarantined before ingestion</span>
      </div>

      {openDomain && (
        <ExcelPanel key={openDomain.key} runId={runId} domain={openDomain} canUpload={canUpload && openDomain.selected}
          onClose={() => setOpen(null)} onOpenMapping={onOpenMapping} />
      )}

      <div className="pra-form__actions pra-hub__actions">
        {selected.length > ready && (
          <span className="pra-muted">
            {selected.length - ready} selected domain(s) have no confirmed source yet. They will be shown as
            Not assessed (never scored low).
          </span>
        )}
        <button type="button" className="pra-button pra-button--primary pra-button--lg" onClick={onContinue}
          disabled={needsConfirm && canSelect}
          title={needsConfirm && canSelect ? "Confirm the evidence domains first" : undefined}>
          Continue to Scoring
        </button>
      </div>

      <GuidanceCards
        next={["Connect approved commercial and technical sources.", "Resolve mapping and reconciliation exceptions."]}
        outcome={["Canonical evidence snapshot is created.", "Source lineage and conflicts remain visible."]}
      />
    </div>
  );
}

function DomainRow({
  domain: d,
  checked,
  canSelect,
  isOpen,
  onToggle,
  onOpenExcel,
}: {
  domain: EvidenceDomain;
  checked: boolean;
  canSelect: boolean;
  isOpen: boolean;
  onToggle: () => void;
  onOpenExcel: () => void;
}) {
  const st = STATUS[d.status];
  const why = d.proposed ? `Proposed: ${d.proposal_reasons.join(", ")}` : "Not proposed for these objectives";
  return (
    <li className={`pra-domain pra-domain--${st.tone}${checked ? "" : " pra-domain--off"}`}>
      <label className="pra-domain__label" title={why}>
        <input type="checkbox" checked={checked} disabled={!canSelect} onChange={onToggle} />
        <span>
          <span className="pra-domain__name">{d.label}</span>
          {d.proposed && <span className="pra-domain__hint">Proposed</span>}
        </span>
      </label>
      <Pill tone={st.tone}>{st.label}</Pill>
      <span className="pra-domain__chips">
        <span className="pra-chip pra-chip--source pra-chip--disabled"
          title={`${d.typical_systems}. Live connectors arrive in a later step.`}>{d.live_system}</span>
        <button type="button" className={`pra-chip pra-chip--source${isOpen ? " pra-chip--on" : ""}`}
          onClick={onOpenExcel} aria-expanded={isOpen} disabled={!d.selected}
          title={d.selected ? "Upload or review Excel files" : "Select and confirm this domain first"}>
          Excel ›
        </button>
      </span>
    </li>
  );
}

function ExcelPanel({
  runId,
  domain,
  canUpload,
  onClose,
  onOpenMapping,
}: {
  runId: string;
  domain: EvidenceDomain;
  canUpload: boolean;
  onClose: () => void;
  onOpenMapping?: (domain: string, sourceId?: string) => void;
}) {
  const upload = useUploadFile(runId);
  const addSource = useAddSource(runId);
  const [sourceId, setSourceId] = useState<string | null>(domain.sources[0]?.id ?? null);
  const [adding, setAdding] = useState(false);
  const [newName, setNewName] = useState("");
  const [newSystem, setNewSystem] = useState("");
  const [chosen, setChosen] = useState<string | null>(null);
  const [error, setError] = useState<string[] | null>(null);
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const uploads = sourceId ? domain.uploads.filter((u) => u.source_id === sourceId) : domain.uploads;
  const fallback = uploads.find((u) => u.status !== "REJECTED") ?? uploads[0];
  const selectedId = chosen ?? fallback?.id ?? null;

  const send = async (file: File | undefined) => {
    if (!file) return;
    setError(null);
    if (!file.name.toLowerCase().endsWith(".xlsx")) {
      setError(["Only Excel .xlsx files are accepted."]);
      return;
    }
    if (file.size > MAX_MB * 1024 * 1024) {
      setError([`The file is larger than ${MAX_MB} MB.`]);
      return;
    }
    try {
      const u = await upload.mutateAsync({ domain: domain.key, file, sourceId });
      setSourceId(u.source_id);
      setChosen(u.id);
      if (u.duplicate) setError(["This exact file was already uploaded; showing the existing upload."]);
    } catch (e) {
      setError(e instanceof ApiError && e.fieldErrors.length
        ? e.fieldErrors.map((x) => x.message)
        : [e instanceof ApiError ? e.message : "Upload failed. Try again."]);
    } finally {
      if (input.current) input.current.value = "";
    }
  };

  return (
    <section className="pra-card pra-excel" aria-labelledby="excel-title">
      <div className="pra-excel__head">
        <div>
          <h2 id="excel-title" className="pra-hub__title">EXCEL · {domain.label}</h2>
          <p className="pra-hub__sub">
            Governed template {domain.template_ref ?? "(none published)"} · .xlsx only, up to {MAX_MB} MB ·
            no macros or passwords · product metadata and aggregates only
          </p>
        </div>
        <div className="pra-excel__tools">
          <button type="button" className="pra-button pra-button--ghost" disabled={!domain.template_ref}
            onClick={() => downloadTemplate(domain.key).catch(() => setError(["Could not download the template."]))}>
            Download template
          </button>
          <button type="button" className="pra-button pra-button--ghost" onClick={onClose}>Close</button>
        </div>
      </div>

      <div className="pra-sources" aria-label="Sources">
        <span className="pra-field__label">Source</span>
        {domain.sources.length === 0 && <span className="pra-chip pra-chip--on">Excel (default)</span>}
        {domain.sources.map((src) => (
          <button key={src.id} type="button" className={`pra-chip${src.id === sourceId ? " pra-chip--on" : ""}`}
            onClick={() => { setSourceId(src.id); setChosen(null); }}>
            {src.name} <span className="pra-muted">· {src.system}</span>
          </button>
        ))}
        {canUpload && !adding && (
          <button type="button" className="pra-link" onClick={() => setAdding(true)}>+ Add another source</button>
        )}
        {adding && (
          <span className="pra-sources__add">
            <input className="pra-input" placeholder="Name, e.g. Dynamics export" value={newName}
              onChange={(e) => setNewName(e.target.value)} aria-label="Source name" />
            <input className="pra-input" placeholder="System, e.g. DYNAMICS" value={newSystem}
              onChange={(e) => setNewSystem(e.target.value.toUpperCase())} aria-label="Source system" />
            <button type="button" className="pra-button pra-button--ghost" disabled={addSource.isPending}
              onClick={async () => {
                setError(null);
                try {
                  const created = await addSource.mutateAsync({ domain: domain.key, name: newName, system: newSystem });
                  setSourceId(created.id);
                  setAdding(false);
                  setNewName("");
                  setNewSystem("");
                } catch (e) {
                  setError(e instanceof ApiError && e.fieldErrors.length ? e.fieldErrors.map((x) => x.message)
                    : [e instanceof ApiError ? e.message : "Could not add the source."]);
                }
              }}>Add</button>
            <button type="button" className="pra-link" onClick={() => setAdding(false)}>Cancel</button>
          </span>
        )}
      </div>

      {canUpload && (
        <div
          className={`pra-drop${dragging ? " pra-drop--over" : ""}`}
          onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => { e.preventDefault(); setDragging(false); send(e.dataTransfer.files[0]); }}
        >
          <span>{upload.isPending ? "Uploading…" : "Drop an .xlsx file here, or"}</span>
          <button type="button" className="pra-button pra-button--primary" disabled={upload.isPending}
            onClick={() => input.current?.click()}>Choose file</button>
          <input ref={input} type="file" hidden
            accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            onChange={(e) => send(e.target.files?.[0])} />
        </div>
      )}
      {error && (
        <div className="pra-alert" role="alert">
          {error.length === 1 ? error[0] : <ul>{error.map((m) => <li key={m}>{m}</li>)}</ul>}
        </div>
      )}

      {uploads.length === 0 ? (
        <p className="pra-muted">No files uploaded for this source yet.</p>
      ) : (
        <table className="pra-uploads">
          <thead>
            <tr><th>File</th><th>Uploaded</th><th>Status</th><th>Schema</th></tr>
          </thead>
          <tbody>
            {uploads.map((u) => (
              <tr key={u.id} className={u.id === selectedId ? "pra-uploads__row--on" : undefined}
                onClick={() => setChosen(u.id)}>
                <td>
                  <button type="button" className="pra-link" onClick={() => setChosen(u.id)}>{u.filename}</button>
                  <span className="pra-muted"> · {fmtSize(u.size_bytes)}</span>
                </td>
                <td>{u.uploaded_by_name} · {fmtTime(u.uploaded_at)}</td>
                <td>
                  <Pill tone={UPLOAD_STATUS[u.status].tone}>{UPLOAD_STATUS[u.status].label}</Pill>
                  {u.status === "REJECTED" && <div className="pra-uploads__reason">{u.reasons[0]?.message}</div>}
                </td>
                <td>{u.schema_version ? `v${u.schema_version}` : u.status === "READY" ? "Not confirmed" : "—"}
                  {u.as_of_date && <div className="pra-muted">as of {u.as_of_date}</div>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {selectedId && (
        <UploadView runId={runId} uploadId={selectedId} canConfirm={canUpload}
          onOpenMapping={onOpenMapping
            ? () => onOpenMapping(domain.key, domain.uploads.find((u) => u.id === selectedId)?.source_id)
            : undefined} />
      )}
    </section>
  );
}

function UploadView({ runId, uploadId, canConfirm, onOpenMapping }: {
  runId: string; uploadId: string; canConfirm: boolean; onOpenMapping?: () => void;
}) {
  const detail = useUploadDetail(runId, uploadId);
  const u = detail.data;
  if (!u) return <p className="pra-muted">Loading file details…</p>;
  if (u.status === "PROCESSING") {
    return <div className="pra-notice" role="status">The file is in quarantine: it is being scanned and read. This page updates by itself.</div>;
  }
  if (u.status === "REJECTED") {
    return (
      <div className="pra-alert" role="alert">
        <strong>{u.filename} was rejected and deleted.</strong>
        <ul>{u.reasons.map((r) => <li key={r.code + r.message}>{r.message}</li>)}</ul>
        Fix the file and upload it again.
      </div>
    );
  }
  return <SheetChooser key={u.id} runId={runId} upload={u} canConfirm={canConfirm} onOpenMapping={onOpenMapping} />;
}

function SheetChooser({ runId, upload: u, canConfirm, onOpenMapping }: {
  runId: string; upload: UploadDetail; canConfirm: boolean; onOpenMapping?: () => void;
}) {
  const sheets = u.discovery?.sheets ?? [];
  const [sheetName, setSheetName] = useState(u.schema?.sheet_name ?? u.discovery?.default_sheet ?? sheets[0]?.name ?? "");
  const [override, setOverride] = useState<SheetInfo | null>(null);
  const [rowInput, setRowInput] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const preview = usePreviewSheet(runId);
  const confirm = useConfirmSheet(runId);
  const [asOf, setAsOf] = useState(u.as_of_date ?? new Date().toISOString().slice(0, 10));

  const discovered = useMemo(() => sheets.find((s) => s.name === sheetName) ?? null, [sheets, sheetName]);
  const info = override ?? discovered;

  useEffect(() => {
    setRowInput(info?.header_row ? String(info.header_row) : "");
  }, [info?.header_row, sheetName]);

  const applyRow = async () => {
    const row = Number(rowInput);
    if (!Number.isInteger(row) || row < 1 || row > 50) {
      setMsg("Header row must be a number from 1 to 50.");
      return;
    }
    setMsg(null);
    try {
      setOverride(await preview.mutateAsync({ uploadId: u.id, sheet: sheetName, header_row: row }));
    } catch (e) {
      setMsg(e instanceof ApiError ? e.message : "Could not read that row.");
    }
  };

  const doConfirm = async () => {
    if (!info?.header_row) return;
    setMsg(null);
    try {
      const res = await confirm.mutateAsync({ uploadId: u.id, sheet: sheetName, header_row: info.header_row,
        as_of: asOf });
      setMsg(`Confirmed as source schema v${res.schema?.version}.`);
    } catch (e) {
      setMsg(e instanceof ApiError ? [e.message, ...e.fieldErrors.map((x) => x.message)].join(" ") : "Could not confirm.");
    }
  };

  const confirmedHere = u.schema && u.schema.sheet_name === sheetName && u.schema.header_row === info?.header_row;
  const tm = info?.template_match;

  return (
    <div className="pra-sheet">
      <div className="pra-sheet__controls">
        <label className="pra-field">
          <span className="pra-field__label">Worksheet</span>
          <select className="pra-input" value={sheetName}
            onChange={(e) => { setSheetName(e.target.value); setOverride(null); setMsg(null); }}>
            {sheets.map((s) => (
              <option key={s.name} value={s.name}>
                {s.name}{s.hidden ? " (hidden)" : ""}{s.valid ? "" : " (not usable)"}
              </option>
            ))}
          </select>
        </label>
        <label className="pra-field">
          <span className="pra-field__label">Header row</span>
          <span className="pra-period">
            <input className="pra-input" inputMode="numeric" value={rowInput} style={{ width: 80 }}
              onChange={(e) => setRowInput(e.target.value)} aria-label="Header row number" />
            <button type="button" className="pra-button pra-button--ghost" onClick={applyRow} disabled={preview.isPending}>
              {preview.isPending ? "Reading…" : "Use this row"}
            </button>
          </span>
        </label>
        <label className="pra-field">
          <span className="pra-field__label">Data as of</span>
          <input className="pra-input" type="date" value={asOf} max={new Date().toISOString().slice(0, 10)}
            onChange={(e) => setAsOf(e.target.value)} disabled={!canConfirm} aria-label="Data as of" />
        </label>
        <div className="pra-sheet__facts">
          {info && <span>{info.data_rows.toLocaleString()} data rows · {info.columns.length} columns</span>}
          {u.scan && <span className="pra-muted">Scan: clean ({u.scan.engine})</span>}
        </div>
      </div>

      {info && info.problems.length > 0 && (
        <div className="pra-alert" role="alert">{info.problems.join(" · ")}</div>
      )}
      {info && info.warnings.length > 0 && (
        <div className="pra-notice">{info.warnings.join(" · ")}</div>
      )}

      {tm && (
        <div className={`pra-match pra-match--${tm.complete ? "ok" : "gap"}`}>
          <strong>Template {info?.template_ref ?? u.discovery?.template_ref}:</strong>{" "}
          {tm.required_found} of {tm.required_total} required columns found.
          {tm.required_missing.length > 0 && <> Missing: {tm.required_missing.join(", ")}.</>}
          {tm.one_of_missing.length > 0 && <> Needs one of: {tm.one_of_missing.join("; ")}.</>}
          {tm.unknown_columns.length > 0 && <> Extra columns (mapped manually later): {tm.unknown_columns.join(", ")}.</>}
        </div>
      )}

      {info && info.columns.length > 0 && (
        <div className="pra-preview">
          <table>
            <thead>
              <tr>
                {info.columns.map((c) => (
                  <th key={c.index}>
                    <span className="pra-preview__col">{c.letter}</span> {c.header}
                    <span className="pra-preview__type">{c.type}{c.empty_pct ? ` · ${c.empty_pct}% empty` : ""}</span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {info.samples.map((row, i) => (
                <tr key={i}>{row.map((v, j) => <td key={j}>{v ?? <span className="pra-muted">—</span>}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {msg && <p className="pra-sheet__msg" role="status">{msg}</p>}

      <div className="pra-sheet__actions">
        {u.schema && (
          <span className="pra-muted">
            Confirmed: sheet “{u.schema.sheet_name}”, header row {u.schema.header_row} → source schema v{u.schema.version}
            {" "}<code title={u.schema.fingerprint}>{u.schema.fingerprint.slice(7, 19)}…</code>
          </span>
        )}
        {canConfirm && (
          <button type="button" className="pra-button pra-button--primary" onClick={doConfirm}
            disabled={!info || !info.valid || confirm.isPending || !!confirmedHere}
            title={info && !info.valid ? "This sheet cannot be used" : undefined}>
            {confirmedHere ? "Confirmed" : confirm.isPending ? "Confirming…" : "Confirm sheet and header"}
          </button>
        )}
        {u.schema && onOpenMapping && (
          <button type="button" className="pra-button pra-button--ghost" onClick={onOpenMapping}>Map columns →</button>
        )}
      </div>
    </div>
  );
}
