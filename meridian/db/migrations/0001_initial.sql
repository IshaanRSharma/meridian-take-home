-- Meridian, initial schema.
--
-- Conventions this schema holds to:
--
--   `key`, not `id`, for anything the frozen spec references. Primitives, edges
--   and scenarios carry a board-scoped slug, and threads, assertions, failures
--   and events reference those slugs with NO foreign key — a card can be deleted
--   and re-created during review without orphaning its conversation, and a
--   generated filename matches its spec key.
--
--   jsonb only for type-discriminated payloads. `primitives.config` varies by
--   `primitive_type` and Pydantic validates it at the boundary. Fixed shapes get
--   columns.
--
--   Mutable things are rows; immutable snapshots are blobs. The board is rows.
--   A spec is one payload, because nothing ever queries inside a frozen spec.
--
--   ISO 8601 durations as text ('PT48H'), which parse in Python, TypeScript and
--   Temporal alike.

create extension if not exists pgcrypto;

-- Two characters minimum: edge keys are naturally short ('e1'), and anything
-- longer only has to be a legal filename and Python identifier.
do $$ begin
  create domain board_key as text check (value ~ '^[a-z][a-z0-9_]{1,63}$');
exception when duplicate_object then null;
end $$;


-- ─────────────────────────────────────────────────────────────────────────
-- the board — mutable
-- ─────────────────────────────────────────────────────────────────────────

create table boards (
  id           uuid primary key default gen_random_uuid(),
  name         text not null,
  status       text not null default 'draft'
               check (status in ('draft', 'in_review', 'submitted')),
  review_round int  not null default 0,
  -- {primitive_key: {x, y}}. Entities have no entry, and that absence is the
  -- entire implementation of "first-class but not drawn".
  layout       jsonb not null default '{}',
  created_at   timestamptz not null default now()
);

create table primitives (
  id             uuid primary key default gen_random_uuid(),
  board_id       uuid not null references boards(id) on delete cascade,
  key            board_key not null,
  primitive_type text not null
                 check (primitive_type in ('event', 'action', 'check', 'entity')),
  group_key      board_key,
  config         jsonb not null default '{}',
  -- How each config field got its value: typed | clicked | filled | answered,
  -- with the sentence or thread it came from. Drives how the UI renders a
  -- field, how hard the reviewer questions it, and the audit path from any
  -- value back to a human utterance.
  provenance     jsonb not null default '{}',
  created_at     timestamptz not null default now(),
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
              check (relation in ('normal', 'exception', 'repeat')),
  on_outcomes text[] not null default '{}',
  condition   text,
  unique (board_id, key),
  -- Only a repeat may point at itself; anything else is a drawing mistake.
  check (from_key <> to_key or relation = 'repeat')
);
create index on edges (board_id, from_key);
create index on edges (board_id, to_key);

-- Reference documents describe the process. Reviewer input only, never in a
-- spec: an SOP tells the reviewer what to ask about, it is not data the agent
-- processes.
create table reference_docs (
  id             uuid primary key default gen_random_uuid(),
  board_id       uuid not null references boards(id) on delete cascade,
  kind           text not null check (kind in ('sop', 'policy', 'email', 'other')),
  filename       text not null,
  storage_path   text not null,
  extracted_text text
);


-- ─────────────────────────────────────────────────────────────────────────
-- review
-- ─────────────────────────────────────────────────────────────────────────

create table scenarios (
  id                uuid primary key default gen_random_uuid(),
  board_id          uuid not null references boards(id) on delete cascade,
  key               board_key not null,
  kind              text not null check (kind in ('happy', 'variant', 'probe')),
  description       text not null,
  -- Exactly what Board.dry_run takes, so a scenario is runnable rather than
  -- merely described.
  outcomes          jsonb not null default '{}',
  start_key         board_key,
  expected_terminal board_key,
  dryrun_result     text check (dryrun_result in
                    ('reached_terminal', 'dead_end', 'undefined_branch', 'loop')),
  round             int not null default 1,
  unique (board_id, key)
);

create table threads (
  id           uuid primary key default gen_random_uuid(),
  board_id     uuid not null references boards(id) on delete cascade,
  category     text not null check (category in
               ('missing_path', 'ambiguous_rule', 'missing_context',
                'undefined_exception', 'undefined_timing', 'redundancy', 'spec_gap')),
  severity     text not null default 'important'
               check (severity in ('blocking', 'important', 'minor')),
  -- Resolving is never blocked. The next review round re-runs lint and every
  -- scenario and reopens anything still unaddressed, which is what keeps a
  -- process owner from having the last word without trapping them in a thread
  -- that has nothing to re-run.
  status       text not null default 'open'
               check (status in ('open', 'answered', 'rejected', 'resolved')),
  origin       text not null default 'scenario'
               check (origin in ('lint', 'scenario', 'semantic', 'reduction', 'repair')),
  round        int  not null default 1,
  question     text not null,
  scenario_key board_key,
  -- The tool call a structural claim cites. A reviewer may explore freely but
  -- may not assert something the board does not do.
  evidence     jsonb,
  created_at   timestamptz not null default now(),
  resolved_at  timestamptz
);
create index on threads (board_id, status);

-- A conversation ranges over several elements at once, so anchors are a table.
create table thread_anchors (
  thread_id   uuid not null references threads(id) on delete cascade,
  anchor_kind text not null check (anchor_kind in
              ('board', 'group', 'primitive', 'edge', 'entity_field')),
  anchor_key  text,
  is_primary  boolean not null default false,
  primary key (thread_id, anchor_kind, anchor_key),
  check ((anchor_kind = 'board') = (anchor_key is null))
);
create index on thread_anchors (anchor_kind, anchor_key);

create table thread_messages (
  id         uuid primary key default gen_random_uuid(),
  thread_id  uuid not null references threads(id) on delete cascade,
  seq        int  not null,
  author     text not null check (author in ('ai', 'human')),
  body       text not null,
  created_at timestamptz not null default now(),
  unique (thread_id, seq)
);

-- Threads are conversations; assertions are settled statements, and only
-- assertions cross the freeze. SINGLE anchor: a statement compiles into exactly
-- one file, so a thread spanning three elements distils into three assertions.
create table assertions (
  id              uuid primary key default gen_random_uuid(),
  board_id        uuid not null references boards(id) on delete cascade,
  thread_id       uuid references threads(id) on delete set null,
  anchor_kind     text not null check (anchor_kind in
                  ('board', 'group', 'primitive', 'edge', 'entity_field')),
  anchor_key      text,
  kind            text not null check (kind in
                  ('rule', 'exception', 'timing', 'owner', 'terminology',
                   'constraint', 'negative')),
  statement       text not null,
  constraint_json jsonb,
  round           int not null default 1,
  superseded_by   uuid references assertions(id),
  created_at      timestamptz not null default now(),
  check ((anchor_kind = 'board') = (anchor_key is null)),
  -- A constraint nothing can check is just prose wearing a label.
  check (kind <> 'constraint' or constraint_json is not null)
);
create index on assertions (board_id, anchor_kind, anchor_key)
  where superseded_by is null;


-- ─────────────────────────────────────────────────────────────────────────
-- the spec — immutable
-- ─────────────────────────────────────────────────────────────────────────

create table specs (
  id        uuid primary key default gen_random_uuid(),
  board_id  uuid not null references boards(id),   -- provenance, deliberately NO cascade
  version   int  not null,
  payload   jsonb not null,
  checksum  text not null,
  frozen_at timestamptz not null default now(),
  unique (board_id, version)
);

create or replace function forbid_spec_mutation() returns trigger
language plpgsql as $$
begin
  raise exception 'specs are immutable; submit again for a new version';
end $$;

create trigger specs_immutable before update or delete on specs
  for each row execute function forbid_spec_mutation();


-- ─────────────────────────────────────────────────────────────────────────
-- tools and bindings
-- ─────────────────────────────────────────────────────────────────────────

-- Seeded from the connected Composio toolkit rather than hand-authored, so
-- `capability_unresolvable` checks a real catalogue instead of someone's memory.
create table tools (
  key          text primary key,      -- 'email.send'
  provider     text not null,         -- 'composio' | 'internal'
  action       text not null,         -- 'GMAIL_SEND_EMAIL'
  input_schema jsonb not null default '{}',
  enabled      boolean not null default true
);


-- ─────────────────────────────────────────────────────────────────────────
-- builds, evaluation and repair
-- ─────────────────────────────────────────────────────────────────────────

create table agent_builds (
  id              uuid primary key default gen_random_uuid(),
  spec_id         uuid not null references specs(id) on delete cascade,
  iteration       int  not null,
  parent_build_id uuid references agent_builds(id),
  source_ref      text not null,        -- 'agents/inbound_pre_alert@a3f9c21'
  created_by      text not null check (created_by in ('codegen', 'repair', 'human')),
  -- Reproducibility is a claim about builds, and the same spec generated with a
  -- different model produces different code.
  model           text,
  prompt_version  text,
  temperature     numeric(3, 2),
  -- primitive_key → file. Codegen writes it, localisation reads it. Without it
  -- the mapping depends on a comment convention that fails silently on rename.
  file_map        jsonb not null default '{}',
  created_at      timestamptz not null default now(),
  unique (spec_id, iteration)
);

create table eval_cases (
  id              uuid primary key default gen_random_uuid(),
  spec_id         uuid not null references specs(id) on delete cascade,
  key             text not null,        -- shipment no: 'CAAU4056270'
  split           text not null default 'train' check (split in ('train', 'holdout')),
  origin          text not null check (origin in
                  ('authored', 'scenario', 'inbox', 'mutation')),
  scenario_key    board_key,
  input           jsonb not null default '{}',
  expected_output jsonb not null,       -- the per-shipment aggregate row
  tags            text[] not null default '{}',
  unique (spec_id, key)
);

create table runs (
  id                   uuid primary key default gen_random_uuid(),
  build_id             uuid not null references agent_builds(id) on delete cascade,
  case_id              uuid references eval_cases(id),   -- null means production
  mode                 text not null check (mode in ('sandbox', 'shadow', 'prod')),
  outcome              text check (outcome in ('passed', 'failed', 'error', 'running')),
  output               jsonb,
  temporal_workflow_id text,
  cost_usd             numeric(10, 4),
  started_at           timestamptz not null default now(),
  ended_at             timestamptz
);
create index on runs (build_id, outcome);

-- Populated by the headless harness only. Production runs leave this empty and
-- link out through runs.temporal_workflow_id, because Temporal already has the
-- history and mirroring it would be a worse copy of a solved thing.
create table run_steps (
  id            uuid primary key default gen_random_uuid(),
  run_id        uuid not null references runs(id) on delete cascade,
  seq           int not null,
  primitive_key board_key not null,
  attempt       int not null default 1,
  status        text not null check (status in ('running', 'ok', 'failed', 'skipped')),
  input         jsonb,
  output        jsonb,
  tool_calls    jsonb,
  error         text,
  latency_ms    int,
  unique (run_id, seq, attempt)
);

create table failures (
  id            uuid primary key default gen_random_uuid(),
  run_id        uuid not null references runs(id) on delete cascade,
  primitive_key board_key,
  detector      text not null check (detector in
                ('assertion', 'output_diff', 'conformance')),
  signature     text not null,          -- bucketing key for localisation
  detail        jsonb not null default '{}'
);
create index on failures (signature);

create table repairs (
  id                 uuid primary key default gen_random_uuid(),
  build_id           uuid not null references agent_builds(id) on delete cascade,
  classification     text not null check (classification in
                     ('implementation_defect', 'spec_gap')),
  failure_signature  text not null,
  failing_case_ids   uuid[] not null default '{}',
  files_touched      text[] not null default '{}',
  summary            text not null,
  diff               text,
  status             text not null default 'proposed' check (status in
                     ('proposed', 'regressed', 'accepted', 'rejected', 'escalated')),
  regressed_case_ids uuid[] not null default '{}',
  produced_build_id  uuid references agent_builds(id),
  raised_thread_id   uuid references threads(id),
  created_at         timestamptz not null default now(),
  -- A spec gap cannot be silently patched in code. If two competent people could
  -- disagree and the customer would care, it goes back to the process owner.
  check (classification <> 'spec_gap' or raised_thread_id is not null)
);

create table deployments (
  id           uuid primary key default gen_random_uuid(),
  build_id     uuid not null references agent_builds(id) on delete cascade,
  stage        text not null check (stage in ('sandbox', 'shadow', 'prod')),
  status       text not null default 'active'
               check (status in ('active', 'paused', 'stopped')),
  bindings_sha text,
  stopped_by   text,
  stopped_at   timestamptz,
  updated_at   timestamptz not null default now()
);


-- ─────────────────────────────────────────────────────────────────────────
-- observability
-- ─────────────────────────────────────────────────────────────────────────

create table events (
  id            bigserial primary key,
  at            timestamptz not null default now(),
  cycle_id      uuid not null,          -- correlates one end-to-end run
  spec_id       uuid,
  build_id      uuid,
  run_id        uuid,
  case_key      text,
  thread_id     uuid,
  primitive_key board_key,
  phase         text not null check (phase in
                ('review', 'compile', 'codegen', 'eval', 'repair', 'deploy', 'prod')),
  kind          text not null,
  status        text not null check (status in ('started', 'ok', 'failed', 'rejected')),
  duration_ms   int,
  cost_usd      numeric(10, 5),
  detail        jsonb not null default '{}'
);
create index on events (cycle_id, at);
create index on events (build_id, phase);

-- The browser's only direct database use is one subscription to this table.
-- Guarded because the publication exists on Supabase and not on plain Postgres.
do $$ begin
  if exists (select 1 from pg_publication where pubname = 'supabase_realtime') then
    alter publication supabase_realtime add table events;
  end if;
end $$;


-- ─────────────────────────────────────────────────────────────────────────
-- row level security
-- ─────────────────────────────────────────────────────────────────────────

-- No auth in this version, but a public-schema table without RLS is readable by
-- the anon key, and that key ships in the frontend bundle. FastAPI holds the
-- service role, which bypasses RLS, so deny-all is correct everywhere except
-- the events feed the browser subscribes to.
do $$
declare t text;
begin
  foreach t in array array[
    'boards', 'primitives', 'edges', 'reference_docs', 'scenarios',
    'threads', 'thread_anchors', 'thread_messages', 'assertions', 'specs',
    'tools', 'agent_builds', 'eval_cases', 'runs', 'run_steps', 'failures',
    'repairs', 'deployments', 'events'
  ]
  loop
    execute format('alter table public.%I enable row level security', t);
  end loop;
end $$;

do $$ begin
  create policy events_read on events for select to anon using (true);
exception when duplicate_object then null;
end $$;
