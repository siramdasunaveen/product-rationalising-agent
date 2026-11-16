"""Disk storage with quarantine/checked areas. Layout: ``<root>/<tenant>/<area>/<uuid>/<name>``."""

import hashlib
import re
import shutil
import uuid
from pathlib import Path

from app.ports.storage import StorageArea, StoredObject

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe(part: str) -> str:
    cleaned = _SAFE.sub("_", part).strip("._")
    if not cleaned:
        raise ValueError("Empty name")
    return cleaned[:150]


class DiskStorage:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self._root / key).resolve()
        if not path.is_relative_to(self._root):
            raise ValueError("Invalid storage key")
        return path

    def put(self, tenant_id: str, name: str, data: bytes,
            area: StorageArea = StorageArea.QUARANTINE) -> StoredObject:
        key = f"{_safe(tenant_id)}/{area.value}/{uuid.uuid4().hex}/{_safe(name)}"
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return StoredObject(key=key, size=len(data), sha256=hashlib.sha256(data).hexdigest())

    def get(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise FileNotFoundError(key)
        return path.read_bytes()

    def promote(self, key: str) -> StoredObject:
        tenant, area, oid, name = key.split("/")
        if area != StorageArea.QUARANTINE.value:
            raise ValueError("Only quarantined objects can be promoted")
        new_key = f"{tenant}/{StorageArea.CHECKED.value}/{oid}/{name}"
        src, dst = self._path(key), self._path(new_key)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(src, dst)
        src.parent.rmdir()
        data = dst.read_bytes()
        return StoredObject(key=new_key, size=len(data), sha256=hashlib.sha256(data).hexdigest())

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.is_file():
            path.unlink()
            try:
                path.parent.rmdir()
            except OSError:
                pass
