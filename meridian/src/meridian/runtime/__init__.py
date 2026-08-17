"""A scaffold a generated agent starts from. Not a fence.

The `spec-to-agent` skill uses what fits here and replaces what does not. Only
three modules are genuinely contracts, and each because something *outside* the
agent reads them: ``outcome`` (the eval harness compares CheckResults),
``trace`` (the failure bundle is assembled from it), and ``context`` (reaching
the world only through a capability key is what keeps addresses, provider names
and credentials out of generated code). ``errors``, ``policy`` and ``check`` are
useful defaults — a generated agent may ignore them.

One public surface, so a generated file has one import to remember rather than
a map of the package.

Nothing in this package imports the compiler, the reviewer, the repositories or
the API. A worker process should not carry the platform, and the Temporal
workflow sandbox reloads whatever it can see.

The whole package is free of module-level mutable state and does no I/O at
import, which is what makes it safe to pass through the sandbox:

    with workflow.unsafe.imports_passed_through():
        from meridian.runtime import CheckResult, RunTrace

Without that, every workflow run reloads the entire check engine.
"""

from meridian.runtime.context import (
    AgentContext,
    Bindings,
    ToolBox,
    UnboundBindings,
    UnboundTools,
)
from meridian.runtime.errors import (
    AgentError,
    BindingError,
    NeedsHumanError,
    RetryableError,
    TerminalError,
)
from meridian.runtime.outcome import CheckResult, Failure
from meridian.runtime.policy import Disposition, RetryPolicy, disposition_of, retry_policy
from meridian.runtime.trace import RunTrace, Step, StepRecorder, ToolCall

__all__ = [
    "AgentContext",
    "AgentError",
    "BindingError",
    "Bindings",
    "CheckResult",
    "Disposition",
    "Failure",
    "NeedsHumanError",
    "RetryPolicy",
    "RetryableError",
    "RunTrace",
    "Step",
    "StepRecorder",
    "TerminalError",
    "ToolBox",
    "ToolCall",
    "UnboundBindings",
    "UnboundTools",
    "disposition_of",
    "retry_policy",
]
