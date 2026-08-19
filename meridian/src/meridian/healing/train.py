"""Fit the one parameter a generated agent has, against the eval suite.

Everything else the healing loop improves, it improves by writing Python — which
needs a coding agent in the loop and produces a commit. `hints.json` is
different: it is read at run time, it reaches the classifier and the extractor as
prompt context, and nothing downstream is checksummed against it. So it is a
**parameter**, and a parameter with an oracle can be fitted.

The shape is the one every hill-climber has:

```
propose   a hint, from the evidence in the failure          ← a model writes it
score     sweep the working set with it                     ← the oracle
keep      only if the reachable score went up               ← the gate
```

**Reverting on no improvement is the whole mechanism.** A proposal that does not
help is discarded rather than argued with, so the file only ever accumulates
statements that paid for themselves on the suite. That is what separates this
from asking a model to write documentation.

**Reachable columns, never the raw score.** A column no card fills cannot move,
so counting it puts noise in the reward and the search follows it.

**The reward is noisy and this is honest about it.** Two sweeps of identical code
disagree, because classification and extraction are model calls and a model at
temperature zero is still not deterministic. `threshold` exists for that: a
proposal has to win by more than the noise floor to be kept, and the floor is
measured rather than assumed.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from meridian.core.llm import Task, structured


class Proposal(BaseModel):
    """One candidate hint, as the proposer must return it."""

    entity: str = Field(default="*")
    """Which entity it is about, or `*` when it applies to every document."""

    hint: str = ""
    """One sentence about how these documents are written. Empty declines."""


async def ask_model(prompt: str) -> str:
    """Send one proposal request and hand back the raw JSON.

    A thin seam so the loop can be driven by a fake in tests and by the real
    model in a run, without either knowing about the other.
    """
    done = await structured(
        Task.REVIEW,
        Proposal,
        "You tune prompts for a document-reading agent. Reply only as JSON.",
        prompt,
    )
    return done.value.model_dump_json()

PROPOSE = """You are tuning how a document-reading agent is prompted.

It reads documents and reports counts. A check disagreed with the recorded
answer. Below is what the run saw. Write ONE sentence that would have helped the
reader get it right — an observation about how these documents are written,
never a business rule about what the numbers mean.

The difference decides where the sentence is allowed to go. An observation is
yours to make, because the suite can tell you if you were wrong. A rule about
what two things MEAN is the process owner's, and no amount of running settles
it — so it must not be laundered in here as a hint.

Good:  "one line may list several identifiers in a single cell"
Good:  "the shorter form omits a trailing suffix the longer form carries"
Bad:   "the two spellings refer to the same thing"     (a decision, not an observation)
Bad:   "count each line once"                          (a rule, not an observation)

FAILING COLUMNS
{columns}

WHAT THE STEPS PRODUCED
{trace}

DOCUMENTS THAT WERE SKIPPED
{declined}

ALREADY KNOWN — do not repeat these
{known}

Reply as JSON: {{"entity": "<entity key or * for all>", "hint": "<one sentence>"}}
"""


@dataclass(frozen=True)
class Attempt:
    """One proposal, and what the suite made of it."""

    entity: str
    hint: str
    before: int
    after: int
    kept: bool

    def gain(self) -> int:
        """Reachable columns won, which may be negative."""
        return self.after - self.before


def load(agent_dir: Path) -> dict[str, list[str]]:
    """Whatever has been learned so far. Absent is the ordinary state."""
    try:
        found = json.loads((agent_dir / "hints.json").read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return found if isinstance(found, dict) else {}


def save(agent_dir: Path, hints: Mapping[str, Sequence[str]]) -> None:
    """Write the parameter back where the agent reads it."""
    (agent_dir / "hints.json").write_text(json.dumps(hints, indent=2, sort_keys=True) + "\n")


def with_hint(
    hints: Mapping[str, Sequence[str]], entity: str, hint: str
) -> dict[str, list[str]]:
    """The same parameter with one statement added.

    Returned rather than mutated, so a rejected proposal never has to be undone
    — the caller simply keeps the dict it already had.
    """
    grown = {key: list(value) for key, value in hints.items()}
    grown.setdefault(entity, []).append(hint)
    return grown


def evidence(failing: Sequence[Mapping[str, Any]]) -> tuple[str, str, str]:
    """Columns, trace and declines, rendered for the proposer.

    The same three things a person reads out of a failure bundle. A proposer
    given only the score would be guessing; these are what make a hint about
    *this* corpus rather than about documents in general.
    """
    columns, trace, declined = [], [], []
    for case in failing:
        for column, (want, got) in (case.get("columns") or {}).items():
            columns.append(f"  {case['key']}.{column}: expected {want}, got {got}")
        for step in case.get("steps") or ():
            trace.append(f"  {case['key']} {step.get('primitive_key')}: {step.get('output')}")
        for gone in case.get("declined") or ():
            declined.append(f"  {gone.get('source')}: {gone.get('reason')}")
    return (
        "\n".join(columns) or "  none",
        "\n".join(trace[:20]) or "  none",
        "\n".join(dict.fromkeys(declined)) or "  none",
    )


async def propose(
    ask: Callable[[str], Awaitable[str]],
    failing: Sequence[Mapping[str, Any]],
    known: Mapping[str, Sequence[str]],
) -> tuple[str, str] | None:
    """One candidate hint, or nothing if the model declined to write one.

    Nothing is a legitimate answer and is not retried: a failure with no
    document-level explanation is one this parameter cannot reach, and inventing
    a sentence to fill the slot would put noise in the file the next run reads.
    """
    columns, trace, declined = evidence(failing)
    said = "\n".join(f"  [{k}] {one}" for k, v in known.items() for one in v) or "  nothing yet"
    body = await ask(
        PROPOSE.format(columns=columns, trace=trace, declined=declined, known=said)
    )
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return None
    hint = str(parsed.get("hint") or "").strip()
    entity = str(parsed.get("entity") or "*").strip() or "*"
    return (entity, hint) if hint else None


def gym_reachable(spec: Any) -> frozenset[str]:
    """Columns some card claims to fill. Re-exported so the trainer has one import."""
    from meridian.healing.gym import reachable_columns  # noqa: PLC0415 - avoids a cycle

    return reachable_columns(spec)


def reachable_score(swept: Any, reachable: frozenset[str]) -> int:
    """The reward: columns that agreed AND that some card could have produced.

    Scoring the raw total would count `status` and `invoices_mismatched_asn`,
    which no hint can move — constant noise the search would still try to climb.
    """
    return sum(
        1
        for result in swept.results
        for column in result.matched
        if column in reachable
    )


def failing(swept: Any) -> list[dict[str, Any]]:
    """The cases that disagreed, shaped for `evidence`."""
    out: list[dict[str, Any]] = []
    for result in swept.results:
        if result.passed():
            continue
        out.append(
            {
                "key": result.key,
                "columns": {m.column: (m.expected, m.actual) for m in result.mismatched},
                "steps": (),
                "declined": (),
            }
        )
    return out
