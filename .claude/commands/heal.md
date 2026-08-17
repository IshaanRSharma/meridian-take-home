---
description: Run the self-healing loop on a generated agent until the eval suite passes or a decision is needed.
argument-hint: <board_id> [max-patches]
---

# Heal

Run the repair loop on the agent built from board `$1`, stopping at `$2` accepted
patches (default 8).

You have an oracle. Unlike the review loop, nothing here needs a human to say
whether an answer is right — the eval suite already knows, so keep going until it
passes or until you hit something the suite cannot settle.

## One case at a time, over a set that only grows

Sweeping everything and fixing the biggest bucket sounds efficient and is not.
**Each eval case is a different shape** — one delivery or several, native
documents or scanned, one unit of work or eleven — and a sweep over all of them
averages the shapes together, so a failure caused by one shape is invisible
among the others. Worse, a full sweep is minutes and a single case is seconds,
which decides whether this loop gets run twice or twenty times.

So: **take one failing case, fix it, then keep it in the set forever.**

```
working set  =  every case already passing  +  the one being fixed
```

Order matters. Start with the case that has the fewest moving parts and add
complexity — a shape you have never processed will fail for reasons that have
nothing to do with the last repair, and meeting them one at a time is the whole
point.

**The set may never shrink.** A case dropped from a sweep is absent rather than
failing, and absent is not evidence of anything. The gate refuses a build
measured on fewer cases than the one it is compared to, so this is enforced
rather than remembered — but sweep the whole working set anyway, because a
refusal costs you the iteration.

## The loop

```
1  meridian eval sweep $1 --case <each case in the working set>
   Every column passes → add the next case to the set and repeat.
   No cases left → STOP and report the score.

2  meridian bundle $1
   No --signature: the largest failing bucket is chosen for you, because
   "which file next" is a ranking question and the biggest bucket is the answer.

3  READ THE BUNDLE BEFORE ANY CODE.
   ASSUMPTIONS THAT COULD EXPLAIN THIS  — a falsified_if matching what you see
                                          is the diagnosis, in seconds
   REPAIR HISTORY FOR THIS SIGNATURE    — an approach recorded here was already
                                          tried. Do not try it again.
   DECLINED                             — zeros with a non-empty DECLINED means
                                          nothing reached a check. Fix the
                                          reading, not the check.

4  Use the repair-agent skill on the bundle. ONE file, the one FILE names.

5  meridian build register $1
   Prints the new iteration. Use it below.

6  meridian eval sweep $1 --case <the whole working set> --build <new>

7  meridian repair record $1 --build <new> --signature <the one you fixed> \
       --class implementation_defect --summary "<what changed and why>"
   The gate compares the two sweeps per column and answers accepted or
   regressed. It can only reject; you may not override it.

8  Regressed → undo the edit and go back to 3 with what you learned.
   Accepted  → back to 1.
```

## Stop, and say which

Three ways this ends. **Two of them are not failures**, and reporting a score as
though the loop finished when it stopped for one of these is the worst thing you
can do here.

| stop | say |
|---|---|
| every case in the suite passes | the score, and which columns had no source to begin with |
| `$2` patches accepted | the score, the curve, and what you would open next |
| a decision that is not yours | *what decision*, and who has to make it |

The third is the one that matters. Two shapes:

- **`spec_gap`** — no patch is correct because no rule decides the answer. Two
  competent people could disagree and the customer would care which you picked.
  Record it with `--class spec_gap --thread <id>`; the table refuses it without
  a thread, because a decision nobody can settle in code has to go back to the
  person who owns the process.
- **`skeleton_defect`** — the fix belongs in `meridian/runtime/`, outside the
  agent directory. Patching it into two leaf files instead is how a shared
  scaffold quietly stops being shared. Stop and report it; do not work around it.

## Adding what you know, mid-loop

You will work something out while reading a failure. There are two places to put
it and picking the wrong one is how a business decision ends up laundered into
code with nobody's approval on it.

```
agents/<slug>/hints.json      IMPLEMENTATION context. Yours to write. Outside
                              the checksum, so it never drifts the build from
                              the contract. The next bundle carries it.

meridian thread ...           a BUSINESS answer. Belongs to whoever owns the
                              process. Becomes an assertion, needs a re-freeze,
                              and codegen then implements approved knowledge.
```

The test is the one you already use to classify: **could two competent people
disagree, and would the customer care which you picked?** If yes it is a thread,
however tempting the one-line fix. If no it is a hint.

```json
// hints.json — keyed by primitive, "*" applies everywhere
{
  "coas_valid": ["certificates print the batch without the invoice's suffix"],
  "*": ["scanned attachments carry no text layer; they are rendered, not parsed"]
}
```

*"Certificates print it without the suffix"* is an observation about the
documents — a hint. *"A batch with and without the suffix is the same batch"* is
a decision about the business — a thread. They sound alike and they are not.

## Rules

**Per column, never per row.** The gate already works this way. A patch that
fixes one column and breaks another leaves the row failing before and after, so
a row-level reading watches a regression go past.

**A green sweep is not proof.** A run returning all zeros scores every column
whose expected value is zero as a pass. `bundle` prints an empty-sweep header
when it sees this — believe it over the number.

**Never edit the eval set, `spec.lock.json`, or anything outside
`agents/<slug>/`.** They are the oracle and the contract. A loop that edits what
it is measured against has stopped measuring anything.

**Append to `assumptions.json` when you make a new decision**, with the same
`falsified_if` shape. The next session reads it out of the bundle.

**If a command does not exist, stop and say so.** Do not substitute another one:
a loop that invents its own verification is not verifying anything.

## Not yet built

`meridian verify` — the import-allowlist and conformance gate — does not exist.
Until it does, nothing mechanically stops a patch making the suite pass by
deleting a check, so read your own diff for that before recording it.
