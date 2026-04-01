"""Acceptance Matrix — gera e gerencia cenários happy/edge/error."""

from __future__ import annotations

import uuid
from typing import Any

VALID_CATEGORIES = {"happy", "edge", "error"}


class AcceptanceMatrix:
    def __init__(self):
        self._scenarios: list[dict[str, Any]] = []

    def add_scenario(self, category: str, user_story: str, description: str) -> None:
        if category not in VALID_CATEGORIES:
            raise ValueError(
                f"categoria inválida: '{category}'. Valores válidos: {VALID_CATEGORIES}"
            )
        self._scenarios.append({
            "id": str(uuid.uuid4()),
            "category": category,
            "user_story": user_story,
            "description": description,
        })

    def get_scenarios(self, category: str) -> list[dict[str, Any]]:
        return [s for s in self._scenarios if s["category"] == category]

    def all_scenarios(self) -> list[dict[str, Any]]:
        return list(self._scenarios)

    def total_count(self) -> int:
        return len(self._scenarios)

    def count_by_category(self) -> dict[str, int]:
        counts: dict[str, int] = {c: 0 for c in VALID_CATEGORIES}
        for s in self._scenarios:
            counts[s["category"]] += 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenarios": list(self._scenarios),
            "counts": self.count_by_category(),
            "total": self.total_count(),
        }
