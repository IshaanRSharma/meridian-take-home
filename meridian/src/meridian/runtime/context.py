"""What a generated agent is handed — and, as much, what it is denied.

The contract is defined by its absences. There is no clock to call, no provider
to name, and no address to read. A generated Check cannot be nondeterministic
about time because there is nothing to ask; it cannot hardcode Gmail because it
never learns that ``email.send`` is Gmail.

Tools and bindings are Protocols so this module depends on no implementation.
``tools/`` provides the real ones and the recording one; the harness provides
fixtures.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from meridian.runtime.errors import BindingError
from meridian.runtime.trace import RunTrace


@runtime_checkable
class ToolBox(Protocol):
    """Every reach outside the process, addressed by capability key."""

    def call(self, capability: str, args: Mapping[str, Any]) -> Mapping[str, Any]:
        """Invoke a capability. The provider behind it is not the caller's business."""
        ...


@runtime_checkable
class Bindings(Protocol):
    """Per-customer identities, resolved at run time and never in the spec."""

    def role(self, name: str) -> str:
        """The recipient filling a role. A personnel change edits YAML, not a spec."""
        ...


class UnboundTools:
    """A toolbox that refuses.

    The default, so that reaching for the world is a loud failure rather than a
    silent empty result. A Check that calls this has a design error, and a stub
    returning ``{}`` would let it pass.
    """

    def call(self, capability: str, args: Mapping[str, Any]) -> Mapping[str, Any]:
        """Refuse, naming the capability nobody bound."""
        msg = f"no provider bound for capability {capability!r}"
        raise NotImplementedError(msg)


class UnboundBindings:
    """Bindings with no file loaded. Every role raises, naming the missing entry."""

    def role(self, name: str) -> str:
        """Refuse, naming the role nobody bound."""
        msg = f"no binding for role {name!r}"
        raise BindingError(msg)


@dataclass(frozen=True)
class AgentContext:
    """The single argument every generated step receives.

    ``clock`` is a **frozen ISO-8601 string, not a callable**. A criterion whose
    right-hand operand is ``now`` reads this value, so two criteria in one case
    compare against one instant and a case cannot pass and fail itself. Making
    the clock a value rather than a function removes the surface entirely rather
    than policing it.
    """

    tools: ToolBox
    bindings: Bindings
    clock: str
    trace: RunTrace
    hints: Mapping[str, Any] = field(default_factory=dict)
