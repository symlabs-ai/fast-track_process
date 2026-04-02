"""
State Manager — leitura/escrita de ft_state.yml com lock.
Unico escritor do estado. LLM nunca toca.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class EngineState:
    """Estado do motor. Serializado em ft_state.yml."""
    process_id: str = ""
    version: str = "0.1.0"
    current_node: str | None = None
    node_status: str = "ready"  # ready | delegated | validating | done | blocked
    completed_nodes: list[str] = field(default_factory=list)
    current_cycle: str = "cycle-01"
    current_sprint: str | None = None
    sprint_status: str | None = None
    gate_log: dict[str, str] = field(default_factory=dict)
    artifacts: dict[str, str | None] = field(default_factory=dict)
    blocked_reason: str | None = None
    pending_approval: str | None = None  # node_id aguardando approve/reject
    metrics: dict[str, Any] = field(default_factory=lambda: {
        "steps_completed": 0,
        "steps_total": 0,
        "tests_passing": 0,
        "coverage": 0,
        "llm_calls": 0,
        "tokens_used": 0,
    })
    _lock: dict[str, Any] | None = None


class StateLockError(RuntimeError):
    """Levantado quando outro processo ft engine esta rodando."""


class StateManager:
    """Gerencia o estado do motor. Unico escritor."""

    def __init__(self, state_path: str | Path):
        self.path = Path(state_path)
        self._state: EngineState | None = None

    def _is_pid_alive(self, pid: int) -> bool:
        """Verifica se um PID ainda esta em execucao."""
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False

    def _check_lock(self, raw: dict[str, Any]) -> None:
        """Lanca StateLockError se outro processo estiver com o lock."""
        lock = raw.get("_lock")
        if not lock or not isinstance(lock, dict):
            return
        pid = lock.get("pid")
        if pid and pid != os.getpid() and self._is_pid_alive(int(pid)):
            raise StateLockError(
                f"ft engine ja esta rodando (PID {pid}). "
                "Aguarde o termino ou delete project/state/engine_state.yml para resetar."
            )

    def load(self, check_lock: bool = False) -> EngineState:
        """Carrega estado do YAML."""
        if self.path.exists():
            with open(self.path) as f:
                raw = yaml.safe_load(f) or {}
            if check_lock:
                self._check_lock(raw)
            self._state = EngineState(
                process_id=raw.get("process_id", ""),
                version=raw.get("version", "0.1.0"),
                current_node=raw.get("current_node"),
                node_status=raw.get("node_status", "ready"),
                completed_nodes=raw.get("completed_nodes", []),
                current_cycle=raw.get("current_cycle", "cycle-01"),
                current_sprint=raw.get("current_sprint"),
                sprint_status=raw.get("sprint_status"),
                gate_log=raw.get("gate_log", {}),
                artifacts=raw.get("artifacts", {}),
                blocked_reason=raw.get("blocked_reason"),
                pending_approval=raw.get("pending_approval"),
                metrics=raw.get("metrics", EngineState().metrics),
                _lock=raw.get("_lock"),
            )
        else:
            self._state = EngineState()
        return self._state

    def save(self) -> None:
        """Salva estado no YAML com lock."""
        if self._state is None:
            raise RuntimeError("State nao carregado. Chame load() primeiro.")

        # Setar lock
        self._state._lock = {
            "owner": "ft_engine",
            "pid": os.getpid(),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

        data = {
            "process_id": self._state.process_id,
            "version": self._state.version,
            "current_node": self._state.current_node,
            "node_status": self._state.node_status,
            "completed_nodes": self._state.completed_nodes,
            "current_cycle": self._state.current_cycle,
            "current_sprint": self._state.current_sprint,
            "sprint_status": self._state.sprint_status,
            "gate_log": self._state.gate_log,
            "artifacts": self._state.artifacts,
            "blocked_reason": self._state.blocked_reason,
            "pending_approval": self._state.pending_approval,
            "metrics": self._state.metrics,
            "_lock": self._state._lock,
        }

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    @property
    def state(self) -> EngineState:
        if self._state is None:
            return self.load()
        return self._state

    def init_from_graph(self, graph_meta: dict[str, Any], first_node_id: str, total_steps: int):
        """Inicializa estado a partir de um grafo de processo."""
        self._state = EngineState(
            process_id=graph_meta.get("id", "unknown"),
            version=graph_meta.get("version", "0.1.0"),
            current_node=first_node_id,
            node_status="ready",
            metrics={
                "steps_completed": 0,
                "steps_total": total_steps,
                "tests_passing": 0,
                "coverage": 0,
                "llm_calls": 0,
                "tokens_used": 0,
            },
        )
        self.save()

    def advance(self, completed_node: str, next_node: str | None, gate_result: str = "PASS"):
        """Avanca estado apos validacao PASS."""
        s = self.state
        if s.node_status == "blocked":
            # Auto-unblock: se a validacao passou, o bloqueio anterior foi resolvido
            s.node_status = "ready"
            s.blocked_reason = None
        s.completed_nodes.append(completed_node)
        s.gate_log[completed_node] = gate_result
        s.current_node = next_node
        s.node_status = "ready" if next_node else "done"
        s.blocked_reason = None
        s.pending_approval = None
        s.metrics["steps_completed"] = len(s.completed_nodes)
        self.save()

    def advance_guarded(self, completed_node: str, next_node: str | None, gate_result: str = "PASS"):
        """Avanca estado com verificacao de gate_result e bloqueio."""
        if gate_result != "PASS":
            raise ValueError(f"gate_result deve ser 'PASS' para avançar, recebido: '{gate_result}'")
        s = self.state
        if s.node_status == "blocked":
            raise RuntimeError(f"Estado bloqueado: {s.blocked_reason}. Use unblock() antes de advance_guarded().")
        s.completed_nodes.append(completed_node)
        s.gate_log[completed_node] = gate_result
        s.current_node = next_node
        s.node_status = "ready" if next_node else "done"
        s.blocked_reason = None
        s.pending_approval = None
        s.metrics["steps_completed"] = len(s.completed_nodes)
        self.save()

    def unblock(self):
        """Remove bloqueio do motor."""
        s = self.state
        s.node_status = "ready"
        s.blocked_reason = None
        self.save()

    def block(self, reason: str):
        """Bloqueia o motor no node atual."""
        if not reason:
            raise ValueError("blocked reason / motivo não pode ser vazio")
        s = self.state
        s.node_status = "blocked"
        s.blocked_reason = reason
        self.save()

    def set_pending_approval(self, node_id: str):
        """Marca node como aguardando aprovacao humana."""
        s = self.state
        s.node_status = "awaiting_approval"
        s.pending_approval = node_id
        self.save()

    def record_artifact(self, name: str, path: str):
        """Registra artefato produzido."""
        if not name:
            raise ValueError("artifact name não pode ser vazio")
        if not path:
            raise ValueError("artifact path não pode ser vazio")
        self.state.artifacts[name] = path
        self.save()

    def list_artifacts(self) -> dict[str, str | None]:
        """Retorna dict com todos os artifacts."""
        return dict(self.state.artifacts)

    def block_gate(self, node_id: str, reason: str):
        """Registra BLOCK no gate_log para o node."""
        s = self.state
        s.gate_log[node_id] = "BLOCK"
        s.node_status = "blocked"
        s.blocked_reason = reason
        self.save()
