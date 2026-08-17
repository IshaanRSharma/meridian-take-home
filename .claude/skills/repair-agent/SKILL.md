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

**Read `assumptions.json` first.** The generator recorded every decision the
spec did not determine, each with a `falsified_if` — a prediction about what
failure would disprove it. Scan those against the failure in front of you before
reading a line of code:

```
falsified_if: "a real pre-alert is missed"        ← and the sweep found 0 runs
falsified_if: "a document is declined that
               should have been read"             ← and DECLINED names the invoice
```

A match there is the answer, and it arrives in seconds rather than after reading
a diff. An assumption whose `prompted_by` is `null` had no spec anchor at all,
which makes it both the likeliest cause and a candidate spec gap.

**Then read `DECLINED`.** Half of "found nothing" failures are a
document that was skipped, not a document that was absent, and those have
completely different fixes.

**Check `REPAIR HISTORY`.** Do not retry an approach already rejected. Without
this, the same failed idea gets tried across sessions.

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

The second test is **whether you can express the fix at all**, and it is asked
second because it only matters once you know the decision is yours:

> Can I make this work by changing something under `agents/<slug>/`?

Almost always yes, because the scaffold injects its moving parts rather than
importing them. A reader, a classifier, an extractor, a provider, a comparison —
all of these arrive as arguments, so the agent can pass a different one without
anybody editing `runtime/`. **Wrapping or replacing an injected component is an
ordinary patch, not a skeleton defect.**

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
| every case fails identically at the same step | **skeleton defect** |

A bucket covering **100% of cases** is evidence in itself: a per-check bug fails
some cases, an infrastructure bug fails all of them the same way.

## 3. When there is nothing to compare

A sweep can fail without producing a single comparison: zero runs, or every case
erroring identically. There is no expected-versus-actual, so the bundle you were
handed is nearly empty — and that emptiness **is** the diagnosis.

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

```bash
mvp eval case <KEY> --build <n>            # the target case
mvp eval sweep --build <n> --split train   # nothing previously passing broke
mvp verify --agent <slug>                  # imports · conformance
```

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

## 9. Know when to stop trying

Three attempts on one signature with no improvement is not persistence, it is a
misdiagnosis. Stop and report what you learned rather than trying a fourth
variation of the same idea — the loop has a budget, and a signature that resists
three honest attempts is usually one of the two things you are not allowed to
decide.

The same applies to a patch the gate rejects twice for regressions. Two
different attempts breaking two different sets of previously-passing cases means
the thing you are changing is load-bearing in a way the failure did not reveal.

## 10. Report

- what changed, in one sentence, and **why that was the cause**
- **which assumption this falsified**, if any — and update `assumptions.json`,
  because a prediction that has been disproved and left in place will mislead
  the next session
- which cases it fixes, and which it does not
- the classification, and for a spec gap or skeleton defect: the decision
  somebody else has to make, and the two or more readings it lies between
