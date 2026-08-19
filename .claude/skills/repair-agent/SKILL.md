---
name: repair-agent
description: Fix one failing eval case in a generated agent under agents/<slug>/. Use when a sweep has failed and a failure bundle needs turning into a minimal patch, or when deciding whether a failure is a code defect or a gap in the spec.
---

# Repair agent

A generated agent failed some eval cases. You are fixing **one signature**, not
the build.

Before the freeze, ground truth lived in a person's head. After it, ground truth
lives in the eval suite — so unlike the reviewer, you have an oracle, and your
job is to make the oracle pass **without weakening what it measures.**

## 1. Read the bundle

It is self-contained by design. Everything you need is in the paste:

```
FAILING SIGNATURE  coas_valid :: output_diff :: coa_count_mismatch   (4 cases)
FILE               agents/inbound_pre_alert/checks/coas_valid.py
SPEC               spec.lock.json § primitives.coas_valid

CASE   CAAU4056270
  expected  {outcome: pass, total: 5, passed: 5, failed: 0}
  actual    {outcome: missing_coa, total: 5, passed: 3, failed: 2}

TRACE
  step 3  extract                 ok    {counts: {commercial_invoice: 1, …}}
  step 4  coas_valid              FAIL  2 unmatched: ['UAC25022 ', 'uac25019']

DECLINED
  image001.gif        matches no recognition rule
  SH00029867 HBL.pdf  matches no recognition rule

SPEC CONTEXT FOR coas_valid
  [terminology] Batch association is by batch number, not page order.
  [negative]    Ignore a certificate whose batch is on no invoice line.

REPAIR HISTORY FOR THIS SIGNATURE
  none
```

**Read the assumptions first.** The generator recorded every decision the spec
did not determine, each with a `falsified_if` — a prediction about what failure
would disprove it. The bundle carries the ones that could apply here under
`ASSUMPTIONS THAT COULD EXPLAIN THIS`: those anchored on the failing step, and
those anchored nowhere at all. Scan them against the failure in front of you
before reading a line of code:

```
falsified_if: "a real pre-alert is missed"        ← and the sweep found 0 runs
falsified_if: "a document is declined that
               should have been read"             ← and DECLINED names the invoice
```

A match there is the answer, and it arrives in seconds rather than after reading
a diff. An assumption whose `prompted_by` is `null` had no spec anchor at all,
which makes it both the likeliest cause and a candidate spec gap.

The bundle shows only what could apply to this signature. `assumptions.json` in
the agent directory has the rest, and is worth opening when nothing in the
bundle fits — a failure explained by no recorded assumption is either a plain
defect or a decision nobody wrote down, and the second is worth fixing too.

**Then read `DECLINED`.** Half of "found nothing" failures are a
document that was skipped, not a document that was absent, and those have
completely different fixes.

**Check `REPAIR HISTORY`.** Do not retry an approach already rejected. Without
this, the same failed idea gets tried across sessions.

### The file named is where the number went wrong, not where the mistake was

`file_map` resolves a failing column to the step that filled it. That is the
right default and it is confidently wrong in one whole class of failure: a step
can only be as correct as what it was handed, so a mistake made upstream
surfaces as a wrong number downstream, and the bundle points at the one file
where the bug definitely is not.

Two readings of `TRACE` separate the two, and both are in front of you:

**Did the step examine anything?** `total: 0` is not a failure of the criterion,
it is the criterion never running. A check that reports `pass` over zero rows
found nothing to disagree with. Look at what the step *before* it produced: if
ingestion reported no instances of an entity the check reads, the fix is in
reading or recognition, and `DECLINED` usually names it.

**Is the number a multiple rather than a difference?** `2` where `1` is
expected, `6` where `3` is, `10` where `5` is — a count that is a small integer
multiple of the truth is almost never arithmetic. It is the same thing counted
once per delivery, or per file, or per page: a grouping mistake, upstream of
every check that reports it. An off-by-one or a wrong threshold looks nothing
like this, and telling them apart costs one glance.

Both are questions about **whether the step was given the right inputs**, asked
before touching how it uses them. Ask them first; they cost seconds and they are
the difference between patching a symptom and patching a cause.

**Then check the card for a field the code never reads.** Every field on a
primitive drives a behaviour — that is why it is a field and not prose — so one
the generator skipped is a behaviour the process asked for and did not get. Read
the card in `spec.lock.json` beside the file and account for each field. A fix
that restores a declared field is the best kind available: it is authorised
already, so it is neither a guess nor a spec gap.

### Some things a failure cannot tell you, and saying so is the deliverable

A suite scores the answer, not the reasoning. Where the answer is wrong for a
reason nothing in the trace records, no amount of reading the bundle reaches it
— which is why the corpus, not the sweep, is what catches a wrong grouping or a
key read from the wrong place.

When you suspect that, **say what evidence would settle it and stop.** A note
saying *"every case under-counts by exactly the number of deliveries; the eval
cannot see grouping, so a case that splits one unit across two inputs would
catch it"* is worth more than a patch that makes the number match. Naming the
case the suite is missing is a contribution to the suite.

## 2. Classify before you patch

Three outcomes, and only one of them is yours.

```
implementation_defect   nobody would disagree about the answer      → patch it
spec_gap                two competent people would disagree, and    → STOP
                        the customer would care which you picked
skeleton_defect         the agent has no seam to express the fix    → STOP,
                        through                                        say what
                                                                       is missing
```

The first test is **who owns the decision**, never where the file lives. If a
person has to choose, it is a spec gap however easy the code would be.

### The default is to decide. `spec_gap` is rare and must stay rare.

Most of what looks like a business question is not one. A shipment arriving by
air instead of sea, a document with no text layer, an identifier in a different
field, a set of attachments in a shape nothing has seen — all of these are
*unfamiliar*, and unfamiliar is not the same as undecidable. Treating them as
gaps sends questions to somebody who would answer *"work it out"*, and a loop
that stops on every novelty has given up the whole advantage of code being the
patch surface.

**The test that separates them is whether the suite can tell you that you were
wrong:**

```
the eval would fail if I chose badly       →  decide, record it, let the suite judge
the eval would pass either way             →  STOP. nothing here is an oracle
```

That second line is what a spec gap actually is. Not "hard", not "unfamiliar" —
**unfalsifiable**. If two answers both produce a passing suite and only one is
right, no amount of running tells you which, and choosing quietly is how a
business decision nobody approved ends up in code.

Two shapes reach that bar in practice, and they are worth knowing by name:

- **the unit of work** — what counts as one case. Aggregate at the wrong grain
  and every number is internally consistent and wrong together.
- **identity** — whether two differently-written things are the same thing.
  Both readings are self-consistent, and only the owner knows.

Everything else: decide, write the assumption with a `falsified_if`, move on.

The second test is **whether you can express the fix at all**, and it is asked
second because it only matters once you know the decision is yours:

> Can I make this work by changing something under `agents/<slug>/`?

Almost always yes, and for two different reasons that are easy to confuse:

```
INJECTED       a reader, a classifier, an extractor, a provider — these arrive
               as arguments, so pass a different one

CALLED         a criterion kernel, a tally, a path resolver — these are
               imported and called from a site inside YOUR file, so wrap the
               call: canonicalise the inputs before it, restore what you need
               after it
```

The second is the one that gets missed. Looking for a constructor argument that
does not exist and concluding there is no seam is how a fixable defect gets
escalated — the seam is the **call site**, and the call site is in a file you own.

**Wrapping or replacing either is an ordinary patch, not a skeleton defect.**

In practice `skeleton_defect` should be **rare to the point of suspicion**.
Every moving part of the scaffold is either injected or called from generated
code. If you are about to declare one, say precisely which line you cannot reach
and why — that sentence is the finding, and if you cannot write it, the seam
exists.

It is a skeleton defect only when there is **no seam** — when the behaviour is
hard-wired somewhere the agent cannot reach, and the only way to change it is to
edit code shared by every agent. Say which seam is missing, because that is the
fix: usually the answer is to make the thing injectable rather than to change
what it does.

**Do not classify by where the code currently lives.** Two agents hitting the
same wall is evidence, not proof — one signature spanning several primitives
means look harder for a seam, not that one is absent.

| failure | class |
|---|---|
| batch ids differ by whitespace or case | defect — normalise |
| an attachment nested two levels in a forward | defect — recurse |
| extraction read the wrong page region | defect — fix the hint |
| a certificate arrives whose batch is on no invoice: ignore or flag? | **spec gap** |
| an invoice line has no ANDA: block or warn? | **spec gap** |
| every case fails identically at the same step | **infrastructure** — read DECLINED and the entry point first. Skeleton defect only if no seam exists |

A bucket covering **100% of cases** is evidence in itself: a per-check bug fails
some cases, an infrastructure bug fails all of them the same way.

### When the sweep is green and the agent is still wrong

The suite is an oracle, not an authority. Some failures produce a **passing**
sweep — most often a correlation or grouping bug, where every case is scored
individually and nothing checks that they were grouped into the right units in
the first place.

You will usually meet this while investigating something else. If you conclude
the spec is wrong and the sweep does not agree, **say so anyway**, and say what
case would have caught it. A gap the suite cannot measure is still a gap, and an
eval case that would expose it is the most useful thing you can leave behind.

## 3. When there is nothing to compare

A sweep can fail without producing a single comparison: zero runs, every case
erroring identically, **or every case returning all-zero counts.** That last one
is the trap — a run that returns zeros is an empty sweep wearing a comparison.
It reports runs, reports no errors, and quietly scores every column whose
expected value happens to be zero as a pass.

The tell is zeros across the board with a **non-empty DECLINED**, or a trace that
stops before the first check. There is no real expected-versus-actual, so the
bundle you were handed is nearly empty — and that emptiness **is** the diagnosis.

It means the failure is **upstream of everything the eval measures**. Nothing
reached the checks, so nothing could disagree with the expected output.

Work forwards from the entry point rather than backwards from a failure:

```
did the trigger fire at all?         zero runs -> the entry condition matched
                                     nothing. Read what it was given, not what
                                     it decided.
did any input arrive?                counts of zero across the board, and
                                     DECLINED naming everything that turned up
did one case error, or all of them?   ALL identical = infrastructure.
                                     SOME = a real per-case bug.
```

A sweep of zero is also the one situation where **the fix is often not in a file
the bundle names**, because no primitive got far enough to be named. Expect to
be working in the entry point, the reader, or whatever assembles inputs.

## 4. Localisation lies in one specific way

`primitive_key` names where the failure was **detected**, not where it was
**caused**. A check reporting `0 passed` may be perfect while its input never
arrived.

So before opening the named file, read the trace upstream:

```
extract  commercial_invoice -> 0 instances     ← the actual bug is here
coas_valid                   FAIL 5 unmatched  ← the bundle names THIS file
```

If an input produced zero instances, the check is innocent. Fix the producer.

`FILE` is resolved from `build.json`'s `file_map`, written by whoever generated
the agent. If the bundle names a file that does not exist, the map is stale —
say so rather than guessing at a path, because a patch applied to the wrong file
passes the gate for the wrong reason.

## 5. Patch

**You are writing code, not editing configuration.** If the right fix is a
parser, a normalisation table, a lookup, a second pass over ambiguous rows —
write it. A thing that only tweaks a parameter is often the shallow version of a
fix that wanted real logic.

**Tuning numbers is a patch, not a spec question.** Confidence floors, page
batch sizes, what counts as blank, which normalisation to apply — all of these
have an oracle, and the oracle is the eval suite. Pick one wrong and a case
fails, which is exactly the signal you are here to act on. A business rule has
no oracle, which is why §2 says stop for those and not for these.

- **One file.** If the fix needs two, it is probably a skeleton defect — say so.
- **No new dependencies.**
- **Minimal.** Do not refactor around the fix; a large diff hides the change
  that mattered and makes the next bundle harder to read.
- **Never weaken a check to make a case pass.** Deleting a criterion, loosening a
  comparison or widening an outcome makes the suite pass by measuring less.
  Conformance catches the obvious version; the subtle version is on you.

### Where the fixes usually are

**Normalisation.** `['UAC25022 ', 'uac25019']` — trailing whitespace and case.
The criterion says `each_has_matching`, not *case-insensitively after trimming*,
so the generator was right not to assume it. This is the first repair the loop is
expected to make.

**Recursion into forwards.** Every message in the corpus is `Fwd: FW: …` and
attachments sit two levels down.

**Classification.** If `DECLINED` names something that should have been read, the
recognition rule or the reader is wrong — not the check.

## 6. Working inside a Temporal workflow

**An exception in workflow code is a HANG, not an error.** Temporal treats it as
a workflow task failure and retries forever: no traceback, no exit, just silence.
If a case never returns, assume it is raising and wrap the call in
`asyncio.wait_for` to surface it.

**Every `meridian` import must sit inside `workflow.unsafe.imports_passed_through()`.**
One left outside loads `meridian.runtime` sandboxed first, and then `Failure`
from `check.criteria` and `Failure` from `runtime` are two different classes.
Pydantic rejects one as not an instance of the other, and by the rule above that
presents as a hang.

**No module-level mutable state.** The sandbox reloads modules per run, so
anything set from outside the workflow is invisible inside it. Data travels on
the signal or the input.

## 7. Verify, in this order

### A patch is held to the same gate as the code it edits

Python 3.12. `make check` runs ruff with 24 rule families and `mypy --strict`,
and it is not advisory — a patch that does not pass has not been made. The ones
a repair trips, in order of how often:

```
mypy --strict   the helper you added needs annotations too, parameters and
                return, and no bare `dict` or `list`
D  pydocstyle   a new function needs a docstring, Google convention, saying WHY
T20             the `print()` you added to see what was happening. Workflow code
                uses `workflow.logger`, which is replay-aware
DTZ             no naive datetimes — the determinism rule, not style. `ctx.clock`
ERA             the old line you commented out instead of deleting. Git remembers
ARG             an argument you stopped using
```

And the one no linter checks: **a comment explains why the fix works, not what
the line does.** `# strip whitespace` above a `.strip()` is noise. `# suppliers
pad batch numbers to a fixed width; the invoice does not` is the reason, and it
is what stops somebody reverting your patch next month.

Keep the diff small enough to read in one pass. A large diff hides the change
that mattered and makes the next bundle harder to work from.

```bash
make check                                             # ruff · mypy --strict · tests
meridian eval case <board> <KEY> --build <n>           # the target case, on its own
meridian eval sweep <board> --case <each> --build <n>  # the whole working set
meridian verify --agent <slug>                         # imports · conformance
```

**Re-running the workflow is just running the case again.** There is no server
to start, no worker to leave polling, and nothing to restart after an edit. The
agent's entry point starts its own environment, registers itself, sends its own
signals and waits for the result — so `meridian eval case` is a complete
Temporal execution, and the next one picks up your patch because the module is
imported fresh each time.

Two consequences worth knowing before you go looking for a control surface:

- **A deadline does not cost you the wall-clock.** The environment skips time,
  so a workflow that waits two days for a corrected document resolves in
  milliseconds. If a case takes minutes it is doing work, not waiting.
- **A hung case is an exception.** Temporal retries a failing workflow task
  forever, with no traceback and no exit, so the timeout is what turns silence
  into a reportable error. A case that times out is almost always raising
  somewhere inside workflow code.

> **Which of these exist right now:** `meridian board · card · edge · review ·
> thread · spec · eval · bundle · build · repair` are built. **`meridian verify`
> is not.** If a command is missing, say so and stop rather than inventing a
> substitute; a loop that invents its own verification is not verifying anything.


The gate is **target passes AND no regression**. It can only reject; a human
overrides, never approves.

## 8. Review the patch before you report

Read the diff back and ask the two questions that matter:

- **Does this fix the cause or the symptom?** Making a case pass by special-casing
  its values is not a repair, it is a hardcode that the next case exposes.
- **Does the check still measure what it measured?** A criterion deleted, a
  comparison loosened, an outcome widened — each makes the suite pass by
  measuring less. Conformance catches the blatant version; the subtle version is
  on you.

Then check the mistakes this loop actually produces: a `try/except` that turns a
bug into a business outcome, a normalisation applied where the spec asked for an
exact match, and a fix in the file the bundle named when the trace showed the
cause upstream.

## 8b. The measurement moves on its own, and you must not chase it

Classification and extraction are model calls. **Temperature zero is not
determinism, so two sweeps of identical code disagree.** Measured on this
corpus: builds 8 and 9 are the same commit, scored 45/81 and 47/81, and four
columns on one case flipped — three the right way, one the wrong way. That last
one was enough to file a working patch as `regressed`.

Two consequences, and both change what you do:

- **A one-column change is not evidence.** If your patch moves a single column
  you have learned nothing; re-run before believing it. A real fix moves a
  bucket — the same column across several cases, or several columns on one case.
- **A one-column regression on a case you did not touch is probably noise.** Ask
  whether what you changed could plausibly reach that column. If it could not,
  say so in your report rather than reverting a good patch to chase a flip.

This does not soften the gate. It can still only reject, and a *reproducible*
regression is still a reason to revert. It means "reproducible" is now carrying
weight it was not carrying before — and that confirming a regression costs one
re-sweep of one case, which is seconds.

## 9. Know when to stop trying

Three attempts on one signature with no improvement is not persistence, it is a
misdiagnosis. Stop and report what you learned rather than trying a fourth
variation of the same idea — the loop has a budget, and a signature that resists
three honest attempts is usually one of the two things you are not allowed to
decide.

The same applies to a patch the gate rejects twice for regressions. Two
different attempts breaking two different sets of previously-passing cases means
the thing you are changing is load-bearing in a way the failure did not reveal.

## 10. Leave a record the next attempt can read

**Your report in chat disappears. Write the attempt down.**

A `/goal` loop runs you repeatedly, often across sessions, and the single most
wasteful thing it can do is retry an approach that was already rejected. The
bundle's `REPAIR HISTORY` section is built from these files.

Append one line per attempt to a file named by the **failure signature** — the
same string the bundle prints, which is stable and is exactly what "do not retry
this" keys on:

```
agents/<slug>/repairs/coas_valid__output_diff__coa_count_mismatch.jsonl
```

One JSON object per line, appended, never rewritten:

```json
{"attempt": 1,
 "signature": "coas_valid :: output_diff :: coa_count_mismatch",
 "classification": "implementation_defect",
 "tried": "canonicalised both sides at the call site before each_has_matching",
 "files": ["agents/inbound_pre_alert/checks/coas_valid.py"],
 "outcome": "accepted",
 "before": "19/21", "after": "21/21", "regressed": [],
 "falsified": "batch_values_compare_exactly"}
```

`outcome` is one of:

```
accepted    the target passed and nothing regressed
regressed   the target passed and something else broke — say WHICH columns
stopped     spec gap, skeleton defect, or three attempts with no progress
```

**A `stopped` line is the most valuable one in the file**, because it is the one
that keeps the next session from spending its budget re-deriving what you
already found. Say what you concluded and what would change the answer.

`meridian repair record` reads this directory into the `repairs` table. Write the file even if you fixed
nothing — an attempt that failed is history, and history is what stops the loop
going in circles.

## 11. Report

- what changed, in one sentence, and **why that was the cause**
- **which assumption this falsified**, if any — and update `assumptions.json`,
  because a prediction that has been disproved and left in place will mislead
  the next session
- which cases it fixes, and which it does not
- the classification, and for a spec gap or skeleton defect: the decision
  somebody else has to make, and the two or more readings it lies between
