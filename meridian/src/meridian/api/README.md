# api

FastAPI. A thin trigger layer, and that is the property worth protecting: all the
logic lives in sibling packages as plain functions, so the CLI and the tests call
the same code with no HTTP involved.

| file | what it is |
|---|---|
| `main.py` | the app, CORS, and the exception handlers that map domain errors |
| `dependencies.py` | one transaction per request |
| `schemas.py` | what a request may say, which is not what the system holds |
| `routes/boards.py` | cards, edges, layout, lint |
| `routes/documents.py` | attach a written procedure, list them, detach one |
| `routes/review.py` | run a round, settle, read threads, answer, reject |
| `routes/specs.py` | freeze, read, sufficiency, yield |
| `routes/observability.py` | events and evals for the timeline |

## One transaction per request

`dependencies.connection` yields a connection inside a transaction and the context
manager rolls it back if the handler raises. So a round of review writes its
threads, its situations and its statements together or writes none of them, and no
route has to remember that.

## Errors are rendered, never re-derived

`BoardNotReadyError` carries its findings and its unsettled threads as objects, so
two callers can present the same refusal without either reconstructing it from
prose. The CLI prints them. The API returns 422 with the same list. A canvas pins
each finding to the card it names instead of showing a count.

That is why `freeze` answering 422 matters: the completeness contract is enforced
at the boundary rather than in the UI, so it holds no matter who calls it.

## schemas.py exists for one reason

A `Primitive` is what the system holds. A `PrimitiveCreate` is what a canvas is
able to say: a type, maybe a name, maybe where it was dropped. Letting a caller
POST a whole `Primitive` would hand it the key, which is minted server-side from
the name so that generated filenames match spec keys.

## What has no routes, on purpose

`build`, `eval` and `repair`. The repair loop is human-run from a terminal: the
platform's job is to make a failure legible enough to paste, and the failure bundle
is the product. An HTTP endpoint wrapping `meridian eval sweep` would add a second
way to do the same thing and a second way to do it wrong.

`spec export` has no route either, because it writes files to disk and that is not
a server's job.
