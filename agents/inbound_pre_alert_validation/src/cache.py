"""Page text, kept between runs, so a sweep pays for reading a document once.

The expensive half of ingestion is turning bytes into text: a download, and for
a document with no text layer one vision call per page. None of it depends on
the code being repaired — a scanned certificate reads the same today as
yesterday — so paying for it on every sweep charges the repair loop for work it
already did.

That cost is not merely money. A loop that takes ten minutes per iteration is a
loop nobody runs twice, and the whole design depends on sweeping after every
patch.

**Keyed on `(message_id, filename)`, and on the reader that produced it.**

Not on the attachment id, which is the obvious choice and is wrong: Gmail mints
a fresh `attachmentId` on every fetch of the same message. Measured on this
corpus, 0 of 156 attachment ids survived a second `GMAIL_FETCH_EMAILS` — so a
cache keyed on one never hits, and the loop pays a download and a vision call
per page on every sweep while looking like it has caching. The message id and
the filename are both stable, and together they name one file.

`READER_VERSION` is the escape hatch: repair the reading path and bump it, and
every entry is bypassed at once rather than silently serving the output of the
bug that was just fixed. That is the failure this kind of cache is famous for,
and a constant somebody has to change is the cheapest defence that actually
works.

Classification and extraction are deliberately *not* cached. They are the parts
a repair is most likely to change, and they run on text that is already local.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from pathlib import Path

READER_VERSION = "3"
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


def read_or(
    message_id: str,
    filename: str,
    vision: str = "",
    cap: int = 0,
    root: Path = DEFAULT_ROOT,
) -> Sequence[str] | None:
    """Page text for one attachment, from disk if it is there.

    Returns None on a miss rather than calling anything, because the caller is
    the one that knows how to handle a download failure and should not have that
    decision buried in a cache.
    """
    where = _path(message_id, filename, root)
    if not where.is_file():
        return None
    try:
        loaded = json.loads(where.read_text())
    except (OSError, json.JSONDecodeError):
        # A half-written entry is a miss, never an error. Re-reading the
        # document is always correct; serving half of one is not.
        return None
    if not isinstance(loaded, dict):
        # Written before entries recorded how they were read. Unknowable, so
        # treated as a miss rather than trusted.
        return None
    # A page read off the text layer is the same bytes whichever model is
    # configured, so it survives a model change. A page a model TRANSCRIBED is
    # that model's output — serving it under a new model's name is the stale
    # -cache failure READER_VERSION exists to prevent, and it would be invisible.
    # Distinguishing them is what keeps a model upgrade from costing a full
    # re-read of every native PDF in the corpus.
    if loaded.get("via") == "vision" and loaded.get("model") != vision:
        return None
    pages: list[str] = loaded.get("pages") or []
    # An entry that stopped exactly ON its page cap was probably cut short, so a
    # raised cap has to re-read it — that truncation is what hid a certificate
    # on page 13 of a 12-page read. One that stopped BELOW its cap saw the whole
    # document and stays valid however high the cap goes afterwards, which is
    # what keeps raising it from re-rendering the entire corpus.
    was = int(loaded.get("cap") or 0)
    if loaded.get("via") == "vision" and (not was or (len(pages) >= was and cap > was)):
        # No recorded cap means the entry predates the field, so whether it was
        # cut short is unknowable — and an unknowable transcription is the one
        # this exists to refuse. Costs a re-read of the scanned documents once.
        return None
    return pages


def write(  # noqa: PLR0913, PLR0917 - an entry names what it holds and how it was made
    message_id: str,
    filename: str,
    pages: Sequence[str],
    via: str = "text",
    model: str = "",
    cap: int = 0,
    root: Path = DEFAULT_ROOT,
) -> None:
    """Remember what one attachment read as.

    Best effort. A cache that cannot be written is slower, not broken, and a
    sweep that fails because a directory is read-only would be a worse system
    than one that has no cache at all.
    """
    where = _path(message_id, filename, root)
    try:
        where.parent.mkdir(parents=True, exist_ok=True)
        where.write_text(
            json.dumps({"pages": list(pages), "via": via, "model": model, "cap": cap})
        )
    except OSError:
        return


def _path(message_id: str, filename: str, root: Path) -> Path:
    """Where one attachment's text lives.

    Hashed rather than used directly: a message id is long and a filename may
    carry characters a filesystem will not take.
    """
    import hashlib  # noqa: PLC0415 - one use, beside it

    digest = hashlib.sha256(f"{message_id}/{filename}".encode()).hexdigest()[:32]
    return root / f"{digest}.v{READER_VERSION}.json"
