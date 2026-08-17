"""Repository tests run against the local container, and commit nothing.

The ``connection`` fixture they use lives in ``tests/conftest.py`` — the
reviewer needs the same one, because a round is a transaction over a board.
"""
