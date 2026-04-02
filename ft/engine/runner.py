"""
Step Runner — loop principal do motor deterministico.
resolve_next() → delegate() → validate() → advance()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ft.engine.graph import Node, ProcessGraph, load_graph
from ft.engine.state import StateManager
from ft.engine.delegate import delegate_to_llm, delegate_with_feedback
from ft.engine.validators import artifacts as val
from ft.engine.validators import gates
from ft.engine.validators import tests as test_val
from ft.engine.validators import code as code_val
from ft.engine.validators import review as review_val
from ft.engine.git_ops import auto_commit
from ft.engine.parallel import ParallelRunner, check_independence
from ft.engine.stakeholder import (
    scan_existing_docs, should_skip_node,
    hyper_mode_prompt, build_rejection_prompt,
    format_pending_summary,
)


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
    artifacts: dict[str, str] = field(default_factory=dict)


# Mapeamento de validadores disponiveis
VALIDATOR_REGISTRY: dict[str, Any] = {
    "file_exists": val.file_exists,
    "min_lines": val.min_lines,
    "has_sections": val.has_sections,
    "min_user_stories": val.min_user_stories,
    "tests_pass": val.tests_pass,
    "tests_fail": val.tests_fail,
    "coverage_min": val.coverage_min,
    "gate_delivery": gates.gate_delivery,
    "gate_smoke": gates.gate_smoke,
    "gate_mvp": gates.gate_mvp,
    "gate_frontend": gates.gate_frontend,
    "gate_server_starts": gates.gate_server_starts,
    "read_artifact": val.read_artifact,
    # Test validators (Phase 3)
    "coverage_per_file": test_val.coverage_per_file,
    "tests_exist": test_val.tests_exist,
    # Code validators
    "lint_clean": code_val.lint_clean,
    "format_check": code_val.format_check,
    "no_todo_fixme": code_val.no_todo_fixme,
    # Review validators
    "no_large_files": review_val.no_large_files,
    "no_print_statements": review_val.no_print_statements,
    "changed_files_have_tests": review_val.changed_files_have_tests,
}


def run_validators(node: Node, project_root: str) -> ValidationResult:
    """Roda todos os validadores de um node. Retorna resultado agregado."""
    items = []
    extra_artifacts: dict[str, str] = {}

    for validator_spec in node.validators:
        for name, args in validator_spec.items():
            fn = VALIDATOR_REGISTRY.get(name)
            if fn is None:
                items.append(ValidationItem(name=name, passed=False, detail=f"Validador desconhecido: {name}"))
                continue

            # read_artifact — caso especial: args como dict com path/key/pattern
            if name == "read_artifact" and isinstance(args, dict):
                passed, detail = fn(**args, project_root=project_root)
                if name == "read_artifact" and passed:
                    try:
                        kv = detail.split(": ", 1)[-1]
                        if "=" in kv:
                            k, v = kv.split("=", 1)
                            extra_artifacts[k.strip()] = v.strip()
                    except Exception:
                        pass
            # Gate validators compostos — recebem args como dict
            elif name.startswith("gate_") and isinstance(args, dict):
                passed, detail = fn(**args, project_root=project_root)
            elif name.startswith("gate_") and isinstance(args, bool) and args is True:
                # gate_delivery: true → usa outputs do node
                if name == "gate_delivery":
                    passed, detail = fn(outputs=node.outputs, project_root=project_root)
                else:
                    passed, detail = fn(project_root=project_root)
            # Validadores simples
            elif isinstance(args, bool) and args is True:
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
        artifacts=extra_artifacts,
    )


# ---------------------------------------------------------------------------
# Task prompt builders
# ---------------------------------------------------------------------------

def build_task_prompt(node: Node, state_dict: dict[str, Any]) -> str:
    """Constroi o prompt de construcao para o LLM baseado no node."""
    outputs_str = ", ".join(node.outputs) if node.outputs else "conforme necessario"

    # Custom prompt override
    if node.prompt:
        return f"""{node.prompt}

Arquivos de saida esperados: {outputs_str}
"""

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
    elif node.type == "test_red":
        return f"""TDD RED PHASE: {node.title}

Escreva APENAS os testes. NAO implemente o codigo de producao ainda.
Os testes DEVEM FALHAR (red phase do TDD).

Arquivos de teste esperados: {outputs_str}

Escreva testes que:
- Cobrem os cenarios principais (happy path)
- Cobrem edge cases
- Usam pytest
- Importam os modulos que serao implementados (mesmo que ainda nao existam)
"""
    elif node.type == "test_green":
        return f"""TDD GREEN PHASE: {node.title}

Implemente o codigo MINIMO necessario para fazer os testes passarem.
NAO refatore, NAO adicione funcionalidades extras.

Arquivos de producao esperados: {outputs_str}

O codigo deve:
- Fazer todos os testes passarem
- Ser o minimo necessario (sem over-engineering)
- Seguir as interfaces definidas nos testes
"""
    elif node.type == "refactor":
        return f"""TDD REFACTOR PHASE: {node.title}

Refatore o codigo mantendo todos os testes passando.
Melhore a qualidade sem mudar o comportamento.

Arquivos: {outputs_str}

Checklist:
- Extrair duplicacoes
- Nomear variaveis/funcoes melhor
- Simplificar logica complexa
- Manter testes verdes
"""
    elif node.type == "review":
        return f"""EXPERT REVIEW: {node.title}

Revise os artefatos produzidos e emita um parecer de qualidade.

Artefatos para revisao: {outputs_str}

Checklist de revisao:
- Cobertura funcional: os artefatos cobrem todos os requisitos do PRD?
- Qualidade tecnica: codigo limpo, testado, sem debt obvio?
- Arquitetura: Clean/Hex respeitada, sem acoplamentos incorretos?
- Seguranca: sem secrets, sem dados sensiveis, inputs validados?
- Observabilidade: logs estruturados, sem prints de debug?

Responda com:
- APPROVED se tudo estiver adequado
- APPROVED WITH NOTES se aprovado mas com observacoes menores (liste-as)
- REJECTED se houver problemas que precisam ser corrigidos (liste-os)

Produza o relatorio em: {outputs_str}
"""
    elif node.type == "retro":
        # Injeta o activity log e state para análise real
        activity_log = ""
        log_path = Path(state_dict.get("_project_root", ".")) / "servicemate_log.md"
        if log_path.exists():
            activity_log = log_path.read_text()

        gate_log = state_dict.get("gate_log", {})
        blocked = state_dict.get("blocked_reason", "")
        completed = state_dict.get("completed_nodes", [])

        return f"""Você é um agente de qualidade conduzindo uma retrospectiva honesta e técnica.

LEIA obrigatoriamente antes de escrever:
- project/docs/PRD.md
- project/docs/TASK_LIST.md
- project/docs/tech_stack.md
- Todos os arquivos em project/docs/ (forgebase-audit.md, smoke-report.md, frontend-prd-review.md se existirem)

DADOS DO CICLO (injetados pelo motor):

Activity Log:
{activity_log or "(nenhum log registrado)"}

Gate Log: {gate_log}
Nodes concluidos: {completed}
Blocked reason: {blocked or "nenhum"}

CHECKLIST OBRIGATÓRIO — verifique cada item explicitamente no retro.md:

INTEGRAÇÃO FULL-STACK:
[ ] O backend tem entry point HTTP (main.py / app.py com FastAPI/Flask)?
[ ] O frontend consegue se conectar ao backend (proxy configurado e backend sobe)?
[ ] Os testes unitários passam MAS o sistema funciona end-to-end?
[ ] Existe pelo menos 1 rota de API testada com request HTTP real?

COBERTURA FUNCIONAL:
[ ] Todas as User Stories P0 do PRD têm implementação verificável?
[ ] O fluxo principal (create→list→detail) funciona de ponta a ponta?
[ ] Empty states e error states estão implementados?

QUALIDADE DE PROCESSO:
[ ] Algum gate foi pulado ou forçado manualmente? Se sim, por quê?
[ ] Os validadores detectaram todos os problemas reais?
[ ] O smoke test testou o sistema rodando, não só testes unitários?

DÍVIDAS TÉCNICAS:
[ ] Liste TODAS as dívidas abertas com prioridade P0/P1/P2
[ ] Para cada dívida, identifique: causa raiz + o que o processo deveria ter pego

FORMATO OBRIGATÓRIO do {outputs_str}:
## 1. Resumo do Ciclo
## 2. O que foi entregue (tabela)
## 3. O que funcionou bem
## 4. Problemas Encontrados (subseções por categoria)
### 4.1 Integração e Servidor
### 4.2 Frontend/UX
### 4.3 Processo e Gates
## 5. Gaps de Detecção (o que o processo NÃO pegou e deveria ter pego)
## 6. Dívidas Técnicas (tabela priorizada)
## 7. Métricas do Ciclo
## 8. Ações para o Próximo Ciclo
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
        self._auto_approve = False

    def _log_activity(self, node_id: str, title: str, node_type: str, result: str, summary: str):
        """Registra atividade no terminal e em servicemate_log.md."""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        meta = f"  # [{ts}] {node_id} ({node_type}) → {result}: {summary}"
        print(meta)
        log_path = Path(self.project_root) / "servicemate_log.md"
        entry = f"| {ts} | `{node_id}` | {title} | {result} | {summary} |\n"
        if not log_path.exists():
            log_path.write_text(
                "# ServiceMate Activity Log\n\n"
                "| Timestamp | Node | Título | Resultado | Resumo |\n"
                "|-----------|------|--------|-----------|--------|\n"
            )
        with log_path.open("a") as f:
            f.write(entry)

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
          "sprint" — avanca ate o fim da sprint atual
          "mvp"    — avanca ate o fim ou BLOCK
        """
        from ft.engine.state import StateLockError
        try:
            state = self.state_mgr.load(check_lock=True)
        except StateLockError as e:
            print(f"  ERRO: {e}")
            return

        if state.current_node is None:
            print("Processo nao inicializado. Rode: ft init")
            return

        self._auto_approve = (mode == "mvp")

        # Determinar sprint de referencia para mode="sprint"
        start_sprint = self.graph.sprint_of(state.current_node) if state.current_node else None
        if mode == "sprint" and start_sprint:
            print(f"  Sprint: {start_sprint}")

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

            # Sprint boundary check — para se mudou de sprint
            if mode == "sprint" and start_sprint and node.sprint != start_sprint:
                print(f"\n  Sprint {start_sprint} completa → proximo: {node_id} (sprint {node.sprint})")
                self._generate_sprint_report(start_sprint, state)
                break

            print(f"\n{'─'*50}")
            print(f"  [{node_id}] {node.title}")
            sprint_label = f" | Sprint: {node.sprint}" if node.sprint else ""
            print(f"  Tipo: {node.type} | Executor: {node.executor}{sprint_label}")
            print(f"{'─'*50}")

            # Review node — expert gate via LLM
            if node.type == "review":
                self._run_review(node)
                if mode == "step":
                    break
                state = self.state_mgr.load()
                if state.node_status in ("blocked", "awaiting_approval"):
                    break
                continue

            # Gate — validacao pura, sem LLM
            if node.type == "gate":
                self._run_gate(node)
                if mode == "step":
                    break
                state = self.state_mgr.load()
                if state.node_status == "blocked":
                    self._log_activity(node_id, node.title, "gate", "BLOCKED", state.blocked_reason or "gate falhou")
                    break
                self._log_activity(node_id, node.title, "gate", "PASS", f"→ {self.graph.resolve_next(node_id) or 'fim'}")
                continue

            # Decision node — avaliar condicao e seguir branch
            if node.type == "decision":
                self._run_decision(node)
                if mode == "step":
                    break
                state = self.state_mgr.load()
                self._log_activity(node_id, node.title, "decision", "ROUTED", f"→ {state.current_node}")
                continue

            # Parallel group — fan-out/fan-in
            if node.parallel_group:
                group_nodes = self.graph.get_parallel_group(node.parallel_group)
                # So inicia fan-out se este e o primeiro do grupo nao completado
                completed = set(self.state_mgr.state.completed_nodes)
                group_pending = [n for n in group_nodes if n.id not in completed]
                if len(group_pending) > 1 and group_pending[0].id == node_id:
                    self._run_parallel_group(group_pending)
                    state = self.state_mgr.load()
                    if state.node_status in ("blocked", "awaiting_approval"):
                        break
                    if mode == "step":
                        break
                    continue

            # Discovery/Document/Build — delegar ao LLM
            if node.executor.startswith("llm"):
                self._run_llm_step(node)
            else:
                # Python executor — so validar
                self._run_gate(node)

            # Checar se ficou bloqueado ou aguardando aprovacao
            state = self.state_mgr.load()
            if state.node_status == "blocked":
                self._log_activity(node_id, node.title, node.type, "BLOCKED", state.blocked_reason or "bloqueado")
                break
            if state.node_status == "awaiting_approval":
                self._log_activity(node_id, node.title, node.type, "AWAITING_APPROVAL", "aguardando aprovacao humana")
                if self._auto_approve:
                    next_id = self.graph.resolve_next(state.current_node)
                    self.state_mgr.advance(state.current_node, next_id)
                    state = self.state_mgr.load()
                else:
                    break
            else:
                self._log_activity(node_id, node.title, node.type, "PASS", f"concluido → {self.graph.resolve_next(node_id) or 'fim'}")

            if mode == "step":
                break

    def _run_llm_step(self, node: Node):
        """Delega ao LLM, valida resultado, avanca ou retenta."""
        state = self.state_mgr.state
        state_dict = {**state.__dict__, "_project_root": self.project_root}
        task_prompt = build_task_prompt(node, state_dict)

        # Hyper-mode: enriquecer prompt com docs existentes
        if node.type in ("discovery", "document", "retro"):
            existing = scan_existing_docs(self.project_root)
            if existing:
                task_prompt = hyper_mode_prompt(existing, task_prompt)
                print(f"  Hyper-mode: {len(existing)} docs existentes carregados")

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
            # Auto-commit para nodes de build/test
            self._maybe_auto_commit(node)

            if node.requires_approval and not self._auto_approve:
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
                    self._maybe_auto_commit(node)

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
            if validation.artifacts:
                for k, v in validation.artifacts.items():
                    self.state_mgr.record_artifact(k, v)
            next_id = self.graph.resolve_next(node.id)
            self.state_mgr.advance(node.id, next_id, "PASS")
            print(f"  GATE PASS → proximo: {next_id}")
        else:
            self.state_mgr.block(f"Gate falhou: {validation.feedback}")
            self.state_mgr.state.gate_log[node.id] = "BLOCK"
            self.state_mgr.save()
            print(f"  GATE BLOCK: {validation.feedback}")

    def _maybe_auto_commit(self, node: Node):
        """Auto-commit apos PASS em nodes de build/test_green/refactor."""
        commit_types = ("build", "test_green", "refactor", "test_red")
        if node.type not in commit_types:
            return

        phase_labels = {
            "test_red": "red",
            "test_green": "green",
            "refactor": "refactor",
            "build": "feat",
        }
        label = phase_labels.get(node.type, "chore")
        message = f"{label}: {node.title} [{node.id}]"

        success, detail = auto_commit(
            message=message,
            project_root=self.project_root,
        )
        if success:
            print(f"  COMMIT: {detail}")
        else:
            print(f"  COMMIT SKIP: {detail}")

    def _run_decision(self, node: Node):
        """Roda decision node — avalia condicao e segue branch."""
        state = self.state_mgr.state
        state_dict = {
            "node_status": state.node_status,
            "blocked_reason": state.blocked_reason,
            **state.gate_log,
            **{k: v for k, v in state.artifacts.items() if v},
        }

        next_id = self.graph.resolve_next(node.id, state_dict)
        if next_id:
            self.state_mgr.advance(node.id, next_id)
            chosen = next_id
            print(f"  DECISION: condicao='{node.condition}' → {chosen}")
        else:
            self.state_mgr.block(f"Decision sem branch valido: condicao={node.condition}")
            print(f"  DECISION BLOCK: nenhum branch valido")

    def _run_review(self, node: Node):
        """
        Sprint Expert Gate — delega ao LLM especialista para revisao.
        Le o relatorio produzido e verifica APPROVED/REJECTED.
        """
        state = self.state_mgr.state
        task_prompt = build_task_prompt(node, {})

        allowed = []
        for output in node.outputs:
            parent = str(Path(output).parent)
            if parent not in allowed:
                allowed.append(parent)
        if not allowed:
            allowed = ["project/docs/"]

        print(f"  Expert Review ({node.executor})...")
        state.metrics["llm_calls"] = state.metrics.get("llm_calls", 0) + 1
        self.state_mgr.save()

        result = delegate_to_llm(
            task=task_prompt,
            project_root=self.project_root,
            allowed_paths=allowed,
        )

        if not result.success:
            self.state_mgr.block(f"Review falhou: {result.output[:300]}")
            print(f"  REVIEW BLOCK: LLM nao conseguiu revisar")
            return

        # Registrar artefato do relatorio
        for output_path in node.outputs:
            name = Path(output_path).stem
            self.state_mgr.record_artifact(name, output_path)

        # Validar artefatos deterministicos
        validation = run_validators(node, self.project_root)
        self._print_validation(validation)

        if not validation.passed:
            if validation.retryable:
                print(f"  REVIEW: validadores falharam, retentando...")
                result2 = delegate_with_feedback(
                    original_task=task_prompt,
                    feedback=validation.feedback or "",
                    project_root=self.project_root,
                    allowed_paths=allowed,
                )
                state.metrics["llm_calls"] = state.metrics.get("llm_calls", 0) + 1
                validation = run_validators(node, self.project_root)
                self._print_validation(validation)

            if not validation.passed:
                self.state_mgr.block(f"Review: validadores falharam: {validation.feedback}")
                return

        # Ler relatorio e verificar veredicto
        review_output = ""
        for output_path in node.outputs:
            full = Path(self.project_root) / output_path
            if full.exists():
                review_output = full.read_text()
                break

        # Veredicto deterministico via parse do relatorio
        output_upper = review_output.upper()
        if "REJECTED" in output_upper:
            # Extrair motivos
            lines = [l.strip() for l in review_output.splitlines() if l.strip()]
            reason_lines = []
            capture = False
            for line in lines:
                if "REJECTED" in line.upper():
                    capture = True
                if capture:
                    reason_lines.append(line)
                    if len(reason_lines) >= 5:
                        break
            reason = " | ".join(reason_lines[:3])
            self.state_mgr.block(f"Expert Review REJECTED: {reason[:300]}")
            print(f"  REVIEW REJECTED — verificar: {node.outputs[0] if node.outputs else ''}")
            return

        # APPROVED ou APPROVED WITH NOTES
        verdict = "APPROVED WITH NOTES" if "WITH NOTES" in output_upper else "APPROVED"
        next_id = self.graph.resolve_next(node.id)
        self.state_mgr.advance(node.id, next_id, verdict)
        print(f"  REVIEW {verdict} → proximo: {next_id}")

    def _run_parallel_group(self, nodes: list[Node]):
        """Fan-out: delega nodes independentes em paralelo via worktrees."""
        print(f"\n  PARALLEL GROUP: {len(nodes)} tasks")
        for n in nodes:
            print(f"    → {n.id}: {n.title}")

        tasks = []
        for n in nodes:
            allowed = [str(Path(o).parent) for o in n.outputs] or ["src/", "tests/"]
            tasks.append({
                "node_id": n.id,
                "task_prompt": build_task_prompt(n, {}),
                "allowed_paths": allowed,
                "outputs": n.outputs,
            })

        par = ParallelRunner(project_root=self.project_root, max_slots=2)
        try:
            results = par.run_parallel(tasks, delegate_to_llm)
        except ValueError as e:
            self.state_mgr.block(str(e))
            print(f"  PARALLEL BLOCK: {e}")
            return

        # Fan-in: merge + validar cada resultado
        all_passed = True
        for wt_result in results:
            node = self.graph.get_node(wt_result.node_id)
            if not wt_result.success:
                self.state_mgr.block(f"Parallel task falhou: {wt_result.node_id}")
                print(f"  PARALLEL FAIL: {wt_result.node_id}")
                all_passed = False
                continue

            # Merge worktree branch
            ok, detail = par.merge_all([wt_result])[0] if wt_result.branch else (False, "sem branch")
            if not ok:
                self.state_mgr.block(f"Merge falhou: {detail}")
                print(f"  MERGE FAIL: {detail}")
                all_passed = False
                continue

            print(f"  MERGED: {wt_result.node_id}")

        if not all_passed:
            return

        # Validar e avançar todos os nodes do grupo
        for wt_result in results:
            node = self.graph.get_node(wt_result.node_id)
            validation = run_validators(node, self.project_root)
            self._print_validation(validation)
            if validation.passed:
                next_id = self.graph.resolve_next(node.id)
                self.state_mgr.advance(node.id, next_id)
                print(f"  PARALLEL PASS: {node.id} → {next_id}")
            else:
                self.state_mgr.block(
                    f"Validacao falhou apos merge: {node.id}: {validation.feedback}"
                )
                return

    def _generate_sprint_report(self, sprint: str, state):
        """Gera relatorio de sprint."""
        sprint_nodes = self.graph.get_sprint_nodes(sprint)
        completed = set(state.completed_nodes)

        done = [n for n in sprint_nodes if n.id in completed]
        pending = [n for n in sprint_nodes if n.id not in completed]

        print(f"\n{'━'*50}")
        print(f"  Sprint Report: {sprint}")
        print(f"  Done: {len(done)}/{len(sprint_nodes)}")
        for n in done:
            gate = state.gate_log.get(n.id, "")
            print(f"    ✓ {n.id}: {n.title} [{gate}]")
        for n in pending:
            print(f"    ○ {n.id}: {n.title}")
        print(f"  LLM calls: {state.metrics.get('llm_calls', 0)}")
        print(f"{'━'*50}")

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

    def reject(self, reason: str, retry: bool = True):
        """
        Stakeholder rejeita artefato pendente.
        Se retry=True, reenvia ao LLM com feedback do motivo.
        """
        state = self.state_mgr.load()
        if not state.pending_approval:
            print("Nenhuma rejeicao pendente.")
            return

        node_id = state.pending_approval
        node = self.graph.get_node(node_id)
        print(f"  REJEITADO: {node_id} — {reason}")

        if retry and node.executor.startswith("llm"):
            # Reenviar ao LLM com feedback da rejeicao
            from ft.engine.delegate import delegate_with_feedback
            original_prompt = build_task_prompt(node, {})
            retry_prompt = build_rejection_prompt(original_prompt, reason)

            allowed = [str(Path(o).parent) for o in node.outputs] or ["src/", "project/docs/"]
            print(f"  Reenviando ao LLM com feedback da rejeicao...")

            # Desbloquear estado para retry
            state.node_status = "ready"
            state.pending_approval = None
            state.blocked_reason = None
            self.state_mgr.save()

            result = delegate_with_feedback(
                original_task=original_prompt,
                feedback=f"REJEITADO PELO STAKEHOLDER: {reason}",
                project_root=self.project_root,
                allowed_paths=allowed,
            )
            state.metrics["llm_calls"] = state.metrics.get("llm_calls", 0) + 1

            if result.success:
                validation = run_validators(node, self.project_root)
                self._print_validation(validation)
                if validation.passed:
                    print(f"  AGUARDANDO APROVACAO — rode: ft approve")
                    self.state_mgr.set_pending_approval(node.id)
                    return
            # Se retry falhou, bloquear
            self.state_mgr.block(f"Retry apos rejeicao falhou: {reason}")
        else:
            self.state_mgr.block(f"Rejeitado pelo stakeholder: {reason}")

    def status(self, full: bool = False):
        """Mostra estado atual."""
        state = self.state_mgr.load()
        completed = set(state.completed_nodes)
        node_status = self.graph.get_status(completed)

        # Determinar sprint atual
        current_sprint = None
        if state.current_node:
            current_sprint = self.graph.sprint_of(state.current_node)

        print(f"\n{'━'*50}")
        print(f"  Processo: {state.process_id} v{state.version}")
        print(f"  Node atual: {state.current_node}")
        print(f"  Status: {state.node_status}")
        if current_sprint:
            print(f"  Sprint: {current_sprint}")
        print(f"  Progresso: {state.metrics['steps_completed']}/{state.metrics['steps_total']}")
        if state.blocked_reason:
            print(f"  BLOCKED: {state.blocked_reason}")
        if state.pending_approval:
            print(f"  AGUARDANDO APROVACAO: {state.pending_approval}")
        print(f"{'━'*50}")

        if full:
            # Agrupar por sprint
            sprints = self.graph.get_sprints()
            no_sprint = [nid for nid, n in self.graph.nodes.items() if not n.sprint]

            if sprints:
                for sprint in sprints:
                    sprint_nodes = self.graph.get_sprint_nodes(sprint)
                    sprint_done = sum(1 for n in sprint_nodes if n.id in completed)
                    print(f"\n  [{sprint}] ({sprint_done}/{len(sprint_nodes)})")
                    for n in sprint_nodes:
                        status = node_status.get(n.id, "blocked")
                        icon = {"done": "✓", "ready": "→", "blocked": "○"}[status]
                        gate_result = state.gate_log.get(n.id, "")
                        gate_str = f" [{gate_result}]" if gate_result else ""
                        current = " ◀" if n.id == state.current_node else ""
                        print(f"    {icon} {n.id}: {n.title}{gate_str}{current}")

            if no_sprint:
                if sprints:
                    print(f"\n  [sem sprint]")
                else:
                    print(f"\n  Grafo:")
                for nid in no_sprint:
                    node = self.graph.get_node(nid)
                    status = node_status.get(nid, "blocked")
                    icon = {"done": "✓", "ready": "→", "blocked": "○"}[status]
                    gate_result = state.gate_log.get(nid, "")
                    gate_str = f" [{gate_result}]" if gate_result else ""
                    current = " ◀" if nid == state.current_node else ""
                    print(f"    {icon} {nid}: {node.title}{gate_str}{current}")

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
