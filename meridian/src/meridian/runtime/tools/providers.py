"""The two providers: one that records, one that reaches Composio.

Recording is the default everywhere — the eval suite, the demo, and any run
nobody explicitly put into live mode. Nothing is sent. The chain from
``effect: notify`` through capability, registry and provider still runs in full,
and a recorded call is more assertable than a delivered message: *this action was
invoked once, with these batch numbers* is a test expectation.
"""

from __future__ import annotations

import csv
from collections.abc import Mapping, Sequence
from pathlib import Path
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
            # Names the state, not a command: there is no `connect` command yet,
            # and an error inviting somebody to run one that does not exist wastes
            # the one moment they were willing to follow an instruction.
            msg = (
                "Composio is not connected: no authenticated client was supplied "
                "for this user. A live run needs one; the fixture harness does not."
            )
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


class TableProvider:
    """Answers reads from a table held in memory.

    The read-shaped counterpart to :class:`RecordingProvider`. Both shipped
    providers were write-shaped, which meant a ``lookup`` had nothing that could
    return a record and the whole third direction of data movement was
    untestable.

    Matching is by filter: every key in the arguments must equal the same key on
    a row. That is deliberately the dumbest thing that works — a fixture table
    is not a query engine, and anything cleverer would be a second
    implementation of a system we do not own.
    """

    def __init__(self, rows: Sequence[Mapping[str, Any]]) -> None:
        """Take the table this environment answers from."""
        self._rows = [dict(row) for row in rows]

    def execute(self, action: str, args: Mapping[str, Any]) -> Mapping[str, Any]:  # noqa: ARG002
        """The first row matching every supplied argument, or nothing.

        Nothing rather than an error: a licence the registry has never heard of
        is a real answer, and the Check that reads the result is what decides
        whether that is a failure.
        """
        for row in self._rows:
            if all(row.get(key) == value for key, value in args.items()):
                return dict(row)
        return {}


class CsvProvider:
    """Writes records to a CSV, standing in for a customer's own system.

    An Action with ``effect: record`` names a system in the process owner's own
    words — *"the receiving log"* — and in production that binds to their WMS.
    Here it binds to a file, which is enough to compare a run against the
    evaluation set and costs no integration.

    Entirely schema-free: the header comes from the first record's keys and
    later records fill the columns they have. Teaching this class what a
    shipment row looks like would make one customer's process part of the
    runtime.
    """

    def __init__(self, path: Path) -> None:
        """Take the file to append to. Created on first write."""
        self._path = Path(path)

    def execute(self, action: str, args: Mapping[str, Any]) -> Mapping[str, Any]:  # noqa: ARG002
        """Append one record, writing a header if the file is new.

        ``action`` is unused: the protocol carries it, and a file has one
        behaviour whichever action name resolved here.
        """
        record = dict(args.get("record") or args)
        if not record:
            return {"written": 0}

        existing = self._header()
        columns = existing or list(record)
        new_file = existing is None

        with self._path.open("a", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
            if new_file:
                writer.writeheader()
            writer.writerow(record)
        return {"written": 1, "path": str(self._path)}

    def _header(self) -> list[str] | None:
        if not self._path.exists() or self._path.stat().st_size == 0:
            return None
        with self._path.open(newline="") as handle:
            return next(csv.reader(handle), None)
