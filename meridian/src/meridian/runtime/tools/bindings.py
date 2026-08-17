"""Who a role actually is, for this customer.

The spec names a role — ``receiving_supervisor``. Bindings say that is
``ops-sup@aurologistics.com``. The split exists because the spec is checksummed
and immutable, so an identity inside it would make a personnel change force a
new spec version.

Takes a mapping rather than a path. Whether the customer's file is YAML, JSON or
a database row is the worker's problem; the runtime should not grow a parser or
a dependency to find out.
"""

from __future__ import annotations

from collections.abc import Mapping

from meridian.runtime.errors import BindingError


class Bindings:
    """Roles resolved to whoever fills them here."""

    def __init__(self, roles: Mapping[str, str]) -> None:
        """Take the already-loaded role table."""
        self._roles = dict(roles)

    def role(self, name: str) -> str:
        """The recipient filling a role, or a failure naming the missing entry.

        Naming it matters: the fix is one line in a file, and an error that says
        only "binding failed" sends someone reading code instead of editing YAML.
        """
        try:
            return self._roles[name]
        except KeyError:
            known = ", ".join(sorted(self._roles)) or "nothing"
            msg = f"no binding for role {name!r}; this customer defines {known}"
            raise BindingError(msg) from None
