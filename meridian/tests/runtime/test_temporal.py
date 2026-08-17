"""The Temporal adapters, tested without a server.

``waits`` translates a raised timeout into a returned bool, and that translation
is the whole file — so it is tested by substituting the SDK call. ``activities``
needs no server at all: it is a dispatcher with a toolbox injected.

Running a real workflow is the toy agent's job, not this file's.
"""

from typing import Any

import pytest

from meridian.runtime.errors import BindingError
from meridian.runtime.temporal import waits
from meridian.runtime.temporal.activities import Capabilities, CapabilityCall


class RecordingTools:
    """A toolbox that answers, and remembers what it was asked."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call(self, capability: str, args: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((capability, dict(args)))
        return {"delivered": True}


class RefusingTools:
    def call(self, capability: str, args: dict[str, Any]) -> dict[str, Any]:
        msg = "no address for receiving_supervisor"
        raise BindingError(msg)


# --- waiting -----------------------------------------------------------------


async def test_inputs_arriving_before_the_deadline_reports_true(monkeypatch):
    async def satisfied(fn, **kw):
        return None

    monkeypatch.setattr(waits.workflow, "wait_condition", satisfied)
    assert await waits.await_inputs(lambda: True, "PT48H") is True


async def test_a_deadline_passing_is_an_outcome_not_an_error(monkeypatch):
    # The process owner drew a path for the deadline expiring. Letting the
    # TimeoutError escape would crash the workflow instead of taking it.
    async def times_out(fn, **kw):
        raise TimeoutError

    monkeypatch.setattr(waits.workflow, "wait_condition", times_out)
    assert await waits.await_inputs(lambda: False, "PT48H") is False


async def test_no_deadline_creates_no_timer(monkeypatch):
    # `timeout=0` throws immediately, per the SDK. So an absent deadline must
    # reach wait_condition as None, not as a zero duration.
    seen: dict[str, Any] = {}

    async def capture(fn, **kw):
        seen["timeout"] = kw.get("timeout")

    monkeypatch.setattr(waits.workflow, "wait_condition", capture)
    await waits.await_inputs(lambda: True, None)
    assert seen["timeout"] is None


async def test_waiting_for_a_person_behaves_the_same_way(monkeypatch):
    async def times_out(fn, **kw):
        raise TimeoutError

    monkeypatch.setattr(waits.workflow, "wait_condition", times_out)
    assert await waits.await_decision(lambda: False, "P5D") is False


# --- the activity boundary ---------------------------------------------------


async def test_shadow_mode_reaches_nothing():
    # The default. Nothing is ever sent, and the chain from `effect: notify`
    # through capability and binding still runs in full.
    tools = RecordingTools()
    result = await Capabilities(tools).invoke(
        CapabilityCall(capability="email.send", args={"to": "receiving_supervisor"})
    )
    assert result.shadowed is True
    assert tools.calls == []


async def test_live_mode_dispatches_through_the_capability_key():
    tools = RecordingTools()
    result = await Capabilities(tools, mode="live").invoke(
        CapabilityCall(capability="email.send", args={"to": "x"})
    )
    assert result.ok
    assert not result.shadowed
    assert tools.calls == [("email.send", {"to": "x"})]


async def test_the_same_message_is_not_sent_twice():
    # A shipment resumes when corrected paperwork arrives and the check re-runs.
    # Without a dedupe key the supervisor is emailed the identical discrepancy
    # a second time, and stops reading them.
    caps = Capabilities(RecordingTools(), mode="live")
    call = CapabilityCall(capability="email.send", args={"to": "x"}, idempotency_key="ship-1:a,b")

    first = await caps.invoke(call)
    second = await caps.invoke(call)

    assert first.output.get("deduplicated") is None
    assert second.output["deduplicated"] is True


async def test_a_changed_message_is_sent():
    # Not "send once" — once per distinct situation. One batch arriving changes
    # the key, and the new discrepancy must still go out.
    tools = RecordingTools()
    caps = Capabilities(tools, mode="live")
    await caps.invoke(CapabilityCall("email.send", {"to": "x"}, idempotency_key="ship-1:a,b"))
    await caps.invoke(CapabilityCall("email.send", {"to": "x"}, idempotency_key="ship-1:b"))
    assert len(tools.calls) == 2


async def test_a_binding_error_comes_back_rather_than_raising():
    # Raising would let the retry policy burn attempts on a misconfiguration
    # every case shares. It returns so the trace records it once.
    result = await Capabilities(RefusingTools(), mode="live").invoke(
        CapabilityCall(capability="email.send", args={})
    )
    assert result.ok is False
    assert "BindingError" in (result.error or "")


def test_the_activity_is_registered_under_a_stable_name():
    # Generated code and the worker refer to it by this name. Renaming the
    # Python method must not change what a running workflow is waiting on.
    assert Capabilities.invoke.__temporal_activity_definition.name == "invoke_capability"


@pytest.mark.parametrize("mode", ["shadow", "live"])
async def test_every_mode_returns_the_same_shape(mode: str):
    result = await Capabilities(RecordingTools(), mode=mode).invoke(
        CapabilityCall(capability="email.send", args={"to": "x"})
    )
    assert isinstance(result.ok, bool)
    assert isinstance(result.output, dict)
