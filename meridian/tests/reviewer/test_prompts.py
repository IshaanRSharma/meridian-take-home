"""What the reviewer is told before it is shown anything.

One property, and it is the one that decides whether the questions mean
anything. A prompt carrying the demo customer's nouns teaches the model what to
ask about rather than how to ask, and the evidence is uncomfortable: the best
question the reviewer produced on the pre-alert board — *how do you know which
certificate belongs to which line* — had its exact form written into the prompt
as an example of good phrasing. Impossible to tell, from the output, whether it
came from the board or from us.

So the examples live in businesses this system will never be pointed at. The
board supplies the nouns, because they are the process owner's; the prompt
supplies only the shape of a good question.

Same rule the shape library already follows: a matcher earns inclusion by being
expressible over primitive types and edge relations alone.
"""

import re

from meridian.reviewer import distill, prompts

# The pre-alert board's vocabulary, plus the vertical it sits in. Every one of
# these is a legitimate word for a *board* to use and an illegitimate one for
# the prompt, which is the whole distinction being drawn.
PRE_ALERT = re.compile(
    r"\b("
    r"certificates?|invoices?|shipments?|batch(es)?|coas?|containers?|prealerts?|"
    r"pre-alerts?|pharma\w*|distributors?|warehouse\w*|consignments?|"
    r"hts|anda|manifest\w*"
    r")\b",
    re.IGNORECASE,
)


def test_no_prompt_carries_the_demo_customer_s_vocabulary() -> None:
    leaked = {
        name: sorted({match.group(0).lower() for match in PRE_ALERT.finditer(text)})
        for name, text in (("review", prompts.SYSTEM), ("distil", distill.SYSTEM))
        if PRE_ALERT.search(text)
    }

    assert not leaked, f"domain nouns in a system prompt: {leaked}"


def test_the_board_is_still_what_supplies_the_nouns() -> None:
    # The other half. A prompt with no examples at all would pass the test above
    # and teach nothing, so the shapes a question can take have to survive: the
    # bounded spaces, and the instruction to read what the owner actually typed.
    for heading in ("HOW MANY", "SAME OR NOT", "ACCEPTABLE", "WHICH ONE", "WHAT IS IT"):
        assert heading in prompts.SYSTEM
    assert "instructions" in prompts.SYSTEM
