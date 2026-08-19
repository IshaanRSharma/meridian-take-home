# Generated agents

Three directories. **`inbound_pre_alert_validation_final/` is the deliverable.**
The other two are the control and the discard, and they are kept because the
comparison between them is the result.

| directory | what it is | score |
|---|---|---|
| **`inbound_pre_alert_validation_final/`** | **the deliverable** — generated from the reviewed spec, then healed by the repair loop over four iterations | **61/63 reachable · 8/9 shipments green** |
| `inbound_pre_alert_cleanroom/` | the control — the same spec handed to a generator that was blocked from reading anything else | measured separately |
| `inbound_pre_alert_validation/` | the first attempt, against the first board. Mostly hand-written, kept for its history | 47/63 |

## Read this before comparing them

`_final`'s **first build was not naive**, and that matters for what its curve
means. The generator was handed three findings that came out of the *first*
agent's repair cycles — that the invoice states its lots in a `BATCH NOS` block
rather than per line item, that scanned bundles run past the page cap, and that
Gmail's `attachmentId` is not stable — and it reused four modules from that
agent verbatim. So its build 1 measures *accumulated knowledge re-expressed
against a better spec*, not what the spec alone produces.

`inbound_pre_alert_cleanroom/` exists to remove that. It was generated from
`spec.lock.json` and `meridian/runtime/` with the other agents, their
`assumptions.json`, their repair logs and the design notes all blocked. Its
score is the honest answer to *"hand the frozen spec to a fresh agent cold"* —
the sufficiency test the build notes call for.

**What is clean either way is everything after build 1 of `_final`.** Builds 2
through 5 were written entirely by the `repair-agent` skill, driven by `/heal`,
with no human edits to the agent directory:

```
build 1  generated  52/63 · 4/9 green
build 2  repair     52/63 · 4/9
build 3  repair     52/63 · 4/9      flat, kept with a reason, not a score
build 4  repair     54/63 · 5/9
build 5  repair     61/63 · 8/9      gate ACCEPTED
```

Gate verdicts across those: two rejected, one escalated, one accepted.

## Why it stops at 61 and not 63

The last two columns are `failed_coa` and `coa_success` on `MNBU4407370`. The
certificate reads `HRB125012`; the invoice writes `HRB125012BR` — a **two**
letter suffix. Review already settled the one-letter case as a rule with a
negative bounding it: *"no fuzzy matching beyond dropping that one trailing
letter."*

The eval set says those are the same batch. Stripping two letters would
contradict an assertion a person approved, so the loop raised a thread rather
than overriding an approved decision because a test told it to. That is a
re-freeze, not a patch.

Two further columns — `status` and `invoices_mismatched_asn` — are filled by no
card on this board and fail every case by construction. They are the ceiling,
not a defect, which is why the reachable denominator is 63 and not 81.
