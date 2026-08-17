"""Reaching outside the process, and refusing to guess about it."""

import pytest

from meridian.runtime.errors import BindingError, RetryableError
from meridian.runtime.tools import (
    Bindings,
    ComposioProvider,
    RecordingProvider,
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
        ComposioProvider(client=None, entity_id="aurologistics").execute("GMAIL_SEND_EMAIL", {})


def test_a_provider_failure_is_retryable_not_a_business_outcome():
    # Getting this backwards is the worse mistake: a supervisor would be told a
    # shipment has a discrepancy because a network blipped.
    class Broken:
        def execute(self, action, args):
            raise ConnectionError("rate limited")

    with pytest.raises(RetryableError):
        ComposioProvider(client=Broken(), entity_id="e").execute("GMAIL_SEND_EMAIL", {})


def test_generated_code_never_learns_the_provider_action():
    # The point of the indirection. Swapping Gmail for Outlook is a registry
    # row; the spec, the bindings and the generated call site are unchanged.
    outlook = {"email.send": Tool(provider="composio", action="OUTLOOK_SEND_MAIL")}
    provider = RecordingProvider()
    Tools(outlook, {"composio": provider}).call("email.send", {"to": "x"})
    assert provider.calls[0][0] == "OUTLOOK_SEND_MAIL"
