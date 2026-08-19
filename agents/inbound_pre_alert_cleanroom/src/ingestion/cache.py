"""What has already been read, so it is not read again.

Extraction is the expensive part of this process by two orders of magnitude: a
shipment is a dozen attachments and a scanned certificate costs a vision call
per page. The suite runs every case against every build and the mailbox does not
change between them, so re-reading it would make measuring a patch cost more
than writing one.

Keyed on the attachment *and* on the schema and prompt that read it, because a
spec revision must invalidate what a previous spec's schema produced. The
alternative is a cache that quietly serves the old shape forever.

Derived data, never the oracle. It lives under the agent directory and is
ignored by git; nothing here writes where a result is measured.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

CACHE_DIR = Path(__file__).resolve().parent.parent.parent / ".cache"
BLOB_DIR = CACHE_DIR / "blobs"


def key_for(*parts: str) -> str:
    """A stable name for one cached answer."""
    return hashlib.sha256(" ".join(parts).encode()).hexdigest()[:32]


def read(key: str) -> Any | None:
    """What was cached under this key, or ``None``.

    A corrupt entry is treated as absent rather than raised on: re-reading one
    attachment costs a model call, and failing a whole sweep over it costs a
    measurement.
    """
    path = CACHE_DIR / f"{key}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def write(key: str, value: Any) -> None:
    """Remember one answer. Failing to cache is never failing to run."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        (CACHE_DIR / f"{key}.json").write_text(json.dumps(value))
    except OSError:
        return


def read_bytes(key: str) -> bytes | None:
    """An attachment already downloaded, or ``None``.

    Bytes are cached separately from the answers derived from them because they
    are invalidated by different things: a spec revision changes what a document
    *means* and changes nothing about the file, so re-reading it would pay a
    download to learn what is already on disk.
    """
    path = BLOB_DIR / f"{key}.bin"
    if not path.is_file():
        return None
    try:
        return path.read_bytes()
    except OSError:
        return None


def write_bytes(key: str, value: bytes) -> None:
    """Remember one downloaded attachment."""
    BLOB_DIR.mkdir(parents=True, exist_ok=True)
    try:
        (BLOB_DIR / f"{key}.bin").write_bytes(value)
    except OSError:
        return
