"""Quick safety checks on an uploaded workbook, before it is stored (CLAUDE.md rule 12).

Pure functions over bytes; no parsing of cell data here (see ``excel_discovery``). The checks do
not trust the file name or the declared content type: an .xlsx is a zip package with specific parts.

Rejected: not .xlsx, too large, legacy .xls, password-protected (encrypted OLE package), not a valid
zip, zip bombs (unpacked size / number of parts), missing workbook parts, macros (VBA project),
too many sheets.
"""

import re
import zipfile
from dataclasses import dataclass
from io import BytesIO

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # Compound File: legacy .xls or an encrypted .xlsx
ZIP_MAGIC = b"PK\x03\x04"
ENCRYPTED_MARKERS = ("EncryptedPackage".encode("utf-16-le"), "EncryptionInfo".encode("utf-16-le"))
# VBA parts only: ordinary workbooks may contain other .bin parts (e.g. printer settings).
MACRO_PARTS = re.compile(r"(^|/)vba(Project|ProjectSignature|ProjectSignatureAgile)\.bin$|(^|/)vbaData\.xml$",
                         re.IGNORECASE)
REQUIRED_PARTS = ("[Content_Types].xml", "xl/workbook.xml")
ALLOWED_EXTENSIONS = (".xlsx",)


@dataclass(frozen=True)
class FileLimits:
    max_bytes: int = 10 * 1024 * 1024
    max_sheets: int = 20
    max_rows: int = 100_000
    max_columns: int = 500
    max_unpacked_bytes: int = 200 * 1024 * 1024
    max_parts: int = 2000


@dataclass(frozen=True)
class Problem:
    code: str
    message: str


def _mb(n: int) -> str:
    return f"{n / (1024 * 1024):.0f} MB"


def check_workbook_file(filename: str, data: bytes, limits: FileLimits) -> list[Problem]:
    """All reasons to reject the file outright. Empty list = safe to quarantine and scan."""
    name = (filename or "").strip()
    lower = name.lower()
    if lower.endswith((".xlsm", ".xltm", ".xlam")):
        return [Problem("macro_enabled", "Macro-enabled workbooks (.xlsm) are not accepted. Save as .xlsx.")]
    if lower.endswith(".xls"):
        return [Problem("legacy_format", "Old .xls workbooks are not accepted. Save as .xlsx.")]
    if not lower.endswith(ALLOWED_EXTENSIONS):
        return [Problem("not_xlsx", "Only Excel .xlsx files are accepted.")]
    if not data:
        return [Problem("empty", "The file is empty.")]
    if len(data) > limits.max_bytes:
        return [Problem("too_large", f"The file is {_mb(len(data))}; the limit is {_mb(limits.max_bytes)}.")]

    if data.startswith(OLE_MAGIC):
        if any(marker in data for marker in ENCRYPTED_MARKERS):
            return [Problem("password_protected",
                            "The workbook is password-protected. Remove the password and upload again.")]
        return [Problem("legacy_format", "This is an old .xls workbook renamed to .xlsx. Save it as .xlsx.")]
    if not data.startswith(ZIP_MAGIC):
        return [Problem("malformed", "The file is not a valid .xlsx workbook.")]

    try:
        with zipfile.ZipFile(BytesIO(data)) as zf:
            infos = zf.infolist()
            names = {i.filename for i in infos}
            if len(infos) > limits.max_parts:
                return [Problem("too_complex", f"The workbook has more than {limits.max_parts} internal parts.")]
            unpacked = sum(i.file_size for i in infos)
            if unpacked > limits.max_unpacked_bytes:
                return [Problem("too_large_unpacked",
                                f"The workbook unpacks to {_mb(unpacked)}; the limit is "
                                f"{_mb(limits.max_unpacked_bytes)}.")]
            if any(i.flag_bits & 0x1 for i in infos):
                return [Problem("password_protected", "The workbook is encrypted.")]
            if any(MACRO_PARTS.search(n) for n in names):
                return [Problem("macros", "The workbook contains macros (VBA). Remove them and save as .xlsx.")]
            missing = [p for p in REQUIRED_PARTS if p not in names]
            if missing:
                return [Problem("malformed", "The file is not a valid .xlsx workbook (missing "
                                + ", ".join(missing) + ").")]
            content_types = zf.read("[Content_Types].xml").decode("utf-8", errors="replace")
            if "macroEnabled" in content_types or "vbaProject" in content_types:
                return [Problem("macros", "The workbook is macro-enabled. Save as a plain .xlsx.")]
            workbook_xml = zf.read("xl/workbook.xml").decode("utf-8", errors="replace")
    except zipfile.BadZipFile:
        return [Problem("malformed", "The file is damaged and cannot be opened.")]
    except (KeyError, RuntimeError, NotImplementedError) as exc:
        return [Problem("malformed", f"The file cannot be read ({type(exc).__name__}).")]

    sheets = len(re.findall(r"<(?:\w+:)?sheet\s", workbook_xml))
    if sheets == 0:
        return [Problem("malformed", "The workbook has no worksheets.")]
    if sheets > limits.max_sheets:
        return [Problem("too_many_sheets", f"The workbook has {sheets} sheets; the limit is {limits.max_sheets}.")]
    return []
