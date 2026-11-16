"""Source adapters: one interface for every way evidence arrives (Excel now, live connectors later).

A run's source of a domain is read through ``SourceAdapter``: check it can be reached/opened,
discover its structure (sheets/objects + columns), and extract rows keyed by column header.
Mapping to the canonical schema (Step 5) works on what ``extract`` returns, whatever the source.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.domain.evidence import SourceType


@dataclass(frozen=True)
class ConnectionCheck:
    ok: bool
    problems: list[dict[str, str]] = field(default_factory=list)  # [{code, message}]


class SourceAdapter(Protocol):
    source_type: SourceType

    def test_connection(self) -> ConnectionCheck: ...

    def discover_schema(self) -> dict[str, Any]:
        """``{"default_sheet": str | None, "sheets": [...]}`` (see ``app.domain.excel_discovery``)."""
        ...

    def extract(self, sheet: str, header_row: int) -> Iterator[dict[str, Any]]:
        """Data rows of ``sheet`` as {header: value}, skipping empty rows. Each row has ``__row__``."""
        ...
