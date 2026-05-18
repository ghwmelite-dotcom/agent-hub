"""Static contract tests on each role's allowed_tools.

These tests describe the canonical capability matrix for the team.
Adding a new role: add a row to EXPECTED. Adding a new MCP tool that
a role needs: update the role's row. Tests fail if a role is missing
a tool the workflow requires.
"""

import pytest

from agent_hub.agents.registry import AgentRegistry


# (role_name, list of MCP tool names the role MUST have in allowed_tools)
EXPECTED_MCP_TOOLS: dict[str, list[str]] = {
    "pm": [
        "mcp__agent_hub__tasks_create",
        "mcp__agent_hub__tasks_get",
        "mcp__agent_hub__tasks_list",
        "mcp__agent_hub__tasks_update",
        "mcp__agent_hub__tasks_comment",
        "mcp__agent_hub__handoff",
    ],
    "architect": [
        "mcp__agent_hub__tasks_get",
        "mcp__agent_hub__tasks_comment",
        "mcp__agent_hub__tasks_update",
        "mcp__agent_hub__gate_request",
    ],
    "fullstack-engineer": [
        "mcp__agent_hub__tasks_get",
        "mcp__agent_hub__tasks_update",
        "mcp__agent_hub__tasks_comment",
        "mcp__agent_hub__worktree_path",
        "mcp__agent_hub__handoff",
    ],
    "implementer": [
        "mcp__agent_hub__tasks_get",
        "mcp__agent_hub__tasks_update",
        "mcp__agent_hub__tasks_comment",
        "mcp__agent_hub__worktree_path",
        "mcp__agent_hub__handoff",
    ],
    "reviewer": [
        "mcp__agent_hub__tasks_get",
        "mcp__agent_hub__tasks_comment",
        "mcp__agent_hub__handoff",
    ],
    "qa": [
        "mcp__agent_hub__tasks_get",
        "mcp__agent_hub__tasks_comment",
        "mcp__agent_hub__tasks_update",
        "mcp__agent_hub__handoff",
    ],
    "researcher": [
        "mcp__agent_hub__tasks_get",
        "mcp__agent_hub__tasks_comment",
    ],
    "senior-uiux-designer": [
        "mcp__agent_hub__tasks_get",
        "mcp__agent_hub__tasks_comment",
    ],
}


@pytest.fixture
def registry() -> AgentRegistry:
    return AgentRegistry.load()


@pytest.mark.parametrize("role_name,required_tools", list(EXPECTED_MCP_TOOLS.items()))
def test_role_allowed_tools_contains_required_mcp_tools(registry, role_name, required_tools):
    role = registry.get(role_name)
    missing = [t for t in required_tools if t not in role.allowed_tools]
    assert not missing, (
        f"Role {role_name!r} is missing required MCP tools: {missing}. "
        f"Current allowed_tools: {role.allowed_tools}"
    )


@pytest.mark.parametrize("role_name", list(EXPECTED_MCP_TOOLS.keys()))
def test_role_system_prompt_mentions_mcp_tools(registry, role_name):
    """Sanity check that the role's prompt references the task system."""
    role = registry.get(role_name)
    prompt = role.system_prompt.lower()
    assert any(keyword in prompt for keyword in (
        "mcp__agent_hub__tasks_create",
        "mcp__agent_hub__tasks_get",
        "mcp__agent_hub__handoff",
        "mcp__agent_hub__gate_request",
    )), f"Role {role_name!r} prompt doesn't mention any orchestration tool."
