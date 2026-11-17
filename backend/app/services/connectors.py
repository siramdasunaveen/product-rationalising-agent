"""Live connectors (BUILD_PLAN step 15): approved source systems and pulls into a run's evidence.

A pull reads the domain's objects through the connector, writes the rows into the domain's governed Excel template
layout and hands the workbook to the normal intake (quarantine, checks, discovery, schema version), so mapping,
validation, reconciliation and snapshots treat live and Excel evidence the same way. Reconciliation then settles
Salesforce against Excel by the precedence policy (DOMAIN:SYSTEM).
"""

import json
import uuid
from datetime import UTC, datetime
from io import BytesIO
from typing import Any

from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.registry import Platform
from app.adapters.sources.salesforce import ConnectorUnavailable, SalesforceClient, SalesforceSourceAdapter
from app.db.models import Connector, ConnectorPull, EvidenceSource, EvidenceUpload, Run
from app.domain import connectors as cd
from app.domain.common import FieldError
from app.domain.evidence import DOMAINS, EvidenceDomain, SourceType
from app.domain.excel_checks import FileLimits
from app.ports.audit import AuditRecord
from app.ports.auth import Principal
from app.services import evidence as evidence_service
from app.services.errors import ServiceError
from app.services.outbox import emit


class ConnectorError(ServiceError):
    code = "connector_error"


class ConnectorNotFound(ConnectorError):
    status_code = 404
    code = "not_found"


class ConnectorValidation(ConnectorError):
    status_code = 422
    code = "validation_failed"


class ConnectorConflict(ConnectorError):
    status_code = 409
    code = "conflict"


class ConnectorFailed(ConnectorError):
    status_code = 502
    code = "connector_unavailable"


def _now() -> datetime:
    return datetime.now(UTC)


def _audit(session: Session, platform: Platform, principal: Principal, action: str, c: Connector,
           payload: dict[str, Any]) -> None:
    platform.audit.record(AuditRecord(
        tenant_id=principal.tenant_id, actor_id=principal.user_id, actor_roles=principal.roles,
        auth_context=principal.auth_context, action=action, entity_type="connector", entity_id=str(c.id),
        payload={"key": c.key, **payload}), session=session)


def to_dict(c: Connector) -> dict[str, Any]:
    return {"id": str(c.id), "key": c.key, "name": c.name, "system": c.system, "status": c.status,
            "instanceUrl": c.config.get("instanceUrl"), "apiVersion": c.config.get("apiVersion"),
            "authType": c.config.get("authType"), "queries": c.config.get("queries") or {},
            "domains": sorted(set(cd.SALESFORCE_QUERIES) | set(c.config.get("queries") or {})),
            "clientId": c.client_id, "secretSet": bool(c.secret_ciphertext), "lastCheck": c.last_check,
            "createdAt": c.created_at.isoformat(), "updatedAt": c.updated_at.isoformat(), "updatedBy": c.updated_by}


def list_connectors(session: Session, tenant_id: str) -> list[Connector]:
    return list(session.scalars(select(Connector).where(Connector.tenant_id == tenant_id).order_by(Connector.name)))


def get(session: Session, tenant_id: str, connector_id: uuid.UUID) -> Connector:
    c = session.scalar(select(Connector).where(Connector.id == connector_id, Connector.tenant_id == tenant_id))
    if c is None:
        raise ConnectorNotFound("Connector not found")
    return c


def save(session: Session, platform: Platform, principal: Principal, body: dict[str, Any],
         connector_id: uuid.UUID | None = None) -> Connector:
    errors = cd.validate_config(body)
    secret = body.get("clientSecret")
    if secret is not None and not 1 <= len(secret) <= 4000:
        errors.append(FieldError("clientSecret", "Enter the client secret (1-4000 characters)"))
    if errors:
        raise ConnectorValidation("The connector is not valid", errors)
    config = {"instanceUrl": body["instanceUrl"].rstrip("/"), "apiVersion": body.get("apiVersion") or cd.DEFAULT_API_VERSION,
              "authType": body.get("authType") or cd.AUTH_TYPES[0], "queries": body.get("queries") or {}}
    if connector_id is None:
        if session.scalar(select(Connector.id).where(Connector.tenant_id == principal.tenant_id,
                                                     Connector.key == body["key"])):
            raise ConnectorValidation("The connector is not valid", [FieldError("key", "This key is already used")])
        c = Connector(tenant_id=principal.tenant_id, key=body["key"], name=body["name"].strip(), system=body["system"],
                      config=config, client_id=body.get("clientId"), status="ACTIVE", created_by=principal.user_id,
                      updated_by=principal.user_id)
        session.add(c)
        action = "connector.created"
    else:
        c = get(session, principal.tenant_id, connector_id)
        if c.key != body["key"]:
            raise ConnectorValidation("The connector is not valid", [FieldError("key", "The key cannot change")])
        c.name, c.system, c.config = body["name"].strip(), body["system"], config
        c.client_id = body.get("clientId") if body.get("clientId") is not None else c.client_id
        c.status = body.get("status") or c.status
        c.updated_by, c.updated_at = principal.user_id, _now()
        action = "connector.updated"
    if secret:
        c.secret_ciphertext = platform.secrets.encrypt(secret)
    session.flush()
    _audit(session, platform, principal, action, c, {"system": c.system, "instance": config["instanceUrl"],
                                                     "secret_changed": bool(secret)})
    session.commit()
    return c


def delete(session: Session, platform: Platform, principal: Principal, connector_id: uuid.UUID) -> None:
    c = get(session, principal.tenant_id, connector_id)
    if session.scalar(select(ConnectorPull.id).where(ConnectorPull.connector_id == c.id).limit(1)):
        c.status, c.updated_by, c.updated_at = "DISABLED", principal.user_id, _now()  # keep lineage; disable instead
        _audit(session, platform, principal, "connector.disabled", c, {})
    else:
        _audit(session, platform, principal, "connector.deleted", c, {})
        session.delete(c)
    session.commit()


def _adapter(platform: Platform, c: Connector) -> SalesforceSourceAdapter:
    if c.system != "SALESFORCE":
        raise ConnectorFailed(f"No adapter for {c.system}")
    if not c.client_id or not c.secret_ciphertext:
        raise ConnectorConflict("Set the OAuth client id and secret first")
    client = SalesforceClient(c.config["instanceUrl"], c.client_id, platform.secrets.decrypt(c.secret_ciphertext),
                              c.config.get("apiVersion") or cd.DEFAULT_API_VERSION)
    return SalesforceSourceAdapter(client, c.config.get("queries"))


def test(session: Session, platform: Platform, principal: Principal, connector_id: uuid.UUID) -> dict[str, Any]:
    c = get(session, principal.tenant_id, connector_id)
    check = _adapter(platform, c).test_connection()
    c.last_check = {"ok": check.ok, "problems": check.problems, "at": _now().isoformat(), "by": principal.user_id}
    c.updated_at = _now()
    _audit(session, platform, principal, "connector.tested", c, {"ok": check.ok})
    session.commit()
    return c.last_check


def _connector_source(session: Session, principal: Principal, run: Run, domain: EvidenceDomain,
                      c: Connector) -> EvidenceSource:
    name = f"{c.name} · {DOMAINS[domain].label}"[:120]
    src = next((s for s in evidence_service.sources_of(session, run, domain)
                if s.source_type == SourceType.API_CONNECTOR.value and s.name == name), None)
    if src is None:
        src = EvidenceSource(tenant_id=run.tenant_id, run_id=run.id, domain=domain.value,
                             source_type=SourceType.API_CONNECTOR.value, name=name, system=c.system,
                             created_by=principal.user_id, created_at=_now())
        session.add(src)
        session.flush()
    return src


def _workbook(headers: list[str], rows: list[dict[str, Any]]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Data"
    ws.append(headers)
    for r in rows:
        ws.append([_cell(r.get(h)) for h in headers])
    out = BytesIO()
    wb.save(out)
    return out.getvalue()


def _cell(v: Any) -> Any:
    if isinstance(v, bool | int | float | str) or v is None:
        return v
    return json.dumps(v, sort_keys=True)


def pull(session: Session, platform: Platform, limits: FileLimits, principal: Principal, run_id: uuid.UUID,
         domain_value: str, connector_id: uuid.UUID) -> tuple[ConnectorPull, EvidenceUpload]:
    """Extract the domain from the connector into the run as a checked, discovered upload (ready to map)."""
    domain = evidence_service._domain(domain_value)
    run = evidence_service._run(session, principal.tenant_id, run_id)
    c = get(session, principal.tenant_id, connector_id)
    if c.status != "ACTIVE":
        raise ConnectorConflict("This connector is disabled")
    q = cd.query_for(domain.value, c.config.get("queries"))
    if q is None:
        raise ConnectorConflict(f"{c.name} has no query for {DOMAINS[domain].label}")
    adapter = _adapter(platform, c)
    record = ConnectorPull(tenant_id=run.tenant_id, connector_id=c.id, run_id=run.id, domain=domain.value,
                           status="FAILED", rows=0, soql=cd.soql(q), requested_by=principal.user_id)
    session.add(record)
    try:
        rows = []
        for row in adapter.extract(domain.value):
            rows.append(row)
            if len(rows) > limits.max_rows:
                raise ConnectorUnavailable(f"The pull exceeds the {limits.max_rows:,} row limit")
    except ConnectorUnavailable as exc:
        record.error = str(exc)[:2000]
        session.commit()
        raise ConnectorFailed(str(exc)) from exc
    headers = list(q["columns"])
    data = _workbook(headers, rows)
    source = _connector_source(session, principal, run, domain, c)
    stamp = _now().strftime("%Y%m%d-%H%M%S")
    result = evidence_service.upload_file(session, platform, limits, principal, run.id, domain.value,
                                          f"{c.key}-{domain.value.lower()}-{stamp}.xlsx", data, source.id)
    upload = result.upload
    if not result.duplicate:
        upload = evidence_service.process_upload(session, platform, limits, upload.id)
        if upload.status != "REJECTED":
            evidence_service.confirm_schema(session, platform, limits, principal, run.id, upload.id, "Data", 1)
            upload = session.get(EvidenceUpload, upload.id)
    record.upload_id, record.rows, record.status = upload.id, len(rows), "SUCCEEDED"
    _audit(session, platform, principal, "connector.pulled", c, {"run_id": str(run.id), "domain": domain.value,
                                                                 "rows": len(rows), "upload_id": str(upload.id)})
    emit(session, tenant_id=run.tenant_id, event_type="ConnectorPulled", aggregate_type="run",
         aggregate_id=str(run.id), payload={"connector": c.key, "domain": domain.value, "rows": len(rows),
                                            "upload_id": str(upload.id)})
    session.commit()
    return record, upload


def pulls(session: Session, run: Run) -> list[ConnectorPull]:
    return list(session.scalars(select(ConnectorPull).where(ConnectorPull.run_id == run.id,
                                                            ConnectorPull.tenant_id == run.tenant_id)
                                .order_by(ConnectorPull.created_at.desc())))
