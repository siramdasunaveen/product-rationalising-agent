"""Excel (.xlsx) source adapter over a checked workbook held in memory."""

from collections.abc import Iterator
from io import BytesIO
from typing import Any

from openpyxl import load_workbook

from app.domain.evidence import SourceType
from app.domain.excel_checks import FileLimits, check_workbook_file
from app.domain.excel_discovery import discover_workbook, normalise_header
from app.ports.connectors import ConnectionCheck


class ExcelSourceAdapter:
    source_type = SourceType.EXCEL_UPLOAD

    def __init__(self, filename: str, data: bytes, limits: FileLimits) -> None:
        self.filename = filename
        self.data = data
        self.limits = limits

    def test_connection(self) -> ConnectionCheck:
        problems = check_workbook_file(self.filename, self.data, self.limits)
        return ConnectionCheck(ok=not problems, problems=[{"code": p.code, "message": p.message} for p in problems])

    def discover_schema(self) -> dict[str, Any]:
        return discover_workbook(self.data, self.limits).to_dict()

    def extract(self, sheet: str, header_row: int) -> Iterator[dict[str, Any]]:
        wb = load_workbook(BytesIO(self.data), read_only=True, data_only=True)
        try:
            ws = wb[sheet]
            headers: dict[int, str] = {}
            for n, row in enumerate(ws.iter_rows(values_only=True), start=1):
                if n < header_row:
                    continue
                if n == header_row:
                    headers = {i: normalise_header(v) for i, v in enumerate(row) if normalise_header(v)}
                    continue
                values = {h: (row[i] if i < len(row) else None) for i, h in headers.items()}
                if all(v is None or (isinstance(v, str) and not v.strip()) for v in values.values()):
                    continue
                yield {"__row__": n, **values}
        finally:
            wb.close()
