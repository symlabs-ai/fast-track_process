"""
CycleManager — gerencia ciclos (cycle-01, cycle-02, ...) (RF-04).
Opera sobre o mesmo arquivo de estado do StateManager.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from ft.engine.state import StateManager, _atomic_write_state


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
        from ft.engine.layout import _manifest_write_lock

        with _manifest_write_lock(self.path):
            _atomic_write_state(self.path, data)

    def current_cycle(self) -> str:
        return self._load_raw().get("current_cycle", "cycle-01")

    def advance_cycle(self, first_node: str | None = None) -> None:
        """Avança para o próximo ciclo, resetando steps_completed."""
        from ft.engine.layout import _manifest_write_lock

        with _manifest_write_lock(self.path):
            data = self._load_raw()
            StateManager(self.path)._check_lock(data)
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

            _atomic_write_state(self.path, data)

    def cycle_history(self) -> list[str]:
        return self._load_raw().get("cycle_history", [])
