"""
Step Runner — loop principal do motor deterministico.
resolve_next() → delegate() → validate() → advance()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ft.engine.graph import Node, ProcessGraph, load_graph
from ft.engine.state import StateManager
from ft.engine.delegate import delegate_to_llm, delegate_with_feedback
from ft.engine.validators import artifacts as val


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

@dataclass
class ValidationItem:
    name: str
    passed: bool
    detail: str


@dataclass
class ValidationResult:
    passed: bool
    retryable: bool
    feedback: str | None
    items: list[ValidationItem] = field(default_factory=list)


# Mapeamento de validadores disponiveis
VALIDATOR_REGISTRY: dict[str, Any] = {
    "file_exists": val.file_exists,
    "min_lines": val.min_lines,
    "has_sections": val.has_sections,
    "min_user_stories": val.min_user_stories,
    "tests_pass": val.tests_pass,
    "tests_fail": val.tests_fail,
    "coverage_min": val.coverage_min,
}


def run_validators(node: Node, project_root: str) -> ValidationResult:
    """Roda todos os validadores de um node. Retorna resultado agregado."""
    items = []

    for validator_spec in node.validators:
        for name, args in validator_spec.items():
            fn = VALIDATOR_REGISTRY.get(name)
            if fn is None:
                items.append(ValidationItem(name=name, passed=False, detail=f"Validador desconhecido: {name}"))
                continue

            # Montar argumentos
            if isinstance(args, bool) and args is True:
                # Ex: tests_pass: true → tests_pass(project_root)
                passed, detail = fn(project_root=project_root)
            elif isinstance(args, (int, float)):
                # Ex: min_lines: 10 → min_lines(path, 10, project_root)
                # path vem do primeiro output do node
                path = node.outputs[0] if node.outputs else ""
                passed, detail = fn(path, args, project_root=project_root)
            elif isinstance(args, str):
                # Ex: file_exists: path → file_exists(path, project_root)
                passed, detail = fn(args, project_root=project_root)
            elif isinstance(args, list):
                # Ex: has_sections: [A, B, C] → has_sections(path, [A,B,C], project_root)
                path = node.outputs[0] if node.outputs else ""
                passed, detail = fn(path, args, project_root=project_root)
            else:
                passed, detail = False, f"Args nao suportados para {name}: {args}"

            items.append(ValidationItem(name=name, passed=passed, detail=detail))

    all_passed = all(item.passed for item in items)
    retryable = not all_passed and node.executor.startswith("llm")
    feedback = None
    if not all_passed:
        failures = [item.detail for item in items if not item.passed]
        feedback = "\n".join(failures)

    return ValidationResult(
        passed=all_passed,
        retryable=retryable,
        feedback=feedback,
        items=items,
    )


# ---------------------------------------------------------------------------
# Task prompt builders
# ---------------------------------------------------------------------------

def build_task_prompt(node: Node, state_dict: dict[str, Any]) -> str:
    """Constroi o prompt de construcao para o LLM baseado no node."""
    outputs_str = ", ".join(node.outputs) if node.outputs else "conforme necessario"

    if node.type == "discovery":
        return f"""Conduza a etapa de discovery: {node.title}

Produza o artefato: {outputs_str}

O artefato deve ser um documento markdown completo e acionavel.
Interaja com o stakeholder se necessario (faca perguntas diretas).
"""
    elif node.type == "document":
        return f"""Produza o documento: {node.title}

Arquivo de saida: {outputs_str}

O documento deve ser completo, estruturado em markdown, e pronto para revisao.
"""
    elif node.type == "build":
        return f"""Implemente: {node.title}

Arquivos de saida esperados: {outputs_str}

Siga TDD: escreva testes primeiro, depois implemente.
Garanta que os testes passam ao final.
"""
    else:
        return f"""Execute: {node.title}\nSaida esperada: {outputs_str}"""


# ---------------------------------------------------------------------------
# Step Runner
# ---------------------------------------------------------------------------

MAX_RETRIES = 3


class StepRunner:
    """Motor deterministico. Roda o loop principal."""

    def __init__(self, process_path: str | Path, state_path: str | Path, project_root: str | Path = "."):
        self.graph = load_graph(process_path)
        self.state_mgr = StateManager(state_path)
        self.project_root = str(Path(project_root).resolve())

    def init_state(self):
        """Inicializa estado a partir do grafo."""
        first = self.graph.first_node()
        total = len([n for n in self.graph.nodes.values() if n.type != "end"])
        self.state_mgr.init_from_graph(self.graph.meta, first.id, total)
        print(f"Estado inicializado. Processo: {self.graph.meta.get('title', '?')}")
        print(f"  Primeiro node: {first.id} ({first.title})")
        print(f"  Total de steps: {total}")

    def run(self, mode: str = "step"):
        """
        Loop principal.

        mode:
          "step"   — avanca exatamente 1 step
          "sprint" — avanca ate o fim da sprint atual (TODO fase 2)
          "mvp"    — avanca ate o fim ou BLOCK
        """
        state = self.state_mgr.load()

        if state.current_node is None:
            print("Processo nao inicializado. Rode: ft init")
            return

        while True:
            node_id = state.current_node
            if node_id is None:
                print("\n=== PROCESSO COMPLETO ===")
                break

            node = self.graph.get_node(node_id)

            if node.type == "end":
                print(f"\n{'='*50}")
                print(f"  PROCESSO COMPLETO")
                print(f"  Steps: {state.metrics['steps_completed']}/{state.metrics['steps_total']}")
                print(f"{'='*50}")
                self.state_mgr.advance(node_id, None)
                break

            print(f"\n{'─'*50}")
            print(f"  [{node_id}] {node.title}")
            print(f"  Tipo: {node.type} | Executor: {node.executor}")
            print(f"{'─'*50}")

            # Gate — validacao pura, sem LLM
            if node.type == "gate":
                self._run_gate(node)
                if mode == "step":
                    break
                state = self.state_mgr.load()
                continue

            # Discovery/Document/Build — delegar ao LLM
            if node.executor.startswith("llm"):
                self._run_llm_step(node)
            else:
                # Python executor — so validar
                self._run_gate(node)

            # Checar se ficou bloqueado ou aguardando aprovacao
            state = self.state_mgr.load()
            if state.node_status in ("blocked", "awaiting_approval"):
                break

            if mode == "step":
                break

    def _run_llm_step(self, node: Node):
        """Delega ao LLM, valida resultado, avanca ou retenta."""
        state = self.state_mgr.state
        task_prompt = build_task_prompt(node, {})

        # Determinar paths permitidos
        allowed = []
        for output in node.outputs:
            parent = str(Path(output).parent)
            if parent not in allowed:
                allowed.append(parent)
        if not allowed:
            allowed = ["src/", "tests/", "project/docs/"]

        print(f"  Delegando ao LLM ({node.executor})...")
        state.node_status = "delegated"
        state.metrics["llm_calls"] = state.metrics.get("llm_calls", 0) + 1
        self.state_mgr.save()

        result = delegate_to_llm(
            task=task_prompt,
            project_root=self.project_root,
            allowed_paths=allowed,
        )

        if not result.success:
            print(f"  LLM reportou BLOCKED: {result.output[:200]}")
            self.state_mgr.block(f"LLM falhou: {result.output[:500]}")
            return

        # Registrar artefatos
        for output_path in node.outputs:
            name = Path(output_path).stem
            self.state_mgr.record_artifact(name, output_path)

        # Validar
        print(f"  Validando...")
        validation = run_validators(node, self.project_root)
        self._print_validation(validation)

        if validation.passed:
            if node.requires_approval:
                print(f"  AGUARDANDO APROVACAO — rode: ft approve")
                self.state_mgr.set_pending_approval(node.id)
                return

            next_id = self.graph.resolve_next(node.id)
            self.state_mgr.advance(node.id, next_id)
            print(f"  PASS → proximo: {next_id}")
            return

        # Retry
        if validation.retryable:
            for retry in range(1, MAX_RETRIES + 1):
                print(f"  RETRY {retry}/{MAX_RETRIES}...")
                result = delegate_with_feedback(
                    original_task=task_prompt,
                    feedback=validation.feedback or "",
                    project_root=self.project_root,
                    allowed_paths=allowed,
                )
                state.metrics["llm_calls"] = state.metrics.get("llm_calls", 0) + 1

                validation = run_validators(node, self.project_root)
                self._print_validation(validation)

                if validation.passed:
                    if node.requires_approval:
                        print(f"  AGUARDANDO APROVACAO — rode: ft approve")
                        self.state_mgr.set_pending_approval(node.id)
                        return
                    next_id = self.graph.resolve_next(node.id)
                    self.state_mgr.advance(node.id, next_id)
                    print(f"  PASS (retry {retry}) → proximo: {next_id}")
                    return

        # Esgotou retries
        self.state_mgr.block(f"Validacao falhou apos {MAX_RETRIES} tentativas: {validation.feedback}")
        print(f"  BLOCK: validacao falhou apos {MAX_RETRIES} tentativas")

    def _run_gate(self, node: Node):
        """Roda gate — validacao pura sem LLM."""
        print(f"  Rodando gate...")
        validation = run_validators(node, self.project_root)
        self._print_validation(validation)

        if validation.passed:
            next_id = self.graph.resolve_next(node.id)
            self.state_mgr.advance(node.id, next_id, "PASS")
            print(f"  GATE PASS → proximo: {next_id}")
        else:
            self.state_mgr.block(f"Gate falhou: {validation.feedback}")
            self.state_mgr.state.gate_log[node.id] = "BLOCK"
            self.state_mgr.save()
            print(f"  GATE BLOCK: {validation.feedback}")

    def approve(self):
        """Stakeholder aprova artefato pendente."""
        state = self.state_mgr.load()
        if not state.pending_approval:
            print("Nenhuma aprovacao pendente.")
            return

        node_id = state.pending_approval
        node = self.graph.get_node(node_id)
        next_id = self.graph.resolve_next(node_id)
        self.state_mgr.advance(node_id, next_id)
        print(f"  APROVADO: {node_id} → proximo: {next_id}")

    def reject(self, reason: str):
        """Stakeholder rejeita artefato pendente."""
        state = self.state_mgr.load()
        if not state.pending_approval:
            print("Nenhuma rejeicao pendente.")
            return

        node_id = state.pending_approval
        self.state_mgr.block(f"Rejeitado pelo stakeholder: {reason}")
        print(f"  REJEITADO: {node_id} — {reason}")

    def status(self, full: bool = False):
        """Mostra estado atual."""
        state = self.state_mgr.load()
        completed = set(state.completed_nodes)
        node_status = self.graph.get_status(completed)

        print(f"\n{'━'*50}")
        print(f"  Processo: {state.process_id} v{state.version}")
        print(f"  Node atual: {state.current_node}")
        print(f"  Status: {state.node_status}")
        print(f"  Progresso: {state.metrics['steps_completed']}/{state.metrics['steps_total']}")
        if state.blocked_reason:
            print(f"  BLOCKED: {state.blocked_reason}")
        if state.pending_approval:
            print(f"  AGUARDANDO APROVACAO: {state.pending_approval}")
        print(f"{'━'*50}")

        if full:
            print(f"\n  Grafo:")
            for nid, status in node_status.items():
                node = self.graph.get_node(nid)
                icon = {"done": "✓", "ready": "→", "blocked": "○"}[status]
                gate_result = state.gate_log.get(nid, "")
                gate_str = f" [{gate_result}]" if gate_result else ""
                print(f"    {icon} {nid}: {node.title}{gate_str}")

            if state.artifacts:
                print(f"\n  Artefatos:")
                for name, path in state.artifacts.items():
                    exists = "✓" if path and Path(self.project_root, path).exists() else "✗"
                    print(f"    {exists} {name}: {path}")

    @staticmethod
    def _print_validation(v: ValidationResult):
        for item in v.items:
            icon = "[ok]" if item.passed else "[FAIL]"
            print(f"    {icon} {item.detail}")
