"""Tests for reading a written procedure onto the board.

One property carries the file: **a document is evidence, never a decision.** It
can start a conversation and it can never be the answer to one, because nobody
has confirmed that what somebody wrote down once is still what happens. So the
tests below check that a claim is anchored the same way a settled statement is,
that an anchor the board does not have is dropped rather than guessed at, and
that nothing here produces an `Assertion` — the type that crosses the freeze.

The alignment is the mechanism, not the summarisation. A claim is only worth
anything sitting next to what the card actually says, because that is what turns
"the document mentions timelines" into "your procedure says one working day and
this step has no deadline".
"""

from dataclasses import dataclass
from typing import Any

import pytest

from meridian.compiler import serialize
from meridian.domain.graph import Board
from meridian.domain.review import DocumentClaim, ReferenceDoc
from meridian.reviewer import reference
from meridian.reviewer.reference import Statements


@dataclass
class Turn:
    output: list[Any]
    output_parsed: Any = None


def model(*statements: dict[str, str]):
    """A fake model that returns these statements for every document."""
    parsed = Statements.model_validate({"statements": list(statements)})

    async def transport(**_: Any) -> Any:
        return Turn(output=[], output_parsed=parsed)

    return transport


def sop(text: str = "The report must name the invoice number.") -> ReferenceDoc:
    return ReferenceDoc(kind="sop", filename="procedure.md", text=text)


# --- alignment ---------------------------------------------------------------


async def test_a_statement_lands_on_the_element_it_is_about(seed: Board):
    claims = await reference.read(
        seed,
        [sop()],
        transport=model({"anchor": "primitive:coas_valid", "statement": "Every batch is checked."}),
    )

    assert len(claims) == 1
    assert str(claims[0].anchor) == "primitive:coas_valid"
    assert claims[0].statement == "Every batch is checked."


async def test_every_claim_says_which_document_it_came_from(seed: Board):
    # A question quoting a procedure has to be able to name it, or the person
    # being asked cannot go and look.
    claims = await reference.read(
        seed,
        [sop()],
        transport=model({"anchor": "board", "statement": "Documents arrive by email."}),
    )

    assert claims[0].source == "procedure.md"


@pytest.mark.parametrize(
    "ref",
    [
        "primitive:coas_valid",
        "edge:e1",
        "entity_field:commercial_invoice.invoice_no",
        "board",
    ],
)
async def test_the_same_anchors_a_settled_statement_may_use(seed: Board, ref: str):
    claims = await reference.read(
        seed, [sop()], transport=model({"anchor": ref, "statement": "A rule."})
    )
    assert [str(c.anchor) for c in claims] == [ref]


@pytest.mark.parametrize(
    "ref",
    [
        "primitive:not_a_card",
        "edge:e99",
        "entity_field:commercial_invoice.nope",
        "invented",
        "coas_valid",
    ],
)
async def test_an_anchor_the_board_does_not_have_is_dropped(seed: Board, ref: str):
    # Not guessed at and not kept. A claim pointing at nothing could never be
    # shown beside a card, which is the only place it means anything.
    claims = await reference.read(
        seed, [sop()], transport=model({"anchor": ref, "statement": "A rule."})
    )
    assert claims == ()


async def test_a_claim_is_not_a_settled_statement(seed: Board):
    # The property the whole module exists for. `Assertion` is what crosses the
    # freeze, and nothing here can produce one.
    claims = await reference.read(
        seed, [sop()], transport=model({"anchor": "board", "statement": "A rule."})
    )

    assert all(isinstance(c, DocumentClaim) for c in claims)
    assert not hasattr(claims[0], "kind"), "a claim must not look like an assertion"


# --- what gets read ----------------------------------------------------------


async def test_a_document_with_no_text_is_skipped(seed: Board):
    empty = ReferenceDoc(kind="sop", filename="scan.pdf", text=None)
    assert await reference.read(seed, [empty], transport=model()) == ()


async def test_no_documents_costs_nothing(seed: Board):
    # Most boards have none, which is the reason the whiteboard exists. It must
    # not cost a model call.
    async def refuse(**_: Any) -> Any:
        raise AssertionError("no document means no call")

    assert await reference.read(seed, [], transport=refuse) == ()


async def test_the_model_is_shown_the_document_and_what_it_may_attach_to(seed: Board):
    seen: dict[str, Any] = {}

    async def capture(**kwargs: Any) -> Any:
        seen.update(kwargs)
        return Turn(output=[], output_parsed=Statements(statements=[]))

    written = sop("Batches are checked against certificates.")
    await reference.read(seed, [written], transport=capture)

    assert "Batches are checked against certificates." in seen["input"][0]["content"]
    assert "primitive:coas_valid" in seen["input"][0]["content"]
    # Named, not just keyed: the document is in the owner's vocabulary and a bare
    # slug gives the model nothing to match a paragraph against.
    assert "Does every batch on the invoice have a matching COA?" in seen["input"][0]["content"]


# --- into the payload --------------------------------------------------------


def test_the_payload_groups_claims_under_the_element_they_are_about(seed: Board):
    claims = (
        DocumentClaim(
            anchor=reference._resolve(seed, "primitive:coas_valid"),  # type: ignore[arg-type]
            statement="Every batch is checked.",
            source="procedure.md",
        ),
        DocumentClaim(
            anchor=reference._resolve(seed, "primitive:coas_valid"),  # type: ignore[arg-type]
            statement="A correction is re-checked in full.",
            source="procedure.md",
        ),
    )

    payload = serialize.review_payload(seed, documented=claims)

    assert payload["from_the_document"] == {
        "primitive:coas_valid": [
            "[procedure.md] Every batch is checked.",
            "[procedure.md] A correction is re-checked in full.",
        ]
    }


def test_a_board_with_no_document_says_so_rather_than_omitting_the_block(seed: Board):
    # Empty rather than absent: the prompt names this block, and a key that comes
    # and goes makes "the document says nothing about this" indistinguishable
    # from "there is no document".
    assert serialize.review_payload(seed)["from_the_document"] == {}
