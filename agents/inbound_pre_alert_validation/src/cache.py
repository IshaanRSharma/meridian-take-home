"""Page text, kept between runs, so a sweep pays for reading a document once.

The expensive half of ingestion is turning bytes into text: a download, and for
a document with no text layer one vision call per page. None of it depends on
the code being repaired — a scanned certificate reads the same today as
yesterday — so paying for it on every sweep charges the repair loop for work it
already did.

That cost is not merely money. A loop that takes ten minutes per iteration is a
loop nobody runs twice, and the whole design depends on sweeping after every
patch.

**Keyed on the attachment and the reader that produced it.** The attachment id
is stable and content-addressed by the provider. `READER_VERSION` is the escape
hatch: repair the reading path and bump it, and every entry is bypassed at once
rather than silently serving the output of the bug that was just fixed. That is
the failure this kind of cache is famous for, and a constant somebody has to
change is the cheapest defence that actually works.

Classification and extraction are deliberately *not* cached. They are the parts
a repair is most likely to change, and they run on text that is already local.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from pathlib import Path

READER_VERSION = "2"
"""Bump when the reading path changes — the text layer parser, the vision
fallback, the page renderer, or the threshold between them. Anything that would
make the same bytes produce different text."""

DEFAULT_ROOT = Path(
    os.environ.get("MERIDIAN_CACHE") or Path(__file__).resolve().parent.parent / ".cache" / "pages"
)
"""Beside the agent, not beside whoever invoked it.

A relative path would put the cache wherever the process happened to start, so
the CLI and the worker would each warm their own and neither would ever hit —
which looks exactly like a cache that does not work."""


def read_or(attachment_id: str, produce: object, root: Path = DEFAULT_ROOT) -> Sequence[str] | None:
    """Page text for one attachment, from disk if it is there.

    `produce` is only described here so the caller keeps the miss path; this
    returns None on a miss rather than calling it, because the caller is the one
    that knows how to handle a download failure and should not have that
    decision buried in a cache.
    """
    del produce
    where = _path(attachment_id, root)
    if not where.is_file():
        return None
    try:
        loaded: list[str] = json.loads(where.read_text())
    except (OSError, json.JSONDecodeError):
        # A half-written entry is a miss, never an error. Re-reading the
        # document is always correct; serving half of one is not.
        return None
    return loaded


def write(attachment_id: str, pages: Sequence[str], root: Path = DEFAULT_ROOT) -> None:
    """Remember what one attachment read as.

    Best effort. A cache that cannot be written is slower, not broken, and a
    sweep that fails because a directory is read-only would be a worse system
    than one that has no cache at all.
    """
    where = _path(attachment_id, root)
    try:
        where.parent.mkdir(parents=True, exist_ok=True)
        where.write_text(json.dumps(list(pages)))
    except OSError:
        return


def _path(attachment_id: str, root: Path) -> Path:
    """Where one attachment's text lives.

    The id is hashed rather than used directly: a provider's identifiers are
    long, and some carry characters a filesystem will not take.
    """
    import hashlib  # noqa: PLC0415 - one use, beside it

    digest = hashlib.sha256(attachment_id.encode()).hexdigest()[:32]
    return root / f"{digest}.v{READER_VERSION}.json"
