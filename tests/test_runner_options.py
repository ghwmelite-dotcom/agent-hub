from pathlib import Path

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


def test_build_sdk_options_uses_role_fields(tmp_path: Path):
    role = _role()
    opts = build_sdk_options(role, cwd=None, db_path=tmp_path / "x.db")
    # We don't bind to the exact ClaudeAgentOptions API surface — just
    # check the attributes we care about. Whatever object is returned
    # must carry these fields.
    assert opts.system_prompt == "You are PM."
    assert opts.model == "claude-sonnet-4-6"
    assert set(opts.allowed_tools) == {"Read", "Bash"}
    assert opts.cwd is None


def test_build_sdk_options_sets_cwd_when_given(tmp_path: Path):
    role = _role()
    cwd = tmp_path / "wt" / "1"
    cwd.mkdir(parents=True)
    opts = build_sdk_options(role, cwd=cwd, db_path=tmp_path / "x.db")
    assert opts.cwd == str(cwd)


def test_build_sdk_options_includes_mcp_servers(tmp_path: Path):
    role = _role()
    db_path = tmp_path / "x.db"
    opts = build_sdk_options(role, cwd=None, db_path=db_path)
    # mcp_servers must include the agent_hub entry.
    assert "agent_hub" in opts.mcp_servers
    assert opts.mcp_servers["agent_hub"]["env"]["AGENT_HUB_DB"] == str(db_path)


def test_build_sdk_options_passes_session_id_when_provided(tmp_path: Path):
    """session_id pinning lets the CLI re-open the same conversation
    after a restart — essential for per-task resume."""
    role = _role()
    sid = "11111111-2222-3333-4444-555555555555"
    opts = build_sdk_options(
        role, cwd=None, db_path=tmp_path / "x.db", session_id=sid,
    )
    assert opts.session_id == sid


def test_build_sdk_options_omits_session_id_by_default(tmp_path: Path):
    role = _role()
    opts = build_sdk_options(role, cwd=None, db_path=tmp_path / "x.db")
    assert opts.session_id is None
