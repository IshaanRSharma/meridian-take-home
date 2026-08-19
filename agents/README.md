# Generated agents

Three directories. **`inbound_pre_alert_cleanroom/` is the deliverable** — it is
the only one where both halves of the pipeline are isolated. The other two are
kept because the comparison between them is the result.

| directory | what it is | ends at |
|---|---|---|
| **`inbound_pre_alert_cleanroom/`** | **the deliverable** — generated from the frozen spec and the runtime scaffold and nothing else, then healed over four repair iterations | **58/63 reachable · 8/9 shipments green** |
| `inbound_pre_alert_validation_final/` | the same spec, but its codegen step was seeded by hand — kept as the comparison | 61/63 · 8/9 |
| `inbound_pre_alert_validation/` | the first attempt, against the first board. Mostly hand-written | 47/63 |

## The two runs, side by side

Same spec. Same nine shipments. The difference is what the generator was told.

```
   COLD — spec + scaffold only            SEEDED — plus findings and copied files
 6  codegen    29/63 · 0/9 green        1  codegen   52/63 · 4/9 green
 7  repair     39/63 · 2/9              2  repair    52/63 · 4/9
 8  repair     47/63 · 3/9              3  repair    52/63 · 4/9
 9  repair     58/63 · 8/9              4  repair    54/63 · 5/9
10  repair     58/63 · 8/9              5  repair    61/63 · 8/9

    +29 columns, 0 → 8 shipments           +9 columns, 4 → 8 shipments
```

The seeded run was handed three findings that came out of the *first* agent's
repair cycles — that the invoice states its lots in a labelled block rather than
per line item, that scanned bundles run past the page cap, and that Gmail's
`attachmentId` is not stable — and it copied `mail.py`, `cache.py` and `spec.py`
byte-for-byte. So its starting score measures transferred work, not what the
spec produces.

The cold run was generated with both other agents, their `assumptions.json`,
their repair logs and the design notes blocked. It shares no filenames with
either: its own `ingestion/` package, its own `matching.py`, `recognition.py`,
`wiring.py`. Its healing loop was given the board id, the agent path, the budget
and the rules — nothing about the corpus.

**Both land at 8 of 9.** The cold run climbed three times as far to get there.

## What the cold start found that review did not

The board correlates shipments on `commercial_invoice.container_no`. On this
corpus that column is headed *MARKS & NOS./CONTAINER NO.* and mostly holds
marks — an address, a booking reference, a carrier name.

```
0 of 9 eval shipments name their container in an email subject
9 of 9 name it in the body, against the field's own label
```

So no email correlated to any shipment, every check ran over zero rows, and the
first sweep scored 29/63 on nothing but columns whose expected value happens to
be zero. The fix reads the value where it is written and derives the search label
from the field path, so a board correlating on `application_id` looks for
*"application id"* with no change to the code.

Two rounds of AI review did not catch this, because the reviewer reads the
drawing and the prose and not the corpus. `_final` hid it, because it inherited
a mail reader that already took the container from the message body. **Only a
cold build could surface it** — which is the sufficiency test the build notes
call for, answered with evidence.

The generator had predicted its own failure. `assumptions.json` carried
`correlation_key_read_only_from_the_invoice`, whose `falsified_if` read *"cases
return empty rows for shipments whose paperwork is plainly in the mailbox."*
That is exactly what happened.

## Why both runs stop short of 63

`MNBU4407370` lists batch `HRB125012BR`; its certificate carries `HRB125012`.
Both are read correctly — this is not an extraction failure.

The card authorises stripping *"a trailing lot suffix **letter**"*, bounded by a
negative: *"No other differences are ignored."* This suffix is two letters. The
eval set says the two are the same batch, and the suite cannot tell a lot
revision from a different batch — both readings are self-consistent.

So both runs escalated instead of widening the rule, independently and with no
shared context: threads `53961ea4` and `f580580a`. Passing that case means
re-freezing the spec, not patching the agent.

Two further columns — `status` and `invoices_mismatched_asn` — are filled by no
card on this board and fail every case by construction. They are the ceiling,
not a defect, which is why the denominator is 63 and not 81.

## Reading the code

`agents-cleanroom/` at the repo root is a symlink, not a copy. Both `build
register` and `eval sweep` resolve an agent as `<root>/<spec.slug>`, and the
clean-room build is a second implementation of the *same* spec — so it carries
the same slug and cannot sit beside the first under one root.
