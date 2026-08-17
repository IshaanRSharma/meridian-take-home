# core

Three files, three seams to the outside world. Everything else in the package
reaches the environment through here and nowhere else.

| file | seam |
|---|---|
| `config.py` | the environment. Nothing else reads `os.environ`. |
| `db.py` | the connection pool, and the one place a transaction opens. |
| `llm.py` | OpenAI. Every model call in the system goes through `structured()`. |

## Why one function for every model call

`structured()` is the only way to reach a model. That is the point, not a
convenience. Three things follow from it:

**The task picks the model.** Callers say what they are doing (`Task.REVIEW`,
`Task.DISTIL`, `Task.EXTRACT`), not which model does it. Pointing every review at
a stronger model is one line in `MODELS` rather than a search across the package.

**Temperature is pinned to 0 in one place.** The reviewer publishes a determinism
number, and that number only measures the prompt if the sampler is held still.

**Tool calls come back rather than being discarded.** `Completed.calls` carries
every call the model made on the way to its answer, in order, because those are
the evidence a review comment cites. A model that looked something up and a model
that guessed produce the same object otherwise, and they must not be
indistinguishable.

The `transport` argument is the testing seam. It defaults to the real OpenAI
client, and a test passes a fake, so every caller in the package is testable with
no key and no network. That is why the suite runs offline.

## config.py

`requires_test_database()` exists because a migration cannot be rolled back. The
test database is a separate variable from the production one, and asking for the
test DSN refuses to hand back production. Integration tests never touch a shared
database, and the local container matches Supabase's major version so a migration
that passes locally passes there.
