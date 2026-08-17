# compiler

Board to frozen spec. This is where the system decides what "incomplete" means,
and where authority stops being a person's and starts being a test suite's.

Pure functions over a `Board`. No connection, no model call, no I/O. `freeze()`
takes the board and the statements and hands back a sealed spec, so the whole gate
reads in one place and persistence is left with one job.

| file | what it does |
|---|---|
| `rules.py` | 21 rules. What is missing, and how badly. |
| `decisions.py` | six sweeps. What the drawing *decided*, which might be wrong. |
| `context.py` | the transposition: conversation-shaped knowledge to card-shaped. |
| `serialize.py` | two serialisations of one board, for two very different readers. |
| `freeze.py` | the gate, about 70 lines, and what it refuses. |

## rules.py versus decisions.py

This split is the most useful thing to understand here.

A **rule** says something is missing, and it cannot be wrong. `recipients` is
empty. This edge points at a card that is not on the board. Three outcomes and two
lines. Provable, and the process owner can close it.

A **decision** says the drawing made a choice, and the choice might be wrong.
Nothing is absent, nothing contradicts, lint is silent. What a sweep surfaces is a
decision nobody examined: this check only runs when that one passes, this failure
path never comes back, the process waits for this indefinitely. These go to the
reviewer, never to a gate, because being asked to justify a choice is not the same
as being told to fill in a blank.

Every claim a sweep makes is derived from the graph, never written by a model. The
reviewer chooses which decisions are worth raising and phrases them in the owner's
words, but it cannot assert a pattern the board does not contain, because it never
authors the claim.

## Severity, and what actually gates

Three levels, and only one of them stops anything.

`blocking` means the drawing does not work as a process. A reference that does not
resolve, an outcome with no line, a step it stops at without saying so. Provable,
visible on the canvas, closable by anyone. This refuses at both gates.

`important` and `minor` become review questions. They never refuse.

A missing value is never blocking. Who receives a report is knowledge only the
process owner has, so it is a question, not a refusal handed to somebody who came
to draw a diagram. There is a test over every config that enforces this, so it is
a property rather than an intention.

`findings()` merges on `(anchor, field)` and keeps the worst severity, so when two
rules find the same problem the owner sees one blank rather than two.

## context.py, the part that is easy to miss

Storage is thread to many anchors. The spec is card to many statements. This file
inverts it, and then the anchor is thrown away: nothing in a frozen spec
references an anchor, because a code generator should read four lists of English
and a config, not resolve references.

`reaches()` decides which cards a statement lands on. A `board` statement reaches
everything. A `primitive` statement reaches one card. An `entity_field` statement
travels *sideways* to every card that reads that entity, which is how a rule about
invoice batch numbers lands on the checks that test them and nowhere else.

Statements are inlined, not referenced. A board-level rule physically repeats on
every card that inherits it. The payload gets bigger and the consumer joins
nothing, which is the right trade when the consumer is writing one file at a time.

## freeze.py refuses three ways

Blocking findings. Unsettled threads. And a spec whose content is identical to the
previous version, because re-submitting an unchanged board would mint v2 with the
same content and "which spec is this build against" would stop being a useful
question.

`assertions` and `threads` have no default values on purpose. When they defaulted
to empty, both gates passed by simply not being handed their input: a board with
six unanswered questions sealed cleanly as v1, every scoped context empty, and
nothing noticed because the checksum covers the content that is there. A caller
with nothing to pass has to say so.
