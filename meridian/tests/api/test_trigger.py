"""Processing mail that arrived, when nobody knows the right answer.

Two things would make this feature worse than not having it, and both are here.

**A pre-alert that cannot be keyed disappears.** `group_by_shipment` drops a
message naming no container, which is correct for the eval suite — there is no
row to score it against — and catastrophic for a trigger, because the operator
sees a clean pass over a mailbox holding a shipment nobody looked at. The corpus
contains exactly one such message, an air-freight pre-alert carrying an air
waybill, and every test below is written so that a trigger which silently dropped
it would fail.

**A row nobody can score is believed anyway.** With no expected output there is
nothing to compare against, so the only honest verdict is about the run rather
than the answer: did it reach a check, did the check examine anything, does its
arithmetic hold, what was skipped. A run returning zeros with half the
attachments declined must never read as a pass.

Offline: no mailbox, no Temporal, no model. Every message here is constructed.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
AGENT_SRC = REPO_ROOT / "agents" / "inbound_pre_alert_validation" / "src"


@pytest.fixture(scope="module")
def agent() -> Any:
    """The generated agent's own trigger, importable the way the API loads it.

    Its directory goes on the path rather than the module being imported by
    file, because that is what `agent_loaded` does at run time — and `trigger`
    imports `harness` and `mail` as siblings, which only resolve that way.
    """
    if not AGENT_SRC.is_dir():
        pytest.skip("no generated agent on disk")
    sys.path.insert(0, str(AGENT_SRC))
    try:
        import trigger  # noqa: PLC0415 - only resolvable once the path is set

        yield trigger
    finally:
        sys.path.remove(str(AGENT_SRC))


@pytest.fixture(scope="module")
def mailbox() -> Any:
    """The agent's mail module, for building messages in its own shapes."""
    if not AGENT_SRC.is_dir():
        pytest.skip("no generated agent on disk")
    sys.path.insert(0, str(AGENT_SRC))
    try:
        import mail  # noqa: PLC0415 - only resolvable once the path is set

        yield mail
    finally:
        sys.path.remove(str(AGENT_SRC))


def a_message(mail: Any, subject: str, body: str, files: tuple[str, ...] = ()) -> Any:
    return mail.Message(
        message_id=f"m-{abs(hash(subject + body)) % 10000}",
        subject=subject,
        sender="Prealert Agent <prealert@example.invalid>",
        received_at="2026-08-04T09:18:00Z",
        body=body,
        attachments=tuple(
            mail.Attachment(
                message_id="m", attachment_id=f"a{n}", filename=name, media_type="application/pdf"
            )
            for n, name in enumerate(files)
        ),
    )


def sea(mail: Any) -> Any:
    """An ordinary pre-alert, which correlates on its container number."""
    return a_message(
        mail,
        "Fwd: FW: Pre Alerts Documents // CGMU5630052",
        "Container No : CGMU5630052\nPlease find attached.",
        ("invoice.pdf",),
    )


def air(mail: Any) -> Any:
    """The real air-freight pre-alert: an air waybill and no container anywhere.

    Taken from the corpus rather than invented — this exact message is in the
    mailbox and is the one the trigger used to drop.
    """
    return a_message(
        mail,
        "Fwd: FW: Pre-Alerts Documents // EUGIA US LLC // DAP SHIPMENT // "
        "INVOICE NO : 180/26-27/454 DT. 31-JUL-26",
        "MAWB:020-07721814 // HAWB : 530610031850 // PALLETS 19 // KWE CLEARING AGENT",
        ("530610031850.pdf", "3EL26022 FINAL FP COA.pdf"),
    )


def test_the_correlation_rule_still_drops_the_air_freight_message(mailbox: Any) -> None:
    """The premise. If this ever stops being true the feature is unnecessary.

    Asserted rather than assumed, because the whole design of the trigger rests
    on `group_by_shipment` being the wrong place to notice this.
    """
    grouped = mailbox.group_by_shipment([sea(mailbox), air(mailbox)])
    assert set(grouped) == {"CGMU5630052"}
    assert mailbox.shipment_of(air(mailbox)) is None


def test_an_air_freight_prealert_is_reported_rather_than_dropped(
    agent: Any, mailbox: Any
) -> None:
    """The bug this exists to fix.

    A trigger that only iterated `group_by_shipment` would report one shipment
    and total success over a mailbox containing two.
    """
    found = agent.uncorrelated_in([sea(mailbox), air(mailbox)])

    assert len(found) == 1
    only = found[0]
    assert "EUGIA" in only.subject
    assert only.attachment_names == ("530610031850.pdf", "3EL26022 FINAL FP COA.pdf")
    assert "container" in only.reason


def test_the_report_names_what_arrived_and_why_it_could_not_be_keyed(
    agent: Any, mailbox: Any
) -> None:
    """A state, not an error.

    An operator reading this has to be able to answer the process owner's
    question — *which message, and what was in it* — without opening a mailbox.
    """
    row = agent.uncorrelated_in([air(mailbox)])[0].as_row()

    assert row["state"] == agent.NEEDS_CORRELATION
    assert row["attachments"]
    assert row["subject"]
    assert row["sender"]
    assert "air waybill" in row["reason"]


def test_no_correlation_rule_is_invented_for_the_air_waybill(agent: Any, mailbox: Any) -> None:
    """The unit of work is the process owner's decision, not the loop's.

    Keying on the MAWB, the HAWB or the invoice number are three defensible
    answers giving three different units, and every count would be internally
    consistent and wrong together. So the trigger must report and stop.
    """
    found = agent.uncorrelated_in([air(mailbox)])[0]

    assert "020-07721814" not in (found.as_row().get("shipment_no") or "")
    assert "shipment_no" not in found.as_row()


def test_mail_that_is_not_a_prealert_is_not_reported_at_all(agent: Any, mailbox: Any) -> None:
    """Only a pre-alert that failed to key is a finding.

    Reporting every uncorrelated message would make the queue an inbox, and an
    operator who has to triage newsletters stops reading it.
    """
    noise = a_message(mailbox, "Lunch?", "no container here")
    assert agent.uncorrelated_in([noise]) == ()


# ── believing a row nobody scored ────────────────────────────────────────────


def a_check(total: int, passed: int, failed: int) -> dict[str, Any]:
    """One check's trace entry, at the counts a test wants to talk about."""
    return {"name": "coas_valid", "output": {"total": total, "passed": passed, "failed": failed}}


def an_outcome(agent: Any, steps: tuple[dict[str, Any], ...], declined: tuple[Any, ...] = ()):
    from meridian.runtime.harness import CaseOutcome  # noqa: PLC0415 - see the fixtures
    from meridian.runtime.trace import Declined, Step  # noqa: PLC0415 - see the fixtures

    return CaseOutcome(
        output={"coa_total": 3},
        steps=tuple(
            Step(seq=n, name=s["name"], output=s["output"]) for n, s in enumerate(steps, 1)
        ),
        declined=tuple(Declined(source=d[0], reason=d[1]) for d in declined),
    )


def test_a_run_that_reached_no_check_is_not_trustworthy(agent: Any) -> None:
    """A row produced without a check running is a row about nothing."""
    checked = agent.inspect("X", an_outcome(agent, ({"name": "read_documents", "output": {}},)))

    assert not checked.trustworthy()
    assert any("no check ran" in why for why in checked.concerns())


def test_a_check_that_examined_zero_rows_is_flagged(agent: Any) -> None:
    """The empty-run trap.

    Zeros agree with every column expecting zero, so a run that counted nothing
    scores as agreement with anything. With no expected row to compare against
    there is no score to catch it, which makes this the only thing that does.
    """
    checked = agent.inspect(
        "X",
        an_outcome(agent, (a_check(0, 0, 0),)),
    )

    assert not checked.trustworthy()
    assert any("zero rows" in why for why in checked.concerns())


def test_counts_that_do_not_add_up_are_flagged(agent: Any) -> None:
    """`CheckResult` enforces this at construction, and a trace is a plain dict.

    So a step whose arithmetic is broken can still reach the record, and a row
    built from it is wrong in a way nothing downstream would notice.
    """
    checked = agent.inspect(
        "X",
        an_outcome(agent, (a_check(5, 3, 1),)),
    )

    assert not checked.trustworthy()
    assert any("do not sum" in why for why in checked.concerns())


def test_a_clean_run_is_trustworthy_and_says_nothing(agent: Any) -> None:
    """Trustworthy is never a claim the numbers are right.

    Only that nothing about the run itself argues against them — which is the
    most that can honestly be said without an oracle.
    """
    checked = agent.inspect(
        "X",
        an_outcome(agent, (a_check(3, 3, 0),)),
    )

    assert checked.trustworthy()
    assert checked.concerns() == ()


def test_skipped_attachments_are_a_concern_even_on_a_clean_run(agent: Any) -> None:
    """Half the mailbox declined is the difference between absent and unread.

    It does not make the arithmetic wrong, so it does not make the run
    untrustworthy — it is the thing an operator has to see before believing a
    count of what arrived.
    """
    checked = agent.inspect(
        "X",
        an_outcome(
            agent,
            (a_check(3, 3, 0),),
            (("SH00029867 HBL.pdf", "matches no recognition rule"),),
        ),
    )

    assert checked.trustworthy()
    assert any("not read" in why for why in checked.concerns())
