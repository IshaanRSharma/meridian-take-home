# Meridian

A pipeline that turns tacit process knowledge into a running, self-repairing agent.

```
whiteboard  →  AI review loop  →  frozen spec  →  codegen  →  Temporal  →  self-heal
 (mutable)      (human oracle)     (immutable)     (agent)    (durable)   (eval oracle)
```

Two loops, split by the freeze. Before it, ground truth lives in a person's head,
so the loop asks a human. After it, ground truth lives in the eval suite, so the
loop asks a test suite. **The freeze is where authority transfers from a person
to a test suite.**

The running example is validating inbound pre-alert documentation for a pharma
distributor: Commercial Invoices and Certificates of Analysis arrive by email
before a container lands, and the agent checks completeness and consistency
across a shipment.

The two human-in-the-loop steps — spec to agent, and agent to better agent —
are run by a person with a coding agent, using checked-in skills:

```bash
codex                                     # or claude
> /spec-to-agent                          # reads spec.lock.json, writes agents/<slug>/
$ mvp eval sweep --build 1
$ mvp bundle --build 1 | pbcopy           # the copy-ready failure bundle
> /repair-agent                           # paste, fix one file
$ mvp build register --from-git
```

The brief says not to force every step to be autonomous, and this is where that
lands: the platform's job is to freeze a spec good enough to build from, and to
make a failure legible enough that one paste fixes it. Both skills live in
[`.claude/skills/`](./.claude/skills/) and are symlinked to `.codex/skills/` —
the format is portable across both tools.

**[`Claude.md`](./Claude.md) is the design document.** Every schema and structure
decision lands there with its reason. Start at §2 for the stack, §3 for the repo
map, §5 for the schema, §7 for why things are the way they are.

**[`SCOPE.md`](./SCOPE.md) is what actually ships**, in what order, and why
everything else was cut. Read it second — it is the shorter of the two and it
explains the shape of the repo.

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

- **Upload an SOP and draft the whole board.** Same call shape as the text path
  plus PDF→text, so it is nearly free once that exists. Deferred so the demo's
  seed board stays hand-verified and deterministic rather than varying per run.
- **Drop a sample document on the canvas to propose an entity's fields.**
  `mvp fixtures pull` does this from the real inbox today.

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

## Status

Built in units, each gated on a contract and a test list before implementation.
Budget and cut list in [`SCOPE.md`](./SCOPE.md).

- [x] repo skeleton, tooling, local services
- [ ] **0** Composio OAuth, inbox snapshot to `fixtures/emails/`
- [x] **1** `domain/` — primitives, graph, review, frozen
- [x] **2** migrations, `repositories/`, seed board
- [x] **3** `compiler/` — *checkpoint: a spec freezes*
- [x] **4** `cli.py`
- [ ] **5** `api/` skeleton + Railway — three services, deployed thin and early
- [x] **6** `reviewer/` — two rounds, real threads
- [x] **7** `runtime/` — the skeleton generated agents import
- [ ] **8** `spec-to-agent` skill — *checkpoint: an agent is generated*
- [ ] **9** eval cases from the snapshot
- [ ] **10** `healing/` — *checkpoint: the curve moves*
- [ ] **11** `events.py`, realtime, background jobs
- [ ] **12** `ui/` — canvas, comments, submit, spec viewer
- [ ] **13** Temporal and Composio on the real path
- [ ] **14** README, PDF, Loom, final deploy
