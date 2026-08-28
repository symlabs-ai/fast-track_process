"""Análise empírica de um ciclo executado.

O score estático de `ft analyse-template` mede quanto do processo é preso por
código; esta análise mede o que de fato aconteceu — quantas vezes cada node
precisou ser reexecutado, quanto custou cada loop de correção e onde o
orçamento de tokens foi consumido.

A pergunta que ela responde é a que o score estático não alcança: *qual das
camadas de validação reprova de verdade e qual só ecoa o que outra já pegou*.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


@dataclass
class NodeStats:
    """Custo observado de um node ao longo do ciclo."""

    id: str
    executions: int = 0
    duration_ms: int = 0
    llm_calls: int = 0
    output_tokens: int = 0
    input_tokens: int = 0
    results: list[str] = field(default_factory=list)

    @property
    def reexecutions(self) -> int:
        return max(0, self.executions - 1)

    @property
    def failures(self) -> int:
        return sum(
            1
            for result in self.results
            if str(result).upper() in {"FAIL", "BLOCKED", "REJECTED", "ERROR"}
        )

    @property
    def first_pass(self) -> bool:
        """Passou na primeira: nenhuma reexecução e nenhum resultado de falha."""
        return self.executions <= 1 and self.failures == 0


@dataclass
class CycleAnalysis:
    """Resultado da análise de um ciclo."""

    run_id: str | None = None
    nodes: dict[str, NodeStats] = field(default_factory=dict)
    on_fail_rounds: dict[str, int] = field(default_factory=dict)
    wall_ms: int | None = None
    llm_calls: int = 0
    output_tokens: int = 0
    input_tokens: int = 0

    @property
    def total_executions(self) -> int:
        return sum(node.executions for node in self.nodes.values())

    @property
    def total_reexecutions(self) -> int:
        return sum(node.reexecutions for node in self.nodes.values())

    @property
    def first_pass_rate(self) -> float:
        """Fração dos nodes executados que passaram sem repetir."""
        executed = [node for node in self.nodes.values() if node.executions]
        if not executed:
            return 0.0
        return sum(1 for node in executed if node.first_pass) / len(executed)

    @property
    def rework_ratio(self) -> float:
        """Fração das execuções que foram repetição de trabalho já feito."""
        total = self.total_executions
        if not total:
            return 0.0
        return self.total_reexecutions / total

    def loops(self) -> list[NodeStats]:
        """Nodes que repetiram, do mais caro para o mais barato."""
        repeated = [node for node in self.nodes.values() if node.reexecutions]
        return sorted(
            repeated,
            key=lambda node: (node.output_tokens, node.duration_ms, node.reexecutions),
            reverse=True,
        )

    def silent_layers(self) -> list[NodeStats]:
        """Nodes de validação que nunca reprovaram nada no ciclo.

        Uma camada que só confirma o que outra já garantiu é candidata a
        virar gate determinístico ou a sair — é o custo que não compra risco.
        """
        return sorted(
            (
                node
                for node in self.nodes.values()
                if node.executions
                and node.llm_calls
                and not node.failures
                and _looks_like_validation(node.id)
            ),
            key=lambda node: node.output_tokens,
            reverse=True,
        )


def _looks_like_validation(node_id: str) -> bool:
    lowered = node_id.lower()
    return any(
        token in lowered
        for token in ("review", "audit", "verify", "validate", "check", "acceptance")
    )


def _as_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value)


def analyse_run_report(
    report: Mapping[str, Any],
    state_metrics: Mapping[str, Any] | None = None,
) -> CycleAnalysis:
    """Consolida o relatório de trace de um ciclo em custo por node."""
    analysis = CycleAnalysis(run_id=report.get("run_id"))

    wall = report.get("wall")
    if isinstance(wall, Mapping):
        duration = wall.get("duration_ms")
        analysis.wall_ms = duration if isinstance(duration, int) else None

    llm = report.get("llm")
    if isinstance(llm, Mapping):
        analysis.llm_calls = _as_int(llm.get("calls"))
        analysis.output_tokens = _as_int(llm.get("output_tokens"))
        analysis.input_tokens = _as_int(llm.get("input_tokens"))

    spans = report.get("spans")
    if not isinstance(spans, Sequence):
        spans = []

    for span in spans:
        if not isinstance(span, Mapping):
            continue
        node_id = span.get("node_id")
        if not isinstance(node_id, str) or not node_id.strip():
            continue
        stats = analysis.nodes.setdefault(node_id, NodeStats(id=node_id))
        category = span.get("category")
        duration = span.get("duration_ms")
        if category == "node":
            stats.executions += 1
            if isinstance(duration, int):
                stats.duration_ms += duration
            result = span.get("result") or span.get("status")
            if isinstance(result, str) and result.strip():
                stats.results.append(result)
        elif category == "llm":
            stats.llm_calls += 1
            metrics = span.get("metrics")
            if isinstance(metrics, Mapping):
                stats.output_tokens += _as_int(metrics.get("output_tokens"))
                stats.input_tokens += _as_int(metrics.get("input_tokens"))

    if isinstance(state_metrics, Mapping):
        rounds = state_metrics.get("on_fail_rounds")
        if isinstance(rounds, Mapping):
            analysis.on_fail_rounds = {
                str(key): _as_int(value) for key, value in rounds.items()
            }
    return analysis
