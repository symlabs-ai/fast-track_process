"""
Grafo de processo — parse YAML → DAG, topological sort, resolve_next().
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Node:
    id: str
    type: str  # discovery, document, build, test, gate, review, decision, sync, end
    title: str
    executor: str = "python"
    outputs: list[str] = field(default_factory=list)
    requires_approval: bool = False
    validators: list[dict[str, Any]] = field(default_factory=list)
    next: str | None = None
    # Para decisions
    branches: dict[str, str] | None = None
    condition: str | None = None
    # Sprint grouping
    sprint: str | None = None
    # Custom prompt for LLM
    prompt: str | None = None
    # Parallel group — nodes com mesmo grupo rodam em paralelo
    parallel_group: str | None = None


class ProcessGraph:
    """DAG de um processo YAML."""

    def __init__(self, nodes: list[Node], meta: dict[str, Any]):
        self.nodes: dict[str, Node] = {n.id: n for n in nodes}
        self.meta = meta
        self._validate()

    def _validate(self):
        """Valida integridade do grafo."""
        ids = set(self.nodes.keys())

        for node in self.nodes.values():
            if node.type == "end":
                continue
            if node.next and node.next not in ids:
                raise ValueError(f"Node '{node.id}' aponta para '{node.next}' que nao existe")
            if node.branches:
                for target in node.branches.values():
                    if target not in ids:
                        raise ValueError(
                            f"Node '{node.id}' branch aponta para '{target}' que nao existe"
                        )

        # Verificar que existe exatamente 1 end node
        end_nodes = [n for n in self.nodes.values() if n.type == "end"]
        if len(end_nodes) != 1:
            raise ValueError(f"Processo deve ter exatamente 1 node type=end, encontrados: {len(end_nodes)}")

    def get_node(self, node_id: str) -> Node:
        if node_id not in self.nodes:
            raise KeyError(f"Node '{node_id}' nao encontrado no grafo")
        return self.nodes[node_id]

    def first_node(self) -> Node:
        """Retorna o primeiro node (primeiro no YAML)."""
        return next(iter(self.nodes.values()))

    def all_node_ids(self) -> list[str]:
        return list(self.nodes.keys())

    def resolve_next(self, current_id: str, state: dict[str, Any] | None = None) -> str | None:
        """Determina o proximo node. Puramente deterministico."""
        node = self.get_node(current_id)

        if node.type == "end":
            return None

        if node.type == "decision" and node.branches and node.condition and state:
            # Avaliar condicao simples (chave no state)
            value = state.get(node.condition)
            if value in node.branches:
                return node.branches[value]
            # Fallback para next
            return node.next

        return node.next

    def get_status(self, completed: set[str]) -> dict[str, str]:
        """Calcula BLOCKED/READY/DONE para cada node."""
        status = {}
        for node_id, node in self.nodes.items():
            if node_id in completed:
                status[node_id] = "done"
            elif self._is_ready(node_id, completed):
                status[node_id] = "ready"
            else:
                status[node_id] = "blocked"
        return status

    def get_sprint_nodes(self, sprint: str) -> list[Node]:
        """Retorna todos os nodes de uma sprint, na ordem do YAML."""
        return [n for n in self.nodes.values() if n.sprint == sprint]

    def get_parallel_group(self, group: str) -> list[Node]:
        """Retorna todos os nodes de um grupo paralelo."""
        return [n for n in self.nodes.values() if n.parallel_group == group]

    def get_sprints(self) -> list[str]:
        """Retorna lista de sprints unicas na ordem que aparecem."""
        seen = []
        for n in self.nodes.values():
            if n.sprint and n.sprint not in seen:
                seen.append(n.sprint)
        return seen

    def sprint_of(self, node_id: str) -> str | None:
        """Retorna a sprint de um node."""
        return self.nodes[node_id].sprint if node_id in self.nodes else None

    def _is_ready(self, node_id: str, completed: set[str]) -> bool:
        """Um node esta ready se todos os nodes que apontam para ele estao done."""
        # Para sequencia linear: o node esta ready se o anterior esta done (ou e o primeiro)
        for other_id, other_node in self.nodes.items():
            if other_node.next == node_id and other_id not in completed:
                return False
            if other_node.branches:
                for target in other_node.branches.values():
                    if target == node_id and other_id not in completed:
                        return False
        # Se ninguem aponta para ele, e o primeiro — ready
        return True


def load_graph(path: str | Path) -> ProcessGraph:
    """Carrega um YAML de processo e retorna o grafo."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Processo nao encontrado: {path}")

    with open(path) as f:
        raw = yaml.safe_load(f)

    nodes = []
    for node_raw in raw.get("nodes", []):
        # Parse validators — podem ser dict simples ou nested
        validators = []
        for v in node_raw.get("validators", []):
            if isinstance(v, dict):
                validators.append(v)
            elif isinstance(v, str):
                validators.append({v: True})

        nodes.append(Node(
            id=node_raw["id"],
            type=node_raw.get("type", "build"),
            title=node_raw.get("title", node_raw["id"]),
            executor=node_raw.get("executor", "python"),
            outputs=node_raw.get("outputs", []),
            requires_approval=node_raw.get("requires_approval", False),
            validators=validators,
            next=node_raw.get("next"),
            branches=node_raw.get("branches"),
            condition=node_raw.get("condition"),
            sprint=node_raw.get("sprint"),
            prompt=node_raw.get("prompt"),
            parallel_group=node_raw.get("parallel_group"),
        ))

    meta = {k: v for k, v in raw.items() if k != "nodes"}
    return ProcessGraph(nodes, meta)
