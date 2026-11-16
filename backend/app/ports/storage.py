from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class StorageArea(StrEnum):
    QUARANTINE = "quarantine"  # uploaded, not yet checked
    CHECKED = "checked"  # passed file checks, usable


@dataclass(frozen=True)
class StoredObject:
    key: str  # opaque key, includes tenant and area
    size: int
    sha256: str


class ObjectStorage(Protocol):
    def put(self, tenant_id: str, name: str, data: bytes, area: StorageArea = StorageArea.QUARANTINE) -> StoredObject: ...

    def get(self, key: str) -> bytes: ...

    def promote(self, key: str) -> StoredObject:
        """Move an object from quarantine to checked; returns the new object."""
        ...

    def delete(self, key: str) -> None: ...
