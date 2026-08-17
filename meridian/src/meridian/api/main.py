"""The HTTP surface.

A trigger layer and nothing else. Every route here calls the same function the
CLI calls — `authoring.create.card`, `reviewer.run.review`, `compiler.freeze` —
so the pipeline stays provable from a terminal, and the browser is a viewer of a
system that already works headlessly rather than the only way to work it.

**Errors are rendered here and nowhere else.** Each domain error already carries
what a caller needs: `BoardNotReadyError` holds its findings and its unsettled
questions, `ValidationError` holds the path to the field that was wrong. The
handlers below turn those into status codes without re-deriving anything, which
is what lets the CLI print the same refusal from the same object. If either side
reconstructed the message from prose the two would drift.

    IncompleteError        422   with the findings, so a canvas can pin them
    ValidationError        422   with the field path, so a form can focus it
    NotFoundError          404
    ConflictingStateError  409
    LLMError               503   nothing is wrong with the board; try again

Pipeline calls are synchronous, a deliberate deviation from `Claude.md` §18. It
has them return a `cycle_id` immediately and the browser follow progress on the
`events` table — but `events.py` does not exist yet, so a caller handed a
`cycle_id` would have no way to learn the outcome, which is worse than waiting.
The functions behind these routes do not change when it lands; only the handler.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from meridian.api.routes import boards, documents, observability, review, specs
from meridian.compiler.freeze import BoardNotReadyError
from meridian.core.db import close_pool
from meridian.core.llm import LLMError
from meridian.domain.errors import ConflictingStateError, IncompleteError, NotFoundError


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Hold the pool for the life of the process, and let it go at the end."""
    yield
    await close_pool()


app = FastAPI(
    title="Meridian",
    summary="Turn tacit process knowledge into a running agent.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?|https://.*\.up\.railway\.app",
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(boards.router)
app.include_router(documents.router)
app.include_router(review.router)
app.include_router(specs.router)
app.include_router(observability.router)


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    """Alive. Deliberately touches no database — this answers "is the process up".

    A health check that queried would report the API down whenever Postgres was
    slow, which is the wrong thing to page somebody about and the wrong thing
    for a platform to restart a container over.
    """
    return {"status": "ok"}


@app.exception_handler(BoardNotReadyError)
async def _not_ready(_: Request, refused: BoardNotReadyError) -> JSONResponse:
    """A gate refusing, with everything standing in the way.

    Both gates answer this way — *enter review* and *freeze* — because both are
    the same kind of refusal one step apart, and a canvas pins the findings to
    the cards they name rather than showing a count.
    """
    return JSONResponse(
        status_code=422,
        content={
            "detail": str(refused),
            "findings": [finding.model_dump(mode="json") for finding in refused.findings],
            "unsettled": [
                {"id": str(thread.id), "question": thread.question} for thread in refused.unsettled
            ],
        },
    )


@app.exception_handler(IncompleteError)
async def _incomplete(_: Request, refused: IncompleteError) -> JSONResponse:
    findings: list[Any] = getattr(refused, "findings", [])
    return JSONResponse(
        status_code=422,
        content={
            "detail": str(refused),
            "findings": [finding.model_dump(mode="json") for finding in findings],
        },
    )


@app.exception_handler(ValidationError)
async def _invalid(_: Request, invalid: ValidationError) -> JSONResponse:
    """A value a card cannot hold.

    `loc` is the path to the field, which is what a form needs to focus the
    right input rather than showing a banner and leaving somebody to hunt.
    """
    return JSONResponse(
        status_code=422,
        content={
            "detail": f"{invalid.error_count()} value(s) this card cannot hold",
            "errors": [
                {"field": ".".join(str(part) for part in error["loc"]), "message": error["msg"]}
                for error in invalid.errors()
            ],
        },
    )


@app.exception_handler(NotFoundError)
async def _missing(_: Request, missing: NotFoundError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(missing)})


@app.exception_handler(ConflictingStateError)
async def _conflict(_: Request, conflict: ConflictingStateError) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(conflict)})


@app.exception_handler(LLMError)
async def _unavailable(_: Request, unavailable: LLMError) -> JSONResponse:
    """The model could not be reached.

    Distinct from a 500 because the remedy is different: nothing is wrong with
    the board, whatever was typed is still on screen, and trying again is the
    whole fix.
    """
    return JSONResponse(status_code=503, content={"detail": str(unavailable)})
