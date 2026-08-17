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
    """Composio, which holds the OAuth grant for one customer's connection.

    The credential is not here and never reaches the spec, the bindings file or
    generated code. Composio keys the grant by a user id; this passes that id and
    nothing else, which is why a regenerated agent directory cannot cost a
    mailbox connection.

    The client is injected rather than constructed, which keeps this testable
    without a key — and, more importantly, leaves **pinning the toolkit version**
    to whoever builds the client:

        Composio(toolkit_versions={"gmail": "20260815_00"})

    That is not optional. Manual execution refuses ``latest`` outright, and the
    reason it refuses is the reason to record the pin: the same spec against a
    different toolkit version is a different build, exactly as the same spec
    against a different model is. The version belongs beside the entity id in
    the bindings file.
    """

    def __init__(self, client: Any, user_id: str) -> None:
        """Take an already-authenticated, version-pinned client and a user id."""
        self._client = client
        self._user_id = user_id

    def execute(self, action: str, args: Mapping[str, Any]) -> Mapping[str, Any]:
        """Run one Composio action for this user, and unwrap the envelope.

        Composio reports a failed action by returning ``successful: False``
        rather than by raising, so a caller that only caught exceptions would
        read an error as an empty-but-fine result — and an empty-but-fine result
        is how a check passes for the wrong reason.
        """
        if self._client is None:
            msg = "Composio is not connected; run `mvp connect check` before a live run"
            raise BindingError(msg)
        try:
            # The SDK is untyped, so the boundary narrows rather than trusting it.
            envelope: Mapping[str, Any] = self._client.tools.execute(
                action, user_id=self._user_id, arguments=dict(args)
            )
        except Exception as error:
            # A provider that is down, rate-limited or slow is retryable. Getting
            # this wrong in the other direction is worse: a business outcome
            # would be reported to a supervisor because a network blipped.
            msg = f"{action} failed: {error}"
            raise RetryableError(msg) from error

        if not envelope.get("successful", False):
            msg = f"{action} failed: {envelope.get('error') or 'no reason given'}"
            raise RetryableError(msg)
        return dict(envelope.get("data") or {})
