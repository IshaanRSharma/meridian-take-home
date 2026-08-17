# repositories

All the SQL. One module per table or small group of tables, and the only job is
turning rows into domain types and back.

No ORM. The queries here are few and every one is shaped by the domain type it
returns, so an ORM would insert a mapping layer between two things that already
agree. The SQL is at the top of each module as a named constant, so you can read
the query without reading the function.

| module | tables |
|---|---|
| `boards.py` | `boards`, `primitives`, `edges` |
| `threads.py` | `threads`, `comment_anchors`, `comment_messages` |
| `assertions.py` | `assertions` |
| `scenarios.py` | `scenarios` |
| `reference_docs.py` | `reference_docs` |
| `specs.py` | `specs` |
| `builds.py` | `agent_builds` |
| `evals.py` | `eval_cases`, `runs`, `failures` |
| `repairs.py` | `repairs` |
| `tools.py` | `tools` |

## Read whole, like a board

`boards.get()` returns a board with every card and edge. `threads.for_board()`
returns every conversation with its anchors and all its turns. There is no lazy
loading and no N+1, because every consumer wants the whole thing: the reviewer
assembling context, the canvas drawing pins, the freeze gate counting what is
unsettled.

## Two patterns worth knowing

**`upsert_primitive` rather than `save`.** `boards.save()` deletes every row and
rewrites them, which is right for seeding and wrong for editing: it would reset
`created_at` and wipe the `provenance` column on every card each time somebody
edited one of them. So editing one card writes one row.

**Deleting a card leaves its edges.** They dangle, lint reports each one as
blocking, and the process owner decides whether the card or the connection was
the mistake. The same goes for conversations: threads reference cards by slug with
no foreign key, so a card can be deleted and re-created during review without
orphaning the conversation about it.
