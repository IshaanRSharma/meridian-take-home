"""Reading what somebody typed and mapping it onto a card's typed fields.

A process owner writes *"we email the receiving supervisor, and if they haven't
come back in two days it goes to the ops manager"*. Four fields on that card are
settled by that sentence and nobody should have to find them in a form.

Three things keep it honest.

**It is a typing accelerator, never an authority.** It returns a patch; the
caller merges it. Nothing here writes, and nothing here can: this module may not
import a repository, which `tests/test_layering.py` enforces. And it only fills
blanks, because overwriting a value somebody typed or picked is the exact moment
an accelerator becomes an authority.

**The allowlist is the schema, not a filter.** Four response models, one per card
type, holding only the fields a model may set. A field it is never shown is
unrepresentable rather than filtered — so the rule cannot rot the way a
post-filter can. The line is `whiteboard.md` §4's:

    Fill may draft anything lint or `bind check` can verify. Pickers own
    anything where a wrong-but-valid answer is possible.

Which is why `criteria`, `outcomes`, `inputs`, `captures` and `scope` are absent:
a bad field path is caught by lint and a bad role by `bind check`, but a bad
criterion is caught by **nothing** — it resolves, it is well formed, and it tests
the wrong thing. `is_terminal` is absent for a related reason: it is the only
field that *suppresses* a blocking finding, and fill may open a gate but never
close one.

**It is lossy on purpose and the residual is kept.** `instructions` is in none of
the schemas — this module sets it from what was said, verbatim, so the model
cannot summarise away the sentence that is really a binding brief. Structured
fields exist to make linting possible; everything else rides in prose, because
the consumer downstream is a language model rather than a parser.

The card never sees the board, and cannot: a model that has never been shown a
key cannot return one.
"""

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from meridian.core.llm import Task, Transport, structured
from meridian.domain.graph import Primitive
from meridian.domain.primitives import Channel, Effect, OnFailure

SYSTEM = """\
You are filling in one card of a business process from a sentence the person who \
runs that process just typed about it.

Fill only what their words actually settle. Everything else stays empty — a \
blank is a question somebody will be asked later, and a guess is a wrong answer \
nobody will ever be asked about. If they did not say it, leave it null.

Do not restate their sentence anywhere. It is kept as-is, separately, and \
paraphrasing it destroys the detail that makes it useful.

Some fields are deliberately absent from what you can set. Anything that has to \
point at something else on the board — which things this step reads, which \
fields it compares, what its possible outcomes are called — is chosen from a \
list by a person, because a plausible wrong answer there looks exactly like a \
right one. Do not try to work around that.

Durations are ISO 8601: two days is P2D, forty-eight hours is PT48H, thirty \
seconds is PT30S.\
"""


# ── what a model may set, per card type ──────────────────────────────────────
#
# Flat rather than nested, deliberately: `timing.deadline` as its own key means
# a malformed duration drops the deadline alone, instead of taking the whole
# timing object with it.


class EventDraft(BaseModel):
    """An occurrence that starts, resumes, or changes the state of the process."""

    name: str | None = Field(default=None, description="what to call this, in their words")
    channel: Channel | None = Field(default=None, description="how it reaches the process")
    match_condition: str | None = Field(
        default=None, description="how to recognise a relevant one, as prose"
    )
    timing_kind: Literal["await", "sla"] | None = Field(
        default=None, description="await = waiting for it; sla = owing somebody an answer"
    )
    timing_mode: Literal["on_arrival", "scheduled"] | None = None
    deadline: str | None = Field(default=None, description="ISO 8601, e.g. PT48H")
    max_lifetime: str | None = Field(default=None, description="ISO 8601 outer bound")
    schedule: str | None = Field(default=None, description="only when it runs on a schedule")


class ActionDraft(BaseModel):
    """A meaningful unit of business work."""

    name: str | None = None
    effect: Effect | None = Field(
        default=None, description="tells someone · writes it down · looks it up · nothing"
    )
    channel: Channel | None = Field(default=None, description="only when it tells someone")
    system: str | None = Field(default=None, description="their name for the software, free text")
    performed_by: str | None = Field(default=None, description="the role, not a person's name")
    recipient_roles: list[str] = Field(
        default_factory=list, description="roles told about this, not people's names"
    )
    timeout: str | None = Field(default=None, description="ISO 8601, only for a lookup")
    on_failure: OnFailure | None = Field(default=None, description="if it does not come back")
    on_timeout: str | None = Field(
        default=None, description="what happens if nobody acts, as prose"
    )
    timing_kind: Literal["await", "sla"] | None = None
    deadline: str | None = Field(default=None, description="ISO 8601")


class CheckDraft(BaseModel):
    """A business test producing a named outcome.

    Almost nothing, and that is the correct result rather than an oversight.
    Everything that makes a check a check — what it tests, what it reads, how
    the answers are named — is a place where a plausible wrong answer is
    undetectable, so a person chooses it from a list.
    """

    name: str | None = None
    on_missing_input: OnFailure | None = Field(
        default=None, description="what to do while something it reads has not arrived"
    )


class EntityDraft(BaseModel):
    """Something the process reads or produces."""

    name: str | None = None
    identified_by: str | None = Field(default=None, description="how you recognise one, as prose")
    cardinality_kind: Literal["one", "many", "one_per"] | None = Field(
        default=None, description="how many to expect per case"
    )


DRAFTS: dict[str, type[BaseModel]] = {
    "event": EventDraft,
    "action": ActionDraft,
    "check": CheckDraft,
    "entity": EntityDraft,
}

# Every config field a model is allowed to reach, after the flat draft above is
# reassembled. Public and greppable, and asserted against the schemas in a test,
# so the design is one table rather than four class bodies to read.
ALLOWED: dict[str, frozenset[str]] = {
    "event": frozenset({"name", "channel", "match_condition", "timing"}),
    "action": frozenset(
        {
            "name",
            "effect",
            "channel",
            "system",
            "performed_by",
            "recipients",
            "timeout",
            "on_failure",
            "on_timeout",
            "timing",
        }
    ),
    "check": frozenset({"name", "on_missing_input"}),
    "entity": frozenset({"name", "identified_by", "cardinality"}),
}


async def fields(
    card: Primitive,
    said: str,
    *,
    overwrite: bool = False,
    transport: Transport | None = None,
) -> dict[str, Any]:
    """The fields this card's own words settle, as a patch onto its config.

    Args:
        card: the card being described. Its current config decides what is
            already answered and therefore left alone.
        said: what the owner typed. Kept verbatim in `instructions` whatever
            else happens.
        overwrite: replace values that are already set. Off by default, because
            overwriting what somebody typed is where an accelerator becomes an
            authority; on when they mean *"no, replace what I said before"*.
        transport: the function that talks to OpenAI. Tests pass a fake.

    Returns:
        A patch to merge, never a config to store. Empty of everything but
        `instructions` when the words settled nothing — which is not a failure:
        a blank is a lint finding, and a lint finding is a question.

    Raises:
        LLMError: the model could not be reached or returned nothing usable.
            The owner's prose is still in front of them, so the cost of this is
            pressing save again.
    """
    if not said.strip():
        return {}

    schema = DRAFTS[card.primitive_type]
    completed = await structured(
        Task.FILL, schema, SYSTEM, _payload(card, said), transport=transport
    )

    patch = _as_config(card.primitive_type, completed.value)
    patch = _only_blanks(card, patch) if not overwrite else patch
    patch = _keeping_what_validates(card, patch)
    patch["instructions"] = _appended(card, said)
    return patch


def _payload(card: Primitive, said: str) -> str:
    """The card and the sentence. Deliberately nothing else.

    No other card's key, no entity name, no outcome. `distill` hands the model a
    list of valid anchors precisely so it stops guessing them; here the opposite
    is wanted — a model that has never seen a key cannot return one.
    """
    return json.dumps(
        {
            "card": card.primitive_type,
            "already_filled": card.config.model_dump(mode="json", exclude_none=True),
            "they_said": said,
        },
        indent=2,
    )


def _as_config(primitive_type: str, draft: BaseModel) -> dict[str, Any]:
    """The flat draft reassembled into the shape a config actually takes."""
    said = draft.model_dump(exclude_none=True)
    patch: dict[str, Any] = {
        key: value for key, value in said.items() if key in ALLOWED[primitive_type]
    }

    timing = {
        field: said[key]
        for key, field in (
            ("timing_kind", "kind"),
            ("timing_mode", "mode"),
            ("deadline", "deadline"),
            ("max_lifetime", "max_lifetime"),
            ("schedule", "schedule"),
        )
        if said.get(key) is not None
    }
    if "kind" in timing:
        patch["timing"] = timing

    if primitive_type == "action":
        # A role, never a person, and never an `EventRef` — an identity in a
        # checksummed spec would make a leaver force a new spec version.
        if said.get("performed_by"):
            patch["performed_by"] = {"kind": "role", "role": said["performed_by"]}
        if said.get("recipient_roles"):
            patch["recipients"] = [
                {"kind": "role", "role": role} for role in said["recipient_roles"]
            ]
    if primitive_type == "entity" and said.get("cardinality_kind"):
        # `per` is a FieldRef and stays a picker, so a `one_per` arrives without
        # one — which `Cardinality.findings()` already reports as *"nothing says
        # what these are counted against"*. The split was anticipated.
        patch["cardinality"] = {"kind": said["cardinality_kind"]}
    return patch


def _only_blanks(card: Primitive, patch: dict[str, Any]) -> dict[str, Any]:
    """Drop anything the card already answers.

    Answered means *differs from what it would be if nobody had said anything* —
    not merely present. Several config fields carry a default that survives the
    round trip through the database and comes back looking filled in:
    `EntityConfig.cardinality` is `one` on every card ever created, so a value
    that is only ever a default would otherwise be unfillable forever.

    The cost is that somebody who deliberately chose the default gets no
    protection on that one field. `overwrite=False` is a courtesy rather than a
    guarantee, and the alternative is a field nothing can ever fill.
    """
    blank = type(card.config)()
    return {
        key: value
        for key, value in patch.items()
        if getattr(card.config, key, None) in (None, getattr(blank, key, None))
    }


def _keeping_what_validates(card: Primitive, patch: dict[str, Any]) -> dict[str, Any]:
    """Everything in the patch the config will actually accept.

    A malformed value — `deadline: "48 hours"` where `PT48H` was wanted — drops
    on its own rather than failing the whole draft. One field the model got
    wrong must not cost the owner the other three, and a dropped field is simply
    blank, which is a lint finding, which is a question somebody was going to be
    asked anyway. There is no error dialog on this path at all.
    """
    config = type(card.config)
    current = card.config.model_dump(mode="json", exclude_none=True)
    candidate = dict(patch)

    while candidate:
        try:
            config.model_validate({**current, **candidate})
        except ValidationError as refused:
            offending = {str(error["loc"][0]) for error in refused.errors() if error["loc"]}
            if not offending & set(candidate):
                return {}
            for key in offending:
                candidate.pop(key, None)
        else:
            return candidate
    return {}


def _appended(card: Primitive, said: str) -> str:
    """Their words, kept whole, after anything already there.

    Never written by the model — it is not in any schema — so the sentence that
    is really a binding brief cannot be summarised into the one field that lints.
    """
    already = card.config.instructions
    return f"{already}\n\n{said}" if already else said


__all__ = ["ALLOWED", "DRAFTS", "ActionDraft", "CheckDraft", "EntityDraft", "EventDraft", "fields"]
