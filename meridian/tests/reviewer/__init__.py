"""Makes ``tests.reviewer.test_scenarios`` distinct from the repository test of the same name.

Both directories mirror a package that has a `scenarios` module, so both want
the file name. Without this, pytest's rootdir import mode gives them the same
module name and collection fails on whichever it reaches second.
"""
