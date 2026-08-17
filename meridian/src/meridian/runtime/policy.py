"""Typed error mapping: what a failure means for control flow.

Four error classes collapse to three dispositions, because there are only three
things a running process can do about a failure — try again, take the path the
process owner drew for it, or ask a person.

The retry configuration here is provider-agnostic. ``workflow/`` translates it
into a Temporal ``RetryPolicy``; nothing in this module imports Temporal, so the
mapping stays testable without a server.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from meridian.runtime.errors import BindingError, NeedsHumanError, RetryableError, TerminalError

Disposition = Literal["retry", "route", "escalate"]

# Deliberately not a method on the exception classes. The classes describe what
# happened; this is policy about what to do, and the healing loop is entitled to
# a different policy than the worker without redefining the vocabulary.
_DISPOSITIONS: dict[type[BaseException], Disposition] = {
    RetryableError: "retry",
    TerminalError: "route",
    NeedsHumanError: "escalate",
    BindingError: "route",
}

# `wait` keeps trying because the process owner said the answer is worth waiting
# for; `fail` gives up at once because they said it is not. `skip` continues
# without the answer, so one attempt is all it gets.
_ATTEMPTS: dict[str, int] = {"fail": 1, "skip": 1, "wait": 8}
_DEFAULT_ATTEMPTS = 3


@dataclass(frozen=True)
class RetryPolicy:
    """Provider-agnostic retry configuration for one step."""

    max_attempts: int
    initial_interval_s: float
    backoff: float
    non_retryable: tuple[str, ...]


def disposition_of(error: BaseException) -> Disposition:
    """What should happen to this failure.

    An error nobody classified is retried rather than routed. Routing it would
    report a bug to a supervisor as though it were a real business finding,
    which is the worse of the two mistakes.
    """
    for error_type, disposition in _DISPOSITIONS.items():
        if isinstance(error, error_type):
            return disposition
    return "retry"


def retry_policy(on_failure: str | None = None) -> RetryPolicy:
    """The policy for a step, derived from what the process owner chose.

    ``BindingError`` is never retried at any setting: every case would fail the
    same way, so attempts are spent proving something already known.
    """
    return RetryPolicy(
        max_attempts=_ATTEMPTS.get(on_failure or "", _DEFAULT_ATTEMPTS),
        initial_interval_s=1.0,
        backoff=2.0,
        non_retryable=(BindingError.__name__, TerminalError.__name__, NeedsHumanError.__name__),
    )
