"""Contract tests for MessageSurface and FakeMessageSurface."""

import pytest

from agent_hub.orchestrator.surface import MessageSurface


class _FakeSurface(MessageSurface):
    def __init__(self):
        self.sent: list[tuple[int, str]] = []

    async def dm(self, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))


@pytest.mark.asyncio
async def test_message_surface_is_abstract():
    """Cannot instantiate MessageSurface directly — it's a Protocol or ABC."""
    with pytest.raises(TypeError):
        MessageSurface()  # type: ignore[abstract]


@pytest.mark.asyncio
async def test_fake_surface_records_dms():
    surface = _FakeSurface()
    await surface.dm(chat_id=42, text="hello")
    await surface.dm(chat_id=42, text="world")
    assert surface.sent == [(42, "hello"), (42, "world")]
