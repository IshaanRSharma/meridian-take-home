# The reviewer

What turns a drawn board into questions a process owner can answer, and their
answers into statements that cross the freeze. Companion to
[`compiler.md`](./compiler.md), which covers the findings and decisions this
reads.

The brief grades this on one line: *"does it actually capture and resolve
ambiguity, or is it decorative."* Everything below exists to make that provable
rather than claimed.

---

## The shape

```
owner edits the board
        │
        ▼
   [ Ready for review ]          explicit. never automatic, never on save.
        │
        ├─ blocking findings? ──▶ REFUSE and name them. The round does not start.
        │
        ▼
   round N
        │  1. re-run the walk behind every answered comment   ── the revision loop
        │  2. enumerate situations from the graph, walk them
        │  3. blanks → comments, in the words the rule wrote
        │  4. board + walks + the conversation so far → ONE model call
        │  5. rank, dedup against every prior, cap at six
        │  6. write comments · write situations · round += 1
        ▼
   owner answers, and redraws
        │
        └──────────▶ back to [ Ready for review ]
```

**`run.py` never writes a card or an edge.** The only board column it touches is
`review_round`. That separation is what makes `resolved` mean something: if the
reviewer could redraw the board it would be marking its own homework, which is
exactly the decorative loop the brief is asking about.

---

## What the model is shown

Assembled fresh every round from rows, and **never stored**. A saved payload
goes stale the moment someone drags a card, and then there are two answers to
"what does the board say". Rebuilding costs one board read and removes a whole
class of bug. At a scale where that mattered you would cache it against the
board's updated_at; at eleven cards it does not.

Five things go in. Two are right today, three are not.

### ✓ The steps, with everything the owner typed

Full config per card — criteria and their operators, timing, outcomes with the
owner's own descriptions, and `instructions` verbatim. This was a summary until
this unit; inlining it is what let the model start noticing that a typed field
and the prose beside it disagree.

### ✗ The things, currently flattened to a list of names

Today the model sees this for the invoice:

```json
"fields": ["container_no", "invoice_no", "line_items"]
```

and the board actually holds a nested schema — `line_items` is an array whose
items carry six fields, every identifier typed `["string","null"]` on purpose so
a Check can detect the failure it exists to detect.

Three question classes die in that flattening. **Nesting**: it cannot ask
whether one bad line fails the line or the invoice, because it cannot see that a
line is a repeated thing. **Nullability**: it cannot ask whether empty and
missing are the same. **Fields nobody checks**: it cannot ask at all.

Fix: the same one already applied to steps — the entity's whole config,
`instructions` included.

### ✗ The paths, and what data is available along each

The walks are in the payload, but as a bare trace. The model has to rebuild the
shape of the process from an adjacency list, and a semantic question is almost
always about a relationship between two points on a path — *how do you match
this to that, by the time you get here*.

So each walked situation carries the path, the edges and their conditions, and
what each step does with the data:

```
coas_valid_missing_coa  (variant)
  what happens   A batch on the invoice has no certificate.
  path           prealert_received ──e1──▶ invoice_complete ──e2 on 'pass'──▶
                 coas_valid ──e5 on 'missing_coa' (exception)──▶ report_coa_discrepancy
  ended          dead_end
  never reached  documentation_validated, report_invoice_discrepancy
  along the way
    prealert_received       brings in  prealert_email, commercial_invoice,
                                       certificate_of_analysis
    invoice_complete        tests      commercial_invoice.line_items[].hts_number,
                                       …fda_product_code, …ndc_number, …anda_number
    coas_valid              tests      commercial_invoice.line_items[].batch_no
                                       against certificate_of_analysis.batch_no
    report_coa_discrepancy  sends      commercial_invoice.invoice_no,
                                       commercial_invoice.line_items[].batch_no
```

All of it derives from `Board.field_references`, `reads()` and `produces()`.
Nothing new is computed or stored.

Groups are included when a board uses them. The pre-alert board has none, so
subgraph structure is expressible and unexercised — worth saying rather than
implying coverage that has never run.

### ✗ What the board declares and never touches

The single highest-value block, and it is four lines of code:

```
DECLARED BUT NEVER READ BY ANY STEP
  prealert_email.sender · subject · received_at · attachment_names
  certificate_of_analysis.product_code
```

Each is either a rule nobody wrote down or a field that should not be there, and
both are worth asking. On this board:

- `prealert_email.sender` is the seed file's own documented gap — reports have to
  go somewhere and *"whoever sent it"* is a guess until someone is asked.
- `certificate_of_analysis.product_code` is the good one. The check matches on
  `batch_no`, and the board declares a product code that nothing reads. The SOP's
  actual rule is that association is by product code rather than page order — so
  the question *"which one identifies which certificate belongs to which line?"*
  is sitting in the data, unasked.

### ~ The conversation so far

Every prior comment with every turn, including the rejected ones with their
reasons. Today it omits `decision_key`, `scenario_key` and `evidence` — so the
model is told to dedup against identities it is not shown.

**Blanks are deliberately withheld.** Every finding is already being put to the
owner in the words the rule wrote. Measured, not assumed: with findings in the
payload the model spent all six questions restating them and asked nothing about
what the values mean. Without them, it asks about matching, ordering and
derivation. They are still asked — just not by the model.

---

## How curiosity is structured

A model asked *"what is missing"* under-extracts badly, because recall is the
wrong operation. So it is never asked that. It is handed bounded spaces and told
to instantiate each against **this** board:

| | probes |
|---|---|
| situations | timing · cardinality · quality · authority |
| values | how many · same or not · acceptable · which one · why is this here |

And one tool, `dry_run`, so a claim about where a situation ends up is one it
**obtained** rather than asserted. The tool's own probe becomes the evidence
stored on the comment — today only enumerated walks are cited, so the model
probes, forms a claim, cites it, and the citation is discarded.

Nothing it returns is trusted about the board. An anchor that resolves to no
card drops the question; an identity it invented is discarded; a walk it cites
that nobody took is not recorded. **Wording is the model's. Assertion is not.**

---

## A comment has to land on something you can point at

```
   ┌──────────────────┐
   │  coas_valid    💬│   the pin sits on the first drawable anchor
   └──────────────────┘
            ║ hover
   ┌────────╨─────────┐   ┌────────────────────────┐
   │ certificate_of   │   │ commercial_invoice     │
   │ _analysis      ◇ │   │  ◇ line_items[]        │   the rest, highlighted
   └──────────────────┘   └────────────────────────┘
```

**At least one anchor must be something that can be drawn** — a `primitive` or
an `edge`. `board` may accompany them and may not stand alone. The interface
pins the comment to the first drawable anchor and highlights the others on
hover, so a multi-anchor question shows the whole shape it is about.

This is a requirement rather than a preference because it was measured breaking.
Asked twice on one board, the model anchored precisely to entity fields and
edges one run, and put **every** comment on `board` the next — three comments
nobody could locate, on a product whose brief asks for comments *on the canvas*.
It also produced `primitive:prealert_email.received_at`, which is malformed —
that is an `entity_field` — and was silently discarded.

So the rule is enforced in two places and **counted in both**: the schema
demands it, validation drops a question that survives with no drawable anchor,
and the round reports how many were dropped and why. Three of the four problems
found in a day were invisible until someone ran it by hand; a number would have
shown all of them.

## Two questions are the same when a model says they are

`temperature=0` does not make a reasoning model deterministic. Measured on one
board, twice: **0% identical wording, 17% identical topic.** The same question —
*how do you know which certificate belongs to which line* — came back in
different words each run.

Two things follow. §11's determinism check cannot compare strings, and
model-invented comments cannot be deduped across rounds by their text.

Both are the same operation, so both get the same answer: **ask a model.** One
call, cheapest tier, closed output — here is every question already asked and
its status, here are the new candidates, which candidates repeat one? Code drops
them. The model judges, code decides, exactly as everywhere else.

```python
async def unasked(candidates, priors, *, transport=None) -> tuple[Thread, ...]
```

Structural comments never go near it — they carry an identity derived from the
graph, and an exact match beats a judgement whenever one is available. The judge
exists only for what has no identity by design.

That one function is also what turns the determinism test from unimplementable
into a number: *how many of run two's questions did run one already ask* is the
same question, asked of two candidate sets instead of a set and its priors.

## The two loops

```
COMMENT LOOP      open ──owner answers──▶ answered
                       ──not applicable─▶ rejected     kept as negative knowledge

REVISION LOOP     answered ──the walk now ends elsewhere──▶ resolved
                           ──it still fails──────────────▶ open
```

`answered` means the knowledge exists. `resolved` means the drawing shows it,
**and the owner does not get to declare it** — step 1 of the round re-runs the
walk stored on the comment's evidence and compares. That is the difference
between resolution being proved and being asserted, and it is the whole reason
scenarios are stored rather than regenerated.

Every comment needs a stable identity or dedup is dead. Today only decisions
have one:

| source | identity |
|---|---|
| a blank | `lint:{anchor}:{field}` — already unique, findings merge on that pair |
| a walk | `scenario:{key}:{result}` |
| a decision | `DecisionPoint.key()` — exists, attached to nothing |
| the model | none, by design — deduped by having every prior in context |

And a settled comment whose gap is still on the board must **reopen**, not be
silently dropped — otherwise a board freezes with a live hole in it and a
`resolved` label over the top.

---

## What is tested, and what that proves

67 unit tests across five modules today. They prove the pieces work. **They do
not prove the loop works**, because no second round has ever run.

The test that answers the grading line is one scripted two-round conversation,
offline against a fake model so it is deterministic and free:

```
round 1 on the seed board        → comments
hand-write the owner's answers   → real turns on real comments
apply the edits those imply      → the completed board
round 2                          → assert:
    · nothing re-asks a round-1 identity, including a rejected one
    · an answered comment whose walk now ends elsewhere is resolved
    · one whose walk still fails is back to open
    · a gap still on the board reopens rather than vanishing
    · the new questions are not the old questions
```

That fixture — the pre-alert board with its four gaps closed — is also what the
ablation suite needs, so one 40-line fixture buys both. Ablation then gives
**recall as a number**: delete a known fact, check the reviewer asks for it, over
N facts. Lint-derived ablations should be caught 100% by construction; semantic
ones will be lower, and that gap is honest data worth reporting.

Two more, both cheap: **determinism** — two runs on one board must produce
identical structural questions — and **no-regression** — run against the
completed board and expect near-zero. A finished board still generating six
questions means the prompt is too fussy and the owner stops reading.

Coverage gets reported as *"N of M situations reach a card marked as an ending"*
**with the M listed beside it**. As a bare fraction it is misleading: on the seed
board it goes 25% → 100% by ticking two checkboxes lint already demands, without
answering a single question.

---

## Order

```
0  instrument        proposed · kept · dropped, with reasons
1  payload           entities in full · paths with data flow · fields nothing
                     reads · prior identities · criteria kept apart from evidence
2  anchors           at least one drawable, enforced and counted
3  the judge         unasked(), and the determinism test it makes possible
4  identities        lint:{anchor}:{field} · scenario:{key}:{result}
5  run.py            the round, including the re-run that proves `resolved`
6  a completed board one fixture, used by both tests below
7  two rounds        proves the loop is not decorative
8  ablation          recall as a number
```

Only **1 and 2 change the answers.** 3 changes their consistency; 4 through 8
are correctness and proof. So 1 and 2 land first and get measured against a live
round before anything else is written — if the questions do not visibly improve,
the rest of the order is wrong.

---

## Not built, deliberately

**The corpus.** The historical output would let round two ask *"every shipment
you have processed reports a count your board does not produce"* — the best
question available on this process. It needs the eval set in the payload, and
the eval set's values are the repair loop's oracle; letting them into elicitation
fits the spec to the test rather than to the process. Cut for time, not for
doubt.

**Reference documents.** Feeding the SOP would unlock the highest-precision
question class — *the document says X and your board says Y* — because the
discrepancy is checkable rather than speculative. A second parsing pipeline, and
48 hours does not have room.

**AI-proposed canvas patches.** The reviewer surfaces the gap and the choice; the
owner edits. Two competent people would model *"urgent goes to the ops manager"*
as either a new check with two branches or one step that decides at the time, and
picking is a business decision. Costs a demo beat, not correctness.

**A separate round-two generator.** Rounds differ because the board and the
conversation differ, not because a different generator runs. That is weaker than
the corpus-grounded round two originally designed, and it is what is honest with
the corpus cut.

---

## Measured

The output is questions, so there is no oracle. Everything below is a number
instead, measured against a **completed board** — `db/seeds/prealert_complete.json`,
the seed as a process owner leaves it: every blank filled, every outcome wired,
the way back drawn after a discrepancy is reported. The pair of boards is what
makes any of this measurable, and neither number means anything without a board
somebody would call done.

### Recall on what a rule can prove — 7/7

Take the completed board, delete one fact, check the reviewer asks for it back.

```
report_coa_discrepancy.recipients        → asked
report_invoice_discrepancy.system        → asked
coas_valid.on_missing_input              → asked
invoice_complete.on_missing_input        → asked
prealert_received.correlation_key        → asked
prealert_received.match_condition        → asked
report_coa_discrepancy.idempotency_key   → asked
```

100%, and it should be: a field nobody filled is a finding, and a finding is a
question. Anything less here is a broken pipe rather than a weak reviewer, which
is why the harness is checked by breaking the pipe on purpose — sever
`_from_blanks` and all seven fail.

Rubbing out the line for a declared outcome is deliberately **not** in this list.
That leaves a drawing that is not a process, which a person can see on the
canvas, so it is a refusal at the gate rather than one of six slots spent asking
someone to answer in prose what they could simply draw.

### Noise on a finished board — 0 provable, 6 noticed

Run the completed board untouched. Deterministically **zero** questions: nothing
is unfilled, so nothing provable is left to ask. That is the number that decides
whether anyone still reads round three.

With a real model it asks **six**, and this is the result the whole design is
for. The board is lint-clean and every question is a genuine business gap:

| asked about | the gap |
|---|---|
| `coas_valid` | which certificate, when two could both match |
| `coas_valid` | what counts as the same batch number — exact text, or ignoring case and padding |
| `prealert_received` | does a second email restart the checks or amend them |
| `invoice_complete` | one bad line item, or the whole invoice |
| `report_invoice_discrepancy` | does a later correction close the earlier report |
| `report_coa_discrepancy` | who actually receives it |

The last one is the one worth reading twice. `recipients` **is** filled, with
`receiving_supervisor`. Lint is silent. What the model found was a contradiction
between that typed field and prose on a *different card* — the email entity's
note that the SOP "never says to whom". **A field being filled is not the same
as the rule being known**, and no structural check can tell the difference.

Which is the claim the reviewer exists to make, stated as a measurement:
**structure and semantics are different things, and a structurally perfect board
still hides six business decisions.**

### What the tools contributed

`where_values_meet` was called on every live round and produced two question
classes nothing else found: the match-semantics question above, and a question
about the **correlation key** — *what counts as the same invoice when the details
are spread across several emails*. The correlation key is the largest join on
any board and appears nowhere as a comparison, so it went unasked for four
consecutive runs before the tool existed.

### Determinism

`temperature=0` does **not** make a reasoning model deterministic: two runs on
one board share 0% of their wording and roughly 17% of their topics. Lint output
is identical run to run, so the deterministic half is genuinely deterministic;
the semantic half is not, and the honest reading is that a single round samples
a distribution rather than enumerating it. That is the argument for `unasked()`
— an LLM judging question equivalence — which is designed and not built.

### Yield — 6/9

The question the measurements above do not answer: the spec *contains* the
answers, but does any of it say something the drawing had not already said?

`distill.paraphrases` asks a model, for each settled statement, whether it could
have been written from the board alone. Run against a full pass over the
completed board — review, answer every question, settle — 6 of 9 statements
carried something new.

The three that did not are instructive, because all three were correctly
anchored, correctly inlined and correctly checksummed:

```
"The shipment is keyed off the container number."     ← correlation_key says exactly this
"The process keys off the container number."          ← and the same fact again, twice
"A bad line item is reported once per line, not
 once per missing identifier."                        ← scope: per_line_item says this
```

Against:

```
"A COA batch matches after stripping spaces and leading zeros, ignoring case."
"If two COAs could match one batch, the later by issue date wins and the
 earlier is marked superseded."
"If the supervisor does not respond within 48 hours it escalates to the ops
 manager."
"A later email amends the shipment already in progress and never starts a new one."
```

Reported, never dropped. A restatement is still true, and losing a settled
answer to a model's judgement is the worse failure — the same trade as demoting
a mislabelled constraint rather than refusing it. What it buys is the honest
number for what the review added on top of the drawing, and one visible defect:
the same fact arrived twice from two different threads, which is what
`unasked()` would catch and does not exist yet.
