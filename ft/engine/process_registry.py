"""
ProcessRegistry — garante unicidade de process_id (RF-01).
"""
from __future__ import annotations

from pathlib import Path

import yaml


class ProcessRegistry:
    """Registro persistente de process_ids únicos."""

    def __init__(self, registry_path: str | Path):
        self.path = Path(registry_path)

    def _load(self) -> list[str]:
        if self.path.exists():
            with open(self.path) as f:
                data = yaml.safe_load(f) or {}
            return data.get("ids", [])
        return []

    def _save(self, ids: list[str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w") as f:
            yaml.dump({"ids": ids}, f, default_flow_style=False, allow_unicode=True)

    def register(self, process_id: str) -> bool:
        """Registra um novo process_id. Retorna True ou lança ValueError."""
        if not process_id:
            raise ValueError("process_id inválido: não pode ser vazio ou None")
        ids = self._load()
        if process_id in ids:
            raise ValueError(f"process_id '{process_id}' já existe / already exists")
        ids.append(process_id)
        self._save(ids)
        return True

    def is_registered(self, process_id: str) -> bool:
        return process_id in self._load()

    def list_ids(self) -> list[str]:
        return self._load()
