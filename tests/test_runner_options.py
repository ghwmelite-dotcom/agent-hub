from pathlib import Path

import pytest

from agent_hub.agents.registry import AgentRole
from agent_hub.agents.runner_options import build_mcp_server_config, build_sdk_options


def _role() -> AgentRole:
    return AgentRole(
        name="pm",
        display_name="PM",
        aliases=["pm"],
        model="claude-sonnet-4-6",
        allowed_tools=["Read", "Bash"],
        system_prompt="You are PM.",
    )


def test_build_mcp_server_config_shape(tmp_path: Path):
    db_path = tmp_path / "agent_hub.db"
    config = build_mcp_server_config(db_path)
    # Expected shape: dict with "agent_hub" key mapping to a stdio launch spec.
    assert "agent_hub" in config
    spec = config["agent_hub"]
    assert spec["command"]  # python executable
    assert spec["args"] == ["-m", "agent_hub.mcp_server"]
    assert spec["env"]["AGENT_HUB_DB"] == str(db_path)


@pytest.mark.asyncio
async def test_build_sdk_options_uses_role_fields(tmp_path: Path):
    role = _role()
    opts = await build_sdk_options(role, cwd=None, db_path=tmp_path / "x.db")
    # We don't bind to the exact ClaudeAgentOptions API surface — just
    # check the attributes we care about. Whatever object is returned
    # must carry these fields.
    assert opts.system_prompt == "You are PM."
    assert opts.model == "claude-sonnet-4-6"
    assert set(opts.allowed_tools) == {"Read", "Bash"}
    assert opts.cwd is None


@pytest.mark.asyncio
async def test_build_sdk_options_sets_cwd_when_given(tmp_path: Path):
    from agent_hub.db import Database

    db_path = tmp_path / "x.db"
    await Database(db_path).init()
    role = _role()
    cwd = tmp_path / "wt" / "1"
    cwd.mkdir(parents=True)
    opts = await build_sdk_options(role, cwd=cwd, db_path=db_path)
    assert opts.cwd == str(cwd)


@pytest.mark.asyncio
async def test_build_sdk_options_includes_mcp_servers(tmp_path: Path):
    role = _role()
    db_path = tmp_path / "x.db"
    opts = await build_sdk_options(role, cwd=None, db_path=db_path)
    # mcp_servers must include the agent_hub entry.
    assert "agent_hub" in opts.mcp_servers
    assert opts.mcp_servers["agent_hub"]["env"]["AGENT_HUB_DB"] == str(db_path)


@pytest.mark.asyncio
async def test_build_sdk_options_passes_session_id_when_provided(tmp_path: Path):
    """session_id pinning lets the CLI re-open the same conversation
    after a restart — essential for per-task resume."""
    role = _role()
    sid = "11111111-2222-3333-4444-555555555555"
    opts = await build_sdk_options(
        role, cwd=None, db_path=tmp_path / "x.db", session_id=sid,
    )
    assert opts.session_id == sid


@pytest.mark.asyncio
async def test_build_sdk_options_omits_session_id_by_default(tmp_path: Path):
    role = _role()
    opts = await build_sdk_options(role, cwd=None, db_path=tmp_path / "x.db")
    assert opts.session_id is None


@pytest.mark.asyncio
async def test_build_sdk_options_appends_memory_section(tmp_path, monkeypatch):
    """When memory exists for the workspace+role, it's appended to system_prompt."""
    from agent_hub.db import Database
    from agent_hub.memory.store import MemoryStore
    from agent_hub.agents.registry import AgentRole
    from agent_hub.agents.runner_options import build_sdk_options

    # Fake the SDK import to capture kwargs without spawning anything.
    captured = {}
    class _FakeOptions:
        def __init__(self, **kwargs):
            captured.update(kwargs)
    monkeypatch.setattr(
        "claude_agent_sdk.ClaudeAgentOptions", _FakeOptions, raising=False,
    )

    db_path = tmp_path / "agent_hub.db"
    db = Database(db_path)
    await db.init()

    ws = tmp_path / "workspace"
    ws.mkdir()
    store = MemoryStore(db_path)
    await store.insert(
        workspace=str(ws), type="project_fact", agent_source="architect",
        title="Stack is Workers + D1", body="b",
    )

    role = AgentRole(
        name="pm", display_name="PM", aliases=[], model="haiku",
        allowed_tools=["Read"], system_prompt="BASE PROMPT",
    )

    await build_sdk_options(role, cwd=ws, db_path=db_path, session_id=None)

    sp = captured["system_prompt"]
    assert "BASE PROMPT" in sp
    assert "## Project memory" in sp
    assert "Stack is Workers + D1" in sp


@pytest.mark.asyncio
async def test_build_sdk_options_no_memory_section_when_empty(tmp_path, monkeypatch):
    from agent_hub.db import Database
    from agent_hub.agents.registry import AgentRole
    from agent_hub.agents.runner_options import build_sdk_options

    captured = {}
    class _FakeOptions:
        def __init__(self, **kwargs):
            captured.update(kwargs)
    monkeypatch.setattr(
        "claude_agent_sdk.ClaudeAgentOptions", _FakeOptions, raising=False,
    )

    db_path = tmp_path / "agent_hub.db"
    db = Database(db_path)
    await db.init()
    ws = tmp_path / "workspace"
    ws.mkdir()

    role = AgentRole(
        name="pm", display_name="PM", aliases=[], model="haiku",
        allowed_tools=["Read"], system_prompt="BASE PROMPT",
    )
    await build_sdk_options(role, cwd=ws, db_path=db_path, session_id=None)
    assert captured["system_prompt"] == "BASE PROMPT"


@pytest.mark.asyncio
async def test_build_sdk_options_no_cwd_skips_memory(tmp_path, monkeypatch):
    """No workspace → no memory injection (memory is workspace-scoped)."""
    from agent_hub.db import Database
    from agent_hub.memory.store import MemoryStore
    from agent_hub.agents.registry import AgentRole
    from agent_hub.agents.runner_options import build_sdk_options

    captured = {}
    class _FakeOptions:
        def __init__(self, **kwargs):
            captured.update(kwargs)
    monkeypatch.setattr(
        "claude_agent_sdk.ClaudeAgentOptions", _FakeOptions, raising=False,
    )

    db_path = tmp_path / "agent_hub.db"
    db = Database(db_path)
    await db.init()
    # Insert memory under SOME workspace
    await MemoryStore(db_path).insert(
        workspace=r"C:\anywhere", type="project_fact", agent_source="x",
        title="X", body="b",
    )

    role = AgentRole(
        name="pm", display_name="PM", aliases=[], model="haiku",
        allowed_tools=["Read"], system_prompt="BASE PROMPT",
    )
    await build_sdk_options(role, cwd=None, db_path=db_path, session_id=None)
    assert "Project memory" not in captured["system_prompt"]


@pytest.mark.asyncio
async def test_fingerprint_mismatch_drops_session(tmp_path, monkeypatch):
    """If stored fingerprint differs from current, session_store.forget is called."""
    from agent_hub.db import Database
    from agent_hub.memory.store import MemoryStore
    from agent_hub.agents.session_store import AgentSessionStore
    from agent_hub.agents.runner import AgentRunner
    from agent_hub.agents.registry import AgentRegistry, AgentRole
    from agent_hub.config import Settings

    async def _fake_connect(self):
        return None

    monkeypatch.setattr(
        "agent_hub.agents.runner._client_factory",
        lambda options: type("FakeClient", (), {
            "connect": _fake_connect,
        })(),
    )
    # Skip real sdk import inside build_sdk_options
    class _FakeOptions:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
    monkeypatch.setattr(
        "claude_agent_sdk.ClaudeAgentOptions", _FakeOptions, raising=False,
    )

    db_path = tmp_path / "agent_hub.db"
    db = Database(db_path)
    await db.init()
    ws = tmp_path / "workspace"
    ws.mkdir()

    role = AgentRole(
        name="pm", display_name="PM", aliases=[], model="haiku",
        allowed_tools=["Read"], system_prompt="BASE",
    )
    registry = AgentRegistry([role])
    settings = Settings(
        telegram_bot_token="fake-token",
        telegram_allowed_user_id=12345,
        database_path=db_path,
        agent_workspaces=[ws],
    )

    runner = AgentRunner(settings, registry)

    # Prime: store a stale fingerprint that won't match current (no memory).
    session_store = AgentSessionStore(db_path)
    await session_store.set_fingerprint(
        agent_name="pm", task_id=1, fingerprint="STALE",
    )
    primed_session = await session_store.get_or_create(
        agent_name="pm", task_id=1,
    )

    # Now insert a memory row — current fingerprint will differ from "STALE".
    await MemoryStore(db_path).insert(
        workspace=str(ws), type="project_fact", agent_source="architect",
        title="X", body="b",
    )

    # Triggers fingerprint compare → forget → fresh UUID.
    client = await runner._get_or_create_client(
        "pm", task_id=1, cwd=ws,
    )
    new_session = await session_store.get(agent_name="pm", task_id=1)
    assert new_session != primed_session
