"""Capability key to provider action, and nothing more.

One class, because the whole job is a dictionary lookup followed by a call. A
registry object, a dispatcher object and an adapter object would be three names
for the same sentence.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from meridian.runtime.errors import BindingError


@dataclass(frozen=True)
class Tool:
    """One registry row: what a capability resolves to."""

    provider: str
    action: str


@runtime_checkable
class Provider(Protocol):
    """Something that can perform a provider action."""

    def execute(self, action: str, args: Mapping[str, Any]) -> Mapping[str, Any]:
        """Perform it, or record that it would have been performed."""
        ...


class Tools:
    """The toolbox a generated agent is handed.

    Refuses rather than guessing. An unregistered capability and an unavailable
    provider are both configuration errors that every case shares, so they raise
    ``BindingError`` — which the retry policy lists as non-retryable, because
    retrying proves something already known.
    """

    def __init__(self, registry: Mapping[str, Tool], providers: Mapping[str, Provider]) -> None:
        """Take the registry and the providers already constructed."""
        self._registry = dict(registry)
        self._providers = dict(providers)

    def call(self, capability: str, args: Mapping[str, Any]) -> Mapping[str, Any]:
        """Resolve a capability and perform it."""
        tool = self._registry.get(capability)
        if tool is None:
            msg = f"no registry entry for capability {capability!r}"
            raise BindingError(msg)

        provider = self._providers.get(tool.provider)
        if provider is None:
            msg = f"capability {capability!r} needs provider {tool.provider!r}, which is not wired"
            raise BindingError(msg)

        return provider.execute(tool.action, args)
