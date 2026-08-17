"""Tests for the two serialisations.

They exist for different readers and the tests are shaped accordingly.

The **reviewer payload** is read by a model that must be able to ask a good
question, so it carries everything observable about the board — including the
conversations already had, because that is where the process owner's own
vocabulary and their hedges live.

The **frozen spec** is read by a code generator that must not make a business
decision, so most of these assert an absence: no questions, no anchors, no
tool names, nobody's email address.
"""

import json
import uuid
from typing import get_args

from meridian.compiler import serialize
from meridian.domain.graph import ActionPrimitive, Board, EventPrimitive
from meridian.domain.primitives import ActionConfig, Channel, EventConfig, RoleRef
from meridian.domain.review import Anchor, Assertion, Thread, ThreadMessage

SETTLED = (
    Assertion(
        thread_id=uuid.uuid4(),
        anchor=Anchor(kind="board"),
        kind="rule",
        statement="one container is one shipment",
    ),
    Assertion(
        anchor=Anchor.parse("primitive:coas_valid"),
        kind="exception",
        statement="re-run every batch when a corrected COA arrives",
    ),
    Assertion(
        anchor=Anchor.parse("edge:e5"),
        kind="timing",
        statement="wait 48 hours, then escalate",
    ),
)

ANSWERED = Thread(
    id=uuid.uuid4(),
    category="undefined_exception",
    question="Does a COA problem end the process?",
    reason="The SOP ends at reporting and never says what closes the shipment.",
    status="answered",
    anchors=(Anchor.parse("primitive:coas_valid"),),
    messages=(
        ThreadMessage(seq=1, author="ai", body="Does a COA problem end the process?"),
        ThreadMessage(seq=2, author="human", body="No — it comes back once they resend it."),
    ),
)


# --- the reviewer's context ------------------------------------------------


def test_the_payload_carries_the_board_and_what_was_derived_from_it(seed: Board):
    payload = serialize.review_payload(seed)
    assert set(payload) >= {
        "board",
        "nodes",
        "edges",
        "entities",
        "outcomes",
        "findings",
        "decisions",
        "prior_threads",
    }


def test_entities_are_listed_apart_from_the_steps(seed: Board):
    payload = serialize.review_payload(seed)
    assert len(payload["nodes"]) == 6
    assert {e["key"] for e in payload["entities"]} == {
        "prealert_email",
        "commercial_invoice",
        "certificate_of_analysis",
    }


def test_the_reviewer_reads_what_the_process_owner_actually_wrote(seed: Board):
    # A skeleton of keys and edges supports questions about structure, which
    # lint and the sweeps already ask. Every semantic question comes from the
    # prose on the card — the subject lines the owner typed, the operators they
    # picked — so the config is the payload, not decoration on it.
    node = {n["key"]: n for n in serialize.review_payload(seed)["nodes"]}

    assert "Pre-Alert Documents" in node["prealert_received"]["config"]["match_condition"]
    assert node["coas_valid"]["config"]["criteria"][0]["op"] == "each_has_matching"
    assert "invoice number" in node["report_coa_discrepancy"]["config"]["instructions"]


def test_the_reviewer_is_not_shown_fields_nobody_filled(seed: Board):
    # An unset field is a blank, and lint already reports the blanks that matter.
    # Carrying dozens of nulls buries the handful of things the owner did write.
    assert all(
        value is not None
        for node in serialize.review_payload(seed)["nodes"]
        for value in node["config"].values()
    )


def test_an_unwired_outcome_is_stated_rather_than_left_to_be_derived(seed: Board):
    # The most common real gap on a board. A model should not have to work it
    # out by comparing two lists.
    wiring = {o["name"]: o for o in serialize.review_payload(seed)["outcomes"]["coas_valid"]}
    assert wiring["mismatched_coa"]["wired"] is False
    assert wiring["pass"]["wired"] is True


def test_a_decision_arrives_with_its_claim_already_written(seed: Board):
    # Detection is deterministic; only the phrasing is the model's. It never
    # authors the claim, so it cannot ask about a pattern that is not there.
    sequence = [d for d in serialize.review_payload(seed)["decisions"] if d["kind"] == "sequence"]
    assert len(sequence) == 1
    assert "only runs when" in sequence[0]["claim"]
    assert sequence[0]["alternative"]


def test_prior_threads_carry_every_turn_not_a_summary(seed: Board):
    # The answer is where the process owner's own words are — "it comes back
    # once they resend it" teaches vocabulary that a distilled statement loses.
    payload = serialize.review_payload(seed, threads=(ANSWERED,))
    (thread,) = payload["prior_threads"]
    assert [m["author"] for m in thread["messages"]] == ["ai", "human"]
    assert thread["reason"]


def test_rejected_threads_are_included_so_nothing_is_asked_twice(seed: Board):
    rejected = ANSWERED.model_copy(update={"status": "rejected"})
    payload = serialize.review_payload(seed, threads=(rejected,))
    assert payload["prior_threads"][0]["status"] == "rejected"


def test_the_corpus_only_appears_when_there_is_one(seed: Board):
    assert "corpus" not in serialize.review_payload(seed)
    with_corpus = serialize.review_payload(seed, corpus={"emails": 10})
    assert with_corpus["corpus"] == {"emails": 10}


def test_the_payload_is_plain_json(seed: Board):
    json.dumps(serialize.review_payload(seed, threads=(ANSWERED,)))


# --- what codegen receives -------------------------------------------------


def test_capabilities_come_from_what_the_owner_chose(seed: Board):
    # Every name here traces to one word a process owner picked from a closed
    # list — a channel, an effect. Nothing is derived from free text, and
    # nothing is derived from a guess about the board.
    spec = serialize.spec_payload(seed)
    assert set(spec.capabilities) == {"email.fetch", "email.send", "system.write"}


def test_reading_a_document_is_codegens_decision_not_the_specs(seed: Board):
    # The generator already has the entity and its field schema. How those
    # fields come out of whatever actually arrived — parse a body, read a PDF —
    # is an implementation decision, and implementation decisions are the ones
    # it is allowed to make. Nothing on a board says whether a thing is a file,
    # so any capability claiming it would be the compiler guessing.
    assert not [c for c in serialize.spec_payload(seed).capabilities if "doc" in c]


def test_a_capability_list_decides_what_becomes_an_activity(seed: Board):
    # Anything touching the world is an activity; anything deciding is workflow
    # code. The determinism rule, as data rather than a convention codegen has
    # to remember.
    spec = serialize.spec_payload(seed)
    assert {p.key for p in spec.activities()} == {
        "prealert_received",
        "report_invoice_discrepancy",
        "report_coa_discrepancy",
    }
    inline = {k for k, p in spec.primitives.items() if not p.is_activity()}
    assert inline == {"invoice_complete", "coas_valid", "documentation_validated"}


def test_a_check_never_reaches_the_world(seed: Board):
    spec = serialize.spec_payload(seed)
    assert spec.primitives["coas_valid"].capabilities == ()


def test_no_tool_name_reaches_the_spec(seed: Board):
    # The same spec has to deploy to a customer running Outlook. A provider
    # action here would make that a new version.
    assert "GMAIL" not in serialize.spec_payload(seed).model_dump_json().upper()


def test_the_spec_carries_settled_statements_not_the_questions(seed: Board):
    spec = serialize.spec_payload(seed, assertions=SETTLED)
    body = spec.model_dump_json()
    assert "re-run every batch" in body
    assert "Does a COA problem end the process" not in body


def test_no_anchor_survives_into_the_spec(seed: Board):
    # The anchor routes a statement and is then discarded. Codegen reads four
    # lists of English and a config, and resolves nothing.
    body = serialize.spec_payload(seed, assertions=SETTLED).model_dump_json()
    assert "primitive:coas_valid" not in body
    assert "anchor" not in body


def test_a_statement_about_an_edge_is_not_lost(seed: Board):
    # It reaches no card by design — a claim about a transition does not belong
    # in a step's file — so the spec has to carry it somewhere or review work
    # disappears at the freeze.
    spec = serialize.spec_payload(seed, assertions=SETTLED)
    assert "wait 48 hours" in " ".join(spec.edge_context["e5"].local)


def test_a_card_receives_what_was_settled_about_it(seed: Board):
    context = serialize.spec_payload(seed, assertions=SETTLED).primitives["coas_valid"].context
    assert context.local == ("[exception] re-run every batch when a corrected COA arrives",)
    assert "[rule] one container is one shipment" in context.inherited


def test_a_card_does_not_receive_another_cards_statements(seed: Board):
    # Codegen reading one primitive should not see statements destined for a
    # different file.
    context = (
        serialize.spec_payload(seed, assertions=SETTLED).primitives["invoice_complete"].context
    )
    assert context.local == ()


def test_freezing_an_unchanged_board_twice_gives_the_same_checksum(seed: Board):
    first = serialize.spec_payload(seed, assertions=SETTLED)
    second = serialize.spec_payload(seed, assertions=SETTLED)
    assert first.checksum == second.checksum
    assert first.is_intact()


def test_editing_the_board_changes_the_checksum(seed: Board):
    before = serialize.spec_payload(seed, assertions=SETTLED)
    renamed = seed.p("coas_valid")
    edited = seed.model_copy(
        update={
            "primitives": tuple(
                renamed.model_copy(
                    update={"config": renamed.config.model_copy(update={"name": "x"})}
                )
                if p.key == "coas_valid"
                else p
                for p in seed.primitives
            )
        }
    )
    assert serialize.spec_payload(edited, assertions=SETTLED).checksum != before.checksum


def test_the_spec_keeps_the_entities_it_reads(seed: Board):
    spec = serialize.spec_payload(seed)
    assert set(spec.entities) == {
        "prealert_email",
        "commercial_invoice",
        "certificate_of_analysis",
    }
    assert "line_items" in spec.entities["commercial_invoice"].fields


def test_every_channel_a_process_owner_can_pick_yields_a_capability():
    # A hand-maintained channel→capability table is a drift hazard with no
    # content: adding a Channel and forgetting the table is a KeyError at
    # serialisation time, on a board that linted clean. Deriving the name means
    # a new channel works the day it is added.
    for channel in get_args(Channel):
        card = ActionPrimitive(
            key="tell", config=ActionConfig(name="Tell", effect="notify", channel=channel)
        )
        (capability,) = serialize.capabilities_for(card)
        assert capability.startswith(f"{channel}.")


def test_information_arriving_by_any_channel_needs_a_way_to_collect_it():
    # A queue-driven or portal-driven process is not a special case of an
    # email-driven one. An Event that named a channel nobody had thought about
    # used to reach the spec with no capability at all, so the card became
    # workflow code and quietly never fetched anything.
    for channel in get_args(Channel):
        card = EventPrimitive(key="arrived", config=EventConfig(name="Arrived", channel=channel))
        assert f"{channel}.fetch" in serialize.capabilities_for(card)


def test_what_was_settled_about_a_document_reaches_the_generator(seed: Board):
    # The reviewer asks how to recognise a COA and how batch numbers are
    # written. Both answers are about the *thing*, not about a step — and the
    # transposition only ever walked the steps, so both were dropped at the
    # freeze without a word. The generator writing the extraction schema is
    # exactly who needed them.
    settled = (
        Assertion(
            anchor=Anchor.parse("primitive:certificate_of_analysis"),
            kind="terminology",
            statement="a batch is matched by product code, not by page order",
        ),
        Assertion(
            anchor=Anchor.parse("entity_field:commercial_invoice.invoice_no"),
            kind="constraint",
            constraint_json={"pattern": r"^\d{7}$"},
            statement="the invoice number is seven digits after the prefix",
        ),
    )
    spec = serialize.spec_payload(seed, assertions=settled)

    assert "[terminology] a batch is matched by product code, not by page order" in (
        spec.entity_context["certificate_of_analysis"].local
    )
    assert "[constraint] the invoice number is seven digits after the prefix" in (
        spec.entity_context["commercial_invoice"].local
    )


def test_a_document_only_carries_what_was_said_about_it(seed: Board):
    settled = (
        Assertion(
            anchor=Anchor.parse("primitive:certificate_of_analysis"),
            kind="terminology",
            statement="matched by product code",
        ),
    )
    spec = serialize.spec_payload(seed, assertions=settled)
    assert "commercial_invoice" not in spec.entity_context


def test_a_decision_someone_has_to_be_asked_for_needs_a_way_to_ask_them():
    # `human.decide` says a person is the one who answers. It does not say how
    # the question reaches them, and a request nobody is sent is not a request —
    # an engineer reading the capability list would never learn a mailbox is
    # involved. The channel the owner picked is the same closed enum a notify
    # step uses, so the delivery capability derives identically.
    card = ActionPrimitive(
        key="manager_decides",
        config=ActionConfig(
            name="Manager approves the purchase",
            effect="decide",
            channel="email",
            recipients=[RoleRef(role="cost_centre_manager")],
        ),
    )
    assert set(serialize.capabilities_for(card)) == {"human.decide", "email.send"}
