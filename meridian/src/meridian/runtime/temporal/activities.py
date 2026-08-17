"""The activity boundary — everything that touches the world crosses it here.

One generic activity, not one per Action. An Action's generated file builds a
request and interprets a result, both pure; the transport is this. That keeps
retry, idempotency and shadow mode in a single implementation, keeps every
provider name out of generated code, and leaves the repair loop a network-free
file to patch when an Action misbehaves.

``capabilities`` on a card is what decides whether it crosses this line at all. A
Check derives none, so it stays in workflow code and a failing eval case fails
for a logic reason rather than because a network was slow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from temporalio import activity

from meridian.runtime.context import ToolBox
from meridian.runtime.errors import BindingError


@dataclass
class CapabilityCall:
    """One request to reach outside the process.

    A single dataclass rather than several arguments, following Temporal's own
    guidance: a long-running workflow outlives the signature it was first given,
    and adding a field here does not break instances already in flight.
    """

    capability: str
    args: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str | None = None


@dataclass
class CapabilityResult:
    """What came back, and whether anything actually left the building."""

    ok: bool
    output: dict[str, Any] = field(default_factory=dict)
    shadowed: bool = False
    error: str | None = None


class Capabilities:
    """The activity implementation, holding the toolbox it dispatches through.

    A class rather than a bare function because the activity needs a dependency
    injected at worker construction — the standard shape for this in the SDK.
    The harness registers one built over fixture tools; production registers one
    built over Composio.
    """

    def __init__(self, tools: ToolBox, *, mode: str = "shadow") -> None:
        """``mode='shadow'`` records outward calls instead of making them."""
        self._tools = tools
        self._mode = mode
        self._seen: set[str] = set()

    @activity.defn(name="invoke_capability")
    async def invoke(self, call: CapabilityCall) -> CapabilityResult:
        """Dispatch one capability, or record that it would have been dispatched.

        Deduplication is by ``idempotency_key`` and scoped to this worker. The
        durable guarantee lives in workflow state, where history replays it; this
        is a cheap second line for a retried activity whose first attempt already
        succeeded but whose acknowledgement was lost.
        """
        if call.idempotency_key and call.idempotency_key in self._seen:
            return CapabilityResult(ok=True, shadowed=True, output={"deduplicated": True})

        if self._mode == "shadow":
            self._remember(call)
            # Empty, never the arguments. Echoing them made a `lookup` compare a
            # value against itself — a fabricated container number matched the
            # record "returned" for it, and the sweep went GREEN. Shadowing is
            # meaningful for a write and corrupting for a read, so the read gets
            # nothing and the Check that wanted it fails honestly.
            return CapabilityResult(ok=True, shadowed=True, output={})

        try:
            output = self._tools.call(call.capability, call.args)
        except BindingError as error:
            # Every case will fail identically, so this must not be retried.
            # `policy.retry_policy` lists it as non-retryable by name.
            return CapabilityResult(ok=False, error=f"BindingError: {error}")

        self._remember(call)
        return CapabilityResult(ok=True, output=dict(output))

    def _remember(self, call: CapabilityCall) -> None:
        if call.idempotency_key:
            self._seen.add(call.idempotency_key)
