# Meridian MVP — build notes

Living document. Every schema and structure decision lands here with its reason,
so the repo, the migrations and the reasoning never drift apart.

**Picking up this codebase?** §2 is the stack, §3 is the repo map, §5 is the
schema, §7 is why things are the way they are.

---

## 1. What this is

A pipeline that turns tacit process knowledge into a running, self-repairing agent.

```
whiteboard  →  AI review loop  →  frozen spec  →  codegen  →  Temporal  →  self-heal
 (mutable)      (human oracle)     (immutable)     (Codex)    (durable)   (eval oracle)
```

Two loops, split by the freeze. Before it, ground truth lives in a person's head,
so the loop asks a human. After it, ground truth lives in the eval suite, so the
loop asks a test suite. **The freeze is where authority transfers from a person to
a test suite** — that one sentence explains why Submit exists and why the spec is
immutable.

Running example: validating inbound pre-alert documentation for a pharma
distributor. Commercial Invoices and Certificates of Analysis arrive by email
before a container lands; the agent checks completeness and consistency across a
shipment and reports discrepancies.

---

## 2. Tech stack

| Layer | Choice | Why |
|---|---|---|
| Backend language | Python 3.12 | Temporal + Composio + Anthropic SDKs; generated agents are Python so the repair agent edits one language |
| API | FastAPI | async; OpenAPI schema generates the frontend types |
| Validation | Pydantic v2 | discriminated unions for primitive configs; the schema *is* the contract |
| DB | Supabase (Postgres 15) | managed Postgres + Realtime + Storage |
| DB access | asyncpg, confined to `persistence/` | no ORM; SQL lives in one place |
| Migrations | plain `.sql` + Supabase CLI | no Alembic against a managed DB |
| Durable execution | Temporal (Python SDK) | resumption, timers, signals, replay |
| Tool calls | Composio | Gmail |
| LLM | **OpenAI throughout** — structured outputs for extraction, review and codegen planning; Codex headless for repair | one key, one SDK, one auth path. The PRD names OpenAI as the codegen provider and the repair loop runs as a Codex skill, so a second provider would buy nothing. |
| Frontend | Vite + React 18 + TypeScript | SPA, no SSR needed |
| Canvas | React Flow (`@xyflow/react` v12) | custom node/edge types, connection validation |
| Charts | Recharts | reliability curve |
| Realtime | `supabase-js` | one subscription to `events` |
| CLI | Typer | calls the same functions the API does |
| Deploy | Railway — three services from one repo: `api`, `worker`, `ui` | one platform, one dashboard, one set of env vars. Serverless was never an option anyway: a Temporal worker polls indefinitely and codegen writes files to disk. |

**Everything runs on Railway.** A Temporal worker polls a task queue indefinitely
and codegen writes files to disk and commits them — neither survives a serverless
function, so the backend was never going to be serverless. Putting the UI there
too means one platform, one dashboard, one place for env vars.

---

## 3. Repo structure

Two top-level projects: `meridian/` is the Python backend, `ui/` is the React
frontend. One manifest each, separate deploy targets, no shared code — `ui`
talks to `meridian` over HTTP only.

```
meridian-mvp/
├── README.md
├── Makefile                     dev · migrate · seed · test · types
├── docker-compose.yml           Temporal dev server (Postgres is Supabase)
│
├── meridian/                    ── BACKEND ──  Python 3.12 → Railway
│   ├── pyproject.toml
│   ├── Dockerfile
│   ├── src/meridian/
│   │   ├── domain/              pure types. imports nothing internal.
│   │   │   ├── primitives.py    EventConfig · ActionConfig · CheckConfig
│   │   │   ├── graph.py         Primitive · Edge · Document · Board
│   │   │   ├── review.py        Anchor · Thread · Assertion · Scenario
│   │   │   └── frozen.py        ScopedContext · SpecPrimitive · FrozenSpec
│   │   │
│   │   ├── store.py             ALL SQL. rows ↔ domain types.
│   │   │
│   │   ├── compiler/            board → frozen spec
│   │   │   ├── rules/           one file per lint rule + registry.py
│   │   │   ├── shapes.py        six matchers, one list
│   │   │   ├── serialize.py     board → reviewer payload | frozen spec
│   │   │   ├── context.py       assertion inheritance up the scope chain
│   │   │   ├── freeze.py        orchestration, ~60 lines
│   │   │   └── conformance.py   does a build still satisfy its spec
│   │   │
│   │   ├── reviewer/            board → threads
│   │   │   ├── scenarios.py     enumerate from graph + LLM generate
│   │   │   ├── dryrun.py        walk the graph, classify each scenario
│   │   │   ├── semantic.py      gap-finding LLM call
│   │   │   ├── ranking.py       dedup vs priors, cap 6
│   │   │   └── distill.py       resolved thread → assertion(s)
│   │   │
│   │   ├── codegen/             spec → python
│   │   │   ├── planner.py       spec → file manifest + signatures
│   │   │   ├── generator.py     per-primitive prompts + scaffold
│   │   │   ├── templates/       workflow.py.j2 · models.py.j2 · activity.py.j2
│   │   │   └── writer.py        path confinement + git commit
│   │   │
│   │   ├── healing/             build → better build
│   │   │   ├── sweep.py         run eval cases, emit events
│   │   │   ├── localize.py      bucket failures, build the copy-ready bundle
│   │   │   ├── gate.py          target passes + no regression
│   │   │   └── classifier.py    implementation_defect vs spec_gap
│   │   │
│   │   ├── runtime/             imported by GENERATED agents. never by api.
│   │   │   ├── context.py       AgentContext: tools, logger, clock
│   │   │   ├── step.py          named step execution + tracing
│   │   │   ├── outcome.py       return shape · errors
│   │   │   ├── temporal.py      plain async fn → Temporal workflow
│   │   │   ├── harness.py       headless execution against fixtures
│   │   │   └── tools/
│   │   │       ├── dispatch.py  capability key → provider
│   │   │       ├── composio.py  one class, all composio tools
│   │   │       ├── internal.py  doc.extract · storage.put
│   │   │       └── fixtures.py  fake tools for the harness
│   │   │
│   │   ├── api/                 ── FASTAPI SERVER ──
│   │   │   ├── main.py          app, CORS, exception handlers
│   │   │   ├── deps.py
│   │   │   ├── background.py    long jobs → return cycle_id immediately
│   │   │   └── routes/
│   │   │       ├── boards.py    primitives · edges · layout
│   │   │       ├── review.py    threads · messages · status transitions
│   │   │       ├── documents.py upload → extract → confirm fields
│   │   │       ├── specs.py     freeze · fetch · bindings status
│   │   │       ├── pipeline.py  codegen · sweep · repair · deploy
│   │   │       └── metrics.py   view passthrough
│   │   │
│   │   ├── worker/              ── TEMPORAL WORKER ──
│   │   │   ├── main.py          registers workflows + activities
│   │   │   ├── poller.py        gmail → signal_with_start
│   │   │   └── activities.py
│   │   │
│   │   ├── bindings.py          load yaml · unbound vs unresolvable
│   │   ├── events.py            the single emit()
│   │   └── cli.py               typer app
│   │
│   └── tests/                   mirrors the package, no DB
│
├── ui/                          ── FRONTEND ──  Vite + React → Railway
│   ├── package.json
│   ├── Dockerfile               node build → nginx or `vite preview`
│   ├── vite.config.ts · tsconfig.json · index.html
│   ├── .env.example             VITE_API_URL · VITE_SUPABASE_URL · VITE_SUPABASE_ANON_KEY
│   └── src/
│       ├── main.tsx · App.tsx
│       ├── routes/              Whiteboard · Executions · Evals · Metrics · HumanInLoop
│       ├── features/            FEATURE-FIRST, not type-first
│       │   ├── canvas/          Canvas · nodes/ · edges/ · Palette · validation · useBoard
│       │   ├── inspector/       Inspector · ConfigForm · FieldPicker
│       │   ├── threads/         ThreadPanel · useThreads
│       │   └── cycle/           Timeline · useCycleEvents
│       ├── components/          shared DUMB ui only — Badge · Pill · StatusDot
│       └── lib/                 api.ts · schema.d.ts (generated) · supabase.ts · queryClient.ts
│
├── agents/                      GENERATED. committed. repair edits here.
│   └── inbound_pre_alert/
│       ├── spec.lock.json
│       ├── workflow.py          templated, not LLM-written
│       ├── models.py · trigger.py
│       └── extraction/  checks/  actions/
│
├── bindings/aurologistics.yaml  per customer. committed. no secrets.
│
├── db/
│   ├── migrations/0001_initial.sql
│   └── seeds/                   tools.sql · prealert_board.py
│
└── fixtures/                    emails/ · documents/ · expected/
```

### Deployment — three Railway services, one repo

| Service | Root | Command |
|---|---|---|
| `api` | `meridian/` | `uvicorn meridian.api.main:app --host 0.0.0.0 --port $PORT` |
| `worker` | `meridian/` | `python -m meridian.worker.main` |
| `ui` | `ui/` | static build served by nginx |

`api` and `worker` share one image and differ only by start command. Railway's
root-directory setting points each service at the right subdirectory; a push
rebuilds all three.

`ui` needs `VITE_API_URL` set to the `api` service's public URL at **build** time
— Vite inlines env vars into the bundle, so it is a build arg, not a runtime
variable. Getting this wrong is the usual first-deploy failure.

The only thing crossing the backend/frontend boundary is generated types, one
direction:

```bash
make types   # cd ui && npx openapi-typescript http://localhost:8000/openapi.json -o src/lib/schema.d.ts
```

### Why this shape

| Choice | Reason |
|---|---|
| `meridian/` and `ui/` at the top | backend and frontend are different toolchains with nothing shared. One manifest each, one Dockerfile each, no ambiguity about what belongs where. |
| `api/` and `worker/` inside the package | they are entrypoints, not separate projects. Same image, different commands. |
| `agents/` at the top level | neither backend source nor frontend — it is output. Makes the path-confinement rule (codegen writes only under `agents/<slug>/`) trivially checkable. |
| `runtime/` inside `meridian/`, imported only by generated code | one package is simpler than a workspace. The boundary is a lint check on generated files rather than a packaging constraint. |
| one `store.py`, not a `persistence/` package | one job: rows ↔ domain types. Splitting it spreads SQL across files that all do the same thing. |
| `shapes.py` is one file | a shape is a name and a match lambda, 2–4 lines each. A directory of modules for lambdas is ceremony. |
| `rules/` **stays** a directory | each rule carries a message, an anchor kind and conditional applicability — real content per file — and `registry.py` becomes a readable definition of "complete". |

**The rule applied throughout: a directory earns itself when its members differ in
content, not just in name.** Ten lint rules differ; ten shape lambdas do not.
Earlier drafts had `persistence/` (5 files), `compiler/shapes/` (11),
`bindings/` (3) and `observability/` (2). All collapsed.

### Python dependency rule

```
domain/      imports nothing internal
store.py     imports domain
compiler/    imports domain
reviewer/    imports domain, compiler
codegen/     imports domain
healing/     imports domain, codegen
runtime/     imports domain only — NEVER compiler, reviewer, codegen, healing
api/         imports everything except runtime
worker/      imports runtime + domain
```

Anything importing upward is a bug. Greppable, so it stays true.

Generated agents under `agents/` may import **only** `meridian.runtime.*` and
their own directory — enforced by a check in `codegen/writer.py`, not by
convention.

**Why lint rules live in `compiler/` and not `domain/`:** domain types are facts
everyone agrees on. Rules are the compiler's *policy* about what "ready to freeze"
means. `reviewer/` and `healing/` have their own policies. Shared nouns, private
judgement.

### shapes.py

```python
def _triples(g):
    return [(g.p(e.from_key), e, g.p(e.to_key)) for e in g.edges]

SHAPES = [
    ("serial_gate", lambda g: [
        [f"primitive:{a.key}", f"edge:{e.key}", f"primitive:{b.key}"]
        for a, e, b in _triples(g)
        if a.is_check and b.is_check and "pass" in e.on_outcomes]),

    ("exception_exit", lambda g: [
        [f"primitive:{a.key}", f"edge:{e.key}", f"primitive:{b.key}"]
        for a, e, b in _triples(g)
        if e.relation == "exception" and not g.outgoing(b.key)]),

    ("collapsed_outcomes", lambda g: [
        [f"primitive:{p.key}"] for p in g.checks()
        if len({e.to_key for e in g.outgoing(p.key)}) < len(p.config.outcomes)]),

    ("divergent_terminals", lambda g:
        [[f"primitive:{p.key}" for p in g.terminals()]] if len(g.terminals()) > 2 else []),

    ("single_entry", lambda g: [["board"]] if len(g.events()) == 1 else []),

    ("no_deadline", lambda g: [
        [f"primitive:{e.key}"] for e in g.events()
        if not e.config.timing or not e.config.timing.deadline]),
]

def match(g) -> list[dict]:
    return [{"kind": k, "elements": els} for k, fn in SHAPES for els in fn(g)]
```

**Six, not ten.** These fired on both the pre-alert board and the synthetic
credentialing board. `cycle_unbounded`, `unordered_siblings`,
`split_request_response` and `overloaded_terminal` are plausible but have not
earned entries.

**Shapes are a floor, not a fence.** The prompt says so explicitly:

```
`shapes` and `gaps` are a starting point, not a checklist. Traverse the graph
yourself. Anything you notice that is in neither list is more valuable, not
less — those are the ones my patterns missed.
```

Without that line the model treats `shapes` as the assignment and stops there. It
also gives a growth path: **a pattern the model keeps rediscovering should be
promoted to a matcher.** The library grows from observed misses, not speculation.

### React conventions

| Convention | Reason |
|---|---|
| `features/` not top-level `components/`, `hooks/`, `utils/` | group by domain concern. A canvas change touches one directory, not four. |
| `components/` is shared **dumb** UI only | if it knows about primitives or threads, it belongs in a feature. |
| hooks colocated with their feature | `useBoard` lives in `canvas/`, not a global `hooks/`. |
| `lib/` for infrastructure | api client, supabase, query client. Not `utils/`. |
| routes are thin | compose features, handle params. Logic lives in the feature. |
| TanStack Query for server state | state is almost entirely server-derived. Local UI state (selection) is `useState` in the feature. |
| `schema.d.ts` generated and committed | regenerate after an API change; TypeScript errors are the drift check. |

### Naming

- directories are **capabilities**, not layers — `compiler`, `reviewer`, `healing`; never `utils`, `helpers`
- modules are nouns, functions are verbs — `compiler/freeze.py` exposes `freeze()`
- `runtime/` not `skeleton/` — it is the contract generated agents import
- generated code is `agents/`, never `src/` — an artefact, not source

---

## 4. Supabase specifics

| Concern | Decision |
|---|---|
| Auth | none for the MVP. single tenant. no `org_id`, no user tables. |
| RLS | **enable on every table anyway.** An un-enabled table in `public` is readable by the anon key, which ships in the frontend bundle. |
| Access | FastAPI holds the service role key and does all reads and writes. The browser never queries tables. |
| Realtime | the browser's only Supabase usage: one subscription to `events`. Needs `alter publication supabase_realtime add table events`. |
| Migrations | plain SQL, Supabase CLI. |
| Extensions | `pgcrypto`. No pgvector — the SOP is four pages and fits in context, and retrieval returns what exists rather than what is *absent*, which is what gap detection needs. |
| Storage | sample PDFs in Supabase Storage; tables hold the path. |

---

## 5. Schema

Twenty tables. Cut along the way: `patch_proposals`, `orgs`, `memberships`, `workflows`,
`document_samples`, `subgraphs`.

### 5.1 Conventions

- **`key`, not `id`, for anything the spec references.** Primitives, edges,
  documents and scenarios carry a board-scoped slug. Threads, assertions,
  failures and events reference those slugs **with no foreign key** — a primitive
  can be deleted and re-created during review without orphaning its conversation,
  and generated filenames match spec keys.
- **`jsonb` only for type-discriminated payloads.** `primitives.config` varies by
  `primitive_type`; Pydantic validates at the boundary. Fixed shapes get columns.
- **Mutable things are rows, immutable snapshots are blobs.** The board is rows;
  the spec is one payload, because nothing ever queries inside a frozen spec.
- **ISO 8601 durations** as text (`PT48H`) — parses in Python, TypeScript and
  Temporal.

### 5.2 Board — mutable

```sql
create domain board_key as text check (value ~ '^[a-z][a-z0-9_]{1,63}$');

create table boards (
  id           uuid primary key default gen_random_uuid(),
  name         text not null,
  status       text not null default 'draft'
               check (status in ('draft','in_review','submitted')),
  review_round int  not null default 0,
  layout       jsonb not null default '{}',    -- {primitive_key: {x,y}}
  created_at   timestamptz not null default now()
);

create table primitives (
  id             uuid primary key default gen_random_uuid(),
  board_id       uuid not null references boards(id) on delete cascade,
  key            board_key not null,
  primitive_type text not null check (primitive_type in ('event','action','check')),
  group_key      board_key,          -- optional region label; NOT a table
  config         jsonb not null,
  unique (board_id, key)
);
create index on primitives (board_id);

create table edges (
  id          uuid primary key default gen_random_uuid(),
  board_id    uuid not null references boards(id) on delete cascade,
  key         board_key not null,
  from_key    board_key not null,
  to_key      board_key not null,
  relation    text not null default 'normal'
              check (relation in ('normal','exception','repeat')),
  on_outcomes text[] not null default '{}',    -- one edge may carry several
  condition   text,
  unique (board_id, key),
  check (from_key <> to_key or relation = 'repeat')
);
create index on edges (board_id, from_key);
create index on edges (board_id, to_key);

-- Operational documents have NO table. A Document is a card, so it is a row in
-- `primitives` with primitive_type = 'document' and DocumentConfig in `config`
-- (name, identified_by, cardinality, fields, sample_path, sample_extracted).
--
-- The process owner has to see what a Check reads, and a separate upload panel
-- with its own table made that invisible on the canvas. Making it a card also
-- collapses a concept: an Action with effect=lookup `produces` a Document, so
-- data that arrived by email and data fetched from a system are the same kind
-- of thing — one form of addressable data instead of two.
--
-- Reference documents stay separate below: an SOP *describes* the process and
-- is reviewer input only, while an invoice *flows through* it.

-- Reference documents: describe the process. Reviewer input only, never in spec.
create table reference_docs (
  id             uuid primary key default gen_random_uuid(),
  board_id       uuid not null references boards(id) on delete cascade,
  kind           text not null check (kind in ('sop','policy','email','other')),
  filename       text not null,
  storage_path   text not null,
  extracted_text text
);
```

### 5.3 Review

```sql
create table scenarios (
  id                uuid primary key default gen_random_uuid(),
  board_id          uuid not null references boards(id) on delete cascade,
  key               board_key not null,
  kind              text not null check (kind in ('happy','variant','probe')),
  description       text not null,
  inputs            jsonb not null default '{}',
  expected_terminal board_key,
  dryrun_result     text check (dryrun_result in
                    ('reached_terminal','dead_end','undefined_branch','loop')),
  round             int not null default 1,
  unique (board_id, key)
);

create table threads (
  id           uuid primary key default gen_random_uuid(),
  board_id     uuid not null references boards(id) on delete cascade,
  category     text not null check (category in
               ('missing_path','ambiguous_rule','missing_context',
                'undefined_exception','undefined_timing','redundancy','spec_gap')),
  severity     text not null default 'important'
               check (severity in ('blocking','important','minor')),
  status       text not null default 'open'
               check (status in ('open','answered','rejected','resolved')),
  origin       text not null default 'scenario'
               check (origin in ('lint','scenario','semantic','reduction','repair')),
  round        int  not null default 1,
  question     text not null,
  scenario_key board_key,                 -- the evidence that exposed it
  created_at   timestamptz not null default now(),
  resolved_at  timestamptz
);
create index on threads (board_id, status);

-- A conversation may span a primitive, an edge and a region at once.
create table thread_anchors (
  thread_id   uuid not null references threads(id) on delete cascade,
  anchor_kind text not null check (anchor_kind in
              ('primitive','edge','group','document_field','board')),
  anchor_key  text,                       -- 'commercial_invoice.batch_nos'
  is_primary  boolean not null default false,
  primary key (thread_id, anchor_kind, anchor_key),
  check ((anchor_kind = 'board') = (anchor_key is null))
);
create index on thread_anchors (anchor_kind, anchor_key);

create table thread_messages (
  id         uuid primary key default gen_random_uuid(),
  thread_id  uuid not null references threads(id) on delete cascade,
  seq        int  not null,
  author     text not null check (author in ('ai','human')),
  body       text not null,
  created_at timestamptz not null default now(),
  unique (thread_id, seq)
);

-- Distillation. Threads are conversations; assertions are settled statements.
-- Only assertions cross the freeze. SINGLE anchor: a statement compiles into
-- exactly one unit, so a multi-anchor thread yields several assertions.
create table assertions (
  id              uuid primary key default gen_random_uuid(),
  board_id        uuid not null references boards(id) on delete cascade,
  thread_id       uuid not null references threads(id) on delete cascade,
  anchor_kind     text not null check (anchor_kind in
                  ('primitive','edge','group','document_field','board')),
  anchor_key      text,
  kind            text not null check (kind in
                  ('rule','exception','timing','owner','terminology',
                   'constraint','negative')),
  statement       text not null,          -- prose, for codegen
  constraint_json jsonb,                   -- machine-checkable, document_field
  round           int not null default 1,
  superseded_by   uuid references assertions(id),
  created_at      timestamptz not null default now(),
  check ((anchor_kind = 'board') = (anchor_key is null)),
  check (kind <> 'constraint' or constraint_json is not null)
);
create index on assertions (board_id, anchor_kind, anchor_key)
  where superseded_by is null;
```

### 5.4 Spec — immutable

```sql
create table specs (
  id        uuid primary key default gen_random_uuid(),
  board_id  uuid not null references boards(id),      -- provenance, NO cascade
  version   int  not null,
  payload   jsonb not null,
  checksum  text not null,
  frozen_at timestamptz not null default now(),
  unique (board_id, version)
);

create or replace function forbid_spec_mutation() returns trigger
language plpgsql as $$
begin raise exception 'specs are immutable; submit again for a new version'; end $$;

create trigger specs_immutable before update or delete on specs
  for each row execute function forbid_spec_mutation();
```

### 5.5 Build and repair

```sql
create table agent_builds (
  id              uuid primary key default gen_random_uuid(),
  spec_id         uuid not null references specs(id) on delete cascade,
  iteration       int  not null,
  parent_build_id uuid references agent_builds(id),
  source_ref      text not null,          -- 'agents/inbound_pre_alert@a3f9c21'
  created_by      text not null check (created_by in ('codegen','repair','human')),
  -- reproducibility: same spec + different model = different code
  model           text,                   -- 'claude-sonnet-4-6'
  prompt_version  text,                   -- sha of the codegen templates
  temperature     numeric(3,2),
  -- localisation: primitive_key → file. codegen writes it, localize reads it.
  -- without this the mapping depends on a '[from primitive X]' header convention
  -- that fails silently when a file is renamed.
  file_map        jsonb not null default '{}',
  created_at      timestamptz not null default now(),
  unique (spec_id, iteration)
);

create table eval_cases (
  id              uuid primary key default gen_random_uuid(),
  spec_id         uuid not null references specs(id) on delete cascade,
  key             text not null,          -- shipment no: 'CAAU4056270'
  split           text not null default 'train' check (split in ('train','holdout')),
  origin          text not null check (origin in
                  ('authored','scenario','inbox','mutation')),
  scenario_key    board_key,              -- review scenarios become eval cases
  input           jsonb not null,
  expected_output jsonb not null,         -- the per-shipment aggregate row
  tags            text[] not null default '{}',
  unique (spec_id, key)
);

create table runs (
  id                   uuid primary key default gen_random_uuid(),
  build_id             uuid not null references agent_builds(id) on delete cascade,
  case_id              uuid references eval_cases(id),      -- null = prod
  mode                 text not null check (mode in ('sandbox','shadow','prod')),
  outcome              text check (outcome in ('passed','failed','error','running')),
  output               jsonb,
  temporal_workflow_id text,
  cost_usd             numeric(10,4),
  started_at           timestamptz not null default now(),
  ended_at             timestamptz
);
create index on runs (build_id, outcome);

create table failures (
  id            uuid primary key default gen_random_uuid(),
  run_id        uuid not null references runs(id) on delete cascade,
  primitive_key board_key,
  detector      text not null check (detector in
                ('assertion','output_diff','conformance')),
  signature     text not null,             -- bucketing key for localisation
  detail        jsonb not null
);
create index on failures (signature);

create table repairs (
  id                 uuid primary key default gen_random_uuid(),
  build_id           uuid not null references agent_builds(id),
  classification     text not null check (classification in
                     ('implementation_defect','spec_gap')),
  failure_signature  text not null,
  failing_case_ids   uuid[] not null default '{}',
  files_touched      text[] not null default '{}',
  summary            text not null,
  diff               text,
  status             text not null default 'proposed' check (status in
                     ('proposed','regressed','accepted','rejected','escalated')),
  regressed_case_ids uuid[] not null default '{}',
  produced_build_id  uuid references agent_builds(id),
  raised_thread_id   uuid references threads(id),
  created_at         timestamptz not null default now(),
  -- a spec gap cannot be silently patched in code
  check (classification <> 'spec_gap' or raised_thread_id is not null)
);
```

### 5.6 Tools, patches, deployment, run steps

Four tables added after auditing the schema against the PRD.

```sql
-- PRD frozen-spec source: "which external capabilities are actually available"
-- ActionConfig.capabilities[] references tools.key; lint verifies each resolves,
-- and codegen binds 'email.send' → GMAIL_SEND_EMAIL from here.
create table tools (
  key          text primary key,           -- 'email.send'
  provider     text not null,              -- 'composio'
  action       text not null,              -- 'GMAIL_SEND_EMAIL'
  input_schema jsonb not null,
  enabled      boolean not null default true
);

-- PRD metrics: emergency stop + promotion lifecycle. The worker checks status
-- before every activity; 'stopped' halts new work without killing in-flight runs.
create table deployments (
  id         uuid primary key default gen_random_uuid(),
  build_id   uuid not null references agent_builds(id),
  stage      text not null check (stage in ('sandbox','shadow','prod')),
  status     text not null default 'active'
             check (status in ('active','paused','stopped')),
  bindings_sha text,                        -- which bindings file version is live
  stopped_by text,
  stopped_at timestamptz,
  updated_at timestamptz not null default now()
);

-- PRD metrics: current step, completed steps, tool calls, retries.
-- Populated by the HEADLESS HARNESS only (mode='sandbox'). Prod runs leave this
-- empty and link out via runs.temporal_workflow_id — Temporal already has the
-- history and mirroring it would be a worse copy of a solved thing. The harness
-- has no history to copy, and the repair loop needs step data to localise.
create table run_steps (
  id            uuid primary key default gen_random_uuid(),
  run_id        uuid not null references runs(id) on delete cascade,
  seq           int not null,
  primitive_key board_key not null,
  attempt       int not null default 1,     -- retries
  status        text not null check (status in ('running','ok','failed','skipped')),
  input         jsonb,
  output        jsonb,
  tool_calls    jsonb,
  error         text,
  latency_ms    int,
  unique (run_id, seq, attempt)
);
```

### 5.7 Observability

```sql
create table events (
  id            bigserial primary key,
  at            timestamptz not null default now(),
  cycle_id      uuid not null,             -- correlates one end-to-end run
  spec_id       uuid,
  build_id      uuid,
  run_id        uuid,
  case_key      text,
  thread_id     uuid,                      -- review-phase events point at a thread
  primitive_key board_key,                 -- threads a failure back to a card
  phase         text not null check (phase in
                ('review','compile','codegen','eval','repair','deploy','prod')),
  kind          text not null,
  status        text not null check (status in ('started','ok','failed','rejected')),
  duration_ms   int,
  cost_usd      numeric(10,5),
  detail        jsonb not null default '{}'
);
create index on events (cycle_id, at);
create index on events (build_id, phase);

alter publication supabase_realtime add table events;
```

Views: `v_reliability` (the curve), `v_timeline` (one cycle end to end),
`v_hotspots` (failures by primitive — ranks repair), `v_cycle_summary`
(patches proposed vs rejected by the gate, cost). No metrics tables.

### 5.8 RLS

```sql
-- No auth in the MVP, but never leave a public-schema table unprotected:
-- without RLS the anon key can read it. FastAPI uses the service role, which
-- bypasses RLS, so deny-all is correct here.
do $$ declare t text; begin
  foreach t in array array[
    'boards','primitives','edges','documents','reference_docs','scenarios',
    'threads','thread_anchors','thread_messages','assertions','specs','tools','agent_builds','eval_cases','runs','run_steps','failures',
    'repairs','deployments','events']
  loop execute format('alter table public.%I enable row level security', t); end loop;
end $$;

create policy events_read on events for select to anon using (true);
```

---

## 6. Primitive semantics

**Four cards: three steps and one thing.** Event, Action and Check answer *what
happened*, *what gets done* and *what needs to be determined*. Entity answers
*what are you looking at*. Steps are connected by edges and form the state
machine; an Entity is referenced by the steps that read or produce it.

Waiting is **not** a fifth — the PRD defines Event as "an occurrence that
starts, **resumes**, or meaningfully changes the state of a process." An Event
with a `deadline` compiles to `wait_condition(timeout=…)`; without one it
compiles to a plain signal handler. A Wait primitive would duplicate semantics
an existing one already carries.

| Primitive | Required config | Compiles to |
|---|---|---|
| **Entity** | `name`, `fields`, `cardinality`; `identified_by` when it arrives rather than being produced | an extraction schema — not a step |
| **Event** | `role`, `correlation_key` (FieldRef), `match_condition`, `timing`, `resulting_state`; `outcomes` when `deadline` is set | workflow entry, signal handler, or `wait_condition` + timer |
| **Action** | `performed_by`, `completion_criteria`; `recipients` + `payload_fields` if it notifies; `idempotency_key` if it writes; `is_terminal` | Temporal activity |
| **Check** | `criteria` (structured), `scope`, `quantifier`, `inputs`, `outcomes` (≥2), `evidence`, `on_missing_input` | pure predicate returning `CheckResult` |

**Checks return aggregates, not booleans.** The eval set is a per-shipment row of
counts — `CoA (Success) 5 / CoA (Total) 5` — so:

```python
class CheckResult(BaseModel):
    outcome: str            # pass | missing_coa | mismatched_coa
    total: int
    passed: int
    failed: int
    failures: list[dict]    # evidence rows
```

**`correlation_key` is a FieldRef, not free text** — so lint can verify the
referenced field exists on a document the Event captures. Free text means codegen
has to guess which field "container number" means.

**Completeness is derived, not configured.** A shipment is complete when every
batch listed across its invoices has a matching COA. The invoice tells you how
many COAs to expect; nothing configures a count.

---

## 7. Decisions log

| Decision | Reason |
|---|---|
| Compile, don't interpret | the spec is a **build input**, inert after codegen. A repair loop editing a schema can only fix what the schema anticipated; editing code, it can restructure. |
| Three primitives, no Wait | a Wait is an Event with a deadline; duplicating semantics is the over-granularity failure the brief warns about. |
| Correlation key is the shipment | eval rows are keyed on container, with several invoices and COAs per row. One workflow instance per shipment, not per invoice. |
| Checks return counts | the deliverable is a per-shipment aggregate, not a branch name. |
| No graph DB | graph-shaped data, whole-board access. Every read loads a board, no query crosses boards, and the freeze must commit atomically with copied assertions. |
| No pgvector | a four-page SOP fits in context. Retrieval returns what exists — it can never return what is *absent*, which is exactly what gap detection needs. |
| No `run_steps` | localise from `failures.primitive_key` + `events`. Temporal owns prod tracing; mirroring its history is a worse copy of a solved thing. |
| `group_key` column, no `subgraphs` table | grouping is a label, not an entity. Add the table when a region needs its own `requires`/`provides`. |
| Layout on `boards` | dragging a node must not write to `primitives`, so that table changes only when the *process* changes. |
| Keys not FKs for spec references | a primitive can be deleted and re-created during review without orphaning threads; generated filenames match spec keys. |
| Spec as a blob | nothing ever queries inside a frozen spec. |
| Threads many-to-many, assertions single-anchor | a conversation spans several elements; a settled statement compiles into exactly one unit. |
| Eval labels held out of review | the eval set's *shape* informs the reviewer's questions; its *values* are the repair loop's oracle. Letting labels into elicitation fits the spec to the test. |
| `agents/` is committed | builds are git commits; every repair is a reviewable diff, revertable with `git checkout`. |
| Repair authority = who owns the decision | not spec-vs-code. If two competent people could disagree and the customer would care, it is a spec gap. Otherwise the loop patches freely. |
| `run_steps` scoped to the harness | Temporal owns prod history; the headless harness has none to copy, and the repair loop needs step data to localise. |
| Rounds differ in kind | round 1 grounded in the SOP, round 2 in the corpus. Two identical rounds are worth less than one. |
| Reviewer tested by ablation | delete a known fact from the completed board, check the reviewer asks for it. Gives recall as a number. |
| Spec / hints split | extraction hints are repair-owned and mutable; the contract is checksummed. Otherwise a patched hint drifts code from spec. |
| Repair is human-run, not autonomous | the deliverable is a legible failure bundle the engineer pastes into Codex with a skill file. Automation is not the product; legibility is. |
| Reviewer forms hypotheses, not questions | enumerate a bounded probe space and instantiate against it. A model asked to volunteer gaps under-extracts by ~a third. |
| Constraints validated against samples | a PO answer can be wrong, not just missing. Checking "7 digits" against `sample_extracted` catches it before the freeze. |
| Extraction is an activity, always | model calls are nondeterministic; in workflow code they break Temporal replay silently. |
| Classify-then-dispatch, never tool-choice | the model picks from a closed enum, the dispatch is code. Keeps control flow deterministic and evals meaningful. |
| PO never sees a tool | they author `channel` from a small enum in their own vocabulary; `capabilities` is derived at bind time. Tool selection is an implementation concern. |
| `channel` is an enum, not free text | "email"/"Email"/"Outlook" all mean one thing and none of them join. An enum makes binding a lookup rather than a guess. |
| Bindings are a YAML file, not a table | per-customer config versioned in git beside `spec.lock.json`. Identities must never enter the checksummed spec — a personnel change would force a new spec version. |
| Tool registry is seeded, never dynamic | it exists to be a closed set the spec validates against. An unbounded tool space makes `capability_unresolvable` unfalsifiable. |
| A tool gap can become a spec gap | if a capability genuinely cannot be automated, the process model assumed something false. Back to the PO, not to engineering. |
| Events are never terminal | an Event is something arriving; after it arrives something must happen. `is_terminal` lives on `ActionConfig` only. |
| No autogenerated forms | the schema being the single source of truth is what gives extensibility; generated rendering is cosmetic. Generate types, hand-write forms. |
| Required-by-Check fields are nullable in the extraction schema | if extraction refused to return a line item missing a code, the Check could never detect the failure it exists to detect. |
| `effect` and `channel` are separate fields | notifying a person and writing to software are independent, not points on one axis. |
| `system` is free text, `channel` is an enum | the system name is per-customer and unguessable; the channel is a closed set in the PO's own vocabulary. |
| `lookup` is the missing third direction | data moves in (Event), out (Action), and fetched mid-process. Only the first two exist. Same root cause as the computation gap: derived values are not addressable. |
| Credentials never touch the spec | spec references a capability, bindings reference an entity, the provider holds the secret. |
| No CRUD for registry or bindings | the UI reads them and highlights gaps; edits happen in a file under version control. Any screen must make the loop visible, not merely make config editable. |
| `agent_builds` records model + prompt version | reproducibility is a claim about builds, and the same spec with a different model produces different code. |
| `agent_builds.file_map` is explicit | localisation otherwise depends on a `[from primitive X]` comment convention that fails silently on rename. |
| `patch_proposals` cut | the PO edits the canvas directly. Losing the AI-proposes-a-patch moment costs a demo beat, not correctness. |
| Shapes emit matches, not prose | detection is deterministic, phrasing is the model's. If the model authored the claim it could hallucinate a pattern that isn't there. |
| Shapes are a floor, not a fence | six matchers guarantee certain classes always get asked; the prompt explicitly invites the model to find what the patterns missed. A pattern it keeps rediscovering gets promoted to a matcher. |
| A directory earns itself when members differ in content | ten lint rules differ; ten shape lambdas do not. `shapes.py`, `store.py`, `bindings.py`, `events.py` are single files. |
| No narrative or `asserts` strings | templated prose solving a problem that doesn't exist at 11 primitives. Shape definitions live in the system prompt, once. |
| Anchors are single elements with database keys | a path has no identity that survives an edit; it is a set of anchors. `group:` is the exception — membership changes, the group doesn't. |
| Context is inlined, not referenced | codegen reads one primitive entry and has everything. A board-level assertion physically appears N times, which is the correct trade. |
| Toolkit scale never bites | the PO names the system, so binding is a lookup on a specific string, not a search over a catalog. Capabilities are coarser than toolkits — `email.send` absorbs Gmail and Outlook. |
| Credentials live at the provider | Composio holds the OAuth token keyed by entity; bindings record only the entity id, so the file is safe to commit. |
| Four effects, not three | data moves four ways at a step: out to a person, out to a system, in from a system, or nowhere. `lookup` was the missing one. |
| Detail in free text is an asset | a PO writing "Nursys for most states, but California and Texas have their own portals" has written the binding brief. Structured fields exist to make linting possible; everything else rides in prose. |
| Codegen must not resolve tools | it would make an unapproved business decision, break the pre-codegen gate, and re-resolve per build instead of once per customer. The model narrows, the human commits. |
| One LLM provider, not two | §23's extraction path moves from Anthropic document blocks to OpenAI structured outputs with file inputs. Same shape, one SDK. §23's code sample still shows the Anthropic call and is rewritten when the extraction unit lands. |
| ASN and Goods stay out of the seed board | the board models only what the SOP states. The gap is then *found* by round-2 corpus grounding rather than pre-answered, which turns §8's open item into the demonstration that the reviewer works. Added to the board as the PO's answer, not as a guess. |
| Dependencies are added by the unit that needs them | a manifest listing the whole §2 stack on day one makes every later diff silent about what the code actually started using. The stack is still §2; only its arrival is staged. |
| `unmet()` returns objects, not strings | lint would otherwise re-derive severity and anchor from prose. This is the difference between §22 Layer 1 being a gate and being a printout. |
| Conditional requirements live in `unmet()`, never in validators | a PO drops a half-filled card and walks away. Pydantic validators would make that board unstoreable; the completeness contract belongs to the freeze gate, not to persistence. |
| Local Postgres for tests, Supabase for real | integration tests never touch a shared database, and matching Supabase's major version means a migration that passes locally passes there. |
| **Entity is the fourth card, and `documents` is not a table** | the brief's own example set is "a trigger, **an input**, a business rule, or a system". A Check's `inputs` referenced documents that appeared nowhere on the canvas, so a PO could not see what a Check read. As a card it is a row in `primitives`; as a side panel it was a second interaction to learn. |
| `entity`, not `document` | tested against five processes: in four of five, most entities come from a **lookup**, not from an emailed file. Pre-alert is the outlier. `document` would be a lie in the spec and in generated code. The palette label stays plain language — the type name and the UI string are different artifacts and need not agree. |
| A lookup produces an Entity | collapses two kinds of addressable data into one. `Action.produces` names an Entity card and `Check.inputs` is uniformly a list of Entity keys, so `ProducedInput` disappears. Four cards, one fewer concept in the type system. |
| Two equal authoring paths, no primary | "describe it" and "show me one" both land on the same object. Making sample-upload primary privileges the one process where every entity is a PDF. `fields` is blocking; `sample_extracted` is minor — a sample is what unlocks constraint-vs-sample, not what defines the entity. |
| `cardinality: one_per(FieldRef)` | "one COA per batch on the invoice" is a relationship between two entities and belongs to neither card alone. It lives on the dependent entity, naming the field that enumerates it — which is what makes `coa_total` derivable rather than configured, and it is the process owner's own phrasing. |
| `Criterion.op = "custom"` | six closed operators is a small expression language, and a seventh comparison would mean editing Python — the low-code trap the brief's Section 0 asks about. `custom` carries the rule as prose with the fields it reads named, so codegen can implement it and lint can still resolve references. The typed ops stay where lint can do more than resolve. |
| `findings()`, not `unmet()` | structured findings with severity and a location are the industry shape — LSP diagnostics, ESLint messages, Django's `check()` returning `CheckMessage`. "Findings" is also already this document's word for lint output, so the API and the prose agree. |
| `identified_by` is important, not blocking | an entity produced by a lookup needs no recognition rule. Whether one is required depends on whether anything produces the entity, which is a board-level fact — so config-level findings state what is true regardless of context, and the compiler rule raises severity. Same split as shared nouns, private judgement. |
| Four criterion ops, not seven | `match` is a comparison with a `matches` operator; `temporal` is a comparison whose operands are dates. What differs is the right-hand side, so that became a typed `Operand` (value · field · now, with an offset). An LLM drafting these has to classify into them, and every extra bucket is another way to be wrong. |
| **No document upload in the MVP** | authoring is natural language plus pickers. Dropping a sample to derive a schema is a second parsing pipeline, and 48 hours does not have room for it. Runtime extraction is untouched — the generated agent still reads the real PDFs, because that is the eval. |
| `constraint_vs_sample` moves to round 2 | it needs real extracted values, which come from the fixture pull rather than an uploaded sample. Ten real documents beat one, and it lands exactly where round 2 is already corpus-grounded. Not cut — rescheduled. |
| A missing sample is not a finding | there is no way for a process owner to supply one, so the finding could never be cleared. A finding nobody can act on is noise, and noise is what makes a PO stop reading findings. |
| **Fill handles description; pickers handle reference** | `effect`, `channel`, `match_condition`, `cardinality`, `payload_fields` come from prose. `criteria`, `outcomes`, `inputs`, `captures`, `scope` come from pickers populated by the board, because anything naming a field path or an outcome **must resolve**. Mapping "all four codes" onto four exact paths is the most error-prone thing extraction could do, and getting it wrong produces a check that tests the wrong thing while looking correct. |
| Every card carries `name`; three fields cut | `event.source`, `event.resulting_state` and `action.completion_criteria` came from the Day 1 PRD and no consumer reads any of them — §28's own test sends them to prose. `check.question` became `name`, so all four cards label themselves the same way. `action.performed_by` survives but is only required for `effect: decide`, where who decides is load-bearing. |
| Steps and things are different node kinds | Event/Action/Check are connected by edges and form the state machine. A Document is referenced, never traversed — so `reachable_from_events`, `terminals` and all six shape matchers operate on steps only. One predicate, applied consistently. |
| Data links write to `inputs`, not to an edge row | `edges` is the transition relation, which is what makes "state machine, not DAG" a fact about the data model. A data link has no outcome, condition or relation; as a row it would be permanently null in half its columns and would force a guard into every shape matcher. The PO still drags a line — it is rendered from config. |

---

## 8. Open items

- [ ] **ASN and Goods checks.** Both appear as eval columns (`Mismatched ASN`,
      `Goods (Failed)`) and nowhere in the SOP. Scope question for Alfonso/Sid —
      not something to guess.
- [ ] **Expected invoice count.** A shipment is complete when every batch has a
      COA, but nothing says whether more invoices are still coming. Likely the
      container ETA is the real deadline, making `timing` relative to an ETA.
- [ ] **`Timing.max_lifetime`** — outer bound so a shipment whose COA never
      arrives does not wait forever.
- [ ] Whether reduction findings (tautology, absorption, contradiction) surface
      as threads or as a separate advisory panel.
- [ ] **Spec / hints split** — confirm with Alfonso/Sid whether their repair loop
      patches extraction hints in code and lets the spec drift, or treats that as
      a spec revision. Changes whether `hints.json` is a separate artifact.
- [ ] **ASN blocking?** MNBU3974949 shows 14 ASN mismatches and status RESOLVED.
      Either ASN mismatch is non-blocking or it was resolved manually.
- [ ] **Who picks tools?** Confirm with Alfonso/Sid that the process owner
      describes a channel and the implementation team binds it, rather than the PO
      selecting integrations on the canvas. If POs pick tools, the whiteboard is
      more technical than assumed.
- [ ] **`mvp connect check`** — verify the Composio entity has a live connection
      for every capability the spec uses, before deploy. One entity for the
      take-home, so nice-to-have; in production it is the real onboarding gate.
- [ ] **Four schema changes from the E2E trace** — `Criterion.produces`,
      `Outcome.priority`, `Derived` correlation keys, and a `join` op. All live
      inside `primitives.config`, so no migration.

---

## 9. The three information sources

They overlap without agreeing, and keeping them separate is the design.

| Source | What it is | What it can tell you |
|---|---|---|
| **SOP** | a written procedure, documented once | a draft process shape. Incomplete by nature — nobody writes down what they do automatically. |
| **Historical output** | the eval set: real shipments, real answers | the true output shape, and where the SOP is silent |
| **Process owner** | the person who runs it | *why*. The only source for rules. |

### What the eval set actually shows

One row per container, not per email:

```
CAAU4056270   Inv 0 ok / 1 fail   CoA 5/5    Goods fail 2   ASN 0    ACTIVE
MNBU3974949   Inv 1 ok / 0 fail   CoA 17/17  Goods fail 0   ASN 14   RESOLVED
176-26926281  Inv 1 ok / 0 fail   CoA 2/2    Goods fail 0   ASN 0    ACTIVE
TTNU8982561   Inv 2 ok / 0 fail   CoA 7/7    Goods fail 0   ASN 0    RESOLVED
```

Four things it reveals that the SOP does not:

1. **The unit is a container.** TTNU8982561 has 2 invoices and 7 COAs in one row. The SOP describes validating one email; nothing says how emails roll up.
2. **Output is counts, not booleans.** Hence `CheckResult` with `total/passed/failed`.
3. **Two checks exist that the SOP never mentions** — Goods and ASN. MNBU3974949 has 14 ASN mismatches and is still RESOLVED, so ASN mismatch is either non-blocking or was resolved manually.
4. **Shipments have a lifecycle.** ACTIVE → RESOLVED. The SOP ends at "report the discrepancy" and never says what closes it.

### The rule

**The eval set's *shape* informs the reviewer's questions. Its *values* are the
repair loop's oracle.** Letting values into elicitation fits the spec to the test
set rather than to the process.

So: "your canvas has no ASN check but every row has an ASN column" is a legitimate
round-2 question. "ASN mismatch is non-blocking" is not something the eval set can
tell you — only the PO can.

---

## 10. Review rounds

A round is a transaction over the board, not a chat turn.

### What changes per round

| Round | Generators active |
|---|---|
| 1 | lint + SOP alignment + graph-enumerated scenarios |
| 2 | corpus-grounded scenarios + reduction findings |
| 3+ | probe taxonomy — the lowest-evidence tier |

Lint runs every round (cheap, and the board changed). The semantic generator
always receives prior threads **including rejected ones**. Cap six questions per
round; overflow carries forward rather than being dropped.

Rounds must differ in kind, not just repeat. Round 1 is grounded in the SOP;
round 2 is grounded in the corpus. That is what makes two rounds worth more than
one long round.

### Status transitions

```
open ──answer──▶ answered ──canvas patch accepted──▶ resolved
 └──reject──▶ rejected                                   │
                                                          │
        reviewer re-runs the originating scenario ────────┘
        still fails → back to open
```

`answered` means the knowledge exists. `resolved` means the canvas reflects it —
and the PO does not get to declare it. The reviewer re-runs the scenario that
produced the thread. That is what makes resolution provable rather than asserted.

`rejected` is not a delete. It compiles into the spec as negative knowledge, so a
later round does not re-ask and codegen knows the case was considered.

### Termination

All three required: zero open threads, zero lint findings, `review_round >= 2`.

Plus a **coverage number** to report alongside: what fraction of scenarios reach a
defined terminal. That is stopping *evidence*, not a gate.

---

## 11. Testing the reviewer

The output is questions, so there is no obvious oracle. Use **ablation**: take the
completed board, delete a known fact, and check whether the reviewer asks for it.

```python
ABLATIONS = [
    ("drop_correlation_key", unset("prealert_received.correlation_key"),
     expect_anchor="prealert_received", expect_category="missing_context"),
    ("drop_deadline",        unset("corrected_docs.timing.deadline"),
     expect_category="undefined_timing"),
    ("unwire_mismatched",    remove_edge("coas_valid", "mismatched_coa"),
     expect_anchor="coas_valid", expect_category="missing_path"),
    ("drop_recipient",       unset("report_coa.recipients"),
     expect_category="missing_context"),
]
```

That gives **recall** as a number: of N ablations, how many were caught. Lint
ablations should be 100% by construction; semantic ones will be lower, and that
gap is honest data worth reporting.

Two more grounded tests:

- **Determinism.** Run twice on the same board. Lint must be identical; semantic
  overlap below ~70% means the prompt is underconstrained.
- **No-regression.** Run against the *completed* board. Near-zero questions
  expected. If a finished board still generates six, the rules are too fussy and
  the PO will stop reading.

Precision proxy: round-1 rejection rate on the real board. Above a third and you
are generating noise.

---

## 12. Repair authority

The line is not spec-vs-code. It is **who owns the decision**.

| Test | Verdict |
|---|---|
| Could two competent people disagree, and would the customer care which you picked? | **spec_gap** — stop, raise a thread |
| Otherwise | **implementation_defect** — patch freely |

Examples:

| Failure | Class |
|---|---|
| batch ids differ by whitespace | defect — normalise |
| attachment nested two levels in a forward | defect — recurse |
| extraction reads the wrong page region | defect — fix the hint in code |
| COA arrives whose batch is on no invoice: ignore or flag? | **spec_gap** |
| an invoice line has no ANDA: block or warn? | **spec_gap** |

`spec_gap` is rare and should stay rare. Sending too much back to the whiteboard
gives up the whole advantage of code being the patch surface.

Enforced structurally:
`check (classification <> 'spec_gap' or raised_thread_id is not null)`.

### The drift problem

Extraction hints live in the frozen spec (`instructions`), but the repair loop
edits code. A patched hint drifts the running code from the approved spec.

Resolution: split the artifact.

```
agents/<slug>/spec.lock.json    immutable · checksummed · the PO's contract
agents/<slug>/hints.json        mutable · repair-owned · extraction guidance
```

Conformance checks the first and ignores the second. **Open question for
Alfonso/Sid** — worth confirming they draw the line the same way.

---

## 13. Human in the loop

Two queues, not a decision. The classifier routes; the human reviews.

```
build 3 · 24/28
  ├─ 3 patches proposed   → read diff, accept or reject
  └─ 1 spec gap           → thread opened on board, PO required
```

CLI is the control surface; every UI button calls the same command.

```bash
mvp spec freeze --board <id>
mvp spec check --spec 1              # sufficiency: hand it to a fresh agent cold
mvp codegen run --spec 1
mvp eval sweep --build 2 --split train
mvp repair once --build 2            # ONE bucket, propose, gate, print diff, STOP
mvp repair loop --build 2 --budget 6
mvp repair show --repair <id>
mvp repair accept --repair <id>      # manual override of the gate
mvp deploy --build 5 --stage shadow
mvp deploy stop --build 5            # emergency stop
```

`repair once` is the demo mode and the honest one: it stops at
`status = 'proposed'` so a human reads the diff. The brief explicitly blesses
this — it says not to force every step to be autonomous.

### Gates

| Gate | Who decides | Blocking |
|---|---|---|
| Freeze the spec | PO — no open threads, lint clean | yes |
| Accept a generated build | human — compiles, baseline sweep | yes |
| Accept a patch | the gate; human may override | no, unless `--manual` |
| Promote to prod | human | yes |

The repair gate is the only automatic one, and it can only **reject**. A human is
required to override, never to approve.

### Skills

Both agents run as checked-in skill files, which is how the brief suggests using
Codex/Claude Code.

```
.claude/skills/repair-agent/SKILL.md
  1. Read the failure bundle: expected vs actual, trajectory, primitive_key.
  2. Open ONLY the file named by primitive_key.
  3. Classify: implementation detail (fix) vs business decision (stop, report).
  4. Check repairs history for this signature — do not retry a rejected approach.
  5. Propose the minimal diff. One file. No new dependencies.
  6. Run the target case, then the regression suite.
  7. Emit a summary: what changed, why, what it fixes.

.claude/skills/spec-to-agent/SKILL.md
  file layout · runtime contract · one primitive per module ·
  workflow shell is templated, never generated
```

Python owns orchestration, budgets, the gate and the database writes. The skill
owns how the agent reasons about a patch.

---

## 14. UI patterns

### Palette drag-drop

```tsx
// palette item
onDragStart={(e) => e.dataTransfer.setData('primitive_type', 'check')}

// canvas
onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; }}
onDrop={(e) => {
  const type = e.dataTransfer.getData('primitive_type');
  const pos = rf.screenToFlowPosition({ x: e.clientX, y: e.clientY });
  createPrimitive({ primitive_type: type, ...pos });    // POST, optimistic
}}
```

### One handle per outcome

The detail that makes unwired outcomes visible on the canvas rather than only in a
lint panel. Three outcomes, three handles; wire two and the third sits unconnected.

```tsx
{outcomes.map((o, i) => (
  <Handle key={o.name} type="source" position={Position.Right} id={o.name}
          style={{ top: 34 + i * 20 }} />
))}
```

### Two persistence paths

```tsx
// layout — debounced, writes boards.layout only. Cosmetic.
const onNodesChange = (changes) => {
  setNodes(applyNodeChanges(changes, nodes));
  const moves = changes.filter(c => c.type === 'position' && !c.dragging);
  if (moves.length) debouncedSaveLayout(moves);
};

// structure — immediate, re-lints. Changes the process.
const onConnect = (c) => createEdge({
  from_key: c.source, to_key: c.target,
  on_outcomes: c.sourceHandle ? [c.sourceHandle] : [],
  relation: inferRelation(c),
});
```

### Connection validation

Reject invalid connections rather than reporting them afterwards.

```tsx
isValidConnection={(c) => {
  const src = nodeByKey(c.source);
  if (src.primitive_type === 'check' && !c.sourceHandle) return false;
  if (src.config.is_terminal) return false;
  return true;
}}
```

### Inspector panel

`onSelectionChange` yields nodes and edges together, so one handler drives both.
Two tabs:

- **Config** — form generated from `openapi.json`, so adding a field to
  `CheckConfig` makes it appear in the UI with no frontend change.
- **Threads** — comments anchored here, with status pills.

**Field pickers, never text inputs.** Anywhere config takes a `FieldRef`, render a
dropdown populated from the board's documents. That is what keeps Checks from
referencing fields that do not exist, and it is what makes typed references work
in practice rather than in principle.

### Pages

`Whiteboard` · `Executions` · `Evals` · `Metrics` · `Human in Loop`

---

## 15. Coding strategy per phase

| Phase | Shape | Tricky part | Verify before moving on | Who writes it |
|---|---|---|---|---|
| **Domain** | pure Pydantic, no I/O | `.unmet()` on each config — every lint rule derives from it | seed board validates; `reachable_from_events()` correct on a 3-node fixture | **you, by hand** |
| **Compiler** | pure functions over `Board` | scope chain ordering (broad→narrow); `document_field` inherits onto every primitive whose `inputs` reference that doc — a different traversal | fixture with one assertion per level: `coas_valid` gets 3, `report_discrepancy` gets 1 | you write `base.py` + one rule, agent does the rest |
| **Runtime** | the contract generated code imports | workflow/activity boundary — I/O in activities only, or replay breaks under crash conditions | hand-write a toy agent against `AgentContext`. If that is awkward, the contract is wrong | **you, by hand** (~200 lines) |
| **Codegen** | deterministic scaffold + per-primitive LLM | the split. `workflow.py` is a pure function of the graph — template it. Only leaf logic goes to the model | generate twice from one spec and diff: scaffold byte-identical, only bodies vary | agent writes it; you review the first `workflow.py` line by line |
| **Healing** | a loop with a gate | `localize` maps `primitive_key` → file via the `[from primitive X]` header codegen wrote. Regression set is *previously-passing*, recomputed each iteration | seed a broken file, run one cycle, confirm the gate rejects an overbroad patch — **build this test first, it is the demo** | you write `gate.py`, agent does the rest |
| **Reviewer** | 4 generators, 1 filter, 1 LLM call | `dryrun` is a tiny interpreter — the one place you interpret rather than compile. Keep it dumb | seeded board + "COA missing" scenario must dead-end at `coas_valid` | agent, one contract per file. Build `dryrun` before `semantic` |

### Vertical slice first

Take `coas_valid` alone, all the way through: domain type → lint finding →
assertion → frozen spec entry → generated `checks/coas_valid.py` → one eval case →
one failure → one patch. If that works end to end, every interface is real and
widening is mechanical.

---

## 16. Temporal integration

### Why it is in the stack

One reason: **resumption**. Documents arrive before the container and in pieces —
invoice Tuesday, corrected COA Thursday. Without durable execution you hand-roll
partial-state persistence, inbox polling, matching new mail to an in-flight
shipment, and surviving a deploy mid-wait.

### Mapping

| Primitive field | Temporal construct |
|---|---|
| `Event.correlation_key` | `workflow_id` — `prealert-{shipment_no}` |
| `Event.match_condition` | filter predicate in the listener, **outside** the workflow |
| `Event.timing.mode = on_arrival` | `signal_with_start` |
| `Event.timing.mode = scheduled` | Temporal Schedule |
| `Event.timing.deadline` | `wait_condition(..., timeout=…)` |
| `Event.timing.max_lifetime` | `workflow_execution_timeout` |
| `Action` | activity — retried per policy |
| `Action.idempotency_key` | dedupe key so a retried send does not double-email |
| `Check` | pure function **inside** workflow code |
| `Outcome` | workflow return value |

### The determinism rule

**Anything touching the world is an activity. Anything deciding is workflow code.**
No `datetime.now()`, no `random`, no I/O above the activity boundary — Temporal
replays workflow code from history to recover, and nondeterminism breaks recovery
in ways that only surface under crash conditions.

This maps cleanly onto the primitives, which is a good sign the vocabulary is right.

### Listener → workflow

```python
# agents/<slug>/trigger.py            [from primitive prealert_received]
async def poll(client: Client, ctx: AgentContext):
    for msg in await ctx.tools.gmail.fetch_unread():
        if not matches(msg, MATCH_CONDITION):
            continue
        shipment = extract_correlation_key(msg)          # Event.correlation_key
        await client.start_workflow(
            PreAlertValidation.run,
            args=[shipment],
            id=f"prealert-{shipment}",                    # durability lives here
            task_queue="meridian",
            start_signal="documents_arrived",
            start_signal_args=[attachments(msg)],
        )
```

`start_workflow` with `start_signal` is signal-with-start: creates the instance if
`prealert-{shipment}` does not exist, signals it if it does. That single call is
why a corrected COA on Thursday reaches the instance started Tuesday.

### Wrapping for the eval loop

Business logic lives in a plain injected-dependency function; the Temporal
workflow is a thin shell around it.

```python
# runtime/temporal.py
async def validate(inv, coas, ctx: AgentContext) -> Outcome: ...   # plain, testable

@workflow.defn
class PreAlertValidation:
    @workflow.run
    async def run(self, shipment: str) -> Outcome:
        ...                                     # waits, signals, activities
        return await validate(inv, coas, ctx)
```

The harness calls `validate()` directly with fixtures — seconds, deterministic,
dozens of iterations a minute. Temporal appears only in the demo path.

### Search attributes

Register `cycleId`, `buildId`, `specVersion` so production runs filter in
Temporal's UI by the same correlation key the dashboard uses. Costs nothing,
makes prod debugging survivable.

### Local dev

Temporal dev server via `docker-compose.yml`. Worker is `meridian/worker`,
deployed to Railway as its own process. **No LLM keys in Temporal's config** — the
model call lives inside an activity, in the worker's environment.

---

## 17. Composio integration

One adapter module, imported by activities, never called from workflow code.

```python
# runtime/tools/gmail.py
class GmailTool:
    def __init__(self, client: Composio, entity_id: str, mode: str = "live"):
        self.c, self.entity, self.mode = client, entity_id, mode

    async def fetch_unread(self) -> list[Message]:
        r = await self.c.execute("GMAIL_FETCH_EMAILS", entity_id=self.entity, ...)
        return [Message.model_validate(m) for m in r["messages"]]

    async def send(self, to: str, subject: str, body: str, idem: str) -> dict:
        if self.mode == "shadow":                      # record, do not send
            return {"shadowed": True, "to": to, "subject": subject}
        return await self.c.execute("GMAIL_SEND_EMAIL", entity_id=self.entity, ...)
```

Exact action names move — check Composio's current docs rather than trusting these.

### Rules

- **Composio only inside activities.** It is I/O; the determinism rule forbids it
  above the boundary.
- **Exactly one write path** (`send`). That is what makes shadow mode a flag
  rather than a refactor, and it is the only place an `idempotency_key` matters.
- **Tools are declared, not imported.** `ActionConfig.capabilities` lists tool
  keys; `tools` table maps `email.send → GMAIL_SEND_EMAIL`; lint verifies every
  capability resolves. Generated code never hardcodes a Composio action name.
- **Injected via `AgentContext`.** The harness swaps in `FixtureGmailTool` reading
  from `fixtures/emails/` — same interface, no network, no code change in the
  generated agent.

### The one-time pull

Pull the test inbox **once**, snapshot to `fixtures/emails/`, author expected
outputs per shipment. Then the repair loop runs against fixtures. Live Composio
stays wired for the demo video.

```bash
mvp fixtures pull --inbox ishaan@usemeridian.io   # once
mvp eval sweep --build 2                          # fixtures, offline, fast
```

Deterministic, and a failing case fails for a logic reason rather than because
Gmail was slow.

---

## 18. FastAPI backend

### Split

`meridian/api` is a **thin trigger layer**. All logic lives in sibling modules as
plain functions, so the CLI and the tests call the same code with no HTTP.

Two response shapes:

| Kind | Behaviour |
|---|---|
| CRUD (board, threads, documents) | synchronous, returns the row |
| Pipeline (freeze, codegen, sweep, repair) | returns `{cycle_id}` immediately, work runs in the background |

The UI never waits on a pipeline call. It subscribes to `events` filtered on
`cycle_id` and watches progress arrive.

### Routes

```
POST   /boards/{id}/primitives          create · PATCH · DELETE
POST   /boards/{id}/edges
PATCH  /boards/{id}/layout              debounced from drag, layout only
GET    /boards/{id}                     board + primitives + edges + threads
GET    /boards/{id}/lint                findings, live, drives the footer count

GET    /boards/{id}/threads?status=
POST   /threads/{id}/messages           PO answers
PATCH  /threads/{id}                    status transition
POST   /threads/{id}/patch/accept       apply a proposed canvas patch

POST   /boards/{id}/documents           upload → extract → confirm fields

POST   /pipeline/review    {board_id}          → {cycle_id}
POST   /pipeline/freeze    {board_id}          → spec_id | 422 with findings
POST   /pipeline/codegen   {spec_id}           → {cycle_id}
POST   /pipeline/sweep     {build_id, split}   → {cycle_id}
POST   /pipeline/repair    {build_id, mode}    mode: once | loop
POST   /repairs/{id}/accept
POST   /pipeline/deploy    {build_id, stage}
POST   /pipeline/stop      {build_id}          emergency stop

GET    /metrics/reliability?spec_id=
GET    /metrics/cycle/{cycle_id}
```

**`POST /pipeline/freeze` returns 422 with the lint findings** when the board is
incomplete. The completeness contract is enforced at the boundary, not in the UI,
so it holds no matter who calls it.

### Background jobs

```python
@router.post("/pipeline/repair")
async def repair(body: RepairRequest, bg: BackgroundTasks):
    cycle_id = uuid4()
    bg.add_task(healing.run, body.build_id, body.mode, cycle_id)
    return {"cycle_id": cycle_id}
```

`BackgroundTasks` is enough at this scale. No Celery, no Redis — a repair cycle is
minutes and one at a time.

### Types to the frontend

```bash
cd ui && npx openapi-typescript http://localhost:8000/openapi.json -o src/lib/schema.d.ts
```

Pydantic models become the single source of truth for both sides. The UI cannot
drift from the API without the TypeScript compiler saying so — which is also how
the inspector form stays in sync when you add a field to `CheckConfig`.

### Errors

One exception handler mapping domain errors to status codes:
`Incomplete → 422` with findings, `NotFound → 404`, `ConflictingState → 409`
(freezing an already-submitted board).

---

## 19. React UI

### Stack

Vite + React 18 + TypeScript · `@xyflow/react` v12 · Recharts · `supabase-js`
(one subscription) · react-router.

### Structure

```
ui/src/
├── api/client.ts          one fetch wrapper, base URL from env
├── api/schema.d.ts        generated — never hand-edited
├── realtime.ts            supabase-js, events only
├── canvas/
│   ├── nodes/             EventNode · ActionNode · CheckNode
│   ├── edges/             NormalEdge · ExceptionEdge · RepeatEdge
│   └── validation.ts      isValidConnection
├── panels/                Inspector · Threads · LintFooter
└── pages/                 Whiteboard · Executions · Evals · Metrics · HumanInLoop
```

### Data flow

```
CRUD          → FastAPI, optimistic update, rollback on error
Pipeline      → FastAPI returns cycle_id, UI does NOT wait
Progress      → Supabase Realtime on events, filtered by cycle_id
```

```ts
supabase.channel('cycle')
  .on('postgres_changes',
      { event: 'INSERT', schema: 'public', table: 'events',
        filter: `cycle_id=eq.${cycleId}` },
      ({ new: e }) => appendToTimeline(e))
  .subscribe();
```

Same path whether the trigger was a button or your terminal — the UI is a viewer
of a system that works headlessly, not a system that only works through the UI.

### Edge rendering by relation

- `normal` — solid grey smoothstep
- `exception` — red dashed
- `repeat` — smoothstep with a wide offset so a back-edge routes *around* the
  graph rather than through it. This is the one that visually proves it is not a DAG.

### Pages

| Page | Content |
|---|---|
| **Whiteboard** | canvas · palette · inspector · lint footer · Submit gated on three conditions |
| **Executions** | run table, trajectory, link out to Temporal for prod runs |
| **Evals** | eval cases, per-case pass/fail by build |
| **Metrics** | reliability curve · live cycle timeline · hotspots by primitive · cost and gate summary |
| **Human in Loop** | proposed patches with diffs · open spec-gap threads |

### Build order for the frontend

Canvas first, but only what Submit depends on: place primitives, connect edges,
edit config, see lint findings, answer threads. Skip minimap, undo, multi-select,
drag-to-resize.

Then the **cycle timeline** — it is the demo. Then the **spec viewer**, because
showing a primitive with its inherited and local assertions is the clearest
evidence the scoped-context mechanism works.

`Executions` and `Evals` last. If they slip, CLI output covers them in the video.

---

## 20. Curiosity: hypothesis-driven review

The reviewer does not ask "what is missing." It **forms hypotheses about how this
process could behave and tests them against the board.** Gap-finding becomes
falsification rather than recall, which is what makes it reproducible.

### Why open questions fail

Klievtsova et al. measured LLM task extraction against ground-truth process
models at roughly 74%, systematically finding about a third fewer tasks than
existed. A model asked to volunteer gaps under-extracts. A model asked to
instantiate a bounded template against a specific primitive does not.

**Enumerate the space, then instantiate. Never recall.**

### Three hypothesis classes, deliberately mixed

| Class | Source | Example on the pre-alert board |
|---|---|---|
| **Happy path** | graph enumeration | invoice + a COA for every batch → reaches `documentation_validated` |
| **Non-happy path** | every declared outcome, every exception edge | one batch has no COA → `missing_coa` |
| **Edge case** | probe taxonomy × primitive type | a COA arrives whose batch is on no invoice |

Round 1 leans happy/non-happy — they are cheap and derived from what the PO drew.
Round 2 leans edge case, grounded in corpus shape.

### The probe taxonomy

Four failure dimensions, crossed with primitive type. This is the bounded space
the model instantiates against.

| Dimension | Probes |
|---|---|
| **Timing** | early · late · never · out of order |
| **Cardinality** | zero · one · many · duplicate |
| **Quality** | malformed · partial · ambiguous · wrong thing entirely |
| **Authority** | who overrides · who is notified · who is blocked |

| Primitive | Instantiations |
|---|---|
| Event | fires twice for the same shipment · fires for something unrelated · never fires |
| Check | input present but malformed · partially satisfiable · who overrides a failure |
| Action | recipient unavailable · send fails · called twice |
| Document | arrives late · never · twice · unreadable · more than one · wrong document |

Cardinality and timing dominate because most real process bugs are one of: it
arrived out of order, there were N instead of one, or it never came.

### Generation is LLM, categories are not

```python
def generate(ctx: ReviewContext) -> list[Scenario]:
    skeleton  = enumerate_from_graph(ctx.board)     # deterministic coverage floor
    realistic = llm_scenarios(ctx, skeleton)        # SOP + samples + corpus shape
    return dedup(skeleton + realistic)
```

Enumeration guarantees you never miss a declared outcome. The LLM adds the ones
requiring domain knowledge — *four COAs for three batches*, *a corrected invoice
after the discrepancy was already reported*. Its job is instantiating templates
with plausible values for **this** domain, not inventing categories.

Skip any probe the graph already answers. A probe fires only when its dry-run
produces a dead end or an undefined branch.

### Honest limit, worth stating

Dry-running tells you a scenario has no **path**. It cannot tell you the path it
took was **wrong** — a scenario can reach a terminal that is the incorrect
terminal and the graph looks fine. That class surfaces only when the corpus
contradicts the drawing, or when the PO reads the trace and says "no, that one
should go to exception."

**Structural simulation catches missing paths; only real data catches wrong ones.**

### Research to cite

- **Kourani et al., ProMoAI (IJCAI 2024)** — emits POWL rather than BPMN because
  BPMN admits deadlocks and unreachable parts while POWL structurally cannot.
  Sound-by-construction, applied here to a human authoring surface.
- **Klievtsova et al.** — the 74% extraction figure. Why structural detection is
  deterministic and the LLM handles only semantics.
- **Kourani et al., arXiv 2412.00023** — LLM process-modeling benchmark with
  ground-truth models, simulated event logs, conformance-based scoring and an
  explicit self-improvement analysis.
- **Knuth 1968 / Hedin 2000** — attribute grammars and reference attribute
  grammars. The scope chain is a reference attribute grammar: containment-scoped
  assertions flow down the group hierarchy, document-field constraints flow along
  `inputs` references.

---

## 21. Self-healing as a human-run terminal loop

Confirmed direction: the repair step is **not** an autonomous background service.
It is a skill file the engineer runs in their terminal against the generated
directory, and the platform's job is to make the failure legible enough to paste.

### The loop

```
1.  mvp eval sweep --build 3
2.  Observability panel shows the failure bundle, copy-ready
3.  Engineer pastes into Codex / Claude Code with the repair skill loaded
4.  Agent edits agents/<slug>/ directly
5.  mvp build register --from-git          → build 4
6.  mvp eval sweep --build 4               → new row on the curve
7.  repeat, or stop
```

Step 2 is the product. Everything else is a terminal.

### The failure bundle

One copy button produces a self-contained block. It must be pasteable with **no
further lookup**:

```
BUILD 3 · sweep train · 24/28

FAILING SIGNATURE  coas_valid :: output_diff :: coa_count_mismatch   (4 cases)

FILE     agents/inbound_pre_alert/checks/coas_valid.py
SPEC     spec.lock.json § primitives.coas_valid

CASE     CAAU4056270
  input          5 COAs, invoice lists 5 batches
  expected       {outcome: pass, total: 5, passed: 5, failed: 0}
  actual         {outcome: missing_coa, total: 5, passed: 3, failed: 2}

TRACE
  step 3  extract_commercial_invoice  ok    batch_nos: [7 chars × 5]
  step 4  extract_certificate_...     ok    5 documents
  step 5  coas_valid                  FAIL  2 unmatched: ['UAC25022 ', 'uac25019']

SPEC CONTEXT FOR coas_valid
  [terminology] Batch association is by product code on the COA, not page order.
  [exception]   A COA whose batch is on no line item belongs to another shipment.
  [constraint]  commercial_invoice.batch_nos matches ^\d{7}$

REPAIR HISTORY FOR THIS SIGNATURE
  none
```

The unmatched values showing trailing whitespace and case variance is the whole
diagnosis — a human or an agent solves it in one read. **That legibility is the
deliverable, not the automation.**

### Classify before pasting

The panel shows the classification so the engineer knows which queue they are in.

- **`implementation_defect`** → paste and repair. Whitespace, nesting, a wrong page
  region. Nobody approved these; the loop owns them.
- **`spec_gap`** → do not paste. No patch is correct because no rule decides the
  answer. Raises a thread on the board; the PO answers; re-freeze as v2.

Heuristic: if the expected output requires a rule that appears in no assertion and
no primitive config, **and the choice is genuinely arbitrary from the code's
perspective**, it is a spec gap. Otherwise it is a defect.

### Registering a build

The agent edits files and commits. The platform notices:

```bash
mvp build register --from-git --spec 1
# reads HEAD, creates agent_builds row with parent = current build,
# created_by = 'repair', source_ref = 'agents/<slug>@<sha>'
```

Manual repairs are first-class in the schema — `created_by` already allows
`'human'`. The curve does not care who wrote the patch.

### Observability requirements this creates

| Surface | Purpose |
|---|---|
| Failure bundle, one copy button | the paste target. Zero further lookup. |
| Reliability curve by build | did the last repair help |
| Hotspots by primitive | which file to open next |
| Repair history per signature | do not retry an approach that already failed |
| Diff view per build | what changed between 3 and 4 |
| Regression detail | which previously-passing cases a build broke |

Repair history matters more here than in an autonomous loop: without it the
engineer re-tries the same failed approach across sessions.

---

## 22. Validation and error handling — the FDE brief

The system's job is to close the gap between the SOP and a production agent
without errors reaching production. Three layers, each catching a different class.

### Layer 1 — before the freeze

Structural, deterministic, blocking.

| Check | Fires when |
|---|---|
| completeness contracts | any required config field unset |
| reference resolution | a Check reads a document not produced upstream |
| tool resolution | `capabilities` names a key absent from `tools` |
| constraint vs sample | PO says 7 digits, a sample document has 9 |
| reachability | a primitive no Event can reach |
| outcome coverage | a declared outcome with no edge |
| **conflicting assertions** | two constraints on one field disagree |

The last two are the ones that catch a PO being wrong rather than incomplete. The
constraint-vs-sample check is the highest-value one and it is cheap: you already
have `documents.sample_extracted`.

```
PO: "batch numbers are 7 digits"
→ validate against sample_extracted
→ 2 of 9 sampled batches are 8 characters
→ reviewer: "Two batches in your sample are 8 characters — UAC25022 and
   UAC25019. Is the rule 7 digits after the prefix, or does it vary?"
```

That is the loop catching an incorrect answer, not just a missing one. Worth
demoing.

### Layer 2 — the ruleset validation pass

Before freeze, a dedicated pass over assertions only. Not gap-finding — **coherence
checking**.

```
for each pair of assertions on overlapping anchors:
    do they contradict?
for each Check:
    is any criterion unreachable given upstream constraints?     (tautology)
    is any criterion implied by an upstream Check?               (absorption)
    can all criteria be satisfied simultaneously?                (contradiction)
for each constraint:
    does it hold against every sample?
```

Boolean reduction applied to the ruleset. These findings are **provable**, which is
what makes them safe to state confidently — "this Check can never fail" is a fact,
not an opinion.

Surface as `category = 'redundancy'` threads with `severity = 'minor'`, so they
rank below genuine gaps.

### Layer 3 — runtime

| Class | Handling |
|---|---|
| Retryable | tool timeout, rate limit → Temporal retry policy |
| Terminal | malformed document, unreadable scan → route to exception |
| NeedsHuman | ambiguous, low extraction confidence → HITL task |
| Conformance | build no longer satisfies its spec → block promotion |

`NeedsHuman` is the important one for the FDE framing. An agent that escalates on
low confidence is safer than one that guesses, and the escalation becomes a new
eval case — that is the loop closing from production back to the suite.

### The FDE test

For any failure, ask: **would a forward-deployed engineer have caught this before
production?**

- Missing recipient → yes, in the interview → **Layer 1**
- Contradictory rules → yes, reading the notes → **Layer 2**
- Unreadable scan → no, only in production → **Layer 3**

Anything a careful human would have caught in week one belongs in Layer 1 or 2.
That is the design target.

---

## 23. Document extraction, Temporal and Composio

### The pipeline

```
Composio fetch  →  attachment bytes  →  classify  →  extract  →  validate  →  store
   (activity)        (Storage)          (LLM)       (LLM)      (schema)    (Postgres)
```

Every stage is a Temporal **activity**. None of it belongs in workflow code.

### OCR: mostly not needed

Native PDFs go straight to the model as a document block — no OCR layer. The
sample invoice and COA are native. OCR is the **fallback** for scans, not the main
path.

```python
@activity.defn
async def extract_document(ref: str, doc_key: str, schema: dict) -> dict:
    pdf = await storage.download(ref)
    if not has_text_layer(pdf):                     # scanned
        pdf = await ocr(pdf)
    resp = await anthropic.messages.create(
        model="claude-sonnet-4-6",
        temperature=0,                              # reproducibility
        messages=[{"role": "user", "content": [
            {"type": "document",
             "source": {"type": "base64", "media_type": "application/pdf",
                        "data": b64(pdf)}},
            {"type": "text", "text": prompt_for(doc_key)},
        ]}],
        tools=[{"name": "emit", "input_schema": schema}],   # from spec documents
        tool_choice={"type": "tool", "name": "emit"},
    )
    return validate_against(schema, tool_input(resp))
```

The schema comes from `spec.documents[key].fields`, which came from the PO
confirming extracted fields, which came from a sample. Full circle.

### What Temporal contributes

| Concern | Temporal |
|---|---|
| Extraction times out | activity retry policy, no bespoke code |
| Model returns invalid JSON | activity fails, retries, then surfaces |
| Worker dies mid-shipment | history replay resumes; completed extractions are not redone |
| 17 COAs on one shipment | `asyncio.gather` over activities, each retried independently |
| Corrected document Thursday | signal wakes the instance; only the new document is extracted |

That last row is the real value. Without durable execution you re-extract
everything on every wake, or you hand-roll a cache.

**Determinism:** the model call must be in an activity. Put it in workflow code and
replay produces different results, silently breaking recovery.

### What Composio contributes

Gmail only. Fetch, download attachments, send. One adapter, one write path
(`send`), injected via `AgentContext` so the harness swaps in a fixture reader.

```python
@activity.defn
async def fetch_shipment_mail(shipment: str) -> list[Attachment]:
    msgs = await composio.execute("GMAIL_FETCH_EMAILS", query=f"subject:{shipment}")
    return [await download(a) for m in msgs for a in m.attachments]
```

Verify current action names against Composio's docs — they move.

### Classification before extraction

You do not know which schema to apply until you have looked at the document.
Cheap classify, then dispatch — **not** an agentic tool-choice loop:

```python
kind = await classify_document(ref)        # returns a fixed enum
match kind:
    case "commercial_invoice":        return await extract(ref, "commercial_invoice")
    case "certificate_of_analysis":   return await extract(ref, "certificate_of_analysis")
    case _:                           return {"kind": "unrecognized", "ref": ref}
```

Model picks from a closed set; the dispatch is code. `unrecognized` keeps the
shipment waiting rather than failing, and is itself a reviewer signal — *"1 of 10
emails has an attachment matching no known document type."*

### Caching

Key extraction on `(storage_path, doc_key, schema_hash)`. A shipment that wakes
five times must not re-extract the same COA five times, and the schema hash means
a spec change correctly invalidates.

---

## 24. Tool registry and bindings

### Three layers

```
PO authors        channel: "email"                 their vocabulary
bindings map      email → email.send               per customer · YAML in repo
registry maps     email.send → GMAIL_SEND_EMAIL    global · seeded table
```

**The PO never sees a tool.** They say "we email the supervisor" or "we post it
into SAP". `channel` is a small enum in their language; `capabilities` is derived,
not authored.

```python
effect:  Literal["notify", "record", "lookup", "noop"]
channel: Literal["email", "sms", "phone", "queue"] | None   # only when notify
system:  str | None                                          # only when record/lookup
timeout: str | None                                          # only when lookup
on_failure: Literal["fail","wait","skip"] | None             # only when lookup
```

**Two questions, not one axis.** *Does it notify a person* and *does it write to
software* are independent, so they are separate fields. `channel` is an enum
because "email"/"Email"/"Outlook" all mean one thing and none of them join.
`system` is **free text** because the answer is genuinely per customer and
unguessable — the PO types "Aurologistics WMS", not a category. An enum there
would force them to classify their own software into our taxonomy.

A rejected earlier draft used `channel: system_of_record`. That is our jargon, not
theirs, and no relabelling fixes it — the concept itself belongs to the data
model. **Enum values may be technical only when the label is a word the PO would
actually say.** "Email" passes; "system of record" does not.

### What the PO actually sees

One dropdown, then a follow-up that depends on the answer. Nothing technical.

> **What does this step do?**
> `Tells someone` · `Writes it down somewhere` · `Looks something up` · `Nothing, it's just the end`

| Answer | Follow-up | Field |
|---|---|---|
| Tells someone | **How?** `Email` · `Text message` · `Phone call` · `Puts it in a work queue` | `channel` — closed set |
| Writes it down | **Which system?** free text | `system` |
| Looks something up | **Where?** free text · **What if it doesn't come back?** | `system`, `timeout`, `on_failure` |
| Nothing | — | `effect: noop`, `is_terminal` |

Four options because data moves four ways at a step: out to a person, out to a
system, **in from a system**, or nowhere. `lookup` is the one that was missing —
see §25.

Only `lookup` asks "what if it doesn't come back", because only a lookup can time
out.

**Closed set where the space is universal; free text where the answer is
per-customer and unguessable.** How you contact a person is a small universal
space — enum. What software a company runs is not — free text. An enum there would
force the PO to classify their own systems into our taxonomy, which is exactly what
broke the earlier `system_of_record` draft.

### Detail is an asset, not a problem

The free-text fields should invite elaboration. A PO writing this:

```
Where?               "State medical board — Nursys for most states, but
                      California and Texas have their own portals. We search
                      by licence number and the licensee's last name."
What if no response? "Give it 5 days, then it goes to the credentialing manager."
```

has just written the **binding brief**. One capability, three backends, state
routing is part of it, a single Composio action will not cover it. Without that
sentence you would resolve `board.lookup` to one thing and find the exception in
production.

It also generates its own review question:

> *"You mentioned California and Texas have their own portals. Is the search the
> same on those, or do they need different information?"*

`system` carries the short name for the binding lookup; `instructions` carries the
elaboration and compiles into the primitive's context, where codegen reads it as
prose. **Structured fields exist to make linting possible; everything else rides in
prose, because the consumer is an LLM, not a parser.**

### Resolving an unknown system

```bash
$ mvp bind check --spec 3
⚠ 1 unbound
   system:"state medical board" · effect=lookup · used by verify_license

$ mvp bind resolve --spec 3
system:"state medical board" · lookup
  inputs: license_number, state   produces: board_record

searching composio…
  no toolkit matches "state medical board"
  closest: verification.com (identity) · Checkr (background checks)

→ [1] use Checkr  [2] write an adapter  [3] make this a human step
```

Pick 2 → write `runtime/tools/nursys.py`, add a registry row, bind it. Twenty
minutes, once. The next staffing customer's binding is one line.

**The model narrows, the human commits.** Codegen must not resolve tools itself:
it would be making a business decision nobody approved, it would break the
pre-codegen gate that catches unresolvable capabilities cleanly, and it would
re-resolve per build — possibly differently — instead of once per customer.

### How a capability reaches a provider

The registry row *is* the mapping.

```sql
tools
  key             provider    action                    input_schema
  ─────────────────────────────────────────────────────────────────
  email.send      composio    GMAIL_SEND_EMAIL          {to, subject, body}
  board.lookup    composio    NURSYS_VERIFY_LICENSE     {license_number, state}
  board.lookup    internal    nursys_scrape             {license_number, state}
```

```python
async def call(key: str, args: dict, entity: str):
    tool = registry[key]
    if tool.provider == "composio":
        return await composio.execute(tool.action, entity_id=entity, **args)
    return await INTERNAL[tool.action](**args)
```

Generated code says `ctx.tools.call("board.lookup", {...})` and never learns what
is behind it. Swapping Composio for an adapter changes one row.

`input_schema` does two jobs: it is the argument shape codegen sees when writing
the call site, and it is what lint checks the primitive's `inputs` against — a
capability needing `state` that no upstream primitive supplies is a finding before
anything is generated.

### Scaling, honestly

| Amortises | Does not |
|---|---|
| capabilities are coarser than toolkits — Gmail and Outlook both bind `email.send` | a homegrown system nobody else uses is a bespoke adapter, full stop |
| resolution is once per customer per system, reused by every later workflow | — |
| the registry compounds within a vertical — the fifth pharma distributor costs zero adapters | — |

**Capability resolution amortises within a vertical and does not amortise across
custom internal systems.** That is the integration cost, and it is the real
per-customer number to watch.

### Two failure modes, two owners

| Failure | Meaning | Fix |
|---|---|---|
| **unbound** | spec says `channel: system_of_record`; bindings file has no entry | add one line to the YAML |
| **unresolvable** | binding names `erp.post`; registry has no such key | registry row (Composio has it) → adapter in `meridian/runtime/tools/` (it doesn't) → **back to the PO** (can't be automated) |

That last escalation matters: **a tool gap can become a spec gap.** If the
capability genuinely does not exist, the process model assumed automation that
isn't available, and that is a question for the process owner, not an engineering
problem. Same path as a repair-time spec gap — raise a thread, re-freeze.

### Two gates, two questions

```bash
mvp spec freeze --board 1     # "is the process fully specified?"     PO owns
mvp bind check  --spec 1      # "can we actually build it?"           FDE owns
mvp codegen run --spec 1      # blocked until bindings resolve
```

The binding check is **not** a board lint rule. The PO cannot fix an unbound
channel, so surfacing it on the canvas would be noise.

### What goes in the registry

Derived from the Actions and Events across processes, not speculated.

| Spec element | Capability |
|---|---|
| `Event.channel: email` | `gmail.fetch` |
| `Check.inputs` → a Document | `doc.extract` |
| `Action.effect: notify` + `channel: email` | `email.send` |
| attachment handling | `storage.put` |

Four rows for the pre-alert build. A capability earns a row when a primitive
declares it **and it cannot be generated**. Not "what might we need" — that is how
registries become junk drawers.

Credentialing would add `web.lookup`. Purchase approval would add `erp.post`.
One row each.

### Why stored, not dynamic

The registry exists to be a **closed set the spec can be validated against**. If
tools were created on demand, `capability_unresolvable` could never fire — any
capability would spawn a row and resolve trivially. The rule only has meaning
because the set is finite and pre-declared.

This is also the PRD's "tool inventory": *which external capabilities are actually
available to implementation*. Available implies bounded.

### Codegen writes tools — for one class only

| Class | Who makes it |
|---|---|
| internal logic — predicates, comparisons, computations | **codegen writes it.** No registry, no credentials. `checks/coas_valid.py` is already this. |
| external capability — anything needing an API key or provider action | **registry required.** Cannot be conjured; there is a mailbox, an auth token, a rate limit. |

The registry is not gatekeeping code generation. It gatekeeps **things that cannot
be generated**. The invariant: *codegen writes logic freely and reaches the world
only through the registry* — enforced by path confinement and an import allowlist
on generated files.

### Composio

Needs an account, an API key, and **one connection per customer mailbox**.

```
tools               global      email.send → composio · GMAIL_SEND_EMAIL
tool_connections    per customer  aurologistics · email.send · entity_id
```

```python
composio = Composio(api_key=os.environ["COMPOSIO_API_KEY"])
await composio.execute("GMAIL_FETCH_EMAILS", entity_id="ishaan-takehome", ...)
```

The entity is the indirection that makes it multi-customer later: same tool key,
different entity, no spec change.

**Do the OAuth setup first.** It is the one step that can eat an hour
unexpectedly, and it blocks the fixture pull, which blocks everything downstream.
Get the connection working, pull the ten emails to `fixtures/`, then work offline.

Action names move — verify `GMAIL_FETCH_EMAILS` / `GMAIL_SEND_EMAIL` against
current Composio docs.

---

## 25. External data — the third direction

Data moves three ways. Two exist in the vocabulary; the third does not.

| Direction | Primitive | Status |
|---|---|---|
| **in** — data arrives | Event carrying Documents | ✓ |
| **out** — data leaves | Action, `effect: notify` / `record` | ✓ |
| **fetched mid-process** — look something up | — | **missing** |

Pre-alert never needs it; everything arrives by email. Credentialing needs it
immediately — *"verify the licence against the state board"* — and so does
anything checking a PO against an ERP.

### Why it does not fit today

A lookup currently has to be modelled as three cards:

```
Action  Query the state board          effect: ?  — neither notify nor record
Event   Verification response received deadline: P5D
Check   Does the board record match?   inputs: ← what document is this?
```

Three cards for what a process owner draws as one box, and the middle one is a
fiction: the response is a return value, not an email arriving. Worse,
`Check.inputs` takes **document keys**, and a lookup result is not a document.

**The root cause is shared with the computation gap (§29): everything a Check can
read must have come from an extracted document.** Two symptoms, one missing
concept — derived values are not addressable state.

### The fix

Add `lookup` as an Action effect with a **named output**:

```python
Action  Verify licence
  effect     lookup
  system     "state medical board"          # the PO's words
  inputs     [application.license_number]
  produces   board_record                   # named, referenceable
  timeout    PT30S
  on_failure fail | wait | skip
```

Then `Check.inputs` accepts a document key **or** a produced value, and the whole
thing collapses back to one card. Same fix computation needs.

### Who owns what

| Layer | Owner | Example |
|---|---|---|
| the step exists in the process | **PO** | "we check the state board" |
| timing, failure handling | **PO** | how long to wait · what if it is down · what if it does not match |
| which endpoint, which credential | **FDE** | `system:"state medical board"` → `board.lookup` |

The PO **must** describe it. A step absent from the canvas is absent from the
generated agent — you cannot bind your way to a step that is not in the spec. And
timeout and failure behaviour are business decisions the engineer cannot invent.

The FDE owns only the *how*, which is why it lives outside the checksummed spec
and can differ per customer without a new version.

> **The spec says what data the process needs and where it conceptually comes
> from. Bindings say how to actually get it.** A missing step is a spec gap and
> goes back to the PO. A missing connector is a binding gap and goes to the
> engineer — unless it is unbuildable, in which case it becomes a spec gap too.

### Credentials — already solved, and unrelated

Worth separating, because "connect to their data" sounds like a credential problem
and is not.

```
spec           references a capability      channel:email · system:"their WMS"
bindings       reference an entity          composio_entity: aurologistics
provider       holds the secret             Composio keeps the OAuth token
```

You never store an OAuth token or a database password. The customer authenticates
once through Composio's flow; your code passes `entity_id`. For a system Composio
does not cover, credentials live in Railway env or Supabase Vault and the adapter
reads them — still never in the spec, the board, or generated code.

**Three layers, secret at the far end.** That part of the design is already right;
the gap is purely that a fetched value has nowhere to live.

### Not built

Pre-alert does not need it. Noted because it is the most likely thing to come up
if the conversation turns to credentialing, and because finding the *shared* cause
with the computation gap is worth more than listing two unrelated limitations.

---

## 26. Recipients, roles and identities

Three separate things, three separate homes.

| Thing | Where | Example |
|---|---|---|
| **Role** | on the primitive, in the spec | `recipients: ["receiving_supervisor"]` |
| **Identity** | bindings file, per customer | `receiving_supervisor: ops-sup@aurologistics.com` |
| **Sender** | never a spec concept | whatever mailbox Composio authenticated as |

Identities must not enter the frozen spec. Two reasons: the spec is checksummed
and immutable, so a personnel change would force a new spec version — absurd. And
the same spec should deploy to a second customer with different people.

```
Action  Report COA discrepancy
  effect          notify
  channel         email
  recipients      [receiving_supervisor]
  payload_fields  invoice_no · batch_nos · discrepancy_description
```

`payload_fields` comes straight from the SOP — *"reported via email with the
Invoice Number, Batch Number(s) and description of discrepancy"* — and they are
FieldRefs, so lint verifies they resolve.

**Lint rules:**

```
notify_missing_recipients    effect=notify with empty recipients     board-level
role_unbound                 a role with no binding entry            pre-codegen
```

**The dynamic-recipient case.** Sometimes the recipient is "whoever sent the
pre-alert" rather than a fixed role. Not a role — a reference into the triggering
event:

```python
recipients: list[RoleRef | EventRef]
EventRef(source="prealert_received", field="sender")
```

The pre-alert SOP does not need it (reports go to a fixed supervisor), but it is
the obvious next case in any email-driven process and the union type costs
nothing now.

---

## 27. The spec view — the FDE surface

Distinct from the whiteboard: different audience, different permissions,
post-freeze.

| Surface | Audience | Vocabulary |
|---|---|---|
| Whiteboard | process owner | business — roles, channels, no tools |
| Spec view | FDE | technical — bindings, conformance, provenance |

```
Spec v1 · checksum 1e77… · frozen 16 Aug · from board "Pre-alert validation"

PRIMITIVES
  ▸ coas_valid            check
      criteria     each_has_matching(invoice.batch_nos, coa.batch_no)
      context      2 inherited · 2 local · 2 negative
      provenance   th_02 th_03 th_07 th_08  → [open threads]

BINDINGS
  channel:email             → email.send → GMAIL_SEND_EMAIL     ✓
  channel:system_of_record  → ?                                 ⚠ unbound
  role:receiving_supervisor → ops-sup@…                         ✓

CONFORMANCE (build 4)
  every primitive implemented          ✓
  every outcome handled                ✓
  no path bypasses a required Action   ✓
```

Three sections, three purposes: **bindings** are the only place a human writes
technical config, and it is post-freeze by design so it never contaminates what
the PO approved. **Conformance** catches a repair that made evals pass by deleting
a validation. **Provenance** is the audit path — click an assertion, land on the
conversation that produced it.

**Spec is read-only and versioned; bindings are editable and per-customer.** That
separation is what makes an FDE contribution surface possible without weakening
immutability.

### No CRUD for the registry or bindings

The registry is four seeded rows; bindings are one YAML file per customer, edited
in an editor and committed. **The UI never writes either.** It reads them and
highlights gaps:

```
BINDINGS
  channel:email             → email.send → GMAIL_SEND_EMAIL   ✓
  channel:system_of_record  → unbound                          ⚠
  role:receiving_supervisor → ops-sup@…                        ✓
```

The loop is: see the gap on screen → edit the file → re-run `mvp bind check`.
Same shape as the repair loop — the interface makes the problem legible, the fix
happens in a terminal.

**The test for any screen: does it make the loop visible, or does it just make
config editable?** Editable config is what a text editor is for. Three surfaces
follow the same rule — the whiteboard shows a dashed card with an unwired outcome,
the spec view shows an unbound channel, the failure bundle shows a localised
diagnosis. Nobody edits data through a form. The product is a gap detector, not a
data editor.

Build priority: the spec viewer is worth building — showing `coas_valid` with its
inherited, local and negative assertions, each clickable back to a thread, is the
clearest visual proof the scoped-context mechanism works. Bindings can be a YAML
file for the demo; conformance can be CLI output.

---

## 28. Deriving fields for other SOPs

The method, so the field set is defensible rather than taste-based.

**1. Write three or four processes as plain sentences.** No schema thinking.

**2. For every step, ask one question:** *what would a coding agent need to know to
implement this that is not in the step's name?*

> "Report the discrepancy" → who receives it · through what channel · what goes in
> the message · is it retryable
>
> "Verify the license" → against what source · how long to wait · what if no
> response
>
> "Manager approval" → who · by when · what if they don't

**3. Tally by frequency across processes.**

| Appears in | Verdict |
|---|---|
| 3+ processes | typed field |
| 2 | typed only if lint or codegen branches on it |
| 1 | `instructions` prose |

**Two structural tests on top:**

- *Does lint or codegen branch on it?* If neither, it is documentation, and
  documentation goes in prose. `completion_criteria` is prose; `channel` is a
  field because codegen emits a different activity per value.
- *Could two competent implementers disagree without it?* If its absence forces a
  business decision at codegen time, it must be a field.

**Why this is finite.** `instructions` absorbs the residual and codegen reads
English fine, so the field set only has to cover **what needs linting** — where
absence is detectable and consequential. You are not modelling all business
processes; you are modelling what "incomplete" means.

### Applied to the pre-alert SOP

Seven steps, ~18 distinct requirements. Everything recurring appears twice *within
one primitive type* — the Check fields in both Checks, the Action fields in both
Actions. That validates sufficiency, not generality. The two genuine cross-cutting
signals are `channel` (Event + both Actions) and `is_terminal` (three Actions).

**One-SOP frequency data is weak evidence.** Stated as such rather than
overclaimed.

### If field discovery were automated

The candidates can be generated; the predicate cannot.

```
process description (prose)
  → LLM: per step, what must an implementer know that is not in the step name?
  → candidate fields, each with a rationale
  → semantic clustering across steps and processes
  → predicate filter                                  ← human
  → typed fields
```

Generation works — it is the same question run manually above, and a model does it
well. Clustering is the fuzzy part: "who receives it", "notify whom" and
"recipient" are one field under three phrasings, and splitting them gives three
fields where one was needed.

**The predicate is the blocker.** *Does codegen branch on it?* is only answerable
after someone writes the branch — you would be asking the generator to predict its
own future behaviour.

What *is* measurable: have codegen **report which spec fields it read** while
generating. A field nothing reads is a candidate for demotion to prose. That is
feedback after the fact, not generation.

**So: autogenerate candidates, human-select fields.** A model reads three process
descriptions, proposes ~40 candidates with rationales, clusters to ~15; twenty
minutes of human judgement keeps eight.

Worth noting the shape: the schema-evolution loop mirrors the healing loop —
codegen reports unread fields, the reviewer proposes new ones from unmapped
instructions, and a human gates both. Not built; a "with more time" item.

---

## 29. Where the vocabulary fails

A named boundary is stronger than an unqualified claim.

### The counterexample: freight rate quoting

```
A quote request arrives.
Look up the lane in the rate table.
Apply the fuel surcharge for the current index.
Add accessorials: liftgate, residential, inside delivery.
If the customer has a contract rate, use that instead.
Compute margin against carrier cost.
If margin is above 18%, send the quote; if below, route to pricing.
```

Four breaks:

| Break | Why |
|---|---|
| "Compute margin" has no home | `effect` is notify/record/mutate/noop — none is *calculate*. It lands in `instructions` as prose, so codegen invents the formula and the spec does not say what the agent computes. |
| Checks cannot compare to a value | needs `{op: compare, left: margin, operator: gt, value: 0.18}` — and `left` is a FieldRef into a document, but margin is derived, not extracted. |
| No intermediate state | `fuel_surcharge`, `base_rate`, `margin` are computed in step N and consumed in step N+2. The model has documents (extracted) and outcomes (terminal), nothing between. |
| Deadline semantics invert | pre-alert waits *for* documents; quoting has an SLA — respond within 4h or go stale. Same field, opposite meaning. |

**In one line:** the vocabulary models *validation and routing* — data arrives, is
checked for presence and consistency, and is routed by the result. Quoting is
*computation* — data arrives and is transformed through intermediate values.

### What it would take

A `Compute` primitive with declared `inputs`, an `expression`, and a named
`output` other primitives can reference — turning derived values into addressable
state. Codegen writing the function is the easy part; the gap is that a computed
value is not a first-class thing, so it cannot be checked, referenced or governed.

Roughly an hour of schema work. **Not built** — no demoed process needs it.

### Two other misses

- **Scheduling / optimisation** — assign twelve loads to eight drivers minimising
  deadhead. Not control flow at all.
- **Blocking human decision** — approval with an SLA and two outcomes. That is the
  `HumanTask` fourth primitive, warranted when a process has one. Pre-alert does
  not.

### Coverage by process type

| Type | Fits | Strains |
|---|---|---|
| Document validation | ✓ | — |
| Credentialing | ✓ | temporal validity — a licence expires |
| Approval routing | mostly | needs HumanTask |
| Quoting / computation | ✗ | no way to express computation |
| Scheduling / optimisation | ✗ | not a control-flow problem |

Most of Meridian's stated verticals — receiving, credentialing, approval routing,
exception handling — sit on the covered side.

---

## 30. What transfers across deployments

**Statements never transfer. Question strategy and schema shapes do.**

Feeding a completed spec into a review at a different customer means the reviewer
asserts another customer's process as if it were theirs — and if they compete, a
business rule has leaked.

| Transfers | Why safe |
|---|---|
| probe hit rates by `(primitive_type, category)` | ranking only; no statements move |
| document schemas | a Commercial Invoice has the same shape at every pharma distributor |
| structural shape rules — "a deadline usually implies an escalation target" | a claim about process shape, not about anyone's business |

```
(check,  undefined_timing)   resolved 89%   → promote this probe
(action, missing_context)    rejected 71%   → demote it
```

Ranking becomes empirical instead of heuristic, with zero customer data moving.

**Same customer, next workflow is different** — seeding from their own prior spec
is fine. It is their data.

The schema already supports the aggregate: `threads` carries
`(category, status, round)` and `assertions` carries `kind`. The mechanism exists;
with one deployment there is nothing yet to aggregate.

---

## 31. Small resolutions

**Events are never terminal.** An Event is something arriving — after it arrives,
something still has to happen. A terminal is always an Action with
`is_terminal: true` and usually `effect: noop` (a named end state,
`return Outcome(...)`).

An Event with a `deadline` that times out routes its `timed_out` outcome to an
escalation Action; *that* is terminal. The Event still has an outgoing edge.

```
event_no_outgoing      an Event with no outgoing edge
terminal_not_action    a primitive with no outgoing edge that is not a terminal Action
```

`is_terminal` therefore belongs on `ActionConfig` only, not on the base primitive.

**No autogenerated forms.** Extensibility comes from the schema being the single
source of truth — lint, codegen and the spec all read the same Pydantic
definitions. Adding a criterion op extends the system whether or not the form
regenerates itself. Three primitive types and ~18 fields is faster hand-written
than debugging a generic schema renderer.

Generate **types**, hand-write **forms**:

```bash
npx openapi-typescript http://localhost:8000/openapi.json -o src/api/schema.d.ts
```

The UI then cannot drift from the API without TypeScript complaining, which is the
real safety property. Autogenerated rendering is cosmetic on top of that — a
"with more time" item.

Note also that composite editors (criteria builder, field picker, recipients)
would be hand-built regardless: a generic renderer produces a JSON editor, and the
point is that a non-technical user never sees JSON.

**Documents compile to nested JSON Schema**, not a flat field list:

```json
"line_items": {"type": "array", "items": {"type": "object", "properties": {
  "drug_description": {"type": "string"},
  "hts_number":       {"type": ["string","null"]}
}}}
```

Field references address into it — `commercial_invoice.line_items[].hts_number` —
and `Check.scope: per_line_item` tells codegen to iterate. **Fields a Check
requires compile as nullable in the extraction schema**: if extraction refused to
return a line item missing an HTS number, the Check could never detect the failure
it exists to detect.

---

## 32. Serialization — reviewer input and spec output

Two serializations of the same board, produced by one traversal in two modes.
**Neither is stored.** The board is rows; only the frozen spec is written.

```python
serialize(board, mode="review")   # → the reviewer's payload, ephemeral
serialize(board, mode="freeze")   # → specs.payload, checksummed
```

### 32.1 Reviewer payload — six keys

```json
{
  "board_id": "8f2a1c", "round": 1,
  "primitives": { "<key>": { "type", "config", "unmet", "in", "out" } },
  "edges":      { "<key>": { "from", "to", "relation", "on_outcomes" } },
  "documents":  { "<key>": { "declared", "fields", "referenced_by" } },
  "shapes":     [ { "kind", "elements": ["primitive:x", "edge:y"] } ],
  "gaps":       [ { "rule", "anchor", "detail" } ],
  "prior_threads": [ { "id", "status", "anchors", "q", "a" } ]
}
```

**Cut deliberately:** a generated `narrative` and per-shape `asserts` strings. Both
were templated prose, and at eleven primitives the model reconstructs the flow from
`edges` without help. Shape *definitions* live in the system prompt instead — ten
lines, written once:

```
exception_exit      a failure path that leaves and never returns.
                    Ask whether the process really ends there.
serial_gate         one check gates another. Ask if the ordering is deliberate.
collapsed_outcomes  two outcomes routed identically. Ask if the distinction matters.
```

**Detection is deterministic; language is the model's.** The model never authors a
claim about structure — it evaluates a match it was handed. If it wrote the claim
it could hallucinate a pattern that is not there, and the question would be about a
fiction.

### 32.2 Why shapes exist — three tiers on one primitive

Take `coas_valid`. Each tier answers something the tier below cannot.

| Tier | Sees | Question | Can the finding be *wrong*? |
|---|---|---|---|
| **node** | `on_missing_input: null` | *If a COA hasn't arrived, is that a failure or do you wait?* | no — the field is simply absent |
| **node + edges** | 3 outcomes, 2 edges | *Your COA check can come out three ways but only two paths are drawn. What happens on the third?* | no — simply absent |
| **shape** | `exception_exit` over `coas_valid → e6 → report_coa_discrepancy` | *A COA problem ends the process — is that right, or does the shipment come back once the paperwork is fixed?* | **yes — the claim may not reflect the process** |

At tier three **nothing is missing**. The edge exists, the outcome is wired, both
primitives are well-formed, lint is silent. What the shape surfaces is a
**decision the drawing makes** — failure is terminal — and a decision can be wrong.

Tiers one and two find *incompleteness*. Tier three finds *unexamined decisions*.
Only the third produces questions on a board that is already legal, and on the
pre-alert board it is the question that unlocks the entire second half of the
process.

### 32.3 The shape library

Ten kinds, domain-free. Each earns inclusion by three tests: the matcher is
expressible over primitive types and edge relations alone; the claim could
plausibly be wrong; and the correction changes structure, not just a field.

| Kind | Matches | Interrogates |
|---|---|---|
| `serial_gate` | Check →`pass`→ Check | is the ordering deliberate, or should they report all problems at once? |
| `unordered_siblings` | two Checks, no path between | does one actually depend on the other? |
| `exception_exit` | exception edge to a terminal | is failure final, or does it resume when fixed? |
| `cycle_unbounded` | repeat path, no max iterations | is there a give-up point? |
| `divergent_terminals` | several terminals, none shared | should anything record the outcome across all of them? |
| `collapsed_outcomes` | two outcomes → same Action | does the distinction change behaviour? |
| `single_entry` | exactly one Event | is there another way information arrives? |
| `no_deadline` | Event with no deadline feeding a downstream Check | is waiting really unbounded? |
| `split_request_response` | Action(`lookup`) → Event(deadline) → Check | is this one step to you, or three? |
| `overloaded_terminal` | one Action with ≥3 inbound exception edges from ≥2 sources | do these different problems need different handling? |

**Validated against a synthetic second process.** Clinician credentialing —
different vertical, same three primitives — was modelled and the library run cold:
five of the original eight fired and produced defensible questions, two silences
were *correct* (`single_entry` staying quiet when there genuinely are two entry
points is the shape working), and two misses produced the last two rows above.
Weak evidence, but real: the library is not pre-alert-specific.

### 32.4 Anchors

An anchor is always **one element with a stable key in the database**:

```
board · group:<key> · primitive:<key> · edge:<key> · document_field:<doc>.<field>
```

**A path, neighbourhood or region is a set of anchors, not an anchor kind.** There
is no `path:p3`, because a path has no identity that survives an edit — delete one
edge and the identifier dangles, whereas a set just loses an element. `group:` is
the exception because a group *does* have a persistent identity
(`primitives.group_key`); membership changes, the group does not.

**Threads are many-anchored; assertions are single-anchored.** A conversation
ranges over several elements, but each settled statement compiles into exactly one
file. Multi-anchored assertions would inject the same sentence into three modules
and duplicate the logic.

```
thread th_04  anchors: [primitive:coas_valid, edge:e6, primitive:report_coa_discrepancy]
  ↓ distill — one answer, three statements
as_07 → edge:e_recheck                    [timing]     "wait 48h, then escalate"
as_08 → primitive:coas_valid              [exception]  "re-run every batch on a correction"
as_09 → primitive:escalate_supervisor     [owner]      "escalation goes to the supervisor"
```

For a shape-derived thread the anchors **are** the shape's elements — the model
copies them rather than inventing them. For a semantic thread it picks its own, and
validation drops any that do not resolve to a real key.

### 32.5 Freeze — the transposition

Storage is thread → many anchors. The freeze inverts it: anchor → many assertions.

```python
def context_for(board, anchor) -> ScopedContext:
    chain = [Anchor("board", None)]
    if anchor.kind == "primitive":
        p = board.primitive(anchor.key)
        if p.group_key:
            chain.append(Anchor("group", p.group_key))
        chain.append(anchor)
        for doc in p.config.inputs:                 # sideways, via inputs
            chain += board.field_anchors(doc)
    hits = [a for a in board.assertions if a.active and a.anchor in chain]
    ...
```

Broad → narrow, so a primitive-level statement overrides a board-level one on
conflict — same convention as lexical scope.

```json
"context": {
  "inherited": ["[rule] one container is one shipment",
                "[terminology] batch↔line-item is by product code",
                "[constraint] batch_nos matches ^[A-Z]{4}\\d{5}[A-Z]$"],
  "local":     ["[exception] re-run every batch on a corrected document",
                "[rule] missing = no COA carries that batch; mismatched = case/padding"],
  "negative":  ["ignore a COA whose batch is on no invoice line — different shipment",
                "expiry is not checked at pre-alert"],
  "provenance": ["th_02", "th_04", "th_05", "th_08"]
}
```

`inherited` means the statement was anchored more broadly, so **several primitives
receive the same text — inlined, not referenced.** Codegen reads one entry and has
everything; no lookups, no joins. That is why a board-level assertion physically
appears N times in the payload.

The chain still filters: `board` anchors reach every primitive, `group:X` only
members, `document_field:invoice.batch_nos` only primitives whose `inputs` include
that document. The batch-format constraint reaches `coas_valid` and
`invoice_complete`, not `report_coa_discrepancy`.

`negative` is collected at every level and **never overridden** — it is a statement
about the world, not about a step.

### 32.6 What codegen never receives

| Withheld | Why |
|---|---|
| the questions | it gets settled statements, not `"AI asked: does this end the process?"`. Transcripts stay in `thread_messages`. |
| sibling assertions from the same thread | `as_07` went to the edge and appears in *that* entry. Codegen reading one primitive should not see statements destined for another file. |
| anchors as data | `provenance` is thread ids for the audit path; the model never resolves them. It reads four lists of English and a config. |

**Conversation-shaped going in, primitive-shaped coming out. The anchor performs
the transposition.**

### 32.7 The chain, end to end

```
structure     exception_exit shape spanning three elements
  ↓
claim         "a COA problem ends the process"
  ↓
thread        anchored to all three
  ↓ PO answers
distill       three assertions, three single anchors
  ↓ canvas patch
structure     new Event, repeat edge, escalation Action  →  new shapes match
  ↓ freeze · context_for walks the scope chain
spec          each primitive carries inherited + local + negative + provenance
  ↓ codegen reads one entry
code          every line traceable to an assertion
```

Round 2 asks questions round 1 could not, because the answer created the structure
that the next shape matches — a `cycle_unbounded` claim only exists once a repeat
edge is drawn. That is what makes rounds non-redundant, and it is structural rather
than a heuristic about what to ask when.

---

## 33. Build order

1. `meridian/domain/primitives.py` and `graph.py` **by hand** — everything depends on these types, and the `.unmet()` completeness contracts generate the lint rules.
2. `db/migrations/0001_initial.sql`, `db/seeds/prealert_board.py` — the incomplete board exists.
3. `meridian/compiler/` — rules, shapes, context, freeze — **checkpoint: a spec freezes.**
4. `meridian/cli.py` — drive everything without a UI.
5. `meridian/runtime/` — the contract generated agents import.
6. `meridian/codegen/` — first generated agent.
7. `fixtures/` — inbox pulled once, ground truth authored per shipment.
8. `meridian/healing/` — sweep, localize, propose, gate. **Checkpoint: the curve moves.**
9. `meridian/reviewer/` — scenarios, dry-run, threads. Hand-written threads in the seed unblock steps 3–8, so a slip here blocks nothing.
10. `meridian/events.py` + `ui/` — canvas and dashboards.

**Sufficiency test after step 3:** hand `spec.lock.json` to a fresh Claude Code
session with no other context. If it asks a business question, the spec is
incomplete — report that finding whether or not you fix it.
