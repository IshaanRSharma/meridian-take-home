"""What the model is told before it is shown anything.

One property, and it decides whether the questions mean anything. A prompt
carrying the demo customer's nouns teaches the model what to ask about rather
than how to ask, and the evidence is uncomfortable: the best question the
reviewer produced on the pre-alert board — *how do you know which certificate
belongs to which line* — had its exact form written into the prompt as an
example of good phrasing. From the output alone there is no way to tell whether
it came from the board or from us.

So the examples live in businesses this system will never be pointed at. The
board supplies the nouns, because they are the process owner's; the prompt
supplies only the shape of a good question.

The prompts are **discovered, not listed**. An earlier version of this file
named two of them and a third — the judge that decides whether a settled
statement says anything new — was added later and went unchecked, carrying a
worked example about certificates and product codes. A test that has to be
updated when the thing it guards grows is a test that will not be.

Docstrings are deliberately out of scope. They are read by people, they explain
code against the running example, and no model ever sees them.
"""

import importlib
import pkgutil
import re

import pytest

import meridian
from meridian.reviewer import prompts as reviewer_prompts

# The pre-alert board's vocabulary, plus the vertical it sits in. Every one of
# these is a legitimate word for a *board* to use and an illegitimate one for a
# prompt, which is the whole distinction being drawn.
PRE_ALERT = re.compile(
    r"\b("
    r"certificates?|invoices?|shipments?|batch(es)?|coas?|containers?|prealerts?|"
    r"pre-alerts?|pharma\w*|distributors?|warehouse\w*|consignments?|"
    r"hts|anda|ndc|aurologistics"
    r")\b",
    re.IGNORECASE,
)

# Short enough that no real instruction escapes. It does catch things that are
# not prompts — a long SQL statement reaches it today — and that is the right
# side to err on: a filter narrow enough to exclude SQL is narrow enough to
# exempt the next prompt somebody writes, and a customer's noun has no business
# being hardcoded in either.
LOOKS_LIKE_A_PROMPT = 200


def prompts() -> list[tuple[str, str]]:
    """Every module-level string in the package big enough to be an instruction.

    Discovered by walking the package rather than by naming files, so a prompt
    added to codegen or to the fill stage is guarded the day it is written.
    """
    found: list[tuple[str, str]] = []
    for module in pkgutil.walk_packages(meridian.__path__, prefix="meridian."):
        try:
            loaded = importlib.import_module(module.name)
        except Exception:  # noqa: S112 - an unimportable module is another test's problem
            continue
        for name in dir(loaded):
            if not name.isupper():
                continue
            value = getattr(loaded, name)
            if isinstance(value, str) and len(value) > LOOKS_LIKE_A_PROMPT:
                found.append((f"{module.name}.{name}", value))
    return sorted(found)


def test_the_package_actually_has_prompts_to_check() -> None:
    # The one way discovery fails silently: find nothing, pass, and guard
    # nothing. Three exist today — the reviewer's, the distiller's, and the
    # judge that the hand-written version of this test missed.
    assert len(prompts()) >= 3


@pytest.mark.parametrize(("name", "text"), prompts(), ids=lambda value: str(value)[:60])
def test_no_prompt_carries_the_demo_customer_s_vocabulary(name: str, text: str) -> None:
    leaked = sorted({match.group(0).lower() for match in PRE_ALERT.finditer(text)})

    assert not leaked, (
        f"{name} names {leaked} — the board supplies nouns, the prompt supplies shape"
    )


def test_the_board_is_still_what_supplies_the_nouns() -> None:
    # The other half. A prompt with no examples at all would pass the test above
    # and teach nothing, so the shapes a question can take have to survive: the
    # bounded spaces, and the instruction to read what the owner actually typed.
    for heading in ("HOW MANY", "SAME OR NOT", "ACCEPTABLE", "WHICH ONE", "WHAT IS IT"):
        assert heading in reviewer_prompts.SYSTEM
    assert "instructions" in reviewer_prompts.SYSTEM
