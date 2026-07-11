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
    type: str  # discovery, document, build, test, gate, human_gate, review, decision, sync, end
    title: str
    executor: str = "python"
    outputs: list[str] = field(default_factory=list)
    write_scope: list[str] = field(default_factory=list)
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
    # Override do limite de turns do LLM para nodes complexos
    max_turns: int | None = None
    # Comandos shell executados antes da delegação ao LLM (setup determinístico)
    env_setup: list[str] = field(default_factory=list)
    env_teardown: list[str] = field(default_factory=list)
    # Override de engine/modelo por node (substitui o global do run)
    llm_engine: str | None = None
    llm_model: str | None = None
    # Desabilita o pre-seed check — node sempre roda mesmo se outputs já existem
    no_pre_seed: bool = False
    # Nó de destino quando human_gate é rejeitado (override do predecessor padrão)
    reject_next: str | None = None
    # Descrição amigável exibida ao usuário quando o step inicia
    description: str | None = None
    # Evento disparado quando o node falha (review ITERATE/REJECTED ou validators)
    # Estrutura: {human_gate: "mensagem", goto: "node_id"}
    on_fail: dict | None = None
    # Nó opcional — pode ser pulado com ft explore --skip
    optional: bool = False


class ProcessGraph:
    """DAG de um processo YAML."""

    def __init__(self, nodes: list[Node], meta: dict[str, Any]):
        node_ids = [node.id for node in nodes]
        duplicated = sorted(
            {node_id for node_id in node_ids if node_ids.count(node_id) > 1}
        )
        if duplicated:
            raise ValueError(
                "Processo contem IDs de node duplicados: " + ", ".join(duplicated)
            )
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
            if node.reject_next and node.reject_next not in ids:
                raise ValueError(
                    f"Node '{node.id}' reject_next aponta para '{node.reject_next}' que nao existe"
                )
            on_fail_target = (node.on_fail or {}).get("goto")
            if on_fail_target and on_fail_target not in ids:
                raise ValueError(
                    f"Node '{node.id}' on_fail.goto aponta para "
                    f"'{on_fail_target}' que nao existe"
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
            if "_default" in node.branches:
                return node.branches["_default"]
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
            if other_node.reject_next == node_id and other_id not in completed:
                return False
            if (other_node.on_fail or {}).get("goto") == node_id and other_id not in completed:
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

        # Normalizar executor: V3 usa nomes curtos (claude/codex/gemini/opencode),
        # runner usa o prefixo "llm" para detectar delegação ao LLM.
        _EXECUTOR_ALIASES = {
            "claude": "llm_claude",
            "codex": "llm_codex",
            "gemini": "llm_gemini",
            "opencode": "llm_opencode",
        }
        raw_executor = node_raw.get("executor", "python")
        executor = _EXECUTOR_ALIASES.get(raw_executor, raw_executor)

        nodes.append(Node(
            id=node_raw["id"],
            type=node_raw.get("type", "build"),
            title=node_raw.get("title", node_raw["id"]),
            executor=executor,
            outputs=node_raw.get("outputs", []),
            write_scope=node_raw.get("write_scope", []),
            requires_approval=node_raw.get("requires_approval", False),
            validators=validators,
            next=node_raw.get("next"),
            branches=node_raw.get("branches"),
            condition=node_raw.get("condition"),
            sprint=node_raw.get("sprint"),
            prompt=node_raw.get("prompt"),
            parallel_group=node_raw.get("parallel_group"),
            max_turns=node_raw.get("max_turns"),
            env_setup=node_raw.get("env_setup", []),
            env_teardown=node_raw.get("env_teardown", []),
            llm_engine=node_raw.get("llm_engine"),
            llm_model=node_raw.get("llm_model"),
            no_pre_seed=node_raw.get("no_pre_seed", False),
            description=node_raw.get("description"),
            reject_next=node_raw.get("reject_next"),
            on_fail=node_raw.get("on_fail"),
            optional=node_raw.get("optional", False),
        ))

    meta = {k: v for k, v in raw.items() if k != "nodes"}
    return ProcessGraph(nodes, meta)
