from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ScanResult:
    clean: bool
    engine: str  # scanner name + version, kept for audit
    signature: str | None = None  # what was found, if not clean


class ScannerUnavailable(Exception):
    """The scanner could not be reached; the file stays in quarantine and the scan is retried."""


class MalwareScanner(Protocol):
    def scan(self, data: bytes, *, filename: str) -> ScanResult: ...
