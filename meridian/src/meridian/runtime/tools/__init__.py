"""Reaching outside the process, addressed by capability.

Three layers, and generated code only ever sees the first:

    generated code   ctx.tools.call("email.send", {...})
    registry         email.send → composio · GMAIL_SEND_EMAIL
    provider         holds the credential, or records instead of sending

Nothing here is imported by a Check. A Check derives no capabilities, which is
what keeps it in workflow code.
"""

from meridian.runtime.tools.bindings import Bindings
from meridian.runtime.tools.dispatch import Provider, Tool, Tools
from meridian.runtime.tools.providers import (
    ComposioProvider,
    CsvProvider,
    RecordingProvider,
    TableProvider,
)

__all__ = [
    "Bindings",
    "ComposioProvider",
    "CsvProvider",
    "Provider",
    "RecordingProvider",
    "TableProvider",
    "Tool",
    "Tools",
]
