"""What can go wrong at runtime, named by what should happen next.

Distinct from ``domain/errors.py``, which names what goes wrong at the API
boundary. These are raised inside a running agent and read by the retry policy.

Four classes, because they read differently to a person looking at a trace.
Three dispositions, because there are only three things a running process can
actually do — see ``policy.py``.
"""

from __future__ import annotations


class AgentError(Exception):
    """Base for every failure a generated agent raises deliberately."""


class RetryableError(AgentError):
    """Transient. The same call later would probably succeed.

    A tool timeout, a rate limit, a 5xx. Nothing about the case is wrong.
    """


class TerminalError(AgentError):
    """Permanent for this case, and the process has a path for it.

    An unreadable scan, a document matching no known type. Retrying cannot help,
    but the process owner drew an exception edge for exactly this, so it routes
    rather than crashing.
    """


class NeedsHumanError(AgentError):
    """Ambiguous, and guessing would be worse than asking.

    Low extraction confidence, two batches matching equally well. An agent that
    escalates here is safer than one that picks, and the escalation becomes a
    new eval case — which is the loop closing from production back to the suite.
    """


class BindingError(AgentError):
    """The deployment is misconfigured, not the case.

    A role with no address, a capability with no provider. Every case will fail
    the same way, so retrying burns the policy and buries the message.
    """
