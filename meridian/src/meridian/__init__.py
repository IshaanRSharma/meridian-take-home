"""Meridian — turns tacit process knowledge into a running, self-repairing agent.

The pipeline, and the two loops it is made of::

    whiteboard  →  AI review loop  →  frozen spec  →  codegen  →  Temporal  →  self-heal
     (mutable)      (human oracle)     (immutable)     (agent)    (durable)   (eval oracle)

Before the freeze, ground truth lives in a person's head, so the loop asks a
human. After it, ground truth lives in the eval suite, so the loop asks a test
suite. The freeze is where authority transfers from a person to a test suite —
which is why Submit exists and why the spec is immutable.

``Claude.md`` at the repository root carries the design and the reason behind
every schema decision. Section numbers referenced in docstrings point there.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("meridian")
except PackageNotFoundError:  # pragma: no cover - source checkout without an install
    __version__ = "0.0.0.dev0"

__all__ = ["__version__"]
