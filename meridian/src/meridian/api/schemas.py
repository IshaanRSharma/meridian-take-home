"""What a request carries.

Separate from the domain types on purpose, and the one place the two diverge. A
`Primitive` is what the system holds; a `PrimitiveCreate` is what a canvas is
able to say — a type, maybe a name, maybe where it was dropped. Letting a caller
POST a whole `Primitive` would hand it the key, which is minted server-side
precisely so that renaming a card cannot orphan the comments pinned to it.

Responses go the other way and are the domain types themselves. They are already
Pydantic, already the shape every other consumer reads, and a parallel set of
response models would be a second description of the same thing waiting to
disagree with the first. `openapi-typescript` generates from these, so the
browser cannot drift from the API without TypeScript saying so.
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


class BoardCreate(BaseModel):
    """A new, empty board."""

    name: Annotated[str, Field(min_length=1, description="what this process is called")]


class PrimitiveCreate(BaseModel):
    """A card dropped on the canvas.

    Only a type is required. Everything else is filled in afterwards, because
    the authoring model is drop-then-describe and anything stricter turns
    *"I'll come back to this"* into an error dialog.
    """

    primitive_type: Literal["event", "action", "check", "entity"]
    name: str | None = None
    group_key: str | None = None
    x: float | None = Field(default=None, description="where it was dropped; entities have none")
    y: float | None = None


class PrimitiveUpdate(BaseModel):
    """Fields to merge onto a card's config.

    A free-form object rather than a typed one, because the four card types take
    four different configs and the merge validates against the right one anyway.
    An absent key is left alone; an explicit null clears the field.
    """

    config: dict[str, Any]


class Described(BaseModel):
    """What somebody typed about a card, for the model to read onto its fields."""

    said: Annotated[str, Field(min_length=1)]
    overwrite: bool = Field(
        default=False, description="replace what is already filled in, not only the blanks"
    )


class EdgeCreate(BaseModel):
    """One line, carrying the one outcome its handle represents."""

    from_key: str
    to_key: str
    relation: Literal["normal", "exception", "repeat"] = "normal"
    on_outcomes: list[str] = Field(default_factory=list)
    condition: str | None = None


class LayoutUpdate(BaseModel):
    """Where some cards now sit, after a drag.

    Sent for the cards that moved rather than the whole canvas, so two people
    dragging two different cards never overwrite each other.
    """

    positions: dict[str, tuple[float, float]]


class MessageCreate(BaseModel):
    """A turn in a conversation about the board."""

    body: Annotated[str, Field(min_length=1)]


class ThreadUpdate(BaseModel):
    """A status transition somebody is asking for.

    `resolved` is absent on purpose: the drawing showing an answer is not
    something anyone may declare, it is something the next round proves by
    re-running the walk that raised the question.
    """

    status: Literal["answered", "rejected", "open"]
    body: str | None = Field(default=None, description="what they said, kept as a turn")


class BoardRef(BaseModel):
    """Which board a pipeline call is about."""

    board_id: Annotated[str, Field(description="the board's id")]
