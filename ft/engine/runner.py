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

from ft.engine import paths
from ft.engine.graph import Node, ProcessGraph, load_graph
from ft.engine.state import StateManager
from ft.engine.delegate import delegate_to_llm, delegate_with_feedback

# Prefixo de blocked_reason que identifica pausa por rate limit da API.
# Falha de infra ≠ falha de conteúdo: não consome auto-fix e o node volta a
# 'ready' para retomada via ft continue.
RATE_LIMIT_MARKER = "[RATE_LIMIT]"
from ft.engine.validators import artifacts as val
from ft.engine.validators import gates
from ft.engine.validators import tests as test_val
from ft.engine.validators import code as code_val
from ft.engine.validators import review as review_val
from ft.engine.validators import check_paths as cp_val
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
# Helpers
# ---------------------------------------------------------------------------

def _last_log_activity(log_path: str) -> str | None:
    """Retorna a última linha de atividade significativa do log JSONL, com timestamp do arquivo."""
    import json as _json
    import os as _os

    p = Path(log_path)
    if not p.exists():
        return None

    mtime = p.stat().st_mtime
    ts = datetime.fromtimestamp(mtime).strftime("%H:%M:%S")

    # Ler as últimas linhas do arquivo (eficiente para arquivos grandes)
    try:
        with p.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            read_size = min(size, 8192)
            f.seek(-read_size, 2)
            tail = f.read().decode("utf-8", errors="replace")
    except Exception:
        return None

    activity: str | None = None
    for line in reversed(tail.splitlines()):
        line = line.strip()
        if not line:
            continue
        if line.startswith("{"):
            try:
                event = _json.loads(line)
                etype = event.get("type", "")
                if etype == "item.completed":
                    item = event.get("item", {})
                    itype = item.get("type", "")
                    if itype == "command_execution":
                        cmd = (item.get("command") or "").strip().replace("\n", " ")[:80]
                        activity = f"$ {cmd}"
                        break
                    if itype == "agent_message":
                        msg = (item.get("text") or "").strip().replace("\n", " ")[:80]
                        if msg:
                            activity = f"→ {msg}"
                            break
            except Exception:
                continue
        else:
            # Plain text (claude / outros engines)
            if not line.startswith("[") and len(line) > 5:
                activity = line[:80]
                break

    if activity:
        return f"[{ts}] {activity}"
    return f"[{ts}] (sem atividade recente legível)"


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
    "unique_screenshots": val.unique_screenshots,
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
    "gate_ui_vscode_layout": gates.gate_ui_vscode_layout,
    "read_artifact": val.read_artifact,
    "bash_passes": val.bash_passes,
    "command_succeeds": val.command_succeeds,
    "git_diff_not_empty": val.git_diff_not_empty,
    "paths_clean": cp_val.paths_clean,
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

    Em modo isolado (worktree/runs): LLM roda no work_dir e escreve lá.
    Validators devem usar work_dir como raiz.
    Fallback para project_root apenas se o arquivo não existir no work_dir
    mas existir no project_root.
    """
    if not work_dir or work_dir == project_root:
        return project_root
    # No modo isolado, work_dir é o worktree onde o LLM escreveu
    if path:
        work_path = Path(work_dir) / path
        proj_path = Path(project_root) / path
        if work_path.exists():
            return work_dir
        if proj_path.exists():
            return project_root
    # Sem path específico: preferir work_dir em modo isolado
    return work_dir


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
                                "lint_clean", "format_check",
                                "gate_frontend", "gate_delivery", "gate_smoke",
                                "gate_mvp", "gate_tdd_sequence", "gate_coverage_80",
                                "gate_e2e_all_pass", "gate_server_starts")

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
                # Gates verificam estrutura de código → usar work_dir quando disponível
                gate_root = work_dir or project_root
                if name == "gate_delivery":
                    passed, detail = fn(outputs=node.outputs, project_root=gate_root)
                else:
                    passed, detail = fn(project_root=gate_root)
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
                # Prefer work_dir (worktree) when available — LLM escreve lá
                root = work_dir or project_root
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
        self.process_path = str(process_path)
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
        self._max_auto_fix = self._environment.get("max_auto_fix", 2)
        self._bypass_human_gates = False  # setado por cmd_run via --bypass-human-gates
        # Run mode: isolated → LLM trabalha em runs/<N>/, continuous → trabalha na raiz
        self._run_mode = self._environment.get("run_mode", "isolated")
        self._work_dir = self._resolve_work_dir()
        # Tracking para log enriquecido
        self._node_start_times: dict[str, datetime] = {}   # node_id → início
        self._node_attempts: dict[str, int] = {}            # node_id → nº tentativas
        self._auto_fix_counts: dict[str, int] = {}          # node_id → auto-fix attempts
        self._auto_fix_prev_error: dict[str, str] = {}     # node_id → último erro (detecção de loop)

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
        if run_dir.parent.name in ("runs", "worktrees"):
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

    def _resolve_llm_engine(self, state: Any | None = None, node: Any | None = None) -> str:
        """Resolve o executor LLM efetivo. Prioridade: node > CLI override > state > env."""
        if node is not None and getattr(node, "llm_engine", None):
            return node.llm_engine
        if self._llm_engine_override:
            return self._llm_engine_override
        if state is not None and getattr(state, "llm_engine", None):
            return state.llm_engine
        env_engine = os.environ.get("FT_LLM_ENGINE", "").strip().lower()
        return env_engine or "claude"

    def _resolve_llm_model(self, state: Any | None = None, node: Any | None = None) -> str | None:
        """Resolve o modelo LLM efetivo. Prioridade: node > CLI override > state > env."""
        if node is not None and getattr(node, "llm_model", None):
            return node.llm_model
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

            # Resolver conditions especiais (file_exists:)
            if node.condition and node.condition.startswith("file_exists:"):
                check_path = node.condition.split(":", 1)[1]
                full_path = Path(self._work_dir) / check_path
                decision_state[node.condition] = "true" if full_path.exists() else "false"

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
        """Mantém process_id/version do estado alinhados ao grafo canônico carregado.

        Não sincroniza se o current_node do state não existe no grafo — indica YAML errado.
        """
        expected_id = self.graph.meta.get("id", state.process_id)
        expected_version = self.graph.meta.get("version", state.version)
        # Sanity check: se o state tem um node atual que não existe no grafo, o YAML
        # carregado é o errado — não sobrescrever process_id
        if state.current_node and state.current_node not in self.graph.nodes:
            return
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

        return ["project/", "docs/"]

    def _clear_no_pre_seed_outputs(self, node: Node) -> None:
        """Remove outputs herdados quando o node precisa gerar artefato fresco."""
        if not getattr(node, "no_pre_seed", False):
            return
        root = Path(self.project_root).resolve()
        for output in node.outputs:
            target = (root / output).resolve()
            try:
                target.relative_to(root)
            except ValueError:
                continue
            try:
                if target.is_file() or target.is_symlink():
                    target.unlink()
                elif target.is_dir():
                    import shutil as _shutil
                    _shutil.rmtree(target)
            except OSError:
                pass

    def _filter_no_pre_seed_docs(
        self,
        node: Node,
        docs: dict[str, str],
    ) -> dict[str, str]:
        """Remove do Hyper-mode os docs que são outputs frescos do node atual."""
        if not getattr(node, "no_pre_seed", False) or not docs:
            return docs
        excluded = {
            Path(output).name
            for output in node.outputs
            if Path(output).parts and Path(output).parts[0] == "docs"
        }
        if not excluded:
            return docs
        return {name: content for name, content in docs.items() if name not in excluded}

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

    def _detect_worktree(self) -> tuple[Path, Path, str] | None:
        """Detecta se estamos num worktree e retorna (work, original_root, branch) ou None."""
        import subprocess as _sp

        work = Path(self.project_root)
        git_file = work / ".git"
        if not git_file.exists() or git_file.is_dir():
            return None

        gitdir_line = git_file.read_text().strip()
        if not gitdir_line.startswith("gitdir:"):
            return None

        gitdir = Path(gitdir_line.split(":", 1)[1].strip())
        original_root = gitdir.parent.parent.parent
        if not (original_root / ".git").is_dir():
            return None

        branch = _sp.run(
            ["git", "branch", "--show-current"],
            cwd=work, capture_output=True, text=True,
        ).stdout.strip()

        return work, original_root, branch

    def merge_on_close(self, strategy: str, paths: list[str] | None = None) -> bool:
        """Merge artefatos do worktree de volta para o repo original.

        strategy:
          "full"      → git merge da branch inteira
          "docs"      → copia apenas docs/ e process/
          "selective"  → copia apenas os paths informados
          "none"      → nada
        paths: lista de paths para modo selective (ex: ["docs/", "project/backend/"])

        Retorna True se o merge foi concluído (ou intencionalmente não havia nada
        a fazer); False se falhou — o chamador NÃO deve destruir worktree/branch.
        """
        import shutil as _shutil
        import subprocess as _sp

        if strategy == "none":
            return True

        wt = self._detect_worktree()
        if not wt:
            # Cycle dir não é git worktree (diretório puro em ~/.ft/worktrees/):
            # merge por cópia — nunca retornar em silêncio.
            return self._merge_by_copy(strategy, paths)
        work, original_root, branch = wt

        if strategy == "full":
            if branch:
                result = _sp.run(
                    ["git", "merge", branch, "--no-edit"],
                    cwd=original_root, capture_output=True, text=True,
                )
                if result.returncode == 0:
                    print(ui.success(f"Merge: branch {branch} mergida em {original_root.name}"))
                    return True
                # git manda conflitos para o STDOUT; stderr costuma vir vazio
                reason = (result.stdout.strip() or result.stderr.strip())[:300]
                print(ui.fail(f"Merge: falha — {reason}"))
                merging = (original_root / ".git" / "MERGE_HEAD").exists()
                if merging:
                    print(ui.warn(
                        f"Merge em andamento com conflitos em {original_root}. "
                        f"Resolva-os e conclua com git commit, depois rode ft close de novo."
                    ))
                return False
            print(ui.fail("Merge: worktree sem branch — merge manual necessário"))
            return False

        # Resolver lista de paths a copiar
        if strategy == "docs":
            copy_paths = ["docs/", "process/"]
        elif strategy == "selective" and paths:
            copy_paths = paths
        else:
            return True

        count = 0
        for p in copy_paths:
            src = work / p
            dst = original_root / p
            if not src.exists():
                print(ui.dim(f"  Skip: {p} (não existe)"))
                continue
            if src.is_dir():
                dst.mkdir(parents=True, exist_ok=True)
                _shutil.copytree(src, dst, dirs_exist_ok=True)
                count += 1
            elif src.is_file():
                dst.parent.mkdir(parents=True, exist_ok=True)
                _shutil.copy2(src, dst)
                count += 1

        if count:
            print(ui.success(f"Merge: {count} item(ns) copiado(s) para {original_root.name}/"))
        return True

    def _merge_by_copy(self, strategy: str, paths: list[str] | None = None) -> bool:
        """Fallback do merge quando o cycle dir não é git worktree.

        full      → project/ vira <root>/project/ + docs do ciclo em <root>/docs/<cycle>/
        docs      → só docs do ciclo em <root>/docs/<cycle>/ (+ process/ se houver)
        selective → paths informados, copiados 1:1
        O PRD/process da raiz nunca são sobrescritos (regra do playbook).
        Retorna True se copiou (ou nada a fazer por design); False se falhou.
        """
        import shutil as _shutil

        from ft.engine import paths as _paths

        # project_root pode ter sido redirecionado para o próprio cycle dir
        # (descoberta de state) — o projeto original é o cwd de quem invocou.
        work = Path(self._work_dir).resolve()
        root = Path.cwd().resolve()
        if not _paths.is_worktree_path(work):
            print(ui.warn("Merge: nada a mergear — modo continuous (código já está na raiz)"))
            return True
        if root == work or _paths.is_worktree_path(root):
            print(ui.fail("Merge: rode o ft close a partir da raiz do projeto (cwd atual é o próprio ciclo)"))
            return False
        if not ((root / ".git").exists() or (root / "process").is_dir()):
            print(ui.fail(f"Merge: {root} não parece a raiz de um projeto ft — merge manual necessário"))
            return False
        cycle = work.name  # ex.: cycle-01
        ignore = _shutil.ignore_patterns(
            "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache", ".venv", "*.pyc"
        )
        copied: list[str] = []

        def _copy(src: Path, dst: Path, label: str) -> None:
            if not src.exists():
                print(ui.dim(f"  Skip: {label} (não existe no ciclo)"))
                return
            if src.is_dir():
                dst.mkdir(parents=True, exist_ok=True)
                _shutil.copytree(src, dst, dirs_exist_ok=True, ignore=ignore)
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                _shutil.copy2(src, dst)
            copied.append(label)

        if strategy == "selective" and paths:
            for p in paths:
                _copy(work / p, root / p, p)
        else:
            # docs do ciclo ficam versionados por ciclo — não clobberam a raiz
            _copy(work / "docs", root / "docs" / cycle, f"docs/ → docs/{cycle}/")
            log_file = work / f"{cycle}_log.md"
            _copy(log_file, root / "docs" / cycle / log_file.name, f"{cycle}_log.md")
            if strategy == "full":
                _copy(work / "project", root / "project", "project/")

        if copied:
            print(ui.success(f"Merge por cópia ({strategy}): {len(copied)} item(ns) → {root.name}/"))
            for c in copied:
                print(ui.dim(f"  ✓ {c}"))
            return True
        print(ui.warn("Merge por cópia: NENHUM artefato encontrado no ciclo — verifique manualmente"))
        return False

    # Compatibilidade: alias para código legado que ainda chama _merge_on_end
    def _merge_on_end(self) -> None:
        spec = self.graph.meta.get("merge_on_end", "none")
        if spec == "full":
            self.merge_on_close("full")
        elif isinstance(spec, list):
            self.merge_on_close("selective", spec)
        elif isinstance(spec, str) and spec not in ("none", ""):
            self.merge_on_close("selective", [f"{spec}/"])
        # Se não tem merge_on_end, nada a fazer (ft close vai perguntar)

    def _handle_on_fail(self, node: "Node", feedback: str) -> None:
        """Processa o evento on_fail de um node. Pausa para ft fix ou bloqueia."""
        on_fail = node.on_fail or {}
        goto = on_fail.get("goto")
        gate_msg = on_fail.get("human_gate", "Falha no node — corrija e tente novamente.")

        if not goto:
            self.state_mgr.block(f"on_fail sem goto definido: {feedback}")
            return

        # Validar que o goto existe no grafo
        if goto not in self.graph.nodes:
            self.state_mgr.block(f"on_fail.goto '{goto}' não encontrado no grafo")
            return

        state = self.state_mgr.state
        state.pending_fix = {"goto": goto, "feedback": feedback}
        state.node_status = "pending_fix"
        state.blocked_reason = None
        self.state_mgr.save()

        print(ui.fix_gate(gate_msg, feedback, goto))

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
            process_file=self.process_path,
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
                # Merge artefatos de volta para o repo original
                self._merge_on_end()
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
                description=node.description,
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
                if state.node_status in ("blocked", "awaiting_approval", "pending_fix"):
                    break
                continue

            # Exploration — sandbox livre do stakeholder
            if node.type == "exploration":
                self._run_exploration(node)
                if mode == "mvp":
                    self.explore_skip()
                    self._log_activity(node_id, node.title, "exploration", "BYPASSED",
                                       "exploração pulada (modo mvp/auto)", sprint=node_sprint)
                    state = self.state_mgr.load()
                    continue
                state = self.state_mgr.load()
                if state.node_status == "exploring":
                    break
                continue

            # Human gate — checkpoint humano obrigatório
            if node.type == "human_gate":
                self._run_human_gate(node)
                if mode == "step":
                    break
                state = self.state_mgr.load()
                if state.node_status == "awaiting_approval":
                    self._log_activity(node_id, node.title, "human_gate", "AWAITING_HUMAN",
                                       "aguardando aprovacao humana (ft approve)", sprint=node_sprint)
                    break
                self._log_activity(node_id, node.title, "human_gate", "BYPASSED",
                                   "bypassed (--bypass-human-gates)", sprint=node_sprint)
                continue

            # Gate — validacao pura, sem LLM
            if node.type == "gate":
                self._run_gate(node)
                if mode == "step":
                    break
                state = self.state_mgr.load()
                if state.node_status == "blocked":
                    blocked_reason = state.blocked_reason or "gate falhou"
                    if blocked_reason.startswith(RATE_LIMIT_MARKER):
                        self._pause_for_rate_limit(node, node_sprint)
                        break
                    fix_count = self._auto_fix_counts.get(node_id, 0)
                    if mode == "mvp" and fix_count < self._max_auto_fix:
                        self._auto_fix_counts[node_id] = fix_count + 1
                        fixed = self._run_auto_fix(node, blocked_reason)
                        state = self.state_mgr.load()
                        if fixed:
                            self._log_activity(node_id, node.title, "gate", "AUTO_FIXED",
                                               f"corrigido automaticamente (tentativa {fix_count + 1})", sprint=node_sprint)
                            continue
                        if (state.blocked_reason or "").startswith(RATE_LIMIT_MARKER):
                            # Rate limit durante o auto-fix: devolve a tentativa
                            self._auto_fix_counts[node_id] = fix_count
                            self._pause_for_rate_limit(node, node_sprint)
                            break
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
                blocked_reason = state.blocked_reason or "bloqueado"
                if blocked_reason.startswith(RATE_LIMIT_MARKER):
                    self._pause_for_rate_limit(node, node_sprint)
                    break
                fix_count = self._auto_fix_counts.get(node_id, 0)
                if mode == "mvp" and fix_count < self._max_auto_fix:
                    self._auto_fix_counts[node_id] = fix_count + 1
                    fixed = self._run_auto_fix(node, blocked_reason)
                    state = self.state_mgr.load()
                    if fixed:
                        self._log_activity(node_id, node.title, node.type, "AUTO_FIXED",
                                           f"corrigido automaticamente (tentativa {fix_count + 1})", sprint=node_sprint)
                        continue
                    if (state.blocked_reason or "").startswith(RATE_LIMIT_MARKER):
                        # Rate limit durante o auto-fix: devolve a tentativa
                        self._auto_fix_counts[node_id] = fix_count
                        self._pause_for_rate_limit(node, node_sprint)
                        break
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

        if mode == "step":
            state = self.state_mgr.load()
            if state.node_status not in ("blocked", "awaiting_approval", "done", "completed"):
                print(ui.dim("  → ft continue   para continuar o próximo step"))

    def _run_llm_step(self, node: Node):
        """Wrapper: garante env_teardown em qualquer saída (PASS, retry, block)."""
        try:
            return self._run_llm_step_inner(node)
        finally:
            if node.env_teardown:
                self._run_env_teardown(node)

    def _run_llm_step_inner(self, node: Node):
        """Delega ao LLM, valida resultado, avanca ou retenta."""
        state = self.state_mgr.state
        self._prepare_validator_snapshots(node)

        # Pre-seed check: se todos os outputs já existem e os validators passam,
        # pula delegação ao LLM — o artefato foi fornecido externamente (ex: --hipotese).
        # NÃO aplica a build nodes: um scaffold de passo anterior não conta como implementação.
        # NÃO aplica se node tiver no_pre_seed: true — node deve sempre rodar (ex: plano de voo).
        if node.outputs and node.type not in ("build",) and not self._validator_snapshot_specs(node) and not getattr(node, "no_pre_seed", False):
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

        self._clear_no_pre_seed_outputs(node)
        effective_engine = self._resolve_llm_engine(state, node=node)
        state_dict = {**state.__dict__, "_project_root": self.project_root}
        task_prompt = build_task_prompt(node, state_dict)
        opencode_deny_read_paths: list[str] = []
        opencode_restrict_tools = False
        opencode_steps: int | None = None

        # Injetar mensagem do último ft approve como contexto para o LLM
        approval_msg = self.state_mgr.state.last_approval_message
        if approval_msg:
            task_prompt = (
                f"MENSAGEM DO STAKEHOLDER (aprovação do gate anterior):\n{approval_msg}\n\n"
                f"Leve esta mensagem em conta ao executar sua tarefa.\n\n"
                f"{task_prompt}"
            )
            # Consumir — não passa para o próximo nó
            self.state_mgr.state.last_approval_message = None
            self.state_mgr.save()
            print(ui.info("Contexto: mensagem do stakeholder injetada no prompt"))

        # Hyper-mode: enriquecer prompt com docs existentes
        if node.type in ("discovery", "document", "retro"):
            existing = self._filter_no_pre_seed_docs(
                node,
                scan_existing_docs(self.project_root),
            )
            if existing:
                is_opencode = effective_engine == "opencode"
                if is_opencode:
                    opencode_deny_read_paths = [f"docs/{name}" for name in existing]
                    if node.type == "document":
                        opencode_restrict_tools = True
                        opencode_steps = 8
                task_prompt = hyper_mode_prompt(
                    existing,
                    task_prompt,
                    preview_lines=30 if is_opencode else 60,
                    allow_followup_reads=not is_opencode,
                )
                print(f"  Hyper-mode: {len(existing)} docs existentes carregados")

        # KB-mode: injetar lições de runs anteriores em nodes de build, refactor e retro
        if node.type in ("build", "refactor", "retro"):
            itype = state_dict.get("artifacts", {}).get("interface_type") or state_dict.get("interface_type")
            lessons = scan_kb_lessons(self._kb_path, interface_type=itype) if self._kb_path else []
            if lessons:
                task_prompt = kb_lessons_prompt(lessons, task_prompt)
                print(f"  KB-mode: lições de runs anteriores injetadas")

        # cycle_memory: continuidade cumulativa entre nodes (intra-ciclo)
        task_prompt = self._inject_cycle_memory(task_prompt)

        # Determinar paths permitidos
        allowed = self._resolve_allowed_paths(node)

        # env_setup: comandos determinísticos antes da delegação (não consome turns)
        if node.env_setup:
            if not self._run_env_setup(node):
                self.state_mgr.block(f"env_setup falhou no node {node.id}")
                return

        print(ui.info(f"Delegando ao LLM ({effective_engine})..."))
        state.node_status = "delegated"
        state.metrics["llm_calls"] = state.metrics.get("llm_calls", 0) + 1
        log_path = self._start_llm_log(state, node.id, "run")
        self.state_mgr.save()

        delegate_kwargs: dict = dict(
            task=task_prompt,
            project_root=self._work_dir,
            allowed_paths=allowed,
            llm_engine=effective_engine,
            llm_model=self._resolve_llm_model(state, node=node),
            log_path=log_path,
            stream_prefix=self._stream_prefix(effective_engine),
        )
        if opencode_deny_read_paths:
            delegate_kwargs["opencode_deny_read_paths"] = opencode_deny_read_paths
        if opencode_restrict_tools:
            delegate_kwargs["opencode_restrict_tools"] = True
        if opencode_steps is not None:
            delegate_kwargs["opencode_steps"] = opencode_steps
        if node.max_turns is not None:
            delegate_kwargs["max_turns"] = node.max_turns

        try:
            result = delegate_to_llm(**delegate_kwargs)
        finally:
            self._clear_active_llm_log(state)

        if not result.success:
            if getattr(result, "rate_limited", False):
                self.state_mgr.block(
                    f"{RATE_LIMIT_MARKER} API do LLM indisponível (rate limit persistiu "
                    f"após todo o backoff) no node {node.id}"
                )
                return
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
            self._record_node_summary(node, getattr(result, "output", None) or str(result))

            if node.requires_approval and not self._auto_approve:
                print(ui.awaiting_approval(auto=self._auto_approve))
                self.state_mgr.set_pending_approval(node.id)
                return

            next_id = self.graph.resolve_next(node.id)
            self._advance_state(node.id, next_id)
            print(ui.step_pass(next_id))
            return

        # Retry — com detecção de erro idêntico para early-BLOCKED
        if validation.retryable:
            previous_feedback = validation.feedback or ""
            for retry in range(1, self._max_node_retries + 1):
                current_feedback = validation.feedback or "validação falhou"
                print(ui.retry(retry, self._max_node_retries))
                print(ui.info(f"Corrigindo automaticamente: {current_feedback}"))

                # Se o erro é idêntico ao da tentativa anterior, parar cedo
                if retry > 1 and current_feedback == previous_feedback:
                    print(ui.fail("Erro idêntico ao da tentativa anterior — bloqueio estrutural detectado"))
                    break

                previous_feedback = current_feedback
                retry_log_path = self._start_llm_log(state, node.id, f"retry-{retry}")
                self.state_mgr.save()
                try:
                    result = delegate_with_feedback(
                        original_task=task_prompt,
                        feedback=current_feedback,
                        project_root=self._work_dir,
                        allowed_paths=self._delegate_allowed_paths(allowed),
                        llm_engine=self._resolve_llm_engine(state, node=node),
                        llm_model=self._resolve_llm_model(state, node=node),
                        log_path=retry_log_path,
                        stream_prefix=self._stream_prefix(self._resolve_llm_engine(state, node=node)),
                        opencode_deny_read_paths=opencode_deny_read_paths,
                        opencode_restrict_tools=opencode_restrict_tools,
                        opencode_steps=opencode_steps,
                    )
                finally:
                    self._clear_active_llm_log(state)
                state.metrics["llm_calls"] = state.metrics.get("llm_calls", 0) + 1

                if getattr(result, "rate_limited", False):
                    self.state_mgr.block(
                        f"{RATE_LIMIT_MARKER} API do LLM indisponível (rate limit persistiu "
                        f"após todo o backoff) no retry do node {node.id}"
                    )
                    return

                validation = run_validators(node, self.project_root, state_dir=str(self.state_mgr.path.parent), work_dir=self._run_dir)
                self._print_validation(validation)

                if validation.passed:
                    self._maybe_auto_commit(node)
                    self._record_node_summary(node, getattr(result, "output", None) or str(result))

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

    def _run_human_gate(self, node: Node) -> None:
        """Checkpoint humano obrigatório — pausa até ft approve ser chamado.

        Não executa validators nem LLM. Apenas aguarda decisão humana.
        Bypassa automaticamente se self._bypass_human_gates=True.
        """
        if self._bypass_human_gates:
            print(ui.info(f"Human gate BYPASSED: {node.title}"))
            next_id = self.graph.resolve_next(node.id)
            self._advance_state(node.id, next_id)
            self.state_mgr.save()
            return

        # env_setup no human_gate: sobe servidor etc. antes de pausar para o stakeholder
        env_ok = True
        if node.env_setup:
            env_ok = self._run_env_setup(node)

        # Mostrar URL se disponível (ler mesmo se env_setup falhou — pode ter sido escrito antes)
        serve_url_path = Path(self._work_dir) / ".serve_url"
        url: str | None = None
        if serve_url_path.exists():
            url = serve_url_path.read_text().strip() or None
        if not url and not env_ok:
            print(ui.warn("env_setup falhou e .serve_url não encontrado — servidor pode não estar rodando"))

        gate_work_dir = str(self.state_mgr.path.parent.parent)
        is_worktree = paths.is_worktree_path(gate_work_dir)
        abs_files = [str(Path(gate_work_dir) / o) for o in (node.outputs or [])] if is_worktree else None
        print(ui.human_gate_card(
            title=node.title,
            description=node.description,
            url=url,
            files=abs_files or None,
        ))
        self.state_mgr.set_pending_approval(node.id)

    def _pause_for_rate_limit(self, node: Node, node_sprint) -> None:
        """Pausa o run por rate limit da API sem penalizar o node.

        Rate limit é falha de infra, não de conteúdo: o node volta a 'ready'
        (nenhum auto-fix consumido) e o run para — 'ft continue --auto' retoma
        do mesmo node quando a API normalizar.
        """
        self.state_mgr.unblock()
        self._log_activity(node.id, node.title, node.type, "RATE_LIMITED",
                           "pausado por rate limit da API — auto-fix não consumido",
                           sprint=node_sprint)
        print(ui.fail("Rate limit da API persistiu após todo o backoff."))
        print(ui.info("Node preservado como 'ready' — rode 'ft continue --auto' quando a API normalizar."))

    def _run_auto_fix(self, node: Node, blocked_reason: str) -> bool:
        """Tenta corrigir automaticamente um node bloqueado (modo MVP).

        Delega ao LLM o motivo do bloqueio para que ele corrija os artefatos.
        Retorna True se os validators passarem após a correção.
        Detecta erro idêntico ao anterior e faz early-BLOCKED.
        """
        fix_count = self._auto_fix_counts.get(node.id, 0)

        # Detectar erro idêntico ao anterior — bloqueio estrutural
        prev_reason = self._auto_fix_prev_error.get(node.id)
        if prev_reason and prev_reason == blocked_reason:
            print(ui.fail("Auto-fix: erro idêntico ao da tentativa anterior — bloqueio estrutural"))
            return False
        self._auto_fix_prev_error[node.id] = blocked_reason

        state = self.state_mgr.load()
        print(ui.info(f"Auto-fix: aplicando correção automática (tentativa {fix_count + 1}/{self._max_auto_fix})"))
        print(ui.dim(f"  Motivo: {blocked_reason[:200]}"))

        # Incluir histórico de erros para evitar que o LLM repita a mesma abordagem
        history_block = ""
        if fix_count > 0 and prev_reason:
            history_block = (
                f"\n\nTENTATIVA ANTERIOR QUE NÃO RESOLVEU:\n"
                f"  - {prev_reason[:300]}\n"
                f"\nNÃO repita a mesma abordagem. Tente algo diferente.\n"
            )

        prompt = (
            f"O processo travou no node '{node.id}' ({node.title}).\n\n"
            f"ERRO:\n{blocked_reason}\n\n"
            f"{history_block}"
            f"Analise o erro, identifique a causa raiz e corrija os arquivos necessários. "
            f"Não altere arquivos de estado ou de processo. "
            f"Quando terminar, diga DONE."
        )

        allowed = self._resolve_allowed_paths(node)
        log_path = self._start_llm_log(state, node.id, f"auto-fix-{self._auto_fix_counts.get(node.id, 0) + 1}")
        # Desbloquear antes de chamar o LLM
        state.node_status = "ready"
        state.blocked_reason = None
        self.state_mgr.save()

        try:
            result = delegate_to_llm(
                task=prompt,
                project_root=self._work_dir,
                allowed_paths=allowed,
                llm_engine=self._resolve_llm_engine(state, node=node),
                llm_model=self._resolve_llm_model(state, node=node),
                log_path=log_path,
                stream_prefix=self._stream_prefix(self._resolve_llm_engine(state, node=node)),
            )
        finally:
            self._clear_active_llm_log(state)

        state = self.state_mgr.load()
        state.metrics["llm_calls"] = state.metrics.get("llm_calls", 0) + 1
        self.state_mgr.save()

        if not result.success:
            if getattr(result, "rate_limited", False):
                # Sinaliza pausa por infra — o caller devolve a tentativa de auto-fix
                self.state_mgr.block(
                    f"{RATE_LIMIT_MARKER} API do LLM indisponível (rate limit persistiu "
                    f"após todo o backoff) durante auto-fix do node {node.id}"
                )
                # Erro de infra não conta como "mesmo erro" para o detector de loop
                self._auto_fix_prev_error.pop(node.id, None)
                return False
            print(ui.fail("Auto-fix: LLM não conseguiu aplicar correção"))
            return False

        validation = run_validators(node, self.project_root, state_dir=str(self.state_mgr.path.parent), work_dir=self._run_dir)
        self._print_validation(validation)

        if validation.passed:
            print(ui.success("Auto-fix: correção aplicada — continuando"))
            next_id = self.graph.resolve_next(node.id)
            self._advance_state(node.id, next_id)
            return True

        print(ui.fail(f"Auto-fix: validators ainda falhando após correção"))
        self.state_mgr.block(f"Auto-fix insuficiente: {validation.feedback}")
        return False

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

    def _cycle_memory_path(self) -> Path:
        return self.state_mgr.path.parent / "cycle_memory.md"

    def _inject_cycle_memory(self, task_prompt: str) -> str:
        """Injeta a memoria cumulativa do ciclo no prompt do proximo node."""
        cm = self._cycle_memory_path()
        if not cm.exists():
            return task_prompt
        content = cm.read_text(errors="replace").strip()
        if not content:
            return task_prompt
        print(ui.dim("  cycle_memory: injetada no prompt"))
        return (
            "MEMORIA DO CICLO (resumo cumulativo dos nodes anteriores — leia antes de agir;\n"
            "'verificado' foi testado de fato, 'assumido' NAO foi):\n\n"
            f"{content}\n\n---\n\n{task_prompt}"
        )

    def _record_node_summary(self, node: Node, result_text: str) -> None:
        """Extrai o NODE_SUMMARY do output do worker e acumula em cycle_memory.md."""
        summary = ""
        text = result_text or ""
        idx = text.rfind("NODE_SUMMARY")
        if idx != -1:
            block = text[idx:]
            lines = []
            for line in block.splitlines()[1:]:
                stripped = line.strip()
                if stripped.startswith("DONE") or stripped.startswith("BLOCKED"):
                    break
                if stripped:
                    lines.append(stripped)
                if len(lines) >= 12:
                    break
            summary = "\n".join(lines)
        if not summary:
            # Fallback: primeiras linhas uteis do resultado
            useful = [l.strip() for l in text.splitlines() if l.strip() and not l.strip().startswith("DONE")]
            summary = "\n".join(f"- {l}" for l in useful[:3]) or "- (sem resumo emitido)"
        try:
            cm = self._cycle_memory_path()
            cm.parent.mkdir(parents=True, exist_ok=True)
            with cm.open("a", encoding="utf-8") as f:
                f.write(f"## {node.id} — {node.title}\n{summary}\n\n")
        except OSError as exc:
            print(ui.dim(f"  cycle_memory: falha ao gravar ({exc})"))

    def _run_env_teardown(self, node: Node) -> None:
        """Executa env_teardown ao final do node — best-effort, nunca bloqueia.

        Uso típico: derrubar servidor subido no env_setup para não deixar
        processo sobrevivente atendendo na porta do projeto.
        """
        print(ui.info(f"env_teardown: {len(node.env_teardown)} comando(s)"))
        for cmd in node.env_teardown:
            print(f"    $ {cmd}")
            try:
                proc = subprocess.Popen(
                    cmd, shell=True, cwd=self._work_dir,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                proc.wait(timeout=60)
            except Exception as exc:  # noqa: BLE001 — teardown nunca derruba o node
                print(ui.dim(f"    env_teardown falhou (ignorado): {exc}"))

    def _run_env_setup(self, node: Node) -> bool:
        """Executa comandos de env_setup antes da delegação ao LLM.

        Comandos rodam no work_dir (run dir no modo isolated).
        Se qualquer comando falhar, loga o erro e retorna False.
        """
        print(ui.info(f"env_setup: {len(node.env_setup)} comando(s)"))
        for cmd in node.env_setup:
            print(f"    $ {cmd}")
            # Usa arquivos temporários em vez de pipes para evitar hang quando o
            # comando inicia processos em background (&) — proc.wait() retorna
            # assim que o shell pai sai, independente dos filhos backgrounded.
            import tempfile
            with tempfile.TemporaryFile(mode="w+") as out_f, \
                 tempfile.TemporaryFile(mode="w+") as err_f:
                proc = subprocess.Popen(
                    cmd, shell=True, cwd=self._work_dir,
                    stdout=out_f, stderr=err_f, text=True,
                )
                try:
                    rc = proc.wait(timeout=300)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    rc = proc.wait()
                out_f.seek(0)
                err_f.seek(0)
                stdout = out_f.read()
                stderr = err_f.read()
            if rc != 0:
                err = stderr.strip() or stdout.strip()
                print(ui.fail(f"env_setup falhou: {cmd}"))
                print(f"    {err[:300]}")
                return False
            if stdout.strip():
                last_line = stdout.strip().splitlines()[-1]
                print(f"    → {last_line[:120]}")
        print(ui.info("env_setup concluído"))
        return True

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
        """Roda decision node — avalia condicao e segue branch.

        Suporta conditions especiais:
          - file_exists:<path>  → resolve para "true"/"false" baseado em filesystem
        """
        state = self.state_mgr.state
        state_dict = self._decision_state_dict(state)

        # Resolver conditions especiais antes de delegar ao graph
        if node.condition and node.condition.startswith("file_exists:"):
            check_path = node.condition.split(":", 1)[1]
            full_path = Path(self._work_dir) / check_path
            state_dict[node.condition] = "true" if full_path.exists() else "false"

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
            llm_engine=self._resolve_llm_engine(state, node=node),
            llm_model=self._resolve_llm_model(state, node=node),
            log_path=review_log_path,
            stream_prefix=self._stream_prefix(self._resolve_llm_engine(state, node=node)),
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
            elif getattr(result, "rate_limited", False):
                self.state_mgr.block(
                    f"{RATE_LIMIT_MARKER} API do LLM indisponível (rate limit persistiu "
                    f"após todo o backoff) no review do node {node.id}"
                )
                return
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
                print(f"  REVIEW: validadores falharam — {validation.feedback or 'sem detalhes'} — retentando...")
                retry_log_path = self._start_llm_log(state, node.id, "review-retry")
                self.state_mgr.save()
                try:
                    result2 = delegate_with_feedback(
                        original_task=task_prompt,
                        feedback=validation.feedback or "",
                        project_root=self._work_dir,
                        allowed_paths=self._delegate_allowed_paths(allowed),
                        llm_engine=self._resolve_llm_engine(state, node=node),
                        llm_model=self._resolve_llm_model(state, node=node),
                        log_path=retry_log_path,
                        stream_prefix=self._stream_prefix(self._resolve_llm_engine(state, node=node)),
                    )
                finally:
                    self._clear_active_llm_log(state)
                state.metrics["llm_calls"] = state.metrics.get("llm_calls", 0) + 1
                validation = run_validators(node, self.project_root, state_dir=str(self.state_mgr.path.parent), work_dir=self._run_dir)
                self._print_validation(validation)

            if not validation.passed:
                feedback = validation.feedback or "validadores falharam"
                if node.on_fail:
                    self._handle_on_fail(node, feedback)
                else:
                    self.state_mgr.block(f"Review: validadores falharam: {feedback}")
                return

        # Ler relatorio e verificar veredicto
        review_output = ""
        for output_path in node.outputs:
            full = Path(self.project_root) / output_path
            if full.exists() and full.is_file():
                review_output = full.read_text()
                break

        # Veredicto deterministico via parse do relatorio
        output_upper = review_output.upper()
        if "REJECTED" in output_upper:
            # Extrair motivos da rejeição para contexto
            lines = [l.strip() for l in review_output.splitlines() if l.strip()]
            reason_lines = []
            capture = False
            for line in lines:
                if "REJECTED" in line.upper():
                    capture = True
                if capture:
                    reason_lines.append(line)
                    if len(reason_lines) >= 10:
                        break
            reason = "\n".join(reason_lines)
            print(ui.fail(f"REVIEW REJECTED"))
            print(ui.dim(f"  Motivo: {reason[:300]}"))

            # Se tem on_fail com goto, delegar correção ao LLM com contexto completo
            if node.on_fail and node.on_fail.get("goto"):
                goto_id = node.on_fail["goto"]
                goto_node = self.graph.get_node(goto_id)
                if goto_node and goto_node.executor.startswith("llm"):
                    print(ui.info(f"Delegando correção ao LLM ({goto_id}) com contexto da rejeição..."))

                    original_prompt = build_task_prompt(goto_node, {})
                    fix_prompt = (
                        f"{original_prompt}\n\n"
                        f"─── CONTEXTO: REVIEW REJEITOU O RESULTADO ANTERIOR ───\n"
                        f"O expert review encontrou os seguintes problemas:\n\n"
                        f"{reason}\n\n"
                        f"Relatório completo em: {node.outputs[0] if node.outputs else 'N/A'}\n\n"
                        f"Corrija TODOS os problemas listados acima. "
                        f"Quando terminar, diga DONE."
                    )

                    allowed = self._resolve_allowed_paths(goto_node)
                    fix_log = self._start_llm_log(state, goto_id, "review-fix")
                    self.state_mgr.save()

                    try:
                        fix_result = delegate_to_llm(
                            task=fix_prompt,
                            project_root=self._work_dir,
                            allowed_paths=self._delegate_allowed_paths(allowed),
                            llm_engine=self._resolve_llm_engine(state, node=goto_node),
                            llm_model=self._resolve_llm_model(state, node=goto_node),
                            log_path=fix_log,
                            stream_prefix=self._stream_prefix(self._resolve_llm_engine(state, node=goto_node)),
                        )
                    finally:
                        self._clear_active_llm_log(state)
                    state.metrics["llm_calls"] = state.metrics.get("llm_calls", 0) + 1

                    if fix_result.success:
                        # Validar o goto_node
                        fix_validation = run_validators(goto_node, self.project_root,
                                                        state_dir=str(self.state_mgr.path.parent),
                                                        work_dir=self._run_dir)
                        self._print_validation(fix_validation)
                        if fix_validation.passed:
                            print(ui.success("Correção aplicada — re-executando review"))
                            # Re-rodar o review (recursão controlada — 1 nível)
                            self._run_review(node)
                            return

                    # Fix falhou — bloqueia com contexto
                    self.state_mgr.block(
                        f"Review REJECTED e auto-fix falhou.\n"
                        f"Motivo da rejeição:\n{reason[:500]}"
                    )
                    return

            # Sem on_fail — bloqueia com o motivo
            self.state_mgr.block(f"Expert Review REJECTED:\n{reason[:500]}")
            return

        # APPROVED ou APPROVED WITH NOTES
        verdict = "APPROVED WITH NOTES" if "WITH NOTES" in output_upper else "APPROVED"
        next_id = self.graph.resolve_next(node.id)
        self._advance_state(node.id, next_id, verdict)
        print(f"  REVIEW {verdict} → proximo: {next_id}")

    def _run_exploration(self, node: "Node") -> None:
        """Pausa o ciclo em modo exploração — aguarda ft explore ou ft explore --finish/--skip."""
        state = self.state_mgr.state
        count = len(state.exploration_log)
        print(ui.exploration_start(node.title, count))
        state.node_status = "exploring"
        state.current_node = node.id
        self.state_mgr.save()

    def explore_request(self, request: str) -> None:
        """Executa um pedido livre do stakeholder no worktree atual."""
        state = self.state_mgr.load()
        if state.node_status != "exploring":
            print(ui.warn("Não há sessão de exploração ativa."))
            return

        import time as _time
        ts = _time.strftime("%H:%M")
        print(ui.exploration_item(len(state.exploration_log) + 1, request))

        allowed = self._resolve_allowed_paths(self.graph.nodes.get(state.current_node))
        log_path = str(self._llm_log_dir() / f"exploration_{len(state.exploration_log) + 1:02d}.log")

        from ft.engine.delegate import delegate_to_llm
        result = delegate_to_llm(
            task=(
                f"MODO EXPLORAÇÃO — pedido do stakeholder:\n\n{request}\n\n"
                f"Implemente a mudança pedida. Diga DONE e liste arquivos alterados. "
                f"Diga BLOCKED se não conseguir."
            ),
            project_root=self._work_dir,
            allowed_paths=self._delegate_allowed_paths(allowed),
            llm_engine=self._resolve_llm_engine(state),
            llm_model=self._resolve_llm_model(state),
            log_path=log_path,
        )

        summary = "DONE" if result.success else f"BLOCKED: {result.output[:120]}"
        state.exploration_log.append(f"[{ts}] {request} → {summary}")
        self.state_mgr.save()

        if result.success:
            print(ui.success(f"Exploração aplicada"))
        else:
            print(ui.fail(f"LLM não conseguiu: {result.output[:200]}"))

        # Mostrar menu novamente
        count = len(state.exploration_log)
        print(ui.exploration_start(self.graph.nodes[state.current_node].title, count))

    def explore_finish(self) -> None:
        """Gera relatório de descobertas e avança o nó de exploração."""
        state = self.state_mgr.load()
        if state.node_status != "exploring":
            print(ui.warn("Não há sessão de exploração ativa."))
            return

        node = self.graph.nodes.get(state.current_node)
        if not node:
            return

        log = state.exploration_log
        if not log:
            print(ui.info("Nenhum pedido registrado — encerrando exploração sem relatório."))
        else:
            # Gera exploration-report.md via LLM
            log_text = "\n".join(f"{i+1}. {entry}" for i, entry in enumerate(log))
            allowed = self._resolve_allowed_paths(node)
            from ft.engine.delegate import delegate_to_llm
            report_result = delegate_to_llm(
                task=(
                    f"Gere docs/exploration-report.md com o relatório da sessão de exploração.\n\n"
                    f"Pedidos realizados:\n{log_text}\n\n"
                    f"O relatório deve ter:\n"
                    f"## Sessão de Exploração\n"
                    f"Data, total de pedidos.\n\n"
                    f"## Pedidos Realizados\n"
                    f"Lista numerada com cada pedido e resultado.\n\n"
                    f"## Descobertas\n"
                    f"O que funcionou bem, o que foi descartado, observações.\n\n"
                    f"## Sugestões para o Próximo Ciclo\n"
                    f"Itens que o stakeholder pode querer levar adiante (em aberto — stakeholder decide).\n\n"
                    f"Diga DONE ao terminar."
                ),
                project_root=self._work_dir,
                allowed_paths=self._delegate_allowed_paths(allowed),
                llm_engine=self._resolve_llm_engine(state),
                llm_model=self._resolve_llm_model(state),
                log_path=str(self._llm_log_dir() / "exploration_report.log"),
            )
            if report_result.success:
                report_path = Path(self._work_dir) / "docs" / "exploration-report.md"
                print(ui.success(f"Relatório de exploração gerado: {report_path}"))
            else:
                print(ui.warn("Não foi possível gerar o relatório — avançando sem ele."))

        state.exploration_log = []
        next_id = self.graph.resolve_next(node.id)
        self._advance_state(node.id, next_id, "EXPLORED")

    def explore_skip(self) -> None:
        """Pula o nó de exploração (opcional)."""
        state = self.state_mgr.load()
        if state.node_status != "exploring":
            print(ui.warn("Não há sessão de exploração ativa."))
            return
        node = self.graph.nodes.get(state.current_node)
        if not node or not node.optional:
            print(ui.warn("Este nó não é opcional."))
            return
        next_id = self.graph.resolve_next(node.id)
        self._advance_state(node.id, next_id, "SKIPPED")
        print(ui.info(f"Exploração pulada → {next_id}"))

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

    def approve(self, message: str | None = None):
        """Stakeholder aprova artefato pendente.

        message: nota opcional registrada no log (ex: 'Revisado e aprovado por João em 2026-04-07').
        """
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
        log_msg = f"APROVADO: {node_id} → {next_id}"
        if message:
            log_msg += f" | nota: {message}"
        print(f"  {log_msg}")
        self._log_activity(node_id, node.title, node.type, "APPROVED",
                           message or "aprovado pelo stakeholder", sprint=node.sprint)
        # Guardar mensagem para o próximo nó LLM injetar como contexto
        if message:
            state.last_approval_message = message
            self.state_mgr.save()
        print(ui.dim("  → ft continue   para prosseguir"))

    def apply_fix(self, instruction: str) -> bool:
        """
        Executa o on_fail.goto: volta ao node alvo, injeta instrução, limpa pending_fix.
        Retorna True se havia pending_fix, False se não.
        """
        state = self.state_mgr.load()
        pending = state.pending_fix
        if not pending:
            return False

        goto = pending.get("goto")
        if not goto or goto not in self.graph.nodes:
            print(f"  Erro: on_fail.goto '{goto}' inválido.")
            return False

        # Ordem canônica para saber quais nodes descartar
        ordered = [n.id for n in self.graph.nodes.values()]
        try:
            target_idx = ordered.index(goto)
        except ValueError:
            print(f"  Erro: node '{goto}' não encontrado na ordem do grafo.")
            return False

        # Volta: remove goto e tudo posterior de completed_nodes
        state.completed_nodes = [
            n for n in state.completed_nodes
            if n in ordered and ordered.index(n) < target_idx
        ]
        state.current_node = goto
        state.node_status = "running"
        state.blocked_reason = None
        state.pending_fix = None
        state.last_approval_message = (
            f"CORREÇÃO SOLICITADA — retornando de on_fail:\n{instruction}\n\n"
            f"Feedback original:\n{pending.get('feedback', '')}"
        )
        state.metrics["steps_completed"] = len(state.completed_nodes)
        self.state_mgr.save()
        print(ui.info(f"↩ Voltando para {goto} com instrução injetada"))
        return True

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

        # Encontrar o node LLM que deve receber o feedback:
        # - Se o node rejeitado é LLM, usa ele mesmo
        # - Se tem reject_next, vai direto para aquele node
        # - Se é human_gate, encontra o predecessor LLM
        retry_node = node
        if node.reject_next:
            retry_node = self.graph.get_node(node.reject_next)
        elif not node.executor.startswith("llm"):
            # Buscar predecessor: o node cujo .next == node_id
            for other_id, other_node in self.graph.nodes.items():
                if other_node.next == node_id:
                    retry_node = other_node
                    break
                if other_node.branches:
                    for target in other_node.branches.values():
                        if target == node_id:
                            retry_node = other_node
                            break

        if retry and retry_node.executor.startswith("llm"):
            # Reenviar ao LLM com feedback da rejeicao
            from ft.engine.delegate import delegate_with_feedback
            original_prompt = build_task_prompt(retry_node, {})
            retry_prompt = build_rejection_prompt(original_prompt, reason)

            allowed = self._resolve_allowed_paths(retry_node)
            print(f"  Reenviando ao LLM ({retry_node.id}) com feedback da rejeicao...")

            # Rollback: remover retry_node e gate da lista de completados
            for nid in (retry_node.id, node_id):
                if nid in state.completed_nodes:
                    state.completed_nodes.remove(nid)
                    state.metrics["steps_completed"] = max(0, state.metrics.get("steps_completed", 1) - 1)

            # Desbloquear estado para retry — posicionar no node LLM
            state.current_node = retry_node.id
            state.node_status = "ready"
            state.pending_approval = None
            state.blocked_reason = None
            retry_log_path = self._start_llm_log(state, retry_node.id, "stakeholder-retry")
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
                validation = run_validators(retry_node, self.project_root, state_dir=str(self.state_mgr.path.parent), work_dir=self._run_dir)
                self._print_validation(validation)
                if validation.passed:
                    # Marcar retry_node como concluído e avançar ao gate
                    state.completed_nodes.append(retry_node.id)
                    state.metrics["steps_completed"] = state.metrics.get("steps_completed", 0) + 1
                    state.current_node = node_id
                    self.state_mgr.save()

                    # Resumo pós-fix: o que o LLM fez
                    print()
                    print(ui.header("Correção aplicada"))
                    summary_lines = [
                        l.strip() for l in (result.output or "").splitlines()
                        if l.strip() and not l.strip().startswith(("[", "⟳", "#"))
                    ][-5:]
                    if summary_lines:
                        for sl in summary_lines:
                            print(f"  {sl[:120]}")
                    changed = result.files_modified or []
                    if changed:
                        print(f"  Arquivos modificados: {', '.join(changed[:5])}")
                    print()

                    # Rodar env_setup do human_gate (sobe servidor) e mostrar card com URL
                    self._run_human_gate(node)
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
        steps_done = state.metrics.get("steps_completed", 0)
        steps_total = state.metrics.get("steps_total", 0)
        current_step = steps_done + 1 if state.node_status not in ("done", "completed") else steps_done
        print(ui.info(f"Progresso: {current_step}/{steps_total} (passo atual)"))
        # Mostrar URL se node atual é human_gate
        current_node_obj = self.graph.nodes.get(state.current_node) if state.current_node else None
        if current_node_obj and current_node_obj.type == "human_gate":
            serve_url_file = Path(self._work_dir) / ".serve_url"
            if serve_url_file.exists():
                print(ui.info(f"URL: {serve_url_file.read_text().strip()}"))
            print(ui.warn(f"HUMAN GATE: {current_node_obj.title}"))
            print(ui.dim("  → ft continue   para entrar no gate"))
        if state.active_llm_log:
            print(ui.dim(f"LLM log ativo: {state.active_llm_log}"))
            last_activity = _last_log_activity(state.active_llm_log)
            if last_activity:
                print(ui.dim(f"  Última atividade: {last_activity}"))
        elif state.last_llm_log:
            print(ui.dim(f"Último LLM log: {state.last_llm_log}"))
        if state.blocked_reason:
            print(ui.fail(f"BLOCKED: {state.blocked_reason}"))
        if state.pending_fix:
            pf = state.pending_fix
            goto = pf.get("goto", "?")
            feedback = pf.get("feedback", "")
            node_obj = self.graph.nodes.get(state.current_node) if state.current_node else None
            gate_msg = (node_obj.on_fail or {}).get("human_gate", "Falha — correção necessária.") if node_obj else "Falha — correção necessária."
            print(ui.fix_gate(gate_msg, feedback, goto))
        if state.pending_approval:
            pending_node = self.graph.nodes.get(state.pending_approval)
            if pending_node and pending_node.type == "human_gate":
                serve_url_file = Path(self._work_dir) / ".serve_url"
                url: str | None = None
                if serve_url_file.exists():
                    url = serve_url_file.read_text().strip() or None
                _gate_wt = str(self.state_mgr.path.parent.parent)
                _is_wt = paths.is_worktree_path(self.state_mgr.path)
                _abs_files = [str(Path(_gate_wt) / o) for o in (pending_node.outputs or [])] if _is_wt else None
                print(ui.human_gate_card(
                    title=pending_node.title,
                    description=pending_node.description,
                    url=url,
                    files=_abs_files or None,
                ))
            else:
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

    def status_report(self):
        """Relatório de tempo e tokens por node do ciclo atual."""
        import json as _json
        from datetime import datetime as _dt

        logs_dir = self._llm_log_dir()
        if not logs_dir.is_dir():
            print(ui.warn("Nenhum log LLM encontrado para o ciclo atual"))
            return

        files = sorted(logs_dir.glob("*.jsonl"), key=lambda f: f.stat().st_mtime)
        if not files:
            print(ui.warn("Nenhum log LLM encontrado"))
            return

        rows = []
        for f in files:
            parts = f.stem.split("__")
            if len(parts) < 3:
                continue
            ts_str, node_id, kind = parts[0], parts[1], parts[2]
            try:
                start = _dt.strptime(ts_str, "%Y%m%d-%H%M%S")
            except ValueError:
                continue
            end = _dt.fromtimestamp(f.stat().st_mtime)
            dur = max(0, int((end - start).total_seconds()))

            input_tok = output_tok = turns = 0
            for line in f.read_text().splitlines():
                line = line.strip()
                if not line or not line.startswith("{"):
                    continue
                try:
                    d = _json.loads(line)
                except Exception:
                    continue
                turns += 1
                usage = d.get("usage") or {}
                if isinstance(usage, dict):
                    input_tok += usage.get("input_tokens", 0) or usage.get("prompt_tokens", 0)
                    output_tok += usage.get("output_tokens", 0) or usage.get("completion_tokens", 0)

            rows.append((node_id, kind, dur, turns, input_tok, output_tok))

        if not rows:
            print(ui.warn("Logs vazios"))
            return

        state = self.state_mgr.load()
        print(ui.header(f"Relatório — {state.process_id} / {state.llm_engine}"))
        print()

        col = 44
        print(f"  {'Node':<{col}} {'Tempo':>7}  {'Turns':>5}  {'In tok':>9}  {'Out tok':>8}")
        print(f"  {'-'*col} {'-'*7}  {'-'*5}  {'-'*9}  {'-'*8}")

        total_dur = total_in = total_out = total_turns = 0
        for node_id, kind, dur, turns, in_tok, out_tok in rows:
            m, s = dur // 60, dur % 60
            tag = f" [{kind}]" if kind != "run" else ""
            label = f"{node_id}{tag}"
            print(f"  {label:<{col}} {m:>3}m{s:02d}s  {turns:>5}  {in_tok:>9,}  {out_tok:>8,}")
            total_dur += dur
            total_in += in_tok
            total_out += out_tok
            total_turns += turns

        print(f"  {'-'*col} {'-'*7}  {'-'*5}  {'-'*9}  {'-'*8}")
        td_m, td_s = total_dur // 60, total_dur % 60
        print(f"  {'TOTAL':<{col}} {td_m:>3}m{td_s:02d}s  {total_turns:>5}  {total_in:>9,}  {total_out:>8,}")
        print()
        steps_done = state.metrics.get("steps_completed", 0)
        steps_total = state.metrics.get("steps_total", 0)
        print(f"  Progresso : {steps_done}/{steps_total} nodes")
        print(f"  Tempo wall: {td_m}m{td_s:02d}s  ({total_dur/3600:.1f}h)")
        print(f"  Tokens    : {total_in:,} in  /  {total_out:,} out  /  {total_in+total_out:,} total")

    @staticmethod
    def _print_validation(v: ValidationResult):
        for item in v.items:
            if item.passed:
                print(ui.validator_ok(item.detail))
            else:
                print(ui.validator_fail(item.detail))
