"""Contract tests for MessageSurface and FakeMessageSurface."""

import pytest

from agent_hub.orchestrator.surface import MessageSurface
from tests.fakes.fake_surface import FakeMessageSurface


@pytest.mark.asyncio
async def test_message_surface_is_abstract():
    with pytest.raises(TypeError):
        MessageSurface()  # type: ignore[abstract]


@pytest.mark.asyncio
async def test_fake_surface_records_dms():
    surface = FakeMessageSurface()
    await surface.dm(chat_id=42, text="hello")
    await surface.dm(chat_id=42, text="world")
    assert surface.sent == [(42, "hello"), (42, "world")]


@pytest.mark.asyncio
async def test_fake_surface_dms_to_chat_filter():
    surface = FakeMessageSurface()
    await surface.dm(chat_id=10, text="a")
    await surface.dm(chat_id=20, text="b")
    await surface.dm(chat_id=10, text="c")
    assert surface.dms_to(10) == ["a", "c"]
    assert surface.dms_to(20) == ["b"]
