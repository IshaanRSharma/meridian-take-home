"""The dependency rule, enforced rather than described.

`Claude.md` §3 states the import table and calls it "greppable, so it stays
true". Greppable is exactly how a rule stops being true: nobody greps, and the
first upward import arrives inside a change that was about something else.

Two properties, and the first carries the weight. **`domain/` imports nothing
internal**, which is what makes it safe for every other package to depend on and
what stops policy leaking into the types everyone shares. The second is the rest
of the table, of which the load-bearing row is `runtime/` — generated agents
import it, and an agent that could reach the compiler would be a running process
carrying the whole authoring toolchain.

An import that ruff can see is an import this can see: both read the same source
with `ast`, so a module hidden behind a function-local import would slip past.
That is a knowable limit rather than a hole to paper over — the rule is about
which packages know about which, and a local import is still the module knowing.
"""

import ast
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "src" / "meridian"

# From Claude.md §3, verbatim in effect. A package may import itself, `core`,
# and whatever is listed. `api` is the composition root and may reach anything
# except `runtime`, which belongs to generated code.
MAY_IMPORT: dict[str, set[str]] = {
    "domain": set(),
    "core": {"domain"},
    "repositories": {"domain"},
    "compiler": {"domain"},
    # Deliberately NOT `compiler`. Lint is the caller's business: if authoring
    # could import `rules`, refusing an edit because of a lint finding is one
    # import away — and that would make the canvas modal and delete the very
    # findings the review loop runs on.
    "authoring": {"domain", "repositories"},
    "reviewer": {"domain", "compiler", "repositories"},
    # There is no `codegen` package and there will not be one: generating an
    # agent is a skill a human runs, not a service. `healing` reaches `runtime`
    # instead, and for the same reason `worker` does — it EXECUTES generated
    # agents and reads the two things they hand back, `CheckResult` and the
    # trace. Rule 4 asks an agent to keep the trace's shape; validating that on
    # arrival is what makes it a rule rather than a comment. The arrow points
    # healing → runtime, so the property this table exists to protect — that
    # generated code cannot reach the toolchain — is untouched.
    "healing": {"domain", "repositories", "runtime"},
    # Imported by GENERATED agents, so it may know the types and nothing else.
    "runtime": {"domain"},
    "worker": {"domain", "runtime"},
    "api": {
        "domain",
        "core",
        "authoring",
        "compiler",
        "reviewer",
        "healing",
        "repositories",
    },
}


def packages() -> list[tuple[str, Path]]:
    """Every module under a package the table names, with the package it is in."""
    found = []
    for path in sorted(SOURCE.rglob("*.py")):
        relative = path.relative_to(SOURCE)
        package = relative.parts[0]
        if package in MAY_IMPORT:
            found.append((package, path))
    return found


def imported_packages(path: Path) -> set[str]:
    """Which `meridian.<package>` this module reaches for, at any depth."""
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
        elif isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
    return {
        name.split(".")[1]
        for name in names
        if name.startswith("meridian.") and len(name.split(".")) > 1
    }


@pytest.mark.parametrize(
    ("package", "path"), packages(), ids=lambda value: getattr(value, "name", value)
)
def test_no_package_imports_upward(package: str, path: Path) -> None:
    # `events` sits beside `core` in the blanket allowance rather than in a row
    # of its own. Every phase in the table it writes — review, compile, codegen,
    # eval, repair, deploy, prod — is a different package, so listing it per row
    # would be listing it everywhere. `domain/` is still held to nothing by the
    # separate test below, which is the exemption that would have mattered.
    allowed = MAY_IMPORT[package] | {package, "core", "events"}
    reached = imported_packages(path)

    assert reached <= allowed, (
        f"{path.relative_to(SOURCE)} imports {sorted(reached - allowed)}, "
        f"which {package}/ may not reach"
    )


def test_the_types_everyone_shares_depend_on_nobody() -> None:
    # Stated separately from the table because it is the property the table
    # exists to protect. Domain types are facts every consumer agrees on; the
    # moment they can import a consumer, one consumer's policy is in all of them.
    for _, path in packages():
        if path.relative_to(SOURCE).parts[0] != "domain":
            continue
        assert imported_packages(path) <= {"domain"}, path


def test_generated_code_cannot_reach_the_toolchain() -> None:
    # `runtime/` is what an agent under `agents/` imports. If it could reach the
    # compiler or the reviewer, every deployed agent would be carrying the
    # authoring toolchain, and the path-confinement check in codegen would be
    # guarding a door in a wall with a hole in it.
    for package, path in packages():
        if package != "runtime":
            continue
        assert imported_packages(path) <= {"runtime", "domain", "core"}, path


def test_the_table_covers_what_is_actually_here() -> None:
    # A package added without a row would be silently exempt, which is the one
    # way this test can rot into decoration.
    present = {
        path.relative_to(SOURCE).parts[0]
        for path in SOURCE.rglob("*.py")
        if path.relative_to(SOURCE).parts[0] != path.name
    }

    assert present <= set(MAY_IMPORT), f"no import rule for {sorted(present - set(MAY_IMPORT))}"
