"""
CycleManager — gerencia ciclos (cycle-01, cycle-02, ...) (RF-04).
Opera sobre o mesmo arquivo de estado do StateManager.
"""
from __future__ import annotations

from pathlib import Path

import yaml


class CycleManager:
    """Gerencia avanço de ciclos no arquivo de estado."""

    def __init__(self, state_path: str | Path):
        self.path = Path(state_path)

    def _load_raw(self) -> dict:
        if self.path.exists():
            with open(self.path) as f:
                return yaml.safe_load(f) or {}
        return {}

    def _save_raw(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    def current_cycle(self) -> str:
        return self._load_raw().get("current_cycle", "cycle-01")

    def advance_cycle(self, first_node: str | None = None) -> None:
        """Avança para o próximo ciclo, resetando steps_completed."""
        data = self._load_raw()
        current = data.get("current_cycle", "cycle-01")

        # Acumula histórico
        history = data.get("cycle_history", [])
        if current not in history:
            history.append(current)

        # Incrementa número do ciclo
        num = int(current.split("-")[1])
        new_cycle = f"cycle-{num + 1:02d}"

        data["current_cycle"] = new_cycle
        data["cycle_history"] = history

        # Reset steps_completed
        metrics = data.get("metrics", {})
        metrics["steps_completed"] = 0
        data["metrics"] = metrics

        if first_node is not None:
            data["current_node"] = first_node

        self._save_raw(data)

    def cycle_history(self) -> list[str]:
        return self._load_raw().get("cycle_history", [])
