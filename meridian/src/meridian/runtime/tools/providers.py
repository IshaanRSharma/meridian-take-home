"""The two providers: one that records, one that reaches Composio.

Recording is the default everywhere — the eval suite, the demo, and any run
nobody explicitly put into live mode. Nothing is sent. The chain from
``effect: notify`` through capability, registry and provider still runs in full,
and a recorded call is more assertable than a delivered message: *this action was
invoked once, with these batch numbers* is a test expectation.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from meridian.runtime.errors import BindingError, RetryableError


class RecordingProvider:
    """Performs nothing, and remembers everything it was asked to perform."""

    def __init__(self) -> None:
        """Start with an empty log."""
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, action: str, args: Mapping[str, Any]) -> Mapping[str, Any]:
        """Record the call and report success."""
        self.calls.append((action, dict(args)))
        return {"recorded": True, "action": action}


class ComposioProvider:
    """Composio, which holds the OAuth grant for one customer's mailbox.

    The credential is not here and never reaches the spec, the bindings file or
    generated code. Composio keys the grant by ``entity_id``; this passes that id
    and nothing else, which is why a regenerated agent directory cannot cost a
    mailbox connection.

    The client is injected rather than constructed. The SDK's action names move —
    ``GMAIL_FETCH_EMAILS`` and friends should be checked against current docs
    rather than trusted from a design note — and injecting keeps this class
    testable without a key.
    """

    def __init__(self, client: Any, entity_id: str) -> None:
        """Take an already-authenticated client and the customer's entity id."""
        self._client = client
        self._entity_id = entity_id

    def execute(self, action: str, args: Mapping[str, Any]) -> Mapping[str, Any]:
        """Run one Composio action for this entity."""
        if self._client is None:
            msg = "Composio is not connected; run `mvp connect check` before a live run"
            raise BindingError(msg)
        try:
            # The SDK is untyped, so the boundary narrows rather than trusting it.
            result: Mapping[str, Any] = self._client.execute(
                action, entity_id=self._entity_id, **dict(args)
            )
        except Exception as error:
            # A provider that is down, rate-limited or slow is retryable. Getting
            # this wrong in the other direction is worse: a business outcome
            # would be reported to a supervisor because a network blipped.
            msg = f"{action} failed: {error}"
            raise RetryableError(msg) from error
        return dict(result)
