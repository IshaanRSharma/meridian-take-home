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
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import cache
import mail
import reading
import spec
from reading import Classifier, Document, Extractor, read_pages, readable, split
from temporalio import activity

from meridian.runtime.ingest import Candidate, Pipeline, candidates_from, ingest


def hints() -> dict[str, list[str]]:
    """What previous runs worked out about reading these documents.

    The one part of this agent that changes without a code edit. `meridian gym
    train` proposes a hint from the evidence in a failure, scores the suite with
    it, and keeps it only if the score went up — so the loop has a parameter to
    fit rather than only code to rewrite.

    Read per activity rather than at import, because the trainer rewrites the
    file between sweeps and a module-level read would pin the first version for
    the life of the process. Absent is the ordinary state, so an unreadable file
    is an empty dict and never an error.
    """
    where = Path(__file__).resolve().parent.parent / "hints.json"
    try:
        loaded = json.loads(where.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


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
    provenance: str = "{}"
    """How many instances of each entity were read, and how many survived.

    Counts alone cannot say whether two invoices are two documents or one
    document read twice, and those have opposite fixes. Read-versus-kept
    separates them in the trace, where a bundle can print it, instead of
    requiring somebody to re-run ingestion by hand to find out."""

    def entities(self) -> dict[str, list[dict[str, Any]]]:
        """The instances, back as data."""
        loaded: dict[str, list[dict[str, Any]]] = json.loads(self.instances)
        return loaded

    def read_and_kept(self) -> dict[str, dict[str, int]]:
        """Per entity, how many instances were read and how many were distinct."""
        loaded: dict[str, dict[str, int]] = json.loads(self.provenance)
        return loaded

    def skipped(self) -> list[tuple[str, str]]:
        """What arrived and was not part of this process, with the reason."""
        return [(str(a), str(b)) for a, b in json.loads(self.declined)]


DECLARED_BATCHES = "_declared_batch_nos"
"""Where a page's `BATCH NOS:` list rides until the readings are merged.

A private key rather than a schema field. `_identity` keys on scalars and skips
lists, so this cannot change which readings are considered one document; and
`_combine` appends lists, so a three-page invoice arrives at the merge with the
block from whichever page carried it. Stripped again before anything downstream
sees it.
"""

_BATCH_BLOCK = re.compile(r"BATCH\s*NO(?:S|\.)?\s*[:\-]?\s*(.+)", re.IGNORECASE | re.DOTALL)
"""The label this exporter prints its lot list under.

A document convention, not a business rule — which is why it is a constant with
a name rather than a literal buried in a function, and why the assumption
recording it names what would falsify it. A supplier writing `LOT NUMBERS`
instead breaks this silently: the block is not found, the model's invented
`batch_no` values survive untouched, and the shipment reports certificates
missing for batches that never existed.

It belongs in `hints.json` rather than here, for the same reason: an observation
about how documents are written is a parameter the loop can fit, where a regex
is a code edit somebody has to make. Left in code for build 1 because the
mechanism has to exist before the label can be pulled out of it."""

_LOOKS_LIKE_A_BATCH = re.compile(r"^(?=\S*[A-Za-z])(?=\S*\d)[A-Za-z0-9]{6,}$")
"""What a batch number looks like on this exporter's paperwork.

The list runs until the first token that fails this, because what follows the
lots is weights, dates and pack counts. Also a document convention: a purely
numeric lot would be rejected by the letter requirement, and nothing would say
so — the count would simply come back short."""


def declared_batches(text: str) -> list[str]:
    """The batch numbers an invoice states, read off the label that states them.

    **This invoice does not carry a batch number per line item.** It carries one
    `BATCH NOS:` block listing every lot in the shipment, and the extraction
    schema asks for `line_items[].batch_no` — a shape the document does not
    have. Asked for something that is not there, the model fills the field
    anyway: correct on the page holding the block, `None` on continuation pages,
    and on a totals page whatever number is nearest, which is how `3291840`
    became a batch number nobody could find a certificate for.

    So the block is read directly rather than inferred. It is labelled, it is
    unambiguous, and it is the only place on the document that claims to be the
    list of batches — which makes this reading a field off a page, not a
    judgement about what a batch is.

    The list runs until the first token that cannot be one. Batch numbers here
    carry letters and digits and no spaces; what follows them is weights, dates
    and pack counts, which the shape test rejects and which mark the end of the
    list rather than a gap in it.
    """
    found = _BATCH_BLOCK.search(text)
    if not found:
        return []
    batches: list[str] = []
    # Split on whitespace as well as commas. The list wraps mid-line as
    # `HPSA26020A, \nQASB26078A`, and a comma-or-newline separator leaves the
    # space between them as an empty token — which reads as the end of the list
    # and stopped it at three of six.
    for token in re.split(r"[,\s]+", found.group(1)):
        if not _LOOKS_LIKE_A_BATCH.match(token):
            break
        batches.append(token)
    return batches


def batch_bearing_entity() -> str:
    """Which entity states the batch list, read off the spec rather than named.

    The certificate check compares `left` — the invoice's batches — against
    `right`, the certificates. So the spec already says which document is
    expected to carry the list, and hardcoding the name here would be a second
    copy of that, wrong the first time somebody renames a card.
    """
    for card in spec.spec()["primitives"].values():
        for criterion in card["config"].get("criteria") or ():
            if criterion.get("op") == "each_has_matching":
                return str(criterion["left"]["entity"])
    return ""


def carrier_entity() -> str:
    """The captured entity that IS the message, rather than something attached.

    Derived, not named: every entity found by reading a page quotes the phrase
    its header carries, and the one describing the email itself quotes nothing —
    *"it arrives in the pre-alert group mailbox"* names no header because there
    is no page to read it off. That absence is what distinguishes it, and it is
    already in the spec.
    """
    captured = set(spec.arriving_entities())
    for key in captured:
        if not reading.keywords_of(str(spec.entity(key).get("identified_by") or "")):
            return key
    return ""


def with_declared_batches(extract: Extractor, entity: str = "") -> Extractor:
    """Carry each page's stated batch list along with what the model extracted.

    Wrapping rather than replacing: the model is still what reads the line items,
    their codes and their descriptions, and it is good at that. The one field it
    cannot read is the one the document does not put there.
    """
    wanted = entity or batch_bearing_entity()

    def extracted(text: str, candidate: Candidate) -> Sequence[Mapping[str, Any]]:
        rows = extract(text, candidate)
        if candidate.entity != wanted:
            return rows
        stated = declared_batches(text)
        return [dict(row) | {DECLARED_BATCHES: list(stated)} for row in rows]

    return cast("Extractor", extracted)


def _spread(items: Sequence[Mapping[str, Any]], stated: Sequence[str]) -> list[dict[str, Any]]:
    """Lay a declared batch list over the line items that carry it.

    Two layouts, and they want opposite handling. Most invoices print one lot per
    line, so the batches go across the lines in order and any line past the end
    of the list is a totals row the reader mistook for goods — dropped, because
    keeping it reports a missing certificate for something that was never a batch.

    Some invoices print every lot in a single cell instead. There the line count
    is genuinely one and the batch count is three, and spreading positionally
    throws two batches away; so the surplus is packed back into the last cell it
    reached, comma separated, which is the form `coas_valid` already splits. That
    is not a special case for one document — it is the same list written the way
    that document writes it.
    """
    if not items:
        return []
    laid = [dict(item) | {"batch_no": batch} for item, batch in zip(items, stated, strict=False)]
    spare = list(stated[len(laid) :])
    if spare:
        last = laid[-1]
        last["batch_no"] = ", ".join([str(last["batch_no"]), *spare])
    return laid


def reconciled(instances: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Give each line item the batch the invoice actually declared for it.

    One batch per line item where the document works that way, and every
    remaining batch packed into the last cell where it does not — see `_spread`,
    because the two layouts appear in this corpus and a rule for one silently
    destroys the other.

    An invoice with no block is left exactly as it was read. Only a document that
    states its batches is entitled to have this applied to it, and a document
    that does not may well be one where the model was right.
    """
    out: list[dict[str, Any]] = []
    for instance in instances:
        row = dict(instance)
        stated = row.pop(DECLARED_BATCHES, None)
        items = row.get("line_items")
        if not stated or not isinstance(items, list):
            out.append(row)
            continue
        row["line_items"] = _spread(items, stated)
        out.append(row)
    return out


class Ingestion:
    """Attachments to entity instances, over the live mailbox.

    A class because the activity needs clients injected at worker construction —
    the standard shape for a dependency in the SDK, and what lets the eval
    harness register the same activity over a different pipeline.
    """

    def __init__(self, gmail: mail.Gmail, model_client: Any) -> None:
        """Hold the mailbox and the model this environment wants used."""
        self._gmail = gmail
        # Loaded per activity rather than at import: the trainer rewrites this
        # file between sweeps, and a module-level read would pin the first
        # version for the life of the process.
        found = hints()
        self._classify = Classifier(model_client, hints=found)
        self._extract = with_declared_batches(Extractor(model_client, hints=found))
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
                if not readable(attachment.filename, attachment.media_type):
                    declined.append((attachment.filename, "not a format this build reads"))
                    continue
                # Reading is the expensive half — a download, and for a scan one
                # vision call per page — and none of it depends on the code being
                # repaired. A certificate reads the same today as yesterday, so
                # charging the repair loop for it on every sweep is charging it
                # twice for work it already did.
                pages = cache.read_or(
                    attachment.message_id,
                    attachment.filename,
                    reading.VISION_MODEL,
                    reading.MAX_VISION_PAGES,
                )
                if pages is None:
                    try:
                        data = self._gmail.download(mail.Attachment(**vars(attachment)))
                        pages, via = read_pages(data, self._model)
                    except Exception as error:
                        declined.append((attachment.filename, f"could not be fetched: {error}"))
                        continue
                    cache.write(
                        attachment.message_id,
                        attachment.filename,
                        pages,
                        via,
                        reading.VISION_MODEL if via == "vision" else "",
                        reading.MAX_VISION_PAGES,
                    )
                documents.extend(split(attachment.filename, pages, candidates))

        text = {document.as_source().ref: document.text for document in documents}
        store = ingest(
            [document.as_source() for document in documents],
            candidates,
            Pipeline(read=_Cached(text), classify=self._classify, extract=self._extract),
        )

        for arrival in arrivals:
            store.add(
                carrier_entity(),
                {
                    "sender": arrival.sender,
                    "subject": arrival.subject,
                    "received_at": arrival.received_at,
                    "attachment_names": [a.filename for a in arrival.attachments],
                },
            )

        declined.extend((s.source, s.reason) for s in store.skipped)
        # Reconciled after merging, never before: an invoice is read one page at
        # a time and only one of those pages carries the block, so the batch list
        # and the line items it describes are not in the same reading until here.
        kept = {
            key: reconciled(_distinct(store.instances(key))) for key in store.counts()
        }
        return Gathered(
            instances=json.dumps(kept),
            declined=json.dumps(declined),
            provenance=json.dumps(
                {
                    key: {"read": len(store.instances(key)), "kept": len(kept[key])}
                    for key in store.counts()
                }
            ),
        )


def _distinct(instances: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One entry per distinct document, however many times it arrived.

    A shipment is several emails and the later ones repeat the earlier ones'
    attachments — a corrected certificate comes with the whole bundle again, not
    on its own. Counting the same invoice once per email makes `invoices_total`
    the number of emails rather than the number of invoices, and every count
    derived from it wrong by the same factor.

    **Nothing on the board declares an identity for an entity.** `identified_by`
    says how to *recognise* one, which is a different question from how to tell
    two apart, so identity has to be inferred from what was read.

    Byte-equality is too strict. The same document read from two page ranges
    gives one full reading and one fragment, and they are not equal — so both
    survive, and a count that should be one is two. Agreement on the scalar
    fields both populate is the workable notion: two readings of one document
    agree on everything they both saw, while two genuinely different documents
    differ on the field that names them.

    Where two agree, they are **combined, not ranked**. Documents are split for
    reading before anything knows where one ends, so a document whose rows run
    past a page break is read as two, and each reading holds the rows on its own
    pages. Keeping the longer one discards the other's rows — which is invisible,
    because the count that drops is the count of things the check was supposed
    to examine, and a check that examines fewer rows reports fewer failures.
    """
    groups: list[dict[str, Any]] = []
    for instance in instances:
        at = next(
            (n for n, group in enumerate(groups) if _same_document(group, instance)), None
        )
        if at is None:
            groups.append(dict(instance))
        else:
            groups[at] = _combine(groups[at], instance)
    return groups


def _combine(into: Mapping[str, Any], addition: Mapping[str, Any]) -> dict[str, Any]:
    """Two readings of one document, as everything either of them saw.

    Lists are appended, dropping rows already present, because a row appearing
    in both readings is one row seen twice and a row in only one is a row the
    other's pages did not cover. Scalars keep the first non-empty value: they
    agree by construction, since agreeing on them is what made these one
    document.
    """
    combined = dict(into)
    for name, value in addition.items():
        if isinstance(value, list):
            existing = combined.get(name)
            rows = list(existing) if isinstance(existing, list) else []
            seen = {json.dumps(row, sort_keys=True, default=str) for row in rows}
            for row in value:
                stamp = json.dumps(row, sort_keys=True, default=str)
                if stamp not in seen:
                    seen.add(stamp)
                    rows.append(row)
            combined[name] = rows
        elif not combined.get(name) and value is not None:
            combined[name] = value
    return combined


def _scalars(instance: Mapping[str, Any]) -> dict[str, Any]:
    """The fields of one reading that could name the document it came from.

    Lists are excluded because a partial reading holds only the rows on its own
    pages, so including them would make every fragment its own document.
    """
    return {
        field: value
        for field, value in instance.items()
        if value is not None and not isinstance(value, list | dict) and str(value).strip()
    }


def _same_document(one: Mapping[str, Any], other: Mapping[str, Any]) -> bool:
    """Whether two readings can be readings of the same document.

    **Agreement on what both populate, not equality of what each captured.**
    That is what `_distinct` above always claimed to do, and comparing the whole
    scalar set instead is a different test that fails on the commonest shape in
    this corpus: an invoice read across two page ranges gives
    `{invoice_no: "U03/25-26/4790"}` from one and
    `{invoice_no: "U03/25-26/4790", container_no: "4761"}` from the other. The
    container number is on the page one reading covered and not the other, so a
    subset became a different key from its superset, one invoice was counted as
    two, and `invoices_total`, `invoices_failed`, `goods_failed` and `coa_total`
    were all wrong by that factor — the check reporting them was never involved.

    A field only one side saw is silent rather than contradicting: absence is
    what a partial reading is made of. Two genuinely different documents differ
    on a field they *both* carry, which is exactly what this refuses to merge.

    Sharing nothing is compatible, deliberately — a fragment that names nothing
    is far more often another view of a document already seen than a new one,
    and standing those apart is what inflated `coa_total` from 9 to 47 on
    HLBU6302759 in the earlier agent.
    """
    mine, theirs = _scalars(one), _scalars(other)
    return all(_agree(mine[field], theirs[field]) for field in mine.keys() & theirs.keys())


def _agree(one: Any, other: Any) -> bool:
    """Whether two readings of one field can be readings of the same value.

    Equal, or one is the other with more printed after it at a word boundary.
    The page reads `INVOICE NO : U07/25-26/4729  DT. 25-FEB-26` and extraction
    stops at the number on one pass and keeps going on the next, so the same
    invoice arrives as `U07/25-26/4729` and `U07/25-26/4729  DT. 25-FEB-26` and
    counts as two — three invoices became four on MNBU3852977 between two builds
    of identical code, because extraction is a model call and is not cached.

    A truncation is not a contradiction, which is the same reason a field only
    one side saw is silent. **The word boundary is what keeps this safe:**
    `U07/25-26/472` would otherwise pass as a prefix of `U07/25-26/4729`, and
    those are two different invoices rather than one read twice.
    """
    mine, theirs = str(one).strip(), str(other).strip()
    if mine == theirs:
        return True
    short, long = sorted((mine, theirs), key=len)
    return long.startswith(short) and long[len(short) :][:1].isspace()


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
