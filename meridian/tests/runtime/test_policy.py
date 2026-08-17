"""How a failure is classified, and what happens to it.

Four error classes, three dispositions. The classes exist because they read
differently to a human; the dispositions exist because there are only three
things a running process can do about a failure.
"""

from typing import get_args

import pytest

from meridian.domain.primitives import OnFailure
from meridian.runtime.errors import (
    AgentError,
    BindingError,
    NeedsHumanError,
    RetryableError,
    TerminalError,
)
from meridian.runtime.policy import Disposition, disposition_of, retry_policy


def test_the_taxonomy_collapses_to_three_dispositions():
    # If it were four, one class would be doing no work; if it were two, the
    # taxonomy would be hiding a decision the process owner should make.
    classes = (RetryableError, TerminalError, NeedsHumanError, BindingError)
    assert len({disposition_of(cls()) for cls in classes}) == 3


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (RetryableError("gmail timed out"), "retry"),
        (TerminalError("the scan is unreadable"), "route"),
        (NeedsHumanError("two batches match equally well"), "escalate"),
        (BindingError("no address for receiving_supervisor"), "route"),
    ],
)
def test_each_class_disposes_the_way_its_name_promises(error: AgentError, expected: Disposition):
    assert disposition_of(error) == expected


def test_an_unrecognised_error_is_retried_rather_than_swallowed():
    # An exception nobody classified is a bug, and a bug that silently routes to
    # a business outcome would be reported to a supervisor as a real finding.
    assert disposition_of(ValueError("something nobody anticipated")) == "retry"


def test_a_configuration_error_is_never_retried():
    # Retrying a missing binding wastes the policy and buries the real message.
    # It routes so the trace records it, but it must not consume attempts.
    policy = retry_policy(on_failure="fail")
    assert "BindingError" in policy.non_retryable


def test_on_failure_wait_gets_more_attempts_than_on_failure_fail():
    # The process owner said what should happen when a lookup does not answer.
    # `fail` means give up immediately; `wait` means keep trying.
    assert retry_policy(on_failure="wait").max_attempts > retry_policy("fail").max_attempts


def test_on_failure_fail_does_not_retry_at_all():
    assert retry_policy(on_failure="fail").max_attempts == 1


def test_the_default_policy_backs_off_rather_than_hammering():
    assert retry_policy().backoff > 1.0


def test_the_vocabulary_is_the_one_the_process_owner_chose_from():
    # `on_failure` is a closed set on the card. Taking a bare `str` here meant a
    # typo silently produced the default policy instead of the one asked for.
    assert set(get_args(OnFailure)) == {"fail", "wait", "skip"}
    for value in get_args(OnFailure):
        assert retry_policy(value).max_attempts >= 1
