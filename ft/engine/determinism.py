"""
Análise estática de determinismo de um processo Fast Track.

Cada node recebe um score d_i de 0 a 1 conforme executor e força dos
validators; o score do template é a média dos d_i. Nodes humanos
(human_gate, requires_approval) ficam fora da conta — variabilidade
intencional — e são reportados como eixo separado.

Camadas do modelo (ver docs/ft_process_authoring.md):
1. Execução — node roda código (python) ou LLM?
2. Resultado — output LLM está preso por validators binários?
3. Empírico — variância entre runs (futuro: ft analyse-cycle).

Este módulo cobre as camadas 1 e 2, computáveis só do YAML.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ft.engine.graph import Node, ProcessGraph

# Tiers de força dos validators. Todos os validators do registry são
# Python puro (determinísticos); o tier mede o quanto cada um restringe
# o RESULTADO de um node LLM, não se o check em si é determinístico.
#
# WEAK: só existência — não restringe conteúdo.
WEAK_VALIDATORS = frozenset({"file_exists", "read_artifact"})

# MEDIUM: forma/estrutura — seções, contagem de linhas, higiene.
MEDIUM_VALIDATORS = frozenset(
    {
        "document_quality",
        "has_sections",
        "min_lines",
        "min_user_stories",
        "no_large_files",
        "no_print_statements",
        "no_todo_fixme",
        "paths_clean",
        "sections_unchanged",
    }
)

# Demais validators do registry (tests_pass, command_succeeds, contratos,
# gate_*) são STRONG: binários/executáveis, forçam convergência semântica.

# d_i por categoria. Executores python e gates são 1.0 por construção.
CATEGORY_SCORES: dict[str, float] = {
    "python": 1.0,
    "llm_strong": 0.7,
    "llm_medium": 0.4,
    "llm_weak": 0.1,
    "llm_none": 0.05,
}

# Tipos estruturais sem execução relevante para o score.
_SKIPPED_TYPES = frozenset({"end", "exploration", "sync"})

_LLM_CATEGORY_BY_TIER = {
    "strong": "llm_strong",
    "medium": "llm_medium",
    "weak": "llm_weak",
    "none": "llm_none",
}


@dataclass
class NodeScore:
    """Classificação de um node para o score de determinismo."""

    id: str
    type: str
    executor: str
    category: str  # python | llm_strong | llm_medium | llm_weak | llm_none
    score: float
    validators: list[str] = field(default_factory=list)
    # Node fraco com gate determinístico alcançável downstream: o gate
    # re-valida o resultado, então a fraqueza local não propaga adiante.
    backed_by_gate: bool = False


@dataclass
class DeterminismReport:
    """Resultado da análise de um processo."""

    process_id: str
    version: str
    scored: list[NodeScore] = field(default_factory=list)
    human_nodes: list[str] = field(default_factory=list)
    skipped_nodes: list[str] = field(default_factory=list)
    unknown_validators: list[str] = field(default_factory=list)

    @property
    def score(self) -> float:
        """Média dos d_i dos nodes pontuados (0.0 a 1.0)."""
        if not self.scored:
            return 0.0
        return sum(n.score for n in self.scored) / len(self.scored)

    @property
    def weak_nodes(self) -> list[NodeScore]:
        return [n for n in self.scored if n.category in ("llm_weak", "llm_none")]

    def category_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for n in self.scored:
            counts[n.category] = counts.get(n.category, 0) + 1
        return counts


def _validator_names(node: Node) -> list[str]:
    names: list[str] = []
    for entry in node.validators:
        if isinstance(entry, dict):
            names.extend(entry.keys())
        else:
            names.append(str(entry))
    return names


def _strongest_tier(names: list[str], known: frozenset[str] | None) -> str:
    """Tier do validator mais forte presente no node."""
    if not names:
        return "none"
    tiers = set()
    for name in names:
        if name in WEAK_VALIDATORS:
            tiers.add("weak")
        elif name in MEDIUM_VALIDATORS:
            tiers.add("medium")
        elif known is not None and name not in known:
            # Validator desconhecido: conservador, conta como MEDIUM.
            tiers.add("medium")
        else:
            tiers.add("strong")
    for tier in ("strong", "medium", "weak"):
        if tier in tiers:
            return tier
    return "none"


def _is_deterministic_backstop(node: Node) -> bool:
    """Gate python com validators que re-valida o resultado anterior."""
    if node.executor != "python":
        return False
    return node.type == "gate" and bool(node.validators)


def _validator_arg_strings(node: Node) -> list[str]:
    """Todas as strings de argumento dos validators de um node."""
    args: list[str] = []

    def _collect(value: object) -> None:
        if isinstance(value, str):
            args.append(value)
        elif isinstance(value, dict):
            for item in value.values():
                _collect(item)
        elif isinstance(value, list):
            for item in value:
                _collect(item)

    for entry in node.validators:
        _collect(entry)
    return args


def _reachable_gates(start: Node, nodes_by_id: dict[str, Node]) -> list[Node]:
    """BFS pelos sucessores (next + branches) coletando gates determinísticos."""
    queue = [start.id]
    visited: set[str] = set()
    gates: list[Node] = []
    while queue:
        current = nodes_by_id.get(queue.pop(0))
        if current is None:
            continue
        targets = [current.next] if current.next else []
        if current.branches:
            targets.extend(current.branches.values())
        for target in targets:
            if not target or target in visited:
                continue
            visited.add(target)
            successor = nodes_by_id.get(target)
            if successor is None:
                continue
            if _is_deterministic_backstop(successor):
                gates.append(successor)
            queue.append(target)
    return gates


def _backed_by_downstream_gate(node: Node, nodes_by_id: dict[str, Node]) -> bool:
    """True se algum gate alcançável re-valida os outputs deste node.

    Alcançar um gate não basta: o gate precisa referenciar os artefatos
    que o node produz (path do output em algum argumento de validator).
    Sem outputs declarados não há como amarrar — retorna False.
    """
    outputs = [o.rstrip("/") for o in node.outputs if o.strip("/")]
    if not outputs:
        return False
    for gate in _reachable_gates(node, nodes_by_id):
        for arg in _validator_arg_strings(gate):
            if any(output in arg for output in outputs):
                return True
    return False


def classify_node(
    node: Node,
    known_validators: frozenset[str] | None = None,
) -> NodeScore | None:
    """Classifica um node; None para nodes fora da conta (humanos/estruturais)."""
    if node.type in _SKIPPED_TYPES:
        return None
    if node.type == "human_gate":
        return None
    names = _validator_names(node)
    if node.executor == "python":
        category = "python"
    else:
        tier = _strongest_tier(names, known_validators)
        category = _LLM_CATEGORY_BY_TIER[tier]
    return NodeScore(
        id=node.id,
        type=node.type,
        executor=node.executor,
        category=category,
        score=CATEGORY_SCORES[category],
        validators=names,
    )


def analyse_graph(
    graph: ProcessGraph,
    known_validators: frozenset[str] | None = None,
) -> DeterminismReport:
    """Computa o score de determinismo estático de um grafo de processo."""
    report = DeterminismReport(
        process_id=str(graph.meta.get("id", "?")),
        version=str(graph.meta.get("version", "?")),
    )
    nodes_by_id = graph.nodes
    seen_unknown: set[str] = set()
    for node in graph.nodes.values():
        names = _validator_names(node)
        if known_validators is not None:
            for name in names:
                if name not in known_validators and name not in seen_unknown:
                    seen_unknown.add(name)
                    report.unknown_validators.append(name)
        scored = classify_node(node, known_validators)
        if scored is None:
            if node.type == "human_gate":
                report.human_nodes.append(node.id)
            else:
                report.skipped_nodes.append(node.id)
            continue
        # requires_approval torna o node um ponto de decisão humana além
        # do seu executor — conta nos dois eixos.
        if node.requires_approval:
            report.human_nodes.append(node.id)
        if scored.category in ("llm_weak", "llm_none"):
            scored.backed_by_gate = _backed_by_downstream_gate(node, nodes_by_id)
        report.scored.append(scored)
    return report
