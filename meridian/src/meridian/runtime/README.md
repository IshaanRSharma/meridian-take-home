# runtime

The contract generated agents import. A scaffold, not a framework.

This package is imported by code under `agents/`, and by nothing else in the
toolchain. The layering test enforces it in both directions: `runtime` may import
`domain` and nothing more, and no generated agent may import `compiler`,
`reviewer`, `authoring` or `healing`. If a deployed agent could reach the
toolchain, every deployment would be carrying the whole authoring stack.

| path | what it is |
|---|---|
| `context.py` | `AgentContext`: tools, clock, logger. What every step is handed. |
| `entities.py` | where the data a process reads actually lives |
| `ingest.py` | classify what arrived, and say "I am not sure" when it is not sure |
| `check/` | the check engine: criteria, counting, fills, field paths |
| `routing.py` | which step runs next, from the edge table |
| `policy.py` | typed errors to three dispositions: retry, route, ask a person |
| `outcome.py` | the return shape |
| `duration.py` | ISO 8601 durations, because they parse in three languages |
| `trace.py` | the step record a failure bundle reads |
| `harness.py` | headless execution against fixtures |
| `temporal/` | plain async functions wrapped as workflows and activities |
| `tools/` | capability key to provider, and the bindings that resolve a role |

## The determinism rule

Anything touching the world is an activity. Anything deciding is workflow code.

No `datetime.now()`, no `random`, no I/O above the activity boundary. Temporal
replays workflow code from history to recover, and nondeterminism breaks recovery
in a way that only shows up under crash conditions, which is the worst possible
time to find out. `Operand.kind = "now"` reads `ctx.clock` for exactly this
reason.

The vocabulary maps onto that boundary cleanly, which is a decent sign it is the
right vocabulary. An Action is an activity. A Check is a pure predicate inside
workflow code, which is also what makes a failing eval case fail for a logic
reason rather than because Gmail was slow.

## Checks return counts, not booleans

The deliverable is a per-shipment row of totals, so a check hands back an outcome
plus how many were tested, how many passed, how many failed, and the evidence rows.
`check/counting.py` is that. One rule can report at two granularities, which is how
"every line item carries four codes" produces both *two line items failed* and *one
invoice failed*, both of which are real columns.

## Tools are declared, never imported

Generated code says `ctx.tools.call("email.send", {...})` and never learns what is
behind it. `tools/dispatch.py` maps a capability key to a provider, the `tools`
table maps `email.send` to `GMAIL_SEND_EMAIL`, and lint verifies every capability
resolves before anything is generated. Swapping Composio for a hand-written adapter
changes one row.

Credentials never appear here, in the spec, or in generated code. The spec
references a capability, the bindings file references an entity, and the provider
holds the secret.
