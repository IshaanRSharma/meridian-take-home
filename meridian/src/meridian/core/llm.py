"""The one seam between this system and OpenAI.

Every model call in the package goes through `structured()`. That is the point:
model choice, determinism and the tool-calling loop are decisions this system
makes once, and a call site that could make them differently is a call site that
will. Extraction, review, distillation and codegen planning then differ only in
their prompt and their schema.

Three things follow from being the only seam.

**The task picks the model.** Callers name what they are doing, not which model
does it, so re-pointing every review at a stronger model is one line here rather
than a search across the package.

**Temperature is pinned to 0.** The reviewer's determinism test — run twice on
one board, compare — measures the prompt only if the sampler is held still. A
single `temperature` argument in one function is what makes that claim true.

**Tool calls are returned, not discarded.** `Completed.calls` carries every call
made on the way to the answer, in order, because they are the evidence a review
comment cites. A model that looked something up and a model that guessed produce
the same object and must not be indistinguishable.

Testability is a design constraint rather than an afterthought: `transport` is
the function that actually talks to OpenAI, and it defaults to the real one. A
test passes a fake and every caller becomes testable with no key and no network.

OpenAI only, deliberately (Claude.md §2) — so there is no provider abstraction
here, and adding one would be an interface with a single implementation.
"""

import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from typing import Any

from openai import AsyncOpenAI
from openai.types.responses import ParsedResponse
from pydantic import BaseModel

from meridian.core.config import settings
from meridian.domain.errors import MeridianError


class LLMError(MeridianError):
    """A model call could not produce a validated object."""


class Task(StrEnum):
    """What a call is for. The reason a model was chosen, not the choice itself."""

    REVIEW = "review"
    """Reasoning over a process graph. The question's quality is the product."""

    DISTIL = "distil"
    """A settled conversation into statements. Mechanical — the thinking is done."""

    CLASSIFY = "classify"
    """A closed enum out. Cheapest model that can pick from a list."""

    FILL = "fill"
    """Natural language into a typed card config. Structure, not judgement."""


# Only the two tasks whose needs genuinely differ from the default get a row.
# Everything else runs on OPENAI_MODEL, so the common case is configured in the
# environment and this dict stays a list of exceptions rather than a registry.
# Overrides only. Anything absent runs on `OPENAI_MODEL`, so this is a list of
# exceptions rather than a configuration surface.
MODELS: dict[Task, str] = {
    # One label from a closed enum. The set of answers is fixed before the call
    # is made, so a larger model would be paying for reasoning the enum already
    # did — and this runs once per document rather than once per round.
    Task.CLASSIFY: "gpt-5.4-nano",
}

# A model that keeps asking for tools is a prompt bug, and the cost of finding
# out is one request per iteration. Eight is past any legitimate depth here —
# the deepest real chain is fetch, extract, compare.
MAX_ITERATIONS = 8


def model_for(task: Task) -> str:
    """The model this task runs on."""
    model = MODELS.get(task) or settings().openai_model
    if not model:
        msg = f"no model for task {task!r}: set OPENAI_MODEL in meridian/.env"
        raise LLMError(msg)
    return model


@dataclass(frozen=True, slots=True)
class Tool:
    """Something the model may call, and the code that answers when it does."""

    name: str
    description: str
    parameters: dict[str, Any]
    """JSON Schema for the arguments. The model sees this, so it is the docs."""

    run: Callable[[dict[str, Any]], Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class ToolCall:
    """One call the model made, and what came back."""

    name: str
    arguments: dict[str, Any]
    result: Any


@dataclass(frozen=True, slots=True)
class Completed[T: BaseModel]:
    """A validated object, and everything the model did to reach it."""

    value: T
    calls: tuple[ToolCall, ...] = ()


# The seam. The real one is `AsyncOpenAI().responses.parse`; a test passes an
# async function of its own. Called with keyword arguments only, hence `...`.
Transport = Callable[..., Awaitable[ParsedResponse[Any]]]


@lru_cache
def _client() -> AsyncOpenAI:
    """One client per process. Its own retry policy handles transient failures."""
    return AsyncOpenAI(api_key=settings().openai_api_key)


def _default_transport() -> Transport:
    return _client().responses.parse


async def structured[T: BaseModel](  # noqa: PLR0913 - four are the call itself; two are seams
    task: Task,
    schema: type[T],
    system: str,
    user: str,
    *,
    tools: Sequence[Tool] = (),
    transport: Transport | None = None,
) -> Completed[T]:
    """Run one task to a validated `schema`, running any tools the model asks for.

    Args:
        task: what this call is for; decides the model.
        schema: the Pydantic model the response must validate against.
        system: instructions, resent unchanged on every iteration.
        user: the payload — a board, a thread, a document.
        tools: what the model may call. Empty for most calls.
        transport: the function that talks to OpenAI. Tests pass a fake.

    Raises:
        LLMError: the model asked for an undeclared tool, returned no object, or
            kept calling tools past `MAX_ITERATIONS`.
    """
    send = transport or _default_transport()
    declared = {tool.name: tool for tool in tools}
    conversation: list[Any] = [{"role": "user", "content": user}]
    calls: list[ToolCall] = []

    for _ in range(MAX_ITERATIONS):
        response = await send(
            model=model_for(task),
            instructions=system,
            input=conversation,
            tools=[_as_tool_param(tool) for tool in tools],
            text_format=schema,
            temperature=0,
        )
        requested = [item for item in response.output if item.type == "function_call"]

        if not requested:
            if response.output_parsed is None:
                msg = f"{task} returned no structured output"
                raise LLMError(msg)
            return Completed(value=response.output_parsed, calls=tuple(calls))

        for item in requested:
            tool = declared.get(item.name)
            if tool is None:
                msg = f"{task} asked for a tool it was never given: {item.name!r}"
                raise LLMError(msg)
            arguments: dict[str, Any] = json.loads(item.arguments)
            result = await tool.run(arguments)
            calls.append(ToolCall(name=item.name, arguments=arguments, result=result))
            # Echo the call before its output. The API rejects an output whose
            # call is absent from the input it is sent with.
            conversation.append(
                {
                    "type": "function_call",
                    "call_id": item.call_id,
                    "name": item.name,
                    "arguments": item.arguments,
                }
            )
            conversation.append(
                {
                    "type": "function_call_output",
                    "call_id": item.call_id,
                    "output": json.dumps(result),
                }
            )

    msg = f"{task} still calling tools after {MAX_ITERATIONS} iterations"
    raise LLMError(msg)


def _as_tool_param(tool: Tool) -> dict[str, Any]:
    """A Tool in the shape the Responses API takes it."""
    return {
        "type": "function",
        "name": tool.name,
        "description": tool.description,
        "parameters": tool.parameters,
        # Strict mode makes the arguments conform to `parameters`, so `run`
        # receives what its schema says rather than what the model felt like.
        "strict": True,
    }
