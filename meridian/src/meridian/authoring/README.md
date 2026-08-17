# authoring

How a card gets onto a board. This is the write path behind the canvas and behind
`meridian card add`.

| file | what it does |
|---|---|
| `create.py` | a new board, a new card, a new edge |
| `edit.py` | merge fields onto a card that is already there |
| `delete.py` | remove a card or an edge, and say what went |
| `layout.py` | where the cards sit |
| `interpret.py` | a paragraph of prose becomes a card config |

## A card is placeable with a name and nothing else

That is the constraint the whole package is built around. A process owner drags a
card, types what it is called, and walks away. Nothing refuses. Every remaining
field arrives one of three ways: derived from what they already did, clicked from a
closed set in their own words, or asked as a labelled blank on the card.

Which is why this package **cannot import `compiler`**, and the layering test
enforces it. If authoring could reach `rules`, refusing an edit because of a lint
finding would be one import away, and that would make the canvas modal and delete
the very findings the review loop runs on.

## interpret.py, and where the line is

Prose fills the fields that come from description. Pickers fill the fields that
reference something.

`effect`, `channel`, `match_condition`, `cardinality`, `payload_fields` come from
what somebody typed. `criteria`, `outcomes`, `inputs`, `captures`, `scope` come
from pickers populated by the board, because anything naming a field path or an
outcome has to resolve. Mapping "all four codes" onto four exact paths is the most
error-prone thing extraction could do, and getting it wrong produces a check that
tests the wrong thing while looking correct.

The original sentence stays on the card as `instructions`, so extraction can only
add and never subtract. That prose is also where the best review questions come
from, because somebody writing "usually the duty manager, but over five hundred it
goes to the region" has written a rule that no typed field captured.

## layout.py takes no lock

The one write here that validates nothing, and it is allowed to because a position
cannot make a board invalid. No rule reads `layout`, nothing on the frozen spec
carries it, and a card at the wrong coordinates is untidy rather than wrong. It
fires on every mouse-up, so it writes to `boards.layout` and never to `primitives`.
That table changes only when the *process* changes.
