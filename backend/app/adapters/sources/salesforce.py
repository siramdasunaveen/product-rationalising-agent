"""Salesforce source adapter (BUILD_PLAN step 15): OAuth 2.0 client-credentials flow and SOQL over the REST API.

Reads catalogue metadata only (Product2, PricebookEntry, CPQ product options by default); no subscriber-level
objects are ever queried. Tokens live in memory for the request; the client secret comes from the Fernet-
encrypted connector record. Any HTTP or auth failure raises ``ConnectorUnavailable`` with a safe message.
"""

from collections.abc import Iterator
from typing import Any

import httpx

from app.domain import connectors as cd
from app.domain.evidence import SourceType
from app.ports.connectors import ConnectionCheck

# Tests inject an httpx transport here (module hook); production uses the default transport.
TRANSPORT: httpx.BaseTransport | None = None
PAGE_LIMIT = 200  # pages per pull (200 x 2000 records): a hard stop against runaway queries


class ConnectorUnavailable(Exception):
    pass


class SalesforceClient:
    def __init__(self, instance_url: str, client_id: str, client_secret: str, api_version: str = cd.DEFAULT_API_VERSION,
                 *, timeout: float = 30.0, transport: httpx.BaseTransport | None = None) -> None:
        self.base = instance_url.rstrip("/")
        self.api = api_version
        self._id, self._secret = client_id, client_secret
        self._client = httpx.Client(timeout=timeout, transport=transport or TRANSPORT)
        self._token: str | None = None

    def _auth(self) -> str:
        if self._token:
            return self._token
        try:
            r = self._client.post(f"{self.base}/services/oauth2/token", data={
                "grant_type": "client_credentials", "client_id": self._id, "client_secret": self._secret})
        except httpx.HTTPError as exc:
            raise ConnectorUnavailable(f"Cannot reach Salesforce: {type(exc).__name__}") from exc
        if r.status_code != 200:
            raise ConnectorUnavailable(f"Salesforce refused the credentials (HTTP {r.status_code})")
        token = r.json().get("access_token") if r.headers.get("content-type", "").startswith("application/json") else None
        if not token:
            raise ConnectorUnavailable("Salesforce did not return an access token")
        self._token = token
        return token

    def _get(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._auth()}"}
        url = path if path.startswith("http") else f"{self.base}{path}"
        try:
            r = self._client.get(url, params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise ConnectorUnavailable(f"Salesforce request failed: {type(exc).__name__}") from exc
        if r.status_code == 401:
            raise ConnectorUnavailable("Salesforce rejected the access token")
        if r.status_code >= 300:
            detail = ""
            try:
                detail = (r.json()[0] or {}).get("errorCode", "")
            except (ValueError, IndexError, KeyError, TypeError, AttributeError):
                pass
            raise ConnectorUnavailable(f"Salesforce returned HTTP {r.status_code} {detail}".strip())
        try:
            return r.json()
        except ValueError as exc:
            raise ConnectorUnavailable("Salesforce did not return JSON") from exc

    def limits(self) -> dict[str, Any]:
        return self._get(f"/services/data/{self.api}/limits")

    def describe(self, sobject: str) -> dict[str, Any]:
        return self._get(f"/services/data/{self.api}/sobjects/{sobject}/describe")

    def query(self, soql: str) -> Iterator[dict[str, Any]]:
        data = self._get(f"/services/data/{self.api}/query", {"q": soql})
        pages = 0
        while True:
            for rec in data.get("records", []):
                yield rec
            if data.get("done", True) or not data.get("nextRecordsUrl"):
                return
            pages += 1
            if pages >= PAGE_LIMIT:
                raise ConnectorUnavailable("The query returned more pages than the pull limit allows")
            data = self._get(data["nextRecordsUrl"])


class SalesforceSourceAdapter:
    """``SourceAdapter`` over a connector: sheets are the evidence domains the connector can serve."""

    source_type = SourceType.API_CONNECTOR

    def __init__(self, client: SalesforceClient, queries: dict[str, Any] | None = None) -> None:
        self.client = client
        self.queries = queries or {}

    def test_connection(self) -> ConnectionCheck:
        try:
            self.client.limits()
        except ConnectorUnavailable as exc:
            return ConnectionCheck(ok=False, problems=[{"code": "unreachable", "message": str(exc)}])
        return ConnectionCheck(ok=True)

    def discover_schema(self) -> dict[str, Any]:
        sheets = []
        for domain in cd.SALESFORCE_QUERIES.keys() | set(self.queries):
            q = cd.query_for(domain, self.queries)
            sheets.append({"name": domain, "object": q["object"], "columns": list(q["columns"])})
        return {"default_sheet": "CRM_CATALOG", "sheets": sorted(sheets, key=lambda s: s["name"])}

    def extract(self, sheet: str, header_row: int = 1) -> Iterator[dict[str, Any]]:
        q = cd.query_for(sheet, self.queries)
        if q is None:
            raise ConnectorUnavailable(f"No Salesforce query is defined for {sheet}")
        for n, rec in enumerate(self.client.query(cd.soql(q)), start=2):
            yield {"__row__": n, **cd.to_row(rec, q)}
