"""Agent Policy — define escopos e permissões por papel de agente."""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ft.engine.state import StateManager


class AgentRole(Enum):
    FT_MANAGER = "ft_manager"
    FT_COACH = "ft_coach"
    FT_GATEKEEPER = "ft_gatekeeper"
    FT_ACCEPTANCE = "ft_acceptance"
    FORGE_CODER = "forge_coder"


_ALLOWED_PATHS: dict[AgentRole, list[str]] = {
    AgentRole.FT_MANAGER: [".", "src", "tests", "docs", "ft"],
    AgentRole.FT_COACH: ["docs"],
    AgentRole.FT_GATEKEEPER: [],
    AgentRole.FT_ACCEPTANCE: ["tests"],
    AgentRole.FORGE_CODER: ["src", "tests"],
}

_CAN_ADVANCE: dict[AgentRole, bool] = {
    AgentRole.FT_MANAGER: True,
    AgentRole.FT_COACH: False,
    AgentRole.FT_GATEKEEPER: False,
    AgentRole.FT_ACCEPTANCE: False,
    AgentRole.FORGE_CODER: False,
}


class AgentPolicy:
    def can_advance_node(self, role: AgentRole) -> bool:
        return _CAN_ADVANCE.get(role, False)

    def allowed_paths(self, role: AgentRole) -> list[str]:
        return list(_ALLOWED_PATHS.get(role, []))

    def assert_allowed(self, role: AgentRole, path: str) -> None:
        allowed = self.allowed_paths(role)
        for prefix in allowed:
            if path.startswith(prefix) or prefix == ".":
                return
        raise PermissionError(
            f"Agente '{role.value}' não tem permissão (escopo não permitido): {path}"
        )

    def advance_as(self, role: AgentRole, state_manager: "StateManager", current_node: str, next_node: str) -> None:
        if not self.can_advance_node(role):
            raise PermissionError(
                f"Apenas ft_manager pode avançar nodes. Role '{role.value}' não tem permissão."
            )
        state_manager.advance(current_node, next_node)
