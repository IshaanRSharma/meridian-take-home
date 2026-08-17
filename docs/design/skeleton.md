# The skeleton — choices, assumptions, behaviour

A scaffold a coding agent starts from, not a framework it is fenced into.
Companion to [`whiteboard.md`](./whiteboard.md) (the cards) and
[`compiler.md`](./compiler.md) (board to spec). This file covers spec to running
agent.

The brief asks for *"a small, reusable scaffold: an entry point, a place for step
execution, error handling, a place for business logic, a place for tool calls…
this shouldn't be specific to one customer's process."*

**Scaffold is the operative word.** The `spec-to-agent` skill starts here, uses
what fits and replaces what does not. Five things it must not break; everything
else is a useful default.

Status: `meridian/runtime/` is partially built — the contracts, the error
policy, the trace, and path resolution. `temporalio` is not yet a dependency.

---

## 1. The whole constraint list

Constrain the interfaces, not the implementation. Four of these five protect
something outside the agent; only the last is about the code itself.

```
1  never write  bindings/ · fixtures/ · tests/cases/ · spec.lock.json · the registry
     provisioning and hand-authored ground truth. Expensive, and not reproducible
     by regenerating anything.

2  no addresses, provider names or credentials in code
     ctx.tools.call("email.send", …) and ctx.bindings.role("receiving_supervisor").
     Rule 1 at the line level.

3  CheckResult keeps its shape
     the eval row is arithmetic over total / passed / failed.

4  the trace keeps its shape
     the failure bundle is assembled from it, and the bundle is the product.

5  no clock, no random, no I/O in workflow code
     Temporal replays workflow code from history. This one breaks recovery,
     not style.
```

Everything in `runtime/` beyond those is a default. Three modules are contracts
because something outside the agent reads them; the rest are convenience:

| | | why |
|---|---|---|
| `outcome.py` | **contract** | the eval harness reads `CheckResult` |
| `trace.py` | **contract** | the healing loop assembles the bundle from it |
| `context.py` | **contract** | the capability indirection *is* rule 2 |
| `errors.py` · `policy.py` | default | sensible; a generated agent may ignore them |
| `check/*` | default | genuinely useful, and replaceable |

### Three earlier rules, deleted

- **A location-based edit rule.** An earlier draft forbade the repair loop from
  touching `runtime/`. That protected a directory rather than a decision, and it
  got the stakes backwards: rewriting a business rule under `agents/` was allowed
  while fixing a PDF reader was not. The real line is `Claude.md` §12's — *could
  two competent people disagree, and would the customer care?* What survives is
  narrower and about a genuine hazard: **the loop may edit what runs, never what
  generates.** `compiler/`, `reviewer/` and `codegen/` produced the artifact under
  test, and a loop that edits its own generator can make the next build pass by
  redefining what passing means.
- **A blanket import allowlist.** Now guidance in the skill file. Rule 1's
  explicit deny-list is shorter and protects something real.
- **"Generate twice, byte-identical."** That test assumed Jinja templates. With
  a coding agent writing the files it means nothing.

### Blast radius is handled by the gate, not by a ban

A patch under `runtime/` affects every agent, and the gate — target passes, no
regression — computes regressions over one build's previously-passing cases. So
the gate scales with the radius instead of forbidding the edit: a runtime patch
re-sweeps every build that imports it. At one agent this costs nothing; at five
it is automatically stricter.

---

## 2. What it is general over

Not over *processes* — over the **vocabulary**. Since `domain/primitives.py` and
`domain/graph.py` are closed sets of enums, "can it run any legal spec?" is a
table rather than an opinion.

```
Effect         notify · record · noop      │ lookup · decide
Channel        email                       │ sms · phone · queue
Criterion op   present · each_has_matching │ compare · custom
Operator       —                           │ eq ne gt gte lt lte matches in
Operand kind   field                       │ value · now
Quantifier     all                         │ any · none · count
Scope / grain  per_line_item               │ per_case · per_document
Measure        —                           │ checked · passed · failed
Timing         on_arrival · await          │ scheduled · sla
Cardinality    one · one_per               │ many
on_failure     —                           │ fail · wait · skip
Edge relation  normal · exception          │ repeat
Recipient      role                        │ event

               ^ exercised by the           ^ NOT exercised —
                 pre-alert seed board         roughly 60% of the surface
```

**Build only against the left column and the result is silently
pharma-logistics-shaped.** One gap is on the board rather than in the code: both
seed checks declare `fills: []`, so the mechanism producing seven of the nine
eval columns is not exercised by the board as drawn.

---

## 3. Design choices

### Three processes were simulated before a file was chosen

| | pre-alert | credentialing | vendor compliance |
|---|---|---|---|
| starts | an email arrives | an application arrives | **scheduled**, monthly |
| entities from | **extraction** | **lookup** | lookup |
| checks | `present`, `each_has_matching` | `compare` against `now` | `compare`, `none` |
| actions | notify · record · noop | **lookup · decide** | notify · queue |
| waits | a deadline | **an SLA and a person** | a **repeat** until clean |

Three findings, none of them visible from pre-alert alone.

**An entity is populated three ways, not one.** Extracted from a captured
document, returned by a `lookup`, or accumulated by Checks as they run.
`ActionConfig.produces` already says so; the seed board never exercises it. A
scaffold assuming *entity = extracted* cannot run credentialing at all.

**There are two kinds of waiting.** For data — a deadline passes or inputs
complete. For a person — a signal arrives, or an SLA expires and an `on_timeout`
branch is taken. Pre-alert has no runtime human.

**The trigger is not a universal file.** `on_arrival` compiles to
`signal_with_start`; `scheduled` to a Schedule; an application arriving over an
API has no trigger at all.

### Business logic: the logic is not general, the accounting is

Every Check does seven things, and six are identical everywhere:

```
resolve paths into rows at a grain      general
evaluate a predicate per row            ← the ONLY part that differs
tally into checked / passed / failed    general
roll up to a coarser grain              general
order outcomes by priority              general
collect evidence rows                   general
fill counts into the output entity      general
```

Offered as helpers rather than imposed. A Check written against them is shorter
and gets the counting right; a Check that ignores them is legal.

### `step.py` was planned and is not being built

`RunTrace.step()` already is it, as a context manager, and normalising a
`CheckResult` belongs to the check engine. Two ways to do one thing is a seam
for them to disagree. Recorded because deleting a planned module is a decision.

### Every Temporal import lives in `runtime/temporal/`

An earlier draft called it `workflow/`, which was wrong twice: it holds no
workflows — the workflow is generated into `agents/<slug>/workflow.py` — and the
name hid what matters, which is that this is the only subpackage importing
`temporalio`. Confining it keeps the type layer and the check engine testable
with nothing installed.

---

## 4. What Temporal imposes

### The sandbox reloads our code on every workflow run

Standard library and `temporalio` are passed through; **everything else — the
runtime, pydantic, the domain types — is completely reloaded per run.** The
escape is explicit:

```python
with workflow.unsafe.imports_passed_through():
    from meridian.runtime import CheckResult, RunTrace
```

Only safe for modules free of side effects, which is a real requirement on the
scaffold: no module-level mutable state, no I/O at import, no clock or
environment read at import. Two tests assert it. Without it, every workflow run
reloads the entire check engine.

### Workflow files must not import activity implementations

Activities do I/O; workflow files are sandboxed. This is the determinism rule
appearing a second time, as an import rule.

### One dataclass in, not several arguments

Temporal's own guidance: a long-running workflow outlives the signature it was
first given.

---

## 5. Observability — who owns what

**No database for Temporal's sake. Yes for evaluation.**

| | Temporal | us |
|---|---|---|
| every activity, its input, output, retries, timers | ✅ free | — |
| a UI to browse one run | ✅ | — |
| **why a check failed** — `['UAC25022 ', 'uac25019']` | ❌ | ✅ `CheckResult.failures` |
| pass rate across cases · the curve across builds | ❌ | ✅ `runs`, `agent_builds` |
| the failure bundle | ❌ | ✅ `failures`, `run_steps` |

Temporal has no notion of *expected* output, so everything comparative is ours
by definition.

**Do not parse event history for the bundle.** The harness runs cases through
the time-skipping environment so history exists, but it dies with the test
server, it is protobuf-shaped, and decisively it records *"the activity returned
this"* and never *"these two batch numbers differ only by whitespace and case"*.
Temporal's history is the link-out for production runs via
`runs.temporal_workflow_id`.

**Tracing is an interceptor.** `worker.Interceptor` is the SDK's cross-cutting
mechanism, so generated code emits no trace calls for anything crossing the
activity boundary. Checks never leave the workflow, so `RunTrace.step()` records
those.

**Logging** is `workflow.logger`, never bare `logging` — it is replay-aware.

**Deliberately not built:** Prometheus via `TelemetryConfig`, OpenTelemetry via
`contrib.opentelemetry`, custom Search Attributes. All one-liners the SDK
offers; SCOPE cuts search attributes explicitly and the others demonstrate
nothing this is graded on.

---

## 6. Artifact lifecycles, and why regeneration is safe

Four artifacts, four owners. This is what makes "delete the directory and
regenerate" a safe operation.

```
spec.lock.json            immutable · checksummed        regeneration READS
bindings/<customer>.yaml  per-customer · hand-edited     regeneration NEVER TOUCHES
tools registry            global · seeded                regeneration NEVER TOUCHES
agents/<slug>/src/**      generated                      regeneration REWRITES
```

**The OAuth grant is in none of them** — it lives at Composio, keyed by entity
id, and bindings record only that id. So a regenerated agent directory cannot
cost you a mailbox connection, because the connection was never in it.

A manifest tells the skill what it may rewrite:

```json
{
  "slug": "inbound_pre_alert",
  "spec_checksum": "1e77…",
  "regenerate": ["src/**"],
  "preserve":   ["hints.json", "tests/cases/**"],
  "external":   ["bindings/aurologistics.yaml", "the tools registry"],
  "capabilities": ["email.fetch", "email.send", "system.write"]
}
```

`preserve` is the load-bearing list: eval cases and extraction hints are
expensive human work, and a regeneration that wiped them would destroy more than
it created.

**`mvp connect check` after every regeneration** — not because regeneration can
break a connection, but because proving it did not is the reassurance you want
after a coding agent has rewritten a directory.

**The trace is a tooling contract.** It records every call by capability key, so
build N's trace is something build N+1 must still satisfy:

```
build 1   email.fetch ×1 · doc.extract ×6 · email.send ×1
build 2   email.fetch ×1 · doc.extract ×6                  ← the notify path vanished
```

Static conformance asks whether every declared capability has a call site; the
trace answers whether it was actually called. Cheap — the data is already in
`trace.tool_calls` and the comparison is a set diff.

---

## 7. Assumptions

- **The vocabulary is expressive enough for the processes we care about.**
  `Claude.md` §29 documents where it is not — computation, scheduling and
  optimisation, blocking human decisions with an SLA. General *over this
  vocabulary, whose boundaries are written down* is defensible; "works for any
  business process" is not.
- **Counting semantics are uniform.** A coarser grain passes exactly when every
  finer unit beneath it passes. Asserted, not proven.
- **Extraction is nondeterministic and that is contained.** It is an activity, so
  Temporal records its result once and replays from history; the eval suite mocks
  it from fixtures, because a suite whose inputs vary cannot be an oracle.
- **Nothing is ever sent.** The chain from `effect: notify` through capability,
  binding and activity runs in full and terminates at a recorder — which is also
  more assertable than a delivered message.

---

## 8. How it gets verified

1. **Two toy agents, hand-written, before any generation.** Pre-alert and
   credentialing, exercising opposite halves of the table in §2. `Claude.md`
   §15's own test — *"if that is awkward, the contract is wrong"* — run twice.
2. **The coverage table is a checklist.** Every cell covered or skipped **with a
   reason**, so a gap is a decision rather than a discovery during the sweep.
3. **The leak test, on identifiers rather than on files.** No module, class,
   function, parameter or attribute name in `runtime/` may contain a customer
   noun. A grep over whole files is the wrong check — five docstrings use the
   running example to explain *why* a decision was made, and that is
   documentation rather than a leak. Parse the AST and inspect identifiers.
4. **The dependency rule.** `runtime/` imports `domain` only — never `compiler`,
   `reviewer`, `codegen` or `healing`. A worker process should not carry the
   platform.
5. **Passthrough safety.** No module-level mutable state, asserted across every
   submodule.

---

## 9. Open questions

- **Localisation names the detecting primitive, not the causing one.** A scanned
  PDF yielding no text makes `coas_valid` fail, and the bundle points at
  `checks/coas_valid.py` — a file that behaved correctly. `localize` has to walk
  upstream through `inputs`: if a Check fails and one of its inputs produced zero
  instances, blame the producer. Found by tracing a real 37-page attachment,
  before writing any of it.
- **The corpus is scanned, and a recorded decision says otherwise.**
  `Claude.md` §23 says native PDFs go straight to the model and OCR is the
  fallback. True of the one-page sample; false of the 37-page bundle, where every
  page is an image with no text layer. **Vision is the main path**, and that is a
  platform decision rather than something the repair loop should discover at eval
  time.
- **`identified_by` is page-level and was read as file-level.** *"The page header
  reads 'Commercial Invoice'"* — page. So one file yields several entity
  instances of different kinds, and `extract` must return a list per file with
  classification per page-range.
- **RESOLVED — the invoice report was drawn as an ending and is not one.** The
  toy agent showed `coas_valid` never running: `invoice_complete` came out
  `missing_information`, routed to the report, and the report was terminal. Both
  the SOP and the corpus disagree — page 2 logs the error and then continues
  straight into certificate verification, and `CAAU4056270` failed its invoice
  check while still having all five certificates counted. The fix is that the
  report is a **step**, not an ending: `report_invoice_discrepancy → coas_valid`.
  A first attempt loosened the edge out of `invoice_complete` instead, and the
  compiler refused it — `missing_information` then matched two edges at once,
  which is an ambiguous route. Worth recording: the wrong fix was caught by a
  lint rule rather than by a test.
- **Superseded — the serial gate as originally described.** Hand-writing an agent against a real frozen spec and running
  `CAAU4056270` showed `coas_valid` never executing: `invoice_complete` came out
  `missing_information`, and edge `e2` routes only `pass`. But the eval row says
  `coa_total: 5, coa_success: 5` — the real shipment failed its invoice check and
  still had every certificate counted. Remove the gate and **all seven producible
  columns match exactly.** The compiler's `sequence` sweep already asks this as a
  question; the corpus answers it.
- **RESOLVED — a Check counts rows, not assertions.** Four criteria over five
  line items is five things checked, not twenty. That is what `quantifier`
  always meant: `all` requires every criterion to hold **for a row to pass**.
  Failures are keyed by `(document, indices)` rather than by locator, so a line
  item missing two codes is one failed line item and not two. Both checks now
  report at the same grain, and the ambiguity disappears rather than needing a
  ruling.
- **Two things the SOP requires have nowhere to live.** *"Log an error mentioning
  … Missing Information Type"* and *"reported via email with … description of
  discrepancy"* are both derived from a check result, and `payload_fields` is
  `tuple[FieldRef, ...]` pointing into entities. This is `Criterion.produces`,
  already open in `Claude.md` §8, and the SOP demands it twice.
- **Which cells of §2 are honestly skipped.** `count` quantifier and `scheduled`
  timing are the likely candidates; they should be named, not discovered missing.
- **`temporalio` is not yet a dependency.** The pure core needs nothing new.
