"""Local malware scanner stub (DECISIONS.md: scanner port, ClamAV or the studio's service later).

It only detects the standard EICAR anti-virus test file, so the quarantine flow can be tested end to
end. It is NOT protection: production must plug in a real scanner behind ``MalwareScanner``.
"""

import zipfile
from io import BytesIO

from app.ports.scanner import ScanResult

EICAR = b"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
ENGINE = "local-eicar-stub/1"


class EicarTestScanner:
    def scan(self, data: bytes, *, filename: str) -> ScanResult:
        if EICAR in data:
            return ScanResult(clean=False, engine=ENGINE, signature="EICAR-Test-File")
        try:  # also look inside the zip package (an .xlsx is a zip)
            with zipfile.ZipFile(BytesIO(data)) as zf:
                for info in zf.infolist():
                    if info.file_size <= 5 * 1024 * 1024 and EICAR in zf.read(info):
                        return ScanResult(clean=False, engine=ENGINE, signature="EICAR-Test-File")
        except zipfile.BadZipFile:
            pass
        return ScanResult(clean=True, engine=ENGINE)
