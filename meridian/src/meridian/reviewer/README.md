# reviewer

Turns a drawn board into questions a process owner can answer, and their answers
into statements that cross the freeze.

The brief grades this on one line: *does it actually capture and resolve
ambiguity, or is it decorative.* Everything here exists to make that provable
rather than claimed. The full design writeup is
[`docs/design/reviewer.md`](../../../../docs/design/reviewer.md).

| file | what it does |
|---|---|
| `run.py` | one round. The orchestration, and the only thing that writes. |
| `scenarios.py` | situations enumerated from the graph. A coverage floor. |
| `dryrun.py` | walk a situation, describe it, cite it, re-run it later. |
| `semantic.py` | the one place a model looks at a board. |
| `tools.py` | what the model may do besides read: `dry_run`, `where_values_meet`. |
| `prompts.py` | what the model is told before it is shown anything. |
| `ranking.py` | six per round, and a question already dealt with never returns. |
| `distill.py` | a settled conversation into statements, one per element. |
| `extract.py` | words off a page, including a PDF or a photo of one. |
| `reference.py` | a written procedure aligned onto elements of the board. |
| `sufficiency.py` | is the frozen spec enough to build from. |

## A round, in order

```
1. refuse if the drawing is not a workable process yet
2. settle: distil every finished conversation, close what is proved
3. re-read the board, because settling just changed it
4. enumerate situations from the graph and walk them
5. read any attached procedure and align it onto elements
6. one model call: the board, the walks, the conversation so far
7. rank, dedup against every prior, cap at six
8. write comments, write situations, round += 1
```

Step 2 comes before step 6 on purpose. A question answered last round is either
shown by this drawing or it is not, and that has to be decided before anything new
is asked, or the round asks about gaps it is in the middle of closing.

**`run.py` never writes a card or an edge.** The only board column it touches is
the round counter. If the reviewer could redraw the board it would be marking its
own homework, and `resolved` would stop meaning anything.

## Three sources of questions, in descending order of certainty

**Blanks.** A rule found a field nobody filled. Asked in the words the rule already
wrote, with no model involved, because the sentence on the card *is* the question.

**The model.** What a rule cannot see: what the values mean, and what the drawing
quietly decided. This is the half only an LLM can do.

**The cap.** Six, because a review somebody answers beats a form they abandon.
`ranking.rank` alternates between the two sources rather than sorting them
together, because both reliably produce more than six and whatever breaks the tie
decides the whole round.

The model never sees the blanks. That is measured, not assumed: shown them, it
spends every question restating them and asks nothing about what the values mean.
`semantic.shown()` is the one place that decision lives, and a test holds the
prompt and the payload to each other so a block cannot be handed over without
being explained.

## Why `resolved` means something

Two loops, and they come apart constantly.

```
comment loop    open ──owner answers──▶ answered
                     ──not applicable─▶ rejected

revision loop   answered ──the walk now ends elsewhere──▶ resolved
                         ──it still fails──────────────▶ stays answered

pressing        answered ──the answer settled nothing──▶ open
```

`answered` means the knowledge exists. `resolved` means the drawing shows it, and
the owner does not get to declare that. Step 2 of the round re-runs the walk stored
on the comment's evidence and compares. If the situation ends somewhere else now,
the drawing changed in the way the answer required. If it ends the same way,
nothing was fixed and saying so does not make it so.

A question about what a value *means* has no walk behind it, so what proves it is
that the answer produced a statement at all. "Yeah, it depends" distils to nothing
and stays open, which is the right outcome.

`rejected` is not a delete. It compiles into the spec as negative knowledge, so a
later round does not re-ask and codegen knows the case was considered.

## Curiosity is bounded, not open

A model asked "what is missing here" under-extracts badly, so it is never asked
that. It gets bounded spaces and instantiates each against this board: timing,
cardinality, quality, authority for situations; how many, same or not, acceptable,
which one, why is this here for values.

Two tools, and neither is a convenience. The whole board is already in the payload,
so anything the model could assemble itself would be a shortcut it might skip,
which makes any question depending on it unreliable. `dry_run` walks a situation,
so a claim about where something ends up is one the model *obtained* rather than
asserted. `where_values_meet` collects every point two values have to be
recognised as the same thing, which sounds derivable and is not: the join points
live in five unrelated config shapes and nothing else puts them in one list.

Nothing the model returns is trusted about the board. An anchor that resolves to
no card drops the question. An identity it invented is discarded. A walk it cites
that nobody took is not recorded. The wording is the model's, the assertion is not.

## Documents are evidence, not answers

`extract.py` gets text off whatever the customer has. Markdown is read off disk.
A PDF or a photograph of a laminated sheet goes to the model, which reads the
page. No OCR library: the model that reads the procedure can read the scan, and
that keeps one seam to OpenAI instead of a second parsing stack.

`reference.py` then aligns each thing the document says onto the one element of the
board it bears on, using the same anchor vocabulary and the same validation as a
settled statement. That alignment is the whole mechanism. Sitting next to what the
card actually says, a claim either agrees, disagrees, or covers something the
drawing is silent about, and the last two are questions.

A `DocumentClaim` is deliberately not an `Assertion`. A document says what somebody
wrote down once. That is evidence about the process, not a decision about it, and
nobody has confirmed it is still true. Only a settled conversation crosses the
freeze, so a claim can start a conversation and can never be the answer to one.
