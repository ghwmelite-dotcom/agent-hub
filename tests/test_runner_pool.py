"""Tests for the per-(agent, task) client pool keying.

We do NOT spin up real ClaudeSDKClients here — we monkey-patch the
runner's internal SDK factory so we can observe how the pool is keyed
and how cwd flows through. Real-SDK tests land in Plan 3 (FakeAgentRunner)
and Plan 4 (Haiku smoke).
"""

from pathlib import Path

import pytest

from agent_hub.agents import AgentRegistry
from agent_hub.agents.runner import AgentRunner
from agent_hub.config import Settings


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        telegram_bot_token="dummy",
        telegram_allowed_user_id=1,
        agent_workspaces=[],
        database_path=tmp_path / "agent_hub.db",
    )


class _FakeClient:
    def __init__(self, options):
        self.options = options
        self.connected = False
        self.disconnected = False

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.disconnected = True


@pytest.fixture
async def patched_runner(monkeypatch, tmp_path):
    """A runner whose SDK is mocked. Returns (runner, created_clients_list).

    Also initializes the DB so worktree lookups in `_get_or_create_client`
    can run without raising. Tests that need worktree rows seed them
    explicitly.
    """
    created: list[_FakeClient] = []

    def fake_client_factory(options):
        c = _FakeClient(options)
        created.append(c)
        return c

    monkeypatch.setattr(
        "agent_hub.agents.runner._client_factory", fake_client_factory, raising=False
    )

    # Initialize the DB so WorktreeRepository.get_by_task() works.
    from agent_hub.db import Database
    settings = _settings(tmp_path)
    db = Database(settings.database_path)
    await db.init()

    registry = AgentRegistry.load()
    runner = AgentRunner(settings=settings, registry=registry)
    return runner, created


@pytest.mark.asyncio
async def test_get_client_caches_per_agent_when_no_task_id(patched_runner):
    runner, created = patched_runner
    c1 = await runner._get_or_create_client("pm", task_id=None, cwd=None)
    c2 = await runner._get_or_create_client("pm", task_id=None, cwd=None)
    # Same call, same key → reused.
    assert c1 is c2
    assert len(created) == 1


@pytest.mark.asyncio
async def test_get_client_keys_by_task_id(patched_runner):
    runner, created = patched_runner
    c_task5 = await runner._get_or_create_client("pm", task_id=5, cwd=None)
    c_task7 = await runner._get_or_create_client("pm", task_id=7, cwd=None)
    # Different task_id → different client.
    assert c_task5 is not c_task7
    assert len(created) == 2


@pytest.mark.asyncio
async def test_get_client_different_agents_different_clients(patched_runner):
    runner, created = patched_runner
    pm_client = await runner._get_or_create_client("pm", task_id=5, cwd=None)
    arch_client = await runner._get_or_create_client("architect", task_id=5, cwd=None)
    assert pm_client is not arch_client
    assert len(created) == 2


@pytest.mark.asyncio
async def test_get_client_passes_cwd_into_options(patched_runner, tmp_path):
    runner, created = patched_runner
    cwd = tmp_path / "wt" / "5"
    cwd.mkdir(parents=True)
    client = await runner._get_or_create_client("pm", task_id=5, cwd=cwd)
    assert client.options.cwd == str(cwd)


@pytest.mark.asyncio
async def test_shutdown_disconnects_all_pool_entries(patched_runner):
    runner, created = patched_runner
    await runner._get_or_create_client("pm", task_id=1, cwd=None)
    await runner._get_or_create_client("pm", task_id=2, cwd=None)
    await runner._get_or_create_client("architect", task_id=1, cwd=None)
    await runner.shutdown()
    assert all(c.disconnected for c in created)


@pytest.mark.asyncio
async def test_send_uses_worktree_path_for_task(patched_runner, tmp_path):
    """When task_id is given AND a worktree is recorded for it, the runner
    should construct the client with cwd=that worktree path (not the
    global workspace)."""
    runner, created = patched_runner

    # Seed: init the DB and record a worktree for task_id=42.
    from agent_hub.db import Database
    from agent_hub.tasks.repository import TaskRepository
    from agent_hub.tasks.worktree_repo import WorktreeRepository

    db_path = runner.settings.database_path
    db = Database(db_path)
    await db.init()
    repo = TaskRepository(db_path)
    wt_repo = WorktreeRepository(db_path)
    task = await repo.create(title="x", description="-", origin_chat_id=1)
    fake_wt_path = tmp_path / "worktrees" / str(task.id)
    fake_wt_path.mkdir(parents=True)
    await wt_repo.record(
        task_id=task.id, path=str(fake_wt_path),
        branch="task/x", base_branch="main",
    )

    client = await runner._get_or_create_client("pm", task_id=task.id, cwd=None)
    # Even though cwd=None was passed, the runner should have resolved
    # cwd via the worktree path for this task_id.
    assert client.options.cwd == str(fake_wt_path)


@pytest.mark.asyncio
async def test_worktree_wins_over_global_workspace(patched_runner, tmp_path):
    """Even when the runner has a global cwd set, task_id's worktree takes precedence."""
    runner, _ = patched_runner

    # Set a global workspace (simulates production after set_workspace).
    global_ws = tmp_path / "global-workspace"
    global_ws.mkdir()
    runner.set_workspace(global_ws)

    # Record a worktree for the task.
    from agent_hub.tasks.repository import TaskRepository
    from agent_hub.tasks.worktree_repo import WorktreeRepository

    repo = TaskRepository(runner.settings.database_path)
    wt_repo = WorktreeRepository(runner.settings.database_path)
    task = await repo.create(title="x", description="-", origin_chat_id=1)
    wt_path = tmp_path / "worktrees" / str(task.id)
    wt_path.mkdir(parents=True)
    await wt_repo.record(
        task_id=task.id, path=str(wt_path), branch="task/x", base_branch="main",
    )

    # Critical: this mirrors how send() actually calls — cwd=self._cwd (non-None).
    client = await runner._get_or_create_client("pm", task_id=task.id, cwd=global_ws)

    # Worktree path must win.
    assert client.options.cwd == str(wt_path)
    # And NOT be the global workspace.
    assert client.options.cwd != str(global_ws)


@pytest.mark.asyncio
async def test_no_task_id_uses_caller_cwd(patched_runner, tmp_path):
    """When no task_id, caller-passed cwd is honoured."""
    runner, _ = patched_runner
    explicit = tmp_path / "explicit"
    explicit.mkdir()
    client = await runner._get_or_create_client("pm", task_id=None, cwd=explicit)
    assert client.options.cwd == str(explicit)
