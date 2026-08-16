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
make db          # Postgres 15 on :54329, for integration tests
make temporal    # Temporal dev server, UI on :8233
```

Copy `.env.example` to `.env` before running anything that talks to a provider.

## Layout

| Path | What it is |
|---|---|
| `meridian/` | the Python backend — one package, two entrypoints (`api`, `worker`) |
| `ui/` | the React frontend, HTTP-only against `meridian/` |
| `agents/` | **generated** code, committed; the repair loop edits here |
| `db/` | plain SQL migrations and seeds |
| `fixtures/` | recorded emails, documents and expected outputs |
| `bindings/` | per-customer YAML; no secrets |
| `docs/` | the brief, the SOP and the deck — **local only**, gitignored |

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

**Verification**

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
- [ ] **2** migrations, `repositories/`, seed board
- [ ] **3** `compiler/` — *checkpoint: a spec freezes*
- [ ] **4** `cli.py`
- [ ] **5** `api/` skeleton + Railway — three services, deployed thin and early
- [ ] **6** `runtime/` — the skeleton generated agents import
- [ ] **7** `codegen/` — *checkpoint: an agent is generated*
- [ ] **8** eval cases from the snapshot
- [ ] **9** `healing/` — *checkpoint: the curve moves*
- [ ] **10** `reviewer/` — two rounds, real threads
- [ ] **11** `events.py`, realtime, background jobs
- [ ] **12** `ui/` — canvas, comments, submit, spec viewer
- [ ] **13** Temporal and Composio on the real path
- [ ] **14** README, PDF, Loom, final deploy
