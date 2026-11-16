"""Workbooks generated in code for tests: good ones, and every kind of bad file the intake must reject."""

import zipfile
from io import BytesIO
from typing import Any

from openpyxl import Workbook, load_workbook

from app.adapters.local.scanner import EICAR
from app.domain.excel_checks import OLE_MAGIC


def xlsx(sheets: dict[str, list[list[Any]]], *, hidden: tuple[str, ...] = ()) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for row in rows:
            ws.append(row)
        if name in hidden:
            ws.sheet_state = "hidden"
    out = BytesIO()
    wb.save(out)
    return out.getvalue()


def fill_template(template: bytes, rows: list[list[Any]]) -> bytes:
    wb = load_workbook(BytesIO(template))
    ws = wb["Data"]
    for row in rows:
        ws.append(row)
    out = BytesIO()
    wb.save(out)
    return out.getvalue()


def _rezip(data: bytes, extra: dict[str, bytes] | None = None, *, replace: dict[str, bytes] | None = None) -> bytes:
    src = zipfile.ZipFile(BytesIO(data))
    out = BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            body = (replace or {}).get(info.filename, src.read(info))
            dst.writestr(info.filename, body)
        for name, body in (extra or {}).items():
            dst.writestr(name, body)
    return out.getvalue()


GOOD = [["Product ID", "Product Name"], ["P1", "Fibre 100"]]


def with_macros() -> bytes:
    return _rezip(xlsx({"Data": GOOD}), {"xl/vbaProject.bin": b"\x00VBA"})


def with_printer_settings() -> bytes:
    return _rezip(xlsx({"Data": GOOD}), {"xl/printerSettings/printerSettings1.bin": b"\x00\x01"})


def encrypted() -> bytes:
    # An encrypted .xlsx is an OLE compound file holding EncryptionInfo + EncryptedPackage streams.
    return OLE_MAGIC + b"\x00" * 500 + "EncryptedPackage".encode("utf-16-le") + b"\x00" * 100


def legacy_xls() -> bytes:
    return OLE_MAGIC + b"\x00" * 500 + "Workbook".encode("utf-16-le")


def not_a_zip() -> bytes:
    return b"Product ID,Product Name\nP1,Fibre\n"


def broken_zip() -> bytes:
    return xlsx({"Data": GOOD})[:300]


def zip_bomb(unpacked_bytes: int) -> bytes:
    return _rezip(xlsx({"Data": GOOD}), {"xl/media/padding.bin.txt": b"0" * unpacked_bytes})


def many_sheets(n: int) -> bytes:
    return xlsx({f"S{i}": GOOD for i in range(n)})


def eicar_inside() -> bytes:
    return _rezip(xlsx({"Data": GOOD}), {"docProps/custom.xml": EICAR})
