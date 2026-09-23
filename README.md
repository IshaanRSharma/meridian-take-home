# I completed this project as part of an interview process with Meridian. The assignment involved building a substantial version of their product/MVP within a 72-hour window.

# No NDA was signed as part of the interview process. This repository contains my implementation of the take-home assignment and is being open sourced as an example of the architecture and approach I built during that period.

# During the interview process, I was told that ideas and approaches surfaced through candidate take-homes could influence Meridian's own product development. I was also told that my LLM/token costs incurred while completing the assignment would be reimbursed.

# After submitting and interviewing, I did not receive further communication or reimbursement.

# I'm documenting the experience here so prospective candidates can make their own assessment of the time commitment involved in the interview process and decide whether they are comfortable participating.

# The Project

# If you're interested in building a generic SOP → semantic graphical representation of a workflow → code-generation agent that executes those semantics using Temporal → local self-healing Gymnasium/evaluation loop → deployment pipeline, take a look through the repository.

self-healing agents


```bash
codex                                     # or claude
> /spec-to-agent                          # reads spec.lock.json, writes agents/<slug>/
$ meridian eval sweep <board>
$ meridian bundle <board> | pbcopy             # the copy-ready failure bundle
> /repair-agent                           # paste, fix one file
$ meridian build register --from-git
```

The brief says not to force every step to be autonomous, and this is where that
lands: the platform's job is to freeze a spec good enough to build from, and to
make a failure legible enough that one paste fixes it. Both skills live in
[`.claude/skills/`](./.claude/skills/) and are symlinked to `.codex/skills/` —
the format is portable across both tools.

**[`Claude.md`](./Claude.md) is the design document.** Every schema and structure
decision lands there with its reason. Start at §2 for the stack, §3 for the repo
map, §5 for the schema, §7 for why things are the way they are.

**[`RUNNING.md`](./RUNNING.md) is how to actually run it**, in four paths from
"just the tests" to "generate an agent and repair it", with the gotchas.

## Quickstart

```bash
make install     # uv venv + dev tools
make check       # lint, mypy --strict, pytest
```

Local services are optional and not needed by the test suite:

```bash
make migrate     # apply db/migrations to DATABASE_URL
make seed        # load the pre-alert board
make db          # Postgres 17 on :54329, migrated — what the test suite uses
make temporal    # Temporal dev server, UI on :8233
```

Each half owns its environment and they share nothing:

```
meridian/.env    DATABASE_URL · OPENAI_* · COMPOSIO_* · TEMPORAL_*
ui/.env          VITE_API_URL · VITE_SUPABASE_URL · VITE_SUPABASE_ANON_KEY
```

The backend never needs the Supabase URL or anon key — it talks to Postgres
directly, so the only thing that ever uses them is the browser's realtime
subscription. Copy each `.env.example` alongside its `.env.example` before
running anything that talks to a provider.

## Layout

| Path | What it is |
|---|---|
| `meridian/` | the Python backend — one package, two entrypoints (`api`, `worker`), its own `db/` and its own `.env` |
| `ui/` | the React frontend, HTTP-only against `meridian/`, its own `.env` |
| `meridian/src/meridian/runtime/` | the **skeleton** a generated agent imports — a scaffold, not a framework |
| `.claude/skills/` | `spec-to-agent` and `repair-agent`, the two human-run steps |
| `agents/` | **generated** code — local until a build is worth reviewing |
| `fixtures/` | recorded emails, documents and expected outputs |
| `bindings/` | per-customer YAML; no secrets |
| `docs/` | the brief, the SOP, the deck and our design notes — **local only** |

Dependencies are added by the unit that first needs them rather than declared up
front, so each commit's dependency diff says what the code actually started
using. The committed stack is `Claude.md` §2.

## Inside the backend

One package, nine directories. Each one has a `README.md` saying what is in it and
why it is shaped that way, so the answer to "where does this live" is one hop.

| directory | what it owns | reads |
|---|---|---|
| [`domain/`](./meridian/src/meridian/domain/) | the four cards, the board, the review types, the frozen spec | nothing internal |
| [`core/`](./meridian/src/meridian/core/) | env, the connection pool, the one seam to OpenAI | `domain` |
| [`repositories/`](./meridian/src/meridian/repositories/) | all the SQL, rows to domain types and back | `domain` |
| [`authoring/`](./meridian/src/meridian/authoring/) | how a card gets on the board, including from prose | `domain`, `repositories` |
| [`compiler/`](./meridian/src/meridian/compiler/) | what "incomplete" means, and the freeze | `domain` |
| [`reviewer/`](./meridian/src/meridian/reviewer/) | board to questions, answers to statements | `domain`, `compiler`, `repositories` |
| [`healing/`](./meridian/src/meridian/healing/) | sweep, localise, the pasteable bundle, the gate | `domain`, `repositories`, `runtime` |
| [`runtime/`](./meridian/src/meridian/runtime/) | the contract generated agents import | `domain` only |
| [`api/`](./meridian/src/meridian/api/) | a thin trigger layer over all of it | everything except `runtime` |

That last column is a test, not a comment. `tests/test_layering.py` walks the
imports and fails if anything points upward. The two rules worth knowing:

**`authoring` cannot import `compiler`.** If it could, refusing an edit because of a
lint finding would be one import away, and that would make the canvas modal and
delete the findings the review loop runs on.

**`runtime` may import `domain` and nothing else.** Generated agents import
`runtime`, so if it could reach the compiler then every deployed agent would be
carrying the authoring toolchain.

## End to end

What actually happens, in order, with the commands that do it. Everything below
runs headlessly. The UI is a viewer of a system that works without it.

**1. Draw something.** A card is placeable with a name and nothing else. Nothing
refuses, because a process owner who drops a card and goes to lunch has to be able
to come back to it.

```bash
meridian board new "Inbound pre-alert validation"
meridian card add <board> --type check --name "Does every batch have a matching COA?"
meridian edge add <board> invoice_complete coas_valid --on pass
meridian board lint <board>              # what is missing, worst first
```

**2. Attach a procedure, if one exists.** Most processes have no document, which is
why the whiteboard exists. When there is one it is the highest-value input, because
a disagreement between the drawing and the procedure is checkable rather than
speculative. Markdown is read off disk. A PDF or a photo of a printed sheet goes to
the model, which reads the page.

```bash
meridian board attach <board> docs/sop-inbound-pre-alert.pdf --kind sop
```

**3. Review.** The round refuses if the drawing is not a workable process yet, then
settles what was answered since last time, walks every declared outcome, reads any
attached procedure, and asks at most six things.

```bash
meridian review run <board>
meridian thread list <board>
meridian thread answer <thread> "The receiving supervisor gets it."
meridian thread reject <thread> "Expiry is not checked at this stage."
```

A rejected question is not deleted. It compiles into the spec as negative
knowledge, so a later round does not re-ask and the code generator knows the case
was considered.

**4. Answer, then edit the canvas.** These are two separate things and the system
insists on both. `answered` means the knowledge exists. `resolved` means the
drawing shows it, and the process owner does not get to declare that: the next
round re-runs the walk that raised the question and compares.

**5. Freeze.** Refuses on blocking findings and on any unsettled question. This is
where authority transfers from a person to a test suite.

```bash
meridian review settle <board>           # the freeze path runs this too
meridian spec freeze <board>
meridian spec check <board>              # what a spec still leaves to its implementer
meridian spec yield <board>              # how much of it the drawing could not have said
meridian spec export <board>             # writes agents/<slug>/spec.lock.json
```

`spec check` is the gate worth running before generating anything. It hands each
card to a model as its implementer and asks what it would have to decide for
itself. An empty answer is the one worth reaching.

**6. Generate.** A human step, run in a terminal with a coding agent. The skill
reads `spec.lock.json` and writes `agents/<slug>/`.

```bash
codex                                    # or claude
> /spec-to-agent
meridian build register <board> --from-git
```

**7. Run it.** The generated agent runs itself: `build.json` names an entry point,
and everything Temporal happens inside the agent. It boots a real Temporal server
in-process with a skipping clock, so a deadline measured in days resolves in
milliseconds.

```bash
meridian eval load <board> --from fixtures/expected/shipments.json
meridian eval sweep <board>              # latest build, or pass an iteration
```

**8. Repair.** One failing signature at a time. The bundle is the product: it has
to be fixable by somebody who has never seen the repo, from the block alone.

```bash
meridian bundle <board> | pbcopy         # largest failure bucket by default
> /repair-agent                          # paste, fix one file
meridian build register <board> --from-git
meridian eval sweep <board>              # a new point on the curve
```

The gate can only reject. A human is required to override it and never to approve
in its place.

## Authoring a card

A process owner never fills in a form. A card is placeable with a name and
nothing else, and every remaining field arrives one of three ways: **derived**
from what they already did, **clicked** from a closed set in their own words, or
**asked** — as a labelled blank on the card when the answer is a value, or as a
review comment when it needs judgment.

Three entry points, all landing on the same card:

| | |
|---|---|
| **Drag a card** | name it, one or two clicks. Always available. |
| **Describe it in text** | a paragraph becomes cards. The original sentence stays on the card as `instructions`, so extraction can only add, never subtract. |
| **Upload the SOP** | *not built — see below* |

The card is a **readback**, not an authoring surface. You cannot verify prose —
read a paragraph back to someone and they nod. Show them a card that says
*"notifies: nobody"* and they fix it. Structure is what makes an absence visible.

## With more time

Consciously cut, with the reason. `SCOPE.md` carries the full list.

**Authoring**

- **Draft the whole board from an SOP.** Attaching one already works
  (`meridian board attach`, and a PDF or a photo of a printed sheet goes to the
  model to be read), and the reviewer aligns what it says onto the cards it bears
  on. What is deferred is generating the cards from it. Held back so the demo's
  seed board stays hand-verified and deterministic rather than varying per run.
- **Drop a sample document to propose an entity's fields.** Nothing populates
  `sample_extracted` today, so a constraint a process owner states cannot be
  checked against a real value. That check is designed and unbuilt, and it is
  the highest-value thing on this list: it catches an answer being *wrong*
  rather than missing.

**Where the vocabulary strains** — all three are named limitations, none needed
for pre-alert:

- **Computation.** `margin = invoiced − contracted` has no home. Freight audit
  and quoting need derived values as addressable state. Roughly an hour of
  schema, but a `Compute` primitive fails the brief's own test — it cannot be
  explained to a non-engineer in one sentence.
- **Derived correlation keys.** A COA arriving alone carries a batch number, not
  a container. Resolving which shipment it belongs to needs a lookup against
  open instances, and `correlation_key: FieldRef` reads one value off one document.
- **Compensation.** Two writes that must succeed or fail together. Pre-alert's
  only write is an email, which cannot be undone — hence `idempotency_key`
  rather than rollback.
- **Things that happen in no particular order.** The vocabulary has branching and
  no parallelism: an edge carries an outcome or it is the one way on. Two
  unconditional edges out of one step is how somebody draws "these both happen",
  and nothing here can express it. This one is enforced rather than noted. The
  interpreter refuses to guess which branch to walk and a lint rule says so on
  the card, because the previous behaviour was worse than a refusal: the board
  linted clean, reported every step reachable, walked whichever branch was drawn
  first, and called a situation successful without ever running the check it was
  named for. POWL, which the design cites for soundness, has partial order as a
  first-class construct. We took the soundness argument and left that behind.

**Review**

- **The AI proposing a canvas patch.** The revision loop today is: the process
  owner answers, edits the canvas themselves, and the reviewer re-tests the
  originating scenario. The design has an extra step — the AI reads the answer
  and proposes the edit (*"add a correction Event and a recheck edge"*) for the
  owner to accept or amend. Roughly two hours: a structured call emitting board
  mutations, plus a diff view. Cut because the brief only requires that the
  owner *respond to comments and update the canvas*, and because a proposal a
  human has to check is worth less than a question they answer directly.

**Verification**

- **Immediate resolve feedback.** Today a process owner can resolve any thread
  freely and the next review round reopens it if the gap is still there. Telling
  them at the moment they click — *"this check still has no path for
  `mismatched_coa`"* — is friendlier, and cheap now that `dry_run` exists. It was
  cut because blocking the click is worse than reopening later, and because a
  judgement thread has nothing to re-run at all.
- **Ablation suite.** Delete a known fact from the finished board, check the
  reviewer asks for it. Turns review quality from a claim into recall as a number.
- **Conformance.** Catches a repair that makes evals pass by deleting a validation.
- **Coherence pass.** Tautology, absorption and contradiction over the ruleset —
  findings that are *provable*, which is what makes them safe to state confidently.

**At scale**

- **An agentic reviewer** with graph-traversal tools. At eleven primitives the
  whole board fits in context and tools solve a discovery problem that does not
  exist; past roughly forty it would.
- **Probe hit rates by `(primitive_type, category)`** across deployments, so
  ranking becomes empirical with zero customer data moving between them.
