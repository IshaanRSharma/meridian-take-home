"""Card configuration and the completeness contract.

Four cards. Event, Action and Check are steps, connected by edges; Entity is a
thing a step reads or produces. Each config exposes ``findings()``, reporting
the fields a process owner has not supplied yet.

``findings()`` rather than Pydantic validators, because a half-filled card must
remain saveable — a process owner drops a card, walks away, and comes back to
it. Completeness is a gate applied at freeze time, not a constraint on storage.

Findings carry a severity and a field path rather than being strings, following
the same shape as LSP diagnostics and Django's system checks. The freeze gate is
"no blocking findings", and every Layer 1 lint rule reads these.
"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field

# Two characters minimum: edge keys are naturally short ("e1"), and anything
# longer only has to be a legal filename and Python identifier.
BOARD_KEY = r"^[a-z][a-z0-9_]{1,63}$"
ISO_8601_DURATION = r"^P(?!$)(\d+Y)?(\d+M)?(\d+W)?(\d+D)?(T(?=\d)(\d+H)?(\d+M)?(\d+S)?)?$"

BoardKey = Annotated[str, Field(pattern=BOARD_KEY)]
Duration = Annotated[str, Field(pattern=ISO_8601_DURATION)]

MIN_OUTCOMES = 2

# Coarse to fine. A check that looks at whole invoices cannot report a line-item
# count, but one that looks at line items can roll up to either coarser grain.
_GRAIN: dict[str, int] = {"per_shipment": 0, "per_document": 1, "per_line_item": 2}
_PLAIN_SCOPE: dict[str, str] = {
    "per_shipment": "the whole shipment",
    "per_document": "whole documents",
    "per_line_item": "line items",
}

Severity = Literal["blocking", "important", "minor"]
Channel = Literal["email", "sms", "phone", "queue"]
Effect = Literal["notify", "record", "lookup", "decide", "noop"]
Operator = Literal["eq", "ne", "gt", "gte", "lt", "lte", "matches", "in"]
OnFailure = Literal["fail", "wait", "skip"]
Scope = Literal["per_shipment", "per_document", "per_line_item"]
Measure = Literal["checked", "passed", "failed"]


class DomainModel(BaseModel):
    """Immutable, and an unrecognised field is an error rather than a silent drop."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        regex_engine="python-re",  # BOARD_KEY and ISO_8601_DURATION use lookaround
    )


class Finding(DomainModel):
    """One piece of configuration a process owner has not supplied.

    ``reason`` is shown on the card, so it is written in the process owner's
    language rather than naming the attribute.
    """

    field: str
    reason: str
    severity: Severity = "blocking"


def _finding(field: str, reason: str, severity: Severity = "blocking") -> Finding:
    return Finding(field=field, reason=reason, severity=severity)


def _under(prefix: str, found: list[Finding]) -> list[Finding]:
    """Re-path nested findings so ``deadline`` surfaces as ``timing.deadline``."""
    return [f.model_copy(update={"field": f"{prefix}.{f.field}"}) for f in found]


# --- references ------------------------------------------------------------


class FieldRef(DomainModel):
    """A reference into an entity's schema, as ``commercial_invoice.line_items[].batch_no``.

    Typed rather than free text so lint can confirm the field exists on an
    entity the process actually has.
    """

    entity: BoardKey
    path: str = Field(min_length=1)

    @classmethod
    def parse(cls, ref: str) -> Self:
        """Build from ``<entity>.<path>``."""
        entity, _, path = ref.partition(".")
        return cls(entity=entity, path=path)

    def segments(self) -> list[str]:
        """Path components with array markers stripped."""
        return [segment.removesuffix("[]") for segment in self.path.split(".")]

    def is_iterated(self) -> bool:
        """Whether the path crosses an array, so a Check must iterate."""
        return "[]" in self.path

    def __str__(self) -> str:
        """Render in the form ``parse`` accepts."""
        return f"{self.entity}.{self.path}"


class RoleRef(DomainModel):
    """A named role. The person filling it lives in the bindings file."""

    kind: Literal["role"] = "role"
    role: str


class EventRef(DomainModel):
    """A recipient read off the triggering event, such as whoever sent it."""

    kind: Literal["event"] = "event"
    source: BoardKey
    field: str


Recipient = Annotated[RoleRef | EventRef, Field(discriminator="kind")]


# --- shared configuration --------------------------------------------------


class Outcome(DomainModel):
    """One named way a Check or a decision can come out."""

    name: BoardKey
    priority: int = 0
    description: str | None = None


class Cardinality(DomainModel):
    """How many instances of an entity a process expects.

    ``one_per`` names the field that enumerates them: one COA per batch on the
    invoice. That is what makes an expected count derivable instead of
    configured, and it is the process owner's own phrasing.
    """

    kind: Literal["one", "many", "one_per"] = "one"
    per: FieldRef | None = None

    def findings(self) -> list[Finding]:
        """Configuration this cardinality needs that is not set."""
        if self.kind == "one_per" and self.per is None:
            return [_finding("per", "Nothing says what these are counted against.")]
        return []


class Timing(DomainModel):
    """When something happens, and what kind of deadline applies.

    ``kind`` separates two meanings that would otherwise share one field.
    ``await`` is waiting for something to arrive and compiles to a timeout on a
    wait; ``sla`` is owing someone an answer and compiles to a process-level
    timer.
    """

    kind: Literal["await", "sla"]
    mode: Literal["on_arrival", "scheduled"] = "on_arrival"
    deadline: Duration | None = None
    max_lifetime: Duration | None = None
    schedule: str | None = None

    def findings(self) -> list[Finding]:
        """Configuration this timing needs that is not set."""
        found: list[Finding] = []
        if self.kind == "sla" and not self.deadline:
            found.append(_finding("deadline", "A time limit is promised but never stated."))
        if self.mode == "scheduled" and not self.schedule:
            found.append(_finding("schedule", "This runs on a schedule, but no schedule is set."))
        return found


class Operand(DomainModel):
    """The right-hand side of a comparison.

    Three kinds cover every comparison the studied processes make: against a
    literal ("more than 18%"), against another field ("within 30 days of the
    order date"), or against the current time ("has the licence expired").
    ``offset`` shifts a field or ``now`` by a duration, which is what makes a
    date window expressible without a separate operator.

    ``now`` reads ``ctx.clock``, never the wall clock — a workflow that calls
    ``datetime.now()`` breaks Temporal replay in ways that only surface under
    crash conditions.
    """

    kind: Literal["value", "field", "now"]
    value: str | float | bool | None = None
    field: FieldRef | None = None
    offset: Duration | None = None

    def findings(self) -> list[Finding]:
        """Configuration this operand kind needs that is not set."""
        if self.kind == "value" and self.value is None:
            return [_finding("value", "Nothing says what this is compared against.")]
        if self.kind == "field" and self.field is None:
            return [_finding("field", "Nothing says which field this is compared against.")]
        return []


class Criterion(DomainModel):
    """One business test inside a Check.

    Four operators, because the LLM that drafts these from a description has to
    classify into them and every extra bucket is another way to be wrong. A date
    comparison is a comparison whose operands are dates; a pattern test is a
    comparison with a ``matches`` operator. What genuinely differs is the
    right-hand side, so that is a typed operand rather than three operators.

    ``each_has_matching`` stays separate because it is quantified over two
    collections rather than scalar, and it is what makes the COA rule checkable
    before any code exists. ``custom`` is the escape hatch: prose for codegen to
    implement, with the fields it reads named so lint still resolves them.
    """

    op: Literal["present", "compare", "each_has_matching", "custom"]
    left: FieldRef | None = None
    operator: Operator | None = None
    right: Operand | None = None
    statement: str | None = None
    reads: tuple[FieldRef, ...] = ()
    produces: str | None = None

    def references(self) -> tuple[FieldRef, ...]:
        """Every field this criterion reads, whichever operator it uses.

        Reference resolution treats a ``custom`` criterion exactly like a typed
        one, so prose does not become a hole in the lint pass.
        """
        named: list[FieldRef] = [ref for ref in (self.left,) if ref is not None]
        if self.right is not None and self.right.field is not None:
            named.append(self.right.field)
        return tuple(named) + self.reads

    def findings(self) -> list[Finding]:
        """Configuration this operator needs that is not set."""
        if self.op == "custom":
            return self._custom_findings()

        found: list[Finding] = []
        if self.left is None:
            found.append(_finding("left", "Nothing says which field this tests."))
        if self.op == "compare":
            found += self._compare_findings()
        if self.op == "each_has_matching":
            found += self._matching_findings()
        return found

    def _compare_findings(self) -> list[Finding]:
        found: list[Finding] = []
        if not self.operator:
            found.append(_finding("operator", "Nothing says how the two values are compared."))
        if self.right is None:
            found.append(_finding("right", "Nothing says what this is compared against."))
        else:
            found += _under("right", self.right.findings())
        return found

    def _matching_findings(self) -> list[Finding]:
        if self.right is None:
            found = [_finding("right", "Nothing says which field this is matched to.")]
        elif self.right.kind != "field":
            found = [_finding("right", "These can only be matched against another field.")]
        else:
            found = _under("right", self.right.findings())
        return found

    def _custom_findings(self) -> list[Finding]:
        found: list[Finding] = []
        if not self.statement:
            found.append(_finding("statement", "Nothing says what this test is."))
        if not self.reads:
            found.append(
                _finding("reads", "Nothing says which fields this test uses.", "important")
            )
        return found


class Fill(DomainModel):
    """One number a Check writes into an entity the process produces.

    A Check already reports counts rather than a boolean. This says where each
    count lands, and at what grain — which is what lets one rule report at two
    granularities. "Every line item carries four codes" produces both *two line
    items failed* and *one invoice failed*, and both are real columns.

    ``per`` defaults to the check's own scope, so the common case says nothing.
    """

    measure: Measure
    field: FieldRef
    per: Scope | None = None


# --- entity ----------------------------------------------------------------


class EntityConfig(DomainModel):
    """Something a step reads or produces: an invoice, a certificate, a record.

    An entity is a type rather than an instance. It says how to recognise one,
    what to read off it, and how many to expect.

    A process owner reaches the same object two ways: by describing it, or by
    dropping a sample and confirming the fields found on it. Most entities
    outside document-driven processes come from a lookup and have no sample at
    all, so ``fields`` is required and ``sample_extracted`` is not — but without
    a sample, a constraint the process owner states cannot be checked against a
    real one.

    ``identified_by`` is only important here because an entity produced by a
    lookup needs no recognition rule. The board-level rule raises it to blocking
    for entities that arrive rather than being produced.
    """

    name: str | None = None
    identified_by: str | None = None
    cardinality: Cardinality = Cardinality()
    fields: dict[str, object] = Field(default_factory=dict)
    sample_path: str | None = None
    sample_extracted: dict[str, object] | None = None
    instructions: str | None = None

    def findings(self) -> list[Finding]:
        """Configuration a process owner still has to supply."""
        found: list[Finding] = []
        if not self.name:
            found.append(_finding("name", "This has no name."))
        if not self.fields:
            found.append(_finding("fields", "Nothing says what to read off this."))
        # `identified_by` is deliberately not reported here. Only an entity that
        # ARRIVES needs recognising, and a card cannot know how it gets here —
        # an entity a lookup returns, or one the checks fill in, has nothing to
        # recognise. The board-level rule owns it.
        #
        # `sample_extracted` likewise: a process owner has no way to supply one,
        # so a finding could never be cleared, and a finding nobody can act on
        # is noise.
        return found + _under("cardinality", self.cardinality.findings())


# --- event -----------------------------------------------------------------


class EventConfig(DomainModel):
    """Something that starts, resumes, or meaningfully changes the process.

    An Event is never terminal: after it arrives, something still has to happen.
    ``is_terminal`` exists on :class:`ActionConfig` only, and is absent here
    rather than defaulted to ``False``, so it cannot be set at all.
    """

    name: str | None = None
    channel: Channel | None = None
    correlation_key: FieldRef | None = None
    match_condition: str | None = None
    captures: tuple[BoardKey, ...] = ()
    timing: Timing | None = None
    outcomes: tuple[Outcome, ...] = ()
    instructions: str | None = None

    def findings(self) -> list[Finding]:
        """Configuration a process owner still has to supply."""
        found: list[Finding] = []
        if not self.name:
            found.append(_finding("name", "This has no name."))
        if not self.correlation_key:
            found.append(_finding("correlation_key", "Nothing says what to file this under."))
        if not self.match_condition:
            found.append(
                _finding("match_condition", "Nothing says how to recognise this.", "important")
            )
        if not self.captures:
            found.append(_finding("captures", "Nothing arrives with this event.", "important"))

        if self.timing is None:
            found.append(_finding("timing", "Nothing says when or how this arrives."))
            return found

        found += _under("timing", self.timing.findings())
        if self.timing.deadline and not self.outcomes:
            found.append(_finding("outcomes", "This can time out, but no outcomes are named."))
        return found


# --- action ----------------------------------------------------------------


class ActionConfig(DomainModel):
    """A meaningful unit of business work.

    There is no ``capabilities`` field. A process owner authors ``effect``,
    ``channel`` and ``system`` in their own words; which tool that resolves to
    is derived at bind time onto the frozen spec, so a tool name cannot reach
    the canvas.
    """

    name: str | None = None
    performed_by: str | None = None
    effect: Effect | None = None
    channel: Channel | None = None
    system: str | None = None
    inputs: tuple[BoardKey, ...] = ()
    produces: BoardKey | None = None
    timeout: Duration | None = None
    on_failure: OnFailure | None = None
    recipients: tuple[Recipient, ...] = ()
    payload_fields: tuple[FieldRef, ...] = ()
    outcomes: tuple[Outcome, ...] = ()
    timing: Timing | None = None
    on_timeout: str | None = None
    idempotency_key: str | None = None
    is_terminal: bool = False
    instructions: str | None = None

    def findings(self) -> list[Finding]:
        """Configuration a process owner still has to supply."""
        found: list[Finding] = []
        if not self.name:
            found.append(_finding("name", "This has no name."))

        if self.effect is None:
            found.append(_finding("effect", "Nothing says what this step does."))
            return found

        if self.channel and self.effect != "notify":
            found.append(_finding("channel", "This has a channel but notifies nobody.", "minor"))

        match self.effect:
            case "notify":
                found += self._notify_findings()
            case "record":
                found += self._record_findings()
            case "lookup":
                found += self._lookup_findings()
            case "decide":
                found += self._decide_findings()
            case "noop":
                pass
        return found

    def _notify_findings(self) -> list[Finding]:
        found: list[Finding] = []
        if not self.recipients:
            found.append(_finding("recipients", "Nobody is named as receiving this."))
        if not self.channel:
            found.append(_finding("channel", "Nothing says how this reaches them."))
        if not self.payload_fields:
            found.append(
                _finding("payload_fields", "Nothing says what the message says.", "important")
            )
        if not self.idempotency_key:
            # A shipment resumes when corrected paperwork arrives, the check
            # re-runs, and without a dedupe key the same person is emailed twice.
            found.append(
                _finding("idempotency_key", "Nothing stops this being sent twice.", "important")
            )
        return found

    def _record_findings(self) -> list[Finding]:
        found: list[Finding] = []
        if not self.system:
            found.append(_finding("system", "Nothing says where this gets written."))
        if not self.idempotency_key:
            found.append(
                _finding("idempotency_key", "Nothing stops this being written twice.", "important")
            )
        return found

    def _lookup_findings(self) -> list[Finding]:
        found: list[Finding] = []
        if not self.system:
            found.append(_finding("system", "Nothing says where this is looked up."))
        if not self.produces:
            found.append(
                _finding("produces", "The answer has nowhere to land, so nothing can use it.")
            )
        if not self.on_failure:
            found.append(
                _finding(
                    "on_failure",
                    "Nothing says what to do if there is no answer.",
                    "important",
                )
            )
        if not self.timeout:
            found.append(_finding("timeout", "Nothing says how long to wait.", "minor"))
        return found

    def _decide_findings(self) -> list[Finding]:
        found: list[Finding] = []
        if not self.performed_by:
            # Who decides is load-bearing for a human task and documentation
            # everywhere else, so it is only required here.
            found.append(_finding("performed_by", "Nobody is named as deciding this."))
        if len(self.outcomes) < MIN_OUTCOMES:
            found.append(_finding("outcomes", "A decision needs at least two possible answers."))

        if self.timing is None:
            found.append(_finding("timing", "Nothing says how long this may take.", "important"))
            return found

        found += _under("timing", self.timing.findings())
        if self.timing.deadline and not self.on_timeout:
            found.append(
                _finding("on_timeout", "Nothing says what happens if nobody decides.", "important")
            )
        return found


# --- check -----------------------------------------------------------------


class CheckConfig(DomainModel):
    """A set of business criteria evaluated together to produce a named outcome.

    A Check reports counts rather than a boolean: the deliverable is a
    per-shipment row of totals, so the outcome is one field of a result that
    also carries how many were tested, how many passed, and which failed.
    """

    name: str | None = None
    criteria: tuple[Criterion, ...] = ()
    scope: Scope = "per_shipment"
    quantifier: Literal["all", "any", "none", "count"] = "all"
    inputs: tuple[BoardKey, ...] = ()
    outcomes: tuple[Outcome, ...] = ()
    evidence: tuple[FieldRef, ...] = ()
    fills: tuple[Fill, ...] = ()
    on_missing_input: OnFailure | None = None
    instructions: str | None = None

    def findings(self) -> list[Finding]:
        """Configuration a process owner still has to supply."""
        found: list[Finding] = []
        if not self.name:
            found.append(_finding("name", "This has no name."))
        if not self.criteria:
            found.append(_finding("criteria", "Nothing says what this check tests."))
        if len(self.outcomes) < MIN_OUTCOMES:
            found.append(_finding("outcomes", "A check needs at least two possible answers."))
        if not self.inputs:
            found.append(_finding("inputs", "Nothing says what this check reads."))
        if not self.on_missing_input:
            found.append(
                _finding(
                    "on_missing_input",
                    "Nothing says what to do if this has not arrived yet.",
                    "important",
                )
            )
        if not self.evidence:
            found.append(_finding("evidence", "Nothing says what to report as proof.", "minor"))
        if self.scope == "per_line_item" and not self._reads_a_line_item():
            found.append(
                _finding(
                    "scope",
                    "This runs per line item, but no criterion reads one.",
                    "important",
                )
            )
        for index, criterion in enumerate(self.criteria):
            found += _under(f"criteria[{index}]", criterion.findings())
        found += self._fill_findings()
        return found

    def _fill_findings(self) -> list[Finding]:
        """Fills that ask for a count this check cannot produce."""
        return [
            _finding(
                "fills",
                f"This looks at {_PLAIN_SCOPE[self.scope]}, "
                f"so it cannot count {_PLAIN_SCOPE[fill.per]}.",
            )
            for fill in self.fills
            if fill.per is not None and _GRAIN[fill.per] > _GRAIN[self.scope]
        ]

    def _reads_a_line_item(self) -> bool:
        return any(ref.is_iterated() for c in self.criteria for ref in c.references())
