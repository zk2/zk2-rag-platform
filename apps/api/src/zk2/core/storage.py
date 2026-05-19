"""Storage abstraction. Local (file-system) for dev; S3 stub for prod."""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from pathlib import Path

from zk2.config import get_settings


class StorageBackend(ABC):
    @abstractmethod
    async def save(self, org_id: int, content: bytes, *, suffix: str = "") -> str:
        """Persist `content` for the org, return an opaque key."""

    @abstractmethod
    async def read(self, key: str) -> bytes:
        ...

    @abstractmethod
    async def delete(self, key: str) -> None:
        ...


class LocalStorage(StorageBackend):
    """Files under `<root>/<org_id>/<uuid>[.ext]`. The key is the path relative to root."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    async def save(self, org_id: int, content: bytes, *, suffix: str = "") -> str:
        org_dir = self.root / str(org_id)
        org_dir.mkdir(parents=True, exist_ok=True)
        name = f"{uuid.uuid4().hex}{suffix}"
        path = org_dir / name
        path.write_bytes(content)
        return str(path.relative_to(self.root))

    async def read(self, key: str) -> bytes:
        path = self.root / key
        if not path.is_file():
            raise FileNotFoundError(key)
        return path.read_bytes()

    async def delete(self, key: str) -> None:
        path = self.root / key
        if path.is_file():
            path.unlink()


_storage: StorageBackend | None = None


def get_storage() -> StorageBackend:
    global _storage
    if _storage is None:
        s = get_settings().storage
        if s.kind == "local":
            _storage = LocalStorage(Path(s.local_upload_dir).resolve())
        else:
            msg = f"Storage kind {s.kind!r} not implemented yet"
            raise NotImplementedError(msg)
    return _storage
