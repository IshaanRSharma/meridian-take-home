"""Unit 0's only assertion: the toolchain is real.

The package resolves a version from installed metadata rather than a literal,
so this failing means the environment is not actually installed — which is the
one thing worth catching before any domain code exists.
"""

import meridian


def test_package_is_installed_and_reports_a_version() -> None:
    assert meridian.__version__ != "0.0.0.dev0"
