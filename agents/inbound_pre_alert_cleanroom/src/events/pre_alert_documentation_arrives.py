"""The way in: a pre-alert email, recognised, read, and keyed to a shipment.

``timing.mode: on_arrival``, so this is a trigger rather than a poll inside the
workflow, and ``match_condition`` is a predicate here rather than a filter in
workflow code.

**Recognising the email.** The card quotes two subjects and then says the quiet
part out loud: *"wording and punctuation vary, and it is usually a forward."*
That is prose describing recognition, not a byte comparison, and the mailbox
proves it — the exact phrase *"Pre-Alert Documents"* appears in none of the
fifteen real messages. They are variously ``Pre-Alerts Documents``, ``Pre Alerts
Documents`` and ``pre-Alerts Documents``, every one of them forwarded twice. So
orthographic variation is absorbed: case, separator, and singular against plural.
Semantic variation is not — a subject naming a different process, or the same
one cancelled, is a question only the process owner can settle.

**Keying the shipment.** ``correlation_key`` is
``commercial_invoice.container_no``, a field on a document inside an attachment.
Nothing can say which shipment an email belongs to until that invoice has been
read, which is why extraction lives out here and the workflow is signalled with
instances. An email whose invoices name no container is reported rather than
dropped: a pre-alert nobody can key is a gap in the process model, and a poll
that silently skipped it would report a clean pass over unexamined work.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import spec
from arrivals import Arrival, Declination, Instance
from ingestion import cache
from ingestion.reading import UnreadableError, pages_of
from ingestion.recognition import (
    CONFIDENCE_FLOOR,
    PROMPT_VERSION,
    Model,
    Recognised,
    recognise,
)

from meridian.runtime import Candidate, ToolBox

KEY = "pre_alert_documentation_arrives"
FETCH_CAPABILITY = "email.fetch"
ATTACHMENT_CAPABILITY = "email.attachment"

# The phrase the subject has to carry once punctuation and case are collapsed,
# and a word saying it is the paperwork rather than an announcement about it.
# Both quoted examples satisfy both halves; "Shipment Documents" satisfies
# neither, which is the point -- it may or may not be this process, and only the
# process owner can say.
_SUBJECT_PHRASE = "pre alert"
_SUBJECT_QUALIFIER = "document"

_NOT_WORD = re.compile(r"[^a-z0-9]+")
_PLURAL = re.compile(r"\b(alert)s\b")

# How many attachments are downloaded at once. Enough to hide the latency of a
# hundred-and-fifty-file mailbox, small enough not to be rate limited.
_DOWNLOADS = 8

# How many attachments are read by a model at once. A cold cache may have to
# read most of the mailbox to key one shipment, so this trades the first case's
# latency against a token-per-minute limit that a scanned bundle reaches fast.
_READERS = 4

PDF = "application/pdf"

# What the transport itself can fill, which is how the envelope entity is found
# rather than named: the capture whose fields these already cover is the email,
# and no page could ever satisfy its recognition rule.
ENVELOPE_FIELDS = ("sender", "subject", "received_at", "attachment_names")


@dataclass(frozen=True)
class Attachment:
    """One file on one message."""

    message_id: str
    attachment_id: str
    filename: str
    media_type: str


@dataclass(frozen=True)
class Message:
    """One message in the mailbox, before anything has been read."""

    message_id: str
    subject: str
    sender: str
    received_at: str
    attachments: tuple[Attachment, ...]
    body: str = ""
    """The message text, kept because the correlation key is legible in it.

    Not a field of any entity the board declares, and carried anyway: see
    ``_correlation_values``. The envelope entity does not expose it, because the
    card lists what the email is *about* — sender, subject, arrival, what came
    attached — and the body is none of those.
    """

    def envelope(self) -> dict[str, Any]:
        """The email itself as an entity instance.

        The Event captures the email alongside its attachments, and the email is
        not a page any recognition rule could match -- its ``identified_by``
        describes how it arrives. So it is filled from the transport rather than
        classified, and the fields are the card's, read off the spec.
        """
        return {
            "sender": self.sender,
            "subject": self.subject,
            "received_at": self.received_at,
            "attachment_names": [one.filename for one in self.attachments],
        }


@dataclass(frozen=True)
class Gathered:
    """One pass over the mailbox for one shipment."""

    arrivals: tuple[Arrival, ...] = ()
    uncorrelated: tuple[str, ...] = ()
    """Messages that matched the process and named no shipment.

    Reported rather than dropped, and carried all the way into the trace. A
    pre-alert nobody can key is a gap in the process model, and on this corpus
    it is the most common reason a shipment's row comes back empty -- which
    without this line looks exactly like a shipment that had no paperwork.
    """
    matched: int = 0
    """How many emails the recognition rule claimed, before any keying."""


def matches(subject: str) -> bool:
    """Whether a subject line is this process's pre-alert.

    Args:
        subject: the subject as it arrived, forwarding prefixes and all.

    Returns:
        Whether the paperwork on this message belongs to this process.
    """
    return _SUBJECT_PHRASE in _normalised(subject) and _SUBJECT_QUALIFIER in _normalised(subject)


def _normalised(subject: str) -> str:
    """The subject with the variation the card warned about collapsed away.

    Case, punctuation and doubled spacing carry no meaning in a forwarded
    subject, and neither does the plural: ``Pre-Alerts Documents`` and
    ``PRE-ALERT DOCUMENTATION`` are the same phrase written twice.
    """
    flattened = _NOT_WORD.sub(" ", subject.lower()).strip()
    return _PLURAL.sub(r"\1", flattened)


def candidates() -> tuple[Candidate, ...]:
    """The closed set an attachment's pages are classified against.

    The envelope entity is excluded because it is the delivery rather than
    something delivered: no page can satisfy *"it arrives in the pre-alert group
    mailbox"*, and offering it would invite a match that cannot be right.
    """
    envelope = spec.envelope_entity(ENVELOPE_FIELDS)
    captures = spec.config(KEY)["captures"]
    return tuple(
        Candidate(
            entity=str(name),
            identified_by=str(spec.entity(str(name))["identified_by"]),
            fields=spec.entity(str(name))["fields"],
        )
        for name in captures
        if name != envelope and spec.entity(str(name)).get("identified_by")
    )


async def messages(tools: ToolBox, limit: int = 50) -> tuple[Message, ...]:
    """Every message in the mailbox that this process recognises as its own."""
    answer = await asyncio.to_thread(tools.call, FETCH_CAPABILITY, {"max_results": limit})
    found: list[Message] = []
    for raw in answer.get("messages") or []:
        subject = str(raw.get("subject") or "")
        if not matches(subject):
            continue
        found.append(
            Message(
                message_id=str(raw.get("messageId") or ""),
                subject=subject,
                sender=str(raw.get("sender") or ""),
                received_at=str(raw.get("messageTimestamp") or ""),
                body=str(raw.get("messageText") or ""),
                attachments=tuple(
                    Attachment(
                        message_id=str(raw.get("messageId") or ""),
                        attachment_id=str(one.get("attachmentId") or ""),
                        filename=str(one.get("filename") or ""),
                        media_type=str(one.get("mimeType") or ""),
                    )
                    for one in raw.get("attachmentList") or []
                ),
            )
        )
    return tuple(found)


async def gather(tools: ToolBox, model: Model, shipment_no: str) -> Gathered:
    """Everything this shipment's paperwork amounts to, one arrival per email.

    Args:
        tools: the toolbox, addressed by capability key.
        model: how a page is classified and read.
        shipment_no: the container the correlation key has to equal.

    Returns:
        One arrival per matching email that named this shipment, plus the
        messages that matched the process and named no shipment at all.
    """
    correlation = spec.config(KEY)["correlation_key"]
    inbox = await messages(tools)
    downloads = asyncio.Semaphore(_DOWNLOADS)
    readers = asyncio.Semaphore(_READERS)

    async def one(message: Message) -> tuple[Message, tuple[tuple[str, bytes], ...]]:
        return message, await _download(tools, message, downloads)

    fetched = await asyncio.gather(*(one(message) for message in inbox))

    # Emails that can be excluded without a model are excluded. Reading costs a
    # call per attachment and the mailbox holds fifteen shipments' worth, so the
    # saving is real on a cold cache, and only *certain* exclusions are taken.
    shortlist = [
        (m, files)
        for m, files in fetched
        if _might_belong(m, files, shipment_no, str(correlation["path"]))
    ]

    read = await asyncio.gather(
        *(_read(message, files, model, readers) for message, files in shortlist)
    )

    arrivals: list[Arrival] = []
    uncorrelated: list[str] = []
    for message, recognised in read:
        containers = _correlation_values(recognised, correlation, message)
        if not containers:
            uncorrelated.append(message.message_id)
            continue
        if shipment_no.strip() not in containers:
            continue
        arrivals.append(_arrival(message, recognised))
    return Gathered(
        arrivals=tuple(arrivals), uncorrelated=tuple(uncorrelated), matched=len(inbox)
    )


def _arrival(message: Message, recognised: Recognised) -> Arrival:
    envelope = spec.envelope_entity(ENVELOPE_FIELDS)
    instances = [Instance(entity=name, values=values) for name, values in recognised.instances]
    if envelope:
        instances.insert(0, Instance(entity=envelope, values=message.envelope()))
    return Arrival(
        message_id=message.message_id,
        subject=message.subject,
        instances=instances,
        declined=[
            Declination(source=one.split(":", 1)[0], reason=one) for one in recognised.declined
        ],
    )


def _labelled(text: str, path: str) -> set[str]:
    """Values written against the correlation key's own label in free text.

    The label is built from the field path rather than spelled here, so this
    reads whatever the board named: ``container_no`` looks for *container no*,
    and a board correlating on ``application_id`` would look for *application
    id* with no change. Separators are anything non-alphanumeric, because the
    body arrives with markdown emphasis around the label and a colon after it in
    roughly equal measure.

    The value is the alphanumeric run that follows, which is what stops
    ``MCAU6047165/40'HC REEFER`` yielding the trailer as part of the number.
    """
    label = r"[\W_]+".join(re.escape(word) for word in path.split("_"))
    return {found.group(1) for found in re.finditer(label + r"[\W_]+([A-Za-z0-9]+)", text, re.I)}


def _correlation_values(
    recognised: Recognised, correlation: Mapping[str, Any], message: Message
) -> set[str]:
    """Every value of the correlation key this email offers, from either source.

    **The extracted invoice is not where this number reliably is.** The board
    names ``commercial_invoice.container_no``, and on this corpus the invoice
    column is headed *MARKS & NOS./CONTAINER NO.* and mostly holds marks — an
    address, a booking reference, a carrier. Reading only the invoice found a
    container on almost nothing, so every shipment came back with no emails and
    every count was zero.

    The same number is written against its own label in the message body on
    every message that carries one. So both sources are consulted and unioned.

    This widens where the value is *read from*; it does not loosen what counts
    as a match. The comparison afterwards is still equality against the
    shipment, and the identifier is the same identifier the board named — a
    container number. Which document is authoritative would be a question for
    the process owner; where a value is legible is a reading decision, and the
    suite judges it.
    """
    entity, path = str(correlation["entity"]), str(correlation["path"])
    found = {
        str(values[path]).strip()
        for name, values in recognised.instances
        if name == entity and values.get(path) is not None and str(values[path]).strip()
    }
    return found | _labelled(message.body, path)


def _might_belong(
    message: Message, files: Sequence[tuple[str, bytes]], shipment_no: str, path: str
) -> bool:
    """Whether this email is worth reading for this shipment.

    A pre-filter, never the correlation rule — that is decided afterwards, on
    what was read. This only skips a model call whose answer is already certain.

    **A body that names a shipment settles it on its own**, in both directions.
    The label is the most reliable source there is, so an email naming a
    different container is excluded whatever its attachments happen to mention,
    and one naming this shipment is read whatever they do not. Requiring the
    attachments to agree as well is what would drop a shipment whose invoice
    writes marks in the container column, which is most of them.

    Only when the body names nothing does the attachment test decide.
    """
    labelled = _labelled(message.body, path)
    if labelled:
        return shipment_no.strip() in labelled
    return _cannot_be_ruled_out(files, shipment_no)


def _cannot_be_ruled_out(files: Sequence[tuple[str, bytes]], shipment_no: str) -> bool:
    """Whether this email might belong to this shipment, decided without a model.

    A pre-filter, never the correlation rule: which shipment an email belongs to
    is still decided by the container number on its extracted invoice. This only
    skips reading emails that can be *excluded* for certain, which is a saving
    worth having on a cold cache and free once the mailbox has been read once.

    An email is excluded only when every page of every attachment carries a text
    layer and none of them names the container. A single scanned page means the
    container could be sitting in an image, and an email that cannot be ruled out
    is read — a shipment whose invoice arrived as a scan would otherwise lose all
    of its paperwork, silently, and the row would look merely small rather than
    wrong.
    """
    for _, content in files:
        try:
            pages = pages_of(content)
        except UnreadableError:
            # Unopenable is not the same as excluded. Something the reader could
            # not handle is a repair, and skipping it would hide the case.
            return True
        for page in pages:
            if not page.has_text() or shipment_no in page.text:
                return True
    return False


async def _download(
    tools: ToolBox, message: Message, gate: asyncio.Semaphore
) -> tuple[tuple[str, bytes], ...]:
    """Every attachment of one message, cached by its provider id.

    Bytes are cached by their provider id and never re-fetched: the mailbox does
    not change between builds, and a suite that re-downloaded a hundred and fifty
    attachments per case would cost more to measure a patch than to write one.
    """

    async def fetch(one: Attachment) -> tuple[str, bytes] | None:
        if one.media_type != PDF:
            # Not something this process declared, and a byte of it never has to
            # be paid for: the corpus carries spreadsheets and signature images.
            return None
        key = cache.key_for("attachment", one.message_id, one.attachment_id)
        stored = cache.read_bytes(key)
        if stored is not None:
            return one.filename, stored
        async with gate:
            answer = await asyncio.to_thread(
                tools.call,
                ATTACHMENT_CAPABILITY,
                {
                    "message_id": one.message_id,
                    "attachment_id": one.attachment_id,
                    "file_name": one.filename,
                },
            )
        content = answer.get("content")
        if not isinstance(content, bytes):
            return None
        cache.write_bytes(key, content)
        return one.filename, content

    got = await asyncio.gather(*(fetch(one) for one in message.attachments))
    # A list of pairs rather than a mapping: two attachments on one message may
    # share a filename, and a mapping would silently keep one of them.
    return tuple(one for one in got if one is not None)


async def _read(
    message: Message,
    files: Sequence[tuple[str, bytes]],
    model: Model,
    gate: asyncio.Semaphore,
) -> tuple[Message, Recognised]:
    """Classify and extract every attachment of one message, once each."""
    known = candidates()
    fingerprint = spec.CHECKSUM[:12]

    async def one(name: str, content: bytes) -> Recognised:
        # Keyed on the content, not the filename: the same certificate is resent
        # under a different name and would otherwise be read and paid for twice.
        key = cache.key_for(
            "recognise",
            fingerprint,
            PROMPT_VERSION,
            str(CONFIDENCE_FLOOR),
            hashlib.sha256(content).hexdigest(),
        )
        stored = cache.read(key)
        if stored is not None:
            return Recognised(
                instances=tuple((str(e), dict(v)) for e, v in stored["instances"]),
                declined=tuple(str(one) for one in stored["declined"]),
            )
        async with gate:
            found = await asyncio.to_thread(recognise, name, content, known, model)
        cache.write(
            key,
            {
                "instances": [[entity, values] for entity, values in found.instances],
                "declined": list(found.declined),
            },
        )
        return found

    parts = await asyncio.gather(*(one(name, content) for name, content in files))
    return message, Recognised(
        instances=tuple(i for part in parts for i in part.instances),
        declined=tuple(d for part in parts for d in part.declined),
    )


@dataclass(frozen=True)
class Unkeyed:
    """A pre-alert this board cannot file, with enough to act on it.

    The message id alone is what ``Gathered`` carries, and it is enough to say
    *something was skipped* while being useless to the person who has to decide
    what to do about it. A finding that cannot be acted on is noise, so this
    carries what an operator reads: who sent it, what it says it is, and what
    came attached.
    """

    message_id: str
    subject: str
    sender: str
    received_at: str
    attachments: tuple[str, ...]


@dataclass(frozen=True)
class Survey:
    """What one pass over the mailbox found, before anything has been run."""

    shipments: tuple[str, ...] = ()
    unkeyed: tuple[Unkeyed, ...] = ()
    matched: int = 0
    latest: str | None = None
    """The shipment named by the most recently received message, if it names one.

    Carried separately because `shipments` is a set and loses arrival order, and
    "what just came in" is a different question from "what is outstanding". A
    trigger fired by hand is almost always asking the first one.
    """


async def survey(tools: ToolBox, model: Model) -> Survey:
    """Read the mailbox once and say what is in it, keyed and unkeyable.

    One pass rather than two. The shipments and the messages that name none are
    the same walk over the same emails, and separating them into two functions
    means reading the mailbox twice to answer one question — which on a cold
    cache is the whole cost of the operation.

    Nothing is run here. Deciding what to process and processing it are
    different jobs, and keeping them apart is what lets the caller skip what the
    platform has already recorded without this having to know what that is.
    """
    correlation = spec.config(KEY)["correlation_key"]
    inbox = await messages(tools)
    downloads = asyncio.Semaphore(_DOWNLOADS)
    readers = asyncio.Semaphore(_READERS)

    async def one(message: Message) -> tuple[Message, Recognised]:
        files = await _download(tools, message, downloads)
        return await _read(message, files, model, readers)

    read = await asyncio.gather(*(one(message) for message in inbox))

    shipments: set[str] = set()
    unkeyed: list[Unkeyed] = []
    newest: tuple[str, str] | None = None
    for message, recognised in read:
        found = _correlation_values(recognised, correlation, message)
        if found:
            shipments |= found
            # Lexicographic on an ISO-8601 timestamp is chronological, so the
            # newest message wins without parsing a date the mailbox already
            # formatted for us.
            arrived = message.received_at or ""
            if newest is None or arrived > newest[0]:
                newest = (arrived, sorted(found)[0])
            continue
        unkeyed.append(
            Unkeyed(
                message_id=message.message_id,
                subject=message.subject,
                sender=message.sender,
                received_at=message.received_at,
                attachments=tuple(one.filename for one in message.attachments),
            )
        )
    return Survey(
        shipments=tuple(sorted(shipments)),
        unkeyed=tuple(unkeyed),
        matched=len(inbox),
        latest=newest[1] if newest else None,
    )


async def shipments_awaiting(
    tools: ToolBox, model: Model, seen: Sequence[str] = ()
) -> tuple[str, ...]:
    """Every shipment the mailbox names that has not been run yet.

    Deliberately *not* ``runtime.harness.TriggerPoll``: that protocol asks an
    agent to own a whole pass -- fetch, run each shipment to completion, report
    what happened -- and running a shipment means standing up a Temporal
    environment, which is ``run_case``'s job here. This is the half of it that
    is actually about the mailbox, so nothing pretends to implement a contract
    it does not.

    ``seen`` is handed in rather than kept, because what counts as already
    processed is the platform's record; an agent with its own ledger would
    disagree with the database the first time either was restored from a backup.
    """
    found = await survey(tools, model)
    return tuple(sorted(set(found.shipments) - set(seen)))


async def warm(tools: ToolBox, model: Model) -> dict[str, int]:
    """Read the whole mailbox once, so no later case pays for it.

    The cache makes a second case cheap and does nothing for the first, and a
    cold first case can exceed the timeout the sweep gives it. That is a real
    operational fact rather than a bug to hide, so it gets a command: run this
    once after the mailbox changes, and every case afterwards is arithmetic over
    documents already read.
    """
    inbox = await messages(tools)
    downloads = asyncio.Semaphore(_DOWNLOADS)
    readers = asyncio.Semaphore(_READERS)

    async def one(message: Message) -> Recognised:
        files = await _download(tools, message, downloads)
        _, recognised = await _read(message, files, model, readers)
        return recognised

    read = await asyncio.gather(*(one(message) for message in inbox))
    correlation = spec.config(KEY)["correlation_key"]
    shipments = {
        c
        for message, recognised in zip(inbox, read, strict=True)
        for c in _correlation_values(recognised, correlation, message)
    }
    return {
        "emails": len(inbox),
        "instances": sum(len(r.instances) for r in read),
        "declined": sum(len(r.declined) for r in read),
        "shipments": len(shipments),
    }
