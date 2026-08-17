"""Reaching the mailbox, and working out which shipment a message belongs to.

All of this is I/O or depends on it, so nothing here is ever called from
workflow code — the trigger uses it to decide what to signal, and an activity
uses it to fetch bytes.

Two decisions live here rather than in the workflow, and both are recorded in
`assumptions.json`:

**Recognising a pre-alert.** The spec quotes a subject phrase. Prose on a card
describes how to recognise something; it is not a byte comparison, and read
literally it matches nothing in the real corpus — every subject is plural, and
the case and separators vary. So the phrase is matched with orthographic
tolerance and nothing more: same words written differently is the same phrase,
different words are a different question and are left alone.

**Finding the correlation key.** The spec says the key is
`commercial_invoice.container_no`, which is true of the finished data and
useless at trigger time, because correlating is what decides which workflow the
attachments belong to and extraction has not run yet. The same number appears in
the message body, under a `Container No` label, in every message in the corpus.
"""

from __future__ import annotations

import base64
import os
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.request import urlopen

TOOLKIT_VERSION = "20260815_00"
"""Pinned, because manual execution refuses `latest` and an unpinned toolkit
turns a provider-side change into a failing sweep with no local cause."""

_SUBJECT_PHRASES = ("pre alert documents", "pre alerts documents", "pre-alert documentation")
"""The spec's phrase, plus the variants that are the same words written
differently. Anything that is not one of these words is a question for the
process owner, not a pattern to widen."""

_CONTAINER = re.compile(r"container\s*no\.?\s*[:\-]?\s*([A-Z]{4}\d{7})", re.IGNORECASE)
_BARE_CONTAINER = re.compile(r"\b[A-Z]{4}\d{7}\b")


@dataclass(frozen=True)
class Attachment:
    """One file hanging off a message, before anything has been read from it."""

    message_id: str
    attachment_id: str
    filename: str
    media_type: str


@dataclass(frozen=True)
class Message:
    """One pre-alert email, reduced to what correlation and ingestion need."""

    message_id: str
    subject: str
    sender: str
    received_at: str
    body: str
    attachments: tuple[Attachment, ...]

    def attachment_names(self) -> tuple[str, ...]:
        """What arrived, by name. Recorded as evidence, never used to classify."""
        return tuple(a.filename for a in self.attachments)


def normalise(text: str) -> str:
    """Collapse a subject to the form two spellings of one phrase share.

    Case, separators and doubled whitespace only. Singular and plural are not
    folded here because they are folded by listing both phrases, which keeps the
    set of accepted wordings readable rather than hidden in a regex.
    """
    return re.sub(r"[\s\-_/]+", " ", text).strip().lower()


def matches(message: Message) -> bool:
    """Whether this message is a pre-alert, per the event's match condition."""
    subject = normalise(message.subject)
    return any(phrase in subject for phrase in _SUBJECT_PHRASES)


def shipment_of(message: Message) -> str | None:
    """The container this message is about, or None if it names none.

    The labelled form is tried first and a bare container code second. Both
    agree on every message in the corpus, and the label is what keeps a code
    that happens to appear in an attachment name from being read as the key.

    None is a real answer, not a failure: an air-freight pre-alert carries an
    air waybill and no container, and the process owner has not said how those
    correlate.
    """
    body = message.body.replace("*", "")
    labelled = _CONTAINER.search(body)
    if labelled:
        return labelled.group(1).upper()
    bare = _BARE_CONTAINER.search(body)
    return bare.group(0).upper() if bare else None


def group_by_shipment(messages: Sequence[Message]) -> dict[str, tuple[Message, ...]]:
    """Pre-alerts, gathered into the unit the process actually runs on.

    One shipment is several messages — a first pre-alert and a corrected one
    days later — and the eval row is per shipment, never per email. Messages
    naming no container are left out rather than guessed at.
    """
    grouped: dict[str, list[Message]] = {}
    for message in messages:
        if not matches(message):
            continue
        shipment = shipment_of(message)
        if shipment is not None:
            grouped.setdefault(shipment, []).append(message)
    return {key: tuple(found) for key, found in grouped.items()}


class Gmail:
    """The live mailbox, through Composio.

    Injected wherever it is used so the same code runs against a recorded
    inbox: everything above this class is pure and testable without a network.
    """

    def __init__(self, api_key: str, user_id: str) -> None:
        """Build a version-pinned client for one connected mailbox."""
        os.environ["COMPOSIO_API_KEY"] = api_key
        from composio import (
            Composio,
        )

        self._client = Composio(toolkit_versions={"gmail": TOOLKIT_VERSION})
        self._user = user_id

    def inbox(self, limit: int = 100) -> tuple[Message, ...]:
        """Every message, whether or not it is a pre-alert.

        Unfiltered on purpose. Filtering server-side by the subject phrase would
        hide the messages the phrase fails to match, which is exactly the class
        of miss `matches` was written to avoid.
        """
        answer = self._client.tools.execute(
            "GMAIL_FETCH_EMAILS", user_id=self._user, arguments={"max_results": limit}
        )
        raw = (answer.get("data") or {}).get("messages") or []
        return tuple(_message(entry) for entry in raw)

    def download(self, attachment: Attachment) -> bytes:
        """The bytes of one attachment."""
        answer = self._client.tools.execute(
            "GMAIL_GET_ATTACHMENT",
            user_id=self._user,
            arguments={
                "message_id": attachment.message_id,
                "attachment_id": attachment.attachment_id,
                "file_name": attachment.filename,
            },
        )
        return _bytes_from(answer.get("data") or {})


def _message(entry: Mapping[str, Any]) -> Message:
    return Message(
        message_id=str(entry.get("messageId", "")),
        subject=str(entry.get("subject") or ""),
        sender=str(entry.get("sender") or ""),
        received_at=str(entry.get("messageTimestamp") or ""),
        body=str(entry.get("messageText") or ""),
        attachments=tuple(_attachments(entry)),
    )


def _attachments(entry: Mapping[str, Any]) -> Iterator[Attachment]:
    message_id = str(entry.get("messageId", ""))
    for item in entry.get("attachmentList") or []:
        yield Attachment(
            message_id=message_id,
            attachment_id=str(item.get("attachmentId") or item.get("attachment_id") or ""),
            filename=str(item.get("filename") or ""),
            media_type=str(item.get("mimeType") or ""),
        )


def _bytes_from(payload: Mapping[str, Any]) -> bytes:
    """Pull file bytes out of whichever shape the provider returned.

    Composio does not hand back the file. It hands back a `file` descriptor
    holding a presigned URL, and has also returned a local path and raw base64
    depending on version and size. All three are handled here, in one place, so
    a provider-side change stays a fetch bug rather than surfacing as a check
    that mysteriously examined nothing.
    """
    descriptor = payload.get("file")
    fields: Mapping[str, Any] = descriptor if isinstance(descriptor, dict) else payload

    for key in ("s3url", "url", "uri"):
        value = fields.get(key)
        if isinstance(value, str) and value.startswith("http"):
            return _fetched(value)

    for key in ("path", "local_path", "file"):
        value = fields.get(key)
        if isinstance(value, str) and Path(value).exists():
            return Path(value).read_bytes()

    for key in ("content", "data", "body"):
        value = fields.get(key)
        if isinstance(value, bytes):
            return value
        if isinstance(value, str):
            return base64.b64decode(value)

    msg = f"no file content in attachment response: {sorted(fields)}"
    raise ValueError(msg)


def _fetched(url: str, timeout: float = 60.0) -> bytes:
    """Download a presigned attachment URL."""
    with urlopen(url, timeout=timeout) as answer:  # noqa: S310 - provider-issued https URL
        downloaded: bytes = answer.read()
        return downloaded
