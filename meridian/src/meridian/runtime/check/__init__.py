"""The check engine: rows in, a CheckResult out.

Six of the seven things a Check does are identical in every process — resolve
paths into rows, evaluate, tally, roll up, order outcomes, collect evidence,
project counts into the output. Only the predicate differs, which is why a
generated Check is a predicate and a call rather than a hand-rolled loop.
"""

from meridian.runtime.check.paths import PathError, Row, resolve

__all__ = ["PathError", "Row", "resolve"]
