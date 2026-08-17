# Running this

Four paths, in increasing order of what they need. Start at the top. Each one
works on its own, and you only go further down if you want what the next one adds.

| you want | needs | time |
|---|---|---|
| 1. Run the tests | uv, docker | 2 min |
| 2. Drive the loop from a terminal | the above plus an OpenAI key | 5 min |
| 3. See it in a browser | the above plus node | 10 min |
| 4. Generate and repair an agent | the above plus Composio, and a coding agent | longer |

---

## Prerequisites

```bash
uv --version        # https://docs.astral.sh/uv
docker --version    # Docker Desktop or colima
node --version      # only for path 3, 20 or newer
```

**What you need a key for, and what you do not.** The test suite runs offline
against a fake transport, so nothing in path 1 touches OpenAI. Paths 2 onward call
a model for real. Composio is only needed if you want the generated agent reading
a live mailbox, and the eval sweep can run against recorded fixtures instead.

---

## 1. Run the tests

```bash
make install        # uv sync
make db             # Postgres 17 on :54329, bootstrapped and migrated
make check          # ruff, mypy --strict, pytest
```

`make db` is a separate command on purpose. Unit tests do not need it. Integration
tests do, and they run against that container and never against a shared database.
The port is 54329 rather than 5432 so it cannot clash with a Postgres you already
have installed.

If you skip `make db`, the database tests skip themselves rather than failing.

```bash
make test           # just pytest
make lint           # just ruff
make typecheck      # just mypy
make cov            # with a coverage report
```

---

## 2. Drive the loop from a terminal

Everything the product does is reachable from the CLI. That is the property worth
protecting: the UI is a viewer of a system that works headlessly, not a system that
only works through the UI.

### Point it at a database

```bash
cp meridian/.env.example meridian/.env
```

Then fill in two things. `DATABASE_URL` can be either your Supabase project or the
local container:

```
DATABASE_URL=postgresql://meridian:meridian@localhost:54329/meridian?sslmode=disable
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-5.1
```

If you use Supabase, take the **Session pooler** host on port 5432, not the
"Direct connection" one. The direct host resolves IPv6-only on newer projects and
fails on an IPv4 network.

```bash
make migrate        # apply db/migrations
make seed           # load the pre-alert board, prints a board id
```

### Draw, review, freeze

```bash
cd meridian
alias m='uv run python -m meridian.cli'

m board lint <board>                 # what is missing, worst first
m review run <board>                 # one round: refuses, settles, walks, asks
m thread list <board>
m thread answer <thread> "The receiving supervisor gets it."
m thread reject <thread> "Expiry is not checked at this stage."
m review run <board>                 # round two: closes what the answers settled
m spec freeze <board>                # refuses on blocking findings or open questions
m spec check <board>                 # what the spec still leaves to its implementer
m spec yield <board>                 # how much of it the drawing could not have said
m spec export <board>                # writes agents/<slug>/spec.lock.json
```

Two things to notice while you do that.

**`review run` refuses the seed board the first time.** Three blocking findings:
one outcome with no line out of it, and two steps the process stops at without
saying so. That refusal is correct. A person can see all three on the canvas in ten
seconds, and spending one of six review slots asking somebody to answer in prose
what they could simply draw is how a review loop earns a bad reputation.

**Answering a question does not resolve it.** `answered` means the knowledge
exists. `resolved` means the drawing shows it, and the next round proves that by
re-running the walk that raised the question. Answer without editing the canvas and
the thread stays unsettled, which keeps blocking the freeze.

### Attach a written procedure, if there is one

```bash
m board attach <board> ../docs/sop-inbound-pre-alert.pdf --kind sop
```

Markdown and text are read off disk. A PDF or a photograph of a printed sheet goes
to the model, which reads the page. The reviewer then aligns what the document says
onto the cards it bears on, and a disagreement between the procedure and the drawing
is the one question class somebody can check rather than argue with.

---

## 3. See it in a browser

Two processes. No CORS configuration, because `vite.config.ts` proxies `/api` to
`127.0.0.1:8000`, so the browser talks to the API same-origin.

```bash
# terminal one
cd meridian && uv run uvicorn meridian.api.main:app --reload --port 8000

# terminal two
cp ui/.env.example ui/.env       # leave VITE_API_URL empty for local dev
cd ui && npm install && npm run dev
```

The API is on `http://localhost:8000`, with generated docs at `/docs`. The UI is on
whatever port Vite prints.

`ui/.env` needs a Supabase publishable key (`sb_publishable_...`) for the login
screen. Never put the secret key there. Vite inlines env vars into a static asset
and the secret key bypasses RLS. The app refuses to start if it finds one.

### Regenerating the frontend types

The only thing crossing the boundary is types, in one direction:

```bash
cd ui && npx openapi-typescript http://localhost:8000/openapi.json -o src/lib/schema.d.ts
```

Run that after any API change. TypeScript complaining is the drift check.

---

## 4. Generate an agent, and repair it

Two steps here are run by a person with a coding agent rather than by the platform.
That is deliberate. The brief says not to force every step to be autonomous, and
these are the two where a human reading a diff is worth more than automation.

Both are checked in as skills at `.claude/skills/`, symlinked to `.codex/skills/`,
so the same files work in Claude Code and Codex.

### Generate

```bash
m spec export <board>                # writes agents/<slug>/spec.lock.json
claude                               # or codex
> /spec-to-agent
```

The skill reads the frozen spec and writes `agents/<slug>/`. Its first instruction
is the one that matters: *everything in the spec was approved by a person, nothing
you add is.* It implements what the spec determines and stops where it does not.

It reads `meridian/src/meridian/runtime/` first, because that is the contract it
imports. A scaffold, not a framework: you import from it, it never calls you, and
there is no base class to inherit.

```bash
m build register <board> --from-git  # reads HEAD and the agent's build.json
```

### Run it

```bash
m eval load <board> --from ../fixtures/expected/shipments.json
m eval sweep <board>
```

**You do not need to start Temporal for this.** The generated agent runs itself:
`build.json` names an entry point, and everything Temporal happens inside the
agent. It calls `WorkflowEnvironment.start_time_skipping()`, which boots a real
Temporal server in-process and jumps the clock to the next timer, so a deadline
measured in days resolves in milliseconds.

`make temporal` exists for the live path, where a long-running worker would connect
to a dev server on 7233 with a web UI on 8233. See the caveat at the bottom.

### Repair

```bash
m bundle <board> | pbcopy             # the largest failure bucket by default
claude
> /repair-agent                       # paste it, fix one file
m build register <board> --from-git
m eval sweep <board>                  # a new point on the curve
```

The bundle is the product. Everything else in the healing package is a terminal: a
sweep is a loop, a gate is a comparison, a repair is a row. What the platform
actually sells is that a failure becomes legible enough that one paste fixes it, so
the bundle carries the expected and actual row, the trace, the file, the spec
context for that primitive, and the repair history for that signature.

The skill fixes **one signature**, not the build. And it decides one thing before
touching code: is this an implementation defect or a gap in the spec. Whitespace in
a batch id is a defect, fix it freely. "A certificate arrives whose batch is on no
invoice, ignore it or flag it" is a spec gap, and no patch is correct because no
rule decides the answer. That goes back to the process owner as a thread.

The gate can only reject. A human is required to override it and never to approve
in its place.

---

## Local services, in full

```bash
make db             # Postgres 17 on 54329, bootstrapped and migrated
make temporal       # Temporal dev server, gRPC 7233, UI http://localhost:8233
make down           # stop both
make clean          # caches and build artefacts
```

Data persists in `./.data/postgres` and `./.data/temporal`. Delete those
directories to start fresh.

Neither service is a dependency of the unit tests.

---

## Troubleshooting

**`make check` fails on lint before running anything.** Lint runs first on purpose.
Read the file and line it names.

**Database tests all skip.** `make db` has not run, or `DATABASE_URL` is unset.
That is a skip rather than a failure by design, so the suite is useful with no
services at all.

**`connection refused` on 54329.** The container is not up. `docker compose ps`
shows what is running.

**Supabase connection hangs and then times out.** You used the Direct connection
host. Switch to the Session pooler on 5432.

**`review run` exits 1 saying "This is not a process yet".** Working as intended.
It lists the blocking findings. Draw those, then run it again.

**`spec freeze` exits 1 listing unsettled questions.** Also intended. Every
question has to be answered or dismissed first. `m thread list <board>` shows what
is open.

**A model call fails with exit code 3.** `OPENAI_API_KEY` or `OPENAI_MODEL` is
missing from `meridian/.env`. Nothing is wrong with the board, and running the
command again is the whole remedy.

**The UI loads but every request 404s.** The API is not running on 8000, or you set
`VITE_API_URL` when you should have left it empty for local dev.

---

## What is not wired

Two honest gaps, both stated rather than discovered.

**There are no Dockerfiles.** `docker-compose.yml` covers local Postgres and
Temporal, which is all it claims to. There is no image for the API, the worker or
the UI, so the three-service Railway deploy described in `Claude.md` §3 cannot be
built as it stands. Running locally is fully supported. Deploying is not.

**There is no long-running worker.** No `worker/` package exists, so the live path
where an email arrives and wakes a workflow is not runnable. The eval sweep is,
and it exercises the same generated workflow through a real Temporal server. For a
demo the sweep is the stronger artifact anyway: it is deterministic, it is fast, and
it runs the actual agent against actual documents.
