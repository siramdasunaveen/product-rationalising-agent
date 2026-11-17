import { useState } from "react";
import { ApiError } from "../api/client";
import { useConnectors, useDeleteConnector, useSaveConnector, useTestConnector, type Connector, type ConnectorForm } from "../api/connectors";
import { usePermissions } from "../api/identity";

const errText = (e: unknown) => (e instanceof ApiError ? e.message : "Something went wrong.");
const EMPTY: ConnectorForm = { key: "", name: "", system: "SALESFORCE", instanceUrl: "", apiVersion: "v61.0", clientId: "", clientSecret: "" };

/** Step 15: approved live connectors (Agent Admin). Credentials are write-only and stored encrypted. */
export function ConnectorsScreen() {
  const { has } = usePermissions();
  const canManage = has("connector.manage");
  const q = useConnectors();
  const save = useSaveConnector();
  const remove = useDeleteConnector();
  const test = useTestConnector();
  const [editing, setEditing] = useState<Connector | null | "new">(null);
  const [form, setForm] = useState<ConnectorForm>(EMPTY);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const edit = (c: Connector | "new") => {
    setEditing(c);
    setFieldErrors({});
    setForm(c === "new" ? EMPTY : { key: c.key, name: c.name, system: c.system, instanceUrl: c.instanceUrl,
      apiVersion: c.apiVersion, clientId: c.clientId ?? "", clientSecret: "", status: c.status });
  };
  const submit = async () => {
    setError(null); setFieldErrors({}); setNote(null);
    try {
      const body = { ...form, clientSecret: form.clientSecret || null, clientId: form.clientId || null };
      await save.mutateAsync({ id: editing && editing !== "new" ? editing.id : undefined, form: body });
      setEditing(null); setNote("Connector saved. Credentials are stored encrypted and never shown again.");
    } catch (e) { if (e instanceof ApiError) setFieldErrors(e.byField()); setError(errText(e)); }
  };

  return (
    <div className="pra-page">
      <div className="pra-page__head">
        <div>
          <h1 className="pra-page__title">Connectors</h1>
          <p className="pra-page__subtitle">Approved live source systems. Pulls read catalogue metadata only, through the same
            validation, mapping and reconciliation as Excel evidence.</p>
        </div>
        {canManage && <button type="button" className="pra-button pra-button--primary" onClick={() => edit("new")}>Add Salesforce connector</button>}
      </div>
      {error && <div className="pra-alert" role="alert">{error}</div>}
      {note && <div className="pra-notice" role="status">{note}</div>}
      {q.error && <div className="pra-alert" role="alert">{errText(q.error)}</div>}

      {editing && (
        <section className="pra-card pra-form pra-connector__form" aria-label="Connector">
          <h2 className="pra-overview__h2">{editing === "new" ? "New connector" : `Edit ${editing.name}`}</h2>
          {([["key", "Key (stable id, e.g. sf-prod)"], ["name", "Name"], ["instanceUrl", "Instance URL (https://org.my.salesforce.com)"],
            ["apiVersion", "API version"], ["clientId", "OAuth client id (connected app)"],
            ["clientSecret", editing === "new" ? "OAuth client secret" : "New client secret (leave blank to keep)"]] as const).map(([k, label]) => (
            <label key={k}>{label}
              <input className={`pra-input${fieldErrors[k] ? " pra-input--invalid" : ""}`} type={k === "clientSecret" ? "password" : "text"}
                value={(form[k] as string | null) ?? ""} disabled={k === "key" && editing !== "new"} autoComplete="off"
                onChange={(e) => setForm({ ...form, [k]: e.target.value })} />
              {fieldErrors[k] && <span className="pra-field__error">{fieldErrors[k]}</span>}
            </label>
          ))}
          <p className="pra-muted">Auth: OAuth 2.0 client credentials (a connected app with the client-credentials flow enabled and a
            run-as user limited to product, price book and CPQ objects). Secrets are Fernet-encrypted at rest.</p>
          <div className="pra-actions">
            <button type="button" className="pra-button pra-button--ghost" onClick={() => setEditing(null)}>Cancel</button>
            <button type="button" className="pra-button pra-button--primary" disabled={save.isPending} onClick={submit}>Save</button>
          </div>
        </section>
      )}

      <div className="pra-table" role="table">
        <div className="pra-table__head pra-connector__row" role="row">
          <span>Connector</span><span>Instance</span><span>Domains</span><span>Credentials</span><span>Last check</span><span />
        </div>
        {q.data && q.data.length === 0 && <div className="pra-table__empty">No connectors yet. Excel uploads remain available.</div>}
        {(q.data ?? []).map((c) => (
          <div key={c.id} role="row" className="pra-table__row pra-connector__row">
            <span><strong>{c.name}</strong><br /><span className="pra-muted">{c.key} · {c.system} · {c.status}</span></span>
            <span>{c.instanceUrl}<br /><span className="pra-muted">{c.apiVersion}</span></span>
            <span>{c.domains.map((d) => d.replace(/_/g, " ").toLowerCase()).join(", ")}</span>
            <span>{c.secretSet ? <span className="pra-pill pra-pill--success">Set</span> : <span className="pra-pill pra-pill--warning">Missing</span>}</span>
            <span>{c.lastCheck ? (
              <span className={`pra-pill pra-pill--${c.lastCheck.ok ? "success" : "danger"}`} title={c.lastCheck.problems.map((p) => p.message).join("; ")}>
                {c.lastCheck.ok ? "Reachable" : "Failed"}</span>) : <span className="pra-muted">Never</span>}
              {c.lastCheck && !c.lastCheck.ok && <><br /><span className="pra-muted">{c.lastCheck.problems[0]?.message}</span></>}</span>
            <span className="pra-connector__actions">
              {canManage && <>
                <button type="button" className="pra-button pra-button--ghost" disabled={test.isPending}
                  onClick={async () => { setError(null); try { await test.mutateAsync(c.id); } catch (e) { setError(errText(e)); } }}>Test</button>
                <button type="button" className="pra-button pra-button--ghost" onClick={() => edit(c)}>Edit</button>
                <button type="button" className="pra-button pra-button--ghost" disabled={remove.isPending}
                  onClick={async () => { setError(null); try { await remove.mutateAsync(c.id); } catch (e) { setError(errText(e)); } }}>Remove</button>
              </>}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
