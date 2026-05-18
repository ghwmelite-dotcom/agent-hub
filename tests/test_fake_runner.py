"""Tests for the FakeAgentRunner — a test double that replays scripted
events instead of calling the real Claude SDK."""

import pytest

from agent_hub.agents.runner import TextChunk, ToolStart, ToolEnd, TurnDone
from tests.fakes.fake_runner import FakeAgentRunner, scripted_turn


@pytest.fixture
def fake_runner():
    runner = FakeAgentRunner()
    return runner


@pytest.mark.asyncio
async def test_script_a_single_turn(fake_runner):
    fake_runner.script("pm", task_id=1, events=[
        TextChunk(text="Hi"),
        ToolStart(tool="tasks.create", input={"title": "x"}),
        ToolEnd(tool="tasks.create", is_error=False),
        TurnDone(cost_usd=0.01, duration_ms=100),
    ])

    events = []
    async for event in fake_runner.send("pm", "go", task_id=1):
        events.append(event)

    assert len(events) == 4
    assert isinstance(events[0], TextChunk)
    assert events[0].text == "Hi"
    assert isinstance(events[-1], TurnDone)


@pytest.mark.asyncio
async def test_unscripted_send_raises(fake_runner):
    """If no script is set for (agent, task_id), send() raises."""
    with pytest.raises(AssertionError) as exc:
        async for _ in fake_runner.send("pm", "go", task_id=99):
            pass
    assert "no script" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_multi_turn_script(fake_runner):
    """Each call to script() queues a turn. Each send() pops one."""
    fake_runner.script("pm", task_id=1, events=[TextChunk(text="first")])
    fake_runner.script("pm", task_id=1, events=[TextChunk(text="second")])

    first = [e async for e in fake_runner.send("pm", "msg1", task_id=1)]
    second = [e async for e in fake_runner.send("pm", "msg2", task_id=1)]

    assert first[0].text == "first"
    assert second[0].text == "second"


@pytest.mark.asyncio
async def test_send_records_messages(fake_runner):
    fake_runner.script("pm", task_id=1, events=[TextChunk(text="ok")])
    async for _ in fake_runner.send("pm", "hello", task_id=1):
        pass
    assert fake_runner.calls == [("pm", "hello", 1)]


def test_scripted_turn_helper():
    """The scripted_turn helper builds a turn from text + optional tool calls."""
    turn = scripted_turn(text="hello", tools=[("tasks.create", {"title": "x"})])
    kinds = [type(e).__name__ for e in turn]
    assert kinds == ["TextChunk", "ToolStart", "ToolEnd", "TurnDone"]
