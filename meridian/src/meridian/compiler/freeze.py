"""The gate, and the moment authority changes hands.

Before the freeze, ground truth about this process lives in a person's head, so
the loop asks a human. After it, ground truth lives in an eval suite, so the loop
asks a test suite. Everything else in the compiler exists to make this one
transfer safe, which is why the interesting behaviour here is what it *refuses*.

A refusal carries the findings themselves, never a count. Someone has to be told
which three things nobody wrote down, in the words the card already uses — a
gate that reports "3 problems" has turned back into a printout.

Pure, and takes no connection. Version comes from the previous spec rather than
from a query, so the whole gate reads in one place and persistence is left with
one job: fetch the latest, insert the next.
"""

from meridian.compiler import rules, serialize
from meridian.domain.errors import ConflictingStateError, IncompleteError
from meridian.domain.frozen import FrozenSpec
from meridian.domain.graph import Board, BoardFinding
from meridian.domain.review import Assertion, Thread


class BoardNotReadyError(IncompleteError):
    """A board that cannot be frozen yet, and everything standing in the way.

    Subclasses ``IncompleteError`` so the boundary keeps mapping it to 422 with
    findings. It carries its payload rather than a message because both callers
    — the CLI and the API — have to render the list, and re-deriving it from
    prose is how the two drift apart.
    """

    def __init__(self, findings: list[BoardFinding], unsettled: list[Thread]) -> None:
        """Hold the blanks and the open questions, so a caller renders rather than re-derives."""
        self.findings = findings
        self.unsettled = unsettled
        super().__init__(f"{len(findings)} blocking findings, {len(unsettled)} open questions")


def freeze(
    board: Board,
    assertions: tuple[Assertion, ...] = (),
    threads: tuple[Thread, ...] = (),
    previous: FrozenSpec | None = None,
) -> FrozenSpec:
    """Seal a board into the spec a code generator is handed.

    Refuses on three counts, and only the first two are about the board being
    incomplete. The third is about a version meaning something: re-submitting an
    unchanged board would otherwise mint v2 with identical content, and "which
    spec is this build against" stops being a useful question.
    """
    blocking = rules.blocking(board)
    unsettled = [thread for thread in threads if not thread.is_settled()]
    if blocking or unsettled:
        raise BoardNotReadyError(blocking, unsettled)

    version = previous.version + 1 if previous else 1
    spec = serialize.spec_payload(board, assertions=assertions, version=version)

    # Content, not the counter — `version` is outside the checksum precisely so
    # this comparison is possible.
    if previous is not None and spec.checksum == previous.checksum:
        msg = f"nothing has changed since version {previous.version}"
        raise ConflictingStateError(msg)

    return spec
