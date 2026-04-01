"""
MetricsTracker — rastreamento de métricas (RF-05).
Opera sobre o campo 'metrics' do arquivo de estado.
"""
from __future__ import annotations

from pathlib import Path

import yaml


class MetricsTracker:
    """Rastreia e persiste métricas do processo."""

    def __init__(self, state_path: str | Path):
        self.path = Path(state_path)

    def _load_metrics(self) -> dict:
        if self.path.exists():
            with open(self.path) as f:
                data = yaml.safe_load(f) or {}
            return data.get("metrics", {})
        return {}

    def _save_metrics(self, metrics: dict) -> None:
        data: dict = {}
        if self.path.exists():
            with open(self.path) as f:
                data = yaml.safe_load(f) or {}
        data["metrics"] = metrics
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    def add_tokens(self, n: int) -> None:
        m = self._load_metrics()
        m["tokens_used"] = m.get("tokens_used", 0) + n
        self._save_metrics(m)

    def total_tokens(self) -> int:
        return self._load_metrics().get("tokens_used", 0)

    def update_coverage(self, pct: float) -> None:
        m = self._load_metrics()
        m["coverage"] = pct
        self._save_metrics(m)

    def current_coverage(self) -> float:
        return self._load_metrics().get("coverage", 0)

    def record_llm_call(self) -> None:
        m = self._load_metrics()
        m["llm_calls"] = m.get("llm_calls", 0) + 1
        self._save_metrics(m)

    def total_llm_calls(self) -> int:
        return self._load_metrics().get("llm_calls", 0)

    def summary(self) -> dict:
        m = self._load_metrics()
        return {
            "tokens_used": m.get("tokens_used", 0),
            "coverage": m.get("coverage", 0),
            "llm_calls": m.get("llm_calls", 0),
        }
