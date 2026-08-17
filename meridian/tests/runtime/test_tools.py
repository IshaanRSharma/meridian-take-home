"""Reaching outside the process, and refusing to guess about it."""

import asyncio

import pytest

from meridian.runtime.context import UnboundTools
from meridian.runtime.errors import BindingError, RetryableError
from meridian.runtime.temporal.activities import Capabilities, CapabilityCall
from meridian.runtime.tools import (
    Bindings,
    ComposioProvider,
    CsvProvider,
    RecordingProvider,
    TableProvider,
    Tool,
    Tools,
)

REGISTRY = {"email.send": Tool(provider="composio", action="GMAIL_SEND_EMAIL")}


def test_a_capability_resolves_to_a_provider_action():
    provider = RecordingProvider()
    Tools(REGISTRY, {"composio": provider}).call("email.send", {"to": "x"})
    assert provider.calls == [("GMAIL_SEND_EMAIL", {"to": "x"})]


def test_an_unregistered_capability_is_refused_by_name():
    # A spec naming a capability the registry does not have is a build problem.
    # Every case fails the same way, so it must not look like a business outcome.
    with pytest.raises(BindingError, match=r"board\.lookup"):
        Tools(REGISTRY, {"composio": RecordingProvider()}).call("board.lookup", {})


def test_a_registered_capability_with_no_provider_wired_is_refused():
    with pytest.raises(BindingError, match="composio"):
        Tools(REGISTRY, {}).call("email.send", {})


def test_recording_reaches_nothing_and_remembers_everything():
    # The default everywhere. A recorded call is more assertable than a
    # delivered message: "invoked once, with these batches" is an expectation.
    provider = RecordingProvider()
    result = provider.execute("GMAIL_SEND_EMAIL", {"to": "supervisor"})
    assert result["recorded"] is True
    assert provider.calls[0][1] == {"to": "supervisor"}


def test_a_role_resolves_to_whoever_fills_it_here():
    assert (
        Bindings({"receiving_supervisor": "ops@x.com"}).role("receiving_supervisor") == "ops@x.com"
    )


def test_an_unbound_role_names_what_this_customer_does_define():
    # The fix is one line in a file. An error saying only "binding failed"
    # sends someone reading code instead of editing YAML.
    with pytest.raises(BindingError, match=r"qa_manager.*receiving_supervisor"):
        Bindings({"receiving_supervisor": "ops@x.com"}).role("qa_manager")


def test_composio_without_a_connection_says_which_command_fixes_it():
    with pytest.raises(BindingError, match="connect check"):
        ComposioProvider(client=None, user_id="u").execute("GMAIL_SEND_EMAIL", {})


class _Envelope:
    """Composio's real shape: an envelope with the payload under `data`."""

    def __init__(self, **envelope):
        self._envelope = envelope
        self.tools = self

    def execute(self, action, user_id, arguments):
        self.seen = (action, user_id, arguments)
        return self._envelope


def test_a_successful_call_is_unwrapped_to_its_data():
    client = _Envelope(successful=True, data={"messages": [{"subject": "Pre-Alert"}]})
    result = ComposioProvider(client, user_id="u").execute("GMAIL_FETCH_EMAILS", {"max_results": 3})
    assert result == {"messages": [{"subject": "Pre-Alert"}]}
    assert client.seen == ("GMAIL_FETCH_EMAILS", "u", {"max_results": 3})


def test_a_failed_call_raises_even_though_composio_does_not():
    # It reports failure by returning successful=False rather than raising. A
    # caller watching only for exceptions would read the error as an empty
    # result, and an empty result is how a check passes for the wrong reason.
    client = _Envelope(successful=False, error="rate limited", data={})
    with pytest.raises(RetryableError, match="rate limited"):
        ComposioProvider(client, user_id="u").execute("GMAIL_FETCH_EMAILS", {})


def test_a_provider_failure_is_retryable_not_a_business_outcome():
    # Getting this backwards is the worse mistake: a supervisor would be told a
    # shipment has a discrepancy because a network blipped.
    class Broken:
        def __init__(self):
            self.tools = self

        def execute(self, action, user_id, arguments):
            raise ConnectionError("the network is down")

    with pytest.raises(RetryableError):
        ComposioProvider(client=Broken(), user_id="u").execute("GMAIL_SEND_EMAIL", {})


def test_generated_code_never_learns_the_provider_action():
    # The point of the indirection. Swapping Gmail for Outlook is a registry
    # row; the spec, the bindings and the generated call site are unchanged.
    outlook = {"email.send": Tool(provider="composio", action="OUTLOOK_SEND_MAIL")}
    provider = RecordingProvider()
    Tools(outlook, {"composio": provider}).call("email.send", {"to": "x"})
    assert provider.calls[0][0] == "OUTLOOK_SEND_MAIL"


# --- reading, and writing somewhere a person can look ------------------------


def test_a_lookup_can_finally_get_an_answer():
    # Both shipped providers were write-shaped, so `effect: lookup` had nothing
    # that could return a record and the third direction of data movement was
    # untestable.
    registry = TableProvider([{"container_no": "CAAU4056270", "vessel": "APL Chicago"}])
    assert registry.execute("READ", {"container_no": "CAAU4056270"})["vessel"] == "APL Chicago"


def test_a_lookup_that_finds_nothing_returns_nothing_rather_than_raising():
    # A container the terminal has never heard of is a real answer. The Check
    # reading the result is what decides whether that is a failure.
    assert TableProvider([]).execute("READ", {"container_no": "MADE-UP"}) == {}


def test_shadow_mode_no_longer_fabricates_a_read():
    # It used to echo the arguments back, so a check comparing a container
    # number against the record "returned" for it compared the value against
    # ITSELF — a fabricated container passed and the sweep went green.
    result = asyncio.run(
        Capabilities(UnboundTools()).invoke(
            CapabilityCall(capability="system.read", args={"container_no": "MADE-UP"})
        )
    )
    assert result.output == {}


def test_a_record_lands_somewhere_a_person_can_compare(tmp_path):
    out = tmp_path / "log.csv"
    provider = CsvProvider(out)
    provider.execute("WRITE", {"record": {"shipment_no": "CAAU4056270", "failed": 1}})
    provider.execute("WRITE", {"record": {"shipment_no": "MNBU3974949", "failed": 0}})

    rows = out.read_text().splitlines()
    assert rows[0] == "shipment_no,failed"
    assert rows[1] == "CAAU4056270,1"
    assert len(rows) == 3


def test_the_csv_learns_its_columns_and_never_knows_the_process(tmp_path):
    # Header from the first record's keys. Teaching this class what a shipment
    # row looks like would put one customer's process in the runtime.
    out = tmp_path / "anything.csv"
    CsvProvider(out).execute("WRITE", {"record": {"licence": "RN9921", "expires": "2027-01"}})
    assert out.read_text().splitlines()[0] == "licence,expires"
