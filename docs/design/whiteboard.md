# The whiteboard — choices, assumptions, behaviour

A short tracker for one part of the system: the canvas a process owner draws on,
and the cards they draw with. `Claude.md` is the full design record and
`SCOPE.md` says what ships. This file is neither. It says what we decided, what
we are taking on faith, and what the thing does — enough that a teammate can
argue with the decisions or verify the behaviour without reading code.

"Process owner" throughout means the non-technical person whose job the process
is: the one who knows what actually happens, and who will never open a JSON
file.

## 1. Design choices

Each row is one decision and the one reason it exists.

| Decision | Why |
|---|---|
| Four cards: **Event**, **Action**, **Check**, **Entity**. | They answer the four questions a process owner can answer without training: what happened, what gets done, what has to be determined, what are you looking at. |
| Event, Action and Check are **steps** — connected by arrows, and together they are the flow. Entity is a **thing** — referenced by steps, never walked through. | An invoice is not a step in a process; it is what a step reads. Mixing the two makes "what runs next" ambiguous. |
| **Entity is not drawn on the canvas.** It is a row in the same table as the other cards but has no position on screen; it is attached to steps and shown as text on them. | Before it was a card, a Check listed inputs that appeared nowhere on the board, so a process owner could not see what a Check read. As a separate side panel it would have been a second interaction to learn. |
| The board is a **state machine, not a DAG** — a `repeat` arrow can send the process back to work that already ran. | A corrected certificate arriving on Thursday has to re-run a check that passed on Tuesday. A strictly one-directional graph cannot say that, and this process does it every week. |
| A card is **placeable with a name and nothing else**. | Someone drops a card mid-thought and walks away. Anything stricter turns "I'll come back to this" into an error dialog. |
| Every other field arrives one of three ways: **derived** (the system works it out), **clicked** (picked from what is already on the board), or **asked** (the AI review asks for it). | Nothing on a card is a blank box a non-technical person has to guess how to fill. |
| Completeness lives in **`findings()` methods, never in validators**. | A validator would refuse to save a half-filled card. Completeness is a gate applied when the board is submitted, not a condition of storing it. |
| A finding carries a **field, a reason and a severity** — it is an object, not a string. | The list of gaps is read by the freeze gate, the reviewer and the card UI. If it were prose, each of them would have to re-derive severity and location by parsing English. |
| `Finding.reason` is written **in the process owner's language** ("Nobody is named as receiving this"), not the field's name. | The reason string is literally the text shown on the card. It is UI copy that happens to live in the domain model. |
| A Check's tests use **four operators only**: `present`, `compare`, `each_has_matching`, `custom`. | An LLM drafting a check from a description has to classify into them, and every extra bucket is another way to be wrong. A date test is a comparison whose values are dates; a pattern test is a comparison with a `matches` operator — what really differs is the right-hand side, so that is typed instead. |
| `custom` carries the rule as **prose plus the fields it reads**. | The escape hatch stays lintable: code generation implements the sentence, and reference checking still knows which fields are involved. |
| `cardinality: one_per(field)` — "one certificate per batch listed on the invoice". | That is the process owner's own phrasing, and it is what makes the expected count **derivable** instead of configured. Nobody types "expect 5". |
| **No tool name ever reaches the canvas.** The process owner writes what a step does (`effect`), how it reaches someone (`channel`), and which system in their own words. Which integration that becomes is worked out when the spec is frozen. | Picking a tool is an implementation decision, and it would make the whiteboard a technical surface. |
| **Natural language fills descriptive fields; pickers own references.** Prose sets things like `effect`, `channel`, how an Event is recognised, and cardinality. Pickers set anything naming a field path or an outcome. | Mapping "all four codes" onto four exact field paths is the most error-prone thing extraction could do, and getting it wrong produces a check that tests the wrong thing while looking correct. |
| An Event is **never an ending**. Only an Action can be marked as one. | An Event is something arriving; after it arrives, something still has to happen. The flag does not exist on the other cards, so it cannot be set by mistake. |
| Dragging a card around writes to the board's layout, not to the card. | The card table then only changes when the *process* changes, which keeps its history meaningful. |

## 2. Assumptions

Stated plainly so they can be challenged. Each is a thing we believe rather than
a thing we verified.

**One container is one shipment, and that is the unit of work.** Everything is
filed under a container number: one run of the process per container, however
many invoices and certificates arrive for it. If a customer ever treats a
purchase order or a vessel as the unit, the correlation key is wrong and the
whole board is filed under the wrong thing.

**The process owner can describe their process in plain sentences, but will not
fill in forms.** This is the load-bearing assumption behind the whole authoring
model — describe-then-confirm, never a blank form. If it is false, the canvas is
still usable but slower than an interview would have been.

**The written procedure is incomplete by nature.** Nobody writes down what they
do automatically. We assume the gaps in a customer's SOP are the interesting
part rather than an oversight, which is why the review loop exists at all.

**No document upload in this version.** A process owner describes an entity;
they cannot drop a sample PDF and have its fields read off. Runtime is
unaffected — the generated agent still reads real PDFs, because that is what the
evaluation set is. The cost is that a claim like "batch numbers are seven
digits" cannot be checked against a real document until later in the review.

**The seeded example board is what a real process owner would have drawn from
the SOP** — gaps included, not gaps invented to make a demo. The board deliberately
models only what the SOP states. Two checks that appear in historical output but
nowhere in the SOP are left off entirely, so that the review has something real
to find rather than something pre-answered.

**Field names inferred from a description are close enough.** When someone says
"HTS", we assume `hts_number` is a good enough name for the thing that reads the
document at runtime to find it, because that reader is semantic rather than a
literal key lookup. A wrong guess here is recoverable — the name is visible on
the card and can be corrected — but it is a guess.

**One asymmetry in the seed board is preserved rather than smoothed over.** One
problem is logged and the other is emailed, because the SOP genuinely says "log
an error" for invoices and "reported via email" for certificates. Whether that
difference is real is a question for the process owner, not something to quietly
resolve.

## 3. Intended behaviour

Described so it can be checked from the outside.

### Dropping a card and leaving it

Drop a card, name it, walk away. It saves. Nothing is refused, nothing is lost,
and no dialog appears. The card immediately shows a short list of what it still
needs, in plain sentences — "Nothing says what this step does", "Nobody is named
as receiving this". Come back later, fill one in, and that line disappears. The
board can sit in this state indefinitely; it just cannot be submitted.

### What the board reports

Asking a board for its findings returns every gap on it: the ones each card can
see about itself, plus the ones only the whole board can see — an arrow pointing
at a card that is not there, an arrow pointing at an Entity (which is not a step,
so nothing can transition to it), a step nothing leads to, a step the process
stops at that is not marked as an ending, a Check outcome with no arrow leaving
it, and a field reference to something the named Entity does not have.

Every finding names the element it belongs to, the field, a sentence of reason,
and one of three severities:

| Severity | Means | Effect on submitting |
|---|---|---|
| **blocking** | Something a generated agent could not be built without. Nobody could implement this step from what is on the card. | Blocks submission. |
| **important** | Worth asking about; the process will probably behave wrongly at an edge without it. | Does not block. |
| **minor** | Advisory. Nice to have, no consequence. | Does not block. |

Ranking is what keeps the list readable: a board with a dozen findings still has
a short blocking list, and that short list is the work.

### Dry run

A dry run walks one imagined scenario over the board — you say how each Check
comes out, and it reports the path taken, plus every step the scenario never
reached. That last part is the point: "if the invoice fails, the certificate
check never runs" becomes visible without anyone having predicted it.

It is deliberately dumb. It says whether a path exists, not whether the path is
right. Four possible results:

| Result | Means |
|---|---|
| `reached_terminal` | The scenario ended at a step marked as an ending. The path is complete. |
| `dead_end` | The scenario stopped somewhere that is not marked as an ending. Either the drawing is missing an arrow or that step should be marked as an end. |
| `undefined_branch` | A Check came out a way no arrow carries. The most common real gap on a board. |
| `loop` | The walk hit its step limit without ending. Expected on a board with `repeat` arrows and a scenario that keeps failing; a bound exists so traversal terminates rather than because loops are wrong. |

An outcome that no arrow names is reported as an undefined branch **even when an
unconditional arrow exists**, so an unwired outcome cannot silently fall through
and stay hidden.

### What the seeded example board reports today

The seed board has six steps and two entities. Neither entity has a position on
screen — that absence is the whole implementation of "first-class but not
drawn".

It reports exactly three **blocking** findings:

1. **`report_coa_discrepancy` — no recipients.** The step emails a certificate
   discrepancy, but the SOP never says who receives it. A step that notifies
   nobody cannot be built.
2. **`report_invoice_discrepancy` — no system.** The SOP says "log an error" and
   does not say where. A step that records something needs somewhere to record
   it.
3. **`coas_valid` — an outcome with nowhere to go.** The check declares three
   ways it can come out (`pass`, `missing_coa`, `mismatched_coa`) and only two
   have arrows. The SOP names the third outcome but not the path.

It also reports **important** findings that do not block, including: neither
check says what to do when a document has not arrived yet; neither reporting
step has anything stopping it firing twice when a shipment resumes; and both
reporting steps are places the process stops without being marked as endings.

Two more observable facts about the seed board:

- Asking `coas_valid` where its outcomes go returns exactly one unwired:
  `mismatched_coa`.
- Running the scenario "the invoice check comes out `missing_information`" gives
  `dead_end`. That is the finding the historical data later confirms is wrong —
  a real shipment failed its invoice check and still had all five certificates
  counted, so a failed invoice cannot really mean the certificate check never
  runs.

## 4. Open questions

Things the sources leave genuinely unsettled, rather than things this document
skipped.

- **What exactly gates Submit.** One place lists the freeze gate as "no open
  threads, lint clean"; another says Submit is "gated on three conditions"
  without naming them. The code-level rule — no blocking findings — is clear;
  the full gate is not.
- **Two checks in the historical output that are in no SOP.** Left off the seed
  board on purpose, to be found by review rather than pre-answered. Whether one
  of them should block a shipment at all is unresolved: a real shipment shows
  fourteen mismatches and a resolved status.
- **When a shipment stops waiting.** A shipment is complete when every batch has
  a matching certificate, but nothing says whether more invoices are still
  coming, and there is no outer time limit on a certificate that never arrives.
- **Stale field names in the long design record.** `Claude.md` §6 still lists
  required fields on Event and Action that were later cut, and refers to the
  completeness method by its old name. The code is the current answer; that
  section is not.
