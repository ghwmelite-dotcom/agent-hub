"""Loads agent role definitions from YAML files in `roles/`."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

_ROLES_DIR = Path(__file__).resolve().parent / "roles"


class AgentRole(BaseModel):
    """A single agent's persona, tool allowlist, and model choice."""

    name: str
    display_name: str
    aliases: list[str] = Field(default_factory=list)
    model: str
    allowed_tools: list[str] = Field(default_factory=list)
    system_prompt: str


class AgentRegistry:
    """Loads and looks up agent roles. Looks up by name OR any alias."""

    def __init__(self, roles: list[AgentRole]):
        self._roles: dict[str, AgentRole] = {r.name: r for r in roles}
        self._aliases: dict[str, str] = {}
        for role in roles:
            self._aliases[role.name.lower()] = role.name
            for alias in role.aliases:
                self._aliases[alias.lower()] = role.name

    @classmethod
    def load(cls, roles_dir: Path | None = None) -> "AgentRegistry":
        roles_dir = roles_dir or _ROLES_DIR
        roles: list[AgentRole] = []
        for yaml_path in sorted(roles_dir.glob("*.yaml")):
            with yaml_path.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
            roles.append(AgentRole(**data))
        if not roles:
            raise RuntimeError(f"No agent roles found in {roles_dir}")
        return cls(roles)

    def all(self) -> list[AgentRole]:
        return list(self._roles.values())

    def names(self) -> list[str]:
        return list(self._roles.keys())

    def get(self, name_or_alias: str) -> AgentRole:
        canonical = self._aliases.get(name_or_alias.lower())
        if not canonical:
            raise KeyError(f"Unknown agent: {name_or_alias!r}")
        return self._roles[canonical]

    def resolve(self, name_or_alias: str) -> str | None:
        """Return the canonical agent name for a possible alias, or None."""
        return self._aliases.get(name_or_alias.lower())
