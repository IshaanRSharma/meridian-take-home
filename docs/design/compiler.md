# The compiler

What turns a board into findings, into a reviewer's context, and into a frozen
spec. Companion to [`whiteboard.md`](./whiteboard.md), which covers the cards
themselves.

Three consumers, three cadences, one traversal:

| Consumer | Wants | How often |
|---|---|---|
| the process owner, editing | findings, in their words | **every keystroke** |
| the AI reviewer | the board plus structural observations | once per round |
| codegen | the frozen spec | once per freeze |

---

## Design choices

### Facts belong to the board; judgement belongs here

A `Board` reports what is observable — three outcomes and two edges, this
reference does not resolve, nothing leads to that card. Whether any of that is a
*problem*, and how bad, is policy. The reviewer and the healing loop each have
their own policy and are entitled to disagree with the compiler's.

That line was drawn and then immediately crossed: `findings()` lived on `Board`
for about an hour before moving here. Worth recording, because it is an easy
mistake to repeat — the judgement feels like it belongs next to the data.

### Two kinds of problem, and they are not the same kind

| | says | can it be wrong? | who owns it |
|---|---|---|---|
| **lint** (`rules.py`) | something is **missing** | no — the field is simply absent | the process owner fills it |
| **decision** (`decisions.py`) | the drawing **chose** something | **yes** — the choice may not match reality | only the process owner knows |

A lint finding is provable. A decision is not: `coas_valid → report → stop` is a
perfectly legal drawing that asserts *a COA problem ends the process*, and that
assertion may be false. Nothing is missing; lint is silent.

This is why the two live in separate modules with separate output types, and why
they surface differently: a lint finding is a blank in the inspector, a decision
is a pin on the canvas.

### Findings speak the process owner's language

`reason` is the text rendered on the card. *"Nobody is named as receiving
this"*, not *"recipients is None"*. The completeness contract and the interface
copy are the same string, which means a new field cannot be added without
someone writing the question a warehouse supervisor will read. If it cannot be
phrased that way, the field is probably wrong.

### A finding says where its fix lives

`kind: "field" | "structure"`. Half the rules report a `field` that is not a
config attribute at all — `outgoing`, `incoming`, `name` — because the problem is
the drawing. Without the flag the interface would have to guess by checking
whether the name happens to exist on the config.

### Rules overlap, and merge at the worst severity

One problem is reported once, at the severity that actually gates the freeze.
This is what lets a card report something as merely important while the board
raises it to blocking — a card cannot know how an entity gets here, and the
board can.

### Decisions are enumerated, not pattern-matched

Six sweeps over structural properties rather than six named patterns:

```
sequence      A precedes B          ── could they be independent?
termination   the process ends      ── or does it resume?
convergence   two paths, one target ── does the difference matter?
boundedness   an unbounded wait     ── forever, really?
entry         the ways in           ── is that all of them?
coverage      an ending reachable   ── should that step be required?
              while skipping a step
```

Each asks the same question of a different feature: **could this plausibly have
been drawn otherwise?** A pattern nobody anticipated tends to fall out of a sweep
that already exists rather than needing new code.

### Every claim is derived; the model only chooses and phrases

A decision carries a `claim` computed from the graph — *"the COA check only runs
when the invoice check passes"* — and an `alternative`. The reviewer picks which
are worth raising and rewrites them in the owner's words, but it never authors
the claim, so it cannot assert a pattern the board does not contain.

### Decision keys are stable

`kind` plus sorted element anchors. The same board yields the same identity every
run, so a question asked in round one is never asked again in round three — and
a *rejected* question does not come back either. Dedup is an exact match rather
than a guess at whether two sentences mean the same thing.

### The anchor routes a statement, then disappears

Threads are stored conversation-shaped: one question ranging over several cards.
The spec is primitive-shaped: one card carrying everything settled about it. The
anchor performs that transposition and is then discarded — **nothing in a frozen
spec references an anchor.** Codegen reads four lists of English and a config.

```
board                    reaches every card
group:validation         reaches its members
primitive:coas_valid     reaches that card only
entity_field:invoice.x   reaches cards that read that entity — sideways, not down
edge:e5                  reaches no card (see open questions)
```

### Statements are inlined, not referenced

A board-level rule physically repeats on every card that inherits it. A
reference table would be smaller — but the consumer is a code generator reading
*one card's entry* to write *one file*, so it must never join anything.
Duplication in the payload buying simplicity at the consumer.

### An edge keeps its own statements

*"Wait 48 hours, then escalate"* is a claim about a **transition**, so it reaches
no card — putting it inside a step's file would tell that step about something it
does not do. `FrozenSpec.edge_context` is where it lives instead. Without it the
whole class of answer about timing between steps would be settled in review and
then silently dropped at the freeze.

### Capabilities derive from closed enums, never from free text

```
event.channel=email        → email.fetch
event.captures non-empty   → storage.put · doc.extract
notify + email             → email.send
record                     → system.write
lookup                     → system.read
decide                     → human.decide
check · noop               → none
```

`system` is per-customer free text — *"Aurologistics WMS"* — so it stays on the
config for the bindings file to key on and never becomes part of a capability
name. And **a capability list is what decides whether a card becomes a Temporal
activity**: the determinism rule as data rather than a convention codegen has to
remember.

### A finding is a fact, not a prompt

`findings()` produces facts about a board. Nothing here decides that a human
should be interrupted — three consumers read the same list and only one of them
ever speaks to the process owner.

| consumer | uses them for | felt as |
|---|---|---|
| the reviewer | one of four generators; it picks, ranks and phrases | 3–6 questions per round |
| the freeze gate | blocking → 422 | one refusal, at Submit |
| the canvas | a dashed card, a footer count | ambient |

Nothing blocks while someone is still drawing. That is why the completeness
contract is `findings()` and not Pydantic validators — a half-filled card must
stay saveable, because the owner is mid-thought.

### The funnel: what is allowed to reach a person

The board is authored from a natural-language description, so **every extraction
miss would otherwise become a question for a human** — being asked about
something you already said is what makes someone stop answering. So a finding
passes through four stages, each a *filter* rather than a label. Nothing is
tagged in advance; everything is attempted in order, and only what survives
moves down.

```
prose ──fill──▶ board.json
                    │
      ① auto-fix ───┤  derivable from the board alone. Nobody is asked.
                    │
      ② re-read ────┤  the same prose, one finding at a time, asked specifically
                    │
      ③ structure ──┤  BLOCKING. The drawing is not yet a workable process, and a
                    │  person fixes it on the canvas before review begins.
                    │
      ④ review ─────┘  every missing value, every decision. CAP 6 per round.
```

**`blocking` is reserved for a drawing that does not work as a process** — a
reference that does not resolve, an outcome with no line out of it, a step the
process stops at without saying so. Those are provable, they are visible on the
canvas, and nobody needs to know the business to close one.

**A missing value is never blocking**, however badly it is needed. Who receives a
report, which system a log goes to, what to do while a document has not arrived —
only the person who runs the process can answer, so it is the reviewer's question
and never a refusal handed to someone who came to draw a diagram. Enforced rather
than intended: no finding a *card* reports about itself may be blocking, and a
test over every config asserts it.

Two gates fall out of that, with different owners:

```
enter review   no blocking findings              the drawing works as a process
freeze         no blocking findings              …and every question is settled
               + no unsettled threads
```

On the seed board: **3 blocking** — `mismatched_coa` leads nowhere, and both
report steps stop without being marked as endings. Then **6 value findings and 7
decisions** go to the review, including the two real SOP silences. The refusal is
ten seconds of canvas work; the interesting part is the conversation after it.

Two constraints on ②, and they matter as much as the stage itself. Fill only
what the prose **states**, never what it implies — a model that infers
`recipients` from a SOP naming nobody has fabricated a business decision and
hidden the one question worth asking. And **when uncertain, promote rather than
resolve**: the SOP's *"report any missing **or mismatched** COA"* looks like it
wires both outcomes to one action, but chasing a typo'd batch and escalating an
absent document may warrant different handling — two competent people could
disagree, which makes it the owner's call.

A value finding becomes a thread like any other question (`origin = 'lint'`), so
a fact settled in one exchange still has a conversation behind it and still
appears in provenance. Both loops run, and for a lint thread the revision loop's
proof is free: re-running the check *is* re-running lint, so the finding either
disappears or it does not.

### Nothing derived may borrow one customer's vocabulary

A sweep that says *"the shipment comes back once the problem is fixed"* on a
credentialing board is a pattern matcher wearing a sweep's clothes. Every noun in
a claim comes from a card name the owner typed, and the closed enums in
`primitives.py` are domain-free for the same reason — `Scope` reaches the frozen
spec, so `per_shipment` would have put pharma logistics in the type system.

Both are held by tests rather than by care: one scans every claim on a board that
has no shipments, the other scans every `Literal` in the domain.

### Tool resolution is deliberately not a rule

A process owner cannot fix an unbound channel. Surfacing it on the canvas would
be noise they can do nothing about. Two gates, two owners: `spec freeze` asks
*is the process fully specified?*, and a separate check before codegen asks
*can we actually build it?*

---

## Assumptions

- **Boards are small.** Traversal is O(V·E) with linear key lookups. Fine at
  eight cards; at two hundred, `GET /boards/{id}/lint` runs on every keystroke
  and would need indexes. `Board` is immutable, so caching them is safe when it
  matters.
- **Filtering by entity is close enough.** A constraint on `invoice.batch_no`
  reaches every card reading that invoice, including ones that never touch batch
  numbers. Over-filtering is worse than under-filtering: extra context costs
  tokens, missing context is a bug.
- **Contradictions are a reader's problem.** Ordering conveys precedence; nothing
  detects that two English statements disagree.
- **A finished board still makes decisions.** Lint goes silent when complete;
  decisions do not. If a sound board produced none, the sweeps would be lint
  wearing a different hat.

---

## Intended behaviour

**Editing a card.** Findings recompute on every change. Blocking ones stop a
freeze; important and minor do not. A `field` finding is a blank in the
inspector; a `structure` finding is something to draw.

**The seeded board reports exactly three blocking findings**, and each traces to
a sentence the SOP never wrote:

```
report_coa_discrepancy  recipients   the SOP names nobody
report_invoice_...      system       "log an error" — where?
coas_valid              outcomes     mismatched_coa leads nowhere
```

**And it makes seven decisions**: one sequence, three terminations, one
boundedness, one entry, one coverage. Seven against a cap of six per round, so
ranking matters from the first round.

**The sequence decision is the one to watch.** Nobody wrote a rule about invoices
and COAs; it falls out of asking the same question of every conditional edge
between two checks:

> *the COA check only runs when the invoice check passes*
> — could both run, and everything wrong be reported together?

The corpus later settles it: `CAAU4056270` failed its invoice check and still
counted 5 of 5 COAs.

**Answering a termination question makes the board cyclic.** The reviewer asks
whether a COA problem really ends the process. The owner says it comes back once
corrected. That adds a `repeat` edge, and the graph stops being acyclic — a
DAG-based tool could not represent the answer its own reviewer just elicited.
The new edge then raises its own question: *this can go round any number of
times.*

---

## Open questions

**A check whose result changes nothing is not surfaced.** A Check with an
unconditional outgoing edge records counts and always continues, which is legal
and is exactly what `CAAU4056270` does. It is worth questioning — *is that
deliberate?* — but no sweep asks it, and inventing one now would be speculation
rather than an observed miss.

**Seven decisions against a cap of six.** Ranking is the reviewer's job, and
nothing here orders them by value. The obvious signals — corpus evidence, prior
rejection rates — arrive with the reviewer.

**Two rules could produce the same `(anchor, field)` at the same severity.** The
merge keeps whichever ran first. `RULES` is a fixed tuple so it is deterministic,
but it is order-dependent, which is a fragile kind of deterministic.
