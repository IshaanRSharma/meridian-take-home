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

## The loop

```
1  meridian eval sweep $1 --split train
   Every column passes → STOP and report the score.

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

5  git add -A && git commit    (agents/ is the diff; every repair is reviewable)

6  meridian build register $1 --from-git
   Prints the new iteration. Use it below.

7  meridian eval sweep $1 --split train --iteration <new>

8  meridian repair record $1 --build <new> --signature <the one you fixed> \
       --class implementation_defect --summary "<what changed and why>"
   The gate compares the two sweeps per column and answers accepted or
   regressed. It can only reject; you may not override it.

9  Regressed → revert the commit and go back to 3 with what you learned.
   Accepted  → back to 1.
```

## Stop, and say which

Three ways this ends. **Two of them are not failures**, and reporting a score as
though the loop finished when it stopped for one of these is the worst thing you
can do here.

| stop | say |
|---|---|
| every column passes | the score, and which columns had no source to begin with |
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
