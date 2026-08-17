"""Tests for the one seam between this system and OpenAI.

Every one of these runs offline. The seam that makes that possible is the
`transport` argument: `structured()` calls a function, and the default happens to
be `client.responses.parse`. A test hands it a fake instead. That is the whole
mechanism — no patching, no recorded cassettes, no mocking framework — and it is
what lets the reviewer, the distiller and codegen all be tested with no key.

The last test is the exception: it makes a real call, and skips when there is no
key or no credit behind it.
"""

from dataclasses import dataclass
from typing import Any

import pytest
from openai import APIStatusError
from pydantic import BaseModel

from meridian.core.config import settings
from meridian.core.llm import (
    MAX_ITERATIONS,
    Completed,
    LLMError,
    Task,
    Tool,
    model_for,
    structured,
)


class Answer(BaseModel):
    """The schema every offline test asks for."""

    value: int


# ── the fake transport ───────────────────────────────────────────────────
# Stubs, not mocks: they carry the three attributes `structured()` reads off a
# response and nothing else. If the loop starts reading a fourth, these fail to
# provide it and the test says so.


@dataclass
class StubToolCall:
    """What the model asks for, in the shape the Responses API returns it."""

    name: str
    arguments: str
    call_id: str = "call_1"
    type: str = "function_call"


@dataclass
class StubResponse:
    """One turn from the model: some tool calls, or a parsed object."""

    output: list[Any]
    output_parsed: Any = None


class FakeTransport:
    """Serves canned turns in order, and records everything it was sent.

    Past the end it repeats the final turn, which is what lets one tool-calling
    response stand in for a model that never stops asking.
    """

    def __init__(self, *turns: StubResponse) -> None:
        self.turns = list(turns)
        self.sent: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> Any:
        self.sent.append(kwargs)
        return self.turns[min(len(self.sent) - 1, len(self.turns) - 1)]


def answered(value: int) -> StubResponse:
    return StubResponse(output=[], output_parsed=Answer(value=value))


def asks_for(name: str, arguments: str = "{}", call_id: str = "call_1") -> StubResponse:
    return StubResponse(output=[StubToolCall(name=name, arguments=arguments, call_id=call_id)])


def tool(name: str, result: Any, calls: list[dict[str, Any]] | None = None) -> Tool:
    async def run(arguments: dict[str, Any]) -> Any:
        if calls is not None:
            calls.append(arguments)
        return result

    return Tool(
        name=name,
        description=f"returns {result!r}",
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
        run=run,
    )


# ── the object comes back ────────────────────────────────────────────────


async def test_the_validated_object_comes_back():
    transport = FakeTransport(answered(4))

    completed = await structured(Task.DISTIL, Answer, "system", "user", transport=transport)

    assert completed == Completed(value=Answer(value=4), calls=())


async def test_the_prompt_is_split_into_instructions_and_input():
    # The system prompt is `instructions`, not a first user turn: it must survive
    # every iteration of the tool loop, and only `input` grows.
    transport = FakeTransport(answered(1))

    await structured(Task.DISTIL, Answer, "you are a reviewer", "this board", transport=transport)

    sent = transport.sent[0]
    assert sent["instructions"] == "you are a reviewer"
    assert sent["input"] == [{"role": "user", "content": "this board"}]
    assert sent["text_format"] is Answer


async def test_temperature_is_pinned_to_zero():
    # Two runs on one board have to agree, or the reviewer's determinism test
    # measures the sampler rather than the prompt.
    transport = FakeTransport(answered(1))

    await structured(Task.REVIEW, Answer, "s", "u", transport=transport)

    assert transport.sent[0]["temperature"] == 0


# ── tools ────────────────────────────────────────────────────────────────


async def test_a_tool_reaches_the_model_as_a_json_schema():
    transport = FakeTransport(answered(1))

    await structured(Task.REVIEW, Answer, "s", "u", tools=(tool("lucky", 7),), transport=transport)

    assert transport.sent[0]["tools"] == [
        {
            "type": "function",
            "name": "lucky",
            "description": "returns 7",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            "strict": True,
        }
    ]


async def test_a_requested_tool_is_run_with_its_parsed_arguments():
    received: list[dict[str, Any]] = []
    transport = FakeTransport(asks_for("lookup", '{"batch": "UAC25022"}'), answered(1))

    await structured(
        Task.REVIEW,
        Answer,
        "s",
        "u",
        tools=(tool("lookup", "found", calls=received),),
        transport=transport,
    )

    assert received == [{"batch": "UAC25022"}]


async def test_a_tool_result_is_sent_back_to_the_model():
    transport = FakeTransport(asks_for("lookup", "{}", call_id="call_9"), answered(1))

    await structured(
        Task.REVIEW, Answer, "s", "u", tools=(tool("lookup", "found"),), transport=transport
    )

    # The call is echoed before its output — the API rejects an output whose
    # call it has not seen in this input.
    assert transport.sent[1]["input"][1:] == [
        {"type": "function_call", "call_id": "call_9", "name": "lookup", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "call_9", "output": '"found"'},
    ]


async def test_completed_records_every_call_in_order():
    # Tool calls are product data, not debug output: they become the evidence a
    # review comment cites, so discarding them loses the citation.
    transport = FakeTransport(
        asks_for("first", '{"n": 1}'),
        asks_for("second", '{"n": 2}'),
        answered(3),
    )

    completed = await structured(
        Task.REVIEW,
        Answer,
        "s",
        "u",
        tools=(tool("first", "a"), tool("second", "b")),
        transport=transport,
    )

    assert [(c.name, c.arguments, c.result) for c in completed.calls] == [
        ("first", {"n": 1}, "a"),
        ("second", {"n": 2}, "b"),
    ]


async def test_a_tool_the_caller_never_declared_is_refused():
    transport = FakeTransport(asks_for("delete_everything"), answered(1))

    with pytest.raises(LLMError, match="delete_everything"):
        await structured(
            Task.REVIEW, Answer, "s", "u", tools=(tool("lookup", "x"),), transport=transport
        )


# ── failure ──────────────────────────────────────────────────────────────


async def test_the_iteration_cap_raises_rather_than_looping_forever():
    transport = FakeTransport(asks_for("lookup"))

    with pytest.raises(LLMError, match="8 iterations"):
        await structured(
            Task.REVIEW, Answer, "s", "u", tools=(tool("lookup", "x"),), transport=transport
        )

    assert len(transport.sent) == MAX_ITERATIONS


async def test_a_turn_with_neither_a_tool_call_nor_an_object_is_an_error():
    # A refusal or a truncated response lands here. Returning None would push
    # the failure into a caller that has no idea what went wrong.
    transport = FakeTransport(StubResponse(output=[], output_parsed=None))

    with pytest.raises(LLMError, match="no structured output"):
        await structured(Task.DISTIL, Answer, "s", "u", transport=transport)


# ── the model mapping ────────────────────────────────────────────────────


@pytest.fixture
def configured_model(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENAI_MODEL", "configured-default")
    settings.cache_clear()
    yield "configured-default"
    settings.cache_clear()


def test_a_task_without_an_override_falls_back_to_the_configured_model(configured_model):
    for task in (Task.REVIEW, Task.DISTIL, Task.FILL):
        assert model_for(task) == configured_model


def test_choosing_a_label_from_a_closed_set_uses_the_smallest_model(configured_model):
    # The one override worth having: the answers are fixed before the call, so
    # anything larger pays for reasoning the enum already did.
    assert model_for(Task.CLASSIFY) != configured_model
    assert "nano" in model_for(Task.CLASSIFY)


async def test_the_task_decides_the_model_on_the_wire(configured_model):
    transport = FakeTransport(answered(1))

    await structured(Task.CLASSIFY, Answer, "s", "u", transport=transport)

    assert transport.sent[0]["model"] == model_for(Task.CLASSIFY)


def test_an_unconfigured_model_names_the_variable_to_set(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENAI_MODEL", "")
    settings.cache_clear()
    try:
        with pytest.raises(LLMError, match="OPENAI_MODEL"):
            model_for(Task.DISTIL)
    finally:
        settings.cache_clear()


# ── the one real call ────────────────────────────────────────────────────


@pytest.mark.llm
async def test_a_real_call_returns_the_schema():
    """The only test that leaves the machine. A few tokens, no tools."""
    if not settings().openai_api_key:
        pytest.skip("no OPENAI_API_KEY — the offline tests cover the loop")

    # An unavailable external dependency skips rather than fails, the same rule
    # the database fixtures follow: an exhausted quota is not a broken seam.
    try:
        completed = await structured(
            Task.DISTIL,
            Answer,
            "Answer with the number only.",
            "What is two plus two?",
        )
    except APIStatusError as exc:  # 401 wrong key, 429 no credit, 5xx outage
        pytest.skip(f"OpenAI unavailable: {exc.status_code}")

    assert completed.value.value == 4
    assert completed.calls == ()
