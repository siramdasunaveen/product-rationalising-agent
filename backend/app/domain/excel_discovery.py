"""Read a checked workbook: sheets, header row, columns with types, row counts and 5 sample rows.

Deterministic (no AI). Runs on files that passed ``excel_checks``. Streams rows (openpyxl read-only),
so memory stays bounded; only the first ``HEADER_SCAN_ROWS`` rows are buffered.

Blocking file problems (the upload is rejected):
- a sheet has more data rows or columns than allowed;
- columns or values look like subscriber or personal data (CLAUDE.md rule 12: metadata and aggregates only).
Blocking sheet problems (that sheet cannot be confirmed): no header row, no data rows, duplicate headers.
"""

import math
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from io import BytesIO
from typing import Any

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from app.domain.excel_checks import FileLimits, Problem

HEADER_SCAN_ROWS = 50  # header must be in the first 50 rows
AUTO_HEADER_ROWS = 20  # automatic detection looks at the first 20
SAMPLE_ROWS = 5
PII_VALUE_SCAN_ROWS = 1000

# Header names that indicate subscriber / personal data. Aggregates ("Subscriber Count") are allowed.
PII_HEADER = re.compile(
    r"\b(msisdn|imsi|imei|iccid|e ?mail( address)?|phone( number| no)?|telephone|mobile (number|no)"
    r"|customer (name|id|number|no)|subscriber (id|name|number|no)|first ?name|last ?name|surname|full name"
    r"|given name|date of birth|dob|birth ?date|national id|passport( number| no)?|ssn|social security"
    r"|(home|street|postal|billing) address|address line|post ?code|zip ?code|iban|card number"
    r"|(billing )?account (number|no|id))\b")
AGGREGATE_HINT = re.compile(r"\b(count|total|number of|volume|qty|quantity|avg|average|share|pct|percent)\b")
EMAIL_VALUE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


@dataclass
class ColumnInfo:
    index: int  # 0-based position in the sheet
    letter: str
    header: str
    type: str  # string | integer | decimal | boolean | date | datetime | empty
    empty_pct: int  # share of data rows without a value, 0..100


@dataclass
class SheetInfo:
    name: str
    hidden: bool
    header_row: int | None  # 1-based
    data_rows: int
    columns: list[ColumnInfo] = field(default_factory=list)
    samples: list[list[str | None]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)  # blocking for confirming this sheet

    @property
    def valid(self) -> bool:
        return not self.problems

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"valid": self.valid}


@dataclass
class WorkbookInfo:
    sheets: list[SheetInfo]
    problems: list[Problem]  # blocking for the whole file

    @property
    def default_sheet(self) -> str | None:
        for s in self.sheets:
            if s.valid and not s.hidden:
                return s.name
        return next((s.name for s in self.sheets if s.valid), None)

    def to_dict(self) -> dict[str, Any]:
        return {"default_sheet": self.default_sheet, "sheets": [s.to_dict() for s in self.sheets]}


def normalise_header(value: Any) -> str:
    return " ".join(str(value).split()) if value is not None else ""


def _norm_for_match(header: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", header.casefold()).strip()


def looks_personal(header: str) -> bool:
    h = _norm_for_match(header)
    return bool(PII_HEADER.search(h)) and not AGGREGATE_HINT.search(h)


def _blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _kind(v: Any) -> str:
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer"
    if isinstance(v, (float, Decimal)):
        return "integer" if float(v).is_integer() else "decimal"
    if isinstance(v, datetime):
        return "date" if v.time() == time(0, 0) else "datetime"
    if isinstance(v, date):
        return "date"
    return "string"


def _combine(kinds: set[str]) -> str:
    if not kinds:
        return "empty"
    if len(kinds) == 1:
        return next(iter(kinds))
    if kinds <= {"integer", "decimal"}:
        return "decimal"
    if kinds <= {"date", "datetime"}:
        return "datetime"
    return "string"


def as_text(v: Any) -> str | None:
    """JSON-safe text for previews."""
    if _blank(v):
        return None
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, datetime):
        return v.date().isoformat() if v.time() == time(0, 0) else v.isoformat(sep=" ")
    if isinstance(v, (date, time)):
        return v.isoformat()
    if isinstance(v, float):
        return repr(v) if not v.is_integer() else str(int(v))
    return str(v).strip()


def detect_header_row(rows: list[tuple]) -> int | None:
    """1-based index of the first row that looks like a header, or None.

    A header row has mostly text labels and at least half as many filled cells as the widest of the
    scanned rows (so a title line such as "Catalog export" is skipped). Duplicate labels do not stop
    detection; they are reported as a problem of the sheet.
    """
    scanned = rows[:AUTO_HEADER_ROWS]
    filled = [[c for c in r if not _blank(c)] for r in scanned]
    widest = max((len(f) for f in filled), default=0)
    if widest == 0:
        return None
    need = max(1, math.ceil(widest / 2))
    for i, cells in enumerate(filled):
        if len(cells) < need:
            continue
        texts = [c for c in cells if isinstance(c, str)]
        if len(texts) < 0.9 * len(cells):
            continue
        labels = [normalise_header(c).casefold() for c in texts]
        if all(re.fullmatch(r"[\d.,\s-]+", lbl) for lbl in labels):
            continue
        return i + 1
    return None


class _ColumnStats:
    def __init__(self) -> None:
        self.kinds: set[str] = set()
        self.filled = 0
        self.email_like = False


def _read_sheet(name: str, hidden: bool, rows_iter, limits: FileLimits,
                header_row: int | None) -> tuple[SheetInfo, list[Problem]]:
    problems: list[Problem] = []
    buffer: list[tuple] = []
    it = iter(rows_iter)
    for row in it:
        buffer.append(tuple(row))
        if len(buffer) >= HEADER_SCAN_ROWS:
            break

    hr = header_row if header_row is not None else detect_header_row(buffer)
    sheet = SheetInfo(name=name, hidden=hidden, header_row=hr, data_rows=0)
    if hidden:
        sheet.warnings.append("Hidden sheet")
    if hr is None or hr > len(buffer) or all(_blank(c) for c in buffer[hr - 1]):
        sheet.header_row = None
        sheet.problems.append("No header row found" if header_row is None
                              else f"Row {header_row} is empty or outside the first {HEADER_SCAN_ROWS} rows")
        return sheet, problems

    header = buffer[hr - 1]
    positions = [i for i, c in enumerate(header) if not _blank(c)]
    if len(positions) > limits.max_columns:
        problems.append(Problem("too_many_columns",
                                f"Sheet '{name}' has {len(positions)} columns; the limit is {limits.max_columns}."))
        return sheet, problems
    headers = {i: normalise_header(header[i]) for i in positions}
    seen: dict[str, str] = {}
    for i in positions:
        key = headers[i].casefold()
        if key in seen:
            sheet.problems.append(f"Duplicate header '{headers[i]}' (columns {seen[key]} and "
                                  f"{get_column_letter(i + 1)})")
        else:
            seen[key] = get_column_letter(i + 1)
    if hr > 1:
        sheet.warnings.append(f"Header is on row {hr}; the {hr - 1} row(s) above it are ignored")

    stats = {i: _ColumnStats() for i in positions}
    unlabeled_data = 0
    data_rows = 0

    def consume(row: tuple) -> bool:
        nonlocal data_rows, unlabeled_data
        if all(_blank(c) for c in row):
            return True
        data_rows += 1
        if data_rows > limits.max_rows:
            return False
        for i, c in enumerate(row):
            if _blank(c):
                continue
            st = stats.get(i)
            if st is None:
                unlabeled_data += 1
                continue
            st.filled += 1
            st.kinds.add(_kind(c))
            if data_rows <= PII_VALUE_SCAN_ROWS and isinstance(c, str) and EMAIL_VALUE.match(c.strip()):
                st.email_like = True
        if len(sheet.samples) < SAMPLE_ROWS:
            sheet.samples.append([as_text(row[i]) if i < len(row) else None for i in positions])
        return True

    ok = True
    for row in buffer[hr:]:
        ok = consume(row)
        if not ok:
            break
    if ok:
        for row in it:
            if not consume(tuple(row)):
                ok = False
                break
    if not ok:
        problems.append(Problem("too_many_rows",
                                f"Sheet '{name}' has more than {limits.max_rows:,} data rows."))
        return sheet, problems

    sheet.data_rows = data_rows
    personal = []
    for i in positions:
        st = stats[i]
        col = ColumnInfo(index=i, letter=get_column_letter(i + 1), header=headers[i], type=_combine(st.kinds),
                         empty_pct=0 if data_rows == 0 else round(100 * (data_rows - st.filled) / data_rows))
        sheet.columns.append(col)
        if looks_personal(col.header):
            personal.append(f"{col.letter} '{col.header}'")
        elif st.email_like:
            personal.append(f"{col.letter} '{col.header}' (e-mail addresses)")
    if personal:
        problems.append(Problem("personal_data",
                                f"Sheet '{name}' looks like it holds subscriber or personal data in columns "
                                + ", ".join(personal) + ". Only product metadata and aggregates are allowed; "
                                "remove these columns and upload again."))
    if data_rows == 0:
        sheet.problems.append("No data rows under the header")
    if unlabeled_data:
        sheet.warnings.append(f"{unlabeled_data} value(s) are in columns without a header and will be ignored")
    empty = [c.header for c in sheet.columns if c.type == "empty"]
    if empty and data_rows:
        sheet.warnings.append("Columns with no values: " + ", ".join(empty))
    return sheet, problems


def discover_workbook(data: bytes, limits: FileLimits, *, sheet: str | None = None,
                      header_row: int | None = None) -> WorkbookInfo:
    """Discover every sheet (or only ``sheet``, optionally with a forced ``header_row``)."""
    try:
        wb = load_workbook(BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 - openpyxl raises many types for damaged files
        return WorkbookInfo([], [Problem("malformed", f"The workbook cannot be read ({type(exc).__name__}).")])
    sheets: list[SheetInfo] = []
    problems: list[Problem] = []
    try:
        for ws in wb.worksheets:
            if sheet is not None and ws.title != sheet:
                continue
            hidden = getattr(ws, "sheet_state", "visible") != "visible"
            info, p = _read_sheet(ws.title, hidden, ws.iter_rows(values_only=True), limits,
                                  header_row if sheet is not None else None)
            sheets.append(info)
            problems += p
    except Exception as exc:  # noqa: BLE001
        problems.append(Problem("malformed", f"The workbook cannot be read ({type(exc).__name__})."))
    finally:
        wb.close()
    if sheet is not None and not sheets:
        problems.append(Problem("unknown_sheet", f"No sheet named '{sheet}'"))
    return WorkbookInfo(sheets, problems)
