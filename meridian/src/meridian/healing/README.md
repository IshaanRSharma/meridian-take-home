# healing

Build to better build. Run the eval cases, work out which file to open, make the
failure pasteable, and refuse a patch that broke something.

This is the half of the system where ground truth is a test suite rather than a
person. Nothing here asks anybody anything.

| file | what it does |
|---|---|
| `sweep.py` | run every eval case through a generated agent, store what happened |
| `compare.py` | expected against actual, one column at a time |
| `localize.py` | from a column that disagreed to the file somebody should open |
| `bundle.py` | the block somebody pastes. This is the product. |
| `gate.py` | target passes and nothing that passed before now fails |
| `record.py` | artifacts a skill wrote, read back into the database |

## The agent runs itself

The sweep is generic and the agent is not. So `build.json` names an entry point
exposing one coroutine, `run_case(case) -> CaseOutcome`, and everything Temporal
happens inside the generated agent: starting an environment, registering a worker,
sending the signals. The sweep loads it, compares it and stores it.

That split is why there is no long-running worker in this path. The generated
harness calls `WorkflowEnvironment.start_time_skipping()`, which boots a real
Temporal server in-process and jumps the clock to the next timer, so a deadline
measured in days resolves in milliseconds and the suite is fast enough to run after
every patch.

## Comparing per column, not per row

This looks like a detail and it is the gate working. A patch that fixes one column
and breaks another leaves the row failing before and failing after, so a row-level
comparison sees nothing happen and lets the regression through. The eval set is a
table of counts, so it is compared as a table of counts.

## Localisation is three lookups, none of them inferred

```
column  →  the primitive whose `fills` target it     from the frozen spec
        →  build.json's file_map                     from whoever generated it
        →  the file
```

Every link is a fact somebody wrote down. The alternative was a `[from primitive
X]` header comment convention in the generated files, which fails silently the
moment a file is renamed.

## bundle.py is the deliverable

Everything else in this package is a terminal. A sweep is a loop, a gate is a
comparison, a repair is a row. What the platform actually sells is that a failure
becomes legible enough that one paste fixes it, and the test for that is brutal:
can somebody who has never seen this repo fix the bug from the block alone, with no
further lookup. So the bundle carries the expected and actual row, the trace, the
file, the spec context for that primitive, and the repair history for that
signature.

The repair history matters more here than in an autonomous loop. Without it an
engineer retries the same failed approach across sessions.

## gate.py can only reject

The only automatic decision in the pipeline. A human is required to override it and
never to approve in its place, which is what makes an override a deliberate act
rather than a rubber stamp. The regression set is the previously-passing cases,
recomputed each iteration rather than fixed at the start.

## record.py points the other way

The bundle is the CLI writing text for a skill to read. `record.py` is a skill
writing files for the CLI to read. A human in a terminal with a coding agent
produces no rows at all by default, and must not, because a loop that needs a
connection string is a loop that only works on the machine that has one.
