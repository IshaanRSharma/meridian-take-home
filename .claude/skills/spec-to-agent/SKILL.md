---
name: spec-to-agent
description: Build a runnable Temporal agent from a frozen Meridian spec.lock.json into agents/<slug>/. Use when a spec needs turning into a first working agent, or when regenerating one after a spec revision.
---

# Spec to agent

A process owner drew a process, an AI reviewer questioned it, and the answers
were settled and frozen. **Everything in the spec was approved by a person.
Nothing you add is.** Implement what the spec determines; stop where it does not.

Read `meridian/src/meridian/runtime/` first — a scaffold, not a fence. Use what
fits, replace what does not. The five rules are at the end of this file.

---

## 1. The spec

```
version · checksum       identity. Never edit either.
entities                 JSON Schema + the owner's own words
primitives               the steps: event · action · check
edges                    (key, from_key, to_key, on_outcomes) — the transitions
edge_context             statements about a transition
entity_context           statements about a kind of data
capabilities             every tool this agent needs
```

`context` on a primitive is settled business knowledge in English:
`inherited` came from a broader scope, `local` is about this step, and
**`negative` states what must NOT happen — do not implement those paths.**
Ignore `provenance`; it is an audit trail.

## 2. Find the output entity first

Nothing labels it, and everything else depends on it.

> **The output entity is the one no Event captures and no Action produces —
> the one that only `fills` write into. It is the workflow's return value.**

On the pre-alert spec that is `shipment_summary`: seven integer fields, no
`identified_by`, and every field is the target of some check's `fill`. It is
never extracted. The workflow builds it as checks run and returns it.

If two entities fit that description, or none does, **stop** — you cannot know
what the process produces.

## 3. Compile each card

### Entity → an extraction schema, not a step

`fields` is already JSON Schema. Read it out of `spec.lock.json` at run time
rather than copying it into code, so the two cannot drift.

**`identified_by` is page-level.** *"The page header reads 'Commercial
Invoice'"* describes a page, not a file. One attachment may hold an invoice and
a dozen certificates, so extraction returns **a list of instances per file**,
classified per page-range. A file that yields one instance is a list of length
one.

Fields a Check reads are `["string", "null"]` **on purpose**: extraction must
return a line item that is missing its batch number, or the Check that exists to
detect that can never fire. Never drop a row for being incomplete.

An entity captured by an Event but read by no Check — `prealert_email` here —
still gets extracted. It carries the correlation key and the audit trail.

### Event → the way in

| field | compiles to |
|---|---|
| `correlation_key` | the workflow id: `<slug>-{value}` |
| `match_condition` | a predicate in `trigger.py`, **outside** the workflow |
| `timing.mode: on_arrival` | `signal_with_start` |
| `timing.mode: scheduled` | a Temporal Schedule; `timing.schedule` is the spec |
| `timing.deadline` | `await_inputs(ready, deadline)` |
| `timing.max_lifetime` | `workflow_execution_timeout` |
| `captures` | the entities this event brings in |

**`deadline` absent means wait indefinitely** — bounded only by
`max_lifetime`. Do not substitute a default. A timeout of zero fires
immediately, and inventing one turns "no time limit" into "expire at once".

### Action → an activity, or not

**`capabilities` decides.** Non-empty means it touches the world, so it is a
Temporal activity. Empty means it is not.

```
effect: notify   → capability from channel. recipients are ROLES:
                   ctx.bindings.role("receiving_supervisor"). Never an address.
effect: record   → system.write. `system` is free text naming the customer's system.
effect: lookup   → system.read, and it PRODUCES an entity (`produces`).
                   Honour `timeout` and `on_failure`.
effect: decide   → block on a human signal, with `timing` as the SLA and
                   `on_timeout` as the branch when nobody answers.
effect: noop     → no activity at all. A named end state: record the outcome
                   and return. `documentation_validated` is this.
```

`payload_fields` with an iterated path — `line_items[].drug_description` —
means **every** value, as a list. The message names all of them.

**`idempotency_key` absent on a `notify` or `record` is a real hazard, not a
default.** The board is cyclic: corrected paperwork arrives, the check re-runs,
and the same message goes out twice. Build a key from the payload so it changes
when the content changes — *once per distinct situation*, not once ever. Say in
your summary that you did, since nobody specified it.

### Check → a pure function, always

`capabilities` is always empty on a Check. It stays in workflow code, which is
what makes a failing eval case fail for a logic reason.

Return `CheckResult(outcome, total, passed, failed, failures)`. `total` must
equal `passed + failed`. **Counts, never a boolean** — the eval row is
arithmetic over them.

```
scope         what one row is: per_case · per_document · per_line_item
quantifier    all · any · none · count
inputs        entity keys this reads
criteria      present · compare · each_has_matching · custom
outcomes      named, with `priority` — LOWEST priority that applies wins
evidence      what to report as proof
fills         where the counts land
```

**`fills[].per` defaults to the check's own `scope`.** A fill whose `per` is
*coarser* than the scope is a roll-up: a document passes exactly when every line
item under it passes. That is how one rule produces both `goods_failed` (line
items) and `invoices_failed` (the documents containing them).

**`on_missing_input` absent** and an input has not arrived: **stop and ask.**
Waiting, failing and skipping give different eval rows, the spec does not say
which, and two competent people would disagree.

### Edges → routing

`(from_key, to_key, on_outcomes)`. **Empty `on_outcomes` fires whatever the
outcome** — that is how an Event's edge works. An outcome with no edge is a
stop, not a crash. `relation: repeat` is an ordinary backward step; the board is
a state machine, not a DAG.

---

## 4. Stop conditions, made mechanical

Not judgement calls. Check each one:

**More declared outcomes than the criteria can distinguish.** Count the
distinguishable results of the criteria; compare to `len(outcomes)`. On
`coas_valid`, one `each_has_matching` yields matched/unmatched — two — against
three declared outcomes. The `description` fields say *"a batch has no COA"* and
*"a COA exists but its batch number disagrees"*, and **nothing in the criteria
can tell those apart.** Stop. Do not invent a similarity threshold.

**An input no upstream step produces.** Every key in `inputs` must be captured
by an Event, produced by an Action, or filled by a Check.

**A `fills` target that is not a declared field** on the entity it names.

**An eval column nothing fills.** Report it; do not invent a source.

**Two readings that give different numbers**, where the eval set cannot
distinguish them.

### Decide these yourself — they are implementation

Whitespace and case when matching. Paging a multi-page document. Which library
parses what. How to structure a helper. Retry intervals. Log wording.

---

## 5. What to build

```
agents/<slug>/
  manifest.json    what you may rewrite. READ IT FIRST.
  spec.lock.json   never edit
  hints.json       extraction guidance. Repair-owned — preserve if present.
  src/
    workflow.py    signals · waits · dispatch on outcome · return the output entity
    trigger.py     only when an Event has timing.mode = on_arrival
    entities.py    types from spec.entities
    checks/        one file per check, named from its key
    actions/       one file per action
  tests/cases/     hand-authored ground truth. NEVER overwrite.
```

## 6. Ingestion: attachments to entity instances

Seven attachments arrive and one of them is the invoice. `runtime/ingest.py`
does this generically — it reads `identified_by` and `fields` off the spec and
knows nothing else.

```
for each attachment:
    read     bytes -> text.  TRY THE TEXT LAYER FIRST, fall back to vision
                             when a page yields nothing. The corpus is mixed:
                             some PDFs are native, one 37-page bundle is pure
                             image. Text is fast and cheap and handles most;
                             vision handles the rest.
    classify text + the closed set of candidates -> Verdict(entity, confidence)
    extract  text + one candidate's schema -> a LIST of instances
    store    keyed by entity
```

**Classification is page-level, not file-level.** `identified_by` says *"the page
header reads…"*. One attachment in the corpus is a certificate of compliance
**and** of analysis; another is a 37-page bundle of an invoice and its
certificates. So a source yields a *list*, and a single-document file is a list
of length one — which means "is this one PDF or twelve" never has to be answered.

**Filenames are never evidence.** `CGMU5630052.pdf` is named after a container.
`U07-5284.pdf` is an invoice. Read the page.

**Anything matching no rule is skipped, and the skip is recorded.** The SOP says
*locate* the invoice among the attachments, so a signature gif and a house bill
of lading are simply not part of this process. But *"found no invoices"* and
*"skipped the invoice"* are the same empty result with entirely different fixes,
so `store.decline(source, reason)` every time.

**Low confidence declines rather than guessing.** A certificate of compliance
reads almost exactly like a certificate of analysis. Extracting against the wrong
schema yields fields that look right and are not, which is worse than declining.

## 7. Three things a workflow file must do

All three found by running a real workflow. None is guessable, and the third
costs an afternoon if you meet it cold.

**Put EVERY `meridian` import inside the passthrough block.**

```python
with workflow.unsafe.imports_passed_through():
    import pydantic_core                       # pydantic loads it lazily
    from meridian.runtime import CheckResult, Failure, RunTrace
    from meridian.runtime.check import present, resolve, tally
    from meridian.runtime.temporal.activities import Capabilities   # THIS ONE TOO
```

One import left outside loads `meridian.runtime` sandboxed first, and then
`Failure` from `check.criteria` and `Failure` from `runtime` are **two different
classes**. Pydantic rejects one as not being an instance of the other.

**An exception in workflow code is a HANG, not an error.** Temporal treats it as
a workflow task failure and retries forever, so there is no traceback and no
exit — it just sits there. If a workflow never returns, assume it is raising and
wrap the call in `asyncio.wait_for` to surface it.

**Return a dataclass, not `dict[str, object]`.** Temporal's payload converter
refuses `object` and fails at the boundary with a type error naming a key rather
than a cause.

## 8. The five rules

```
1  never write  bindings/ · fixtures/ · tests/cases/ · spec.lock.json
2  reach the world only via ctx.tools.call(capability) and ctx.bindings.role()
3  keep CheckResult's shape — the eval row is arithmetic over it
4  keep the trace's shape — the failure bundle is assembled from it
5  no clock, no random, no I/O in workflow code.
   Comparing against `now` reads ctx.clock, which is a frozen value.
```

## 9. Verify

```bash
make check                                   # ruff · mypy --strict · tests
mvp verify --agent <slug>                    # imports · conformance
mvp eval sweep --build <n> --split train
```

Build 1 is **not** expected to pass every case. It must compile, run every case,
and produce a parseable result for each. A case that **errors** is a problem; a
case that **fails** is the first point on the curve.

## 10. Your summary

End with:

- every stop condition you hit — which primitive, which decision, which readings
- anything you decided that the spec did not determine, and why it was
  implementation rather than business
- any eval column nothing fills
