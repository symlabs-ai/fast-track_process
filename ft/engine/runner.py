"""
Step Runner — loop principal do motor deterministico.
resolve_next() → delegate() → validate() → advance()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import os
import subprocess
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
from ft.engine.git_ops import auto_commit, commit_knowledge
from ft.engine.hooks import load_environment, run_hooks, hooks_all_passed
from ft.engine import ui
from ft.engine.parallel import ParallelRunner, check_independence
from ft.engine.stakeholder import (
    scan_existing_docs, should_skip_node,
    hyper_mode_prompt, build_rejection_prompt,
    format_pending_summary,
    scan_kb_lessons, kb_lessons_prompt,
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
    "sections_unchanged": val.sections_unchanged,
    "demand_coverage": val.demand_coverage,
    "prd_coverage": val.prd_coverage,
    "tests_pass": val.tests_pass,
    "tests_fail": val.tests_fail,
    "coverage_min": val.coverage_min,
    "gate_delivery": gates.gate_delivery,
    "gate_smoke": gates.gate_smoke,
    "gate_mvp": gates.gate_mvp,
    "gate_frontend": gates.gate_frontend,
    "gate_server_starts": gates.gate_server_starts,
    "gate_kb_review": gates.gate_kb_review,
    "gate_acceptance_cli": gates.gate_acceptance_cli,
    "gate_pulse_instrumented": gates.gate_pulse_instrumented,
    "screenshot_review_passed": gates.screenshot_review_passed,
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


def _resolve_validator_root(path: str, project_root: str, work_dir: str | None) -> str:
    """Resolve o root efetivo para um validator.

    Estratégia: se o arquivo existe no work_dir (que pode ser runs/<N>/
    prefixado ao project_root), usa work_dir. Senão, project_root.
    """
    if not work_dir or work_dir == project_root:
        return project_root
    # Se o arquivo existe no work_dir, usar work_dir (LLM escreveu lá)
    if path and (Path(work_dir) / path).exists():
        return work_dir
    # Fallback para project_root (docs compartilhados, process/, etc.)
    return project_root


def run_validators(node: Node, project_root: str, state_dir: str | None = None, work_dir: str | None = None) -> ValidationResult:
    """Roda todos os validadores de um node. Retorna resultado agregado."""
    items = []
    extra_artifacts: dict[str, str] = {}

    for validator_spec in node.validators:
        for name, args in validator_spec.items():
            fn = VALIDATOR_REGISTRY.get(name)
            if fn is None:
                items.append(ValidationItem(name=name, passed=False, detail=f"Validador desconhecido: {name}"))
                continue

            # Resolver root efetivo: docs/ → project_root, código → work_dir
            def _eff_root(path: str = "") -> str:
                return _resolve_validator_root(path, project_root, work_dir)

            # Validators booleanos de código (tests_pass, tests_exist, etc.) → work_dir
            _code_validators = ("tests_pass", "tests_fail", "tests_exist",
                                "coverage_min", "coverage_per_file",
                                "lint_clean", "format_check")

            # read_artifact — caso especial: args como dict com path/key/pattern
            if name == "read_artifact" and isinstance(args, dict):
                root = _eff_root(args.get("path", ""))
                passed, detail = fn(**args, project_root=root)
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
                passed, detail = fn(**args, project_root=_eff_root())
            elif name.startswith("gate_") and isinstance(args, bool) and args is True:
                # gate_delivery: true → usa outputs do node
                if name == "gate_delivery":
                    passed, detail = fn(outputs=node.outputs, project_root=_eff_root())
                else:
                    passed, detail = fn(project_root=_eff_root())
            elif isinstance(args, dict):
                # sections_unchanged: resolve snapshot_path relativo ao state_dir
                if name == "sections_unchanged" and state_dir and "snapshot_path" in args:
                    resolved_args = dict(args)
                    resolved_args["snapshot_path"] = str(
                        Path(state_dir) / args["snapshot_path"]
                    )
                    passed, detail = fn(**resolved_args, project_root=_eff_root(args.get("path", "")))
                else:
                    passed, detail = fn(**args, project_root=_eff_root())
            # Validadores simples
            elif isinstance(args, bool) and args is True:
                # Validators de código → work_dir; outros → project_root
                root = (work_dir or project_root) if name in _code_validators else project_root
                passed, detail = fn(project_root=root)
            elif isinstance(args, (int, float)):
                path = node.outputs[0] if node.outputs else ""
                if not path:
                    passed, detail = False, f"{name} FAIL: node sem outputs — não é possível inferir o path do artefato"
                else:
                    passed, detail = fn(path, args, project_root=_eff_root(path))
            elif isinstance(args, str):
                # Ex: file_exists: path → file_exists(path, project_root)
                passed, detail = fn(args, project_root=_eff_root(args))
            elif isinstance(args, list):
                path = node.outputs[0] if node.outputs else ""
                if not path:
                    passed, detail = False, f"{name} FAIL: node sem outputs — não é possível inferir o path do artefato"
                else:
                    passed, detail = fn(path, args, project_root=_eff_root(path))
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
        project_root = Path(state_dict.get("_project_root", ".")).resolve()
        log_path = project_root / f"{project_root.name}_log.md"
        if log_path.exists():
            activity_log = log_path.read_text()

        gate_log = state_dict.get("gate_log", {})
        blocked = state_dict.get("blocked_reason", "")
        completed = state_dict.get("completed_nodes", [])

        return f"""Você é um agente de qualidade conduzindo uma retrospectiva honesta e técnica.

LEIA obrigatoriamente antes de escrever:
- docs/PRD.md
- docs/TASK_LIST.md
- docs/tech_stack.md
- Todos os arquivos em docs/ (forgebase-audit.md, smoke-report.md, frontend-prd-review.md se existirem)

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

    def __init__(
        self,
        process_path: str | Path,
        state_path: str | Path,
        project_root: str | Path = ".",
        llm_engine: str | None = None,
        llm_model: str | None = None,
        verbose: bool = False,
    ):
        self.graph = load_graph(process_path)
        self.state_mgr = StateManager(state_path)
        self.project_root = str(Path(project_root).resolve())
        self._llm_engine_override = llm_engine.lower().strip() if llm_engine else None
        self._llm_model_override = llm_model.strip() if llm_model else None
        self._auto_approve = False
        self._verbose = verbose
        # KB path: diretório com lições de runs anteriores (opcional)
        self._kb_path = os.environ.get("FT_KB_PATH")
        # Nome do log derivado da pasta do projeto (ex: pokemon_log.md)
        self._log_filename = f"{Path(self.project_root).name}_log.md"
        # Environment config + hooks
        self._environment = load_environment(self.project_root)
        self._max_node_retries = self._environment.get("max_node_retries", MAX_RETRIES)
        self._max_gate_retries = self._environment.get("max_gate_retries", MAX_RETRIES)
        # Run mode: isolated → LLM trabalha em runs/<N>/, continuous → trabalha na raiz
        self._run_mode = self._environment.get("run_mode", "isolated")
        self._work_dir = self._resolve_work_dir()
        # Tracking para log enriquecido
        self._node_start_times: dict[str, datetime] = {}   # node_id → início
        self._node_attempts: dict[str, int] = {}            # node_id → nº tentativas

    def _stream_prefix(self, engine: str | None = None) -> str | None:
        """Retorna stream_prefix se verbose, None caso contrário."""
        if not self._verbose:
            return None
        label = engine or self._resolve_llm_engine()
        return f"{label}>"

    def _resolve_work_dir(self) -> str:
        """Resolve o diretório de trabalho (CWD) para delegação ao LLM.

        Modo isolated: runs/<N>/ (código gerado dentro do run)
        Modo continuous: project_root
        """
        if self._run_mode != "isolated":
            return self.project_root
        state_dir = self.state_mgr.path.parent  # runs/<N>/state/
        run_dir = state_dir.parent              # runs/<N>/
        if run_dir.parent.name == "runs":
            run_dir.mkdir(parents=True, exist_ok=True)
            return str(run_dir)
        return self.project_root

    @property
    def _run_dir(self) -> str | None:
        """Retorna o path absoluto do run dir ou None se continuous."""
        if self._work_dir != self.project_root:
            return self._work_dir
        return None

    def _delegate_allowed_paths(self, paths: list[str]) -> list[str]:
        """Ajusta allowed_paths para o modo isolated.

        No modo isolated, paths de docs/ são absolutos (vivem na raiz).
        Paths de código ficam relativos ao CWD (run dir).
        """
        if self._work_dir == self.project_root:
            return paths
        result = []
        for p in paths:
            if p.startswith("docs/") or p.startswith("process/") or p == "CHANGELOG.md":
                # docs/ e process/ vivem na raiz — path absoluto
                result.append(str(Path(self.project_root) / p))
            else:
                result.append(p)
        return result

    def _resolve_llm_engine(self, state: Any | None = None) -> str:
        """Resolve o executor LLM efetivo para esta run."""
        if self._llm_engine_override:
            return self._llm_engine_override
        if state is not None and getattr(state, "llm_engine", None):
            return state.llm_engine
        env_engine = os.environ.get("FT_LLM_ENGINE", "").strip().lower()
        return env_engine or "claude"

    def _resolve_llm_model(self, state: Any | None = None) -> str | None:
        """Resolve o modelo LLM efetivo (None = usar default do engine)."""
        if self._llm_model_override:
            return self._llm_model_override
        if state is not None and getattr(state, "llm_model", None):
            return state.llm_model
        return os.environ.get("FT_LLM_MODEL") or None

    def _persist_llm_engine(self, state: Any) -> None:
        """Persiste engine e model no estado para comandos subsequentes do projeto."""
        effective_engine = self._resolve_llm_engine(state)
        effective_model = self._resolve_llm_model(state)
        changed = False
        if getattr(state, "llm_engine", None) != effective_engine:
            state.llm_engine = effective_engine
            changed = True
        if getattr(state, "llm_model", None) != effective_model:
            state.llm_model = effective_model
            changed = True
        if changed:
            self.state_mgr.save()

    def _decision_state_dict(self, state: Any) -> dict[str, Any]:
        """Constroi o contexto deterministico usado para resolver decisions."""
        return {
            "node_status": state.node_status,
            "blocked_reason": state.blocked_reason,
            **state.gate_log,
            **{k: v for k, v in state.artifacts.items() if v},
        }

    def _predecessor_ids(self, node_id: str) -> list[str]:
        """Retorna todos os predecessores imediatos de um node."""
        predecessors: list[str] = []
        for other_id, other_node in self.graph.nodes.items():
            if other_node.next == node_id:
                predecessors.append(other_id)
            if other_node.branches:
                for target in other_node.branches.values():
                    if target == node_id:
                        predecessors.append(other_id)
        return predecessors

    def _refresh_progress_metrics(self, state: Any) -> bool:
        """Recalcula métricas de progresso a partir do grafo atual."""
        total_steps = sum(1 for node in self.graph.nodes.values() if node.type != "end")
        completed_steps = sum(
            1
            for node_id in state.completed_nodes
            if node_id in self.graph.nodes and self.graph.get_node(node_id).type != "end"
        )

        changed = False
        if state.metrics.get("steps_total") != total_steps:
            state.metrics["steps_total"] = total_steps
            changed = True
        if state.metrics.get("steps_completed") != completed_steps:
            state.metrics["steps_completed"] = completed_steps
            changed = True
        return changed

    def _reconcile_state_with_graph(self, state: Any) -> bool:
        """
        Alinha estado persistido ao grafo atual.

        Isso corrige drift de versão do processo e backfill seguro de decisions
        inseridas depois que um projeto já havia avançado além delas.
        """
        changed = False

        deduped_completed = list(dict.fromkeys(state.completed_nodes))
        if deduped_completed != state.completed_nodes:
            state.completed_nodes = deduped_completed
            changed = True

        completed = set(state.completed_nodes)
        progress_frontier = set(completed)
        if state.current_node:
            progress_frontier.add(state.current_node)

        decision_state = self._decision_state_dict(state)
        for node in self.graph.nodes.values():
            if node.type != "decision" or node.id in completed:
                continue

            predecessors = self._predecessor_ids(node.id)
            if predecessors and not all(pred in completed for pred in predecessors):
                continue

            resolved_next = self.graph.resolve_next(node.id, decision_state)
            if not resolved_next or resolved_next not in progress_frontier:
                continue

            state.completed_nodes.append(node.id)
            state.gate_log.setdefault(node.id, "PASS")
            completed.add(node.id)
            progress_frontier.add(node.id)
            changed = True

        if self._refresh_progress_metrics(state):
            changed = True

        known_ids = {node.id for node in self.graph.nodes.values()}
        ordered_known = [node.id for node in self.graph.nodes.values() if node.id in state.completed_nodes]
        unknown_ids = [node_id for node_id in state.completed_nodes if node_id not in known_ids]
        normalized_completed = ordered_known + unknown_ids
        if normalized_completed != state.completed_nodes:
            state.completed_nodes = normalized_completed
            changed = True

        return changed

    def _sync_process_meta(self, state: Any) -> None:
        """Mantém process_id/version do estado alinhados ao grafo canônico carregado."""
        expected_id = self.graph.meta.get("id", state.process_id)
        expected_version = self.graph.meta.get("version", state.version)
        if state.process_id != expected_id or state.version != expected_version:
            state.process_id = expected_id
            state.version = expected_version
            changed = True
        else:
            changed = False

        if self._reconcile_state_with_graph(state):
            changed = True

        if changed:
            self.state_mgr.save()

    def _llm_log_dir(self) -> Path:
        """Diretório persistente para logs detalhados do executor LLM."""
        return self.state_mgr.path.parent / "llm_logs"

    def _display_path(self, path: Path) -> str:
        """Formata path relativo ao projeto quando possível."""
        try:
            return str(path.resolve().relative_to(Path(self.project_root).resolve()))
        except ValueError:
            return str(path)

    def _build_llm_log_path(self, node_id: str, phase: str) -> Path:
        """Gera nome estável e legível para um log de step delegado."""
        safe_node = node_id.replace("/", "-")
        safe_phase = phase.replace("/", "-")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        suffix = ".jsonl" if self._resolve_llm_engine(self.state_mgr.state) == "codex" else ".log"
        return self._llm_log_dir() / f"{stamp}__{safe_node}__{safe_phase}{suffix}"

    def _resolve_allowed_paths(self, node: Node) -> list[str]:
        """Resolve o escopo de escrita efetivo do node."""
        if node.write_scope:
            return list(dict.fromkeys(node.write_scope))

        allowed = []
        for output in node.outputs:
            parent = str(Path(output).parent)
            if parent not in allowed:
                allowed.append(parent)
        if allowed:
            return allowed

        return ["src/", "tests/", "docs/"]

    def _start_llm_log(self, state: Any, node_id: str, phase: str) -> str:
        """Registra no estado o log ativo para a delegação corrente."""
        log_path = self._build_llm_log_path(node_id, phase)
        rel = self._display_path(log_path)
        state.active_llm_log = rel
        state.last_llm_log = rel
        print(f"  LLM log: {rel}")
        return str(log_path)

    def _clear_active_llm_log(self, state: Any) -> None:
        """Limpa referência ao log ativo após a conclusão do subprocesso."""
        if getattr(state, "active_llm_log", None):
            state.active_llm_log = None
            self.state_mgr.save()

    def _validator_snapshot_specs(self, node: Node) -> list[dict[str, Any]]:
        """Extrai configurações de snapshot usadas por validadores do node."""
        specs: list[dict[str, Any]] = []
        for validator_spec in node.validators:
            config = validator_spec.get("sections_unchanged")
            if isinstance(config, dict):
                specs.append(config)
        return specs

    def _resolve_snapshot_path(self, snapshot_path: str) -> Path:
        """Resolve snapshot_path relativo ao diretório de state (runs/<N>/state/)."""
        return self.state_mgr.path.parent / snapshot_path

    def _prepare_validator_snapshots(self, node: Node) -> None:
        """Cria baselines determinísticos antes de steps que reescrevem artefatos."""
        for spec in self._validator_snapshot_specs(node):
            source_path = spec.get("path")
            snapshot_path = spec.get("snapshot_path")
            if not source_path or not snapshot_path:
                continue

            source = Path(self.project_root) / source_path
            snapshot = self._resolve_snapshot_path(snapshot_path)
            if snapshot.exists() or not source.exists():
                continue

            snapshot.parent.mkdir(parents=True, exist_ok=True)
            snapshot.write_text(source.read_text())
            print(f"  Snapshot baseline: {self._display_path(snapshot)}")

    def _clear_validator_snapshots(self, node_id: str) -> None:
        """Remove baselines temporários após sucesso do node."""
        try:
            node = self.graph.get_node(node_id)
        except KeyError:
            return

        for spec in self._validator_snapshot_specs(node):
            snapshot_path = spec.get("snapshot_path")
            if not snapshot_path:
                continue
            snapshot = self._resolve_snapshot_path(snapshot_path)
            if snapshot.exists():
                snapshot.unlink()

    def _reset_validator_snapshots(self) -> None:
        """Limpa snapshots órfãos ao reinicializar um projeto."""
        for node in self.graph.nodes.values():
            for spec in self._validator_snapshot_specs(node):
                snapshot_path = spec.get("snapshot_path")
                if not snapshot_path:
                    continue
                snapshot = self._resolve_snapshot_path(snapshot_path)
                if snapshot.exists():
                    snapshot.unlink()

    def _advance_state(self, completed_node: str, next_node: str | None, gate_result: str = "PASS") -> None:
        """Avança o estado após sucesso, resolvendo bloqueios antigos do mesmo node."""
        if self.state_mgr.state.node_status == "blocked":
            self.state_mgr.unblock()
        self.state_mgr.advance(completed_node, next_node, gate_result)
        state = self.state_mgr.state
        if self._refresh_progress_metrics(state):
            self.state_mgr.save()
        self._clear_validator_snapshots(completed_node)

    def _fire_hooks(self, event: str) -> bool:
        """Dispara hooks para um evento. Retorna True se todos passaram (ou nenhum)."""
        results = run_hooks(event, self.project_root, self._environment)
        if not results:
            return True
        return hooks_all_passed(results)

    def _mark_node_start(self, node_id: str):
        """Registra o instante de início de um node (para cálculo de duração)."""
        self._node_start_times[node_id] = datetime.now()
        self._node_attempts[node_id] = self._node_attempts.get(node_id, 0) + 1

    def _init_log(self):
        """Cria <projeto>_log.md com frontmatter YAML e cabeçalho da tabela.

        Chamado em init_state() — garante que o log existe antes do primeiro node.
        """
        import importlib.metadata as _im

        log_path = Path(self.project_root) / self._log_filename
        if log_path.exists():
            return  # já inicializado (retomada de run)

        try:
            ft_version = _im.version("ft-engine")
        except Exception:
            ft_version = "dev"

        state_dict: dict = {}
        try:
            state_dict = self.state_mgr.load().__dict__
        except Exception:
            pass

        ts = datetime.now()
        run_meta = (
            "---\n"
            f"project: {Path(self.project_root).name}\n"
            f"ft_version: {ft_version}\n"
            f"process_variant: {self.graph.meta.get('id', 'unknown')}\n"
            f"cycle: {state_dict.get('cycle', 'cycle-01')}\n"
            f"interface_type: {state_dict.get('interface_type', 'unknown')}\n"
            f"run_date: {ts.strftime('%Y-%m-%d')}\n"
            "---\n\n"
            "# Run Log\n\n"
            "| timestamp | node_id | title | sprint | type | attempt | duration_s | result | summary |\n"
            "|-----------|---------|-------|--------|------|---------|------------|--------|---------|\n"
        )
        log_path.write_text(run_meta)

    def _log_event(self, event_id: str, title: str, result: str, summary: str):
        """Loga um evento de sistema (init, environment) na mesma tabela do run log.

        Usa type=system e sprint/attempt/duration vazios — distingue de nodes de processo.
        """
        ts_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"  # [{ts_str}] {event_id} (system) → {result}: {summary}")
        log_path = Path(self.project_root) / self._log_filename
        entry = f"| {ts_str} | `{event_id}` | {title} |  | system |  |  | {result} | {summary} |\n"
        with log_path.open("a") as f:
            f.write(entry)

    def _log_activity(self, node_id: str, title: str, node_type: str, result: str,
                      summary: str, sprint: str | None = None):
        """Registra atividade no terminal e em <projeto>_log.md.

        Colunas extras para análise/ML:
          sprint     — sprint à qual o node pertence (feature do grafo)
          type       — tipo do node (gate/build/decision/review/…)
          attempt    — número da tentativa (1 = primeira, 2+ = retry)
          duration_s — segundos decorridos desde _mark_node_start()
        """
        ts = datetime.now()
        ts_str = ts.strftime("%Y-%m-%d %H:%M:%S")

        attempt = self._node_attempts.get(node_id, 1)

        start = self._node_start_times.get(node_id)
        duration_s = round((ts - start).total_seconds(), 1) if start else ""

        sprint_label = sprint or ""

        meta = f"  # [{ts_str}] {node_id} ({node_type}) → {result}: {summary}"
        print(meta)

        log_path = Path(self.project_root) / self._log_filename
        entry = (
            f"| {ts_str} | `{node_id}` | {title} | {sprint_label} | {node_type} "
            f"| {attempt} | {duration_s} | {result} | {summary} |\n"
        )
        with log_path.open("a") as f:
            f.write(entry)

    def init_state(self):
        """Inicializa estado a partir do grafo."""
        self._reset_validator_snapshots()
        first = self.graph.first_node()
        total = len([n for n in self.graph.nodes.values() if n.type != "end"])
        self.state_mgr.init_from_graph(
            self.graph.meta,
            first.id,
            total,
            llm_engine=self._resolve_llm_engine(),
        )
        print(ui.init_banner(
            self.graph.meta.get("title", "?"), first.id, first.title, total,
        ))
        self._init_log()
        self._log_event(
            "INIT",
            "Inicialização do processo",
            "PASS",
            f"process={self.graph.meta.get('id', '?')} nodes={total} first={first.id}",
        )
        self._fire_hooks("on_init")

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
            print(ui.fail(str(e)))
            return

        self._persist_llm_engine(state)
        self._sync_process_meta(state)

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
                print(ui.process_complete(
                    state.metrics['steps_completed'], state.metrics['steps_total'],
                ))
                # Commitar conhecimento produzido pelo ciclo
                ok, detail = commit_knowledge(self.project_root, label="pós-run — ciclo completo")
                print(ui.dim(detail))
                self._fire_hooks("on_deliver")
                self._advance_state(node_id, None)
                break

            # Sprint boundary check — para se mudou de sprint
            if mode == "sprint" and start_sprint and node.sprint != start_sprint:
                print(ui.sprint_complete(start_sprint))
                print(ui.info(f"Próximo: {node_id} (sprint {node.sprint})"))
                self._generate_sprint_report(start_sprint, state)
                break

            step_num = len(state.completed_nodes) + 1
            step_total = state.metrics.get("steps_total", "?")
            print(ui.step_card(
                step_num, step_total, node.title,
                node_id, node.type, node.executor, node.sprint,
            ))

            self._mark_node_start(node_id)
            self._fire_hooks("on_node_start")
            node_sprint = node.sprint or None

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
                    self._log_activity(node_id, node.title, "gate", "BLOCKED",
                                       state.blocked_reason or "gate falhou", sprint=node_sprint)
                    break
                self._log_activity(node_id, node.title, "gate", "PASS",
                                   f"→ {self.graph.resolve_next(node_id) or 'fim'}", sprint=node_sprint)
                continue

            # Decision node — avaliar condicao e seguir branch
            if node.type == "decision":
                self._run_decision(node)
                if mode == "step":
                    break
                state = self.state_mgr.load()
                self._log_activity(node_id, node.title, "decision", "ROUTED",
                                   f"→ {state.current_node}", sprint=node_sprint)
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
                self._log_activity(node_id, node.title, node.type, "BLOCKED",
                                   state.blocked_reason or "bloqueado", sprint=node_sprint)
                break
            if state.node_status == "awaiting_approval":
                if self._auto_approve:
                    print(ui.awaiting_approval(auto=True))
                    self._log_activity(node_id, node.title, node.type, "AUTO_APPROVED",
                                       "auto-aprovado (modo MVP)", sprint=node_sprint)
                    next_id = self.graph.resolve_next(state.current_node)
                    self._advance_state(state.current_node, next_id)
                    state = self.state_mgr.load()
                else:
                    print(ui.awaiting_approval(auto=False))
                    self._log_activity(node_id, node.title, node.type, "AWAITING_APPROVAL",
                                       "aguardando aprovacao humana", sprint=node_sprint)
                    break
            else:
                self._fire_hooks("on_node_end")
                self._log_activity(node_id, node.title, node.type, "PASS",
                                   f"concluido → {self.graph.resolve_next(node_id) or 'fim'}",
                                   sprint=node_sprint)

            if mode == "step":
                break

    def _run_llm_step(self, node: Node):
        """Delega ao LLM, valida resultado, avanca ou retenta."""
        state = self.state_mgr.state
        self._prepare_validator_snapshots(node)

        # Pre-seed check: se todos os outputs já existem e os validators passam,
        # pula delegação ao LLM — o artefato foi fornecido externamente (ex: --hipotese).
        # NÃO aplica a build nodes: um scaffold de passo anterior não conta como implementação.
        if node.outputs and node.type not in ("build",) and not self._validator_snapshot_specs(node):
            all_exist = all(
                (Path(self.project_root) / o).exists() for o in node.outputs
            )
            if all_exist:
                validation = run_validators(node, self.project_root, state_dir=str(self.state_mgr.path.parent), work_dir=self._run_dir)
                if validation.passed:
                    print(ui.success("Artefato já existe e é válido — pulando etapa"))
                    self._log_event(
                        f"SEED:{node.id}",
                        f"Artefato pré-existente: {node.title}",
                        "PASS",
                        f"outputs={', '.join(node.outputs)}",
                    )
                    for output_path in node.outputs:
                        self.state_mgr.record_artifact(Path(output_path).stem, output_path)
                    if node.requires_approval and not self._auto_approve:
                        print(ui.awaiting_approval(auto=self._auto_approve))
                        self.state_mgr.set_pending_approval(node.id)
                        return
                    next_id = self.graph.resolve_next(node.id)
                    self._advance_state(node.id, next_id)
                    print(ui.step_pass(next_id, "PASS (pre-seed)"))
                    return

        state_dict = {**state.__dict__, "_project_root": self.project_root}
        task_prompt = build_task_prompt(node, state_dict)

        # Hyper-mode: enriquecer prompt com docs existentes
        if node.type in ("discovery", "document", "retro"):
            existing = scan_existing_docs(self.project_root)
            if existing:
                task_prompt = hyper_mode_prompt(existing, task_prompt)
                print(f"  Hyper-mode: {len(existing)} docs existentes carregados")

        # KB-mode: injetar lições de runs anteriores em nodes de build, refactor e retro
        if node.type in ("build", "refactor", "retro"):
            itype = state_dict.get("artifacts", {}).get("interface_type") or state_dict.get("interface_type")
            lessons = scan_kb_lessons(self._kb_path, interface_type=itype) if self._kb_path else []
            if lessons:
                task_prompt = kb_lessons_prompt(lessons, task_prompt)
                print(f"  KB-mode: lições de runs anteriores injetadas")

        # Determinar paths permitidos
        allowed = self._resolve_allowed_paths(node)

        # env_setup: comandos determinísticos antes da delegação (não consome turns)
        if node.env_setup:
            self._run_env_setup(node)

        print(ui.info(f"Delegando ao LLM ({node.executor})..."))
        state.node_status = "delegated"
        state.metrics["llm_calls"] = state.metrics.get("llm_calls", 0) + 1
        log_path = self._start_llm_log(state, node.id, "run")
        self.state_mgr.save()

        delegate_kwargs: dict = dict(
            task=task_prompt,
            project_root=self._work_dir,
            allowed_paths=allowed,
            llm_engine=self._resolve_llm_engine(state),
            llm_model=self._resolve_llm_model(state),
            log_path=log_path,
            stream_prefix=self._stream_prefix(self._resolve_llm_engine(state)),
        )
        if node.max_turns is not None:
            delegate_kwargs["max_turns"] = node.max_turns

        try:
            result = delegate_to_llm(**delegate_kwargs)
        finally:
            self._clear_active_llm_log(state)

        if not result.success:
            print(ui.fail(f"LLM reportou BLOCKED: {result.output[:200]}"))
            self.state_mgr.block(f"LLM falhou: {result.output[:500]}")
            return

        # Registrar artefatos
        for output_path in node.outputs:
            name = Path(output_path).stem
            self.state_mgr.record_artifact(name, output_path)

        # Validar
        print(ui.info("Validando..."))
        validation = run_validators(node, self.project_root, state_dir=str(self.state_mgr.path.parent), work_dir=self._run_dir)
        self._print_validation(validation)

        if validation.passed:
            # Auto-commit para nodes de build/test
            self._maybe_auto_commit(node)

            if node.requires_approval and not self._auto_approve:
                print(ui.awaiting_approval(auto=self._auto_approve))
                self.state_mgr.set_pending_approval(node.id)
                return

            next_id = self.graph.resolve_next(node.id)
            self._advance_state(node.id, next_id)
            print(ui.step_pass(next_id))
            return

        # Retry
        if validation.retryable:
            for retry in range(1, self._max_node_retries + 1):
                print(ui.retry(retry, self._max_node_retries))
                print(ui.info(f"Corrigindo automaticamente: {validation.feedback or 'validação falhou'}"))
                retry_log_path = self._start_llm_log(state, node.id, f"retry-{retry}")
                self.state_mgr.save()
                try:
                    result = delegate_with_feedback(
                        original_task=task_prompt,
                        feedback=validation.feedback or "",
                        project_root=self._work_dir,
                        allowed_paths=self._delegate_allowed_paths(allowed),
                        llm_engine=self._resolve_llm_engine(state),
                        llm_model=self._resolve_llm_model(state),
                        log_path=retry_log_path,
                        stream_prefix=self._stream_prefix(self._resolve_llm_engine(state)),
                    )
                finally:
                    self._clear_active_llm_log(state)
                state.metrics["llm_calls"] = state.metrics.get("llm_calls", 0) + 1

                validation = run_validators(node, self.project_root, state_dir=str(self.state_mgr.path.parent), work_dir=self._run_dir)
                self._print_validation(validation)

                if validation.passed:
                    self._maybe_auto_commit(node)

                    if node.requires_approval:
                        print(ui.awaiting_approval(auto=self._auto_approve))
                        self.state_mgr.set_pending_approval(node.id)
                        return
                    next_id = self.graph.resolve_next(node.id)
                    self._advance_state(node.id, next_id)
                    print(ui.step_pass(next_id, f"PASS (retry {retry})"))
                    return

        # Esgotou retries
        self.state_mgr.block(f"Validacao falhou apos {self._max_node_retries} tentativas: {validation.feedback}")
        print(ui.step_block(f"validação falhou após {self._max_node_retries} tentativas"))

    def _run_gate(self, node: Node):
        """Roda gate — validacao pura sem LLM. Em modo mvp, tenta corrigir via LLM."""
        print(ui.info("Rodando gate..."))
        validation = run_validators(node, self.project_root, state_dir=str(self.state_mgr.path.parent), work_dir=self._run_dir)
        self._print_validation(validation)

        if validation.passed:
            self._gate_accept(node, validation)
            return

        # Verificar se o erro é de configuração (não faz sentido delegar ao LLM)
        _engine_errors = ("node sem outputs", "Validador desconhecido")
        is_engine_error = any(
            marker in (item.detail or "")
            for item in validation.items if not item.passed
            for marker in _engine_errors
        )

        # Em modo mvp, tentar corrigir via LLM antes de bloquear
        if self._auto_approve and self._max_gate_retries > 0 and not is_engine_error:
            previous_errors: list[str] = []
            for attempt in range(1, self._max_gate_retries + 1):
                print(ui.retry(attempt, self._max_gate_retries))
                print(ui.info(f"Gate falhou — delegando correção ao LLM..."))

                state = self.state_mgr.state
                feedback = validation.feedback or "gate falhou"
                previous_errors.append(feedback)

                history_block = ""
                if attempt > 1:
                    history_block = (
                        "\n\nTENTATIVAS ANTERIORES QUE NÃO RESOLVERAM:\n"
                        + "\n".join(f"  - Tentativa {i+1}: {e}" for i, e in enumerate(previous_errors[:-1]))
                        + "\n\nNÃO repita a mesma abordagem. Tente algo diferente.\n"
                    )

                fix_prompt = (
                    f"O gate '{node.title}' ({node.id}) falhou com o seguinte erro:\n\n"
                    f"{feedback}\n\n"
                    f"Corrija o problema para que o gate passe. "
                    f"Analise o erro, identifique a causa raiz e faça as alterações necessárias."
                    f"{history_block}\n"
                    f"Quando terminar, diga DONE."
                )

                log_path = self._start_llm_log(state, node.id, f"gate-fix-{attempt}")
                self.state_mgr.save()
                try:
                    result = delegate_to_llm(
                        task=fix_prompt,
                        project_root=self._work_dir,
                        allowed_paths=self._delegate_allowed_paths(["src/", "tests/", "docs/", "main.py", "app.py", "server.py", "frontend/"]),
                        llm_engine=self._resolve_llm_engine(state),
                        llm_model=self._resolve_llm_model(state),
                        log_path=log_path,
                        stream_prefix=self._stream_prefix(self._resolve_llm_engine(state)),
                    )
                finally:
                    self._clear_active_llm_log(state)

                # Re-validar
                validation = run_validators(node, self.project_root, state_dir=str(self.state_mgr.path.parent), work_dir=self._run_dir)
                self._print_validation(validation)

                if validation.passed:
                    print(ui.success(f"Gate corrigido na tentativa {attempt}"))
                    self._gate_accept(node, validation)
                    return

            # Esgotou retries
            print(ui.fail(f"Gate não corrigido após {self._max_gate_retries} tentativas"))

        if is_engine_error:
            # Tentar autofix antes de desistir
            if self._try_autofix_gate(node, validation):
                return
            # Autofix não resolveu — explicar em linguagem clara
            self._explain_gate_problem(node, validation)

        self.state_mgr.block(f"Gate falhou: {validation.feedback}")
        self.state_mgr.state.gate_log[node.id] = "BLOCK"
        self.state_mgr.save()
        self._fire_hooks("on_gate_fail")
        print(ui.gate_block(validation.feedback or "validação falhou"))

    def _try_autofix_gate(self, node: Node, validation: ValidationResult) -> bool:
        """Tenta autocorrigir erros de configuração do gate. Retorna True se resolveu."""
        import yaml as _yaml

        fixed = False

        # Autofix: "node sem outputs" + tem file_exists no mesmo gate → copiar path
        has_missing_outputs = any(
            "node sem outputs" in (item.detail or "")
            for item in validation.items if not item.passed
        )
        if has_missing_outputs and not node.outputs:
            # Procurar path no file_exists do mesmo node
            for spec in node.validators:
                if "file_exists" in spec and isinstance(spec["file_exists"], str):
                    inferred_path = spec["file_exists"]
                    node.outputs = [inferred_path]
                    print(ui.autofix_applied(
                        f"outputs inferido de file_exists → [{inferred_path}]"
                    ))
                    fixed = True
                    break

        if not fixed:
            return False

        # Re-validar com o fix aplicado
        validation = run_validators(node, self.project_root, state_dir=str(self.state_mgr.path.parent), work_dir=self._run_dir)
        self._print_validation(validation)

        if validation.passed:
            print(ui.success("Autocorreção resolveu o problema"))
            self._gate_accept(node, validation)
            return True

        return False

    def _explain_gate_problem(self, node: Node, validation: ValidationResult) -> None:
        """Explica o problema do gate em linguagem clara para o usuário."""
        failed_items = [item for item in validation.items if not item.passed]
        if not failed_items:
            return

        detail = failed_items[0].detail or ""

        if "node sem outputs" in detail:
            what = (
                f"O gate \"{node.title}\" precisa verificar o conteúdo de um arquivo, "
                f"mas não sabe qual arquivo verificar."
            )
            alternatives = [
                f"Informar qual arquivo o gate deve verificar (provável: o mesmo do file_exists)",
                f"Remover a verificação de conteúdo deste gate (menos seguro)",
                f"Pular este gate e continuar o processo",
            ]
        elif "Validador desconhecido" in detail:
            validator_name = detail.split(":")[-1].strip() if ":" in detail else "?"
            what = (
                f"O gate \"{node.title}\" usa uma verificação chamada \"{validator_name}\" "
                f"que o sistema não reconhece. Pode ser um erro de digitação."
            )
            alternatives = [
                f"Corrigir o nome da verificação no processo",
                f"Remover essa verificação do gate",
                f"Pular este gate e continuar o processo",
            ]
        else:
            what = f"O gate \"{node.title}\" encontrou um problema de configuração: {detail}"
            alternatives = [
                "Investigar e corrigir o problema",
                "Pular este gate e continuar o processo",
            ]

        print(ui.problem_explanation(what, alternatives, node.id))

    def _gate_accept(self, node: Node, validation: ValidationResult):
        """Aceita um gate que passou — registra artefatos e avança."""
        if validation.artifacts:
            for k, v in validation.artifacts.items():
                self.state_mgr.record_artifact(k, v)
        next_id = self.graph.resolve_next(node.id)
        self._advance_state(node.id, next_id, "PASS")
        self._fire_hooks("on_gate_pass")
        print(ui.gate_pass(next_id))

    def _run_env_setup(self, node: Node) -> None:
        """Executa comandos de env_setup antes da delegação ao LLM.

        Comandos rodam no work_dir (run dir no modo isolated).
        Se qualquer comando falhar, bloqueia o node.
        """
        print(ui.info(f"env_setup: {len(node.env_setup)} comando(s)"))
        for cmd in node.env_setup:
            print(f"    $ {cmd}")
            result = subprocess.run(
                cmd, shell=True, cwd=self._work_dir,
                capture_output=True, text=True, timeout=300,
            )
            if result.returncode != 0:
                err = result.stderr.strip() or result.stdout.strip()
                print(ui.fail(f"env_setup falhou: {cmd}"))
                print(f"    {err[:300]}")
                self.state_mgr.block(f"env_setup falhou: {cmd}\n{err[:500]}")
                return
            if result.stdout.strip():
                # Mostrar última linha do output para feedback
                last_line = result.stdout.strip().splitlines()[-1]
                print(f"    → {last_line[:120]}")
        print(ui.info("env_setup concluído"))

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
            print(ui.success(f"COMMIT: {detail}"))
        else:
            print(ui.dim(f"COMMIT SKIP: {detail}"))

    def _run_decision(self, node: Node):
        """Roda decision node — avalia condicao e segue branch."""
        state = self.state_mgr.state
        state_dict = self._decision_state_dict(state)

        next_id = self.graph.resolve_next(node.id, state_dict)
        if next_id:
            self._advance_state(node.id, next_id)
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

        allowed = self._resolve_allowed_paths(node)

        # Verificar se artefatos já existem e validators já passam (ex: retry após max-turns)
        early_check = run_validators(node, self.project_root, state_dir=str(self.state_mgr.path.parent), work_dir=self._run_dir)
        if early_check.passed:
            print(ui.success("Expert Review: artefatos já existem e validação OK — pulando etapa"))
            for output_path in node.outputs:
                self.state_mgr.record_artifact(Path(output_path).stem, output_path)
            next_id = node.next
            self._advance_state(node.id, next_id, "PASS")
            return

        print(f"  Expert Review ({node.executor})...")
        state.metrics["llm_calls"] = state.metrics.get("llm_calls", 0) + 1
        review_log_path = self._start_llm_log(state, node.id, "review")
        self.state_mgr.save()

        review_kwargs: dict = dict(
            task=task_prompt,
            project_root=self._work_dir,
            allowed_paths=self._delegate_allowed_paths(allowed),
            llm_engine=self._resolve_llm_engine(state),
            llm_model=self._resolve_llm_model(state),
            log_path=review_log_path,
            stream_prefix=self._stream_prefix(self._resolve_llm_engine(state)),
        )
        if node.max_turns is not None:
            review_kwargs["max_turns"] = node.max_turns

        try:
            result = delegate_to_llm(**review_kwargs)
        finally:
            self._clear_active_llm_log(state)

        if not result.success:
            # Mesmo com falha do LLM (ex: max-turns atingido), verificar se os artefatos
            # foram produzidos e os validators passam — o LLM pode ter concluído antes de parar.
            pre_check = run_validators(node, self.project_root, state_dir=str(self.state_mgr.path.parent), work_dir=self._run_dir)
            if pre_check.passed:
                print(f"  REVIEW: LLM encerrou com erro mas artefatos OK — validadores passaram")
                result.success = True  # tratamos como sucesso
            else:
                self.state_mgr.block(f"Review falhou: {result.output[:300]}")
                print(f"  REVIEW BLOCK: LLM nao conseguiu revisar")
                return

        # Registrar artefato do relatorio
        for output_path in node.outputs:
            name = Path(output_path).stem
            self.state_mgr.record_artifact(name, output_path)

        # Validar artefatos deterministicos
        validation = run_validators(node, self.project_root, state_dir=str(self.state_mgr.path.parent), work_dir=self._run_dir)
        self._print_validation(validation)

        if not validation.passed:
            if validation.retryable:
                print(f"  REVIEW: validadores falharam, retentando...")
                retry_log_path = self._start_llm_log(state, node.id, "review-retry")
                self.state_mgr.save()
                try:
                    result2 = delegate_with_feedback(
                        original_task=task_prompt,
                        feedback=validation.feedback or "",
                        project_root=self._work_dir,
                        allowed_paths=self._delegate_allowed_paths(allowed),
                        llm_engine=self._resolve_llm_engine(state),
                        llm_model=self._resolve_llm_model(state),
                        log_path=retry_log_path,
                        stream_prefix=self._stream_prefix(self._resolve_llm_engine(state)),
                    )
                finally:
                    self._clear_active_llm_log(state)
                state.metrics["llm_calls"] = state.metrics.get("llm_calls", 0) + 1
                validation = run_validators(node, self.project_root, state_dir=str(self.state_mgr.path.parent), work_dir=self._run_dir)
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
        self._advance_state(node.id, next_id, verdict)
        print(f"  REVIEW {verdict} → proximo: {next_id}")

    def _run_parallel_group(self, nodes: list[Node]):
        """Fan-out: delega nodes independentes em paralelo via worktrees."""
        print(f"\n  PARALLEL GROUP: {len(nodes)} tasks")
        for n in nodes:
            print(f"    → {n.id}: {n.title}")

        tasks = []
        for n in nodes:
            allowed = self._resolve_allowed_paths(n)
            tasks.append({
                "node_id": n.id,
                "task_prompt": build_task_prompt(n, {}),
                "allowed_paths": allowed,
                "outputs": n.outputs,
                "log_path": str(self._build_llm_log_path(n.id, "parallel")),
            })

        par = ParallelRunner(project_root=self._work_dir, max_slots=2)
        try:
            llm_engine = self._resolve_llm_engine(self.state_mgr.state)
            llm_model = self._resolve_llm_model(self.state_mgr.state)
            results = par.run_parallel(
                tasks,
                lambda **kwargs: delegate_to_llm(
                    llm_engine=llm_engine,
                    llm_model=llm_model,
                    stream_prefix=self._stream_prefix(llm_engine),
                    **kwargs,
                ),
            )
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
            validation = run_validators(node, self.project_root, state_dir=str(self.state_mgr.path.parent), work_dir=self._run_dir)
            self._print_validation(validation)
            if validation.passed:
                next_id = self.graph.resolve_next(node.id)
                self._advance_state(node.id, next_id)
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
        self._persist_llm_engine(state)
        self._sync_process_meta(state)
        if not state.pending_approval:
            print("Nenhuma aprovacao pendente.")
            return

        node_id = state.pending_approval
        node = self.graph.get_node(node_id)
        next_id = self.graph.resolve_next(node_id)
        self._advance_state(node_id, next_id)
        print(f"  APROVADO: {node_id} → proximo: {next_id}")

    def reject(self, reason: str, retry: bool = True):
        """
        Stakeholder rejeita artefato pendente.
        Se retry=True, reenvia ao LLM com feedback do motivo.
        """
        state = self.state_mgr.load()
        self._persist_llm_engine(state)
        self._sync_process_meta(state)
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

            allowed = self._resolve_allowed_paths(node)
            print(f"  Reenviando ao LLM com feedback da rejeicao...")

            # Desbloquear estado para retry
            state.node_status = "ready"
            state.pending_approval = None
            state.blocked_reason = None
            retry_log_path = self._start_llm_log(state, node.id, "stakeholder-retry")
            self.state_mgr.save()

            try:
                result = delegate_with_feedback(
                    original_task=original_prompt,
                    feedback=f"REJEITADO PELO STAKEHOLDER: {reason}",
                    project_root=self._work_dir,
                    allowed_paths=self._delegate_allowed_paths(allowed),
                    llm_engine=self._resolve_llm_engine(state),
                    llm_model=self._resolve_llm_model(state),
                    log_path=retry_log_path,
                    stream_prefix=self._stream_prefix(self._resolve_llm_engine(state)),
                )
            finally:
                self._clear_active_llm_log(state)
            state.metrics["llm_calls"] = state.metrics.get("llm_calls", 0) + 1

            if result.success:
                validation = run_validators(node, self.project_root, state_dir=str(self.state_mgr.path.parent), work_dir=self._run_dir)
                self._print_validation(validation)
                if validation.passed:
                    print(ui.awaiting_approval(auto=self._auto_approve))
                    self.state_mgr.set_pending_approval(node.id)
                    return
            # Se retry falhou, bloquear
            self.state_mgr.block(f"Retry apos rejeicao falhou: {reason}")
        else:
            self.state_mgr.block(f"Rejeitado pelo stakeholder: {reason}")

    def status(self, full: bool = False):
        """Mostra estado atual."""
        state = self.state_mgr.load()
        self._sync_process_meta(state)
        completed = set(state.completed_nodes)
        node_status = self.graph.get_status(completed)

        # Determinar sprint atual
        current_sprint = None
        if state.current_node:
            current_sprint = self.graph.sprint_of(state.current_node)

        print(ui.header(f"{state.process_id} v{state.version}"))
        print(ui.info(f"LLM engine: {state.llm_engine}"))
        print(ui.info(f"Node atual: {state.current_node}"))
        print(ui.info(f"Status: {state.node_status}"))
        if current_sprint:
            print(ui.info(f"Sprint: {current_sprint}"))
        print(ui.info(f"Progresso: {state.metrics['steps_completed']}/{state.metrics['steps_total']}"))
        if state.active_llm_log:
            print(ui.dim(f"LLM log ativo: {state.active_llm_log}"))
        elif state.last_llm_log:
            print(ui.dim(f"Último LLM log: {state.last_llm_log}"))
        if state.blocked_reason:
            print(ui.fail(f"BLOCKED: {state.blocked_reason}"))
        if state.pending_approval:
            print(ui.warn(f"AGUARDANDO APROVAÇÃO: {state.pending_approval}"))

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
            if item.passed:
                print(ui.validator_ok(item.detail))
            else:
                print(ui.validator_fail(item.detail))
