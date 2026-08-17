"""The activity boundary for this process: everything that touches the world.

Two activities reach the worker. `Capabilities.invoke` is the scaffold's, shared
by every agent, and handles the Actions. `Ingestion.read_documents` is this
one's, and it exists because turning attachments into entity instances is a
network call and two model calls — none of which may happen in workflow code,
where Temporal replays from history and a second answer breaks recovery
silently.

The split inside is deliberate: the activity fetches and reads, and hands back
plain data. Every decision made about that data — which check runs, which
outcome, which branch — happens above the boundary where it is replayable.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import mail
import spec
from reading import Classifier, Document, Extractor, read_pages, split
from temporalio import activity

from meridian.runtime.ingest import Pipeline, candidates_from, ingest


@dataclass
class Attachment:
    """One file to fetch, named by where it came from.

    Carried on the signal rather than looked up again, so the workflow's history
    records exactly which files it was told about.
    """

    message_id: str = ""
    attachment_id: str = ""
    filename: str = ""
    media_type: str = ""


@dataclass
class Arrival:
    """One pre-alert email, as the trigger saw it.

    A dataclass because Temporal's converter refuses bare `object`, and because
    a signal outlives the signature it was first given — adding a field here
    does not break an instance already waiting.
    """

    message_id: str = ""
    subject: str = ""
    sender: str = ""
    received_at: str = ""
    attachments: list[Attachment] = field(default_factory=list)


@dataclass
class Gathered:
    """What ingestion found, as JSON across the activity boundary.

    Serialised rather than typed because the shape is the spec's, not this
    module's: entity keys and their fields come from `spec.lock.json`, and a
    dataclass here would have to be regenerated every time a board grows a
    field.
    """

    instances: str = "{}"
    declined: str = "[]"

    def entities(self) -> dict[str, list[dict[str, Any]]]:
        """The instances, back as data."""
        loaded: dict[str, list[dict[str, Any]]] = json.loads(self.instances)
        return loaded

    def skipped(self) -> list[tuple[str, str]]:
        """What arrived and was not part of this process, with the reason."""
        return [(str(a), str(b)) for a, b in json.loads(self.declined)]


class Ingestion:
    """Attachments to entity instances, over the live mailbox.

    A class because the activity needs clients injected at worker construction —
    the standard shape for a dependency in the SDK, and what lets the eval
    harness register the same activity over a different pipeline.
    """

    def __init__(self, gmail: mail.Gmail, model_client: Any) -> None:
        """Hold the mailbox and the model this environment wants used."""
        self._gmail = gmail
        self._classify = Classifier(model_client)
        self._extract = Extractor(model_client)
        self._model = model_client

    @activity.defn(name="read_documents")
    async def read_documents(self, arrivals: Sequence[Arrival]) -> Gathered:
        """Fetch every attachment on every arrival and read what is in it.

        The email itself is stored as an instance too. It is captured by the
        event and read by no check, which is not a reason to drop it: it carries
        the sender a report would reply to and the attachment list that says
        what was supposed to be here.
        """
        candidates = candidates_from(spec.entities())
        documents: list[Document] = []
        store = None
        declined: list[tuple[str, str]] = []

        for arrival in arrivals:
            for attachment in arrival.attachments:
                try:
                    data = self._gmail.download(mail.Attachment(**vars(attachment)))
                    pages = read_pages(data, self._model)
                except Exception as error:
                    declined.append((attachment.filename, f"could not be fetched: {error}"))
                    continue
                documents.extend(split(attachment.filename, pages, candidates))

        text = {document.as_source().ref: document.text for document in documents}
        store = ingest(
            [document.as_source() for document in documents],
            candidates,
            Pipeline(read=_Cached(text), classify=self._classify, extract=self._extract),
        )

        for arrival in arrivals:
            store.add(
                "prealert_email",
                {
                    "sender": arrival.sender,
                    "subject": arrival.subject,
                    "received_at": arrival.received_at,
                    "attachment_names": [a.filename for a in arrival.attachments],
                },
            )

        declined.extend((s.source, s.reason) for s in store.skipped)
        return Gathered(
            instances=json.dumps({key: _distinct(store.instances(key)) for key in store.counts()}),
            declined=json.dumps(declined),
        )


def _distinct(instances: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One entry per distinct document, however many times it arrived.

    A shipment is several emails and the later ones repeat the earlier ones'
    attachments — a corrected certificate comes with the whole bundle again, not
    on its own. Counting the same invoice once per email makes `invoices_total`
    the number of emails rather than the number of invoices, and every count
    derived from it wrong by the same factor.

    Identity is the extracted content, because nothing on the board declares a
    key for an entity and content is what "the same document" means. Extraction
    runs at temperature zero, so the same page yields the same fields.
    """
    seen: dict[str, dict[str, Any]] = {}
    for instance in instances:
        seen.setdefault(json.dumps(instance, sort_keys=True, default=str), dict(instance))
    return list(seen.values())


@dataclass(frozen=True)
class _Cached:
    """A reader over text that has already been pulled out of the bytes.

    Splitting has to happen before classification — one attachment can hold an
    invoice and a dozen certificates — so by the time `ingest` asks for a
    source's text it has been read once already. Reading it twice would double
    the model calls on the one document that has no text layer.
    """

    text: dict[str, str]

    def read(self, source: Any) -> str:
        """The text for one already-split document."""
        return self.text.get(source.ref, "")
