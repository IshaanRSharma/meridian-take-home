---
name: spec-to-agent
description: Turn a frozen Meridian spec into a runnable Temporal agent under agents/<slug>/. Use when a spec.lock.json needs building into an initial agent, or when regenerating one after a spec revision.
---

# Spec to agent

You are building the first working version of an agent from an approved,
immutable specification. A process owner drew the process, an AI reviewer
questioned it, and the answers were settled and frozen. **Everything in the spec
was approved by a person. Nothing you add is.**

That is the whole job: implement what the spec determines, and stop when it
does not determine something.

## Read first

- `agents/<slug>/spec.lock.json` — the specification. Immutable, checksummed.
- `meridian/src/meridian/runtime/` — the scaffold. Start here, use what fits,
  replace what does not.
- `docs/design/skeleton.md` §1 — the five rules, in full.

## What the spec contains

Eight top-level keys:

```
version · checksum          identity. Never edit either.
entities                    what data looks like
primitives                  the steps
edges                       (from, to, on_outcomes) — the transition relation
edge_context                statements about a transition, not about a step
entity_context              statements about a kind of data
capabilities                every tool this agent needs, derived from the cards
```

**An entity** is a JSON Schema plus the process owner's own words:

```json
"commercial_invoice": {
  "name": "Commercial Invoice",
  "identified_by": "the page header reads \"Commercial Invoice\"",
  "cardinality": {"kind": "one"},
  "fields": {"invoice_no": {"type": "string"},
             "line_items": {"type": "array", "items": {"type": "object",
                "properties": {"batch_no": {"type": ["string", "null"]}}}}},
  "instructions": "…"
}
```

`identified_by` is **page-level** — it says how to recognise the document when
you are looking at one, and one file may contain several. Fields a Check reads
are nullable on purpose: extraction must return a line item that is missing a
batch number, or the Check that exists to detect that can never fire.

**A primitive** carries its config, its capabilities, and what was settled about
it during review:

```json
"coas_valid": {
  "primitive_type": "check",
  "config": {
    "criteria": [{"op": "each_has_matching",
                  "left":  {"entity": "commercial_invoice", "path": "line_items[].batch_no"},
                  "right": {"kind": "field", "field": {"entity": "certificate_of_analysis",
                                                       "path": "batch_no"}}}],
    "scope": "per_line_item", "quantifier": "all",
    "inputs": ["commercial_invoice", "certificate_of_analysis"],
    "outcomes": [{"name": "pass", "priority": 0},
                 {"name": "missing_coa", "priority": 1, "description": "…"},
                 {"name": "mismatched_coa", "priority": 2, "description": "…"}],
    "evidence": [...], "fills": [...], "instructions": "…"
  },
  "capabilities": [],
  "context": {"inherited": [...], "local": [...], "negative": [...]}
}
```

**`context` is settled business knowledge, in English.** `inherited` came from a
broader scope, `local` is about this step, and **`negative` states what must NOT
happen — do not implement those paths.** `provenance` is an audit trail; ignore
it.

## What to build

```
agents/<slug>/
  manifest.json     what you may rewrite. READ IT FIRST.
  spec.lock.json    never edit
  hints.json        extraction guidance. Repair-owned — preserve if present.
  src/
    workflow.py     the shell: signals, waits, dispatch on outcome, return
    trigger.py      only if an Event has timing.mode = on_arrival
    entities.py     types from spec.entities
    checks/         one file per check primitive, named from its key
    actions/        one file per action primitive
  tests/cases/      hand-authored ground truth. NEVER overwrite.
```

## How each card compiles

| card | becomes |
|---|---|
| **Entity** | an extraction schema — not a step |
| **Event** | workflow entry. `correlation_key` → workflow id. `timing.deadline` → `wait_condition(timeout=…)`. `match_condition` → a predicate **outside** the workflow |
| **Action**, capabilities non-empty | a Temporal activity. `idempotency_key` → a dedupe key |
| **Check**, capabilities empty | a pure function in workflow code returning `CheckResult` |

**`capabilities` decides the boundary.** Non-empty means it touches the world,
so it is an activity. A Check always has none — that is what lets it be workflow
code, and what makes a failing eval case fail for a logic reason.

A Check returns **counts, not a boolean**:
`CheckResult(outcome, total, passed, failed, failures)`. `total` must equal
`passed + failed`. `outcome` is one of the declared outcome names, chosen by
`priority` — lowest wins when several apply.

`fills` project those counts into an output entity at a grain. One criterion
counted at two grains is how a single rule yields both a line-item count and a
document count.

## The five rules

```
1  never write  bindings/ · fixtures/ · tests/cases/ · spec.lock.json
     provisioning and hand-authored ground truth. You cannot regenerate these.

2  no addresses, provider names or credentials in code
     ctx.tools.call("email.send", {...})  and  ctx.bindings.role("receiving_supervisor")
     You never learn that email.send is Gmail. That is deliberate — the same
     spec deploys to a customer on Outlook without becoming a new version.

3  keep CheckResult's shape — the eval row is arithmetic over it

4  keep the trace's shape — the failure bundle is assembled from it

5  no clock, no random, no I/O in workflow code
     Temporal replays workflow code from history. Comparing against `now` reads
     ctx.clock, which is a frozen value, not a call.
```

## Verify before you finish

```bash
make check                              # ruff · mypy --strict · tests
mvp verify --agent <slug>               # imports · conformance
mvp eval sweep --build <n> --split train
```

Build 1 is **not** expected to pass every case. It is expected to compile, run
every case, and produce a parseable result for each. A case that *errors* is a
problem; a case that *fails* is the starting point of the curve.

## When to stop

**If the spec does not determine an answer, stop. Do not choose.**

Report which primitive, which decision, and the two or more readings you are
choosing between. A guess here becomes a business rule nobody approved, and the
whole point of the frozen spec is that a person signed off on every rule in it.

Examples that mean stop:

- an outcome is declared but nothing in the criteria can produce it, and the
  `description` does not say how to tell it apart
- a Check's `inputs` name an entity nothing upstream produces
- two readings of a criterion would give different numbers, and the eval set
  cannot distinguish them
- `fills` target a field on an entity nobody declared

Examples that do **not** mean stop — decide these yourself:

- how to normalise whitespace or case when matching (implementation)
- how to page through a multi-page document (implementation)
- which library to parse with, how to structure a helper (implementation)
