"""
Process YAML Validator — valida schema, grafo e semântica do processo.

Usado por `ft validate` para verificar se um processo customizado é válido
antes de executar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ft.engine.graph import ProcessGraph, Node
from ft.engine.context_profiles import HYPER_MODE_FIELDS, KNOWN_CONTEXT_PROFILES


VALID_NODE_TYPES = frozenset({
    "discovery", "document", "build", "test_red", "test_green",
    "refactor", "review", "retro", "gate", "decision", "sync", "end",
    "human_gate", "exploration",
})

# Nomes PÓS-normalização do graph loader (claude→llm_claude etc.) — o validator
# roda sobre o ProcessGraph carregado, nunca sobre o YAML cru. llm_coder/llm_coach
# são os nomes legados da V2, mantidos por compatibilidade.
VALID_EXECUTORS = frozenset({
    "python", "human",
    "llm_claude", "llm_codex", "llm_gemini", "llm_opencode",
    "llm_coder", "llm_coach",
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
    _check_parallel_groups(graph, report)

    return report


def _check_structure(graph: ProcessGraph, report: ValidationReport) -> None:
    """Verifica schema: tipos, executors, campos obrigatórios."""
    parallel_policy = graph.meta.get("parallel_policy")
    if parallel_policy is not None:
        if not isinstance(parallel_policy, dict):
            report.add_error(None, "parallel_policy deve ser um mapping")
        else:
            planner_timeout = parallel_policy.get("planner_timeout_seconds")
            if "planner_timeout_seconds" in parallel_policy and (
                planner_timeout is not None
                and (
                    isinstance(planner_timeout, bool)
                    or not isinstance(planner_timeout, int)
                    or planner_timeout <= 0
                )
            ):
                report.add_error(
                    None,
                    "parallel_policy.planner_timeout_seconds deve ser inteiro "
                    "positivo ou null",
                )
            rate_limit_respawns = parallel_policy.get("rate_limit_respawns")
            if "rate_limit_respawns" in parallel_policy and (
                isinstance(rate_limit_respawns, bool)
                or not isinstance(rate_limit_respawns, int)
                or rate_limit_respawns < 0
            ):
                report.add_error(
                    None,
                    "parallel_policy.rate_limit_respawns deve ser inteiro não negativo",
                )

    commit_policy = graph.meta.get("commit_policy")
    if commit_policy is not None:
        if not isinstance(commit_policy, dict):
            report.add_error(None, "commit_policy deve ser um mapping")
        elif "verify_hooks" in commit_policy and not isinstance(
            commit_policy["verify_hooks"], bool
        ):
            report.add_error(
                None,
                "commit_policy.verify_hooks deve ser booleano",
            )

    close_policy = graph.meta.get("close_policy")
    if close_policy is not None:
        if not isinstance(close_policy, dict):
            report.add_error(None, "close_policy deve ser um mapping")
        else:
            backlog_policy = close_policy.get("backlog")
            if backlog_policy is not None:
                if not isinstance(backlog_policy, dict):
                    report.add_error(
                        None, "close_policy.backlog deve ser um mapping"
                    )
                else:
                    backlog_mode = backlog_policy.get("mode", "global")
                    if not isinstance(backlog_mode, str) or backlog_mode not in {
                        "global",
                        "referenced",
                        "none",
                    }:
                        report.add_error(
                            None,
                            "close_policy.backlog.mode deve ser global, "
                            "referenced ou none",
                        )
                    if backlog_mode == "referenced" and not (
                        isinstance(backlog_policy.get("references_path"), str)
                        and backlog_policy["references_path"].strip()
                    ):
                        report.add_error(
                            None,
                            "close_policy.backlog.references_path é obrigatório "
                            "no modo referenced",
                        )
    for node in graph.nodes.values():
        if not node.id:
            report.add_error(None, "nó sem id")
        if not node.title:
            report.add_error(node.id, "nó sem title")
        if node.type not in VALID_NODE_TYPES:
            report.add_error(node.id, f"type '{node.type}' inválido (válidos: {', '.join(sorted(VALID_NODE_TYPES))})")
        if node.type != "end" and node.executor and node.executor not in VALID_EXECUTORS:
            report.add_error(node.id, f"executor '{node.executor}' não reconhecido (válidos: {', '.join(sorted(VALID_EXECUTORS))})")
        if not isinstance(node.preserve_outputs_on_reentry, bool):
            report.add_error(
                node.id,
                "preserve_outputs_on_reentry deve ser booleano",
            )
        if node.llm_timeout_seconds is not None and (
            isinstance(node.llm_timeout_seconds, bool)
            or not isinstance(node.llm_timeout_seconds, int)
            or node.llm_timeout_seconds <= 0
        ):
            report.add_error(
                node.id,
                "llm_timeout_seconds deve ser um inteiro positivo",
            )
        for field_name in ("hyper_mode_docs", "hyper_mode_full_docs"):
            value = getattr(node, field_name)
            if value is None:
                continue
            if not isinstance(value, list) or any(
                not isinstance(item, str) or not item.strip() for item in value
            ):
                report.add_error(
                    node.id,
                    f"{field_name} deve ser uma lista de paths markdown não vazios",
                )
        for field_name in (
            "hyper_mode_preview_lines",
            "hyper_mode_full_max_lines",
        ):
            value = getattr(node, field_name)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                report.add_error(
                    node.id,
                    f"{field_name} deve ser um inteiro maior ou igual a zero",
                )
        if node.context_profile is not None:
            if not isinstance(node.context_profile, str) or not node.context_profile.strip():
                report.add_error(node.id, "context_profile deve ser uma string não vazia")
            elif node.context_profile not in KNOWN_CONTEXT_PROFILES:
                report.add_error(
                    node.id,
                    f"context_profile '{node.context_profile}' não reconhecido "
                    f"(válidos: {', '.join(sorted(KNOWN_CONTEXT_PROFILES))})",
                )
            mixed_fields = [
                field_name
                for field_name in HYPER_MODE_FIELDS
                if getattr(node, field_name) is not None
            ]
            if mixed_fields:
                report.add_error(
                    node.id,
                    "context_profile não pode ser combinado com HyperMode: "
                    + ", ".join(mixed_fields),
                )


def _check_graph_integrity(graph: ProcessGraph, report: ValidationReport) -> None:
    """Verifica conectividade, nós órfãos, e terminação."""
    ids = set(graph.nodes.keys())
    first_id = next(iter(graph.nodes)).strip() if graph.nodes else None

    def _edge_targets(node: Node) -> list[str]:
        targets: list[str] = []
        if node.next:
            targets.append(node.next)
        if node.branches:
            targets.extend(node.branches.values())
        if node.reject_next:
            targets.append(node.reject_next)
        on_fail_target = (node.on_fail or {}).get("goto")
        if on_fail_target:
            targets.append(on_fail_target)
        return targets

    # Nós apontados por algum outro nó
    pointed_to: set[str] = set()
    for node in graph.nodes.values():
        pointed_to.update(_edge_targets(node))

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
            for target in _edge_targets(node):
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


def _check_parallel_groups(graph: ProcessGraph, report: ValidationReport) -> None:
    """Valida parallel_group: só nodes LLM, outputs disjuntos entre membros.

    O fan-out roda cada membro num worktree e faz merge no fan-in — outputs
    compartilhados geram conflito de merge, e tipos de controle (gate,
    human_gate, decision, end) não podem ser delegados a um worktree.
    """
    groups: dict[str, list[Node]] = {}
    for node in graph.nodes.values():
        if node.parallel_group:
            groups.setdefault(str(node.parallel_group), []).append(node)

    control_types = {"gate", "human_gate", "decision", "end", "exploration"}
    for group_name, members in groups.items():
        if len(members) < 2:
            report.add_warning(
                members[0].id,
                f"parallel_group '{group_name}' com um único node — sem efeito",
            )
        for node in members:
            if node.type in control_types:
                report.add_error(
                    node.id,
                    f"parallel_group '{group_name}' não aceita node de controle "
                    f"(type={node.type})",
                )
            if not node.executor.startswith("llm"):
                report.add_error(
                    node.id,
                    f"parallel_group '{group_name}' exige executor LLM "
                    f"(executor={node.executor})",
                )
            if not node.outputs:
                report.add_error(
                    node.id,
                    f"parallel_group '{group_name}' exige outputs declarados "
                    "(usados na checagem de independência do fan-out)",
                )
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                shared = set(a.outputs) & set(b.outputs)
                if shared:
                    report.add_error(
                        a.id,
                        f"parallel_group '{group_name}': outputs compartilhados "
                        f"com {b.id}: {sorted(shared)}",
                    )


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
