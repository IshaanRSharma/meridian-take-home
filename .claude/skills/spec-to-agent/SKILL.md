---
name: spec-to-agent
description: Build a runnable Temporal agent from a frozen Meridian spec.lock.json into agents/<slug>/. Use when a spec needs turning into a first working agent, or when regenerating one after a spec revision.
---

# Spec to agent

A process owner drew a process, an AI reviewer questioned it, and the answers
were settled and frozen. **Everything in the spec was approved by a person.
Nothing you add is.** Implement what the spec determines; stop where it does not.

Read `meridian/src/meridian/runtime/` first. It is a **scaffold, not a
framework** — you import from it, it never calls you, and there is no base class
to inherit or plugin to register. That is deliberate: it means you can replace
any part of it that does not suit this process.

**Write real code.** The point of freezing a spec and handing it to you, rather
than building an engine that interprets specs at runtime, is that you can do
things a configuration language cannot. If a process needs a parser, a
normalisation table, a retry that backs off differently, a cache, a second pass
over ambiguous rows — write it. An interpreter can only ever do what its schema
anticipated; you are not limited that way, and that freedom is the whole reason
this pipeline compiles rather than interprets.

**What is actually fixed is small** — §8's five rules, and they are all about not
breaking something outside this agent. Everything else in `runtime/` is a
convenience:

| | |
|---|---|
| `outcome` · `trace` · `context` | **contracts** — the eval harness and the healing loop read these |
| `check/*` · `ingest` · `routing` · `policy` · `duration` | **helpers** — use, extend, or replace |

A Check written against `check/*` is shorter and gets the counting right. A
Check that ignores it entirely and returns a correct `CheckResult` is equally
valid.

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

### The slug is given to you, never invented

**`spec.lock.json` carries no name.** It has a checksum, a version and the cards
— nothing that says what this agent is called. So the directory name arrives as
an argument, and it must be **stable across regenerations**: `agent_builds`
rows, the repair loop's `file_map` and every failure bundle key on it. If nobody
told you the slug, **ask**. Do not derive one from the checksum or the board id.

### Four things the platform reads. Everything else is yours.

This is the whole contract. It is short on purpose — the platform depends on
four things and has no opinion about the rest.

```
agents/<slug>/
  spec.lock.json    verbatim, byte for byte. Conformance verifies the checksum.
  build.json        file_map: {primitive_key: path}   ← the repair loop reads this
                    plus model · prompt_version · temperature · spec_checksum
  <an entry point>  runnable, and named in build.json. `mvp eval sweep` runs it.
  one file per primitive, and its path in file_map
```

**`file_map` is the load-bearing one.** Localisation resolves
`primitive_key → file` from it, so a failure bundle can name the file to open.
Without it the loop falls back to guessing from a comment convention, which
fails silently the moment anything is renamed.

**One file per primitive** exists so a repair touches one file. That is a rule
about *blast radius*, not about taste — two primitives in one module means a fix
to one can break the other and the gate has no way to tell.

Beyond those four: module names, how you split helpers, whether entities are a
module or a package, how many files a check takes — **yours**. A layout that
reads well for this process beats one that matches an example.

### Names come from the spec, not from you

Every identifier a human will later grep for should be traceable to the spec:

```
file name        the primitive key      checks/coas_valid.py
function name    the primitive key      def coas_valid(...)
outcome literal  Outcome.name           "missing_coa", never "MISSING" or an enum
entity access    the entity key         store.instances("commercial_invoice")
field paths      FieldRef.path          read them; never retype a path
```

The reason is the failure bundle: it prints `coas_valid :: output_diff` and a
file path, and a human has to find that code in one look. A file called
`certificate_checks.py` holding a function called `validate()` breaks that
without breaking anything a test can see.

### A shape that works, if you want one

Not a requirement — the four things above are. This is what the pre-alert agent
looks like when written by hand:

```
agents/inbound_pre_alert/
  spec.lock.json   build.json   hints.json          (hints: repair-owned, preserve)
  src/
    workflow.py    the @workflow.defn class — signals, waits, dispatch, return
    worker.py      the entry point
    trigger.py     only when an Event has timing.mode = on_arrival
    entities.py    types from spec.entities
    checks/coas_valid.py            actions/report_coa_discrepancy.py
  tests/cases/*.json                hand-authored ground truth. NEVER overwrite.
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

### The numbers here are yours to choose

`confidence_floor` defaults to `0.6`. Nobody approved that — it is a starting
point, and it is **yours to tune**, along with anything else in this shape:

```
how sure before extracting          how many pages to send a model at once
text-vs-vision fallback threshold   how to split a bundle into documents
what a "blank" field is             which normalisation to apply when matching
```

None of these is a business decision, and the test for that is not whether two
people would disagree — it is **whether anything other than a person can settle
it.** These have an oracle: pick one wrong and an eval case fails, and the
repair loop tunes it. A business rule has no oracle, which is why §4 says stop.

Say in your summary which numbers you chose and why, so the first sweep's
failures are readable against them.

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

## 10. Review your own output before you finish

You wrote this in one pass and it has not run. Read it back looking for the
mistakes this pipeline actually produces — not style, not naming.

**Against the spec**

- every primitive in `spec.primitives` has a file, and every file maps to one
- every declared outcome is reachable from some branch you wrote
- every `fills` target is a field the output entity declares
- nothing you wrote hardcodes a value the spec supplies — an entity name, a
  field path, a threshold, a channel. If it is in the spec, read it from there.

**Against the five rules**

- no address, provider name or credential in any generated line
- `CheckResult` still reports counts, and `total == passed + failed`
- nothing in workflow code reads a clock, calls random, or does I/O
- every `meridian` import is inside the passthrough block

**The mistakes that fail quietly**

- a check that returns `pass` because it examined *nothing* — zero rows is not
  success, and `examined_anything()` exists to say so
- a `try/except` that swallows a failure into a business outcome. A bug reported
  to a supervisor as a discrepancy is worse than a crash.
- a field read off an entity the step was never given
- an entity read before anything produces it — trace the flow, not just the names
- normalisation nobody asked for. Trimming and lowercasing when the criterion
  says `each_has_matching` is inventing a rule; leave it exact and let the eval
  demand it.

**Then say what you are unsure about.** A line saying *"I assumed X; if the
first sweep fails on Y, that assumption is why"* is worth more than a confident
summary, because it is the first thing to check when something fails.

## 11. Your summary

End with:

- every stop condition you hit — which primitive, which decision, which readings
- anything you decided that the spec did not determine, and why it was
  implementation rather than business
- any eval column nothing fills
