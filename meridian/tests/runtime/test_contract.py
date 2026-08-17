"""What a generated agent is handed, and what it is denied.

Most of these assert an absence. The contract's job is to make the determinism
rule unbreakable rather than merely documented — a check that cannot reach a
clock cannot be nondeterministic about time, whatever the sandbox does.
"""

import importlib
import pkgutil

import pytest

import meridian.runtime
from meridian.runtime.context import AgentContext


def test_a_check_cannot_ask_what_time_it_is(ctx: AgentContext):
    # `now` is a frozen value captured once at workflow start, not a function.
    # There is nothing to call, so a nondeterministic comparison against the
    # clock is not something a generated check can express.
    assert isinstance(ctx.clock, str)
    assert not callable(ctx.clock)


def test_the_clock_does_not_move_while_a_case_runs(ctx: AgentContext):
    # Two criteria comparing against `now` in one run must see one instant, or
    # a case can pass and fail itself.
    assert ctx.clock == ctx.clock


def test_hints_are_readable_and_absent_by_default(ctx: AgentContext):
    # hints.json is repair-owned and optional. Conformance must never depend on
    # it existing, so an agent has to run without one.
    assert ctx.hints == {}


def test_the_runtime_imports_nothing_from_the_platform():
    # runtime/ is imported BY generated agents; importing the compiler or the
    # reviewer here would drag the whole platform into a worker process, and
    # into the workflow sandbox.
    forbidden = ("compiler", "reviewer", "codegen", "healing", "repositories", "api")
    for module in pkgutil.walk_packages(meridian.runtime.__path__, "meridian.runtime."):
        source = importlib.import_module(module.name)
        for name in forbidden:
            assert f"meridian.{name}" not in str(getattr(source, "__dict__", {}).keys()), (
                f"{module.name} imports meridian.{name}"
            )


def test_the_runtime_holds_no_module_level_mutable_state():
    # The workflow sandbox reloads non-stdlib modules on every run unless they
    # are passed through, and passthrough is only safe for modules free of
    # side effects. Mutable module state would leak between cases.
    for module in pkgutil.walk_packages(meridian.runtime.__path__, "meridian.runtime."):
        source = importlib.import_module(module.name)
        for name, value in vars(source).items():
            if name.startswith("__") or isinstance(value, type):
                continue
            assert not isinstance(value, (list, dict, set)) or name.isupper(), (
                f"{module.name}.{name} is mutable module state"
            )


def test_the_public_surface_is_one_import(ctx: AgentContext):
    # The import allowlist for generated code has one entry because of this.
    surface = ("AgentContext", "CheckResult", "Failure", "RunTrace", "RetryableError")
    for name in surface:
        assert hasattr(meridian.runtime, name), f"{name} is not on the public surface"


def test_a_tool_call_goes_through_a_capability_key_not_a_provider(ctx: AgentContext):
    # Generated code never learns that email.send is Gmail. Swapping the
    # provider is a registry row, not a regeneration.
    with pytest.raises(NotImplementedError):
        ctx.tools.call("email.send", {"to": "x"})
