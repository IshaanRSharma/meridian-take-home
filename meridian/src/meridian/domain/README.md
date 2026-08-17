# domain

The vocabulary. Every other package agrees on these types, and this one imports
nothing internal at all. If you are new here, read this directory first, because
the rest of the codebase is operations on these objects.

Nothing in here does anything. There is no I/O, no model call, no SQL. It states
facts about a board and refuses to have an opinion about whether a board is
*ready*, because "ready" means something different to the compiler, the reviewer
and the healing loop.

## Files

| file | what it holds |
|---|---|
| `primitives.py` | the four card configs, and `findings()` on each |
| `graph.py` | `Board`, the node and edge types, traversal, `dry_run` |
| `review.py` | `Anchor`, `Thread`, `Assertion`, `Scenario`, `ReferenceDoc`, `DocumentClaim` |
| `frozen.py` | `FrozenSpec`, `SpecPrimitive`, `ScopedContext` |
| `keys.py` | turning a name a person typed into a slug |
| `errors.py` | the four error classes the API maps to status codes |

## Two decisions that shape everything downstream

**`findings()` instead of Pydantic validators.** A process owner drops a card on
the canvas, names it, and walks away to lunch. That card has to be saveable. If
required fields were validators, a half-filled card could not be stored, and the
canvas would become modal: fix this before you may continue. So completeness is
not a constraint on storage. It is a gate applied at freeze time, and `findings()`
is what reports it. Each finding carries a severity and a field path, the same
shape as an LSP diagnostic.

**Severity is required, never defaulted.** `_finding()` takes it positionally on
purpose. A default would quietly make every field added later into a freeze gate
that nobody chose. `blocking` has to keep meaning "no correct agent can be
generated without this", so `grep '"blocking"'` is the complete list of gates in
the system.

## Four cards, and why not five

Event, Action and Check are steps. They are connected by edges and together they
are the state machine. Entity is a thing: steps read it or produce it, and it is
never traversed. Everything in `graph.py` that walks the graph walks steps only.

There is no Wait primitive. An Event with a deadline is a wait. There is no
Document primitive either, because in four of the five processes we modelled the
data comes back from a lookup rather than arriving as a file, so `document` would
be a lie in the generated code. The palette still says "document" where that is
the honest word for a customer. The type name and the UI string are different
artifacts and do not have to agree.

## The graph is not a DAG

A `repeat` edge sends execution back to work that already ran, because a
corrected certificate arriving on Thursday has to re-run a check that passed on
Tuesday. So traversal terminates by bounding iterations (`MAX_DRY_RUN_STEPS`)
rather than by assuming acyclicity, and `dry_run` answers a check per visit rather
than once, which is how a resubmission is expressible at all.
