"""
Process YAML Validator — valida schema, grafo e semântica do processo.

Usado por `ft validate` para verificar se um processo customizado é válido
antes de executar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ft.engine.graph import ProcessGraph, Node


VALID_NODE_TYPES = frozenset({
    "discovery", "document", "build", "test_red", "test_green",
    "refactor", "review", "retro", "gate", "decision", "sync", "end",
})

VALID_EXECUTORS = frozenset({
    "python", "llm_coder", "llm_coach", "human",
})


@dataclass
class Issue:
    level: str  # "error" | "warning"
    node_id: str | None
    message: str


@dataclass
class ValidationReport:
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "warning"]

    @property
    def passed(self) -> bool:
        return len(self.errors) == 0

    def add_error(self, node_id: str | None, message: str) -> None:
        self.issues.append(Issue("error", node_id, message))

    def add_warning(self, node_id: str | None, message: str) -> None:
        self.issues.append(Issue("warning", node_id, message))


def validate_process(graph: ProcessGraph, validator_registry: dict[str, Any] | None = None) -> ValidationReport:
    """Valida um ProcessGraph completo. Retorna ValidationReport."""
    report = ValidationReport()

    _check_structure(graph, report)
    _check_graph_integrity(graph, report)
    _check_validators(graph, validator_registry or {}, report)
    _check_semantics(graph, report)

    return report


def _check_structure(graph: ProcessGraph, report: ValidationReport) -> None:
    """Verifica schema: tipos, executors, campos obrigatórios."""
    for node in graph.nodes.values():
        if not node.id:
            report.add_error(None, "nó sem id")
        if not node.title:
            report.add_error(node.id, "nó sem title")
        if node.type not in VALID_NODE_TYPES:
            report.add_error(node.id, f"type '{node.type}' inválido (válidos: {', '.join(sorted(VALID_NODE_TYPES))})")
        if node.type != "end" and node.executor and node.executor not in VALID_EXECUTORS:
            report.add_error(node.id, f"executor '{node.executor}' não reconhecido (válidos: {', '.join(sorted(VALID_EXECUTORS))})")


def _check_graph_integrity(graph: ProcessGraph, report: ValidationReport) -> None:
    """Verifica conectividade, nós órfãos, e terminação."""
    ids = set(graph.nodes.keys())
    first_id = next(iter(graph.nodes)).strip() if graph.nodes else None

    # Nós apontados por algum outro nó
    pointed_to: set[str] = set()
    for node in graph.nodes.values():
        if node.next:
            pointed_to.add(node.next)
        if node.branches:
            for target in node.branches.values():
                pointed_to.add(target)

    # Nós órfãos (ninguém aponta para eles, exceto o primeiro)
    for node_id in ids:
        if node_id != first_id and node_id not in pointed_to:
            report.add_error(node_id, "nó órfão — nenhum nó aponta para ele")

    # Alcançabilidade (BFS do primeiro nó)
    if first_id:
        reachable: set[str] = set()
        queue = [first_id]
        while queue:
            current = queue.pop(0)
            if current in reachable:
                continue
            reachable.add(current)
            node = graph.nodes.get(current)
            if not node:
                continue
            if node.next and node.next not in reachable:
                queue.append(node.next)
            if node.branches:
                for target in node.branches.values():
                    if target not in reachable:
                        queue.append(target)

        unreachable = ids - reachable
        for node_id in unreachable:
            report.add_error(node_id, "nó inalcançável a partir do primeiro nó")

    # Verificar que pelo menos um caminho chega ao end
    end_nodes = [n for n in graph.nodes.values() if n.type == "end"]
    if not end_nodes:
        report.add_error(None, "processo sem nó terminal (type=end)")
    elif first_id:
        # Verificar se end é alcançável
        end_id = end_nodes[0].id
        if end_id not in reachable:
            report.add_error(end_id, "nó end não é alcançável a partir do primeiro nó")


def _check_validators(graph: ProcessGraph, registry: dict[str, Any], report: ValidationReport) -> None:
    """Verifica que validators referenciados nos nós existem no registry."""
    if not registry:
        return  # Sem registry, não pode verificar
    for node in graph.nodes.values():
        for validator_spec in node.validators:
            for name in validator_spec.keys():
                if name not in registry:
                    report.add_warning(node.id, f"validator '{name}' não encontrado no registry")


def _check_semantics(graph: ProcessGraph, report: ValidationReport) -> None:
    """Checks semânticos (warnings)."""
    for node in graph.nodes.values():
        if node.type == "end":
            continue

        # Gates devem ter validators
        if node.type == "gate" and not node.validators:
            report.add_warning(node.id, "gate sem validators")

        # Build devem ter outputs
        if node.type == "build" and not node.outputs:
            report.add_warning(node.id, "build sem outputs definidos")

        # Decision devem ter branches
        if node.type == "decision" and not node.branches:
            report.add_warning(node.id, "decision sem branches")

        # LLM nodes sem max_turns
        if node.executor in ("llm_coder", "llm_coach") and not node.max_turns:
            report.add_warning(node.id, "nó LLM sem max_turns (recomendado)")

        # Nó não-terminal sem next e sem branches
        if node.type != "end" and not node.next and not node.branches:
            report.add_error(node.id, "nó não-terminal sem next nem branches")


def format_report(report: ValidationReport, total_nodes: int) -> str:
    """Formata o relatório para exibição no terminal."""
    lines: list[str] = []

    if report.passed:
        lines.append(f"  \u2705 Schema: {total_nodes} nós válidos")
    else:
        error_count = len(report.errors)
        lines.append(f"  \u274c Schema: {error_count} erro(s) encontrado(s)")

    for issue in report.issues:
        prefix = "\u274c" if issue.level == "error" else "\u26a0\ufe0f "
        node_ctx = f"{issue.node_id}: " if issue.node_id else ""
        lines.append(f"  {prefix} {node_ctx}{issue.message}")

    status = "PASS" if report.passed else "FAIL"
    warning_note = f" ({len(report.warnings)} warnings)" if report.warnings else ""
    error_note = f" ({len(report.errors)} erros)" if report.errors else ""
    lines.append(f"\n  Resultado: {status}{error_note}{warning_note}")

    return "\n".join(lines)
