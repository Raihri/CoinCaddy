"""Private storage for original uploaded files.

Files live under STORAGE_DIR (default <repo>/storage/private), outside anything the web servers
serve, named by business and batch ID rather than by the user's filename. No route reads them back.
"""

import os
from pathlib import Path
from uuid import UUID

from app.config import REPO_ROOT, get_settings


def root() -> Path:
    configured = get_settings().storage_dir
    return Path(configured).resolve() if configured else (REPO_ROOT / "storage" / "private").resolve()


def save_original(business_id: UUID, batch_id: UUID, file_type: str, data: bytes) -> str:
    key = f"{business_id}/{batch_id}.{file_type}"
    path = (root() / key).resolve()
    if root() not in path.parents:
        raise ValueError("storage key escapes the storage root")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
    return key


def remove_original(key: str) -> None:
    path = (root() / key).resolve()
    if root() in path.parents and path.exists():
        path.unlink()


def original_exists(key: str) -> bool:
    return (root() / key).resolve().exists()
