# Scope

`Claude.md` is the design. This is the subset that ships, in what order, and the
reasoning for everything left out.

The brief allows **~48 hours of elapsed build time** and grades *"what you chose
to build well versus what you consciously cut."* A cut list is a deliverable, not
an apology.

**On AI tooling.** The brief expects heavy Codex / Claude Code use, and that is
assumed throughout. It compresses unevenly: writing typed Python against a
settled design compresses a great deal; OAuth setup, integration debugging,
authoring ground truth and recording a demo compress by nothing. The budget below
reflects that asymmetry rather than a flat multiplier.

The rule applied throughout: **build what the loop needs to be provable end to
end, then buy depth with whatever hours remain.**

---

## 1. Requirement coverage

Every requirement in the brief, and where it lives. Nothing here is optional.

| # | Requirement | Ships as |
|---|---|---|
| 1a | Canvas with a fixed primitive set | React Flow: drag from palette, connect, edit config in an inspector. One source handle per declared outcome, so an unwired outcome is visible on the card rather than only in a lint panel. |
| 1b | AI review leaves structured comments **on the canvas** | `threads` anchored to `primitive:` / `edge:` / `document_field:` keys, rendered as pins on the element — not a chat sidebar |
| 1b | Statuses `open` · `answered` · `rejected` · `resolved` | enforced in the DB. `answered` means the knowledge exists; `resolved` requires the reviewer to **re-run the originating scenario**, so resolution is proved rather than declared |
| 1c | Revision loop | reply posts a `thread_message`; a canvas patch re-runs lint and the scenario; `rejected` compiles into the spec as negative knowledge rather than being deleted |
| 1d | Submit → immutable spec | `specs` row with a Postgres trigger that raises on `UPDATE`/`DELETE`, plus a checksum. Editing the board afterwards cannot touch it. |
| 2a | Reusable, **process-agnostic** skeleton | `meridian/runtime/` — `AgentContext`, named step execution, outcome/error types, tool dispatch. Generated agents import only this. |
| 2b | Codegen: spec + skeleton → agent | deterministic scaffold (the workflow shell is **templated, never generated**) + per-primitive LLM leaf logic |
| 2c | Eval suite | `eval_cases`, one row per shipment, expected output authored from the provisioned inbox |
| 2d | Close the loop | `mvp eval sweep` → failure bundle → Codex repair skill → `mvp build register` → re-sweep. Human-triggered, which the brief explicitly blesses. |
| 3 | E2E, **≥2 review rounds** | intentionally incomplete seed board → round 1 (SOP-grounded) → round 2 (corpus-grounded) → freeze → generate → sweep → repair |
| — | React · Temporal · Composio · Supabase | all four on the real path |
| — | Deployed | Railway, three services from one repo — `api`, `worker`, `ui`. Deployed thin and early (§3), not as a final step. |
| — | A repo a stranger can pick up | README, this file, `Claude.md`, one concern per directory |
| — | Section 0 stance, grounded in the build | see §5 |

---

## 2. Three tiers

### Core — the loop is not provable without these

Units 1–13 in §3. Everything in the table above.

### Stretch — bought with recovered hours, in this order

Ranked by evidence-per-hour, not by appeal.

| | Item | Hrs | Why it ranks here |
|---|---|---|---|
| 1 | **Reviewer ablation suite** | 1.5 | Delete a known fact from the completed board, check the reviewer asks for it. Turns *"the review loop works"* from a claim into **recall as a number** — and the review loop is the most heavily graded thing in the brief. Cheapest credibility available. |
| 2 | **Conformance check** | 1.0 | Catches a repair that makes evals pass by deleting a validation. Small, and it closes the most embarrassing hole in a self-healing story. |
| 3 | **Metrics page — reliability curve** | 2.0 | The curve moving across builds *is* the self-healing claim. Currently CLI-only; on screen it is the single most persuasive frame in the video. |
| 4 | **Layer 2 coherence pass** | 1.5 | Tautology, absorption, contradiction over the ruleset. Findings are *provable*, which is what makes them safe to state confidently. |
| 5 | **Emergency stop + `deployments`** | 1.0 | Named explicitly in the PRD's metrics section. Cheap, and it signals production thinking. |
| 6 | **Executions page** | 1.5 | Run table with trajectory, linking out to Temporal for prod runs. |

Roughly 8 hours are available against 7.5 hours of queue, so items 1–5 are
realistic and item 6 is marginal. Railway used to sit at the bottom of this list;
it is now core (§3).

### Cut — and staying cut

| Cut | Why it is safe | What is lost |
|---|---|---|
| **Temporal beyond one workflow** | Durable execution earns its place through *resumption* — a corrected COA on Thursday reaching the shipment started Tuesday. That is `signal_with_start` plus one `wait_condition`. Schedules, search attributes and a production Gmail poller demonstrate nothing further. | No production trigger path. Documented as the next step. |
| **Bindings resolution UX** | The registry ships seeded and lint verifies capabilities resolve. The interactive "search Composio, pick an adapter" flow solves a problem pre-alert does not have — it needs Gmail and nothing else. | The multi-customer binding story is prose in the PDF, not code. |
| **Autonomous repair loop** | The brief blesses a human in the loop. `repair once` stops at `proposed` so an engineer reads the diff — which is the honest demo and the better product. | No unattended convergence. By design. |
| **`Compute` primitive** | Fails the brief's own test — cannot be explained to a non-engineer in one sentence — and fails our frequency rule (1 of 5 processes studied). | Quoting-style processes are inexpressible. Named as the vocabulary's boundary, with the schema change it would need. |
| **Autogenerated config forms** | Extensibility comes from the schema being one source of truth, which generating *types* already delivers. Generated rendering is cosmetic, and a generic renderer produces a JSON editor — the exact thing a non-technical user must never see. | Adding a config field needs a form edit. |
| **pgvector / retrieval** | A four-page SOP fits in context, and retrieval returns what *exists* — it can never return what is **absent**, which is precisely what gap detection needs. | Nothing. This one is a correctness argument, not a time argument. |

---

## 3. Order and budget

~41.5 hours core against ~48 elapsed, leaving ~6.5 for the stretch queue.

That buffer is now smaller than the stretch queue itself (§2 lists 7.5 hours),
so one item comes off. The **Executions page** goes first — it was already
marginal, and the CLI covers it in the video.

| | Unit | Hrs | Checkpoint |
|---|---|---|---|
| 0 | **Composio OAuth + inbox snapshot** | 1.0 | `fixtures/emails/` frozen — run **first**, out of band |
| 1 | `domain/` — primitives, graph, review, frozen | 2.0 | seed board validates |
| 2 | migrations + `store.py` + seed board | 2.5 | the incomplete board exists |
| 3 | `compiler/` — rules, shapes, context, freeze | 3.0 | **a spec freezes** |
| 4 | `cli.py` | 0.75 | everything drivable headless |
| 5 | **`api/` skeleton + Railway, three services** | 2.0 | `api`, `worker`, `ui` green on a health check |
| 6 | `runtime/` — the skeleton | 2.0 | a hand-written toy agent runs against it |
| 7 | `codegen/` | 3.5 | **an agent is generated** |
| 8 | eval cases from the snapshot | 1.5 | ground truth per shipment |
| 9 | `healing/` — sweep, bundle, gate, repair skill | 3.5 | **the curve moves** |
| 10 | `reviewer/` — scenarios, dry-run, semantic, distill | 3.5 | two rounds, real threads |
| 11 | `events.py` + realtime + background jobs | 1.5 | `cycle_id` streams to the browser |
| 12 | `ui/` — canvas, comments, submit, spec viewer, cycle panel | 6.5 | the demo |
| 12b | **text → cards** — `mvp board add --text`, route, UI panel | 1.5 | a paragraph becomes cards |
| 13 | Temporal + Composio on the real path | 2.5 | one live shipment |
| 14 | README, PDF, Loom, final deploy | 4.0 | shipped |

**Why this order.**

*Composio first.* §17 says pull the inbox **once**, snapshot it, and work
offline afterwards. That makes every downstream run deterministic — a failing
eval case fails for a logic reason, not because Gmail was slow. It is also the
step most likely to eat an unplanned hour, so it eats it on hour one.

*Deploy at unit 5, not unit 14.* Three services deployed empty is a two-hour
problem; three services deployed for the first time at hour 45 is an unbounded
one. `VITE_API_URL` is inlined at **build** time, so it is a build arg rather
than a runtime variable — the usual first-deploy failure, and worth hitting
while nothing depends on it. Every unit after 5 ships to a live URL.

*The API is not a unit.* `meridian/api` is a thin trigger layer: the app, CORS,
exception handlers and a health check ship at unit 5, and **every later unit adds
its own routes** as the module behind them lands. The CLI and the API call the
same plain functions — `mvp spec freeze --board 1` and `POST /pipeline/freeze`
both call `compiler.freeze()` — so the CLI is not throwaway scaffolding. It is
how the pipeline stays provable from a terminal, which is what makes a UI slip
cost polish rather than evidence.

| Unit lands | Routes it adds |
|---|---|
| 2 · store | `GET /boards/{id}` · primitives · edges · layout |
| 3 · compiler | `GET /boards/{id}/lint` · `POST /pipeline/freeze` |
| 7 · codegen | `POST /pipeline/codegen` |
| 9 · healing | `POST /pipeline/sweep` · `POST /pipeline/repair` · `POST /repairs/{id}/accept` |
| 10 · reviewer | `GET /boards/{id}/threads` · `POST /threads/{id}/messages` · `PATCH /threads/{id}` |
| 11 · events | `GET /metrics/cycle/{id}` and the realtime subscription |

Two response shapes throughout: CRUD is synchronous and returns the row;
anything in the pipeline returns `{cycle_id}` immediately and does its work in
the background. The UI never waits on a pipeline call — it subscribes to
`events` filtered on `cycle_id` and watches progress arrive. Same path whether
the trigger was a button or a terminal.

*The spine (1–9) is provable from a terminal*, so a UI slip costs polish rather
than evidence. `reviewer/` sits at 10 because hand-written threads in the seed
unblock units 3–9 — a slip there blocks nothing. Within unit 12 the build order
is canvas → comments → submit → cycle panel → spec viewer, each shippable alone.

**Vertical slice first.** Before widening, `coas_valid` goes all the way through
alone: domain type → lint finding → assertion → frozen spec entry → generated
`checks/coas_valid.py` → one eval case → one failure → one patch. If that holds,
every interface is real and widening is mechanical.

---

## 4. If the buffer evaporates

Dropped in this order, hardest first:

1. **Spec viewer** → the frozen spec is pretty-printed JSON in the video.
2. **Cycle panel** → the CLI prints the timeline.
3. **Round 2 semantic generation** → round 2 runs lint plus corpus-grounded
   scenarios only. *(Floor: two rounds are non-negotiable — the brief requires
   them.)*
4. **Composio live path** → `FixtureGmailTool` reads the snapshot, and the video
   says so plainly rather than implying a live pull.

Nothing above touches a graded criterion. Anything that would is not on this list.

---

## 5. Deliverables

Three artifacts beyond the repo. Budgeted, not left to the last hour.

**Design review (Day 1)** — `Claude.md` plus a one-page diagram of the two loops
split by the freeze. Sent before implementation; the 48-hour clock starts when
Alfonso and Sid confirm direction.

**Loom, ~6 minutes**, one continuous thread:

| Beat | Shows |
|---|---|
| the incomplete board | what a PO actually drew from the SOP |
| review round 1 | comments appear **on cards**, anchored, with categories |
| answering as the PO | status moves `open → answered → resolved`, and resolution re-runs the scenario |
| round 2 | the ASN question — a gap **no human wrote down**, found from corpus shape |
| Submit | checksum, and editing the board afterwards leaves the spec untouched |
| codegen | generated agent, spec entry beside the code it produced |
| sweep | failures, the copy-ready bundle |
| repair | Codex with the skill file, one diff, gate, re-sweep, curve moves |

**PDF** — how to run it · the primitive set and why, backed by the five-process
study · the comment and spec data model · the Section 0 stance · what I would do
differently.

**The Section 0 stance, grounded.** Three claims the build has to earn:
`relation: repeat` makes the board **cyclic** — a corrected COA routes back to a
check that already ran — so this is a state machine, not a DAG, and the
difference is load-bearing rather than pedantic. The spec is a **build input,
inert after codegen**: a repair loop editing a schema can only fix what the schema
anticipated, while a loop editing code can restructure. And the artifact a
frontier coding agent repairs is **a Python file with a stack trace**, not a JSON
node — which is exactly why the low-code abstraction stops at the whiteboard and
does not extend into the runtime.

---

## 6. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| **Temporal has no home in production** | Railway runs the `worker`, but a worker needs a server to poll. Blocks the deployed demo, not the local one. | Temporal Cloud — the brief offers help creating an account, so ask for one now. Self-hosting Temporal on Railway needs a persistence store plus several services and is not a 48-hour side quest. Fallback: the dev server as a fourth Railway service with a SQLite volume — hacky, but it is one container. |
| `VITE_API_URL` inlined at build time | The classic first-deploy failure: UI builds fine, then calls `localhost` in production | Set as a Railway **build arg**, not a runtime variable, and prove it at unit 5 while nothing depends on it |
| Composio OAuth eats an hour | Blocks the inbox snapshot, which blocks evals and repair | It is unit 0. Done before any code depends on it. |
| Docker daemon currently down | Temporal and local Postgres unverified | Supabase covers the database; Temporal is not needed until unit 13 |
| Codegen emits code that will not import | Blocks unit 8 | The workflow shell is templated, never generated. Only leaf bodies come from the model, and the writer enforces path confinement plus an import allowlist. |
| Reviewer generates noise | Weakens the most heavily graded loop | Shapes are deterministic matchers; the model phrases a finding but never authors the claim that a pattern exists. Precision proxy: round-1 rejection rate above a third means the rules are too fussy. |
