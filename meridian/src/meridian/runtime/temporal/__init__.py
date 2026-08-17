"""Every ``temporalio`` import in the runtime lives here and nowhere else.

Confining it is what keeps the type layer, the error policy and the whole check
engine testable with nothing installed — and what makes swapping the durable
execution engine a change to one directory rather than a rewrite.

The workflow itself is not here. It is generated into
``agents/<slug>/workflow.py``; this package is the adapters that workflow uses.
"""

from meridian.runtime.temporal.activities import (
    Capabilities,
    CapabilityCall,
    CapabilityResult,
)
from meridian.runtime.temporal.waits import await_decision, await_inputs

__all__ = [
    "Capabilities",
    "CapabilityCall",
    "CapabilityResult",
    "await_decision",
    "await_inputs",
]
