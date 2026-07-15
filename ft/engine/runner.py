"""
Step Runner — loop principal do motor deterministico.
resolve_next() → delegate() → validate() → advance()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import math
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any


from ft.engine import paths
from ft.engine.graph import Node, load_graph
from ft.engine.context_profiles import compose_context_profile
from ft.engine.state import StateManager
from ft.engine.delegate import (
    DelegateResult,
    delegate_to_llm,
    delegate_with_feedback,
    delegate_opencode_exact_file_raw,
)

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
from ft.engine.git_ops import (
    auto_commit,
    commit_knowledge,
    git_command_prefix,
    verify_hooks_from_process_meta,
)
from ft.engine.hooks import load_environment, run_hooks, hooks_all_passed
from ft.engine.llm_usage import format_llm_usage_lines, summarize_llm_usage
from ft.engine.layout import (
    archive_cycle_artifacts,
    is_cycle_artifact,
    process_digest,
    validate_local_process_path,
)
from ft.engine.llm_defaults import LLMSelection, LiveLLMSettings, normalize_llm_effort
from ft.engine.trace import TraceRecorder, TraceSpan, build_run_report
from ft.engine import ui
from ft.providers.opencode_fallbacks import (
    OpenCodeDomainFallbackMixin,
    _opencode_compact_bundle_prompt,
    _opencode_compact_bundles_enabled,
    _opencode_deny_edit_tools_enabled,
    _opencode_deterministic_fallbacks_enabled,
)
from ft.engine.parallel import ParallelRunner
from ft.engine.stakeholder import (
    DEFAULT_HYPER_MODE_FULL_MAX_LINES,
    scan_existing_docs, hyper_mode_prompt,
    scan_kb_lessons, kb_lessons_prompt,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REVIEW_REJECT_VERDICTS = {"REJECTED", "BLOCKED", "INCOMPLETE", "INCOMPLETO", "ITERATE"}
_REVIEW_APPROVE_VERDICTS = {"APPROVED", "APPROVED WITH NOTES"}
_REVIEW_VERDICTS = _REVIEW_APPROVE_VERDICTS | _REVIEW_REJECT_VERDICTS
_CYCLE_OBJECTIVE_MAX_CHARS = 160
_GENERIC_OBJECTIVE_HEADINGS = {
    "bug",
    "demanda",
    "descrição",
    "feature",
    "feature request",
    "objetivo",
    "objetivo do ciclo",
    "pedido",
    "request",
    "solicitação",
    "tweak",
}


def _brief_cycle_objective(raw: str) -> str | None:
    """Converte a demanda original em uma linha estável para ``ft status``."""
    paragraph: list[str] = []

    def shorten(value: str) -> str:
        if len(value) <= _CYCLE_OBJECTIVE_MAX_CHARS:
            return value
        limit = _CYCLE_OBJECTIVE_MAX_CHARS - 1
        shortened = value[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-")
        if len(shortened) < limit // 2:
            shortened = value[:limit].rstrip(" ,;:-")
        return f"{shortened}…"

    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line:
            if paragraph:
                break
            continue
        is_heading = bool(re.match(r"^#{1,6}(?:\s|$)", line))
        line = re.sub(r"^(?:#{1,6}|>|[-*+]|\d+[.)])\s*", "", line)
        line = re.sub(r"^\[[ xX]\]\s*", "", line)
        line = line.replace("**", "").replace("__", "").replace("`", "")
        line = re.sub(r"\s+", " ", line).strip()
        if not line:
            continue
        if line.casefold().rstrip(":") in _GENERIC_OBJECTIVE_HEADINGS:
            if paragraph:
                break
            continue
        if is_heading:
            return shorten(line)
        paragraph.append(line)

    objective = " ".join(paragraph).strip()
    return shorten(objective) if objective else None


class LLMEpisodeBudgetExceeded(RuntimeError):
    """Hard stop preservando o diff quando o orçamento cumulativo termina."""


def _normalize_review_line(line: str) -> str:
    clean = line.strip()
    clean = re.sub(r"^[#>\-\s]+", "", clean)
    clean = clean.replace("**", "").replace("__", "").replace("`", "")
    return clean.strip()


def _canonical_review_verdict(value: str) -> str | None:
    upper = re.sub(r"\s+", " ", value.strip().upper())
    if upper.startswith("APPROVED WITH NOTES"):
        return "APPROVED WITH NOTES"
    for verdict in sorted(_REVIEW_VERDICTS, key=len, reverse=True):
        if upper == verdict or upper.startswith(f"{verdict} "):
            return verdict
    return None


def _line_review_verdict(line: str) -> str | None:
    clean = _normalize_review_line(line)
    if not clean:
        return None

    labeled = re.match(
        r"(?i)^(resultado|result|status|parecer|veredicto|verdict)\s*[:=-]\s*(.+)$",
        clean,
    )
    if labeled:
        return _canonical_review_verdict(labeled.group(2))

    return _canonical_review_verdict(clean)


def _parse_review_verdict(review_output: str) -> str:
    """Parse explicit review verdicts without treating body text as a rejection."""
    for line in review_output.splitlines():
        verdict = _line_review_verdict(line)
        if verdict:
            return verdict

    upper = review_output.upper()
    if re.search(r"\bAPPROVED\s+WITH\s+NOTES\b", upper):
        return "APPROVED WITH NOTES"
    if re.search(r"\bAPPROVED\b", upper):
        return "APPROVED"
    return "APPROVED"


def _extract_review_rejection_reason(review_output: str, verdict: str) -> str:
    lines = [line.strip() for line in review_output.splitlines() if line.strip()]
    reason_lines: list[str] = []
    capture = False
    for line in lines:
        if _line_review_verdict(line) == verdict:
            capture = True
        if capture:
            reason_lines.append(line)
            if len(reason_lines) >= 10:
                break
    if not reason_lines:
        reason_lines = lines[:10]
    return "\n".join(reason_lines)


def _review_recovery_feedback(feedback: str) -> str:
    return (
        f"{feedback}\n\n"
        "RECUPERACAO DE REVIEW APOS INTERRUPCAO/MAX_TURNS:\n"
        "- Nao reinicie a analise do zero e nao persiga evidencia perfeita.\n"
        "- NAO abra browser, NAO use chrome-devtools, NAO suba servidor, NAO rode curl/npm/testes.\n"
        "- Use somente os screenshots/logs/artefatos ja existentes no diretorio de trabalho.\n"
        "- Sua tarefa agora e escrever/atualizar somente o relatorio canonico do review.\n"
        "- Se existe screenshot, log ou nome de arquivo indicando falha bloqueante (overflow, blank, erro), registre imediatamente `Resultado: REJECTED` e pare.\n"
        "- Se os criterios obrigatorios estiverem cobertos, use `Resultado: APPROVED` ou `Resultado: APPROVED WITH NOTES`.\n"
        "- Remova referencias a screenshots que nao existem e garanta que o relatorio seja mais recente que o frontend atual."
    )


def _last_log_activity(log_path: str) -> str | None:
    """Retorna a última linha de atividade significativa do log JSONL, com timestamp do arquivo."""
    import json as _json

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
    duration_ms: int | None = None


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
    "document_quality": val.document_quality,
    "api_contract_complete": val.api_contract_complete,
    "relative_dates_only": val.relative_dates_only,
    "min_user_stories": val.min_user_stories,
    "sections_unchanged": val.sections_unchanged,
    "demand_coverage": val.demand_coverage,
    "prd_coverage": val.prd_coverage,
    "project_backlog_valid": val.project_backlog_valid,
    "task_list_references_backlog": val.task_list_references_backlog,
    "backlog_pending_decisions": val.backlog_pending_decisions,
    "backlog_referenced_decisions": val.backlog_referenced_decisions,
    "features_catalog_valid": val.features_catalog_valid,
    "implemented_backlog_covered_by_features": val.implemented_backlog_covered_by_features,
    "process_improvements_classified": val.process_improvements_classified,
    "ui_criteria_ids": val.ui_criteria_ids,
    "ui_criteria_coverage": val.ui_criteria_coverage,
    "visual_p0_acceptance": val.visual_p0_acceptance,
    "unique_screenshots": val.unique_screenshots,
    "tests_pass": val.tests_pass,
    "tests_fail": val.tests_fail,
    "pytest_red_quality": val.pytest_red_quality,
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


def run_validators(
    node: Node,
    project_root: str,
    state_dir: str | None = None,
    work_dir: str | None = None,
    *,
    resume: bool = False,
    trace: TraceRecorder | None = None,
    parent_span_id: str | None = None,
    attempt_id: str | None = None,
) -> ValidationResult:
    """Roda todos os validadores de um node. Retorna resultado agregado.

    ``command_succeeds`` pode declarar ``resume_command`` junto de ``command``.
    O comando alternativo só é selecionado por uma retomada explícita; execuções
    normais continuam usando ``command``. Isso permite que uma retomada valide um
    receipt determinístico sem repetir o comando caro que o produziu.
    """
    items: list[ValidationItem] = []
    extra_artifacts: dict[str, str] = {}
    validation_span: TraceSpan | None = None
    if trace is not None:
        validation_ordinal = trace.next_ordinal("validation", node.id)
        validation_span = trace.begin_span(
            category="validation",
            name="validators",
            node_id=node.id,
            parent_span_id=parent_span_id,
            attempt_id=attempt_id,
            invocation_id=f"{node.id}:validation:{validation_ordinal}",
            ordinal=validation_ordinal,
            attributes={
                "mode": node.validation_mode,
                "resume": resume,
                "validator_count": sum(
                    1
                    for spec in node.validators
                    for name in spec
                    if name != "stop_on_failure"
                ),
            },
        )

    def _execute(name: str, args: Any) -> tuple[bool, str]:
        fn = VALIDATOR_REGISTRY.get(name)
        if fn is None:
            return False, f"Validador desconhecido: {name}"

        if isinstance(args, dict):
            args = dict(args)
            # Metadata do runner vale para qualquer validator e nunca faz
            # parte da assinatura da função determinística subjacente.
            args.pop("stop_on_failure", None)

        def _eff_root(path: str = "") -> str:
            return _resolve_validator_root(path, project_root, work_dir)

        if name == "read_artifact" and isinstance(args, dict):
            passed, detail = fn(**args, project_root=_eff_root(args.get("path", "")))
            if passed:
                try:
                    kv = detail.split(": ", 1)[-1]
                    if "=" in kv:
                        key, value = kv.split("=", 1)
                        extra_artifacts[key.strip()] = value.strip()
                except Exception:
                    pass
            return passed, detail

        if name.startswith("gate_") and isinstance(args, dict):
            return fn(**args, project_root=_eff_root())
        if name.startswith("gate_") and args is True:
            gate_root = work_dir or project_root
            if name == "gate_delivery":
                return fn(outputs=node.outputs, project_root=gate_root)
            return fn(project_root=gate_root)

        if isinstance(args, dict):
            resolved_args = dict(args)
            resume_command = (
                resolved_args.pop("resume_command", None)
                if name == "command_succeeds"
                else None
            )
            if resume and resume_command is not None:
                if not isinstance(resume_command, str) or not resume_command.strip():
                    return (
                        False,
                        "command_succeeds FAIL: resume_command deve ser uma string não vazia",
                    )
                resolved_args["command"] = resume_command
            if name == "sections_unchanged" and state_dir and "snapshot_path" in args:
                resolved_args["snapshot_path"] = str(
                    Path(state_dir) / args["snapshot_path"]
                )
                return fn(
                    **resolved_args,
                    project_root=_eff_root(args.get("path", "")),
                )
            return fn(**resolved_args, project_root=_eff_root())

        if args is True:
            return fn(project_root=work_dir or project_root)
        if isinstance(args, (int, float)):
            path = node.outputs[0] if node.outputs else ""
            if not path:
                return False, (
                    f"{name} FAIL: node sem outputs — não é possível inferir "
                    "o path do artefato"
                )
            return fn(path, args, project_root=_eff_root(path))
        if isinstance(args, str):
            return fn(args, project_root=_eff_root(args))
        if isinstance(args, list):
            path = node.outputs[0] if node.outputs else ""
            if not path:
                return False, (
                    f"{name} FAIL: node sem outputs — não é possível inferir "
                    "o path do artefato"
                )
            return fn(path, args, project_root=_eff_root(path))
        return False, f"Args nao suportados para {name}: {args}"

    stop_requested = False
    for validator_spec in node.validators:
        spec_stop = validator_spec.get("stop_on_failure") is True
        for name, args in validator_spec.items():
            if name == "stop_on_failure":
                continue
            child_span: TraceSpan | None = None
            if trace is not None:
                child_ordinal = trace.next_ordinal("validator", node.id)
                child_span = trace.begin_span(
                    category="validator",
                    name=name,
                    node_id=node.id,
                    parent_span_id=(
                        validation_span.span_id if validation_span is not None else parent_span_id
                    ),
                    attempt_id=attempt_id,
                    invocation_id=f"{node.id}:validator:{child_ordinal}",
                    ordinal=child_ordinal,
                    attributes={"resume": resume},
                )
            started = time.monotonic_ns()
            try:
                passed, detail = _execute(name, args)
            except Exception as exc:
                if child_span is not None:
                    child_span.finish(status="error", result=type(exc).__name__)
                if validation_span is not None:
                    validation_span.finish(status="error", result=type(exc).__name__)
                raise
            duration_ms = max(0, (time.monotonic_ns() - started) // 1_000_000)
            items.append(
                ValidationItem(
                    name=name,
                    passed=passed,
                    detail=detail,
                    duration_ms=duration_ms,
                )
            )
            if child_span is not None:
                child_span.finish(
                    status="ok" if passed else "error",
                    result="PASS" if passed else "FAIL",
                    metrics={"duration_ms": duration_ms},
                    attributes={"detail": detail},
                )
            arg_stop = isinstance(args, dict) and args.get("stop_on_failure") is True
            if not passed and (
                node.validation_mode == "fail_fast" or spec_stop or arg_stop
            ):
                stop_requested = True
                break
        if stop_requested:
            break

    all_passed = all(item.passed for item in items)
    retryable = not all_passed and node.executor.startswith("llm")
    feedback = None
    if not all_passed:
        failures = [item.detail for item in items if not item.passed]
        feedback = "\n".join(failures)

    if validation_span is not None:
        validation_span.finish(
            status="ok" if all_passed else "error",
            result="PASS" if all_passed else "FAIL",
            metrics={
                "executed": len(items),
                "configured": sum(
                    1
                    for spec in node.validators
                    for name in spec
                    if name != "stop_on_failure"
                ),
            },
        )

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


def _format_outputs_contract(outputs: list[str]) -> str:
    """Formata outputs com tipo explícito para reduzir variações de path."""
    if not outputs:
        return "- conforme necessario"
    lines: list[str] = []
    for output in outputs:
        kind = "Diretorio" if output.endswith("/") else "Arquivo"
        lines.append(f"- {kind}: {output}")
    return "\n".join(lines)


def _format_validators_contract(validators: list[dict[str, Any]]) -> str:
    """Formata validadores em texto curto para o executor."""
    if not validators:
        return "- nenhum validador explicito"
    lines: list[str] = []
    for spec in validators:
        for name, args in spec.items():
            if args is True:
                lines.append(f"- {name}")
            else:
                lines.append(f"- {name}: {args}")
    return "\n".join(lines)


def _required_sections_hint(validators: list[dict[str, Any]]) -> str:
    """Extrai headings obrigatórios de validadores has_sections para o prompt."""
    sections: list[str] = []
    for spec in validators:
        args = spec.get("has_sections")
        if isinstance(args, list):
            sections.extend(str(item) for item in args if str(item).strip())
        elif isinstance(args, dict):
            values = args.get("sections")
            if isinstance(values, list):
                sections.extend(str(item) for item in values if str(item).strip())
    sections = list(dict.fromkeys(sections))
    if not sections:
        return ""
    headings = "\n".join(f"- ## {section}" for section in sections)
    return (
        "\nHeadings obrigatorios: inclua exatamente estes headings Markdown "
        "(mesma grafia, singular/plural e acentos):\n"
        f"{headings}\n"
    )


def _api_contract_format_hint(node: Node) -> str:
    """Formato estrito para contrato de API acionavel."""
    if node.id != "ft.plan.03.api_contract":
        return ""
    return """
FORMATO RIGIDO OBRIGATORIO PARA ESTE DOCUMENTO:
- Use `## Base URL` com valor `http://localhost:8000`.
- Use `## Endpoints` imediatamente antes da tabela.
- Em `## Endpoints`, escreva UMA tabela Markdown com EXATAMENTE estas colunas:
  `Método | Path | Descrição | Request | Response | Erros`
- A coluna `Path` deve conter somente paths relativos, nunca URL completa,
  nunca `http://{base_url}`, nunca `{base_url}`.
- O health check deve ser exatamente `GET | /health | ...`.
- Todo endpoint de produto deve começar com `/api/`, por exemplo:
  `/api/recursos`, `/api/itens`, `/api/eventos`.
- Inclua pelo menos 3 endpoints de produto e inclua `POST /api/<recurso>`
  para entidades criaveis.
- Nao descreva endpoints como headings soltos; a tabela e o contrato canonico.

Esqueleto minimo esperado:

## Base URL

`http://localhost:8000`

## Endpoints

| Método | Path | Descrição | Request | Response | Erros |
|---|---|---|---|---|---|
| GET | /health | Health check | - | `{ "status": "ok" }` | 500 |
| GET | /api/recursos | Listar recursos | - | `{ "items": [...] }` | 500 |
| POST | /api/recursos | Criar recurso | `{...}` | `{ "id": 1, ... }` | 400, 500 |
"""


def _build_execution_hints(node: Node) -> str:
    """Contrato operacional para nodes que escrevem codigo via LLM."""
    lines = [
        "Regras operacionais obrigatorias:",
        "- Use somente paths relativos ao diretorio de trabalho e aos outputs permitidos.",
        "- Antes de escrever qualquer arquivo em um diretorio, crie esse diretorio no mesmo comando/script com `mkdir -p`.",
        "- Prefira um unico comando Bash idempotente com `set -euo pipefail` para criar a arvore de arquivos.",
        "- Nao escreva temporarios na raiz do worktree; se precisar, use apenas paths dentro dos outputs permitidos.",
        "- Heredocs precisam fechar o delimitador (`EOF`) no fim; nao deixe JSON, TS, JS ou Python incompleto.",
        "- Depois de criar package.json, valide com `node -e \"JSON.parse(require('fs').readFileSync('CAMINHO/package.json','utf8'))\"`.",
        "- Execute localmente os comandos dos validadores antes de dizer DONE.",
    ]
    if node.id == "ft.frontend.01.scaffold":
        lines.extend(
            [
                "",
                "Dica especifica para este scaffold:",
                "- Crie obrigatoriamente `project/frontend/package.json` e `project/frontend/scripts/build.mjs`.",
                "- Em modo file bundle, retorne blocos para TODOS estes paths: `project/frontend/package.json` e `project/frontend/scripts/build.mjs`.",
                "- Se voce retornar somente `package.json`, este node falhara porque `npm run build --silent` precisa de `project/frontend/scripts/build.mjs`.",
                "- O `package.json` DEVE conter `scripts.build`; sem isso o validador `npm run build --silent` falha.",
                "- Use exatamente este script no `package.json`: `\"build\": \"node scripts/build.mjs\"`.",
                "- Crie obrigatoriamente `project/frontend/scripts/build.mjs`; ele pode apenas imprimir sucesso e sair com codigo 0.",
                "- Comece pelo comando: `mkdir -p project/frontend/scripts`.",
                "- Nunca use `npm init`, `npm create`, `npx` ou paths absolutos para este scaffold.",
                "- Antes do DONE, rode exatamente: `(cd project/frontend && npm install --silent && npm run build --silent)`.",
            ]
        )
    return "\n".join(lines)








def _should_skip_auto_fix(blocked_reason: str) -> bool:
    """Erros semânticos que o LLM não deve maquiar com alteração pontual."""
    reason = (blocked_reason or "").lower()
    return (
        "screenshots e2e nao correspondem ao produto esperado" in reason
        or "gameplay guard falhou" in reason
    )






def _description_block(node: Node) -> str:
    if not node.description:
        return ""
    return f"\nDescricao especifica do node:\n{node.description}\n"


def build_task_prompt(node: Node, state_dict: dict[str, Any]) -> str:
    """Constroi o prompt de construcao para o LLM baseado no node."""
    outputs_str = ", ".join(node.outputs) if node.outputs else "conforme necessario"
    outputs_contract = _format_outputs_contract(node.outputs)
    validators_contract = _format_validators_contract(node.validators)
    sections_hint = _required_sections_hint(node.validators)
    api_contract_hint = _api_contract_format_hint(node)
    desc = _description_block(node)

    # Custom prompt override
    if node.prompt:
        return f"""{node.prompt}

{desc}
Contrato de saida esperado:
{outputs_contract}

Validadores que precisam passar:
{validators_contract}
"""

    if node.type == "discovery":
        return f"""Conduza a etapa de discovery: {node.title}
{desc}

Contrato de saida esperado:
{outputs_contract}

O artefato deve ser um documento markdown completo e acionavel.
Interaja com o stakeholder se necessario (faca perguntas diretas).
"""
    elif node.type == "document":
        return f"""Produza o documento: {node.title}
{desc}

Contrato de saida esperado:
{outputs_contract}

Validadores que precisam passar:
{validators_contract}
{sections_hint}
{api_contract_hint}

O documento deve ser completo, estruturado em markdown, e pronto para revisao.
"""
    elif node.type == "test_red":
        return f"""TDD RED PHASE: {node.title}
{desc}

Escreva APENAS os testes. NAO implemente o codigo de producao ainda.
Os testes DEVEM FALHAR (red phase do TDD).

Contrato de saida esperado:
{outputs_contract}

Validadores que precisam passar:
{validators_contract}

Escreva testes que:
- Cobrem os cenarios principais (happy path)
- Cobrem edge cases
- Usam pytest
- Importam os modulos que serao implementados (mesmo que ainda nao existam)
"""
    elif node.type == "test_green":
        return f"""TDD GREEN PHASE: {node.title}
{desc}

Implemente o codigo MINIMO necessario para fazer os testes passarem.
NAO refatore, NAO adicione funcionalidades extras.

Contrato de saida esperado:
{outputs_contract}

Validadores que precisam passar:
{validators_contract}

O codigo deve:
- Fazer todos os testes passarem
- Ser o minimo necessario (sem over-engineering)
- Seguir as interfaces definidas nos testes
"""
    elif node.type == "refactor":
        return f"""TDD REFACTOR PHASE: {node.title}
{desc}

Refatore o codigo mantendo todos os testes passando.
Melhore a qualidade sem mudar o comportamento.

Contrato de saida esperado:
{outputs_contract}

Validadores que precisam passar:
{validators_contract}

Checklist:
- Extrair duplicacoes
- Nomear variaveis/funcoes melhor
- Simplificar logica complexa
- Manter testes verdes
"""
    elif node.type == "review":
        return f"""EXPERT REVIEW: {node.title}
{desc}

Revise os artefatos produzidos e emita um parecer de qualidade.

Contrato de saida obrigatorio:
{outputs_contract}

Validadores que precisam passar:
{validators_contract}

Regras de output:
- Use exatamente os paths acima; nao crie variacoes de nome, pluralizacao ou subpastas alternativas.
- Se houver um arquivo .md nos outputs, ele e o relatorio canonico do review.
- Diretorios listados nao substituem arquivos obrigatorios.
- Antes de declarar DONE, crie ou atualize os arquivos obrigatorios do contrato.

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

Produza o relatorio no arquivo .md canonico listado no contrato de saida.
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
        execution_hints = _build_execution_hints(node)
        return f"""Implemente: {node.title}
{desc}

Contrato de saida esperado:
{outputs_contract}

Validadores que precisam passar:
{validators_contract}

{execution_hints}

Siga TDD: escreva testes primeiro, depois implemente.
Garanta que os testes passam ao final.
"""
    else:
        return f"""Execute: {node.title}
{desc}

Contrato de saida esperado:
{outputs_contract}

Validadores que precisam passar:
{validators_contract}
"""


# ---------------------------------------------------------------------------
# Step Runner
# ---------------------------------------------------------------------------

MAX_RETRIES = 3


def _lexical_absolute(path: str | Path) -> Path:
    return Path(os.path.abspath(path))


def _first_unprotected_symlink(
    source: Path,
    *,
    root: Path,
    protected: Path,
) -> Path | None:
    project = root.resolve()
    lexical = _lexical_absolute(source)
    if lexical == protected:
        return None
    try:
        relative = lexical.relative_to(project)
    except ValueError:
        return lexical
    current = project
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return current
    try:
        lexical.resolve(strict=False).relative_to(project)
    except ValueError:
        return lexical
    if not source.is_dir():
        return None
    for candidate in source.rglob("*"):
        if _lexical_absolute(candidate) == protected:
            continue
        if candidate.is_symlink():
            return candidate
    return None


def _destination_symlink_or_escape(root: Path, destination: Path) -> Path | None:
    project = root.resolve()
    lexical = _lexical_absolute(destination)
    try:
        relative = lexical.relative_to(project)
    except ValueError:
        return lexical
    current = project
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return current
    if lexical.is_dir():
        for candidate in lexical.rglob("*"):
            if candidate.is_symlink():
                return candidate
    try:
        lexical.resolve(strict=False).relative_to(project)
    except ValueError:
        return lexical
    return None


def _normalized_close_copy_paths(
    requested: list[str],
    *,
    cycle_id: str,
) -> tuple[list[str], str | None]:
    """Expand safe `.ft/` metadata without ever copying process snapshots.

    A cycle worktree pins `.ft/process/` at birth. Copying that catalog back
    would revert updates/evolves performed later in the main checkout. Process
    evolution must use Git's 3-way merge (`full`) or `ft evolve`, never a
    last-writer-wins directory copy.
    """
    result: list[str] = []
    safe_ft = [f".ft/cycles/{cycle_id}/", ".ft/.gitignore"]
    for raw in requested:
        value = str(raw).strip()
        relative = Path(value.rstrip("/"))
        folded_parts = tuple(part.casefold() for part in relative.parts)
        if not value or relative.is_absolute() or ".." in relative.parts:
            return [], f"path seletivo inseguro: {raw}"
        if relative == Path("."):
            return [], (
                "merge seletivo da raiz é recusado porque incluiria snapshots "
                "de .ft/process; informe paths específicos"
            )
        if folded_parts[:2] == (".ft", "process"):
            return [], (
                "merge por cópia de .ft/process é recusado para não reverter "
                "updates concorrentes; use merge full ou ft evolve"
            )
        if folded_parts == (".ft",):
            candidates = safe_ft
        elif folded_parts == (".ft", "manifest.yml"):
            candidates = []
        else:
            canonical_parts = list(relative.parts)
            if folded_parts and folded_parts[0] == ".ft":
                canonical_parts[0] = ".ft"
                if len(folded_parts) > 1 and folded_parts[1] == "cycles":
                    canonical_parts[1] = "cycles"
                elif len(folded_parts) > 1 and folded_parts[1] == ".gitignore":
                    canonical_parts[1] = ".gitignore"
            candidates = [Path(*canonical_parts).as_posix()]
        for candidate in candidates:
            if candidate not in result:
                result.append(candidate)
    cycle_path = f".ft/cycles/{cycle_id}/"
    if cycle_path not in result:
        result.append(cycle_path)
    return result, None


def _copy_close_paths(
    source_root: Path,
    destination_root: Path,
    copy_paths: list[str],
    *,
    label: str,
    ignore=None,
) -> tuple[bool, list[str]]:
    """Copy close artifacts atomically with respect to project writers."""
    import shutil as _shutil
    from ft.engine.layout import (
        _assert_no_exclusive_startup,
        _manifest_write_lock,
    )

    protected_manifest = _lexical_absolute(source_root / ".ft" / "manifest.yml")
    copied: list[str] = []
    try:
        with _manifest_write_lock(destination_root):
            _assert_no_exclusive_startup(destination_root)
            for raw in copy_paths:
                relative = Path(str(raw).strip().rstrip("/"))
                folded_parts = tuple(
                    part.casefold() for part in relative.parts
                )
                if (
                    relative.is_absolute()
                    or ".." in relative.parts
                    or relative == Path(".")
                    or folded_parts == (".ft",)
                    or folded_parts[:2] == (".ft", "process")
                ):
                    print(ui.fail(f"{label}: path por cópia inseguro — {raw}"))
                    return False, []
                source = source_root / raw
                destination = destination_root / raw
                if folded_parts == (".ft", "manifest.yml"):
                    continue
                source_issue = _first_unprotected_symlink(
                    source,
                    root=source_root,
                    protected=protected_manifest,
                )
                if source_issue is not None:
                    print(ui.fail(f"{label}: origem simbólica recusada — {source_issue}"))
                    return False, []
                destination_issue = _destination_symlink_or_escape(
                    destination_root,
                    destination,
                )
                if destination_issue is not None:
                    print(ui.fail(
                        f"{label}: destino inseguro recusado — {destination_issue}"
                    ))
                    return False, []

            for raw in copy_paths:
                source = source_root / raw
                destination = destination_root / raw
                relative = Path(str(raw).strip().rstrip("/"))
                folded_parts = tuple(
                    part.casefold() for part in relative.parts
                )
                if folded_parts == (".ft", "manifest.yml"):
                    print(ui.dim(
                        "  Skip: .ft/manifest.yml "
                        "(defaults vivem no checkout principal)"
                    ))
                    continue
                if not source.exists():
                    print(ui.dim(f"  Skip: {raw} (não existe)"))
                    continue
                if source.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                    _shutil.copytree(
                        source,
                        destination,
                        dirs_exist_ok=True,
                        ignore=ignore,
                    )
                else:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    _shutil.copy2(source, destination)
                copied.append(raw)
    except RuntimeError as exc:
        print(ui.fail(f"{label}: {exc}"))
        return False, []
    return True, copied


@dataclass
class OpenCodeOptions:
    deny_read_paths: list[str] = field(default_factory=list)
    restrict_tools: bool = False
    steps: int | None = None
    deny_edit_tools: bool = False
    early_success_paths: list[str] = field(default_factory=list)
    capture_output_path: str | None = None


class StepRunner(OpenCodeDomainFallbackMixin):
    """Motor deterministico. Roda o loop principal."""

    def __init__(
        self,
        process_path: str | Path,
        state_path: str | Path,
        project_root: str | Path = ".",
        llm_engine: str | None = None,
        llm_model: str | None = None,
        verbose: bool = False,
        llm_effort: str | None = None,
        llm_defaults_root: str | Path | None = None,
        llm_engine_is_override: bool | None = None,
        llm_model_is_override: bool | None = None,
        llm_effort_is_override: bool | None = None,
    ):
        self.project_root = str(Path(project_root).resolve())
        selected_process = Path(process_path)
        if not selected_process.is_absolute():
            selected_process = Path(self.project_root) / selected_process
        selected_process = selected_process.resolve()
        if paths.project_manifest(self.project_root).is_file():
            selected_process = validate_local_process_path(
                self.project_root,
                selected_process,
                require_registered=True,
            )
        self.graph = load_graph(selected_process)
        self.process_path = str(selected_process)
        execution_policy = self.graph.meta.get("execution_policy", {})
        if (
            isinstance(execution_policy, dict)
            and execution_policy.get("runtime_source") == "local_only"
        ):
            process_catalog = paths.project_process_dir(self.project_root).resolve()
            try:
                process_catalog.relative_to(Path(self.project_root).resolve())
                selected_process.relative_to(process_catalog)
            except ValueError as exc:
                raise ValueError(
                    "processo com runtime_source=local_only deve estar dentro de .ft/process/"
                ) from exc
        self.state_mgr = StateManager(state_path)
        defaults_root = llm_defaults_root if llm_defaults_root is not None else self.project_root
        engine_is_override = (
            llm_engine is not None
            if llm_engine_is_override is None
            else llm_engine_is_override
        )
        model_is_override = (
            llm_model is not None
            if llm_model_is_override is None
            else llm_model_is_override
        )
        effort_is_override = (
            llm_effort is not None
            if llm_effort_is_override is None
            else llm_effort_is_override
        )
        self._llm_settings = LiveLLMSettings.from_inputs(
            defaults_root=defaults_root,
            cycle_root=self.project_root,
            llm_engine=llm_engine,
            llm_model=llm_model,
            llm_effort=llm_effort,
            engine_is_override=engine_is_override,
            model_is_override=model_is_override,
            effort_is_override=effort_is_override,
        )
        self._auto_approve = False
        self._verbose = verbose
        # KB path: diretório com lições de runs anteriores (opcional)
        self._kb_path = os.environ.get("FT_KB_PATH")
        # Nome do log derivado da pasta do projeto (ex: pokemon_log.md)
        self._log_filename = f"{Path(self.project_root).name}_log.md"
        # Environment config + hooks
        self._environment = load_environment(
            self.project_root,
            process_path=self.process_path,
        )
        self._max_node_retries = self._environment.get("max_node_retries", MAX_RETRIES)
        self._max_gate_retries = self._environment.get("max_gate_retries", MAX_RETRIES)
        self._max_auto_fix = self._environment.get("max_auto_fix", 2)
        self._bypass_human_gates = False  # setado por cmd_run via --bypass-human-gates
        # Run mode: isolated → LLM trabalha na worktree externa; continuous → na raiz.
        self._run_mode = self._environment.get("run_mode", "isolated")
        self._work_dir = self._resolve_work_dir()
        trace_run_id = (
            self.state_mgr.path.parent.parent.name
            if self.state_mgr.path.parent.name == "state"
            else Path(self.project_root).name
        )
        self.trace = TraceRecorder.for_state_path(
            self.state_mgr.path,
            trace_run_id,
        )
        # Tracking para log enriquecido
        self._node_start_times: dict[str, datetime] = {}   # node_id → início
        self._node_attempts: dict[str, int] = {}            # node_id → nº tentativas
        self._active_node_trace: TraceSpan | None = None
        self._active_node_trace_id: str | None = None
        self._active_node_attempt_id: str | None = None
        self._run_trace_id: str | None = None
        self._active_llm_traces: dict[str, TraceSpan] = {}
        self._active_llm_episodes: dict[str, str] = {}
        self._pending_llm_trace_attributes: dict[str, Any] = {}
        self._auto_fix_counts: dict[str, int] = {}          # node_id → auto-fix attempts
        self._auto_fix_prev_error: dict[str, str] = {}     # node_id → último erro (detecção de loop)

    def _stream_prefix(self, engine: str | None = None) -> str | None:
        """Retorna stream_prefix se verbose, None caso contrário."""
        if not self._verbose:
            return None
        label = engine or self._resolve_llm_engine()
        return f"{label}>"

    def _node_needs_shell_tools(self, node: Node) -> bool:
        """Detecta nodes que precisam de shell/list/grep para validar ou executar."""
        shell_validators = {
            "bash_passes",
            "command_succeeds",
            "tests_pass",
            "tests_fail",
            "coverage_min",
            "coverage_per_file",
            "lint_clean",
            "format_check",
            "gate_frontend",
            "gate_delivery",
            "gate_smoke",
            "gate_mvp",
            "gate_tdd_sequence",
            "gate_coverage_80",
            "gate_e2e_all_pass",
            "gate_server_starts",
        }
        for spec in node.validators:
            if any(name in shell_validators for name in spec):
                return True
        return node.type in {"build", "test_red", "test_green", "refactor"}

    def _opencode_options_for_node(
        self,
        node: Node,
        effective_engine: str,
        deny_read_paths: list[str] | None = None,
        restrict_tools: bool | None = None,
        steps: int | None = None,
    ) -> OpenCodeOptions:
        """Define limites e permissões OpenCode de forma consistente por node."""
        if effective_engine != "opencode":
            return OpenCodeOptions()

        default_steps_by_type = {
            "discovery": 12,
            "document": 8,
            "review": 10,
            "retro": 12,
            "build": 40,
            "test_red": 30,
            "test_green": 50,
            "refactor": 30,
            "gate": 20,
        }
        default_steps = default_steps_by_type.get(node.type, 30)
        if steps is not None:
            resolved_steps = steps
        elif node.max_turns is not None:
            resolved_steps = min(node.max_turns, default_steps)
        else:
            resolved_steps = default_steps

        if restrict_tools is None:
            restrict_tools = node.type == "review" and not self._node_needs_shell_tools(node)

        early_success_paths: list[str] = []
        if node.type in {"discovery", "document", "retro"}:
            early_success_paths = [
                str(output)
                for output in node.outputs
                if not str(output).endswith("/")
            ]
        capture_output_path = None
        capture_docs = os.environ.get("FT_OPENCODE_CAPTURE_DOCS", "").strip().lower() not in {
            "0",
            "false",
            "no",
            "nao",
            "não",
            "off",
        }
        if capture_docs and node.type in {"discovery", "document", "retro"} and len(early_success_paths) == 1:
            capture_output_path = early_success_paths[0]

        return OpenCodeOptions(
            deny_read_paths=list(dict.fromkeys(deny_read_paths or [])),
            restrict_tools=bool(restrict_tools),
            steps=resolved_steps,
            deny_edit_tools=(
                _opencode_deny_edit_tools_enabled()
                and node.type in {"build", "test_red", "test_green", "refactor"}
            ),
            early_success_paths=early_success_paths,
            capture_output_path=capture_output_path,
        )

    @staticmethod
    def _apply_opencode_options(delegate_kwargs: dict, options: OpenCodeOptions) -> None:
        """Anexa opções OpenCode ao kwargs de delegação."""
        if options.deny_read_paths:
            delegate_kwargs["opencode_deny_read_paths"] = options.deny_read_paths
        if options.restrict_tools:
            delegate_kwargs["opencode_restrict_tools"] = True
        if options.steps is not None:
            delegate_kwargs["opencode_steps"] = options.steps
        if options.deny_edit_tools:
            delegate_kwargs["opencode_deny_edit_tools"] = True
        if options.early_success_paths:
            delegate_kwargs["opencode_early_success_paths"] = options.early_success_paths
        if options.capture_output_path:
            delegate_kwargs["opencode_capture_output_path"] = options.capture_output_path

    def _project_relative_process_path(self) -> str | None:
        """Return the selected process path when it belongs to this checkout.

        Real project runs are guarded by the v2 manifest in ``__init__``.  A
        few low-level unit harnesses intentionally build a runner without a
        manifest and keep their fixture YAML outside ``project_root``; those
        harnesses must not manufacture a local process path for prompts.
        """
        try:
            return Path(self.process_path).resolve().relative_to(
                Path(self.project_root).resolve()
            ).as_posix()
        except ValueError:
            return None

    def _try_opencode_compact_bundle_node(
        self,
        node: Node,
        state,
        effective_engine: str,
        allowed_paths: list[str],
        opencode_options: OpenCodeOptions,
    ) -> bool:
        """Materializa bundles compactos OpenCode em chamadas pequenas por arquivo."""
        if effective_engine != "opencode":
            return False
        process_relative = self._project_relative_process_path()
        compact = _opencode_compact_bundle_prompt(node, process_relative)
        if not compact:
            return False
        files = [
            (match.group(1).strip(), match.group(2).strip())
            for match in re.finditer(r'<ft_file\s+path="([^"]+)">\n?(.*?)\n?</ft_file>', compact, re.DOTALL)
        ]
        if not files:
            return False

        def write_controlled_file(path: str, content: str) -> bool:
            root = Path(self._work_dir).resolve()
            target = (root / path).resolve()
            try:
                target.relative_to(root)
            except ValueError:
                self.state_mgr.block(f"OpenCode compact bundle path fora do worktree: {path}")
                return False
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content.rstrip() + "\n", encoding="utf-8")
            return True

        print(ui.info(f"OpenCode compact bundle: materializando {len(files)} arquivos"))
        direct_compact_nodes = globals().get(
            "_OPENCODE_DIRECT_COMPACT_NODES",
            {
                "ft.tdd.01.red",
                "ft.tdd.02.green",
                "ft.tdd.03.refactor",
                "ft.delivery.01.entrypoint",
                "ft.delivery.02.self_review",
                "ft.delivery.03.makefile",
                "ft.smoke.01.run",
                "ft.acceptance.01.cli",
            },
        )
        if node.id in direct_compact_nodes:
            print(ui.info("OpenCode compact bundle: materialização direta de bundle estático controlado"))
            for path, content in files:
                if not write_controlled_file(path, content):
                    return True
            validation = self._run_validators(node)
            self._print_validation(validation)
            if not validation.passed:
                self.state_mgr.block(f"OpenCode compact bundle insuficiente: {validation.feedback}")
                return True

            for output_path in node.outputs:
                self.state_mgr.record_artifact(Path(output_path).stem, output_path)
            self._maybe_auto_commit(node)
            self._record_node_summary(
                node,
                "NODE_SUMMARY:\n- fiz: bundle estático materializado pelo runner\n- verificado: validators do node passaram",
            )
            next_id = self.graph.resolve_next(node.id)
            self._advance_state(node.id, next_id)
            print(ui.step_pass(next_id, "PASS (opencode compact bundle)"))
            return True

        if node.id == "ft.tdd.01.red":
            shutil.rmtree(Path(self._work_dir) / "project" / "tests", ignore_errors=True)
        for idx, (path, content) in enumerate(files, start=1):
            if not content:
                if not write_controlled_file(path, ""):
                    return True
                continue
            compact_selection, log_path = self._start_delegation_attempt(
                state,
                node,
                f"compact-{idx}",
            )
            try:
                if compact_selection.engine == "opencode":
                    result = delegate_opencode_exact_file_raw(
                        path=path,
                        content=content,
                        project_root=self._work_dir,
                        allowed_paths=allowed_paths,
                        llm_model=compact_selection.model,
                        llm_effort=compact_selection.effort,
                        log_path=log_path,
                    )
                else:
                    result = delegate_to_llm(
                        task=(
                            f"Materialize exatamente o arquivo {path} com o conteúdo "
                            f"fornecido abaixo, sem alterar outros arquivos.\n\n{content}"
                        ),
                        project_root=self._work_dir,
                        allowed_paths=allowed_paths,
                        llm_engine=compact_selection.engine,
                        llm_model=compact_selection.model,
                        llm_effort=compact_selection.effort,
                        log_path=log_path,
                        stream_prefix=self._stream_prefix(compact_selection.engine),
                        llm_timeout_seconds=node.llm_timeout_seconds,
                    )
            finally:
                self._clear_active_llm_log(state)
            if not result.success:
                if node.id == "ft.frontend.01.scaffold":
                    print(ui.warn(f"OpenCode compact bundle falhou em {path}; materializando scaffold controlado pelo runner"))
                    if not write_controlled_file(path, content):
                        return True
                    continue
                self.state_mgr.block(f"OpenCode compact bundle falhou em {path}: {result.output[:500]}")
                return True

        validation = self._run_validators(node)
        self._print_validation(validation)
        if not validation.passed:
            self.state_mgr.block(f"OpenCode compact bundle insuficiente: {validation.feedback}")
            return True

        for output_path in node.outputs:
            self.state_mgr.record_artifact(Path(output_path).stem, output_path)
        self._maybe_auto_commit(node)
        self._record_node_summary(
            node,
            "NODE_SUMMARY:\n- fiz: bundle compacto via OpenCode por arquivo\n- verificado: validators do node passaram",
        )
        next_id = self.graph.resolve_next(node.id)
        self._advance_state(node.id, next_id)
        print(ui.step_pass(next_id, "PASS (opencode compact bundle)"))
        return True

    def _try_opencode_real_evidence_node(self, node: Node, effective_engine: str) -> bool:
        """Executa evidências reais pelo engine quando OpenCode só geraria shell frágil."""
        if effective_engine != "opencode":
            return False

        if node.id == "ft.final.01.visual_check":
            root = Path(self._work_dir)
            print(ui.info("OpenCode visual check: validando screenshots reais pelo engine"))
            try:
                self._write_opencode_visual_report(root)
            except Exception as exc:
                self.state_mgr.block(f"OpenCode visual check falhou: {exc}")
                return True
            return self._finish_opencode_fallback_node(
                node,
                "NODE_SUMMARY:\n- fiz: visual-check baseado em screenshots reais de navegação e criação\n- verificado: validators do node passaram",
            )

        if node.id != "ft.e2e.02.screenshots":
            return False
        root = Path(self._work_dir)
        print(ui.info("OpenCode E2E: executando browser real via Playwright pelo engine"))
        try:
            self._run_opencode_browser_e2e(root)
        except Exception as exc:
            self.state_mgr.block(f"OpenCode E2E real falhou: {exc}")
            return True
        return self._finish_opencode_fallback_node(
            node,
            "NODE_SUMMARY:\n- fiz: navegação, criação real via UI e screenshots via Playwright\n- verificado: validators do node passaram",
        )

    def _try_repair_opencode_frontend_scaffold(
        self,
        node: Node,
        effective_engine: str,
        validation: ValidationResult,
    ) -> bool:
        """Repara o contrato minimo do scaffold quando OpenCode erra schema/path."""
        if effective_engine != "opencode" or node.id != "ft.frontend.01.scaffold":
            return False
        feedback = validation.feedback or ""
        repair_triggers = (
            "project/frontend/package.json",
            'Missing script: "build"',
            "scripts/build.mjs",
            "command_succeeds FAIL",
            "npm run build",
        )
        if not any(trigger in feedback for trigger in repair_triggers):
            return False

        root = Path(self._work_dir)
        frontend = root / "project" / "frontend"
        package_json = frontend / "package.json"

        print(ui.info("OpenCode repair: normalizando scaffold frontend minimo"))
        frontend.mkdir(parents=True, exist_ok=True)
        try:
            data = json.loads(package_json.read_text(encoding="utf-8")) if package_json.exists() else {}
        except json.JSONDecodeError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        package_name = "@neon-stack/frontend" if self._is_opencode_game_product(root) else "@ft/frontend"
        data.setdefault("name", package_name)
        data.setdefault("version", "0.1.0")
        scripts = data.get("scripts")
        if not isinstance(scripts, dict):
            scripts = {}
        scripts["build"] = "node scripts/build.mjs"
        data["scripts"] = scripts
        data.setdefault("private", True)
        data.setdefault("type", "module")
        package_json.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

        scripts_dir = frontend / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        build_script = scripts_dir / "build.mjs"
        if not build_script.exists() or not build_script.read_text(encoding="utf-8").strip():
            build_script.write_text("console.log('build ok');\n", encoding="utf-8")
        (root / ".build_ok").write_text("frontend scaffold ready\n", encoding="utf-8")

        repaired = self._run_validators(node)
        self._print_validation(repaired)
        if not repaired.passed:
            return False

        for output_path in node.outputs:
            self.state_mgr.record_artifact(Path(output_path).stem, output_path)
        self._maybe_auto_commit(node)
        self._record_node_summary(
            node,
            "NODE_SUMMARY:\n- fiz: reparo determinístico do scaffold OpenCode (scripts.build)\n- verificado: validators do node passaram",
        )
        next_id = self.graph.resolve_next(node.id)
        self._advance_state(node.id, next_id)
        print(ui.step_pass(next_id, "PASS (opencode scaffold repair)"))
        return True

    def _extract_api_endpoint_candidates(self) -> list[tuple[str, str, str]]:
        """Extrai endpoints explícitos já citados nos docs do projeto."""
        root = Path(self._work_dir)
        sources = [
            root / "docs" / "task_list.md",
            root / "docs" / "PRD.md",
        ]
        endpoint_re = re.compile(
            r"\b(GET|POST|PUT|PATCH|DELETE)\b\s*(?:\|\s*|\s+)"
            r"`?(/(?:health\b|api/[A-Za-z0-9_./{}-]+|[A-Za-z0-9_./{}-]+))`?",
            re.IGNORECASE,
        )

        def normalize_path(raw: str) -> str:
            path = "/" + raw.strip().strip("`").lstrip("/")
            path = path.rstrip(".,;:)").replace("//", "/")
            if path == "/api/health":
                return "/health"
            if path != "/health" and not path.startswith("/api/"):
                path = f"/api{path}"
            return path

        def clean_description(text: str, fallback: str) -> str:
            value = re.sub(r"\s+", " ", text.replace("|", " ")).strip(" -–:`")
            if not value:
                value = fallback
            return value[:72]

        candidates: list[tuple[str, str, str]] = []
        seen: set[tuple[str, str]] = set()
        for source in sources:
            if not source.exists():
                continue
            try:
                lines = source.read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError:
                continue
            for line in lines:
                for match in endpoint_re.finditer(line):
                    method = match.group(1).upper()
                    path = normalize_path(match.group(2))
                    if path == "/api/health":
                        path = "/health"
                    key = (method, path)
                    if key in seen:
                        continue
                    seen.add(key)
                    description = ""
                    if "|" in line:
                        cells = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
                        for idx, cell in enumerate(cells):
                            if (
                                cell.upper() == method
                                and idx + 2 < len(cells)
                                and normalize_path(cells[idx + 1]) == path
                            ):
                                description = cells[idx + 2].strip()
                                break
                    if not description:
                        description = line[match.end():].strip(" |-–:") or f"{method} {path}"
                    candidates.append((method, path, clean_description(description, f"{method} {path}")))
        if ("GET", "/health") not in {(method, path) for method, path, _ in candidates}:
            candidates.insert(0, ("GET", "/health", "Health check"))
        product = [item for item in candidates if item[1].startswith("/api/") and item[1] != "/api/health"]
        health = [item for item in candidates if item[1] == "/health"]
        return (health[:1] + product)[:14]

    def _enrich_validation_feedback(self, node: Node, feedback: str) -> str:
        """Adiciona contexto acionável ao feedback enviado ao LLM."""
        if node.id != "ft.plan.03.api_contract":
            return feedback
        candidates = self._extract_api_endpoint_candidates()
        if not candidates:
            return feedback
        rows = []
        for method, path, description in candidates:
            request = "-" if method == "GET" else "`{...}`"
            response = '`{ "status": "ok" }`' if path == "/health" else '`{ "items": [...] }`'
            errors = "500" if method == "GET" else "400, 500"
            rows.append(f"| {method} | {path} | {description or method + ' ' + path} | {request} | {response} | {errors} |")
        return (
            f"{feedback}\n\n"
            "DIAGNOSTICO ESPECIFICO DO CONTRATO DE API:\n"
            "- O artefato anterior falhou na validacao; ele foi omitido para evitar contaminacao do retry.\n"
            "- Reescreva o arquivo inteiro. Nao preserve o formato anterior.\n"
            "- Cada endpoint deve ser uma linha Markdown com 6 colunas separadas por `|`.\n"
            "- A coluna Path deve conter `/health` ou `/api/...`; nunca URL completa.\n"
            "- Use estes endpoints explícitos já encontrados no PRD/task_list como base:\n"
            f"{chr(10).join(rows)}"
            "\n\n"
            "SAIDA ESPERADA: somente o Markdown final de docs/api_contract.md, começando em `## Base URL`."
        )

    def _try_repair_api_contract(
        self,
        node: Node,
        effective_engine: str,
        validation: ValidationResult,
    ) -> bool:
        """Normaliza contrato de API quando o LLM produz prosa em vez de tabela."""
        if effective_engine != "opencode" or node.id != "ft.plan.03.api_contract":
            return False
        feedback = validation.feedback or ""
        root = Path(self._work_dir)
        target = root / "docs" / "api_contract.md"
        repair_markers = (
            "api_contract_complete FAIL",
            "has_sections FAIL",
            "file_exists FAIL",
            "docs/api_contract.md nao existe",
            "docs/api_contract.md não existe",
        )
        if target.exists() and not any(marker in feedback for marker in repair_markers):
            return False
        candidates = self._extract_api_endpoint_candidates()
        product = [(m, p, d) for m, p, d in candidates if p.startswith("/api/")]
        if len(product) < 3 and self._is_opencode_game_product(root):
            candidates = [
                ("GET", "/health", "Verifica disponibilidade do servidor"),
                ("GET", "/api/daily-seed", "Retorna a seed diária para partida determinística"),
                ("POST", "/api/game-sessions", "Cria uma nova partida jogável"),
                ("POST", "/api/scores", "Registra score final de uma partida"),
                ("GET", "/api/leaderboard", "Lista ranking diário por score"),
            ]
            product = [(m, p, d) for m, p, d in candidates if p.startswith("/api/")]
        if len(product) < 3:
            return False

        target.parent.mkdir(parents=True, exist_ok=True)
        rows: list[str] = [
            "| Método | Path | Descrição | Request | Response | Erros |",
            "|---|---|---|---|---|---|",
        ]
        if not any(path == "/health" for _, path, _ in candidates):
            candidates.insert(0, ("GET", "/health", "Health check"))
        for method, path, description in candidates:
            request = "-" if method == "GET" else "`{...}`"
            if path == "/health":
                response = '`{ "status": "ok" }`'
            elif method == "GET":
                response = '`{ "items": [...] }`'
            else:
                response = '`{ "id": 1, ... }`'
            errors = "500" if method == "GET" else "400, 500"
            rows.append(f"| {method} | {path} | {description or method + ' ' + path} | {request} | {response} | {errors} |")
        body = "\n".join([
            "## Base URL",
            "",
            "`http://localhost:8000`",
            "",
            "## Endpoints",
            "",
            *rows,
            "",
            "## Observações de Contrato",
            "",
            "- `/health` é endpoint de infraestrutura e não usa prefixo `/api`.",
            "- Endpoints de produto usam `/api/<recurso>` para manter o contrato entre frontend e backend.",
            "- Requisições `POST`, `PUT` e `PATCH` usam JSON no corpo e retornam JSON.",
            "- Erros de validação retornam HTTP 400; falhas internas retornam HTTP 500.",
            "- Campos obrigatórios ausentes retornam HTTP 400 com mensagem acionável.",
            "- Recursos não encontrados retornam HTTP 404 quando houver endpoint por identificador.",
            "- Listagens retornam arrays ou objetos com chave `items` conforme necessidade da tela.",
            "- Valores monetários usam número decimal em JSON, sem formatação local no contrato.",
            "- Datas e horários trafegam como strings ISO 8601.",
            "- Este contrato foi normalizado pelo engine a partir de PRD/task_list quando a resposta do LLM não ficou acionável.",
            "",
            "## Schemas Mínimos",
            "",
            "- GameSession: `id`, `seed`, `status`, `score`, `lines`, `level`, `created_at`.",
            "- Score: `id`, `session_id`, `score`, `lines`, `level`, `duration_ms`, `created_at`.",
            "- LeaderboardEntry: `player`, `score`, `lines`, `level`, `rank`.",
            "- DailySeed: `date`, `seed`, `expires_at`.",
        ])
        print(ui.info("OpenCode repair: normalizando contrato de API a partir dos docs"))
        target.write_text(body.rstrip() + "\n", encoding="utf-8")

        repaired = self._run_validators(node)
        self._print_validation(repaired)
        if not repaired.passed:
            return False

        for output_path in node.outputs:
            self.state_mgr.record_artifact(Path(output_path).stem, output_path)
        self._maybe_auto_commit(node)
        self._record_node_summary(
            node,
            "NODE_SUMMARY:\n- fiz: normalizacao deterministica do contrato de API a partir de PRD/task_list\n- verificado: validators do node passaram",
        )
        next_id = self.graph.resolve_next(node.id)
        self._advance_state(node.id, next_id)
        print(ui.step_pass(next_id, "PASS (api contract repair)"))
        return True

    def _try_repair_test_data(
        self,
        node: Node,
        effective_engine: str,
        validation: ValidationResult,
    ) -> bool:
        """Normaliza massa de dados quando OpenCode cria datas absolutas ou documento inválido."""
        if effective_engine != "opencode" or node.id != "ft.plan.05.test_data":
            return False
        feedback = validation.feedback or ""
        repair_markers = (
            "relative_dates_only FAIL",
            "document_quality FAIL",
            "file_exists FAIL",
            "docs/test_data.md nao existe",
            "docs/test_data.md não existe",
        )
        if not any(marker in feedback for marker in repair_markers):
            return False
        root = Path(self._work_dir)
        if not self._is_opencode_game_product(root):
            return False

        print(ui.info("OpenCode repair: normalizando massa de dados de jogo com datas relativas"))
        self._write_opencode_game_test_data_artifact()

        repaired = self._run_validators(node)
        self._print_validation(repaired)
        if not repaired.passed:
            return False

        for output_path in node.outputs:
            self.state_mgr.record_artifact(Path(output_path).stem, output_path)
        self._maybe_auto_commit(node)
        self._record_node_summary(
            node,
            "NODE_SUMMARY:\n- fiz: normalizacao deterministica da massa de dados de jogo com datas relativas\n- verificado: validators do node passaram",
        )
        next_id = self.graph.resolve_next(node.id)
        self._advance_state(node.id, next_id)
        print(ui.step_pass(next_id, "PASS (test data repair)"))
        return True




    def _try_repair_opencode_frontend_implementation(
        self,
        node: Node,
        effective_engine: str,
        validation: ValidationResult,
    ) -> bool:
        """Repara implementacao frontend quando OpenCode gera comandos incompletos."""
        if effective_engine != "opencode" or node.id != "ft.frontend.02.implement":
            return False
        root = Path(self._work_dir)
        game_product = self._is_opencode_game_product(root)
        feedback = validation.feedback or ""
        repair_triggers = (
            "command_succeeds FAIL",
            "frontend sem fluxo de criacao via UI",
            "project/frontend/src",
            "npm run build",
        )
        if not any(trigger in feedback for trigger in repair_triggers):
            return False

        if game_product:
            print(ui.info("OpenCode repair: implementando frontend de jogo jogável"))
        else:
            print(ui.info("OpenCode repair: implementando frontend estatico validavel"))
        self._write_opencode_frontend_implementation(root / "project" / "frontend")
        if game_product:
            try:
                self._assert_opencode_game_playability_contract(root)
            except Exception as exc:
                self.state_mgr.block(str(exc))
                return True

        repaired = self._run_validators(node)
        self._print_validation(repaired)
        if not repaired.passed:
            return False

        for output_path in node.outputs:
            self.state_mgr.record_artifact(Path(output_path).stem, output_path)
        self._maybe_auto_commit(node)
        self._record_node_summary(
            node,
            "NODE_SUMMARY:\n- fiz: reparo deterministico da implementacao frontend OpenCode\n- verificado: validators do node passaram",
        )
        next_id = self.graph.resolve_next(node.id)
        self._advance_state(node.id, next_id)
        print(ui.step_pass(next_id, "PASS (opencode frontend repair)"))
        return True




    def _write_doc(self, relative_path: str, content: str) -> None:
        target = Path(self._work_dir) / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def _run_validators(self, node: Node, *args, **kwargs) -> ValidationResult:
        kwargs.setdefault("trace", self.trace)
        kwargs.setdefault("parent_span_id", self._active_node_trace_id)
        kwargs.setdefault("attempt_id", self._active_node_attempt_id)
        if args or kwargs:
            if args:
                return run_validators(node, *args, **kwargs)
            return run_validators(
                node,
                self.project_root,
                state_dir=str(self.state_mgr.path.parent),
                work_dir=self._run_dir,
                **kwargs,
            )
        return run_validators(
            node,
            self.project_root,
            state_dir=str(self.state_mgr.path.parent),
            work_dir=self._run_dir,
            trace=self.trace,
            parent_span_id=self._active_node_trace_id,
            attempt_id=self._active_node_attempt_id,
        )













    def _finish_opencode_fallback_node(self, node: Node, summary: str, result: str = "PASS") -> bool:
        validation = self._run_validators(node)
        self._print_validation(validation)
        if not validation.passed:
            self.state_mgr.block(f"OpenCode fallback insuficiente: {validation.feedback}")
            return True

        for output_path in node.outputs:
            self.state_mgr.record_artifact(Path(output_path).stem, output_path)
        self._maybe_auto_commit(node)
        self._record_node_summary(node, summary)
        next_id = self.graph.resolve_next(node.id)
        self._advance_state(node.id, next_id, result)
        print(ui.step_pass(next_id, "PASS (opencode fallback)"))
        return True


    def _resolve_work_dir(self) -> str:
        """Resolve o diretório de trabalho (CWD) para delegação ao LLM.

        Modo isolated: worktree externo em $FT_HOME/worktrees/
        Modo continuous: project_root
        """
        if self._run_mode != "isolated":
            return self.project_root
        state_dir = self.state_mgr.path.parent
        run_dir = state_dir.parent
        if paths.is_worktree_path(run_dir):
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

        Em worktrees externos, docs/ e .ft/process/ vivem no próprio workdir e
        devem continuar relativos para o sandbox permitir escrita.
        """
        if self._work_dir == self.project_root:
            return paths
        work_root = Path(self._work_dir)
        result = []
        for p in paths:
            if p.startswith("docs/") or p.startswith(".ft/process/") or p == "CHANGELOG.md":
                top = p.split("/", 1)[0]
                if (work_root / top).exists():
                    result.append(p)
                else:
                    result.append(str(Path(self.project_root) / p))
            else:
                result.append(p)
        return result

    @staticmethod
    def _normalize_llm_effort(value: Any) -> str | None:
        return normalize_llm_effort(value)

    def _read_live_llm_defaults(self) -> dict[str, Any]:
        return self._llm_settings.read_live_defaults()

    @staticmethod
    def _llm_defaults_digest(defaults: dict[str, Any]) -> str:
        return LiveLLMSettings.defaults_digest(defaults)

    def _recorded_llm_defaults_digest(self, state: Any | None) -> str | None:
        return self._llm_settings.recorded_digest(state)

    @property
    def _has_command_llm_override(self) -> bool:
        return self._llm_settings.has_command_override

    def _resolve_llm_selection(
        self,
        state: Any | None = None,
        node: Any | None = None,
        *,
        manifest_defaults: dict[str, Any] | None = None,
        manifest_is_active: bool | None = None,
    ) -> LLMSelection:
        return self._llm_settings.resolve(
            state,
            node,
            manifest_defaults=manifest_defaults,
            manifest_is_active=manifest_is_active,
        )

    def _resolve_llm_engine(self, state: Any | None = None, node: Any | None = None) -> str:
        return self._resolve_llm_selection(state, node).engine

    def _resolve_llm_model(self, state: Any | None = None, node: Any | None = None) -> str | None:
        return self._resolve_llm_selection(state, node).model

    def _resolve_llm_effort(self, state: Any | None = None, node: Any | None = None) -> str | None:
        return self._resolve_llm_selection(state, node).effort

    def _persist_llm_selection(
        self,
        state: Any,
        selection: LLMSelection,
        *,
        defaults_digest: str | None = None,
    ) -> None:
        changed = False
        for attribute, value in (
            ("llm_engine", selection.engine),
            ("llm_model", selection.model),
            ("llm_effort", selection.effort),
        ):
            if getattr(state, attribute, None) != value:
                setattr(state, attribute, value)
                changed = True
        if (
            defaults_digest is not None
            and getattr(state, "llm_defaults_digest", None) != defaults_digest
        ):
            state.llm_defaults_digest = defaults_digest
            changed = True
        if changed:
            self.state_mgr.save()

    def _capture_delegation_llm_selection(
        self,
        state: Any,
        node: Any | None = None,
    ) -> LLMSelection:
        """Freeze live defaults for exactly one outgoing LLM call."""
        defaults = self._read_live_llm_defaults()
        current_digest = self._llm_defaults_digest(defaults)
        recorded_digest = self._recorded_llm_defaults_digest(state)
        manifest_changed = current_digest != recorded_digest
        persisted = self._resolve_llm_selection(
            state,
            manifest_defaults=defaults,
            manifest_is_active=manifest_changed,
        )
        consume_digest = not (self._has_command_llm_override and manifest_changed)
        self._persist_llm_selection(
            state,
            persisted,
            defaults_digest=current_digest if consume_digest else None,
        )
        return self._resolve_llm_selection(
            state,
            node,
            manifest_defaults=defaults,
            manifest_is_active=manifest_changed,
        )

    def _persist_llm_engine(self, state: Any) -> None:
        """Persiste engine, model e effort para comandos subsequentes do projeto."""
        defaults = self._read_live_llm_defaults()
        current_digest = self._llm_defaults_digest(defaults)
        recorded_digest = self._recorded_llm_defaults_digest(state)
        manifest_changed = current_digest != recorded_digest
        selection = self._resolve_llm_selection(
            state,
            manifest_defaults=defaults,
            manifest_is_active=manifest_changed,
        )
        consume_digest = not (self._has_command_llm_override and manifest_changed)
        self._persist_llm_selection(
            state,
            selection,
            defaults_digest=current_digest if consume_digest else None,
        )

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

    def _decision_next_for_state(self, node: Node, state: Any) -> str | None:
        decision_state = self._decision_state_dict(state)
        if node.condition and node.condition.startswith("file_exists:"):
            check_path = node.condition.split(":", 1)[1]
            full_path = Path(self._work_dir) / check_path
            decision_state[node.condition] = "true" if full_path.exists() else "false"
        return self.graph.resolve_next(node.id, decision_state)

    def _collect_unselected_path(self, start_id: str | None, stop_ids: set[str], completed: set[str]) -> list[str]:
        skipped: list[str] = []
        seen: set[str] = set()
        current = start_id
        while current and current not in stop_ids and current not in completed and current not in seen:
            node = self.graph.nodes.get(current)
            if node is None:
                break
            seen.add(current)
            if node.type != "end":
                skipped.append(current)
            current = node.next
        return skipped

    def _reachable_ids(self, start_id: str | None) -> set[str]:
        """Retorna todos os nodes alcançáveis a partir de um ponto do grafo."""
        if not start_id:
            return set()
        reachable: set[str] = set()
        stack = [start_id]
        while stack:
            current = stack.pop()
            if not current or current in reachable:
                continue
            node = self.graph.nodes.get(current)
            if node is None:
                continue
            reachable.add(current)
            if node.next:
                stack.append(node.next)
            if node.branches:
                stack.extend(node.branches.values())
        return reachable

    def _mark_unselected_paths_skipped(
        self,
        state: Any,
        completed_node: str | None = None,
        next_node: str | None = None,
    ) -> bool:
        """Marca branches não escolhidos como SKIPPED para progresso refletir o caminho fechado."""
        changed = False
        completed = set(state.completed_nodes)
        completed_nodes = [completed_node] if completed_node else list(state.completed_nodes)

        for node_id in completed_nodes:
            node = self.graph.nodes.get(node_id)
            if node is None:
                continue

            selected_next = next_node if node_id == completed_node else None
            if selected_next is None:
                selected_next = self._decision_next_for_state(node, state) if node.type == "decision" else node.next

            candidates: list[str] = []
            if node.type == "decision" and node.branches:
                candidates.extend(target for target in node.branches.values() if target != selected_next)

            gate_result = state.gate_log.get(node.id, "")
            rejected = gate_result.upper().startswith("REJECT")
            if node.type == "human_gate" and node.reject_next and not rejected and node.reject_next != selected_next:
                candidates.append(node.reject_next)

            # Branches podem reconvergir depois de um step intermediário
            # (ex.: se falta ui_criteria.md, gerar arquivo e voltar ao planejamento).
            # Não marque como SKIPPED nodes que ainda são alcançáveis pela branch escolhida.
            stop_ids = {node.id, *self._reachable_ids(selected_next)}
            for candidate in dict.fromkeys(candidates):
                for skipped_id in self._collect_unselected_path(candidate, stop_ids, completed):
                    state.completed_nodes.append(skipped_id)
                    state.gate_log.setdefault(skipped_id, "SKIPPED")
                    completed.add(skipped_id)
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

        if self._mark_unselected_paths_skipped(state):
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

    def _build_llm_log_path(
        self,
        node_id: str,
        phase: str,
        *,
        engine: str | None = None,
    ) -> Path:
        """Gera nome estável e legível para um log de step delegado."""
        safe_node = node_id.replace("/", "-")
        safe_phase = phase.replace("/", "-")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        effective_engine = engine or self._resolve_llm_engine(self.state_mgr.state)
        suffix = ".jsonl" if effective_engine == "codex" else ".log"
        return self._llm_log_dir() / f"{stamp}__{safe_node}__{safe_phase}{suffix}"

    def _resolve_allowed_paths(self, node: Node) -> list[str]:
        """Resolve o escopo de escrita efetivo do node."""
        if node.write_scope:
            return list(dict.fromkeys(node.write_scope))

        allowed = []
        code_node_writes_project = node.type in {"build", "test_red", "test_green", "refactor"} and any(
            str(output).startswith("project/") for output in node.outputs
        )
        if code_node_writes_project:
            allowed.append("project")
        for output in node.outputs:
            output_str = str(output)
            if code_node_writes_project and output_str.startswith("project/"):
                continue
            if output_str.endswith("/"):
                allowed_path = output_str.rstrip("/") or "."
            else:
                allowed_path = output_str
            if allowed_path not in allowed:
                allowed.append(allowed_path)
        if allowed:
            return allowed

        return ["project/", "docs/"]

    def _clear_no_pre_seed_outputs(self, node: Node) -> None:
        """Remove only disposable cycle outputs before forced regeneration."""
        if (
            not getattr(node, "no_pre_seed", False)
            or getattr(node, "preserve_outputs_on_reentry", False)
        ):
            return
        root = Path(self.project_root).resolve()
        for output in node.outputs:
            try:
                disposable = is_cycle_artifact(output, self.graph.meta)
            except ValueError:
                disposable = False
            if not disposable:
                continue
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
        """Remove do Hyper-mode docs gerados pelo próprio node atual.

        Em retries, o output pode existir mas estar inválido. Injetá-lo de volta
        como contexto faz o LLM copiar ou derivar do artefato quebrado.
        """
        if not docs:
            return docs
        excluded = {
            Path(output).name
            for output in node.outputs
            if Path(output).parts and Path(output).parts[0] == "docs"
        }
        if not excluded:
            return docs
        return {name: content for name, content in docs.items() if name not in excluded}

    def _scan_hyper_mode_docs(self, node: Node) -> dict[str, str]:
        """Carrega o conjunto de docs configurado para o node atual."""
        return scan_existing_docs(
            self.project_root,
            allowlist=node.hyper_mode_docs,
        )

    @staticmethod
    def _hyper_mode_prompt_for_node(
        node: Node,
        existing_docs: dict[str, str],
        original_prompt: str,
        *,
        default_preview_lines: int,
        allow_followup_reads: bool,
    ) -> str:
        """Aplica limites opcionais do node sem alterar defaults legados."""
        preview_lines = (
            default_preview_lines
            if node.hyper_mode_preview_lines is None
            else node.hyper_mode_preview_lines
        )
        full_max_lines = (
            DEFAULT_HYPER_MODE_FULL_MAX_LINES
            if node.hyper_mode_full_max_lines is None
            else node.hyper_mode_full_max_lines
        )
        return hyper_mode_prompt(
            existing_docs,
            original_prompt,
            preview_lines=preview_lines,
            allow_followup_reads=allow_followup_reads,
            full_docs=node.hyper_mode_full_docs,
            full_max_lines=full_max_lines,
        )

    def _compose_profile_context(
        self,
        node: Node,
        original_prompt: str,
        state: Any,
        selection: LLMSelection,
    ) -> tuple[str, list[str]]:
        """Apply the node profile once, independently of provider/attempt kind."""
        if not node.context_profile:
            return original_prompt, []
        result = compose_context_profile(
            node.context_profile,
            self._work_dir,
            original_prompt,
            last_approval_message=getattr(state, "last_approval_message", None),
            base_commit=getattr(state, "base_commit", None),
        )
        print(
            ui.dim(
                f"  Context profile {node.context_profile}: "
                f"{len(result.loaded_paths)} recortes, {len(result.context)} chars"
            )
        )
        self._pending_llm_trace_attributes = {
            "context_profile": node.context_profile,
            "context_chars": len(result.context),
            "context_paths": list(result.loaded_paths),
            "context_truncated": result.truncated,
        }
        deny_paths = (
            list(result.deny_read_paths)
            if selection.engine == "opencode"
            else []
        )
        return result.prompt, deny_paths

    def _start_llm_log(
        self,
        state: Any,
        node_id: str,
        phase: str,
        *,
        engine: str | None = None,
        selection: LLMSelection | None = None,
    ) -> str:
        """Registra no estado o log ativo para a delegação corrente."""
        node = self.graph.nodes.get(node_id)
        episode = self._reserve_llm_episode_call(state, node) if node is not None else None
        effective_engine = selection.engine if selection is not None else engine
        log_path = self._build_llm_log_path(
            node_id,
            phase,
            engine=effective_engine,
        )
        rel = self._display_path(log_path)
        state.active_llm_log = rel
        state.last_llm_log = rel
        attributes: dict[str, Any] = {
            "engine": selection.engine if selection is not None else effective_engine,
            "model": selection.model if selection is not None else None,
            "effort": selection.effort if selection is not None else None,
            "provenance": (
                dict(selection.provenance) if selection is not None else {}
            ),
            "resolution": (
                list(selection.resolution) if selection is not None else []
            ),
            "log_path": rel,
            "episode_key": node.llm_episode if node is not None else None,
            "episode_ordinal": episode.get("ordinal") if episode else None,
            "episode_call": episode.get("calls") if episode else None,
            **self._pending_llm_trace_attributes,
        }
        self._pending_llm_trace_attributes = {}
        llm_ordinal = self.trace.next_ordinal("llm", node_id)
        llm_span = self.trace.begin_span(
            category="llm",
            name=phase,
            node_id=node_id,
            parent_span_id=(
                self._active_node_trace.span_id
                if self._active_node_trace is not None
                else None
            ),
            attempt_id=self._active_node_attempt_id,
            invocation_id=f"{node_id}:llm:{llm_ordinal}",
            ordinal=llm_ordinal,
            attributes=attributes,
        )
        self._active_llm_traces[rel] = llm_span
        if node is not None and node.llm_episode:
            self._active_llm_episodes[rel] = node.llm_episode
        print(f"  LLM log: {rel}")
        return str(log_path)

    def _reserve_llm_episode_call(self, state: Any, node: Node) -> dict[str, Any] | None:
        key = node.llm_episode
        if not key:
            return None
        record = state.llm_episodes.get(key)
        if not isinstance(record, dict):
            record = {
                "ordinal": 1,
                "calls": 0,
                "consumed_seconds": 0.0,
                "last_reason": "initial",
            }
            state.llm_episodes[key] = record
        calls = int(record.get("calls", 0) or 0)
        consumed = float(record.get("consumed_seconds", 0.0) or 0.0)
        exhausted_reason: str | None = None
        if node.llm_episode_max_calls is not None and calls >= node.llm_episode_max_calls:
            exhausted_reason = f"limite de {node.llm_episode_max_calls} chamada(s)"
        if (
            node.llm_episode_budget_seconds is not None
            and consumed >= node.llm_episode_budget_seconds
        ):
            exhausted_reason = (
                f"orçamento de {node.llm_episode_budget_seconds}s "
                f"(consumidos {consumed:.1f}s)"
            )
        if exhausted_reason:
            try:
                status = subprocess.run(
                    ["git", "status", "--short"],
                    cwd=self._work_dir,
                    capture_output=True,
                    text=True,
                    timeout=20,
                    check=False,
                )
                status_lines = status.stdout.splitlines()
            except (OSError, subprocess.TimeoutExpired):
                status_lines = []
            changed_paths = [
                line[3:].strip()
                for line in status_lines
                if len(line) > 3 and line[3:].strip()
            ][:100]
            record["checkpoint"] = {
                "created_at": datetime.now(timezone.utc).isoformat(),
                "node_id": node.id,
                "changed_paths": changed_paths,
                "truncated": len(status_lines) > len(changed_paths),
            }
            reason = (
                f"Orçamento cumulativo do episódio LLM '{key}' esgotado: "
                f"{exhausted_reason}. Diff e artefatos foram preservados; "
                "revise antes de iniciar um novo episódio."
            )
            self.state_mgr.block(reason)
            raise LLMEpisodeBudgetExceeded(reason)
        record["calls"] = calls + 1
        record["last_node"] = node.id
        return record

    def _effective_llm_timeout(self, node: Node) -> int | None:
        timeout = node.llm_timeout_seconds
        if not node.llm_episode or node.llm_episode_budget_seconds is None:
            return timeout
        state = self.state_mgr.state
        record = state.llm_episodes.get(node.llm_episode, {})
        consumed = float(record.get("consumed_seconds", 0.0) or 0.0)
        remaining = max(1, int(node.llm_episode_budget_seconds - consumed))
        return min(timeout, remaining) if timeout is not None else remaining

    def _restart_llm_episode(self, state: Any, key: str, reason: str) -> None:
        previous = state.llm_episodes.get(key, {})
        ordinal = int(previous.get("ordinal", 0) or 0) + 1
        state.llm_episodes[key] = {
            "ordinal": ordinal,
            "calls": 0,
            "consumed_seconds": 0.0,
            "last_reason": reason,
        }
        self.state_mgr.save()

    def _start_delegation_attempt(
        self,
        state: Any,
        node: Node,
        phase: str,
    ) -> tuple[LLMSelection, str]:
        """Capture one provider bundle, then create its matching active log."""
        selection = self._capture_delegation_llm_selection(state, node=node)
        log_path = self._start_llm_log(
            state,
            node.id,
            phase,
            engine=selection.engine,
            selection=selection,
        )
        self.state_mgr.save()
        return selection, log_path

    def _clear_active_llm_log(self, state: Any) -> None:
        """Limpa referência ao log ativo após a conclusão do subprocesso."""
        active = getattr(state, "active_llm_log", None)
        if active:
            span = self._active_llm_traces.pop(str(active), None)
            if span is not None:
                episode_key = self._active_llm_episodes.pop(str(active), None)
                if episode_key:
                    elapsed = max(
                        0.0,
                        (time.monotonic_ns() - span.started_monotonic_ns) / 1_000_000_000,
                    )
                    record = state.llm_episodes.get(episode_key)
                    if isinstance(record, dict):
                        record["consumed_seconds"] = round(
                            float(record.get("consumed_seconds", 0.0) or 0.0)
                            + elapsed,
                            3,
                        )
                span.finish(status="returned")
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
        """Resolve snapshot_path relativo ao diretório externo de state do ciclo."""
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

    def _verify_commit_hooks(self) -> bool:
        """Resolve the process-scoped Git policy; legacy processes default on."""
        return verify_hooks_from_process_meta(self.graph.meta)

    def merge_on_close(self, strategy: str, paths: list[str] | None = None) -> bool:
        """Fecha o ciclo com um span durável e arquiva o relatório final.

        O processo pode terminar horas antes do ``ft close``. Por isso o close
        é um span de topo próprio: o relatório consegue medir o wall time real
        sem manter artificialmente aberto o span de execução do grafo.
        """
        from ft.engine import paths as _engine_paths

        if strategy == "none":
            return True

        work_root = Path(self.project_root)
        state = self.state_mgr.load()
        worktree = self._detect_worktree()
        cycle_id = (
            work_root.name
            if _engine_paths.is_worktree_path(work_root)
            else getattr(state, "current_cycle", "cycle-01")
        )
        target_root = worktree[1] if worktree else work_root
        if not worktree and _engine_paths.is_worktree_path(work_root):
            candidate = Path.cwd().resolve()
            if candidate != work_root.resolve():
                target_root = candidate

        ordinal = self.trace.next_ordinal("close", cycle_id)
        close_span = self.trace.begin_span(
            category="close",
            name=f"merge:{strategy}",
            node_id=cycle_id,
            invocation_id=f"{cycle_id}:close:{ordinal}",
            ordinal=ordinal,
            attributes={"strategy": strategy},
        )
        try:
            success = self._merge_on_close_impl(strategy, paths)
        except BaseException as exc:
            close_span.finish(
                status="error",
                result=type(exc).__name__,
            )
            raise

        close_span.finish(
            status="ok" if success else "error",
            result="merged" if success else "merge_failed",
        )
        if success:
            # Em execuções normais o span run já termina no end node. Um close
            # forçado ou retomado pode encontrar spans órfãos; feche-os para o
            # relatório arquivado não permanecer artificialmente "active".
            self.trace.finish_open_spans(
                category="run",
                status="ok",
                result="cycle_closed",
            )
            self.trace.finish_open_spans(
                status="interrupted",
                result="cycle_closed",
            )
        if success and not self._finalize_close_report(
            cycle_id=cycle_id,
            work_root=work_root,
            target_root=target_root,
            commit=strategy == "full",
        ):
            return False
        return success

    def _finalize_close_report(
        self,
        *,
        cycle_id: str,
        work_root: Path,
        target_root: Path,
        commit: bool,
    ) -> bool:
        """Regera o report após o merge sem incluir logs crus no Git."""
        import subprocess as _sp

        from ft.engine.trace import write_run_report

        destination = target_root / ".ft" / "cycles" / cycle_id / "run-report.json"
        # Um close selective pode deliberadamente excluir o histórico.
        if not destination.exists():
            return True
        try:
            write_run_report(
                self.trace.path,
                destination,
                run_id=cycle_id,
                log_root=work_root,
            )
        except OSError as exc:
            print(ui.fail(f"Relatório final do close falhou: {exc}"))
            return False

        if not commit or not (target_root / ".git").exists():
            return True

        relative = destination.relative_to(target_root).as_posix()
        from ft.engine.layout import (
            _assert_no_exclusive_startup,
            _manifest_write_lock,
            _suspend_for_exclusive_project_write,
        )

        verify_hooks = self._verify_commit_hooks()
        with _manifest_write_lock(target_root):
            _assert_no_exclusive_startup(target_root)
            with _suspend_for_exclusive_project_write(
                target_root,
                reason=f"ft close report {cycle_id}",
            ):
                added = _sp.run(
                    ["git", "add", "--", relative],
                    cwd=target_root,
                    capture_output=True,
                    text=True,
                )
                if added.returncode != 0:
                    print(ui.fail(
                        "Relatório final do close não pôde ser preparado — "
                        + (added.stderr.strip() or added.stdout.strip())[:300]
                    ))
                    return False
                changed = _sp.run(
                    ["git", "diff", "--cached", "--quiet", "--", relative],
                    cwd=target_root,
                    capture_output=True,
                    text=True,
                )
                if changed.returncode == 0:
                    return True
                if changed.returncode != 1:
                    print(ui.fail("Não foi possível inspecionar o relatório final do close"))
                    return False
                command = [
                    *git_command_prefix(verify_hooks),
                    "commit",
                ]
                if not verify_hooks:
                    command.extend(["--no-verify", "--no-gpg-sign"])
                command.extend(
                    [
                        "--only",
                        "-m",
                        f"chore(ft): finalize report {cycle_id}",
                        "--",
                        relative,
                    ]
                )
                completed = _sp.run(
                    command,
                    cwd=target_root,
                    capture_output=True,
                    text=True,
                )
                if completed.returncode != 0:
                    print(ui.fail(
                        "Commit do relatório final do close falhou — "
                        + (completed.stderr.strip() or completed.stdout.strip())[:300]
                    ))
                    return False
        return True

    def _merge_on_close_impl(self, strategy: str, paths: list[str] | None = None) -> bool:
        """Merge artefatos do worktree de volta para o repo original.

        strategy:
          "full"      → git merge da branch inteira
          "docs"      → copia docs/ e histórico em .ft/cycles/ (não processos)
          "selective"  → copia apenas os paths informados
          "none"      → nada
        paths: lista de paths para modo selective (ex: ["docs/", "project/backend/"])

        Retorna True se o merge foi concluído (ou intencionalmente não havia nada
        a fazer); False se falhou — o chamador NÃO deve destruir worktree/branch.
        """
        import subprocess as _sp
        from ft.engine import paths as _engine_paths

        work_root = Path(self.project_root)
        state = self.state_mgr.load()
        wt = self._detect_worktree()
        if wt:
            _, _, active_branch = wt
            expected_branch = getattr(state, "worktree_branch", None)
            if expected_branch and active_branch != expected_branch:
                print(ui.fail(
                    "Merge: branch ativa da worktree diverge da branch fixada "
                    f"no ciclo ({active_branch or '<detached>'} != {expected_branch})"
                ))
                return False
        cycle_id = (
            work_root.name
            if _engine_paths.is_worktree_path(work_root)
            else getattr(state, "current_cycle", "cycle-01")
        )
        try:
            archived = archive_cycle_artifacts(
                work_root,
                cycle_id,
                state=state,
                graph_meta=self.graph.meta,
            )
        except (OSError, ValueError) as exc:
            print(ui.fail(f"Arquivo do ciclo falhou: {exc}"))
            return False

        print(ui.success(f"Histórico: .ft/cycles/{cycle_id}/"))
        if archived.moved:
            print(ui.dim(f"  {len(archived.moved)} artefato(s) de ciclo arquivado(s)"))

        # Full merge opera sobre commits; o fechamento precisa registrar o move
        # docs/ -> .ft/cycles/ antes de integrar a branch.
        committed, detail = auto_commit(
            f"chore(ft): archive {cycle_id}",
            project_root=str(work_root),
            verify_hooks=self._verify_commit_hooks(),
        )
        if not committed:
            print(ui.fail(detail))
            return False

        if not wt:
            # Cycle dir não é git worktree (diretório puro em ~/.ft/worktrees/):
            # merge por cópia — nunca retornar em silêncio.
            return self._merge_by_copy(strategy, paths)
        work, original_root, branch = wt

        if strategy == "full":
            if branch:
                from ft.engine.layout import (
                    _assert_no_exclusive_startup,
                    _manifest_write_lock,
                    _suspend_for_exclusive_project_write,
                )

                verify_hooks = self._verify_commit_hooks()
                merge_command = [
                    *git_command_prefix(verify_hooks),
                    "merge",
                    branch,
                    "--no-edit",
                ]
                if not verify_hooks:
                    merge_command.extend(["--no-verify", "--no-gpg-sign"])
                with _manifest_write_lock(original_root):
                    _assert_no_exclusive_startup(original_root)
                    with _suspend_for_exclusive_project_write(
                        original_root,
                        reason=f"ft close merge {cycle_id}",
                    ):
                        result = _sp.run(
                            merge_command,
                            cwd=original_root,
                            capture_output=True,
                            text=True,
                        )
                if result.returncode == 0:
                    print(ui.success(f"Merge: branch {branch} mergida em {original_root.name}"))
                    return True
                merging = (original_root / ".git" / "MERGE_HEAD").exists()
                if merging:
                    from ft.engine.canonical_merge import resolve_canonical_conflicts

                    with _manifest_write_lock(original_root):
                        with _suspend_for_exclusive_project_write(
                            original_root,
                            reason=f"ft close canonical reconcile {cycle_id}",
                        ):
                            canonical = resolve_canonical_conflicts(original_root)
                            if canonical.success:
                                commit_command = [
                                    *git_command_prefix(verify_hooks),
                                    "commit",
                                    "--no-edit",
                                ]
                                if not verify_hooks:
                                    commit_command.extend(["--no-verify", "--no-gpg-sign"])
                                completed = _sp.run(
                                    commit_command,
                                    cwd=original_root,
                                    capture_output=True,
                                    text=True,
                                )
                                if completed.returncode == 0:
                                    print(ui.success(
                                        "Merge: conflitos canônicos reconciliados "
                                        f"({', '.join(canonical.resolved)})"
                                    ))
                                    return True
                                print(ui.fail(
                                    "Merge: documentos reconciliados, mas o commit "
                                    "de merge falhou — "
                                    + (completed.stderr.strip() or completed.stdout.strip())[:300]
                                ))
                                return False
                            print(ui.warn(
                                "Merge: reconciliação canônica conservadora não se aplica — "
                                f"{canonical.error}"
                            ))
                # git manda conflitos para o STDOUT; stderr costuma vir vazio
                reason = (result.stdout.strip() or result.stderr.strip())[:300]
                print(ui.fail(f"Merge: falha — {reason}"))
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
            requested_paths = ["docs/", ".ft/"]
        elif strategy == "selective" and paths:
            requested_paths = list(paths)
        else:
            return True
        copy_paths, copy_error = _normalized_close_copy_paths(
            requested_paths,
            cycle_id=cycle_id,
        )
        if copy_error:
            print(ui.fail(f"Merge: {copy_error}"))
            return False

        copied_ok, copied = _copy_close_paths(
            work,
            original_root,
            copy_paths,
            label="Merge",
        )
        if not copied_ok:
            return False
        if copied:
            print(ui.success(
                f"Merge: {len(copied)} item(ns) copiado(s) "
                f"para {original_root.name}/"
            ))
        return True

    def _merge_by_copy(self, strategy: str, paths: list[str] | None = None) -> bool:
        """Fallback do merge quando o cycle dir não é git worktree.

        full      → código, docs canônicos e histórico voltam para a raiz
        docs      → apenas docs canônicos e .ft/cycles/
        selective → paths informados, copiados 1:1
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
        if not ((root / ".git").exists() or _paths.project_manifest(root).is_file()):
            print(ui.fail(f"Merge: {root} não parece a raiz de um projeto ft — merge manual necessário"))
            return False
        cycle = work.name  # ex.: cycle-01
        ignore = _shutil.ignore_patterns(
            "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache", ".venv", "*.pyc"
        )
        if strategy == "selective" and paths:
            requested_paths = list(paths)
        else:
            requested_paths = ["docs/", ".ft/"]
            if strategy == "full":
                requested_paths.append("project/")
        copy_paths, copy_error = _normalized_close_copy_paths(
            requested_paths,
            cycle_id=cycle,
        )
        if copy_error:
            print(ui.fail(f"Merge por cópia: {copy_error}"))
            return False
        copied_ok, copied = _copy_close_paths(
            work,
            root,
            copy_paths,
            label="Merge por cópia",
            ignore=ignore,
        )
        if not copied_ok:
            return False

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
        if self._auto_approve and self._bypass_human_gates:
            print(ui.info("Bypass human gates: aplicando on_fail automaticamente"))
            self.apply_fix(gate_msg)

    def _advance_state(self, completed_node: str, next_node: str | None, gate_result: str = "PASS") -> None:
        """Avança o estado após sucesso, resolvendo bloqueios antigos do mesmo node."""
        if self.state_mgr.state.node_status == "blocked":
            self.state_mgr.unblock()
        # A mensagem do stakeholder pertence ao node que acabou de executá-la.
        # Mantê-la até o avanço permite que um retry após interrupção/orfandade
        # reconstrua exatamente o mesmo prompt, sem vazá-la para o próximo node.
        self.state_mgr.state.last_approval_message = None
        self.state_mgr.advance(completed_node, next_node, gate_result)
        state = self.state_mgr.state
        self._mark_unselected_paths_skipped(state, completed_node, next_node)
        if self._refresh_progress_metrics(state):
            self.state_mgr.save()
        self._clear_validator_snapshots(completed_node)





    def _rewind_invalid_tdd_red(self, node: Node, state) -> bool:
        """Volta para RED se um node posterior detecta suite de testes falsa."""
        if node.id not in {"ft.tdd.02.green", "ft.tdd.03.refactor", "gate.tdd"}:
            return False
        red_id = "ft.tdd.01.red"
        if red_id not in state.completed_nodes or red_id not in self.graph.nodes:
            return False

        work_root = Path(self._work_dir)
        semantic_detail = self._game_product_admin_test_detail(work_root)
        tests_dir = "src/tests" if (work_root / "src" / "tests").exists() else "project/tests"
        passed, quality_detail = val.pytest_red_quality(
            tests_dir=tests_dir,
            project_root=str(work_root),
        )
        if passed and not semantic_detail:
            return False
        detail = semantic_detail or quality_detail

        print(ui.warn(f"TDD RED inválido detectado antes de {node.id}: {detail}"))
        print(ui.info("Voltando para ft.tdd.01.red para refazer a suite de testes"))

        first_invalid = state.completed_nodes.index(red_id)
        for completed in state.completed_nodes[first_invalid:]:
            state.gate_log.pop(completed, None)
            self._clear_validator_snapshots(completed)
        state.completed_nodes = state.completed_nodes[:first_invalid]
        state.artifacts.pop("tests", None)
        self._remove_node_outputs_from_worktree(red_id)
        state.current_node = red_id
        state.node_status = "ready"
        state.blocked_reason = None
        state.pending_approval = None
        state.active_llm_log = None
        state.metrics["steps_completed"] = len(state.completed_nodes)
        self.state_mgr.save()
        self._log_activity(
            node.id,
            node.title,
            node.type,
            "REWIND",
            f"red inválido: {detail}",
            sprint=node.sprint or None,
        )
        return True

    def _fire_hooks(self, event: str) -> bool:
        """Dispara hooks para um evento. Retorna True se todos passaram (ou nenhum)."""
        results = run_hooks(
            event,
            self.project_root,
            self._environment,
            process_path=self.process_path,
        )
        if not results:
            return True
        return hooks_all_passed(results)

    def _mark_node_start(self, node_id: str):
        """Registra o instante de início de um node (para cálculo de duração)."""
        self._ensure_run_trace()
        # A tentativa anterior pode ter sido interrompida por SIGINT/SIGKILL. O
        # journal é a fonte durável dos ordinais; contadores em memória servem
        # somente para renderizar o log Markdown legado.
        self.trace.finish_open_spans(
            category="llm_provider",
            node_id=node_id,
            status="interrupted",
            result="runner_restarted",
        )
        self.trace.finish_open_spans(
            category="llm",
            node_id=node_id,
            status="interrupted",
            result="runner_restarted",
        )
        self.trace.finish_open_spans(
            category="node",
            node_id=node_id,
            status="interrupted",
            result="runner_restarted",
        )
        ordinal = self.trace.next_ordinal("node", node_id)
        self._node_start_times[node_id] = datetime.now()
        self._node_attempts[node_id] = ordinal
        node = self.graph.get_node(node_id)
        attempt_id = f"{node_id}:{ordinal}"
        self._active_node_attempt_id = attempt_id
        self._active_node_trace = self.trace.begin_span(
            category="node",
            name=node.title,
            node_id=node_id,
            parent_span_id=self._run_trace_id,
            attempt_id=attempt_id,
            invocation_id=attempt_id,
            ordinal=ordinal,
            attributes={
                "node_type": node.type,
                "executor": node.executor,
                "sprint": node.sprint,
            },
        )
        self._active_node_trace_id = self._active_node_trace.span_id

    def _ensure_run_trace(self) -> str:
        """Retoma ou inicia o span raiz do ciclo sem depender da instância Python."""
        if self._run_trace_id is not None:
            return self._run_trace_id
        open_runs = self.trace.open_span_ids(category="run")
        if open_runs:
            self._run_trace_id = open_runs[-1]
            return self._run_trace_id
        ordinal = self.trace.next_ordinal("run", self.trace.run_id)
        span = self.trace.begin_span(
            category="run",
            name="cycle",
            node_id=self.trace.run_id,
            invocation_id=f"{self.trace.run_id}:run:{ordinal}",
            ordinal=ordinal,
            attributes={
                "project": Path(self.project_root).name,
                "process": self.graph.meta.get("id"),
                "process_version": self.graph.meta.get("version"),
            },
        )
        self._run_trace_id = span.span_id
        return self._run_trace_id

    def _finish_run_trace(self, *, status: str, result: str) -> None:
        self._ensure_run_trace()
        if self._run_trace_id is not None:
            self.trace.finish_open_span(
                self._run_trace_id,
                status=status,
                result=result,
            )

    def _start_human_wait(self, node: Node, reason: str) -> None:
        if self.trace.open_span_ids(category="human", node_id=node.id):
            return
        ordinal = self.trace.next_ordinal("human", node.id)
        self.trace.begin_span(
            category="human",
            name="awaiting_decision",
            node_id=node.id,
            parent_span_id=self._active_node_trace_id or self._run_trace_id,
            attempt_id=self._active_node_attempt_id,
            invocation_id=f"{node.id}:human:{ordinal}",
            ordinal=ordinal,
            attributes={"reason": reason},
        )

    def _finish_human_wait(self, node_id: str, result: str) -> None:
        self.trace.finish_open_spans(
            category="human",
            node_id=node_id,
            status="ok" if result == "approved" else "error",
            result=result,
        )

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

        if self._active_node_trace is not None:
            normalized = result.strip().lower().replace(" ", "_")
            failed = normalized in {
                "blocked",
                "failed",
                "rate_limited",
                "rejected",
            }
            self._active_node_trace.finish(
                status="error" if failed else "ok",
                result=result,
                attributes={"summary": summary},
            )
            self._active_node_trace = None
            self._active_node_trace_id = None
            self._active_node_attempt_id = None

    def _finish_active_node_trace(
        self,
        *,
        status: str,
        result: str,
        summary: str | None = None,
    ) -> None:
        """Fecha uma tentativa ativa sem duplicar a linha do log Markdown."""
        if self._active_node_trace is None:
            return
        self._active_node_trace.finish(
            status=status,
            result=result,
            attributes={"summary": summary} if summary else None,
        )
        self._active_node_trace = None
        self._active_node_trace_id = None
        self._active_node_attempt_id = None

    def init_state(self):
        """Inicializa estado a partir do grafo."""
        self._reset_validator_snapshots()
        first = self.graph.first_node()
        total = len([n for n in self.graph.nodes.values() if n.type != "end"])
        cycle_id = "cycle-01"
        state_path = self.state_mgr.path.resolve()
        if paths.is_worktree_path(state_path):
            cycle_id = state_path.parent.parent.name
        process_file = Path(self.process_path).resolve()
        root = Path(self.project_root).resolve()
        try:
            selected_process_path = process_file.relative_to(root).as_posix()
        except ValueError:
            selected_process_path = str(process_file)
        execution_policy = self.graph.meta.get("execution_policy", {})
        template_id = (
            execution_policy.get("template")
            if isinstance(execution_policy, dict)
            else None
        ) or self.graph.meta.get("id")
        base_commit = None
        worktree_branch = None
        try:
            base_result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=20,
            )
            if base_result.returncode == 0:
                base_commit = base_result.stdout.strip() or None
            branch_result = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=20,
            )
            if branch_result.returncode == 0:
                worktree_branch = branch_result.stdout.strip() or None
        except (OSError, subprocess.TimeoutExpired):
            pass
        initial_defaults = self._read_live_llm_defaults()
        initial_llm = self._resolve_llm_selection(
            manifest_defaults=initial_defaults,
            manifest_is_active=True,
        )
        self.state_mgr.init_from_graph(
            self.graph.meta,
            first.id,
            total,
            llm_engine=initial_llm.engine,
            llm_model=initial_llm.model,
            llm_effort=initial_llm.effort,
            llm_defaults_digest=self._llm_defaults_digest(initial_defaults),
            current_cycle=cycle_id,
            cycle_objective=self._cycle_objective_from_input(),
            process_path=selected_process_path,
            process_digest=process_digest(process_file),
            process_immutable=(
                isinstance(execution_policy, dict)
                and execution_policy.get("runtime_source") == "local_only"
            ),
            template_id=str(template_id) if template_id else None,
            base_commit=base_commit,
            worktree_branch=worktree_branch,
        )
        self._ensure_run_trace()
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

    def recover_orphaned_delegation(self, mode: str = "step") -> bool:
        """Finalize an interrupted LLM node when its validators already pass.

        A delegated node can outlive the orchestrator process when the CLI is
        interrupted after the LLM wrote valid artifacts but before the normal
        commit/advance tail ran. Retrying that state by delegating again wastes
        a full LLM turn and can overwrite a valid fix. Recovery is deliberately
        strict: it only applies to a ``delegated`` state whose lock PID is no
        longer alive, and it uses the node's ordinary validators before making
        any progress decision.

        Returns True when the orphan was finalized. A failed validation resets
        the node to ``ready`` and returns False so the caller can execute the
        normal delegation path.
        """
        state = self.state_mgr.load()
        if state.node_status != "delegated" or not state.current_node:
            return False

        previous_lock = getattr(self.state_mgr, "_previous_claim_lock", None)
        was_claimed = bool(getattr(self.state_mgr, "_claim_performed", False))
        lock = previous_lock if isinstance(previous_lock, dict) else (
            {} if was_claimed else
            state._lock if isinstance(state._lock, dict) else {}
        )
        pid = lock.get("pid")
        if pid:
            from ft.engine.state import lock_owner_is_alive

            if was_claimed:
                if lock_owner_is_alive(lock):
                    return False
            else:
                try:
                    pid_alive = self.state_mgr._is_pid_alive(int(pid))
                except (TypeError, ValueError):
                    pid_alive = False
                if pid_alive and lock_owner_is_alive(lock):
                    return False

        node_id = state.current_node
        if node_id not in self.graph.nodes:
            return False
        node = self.graph.get_node(node_id)
        self._auto_approve = mode == "mvp"

        print(ui.warn(
            f"Delegação órfã detectada em {node_id} — validando artefatos existentes antes de redelegar"
        ))
        self.trace.finish_open_spans(
            category="llm_provider",
            node_id=node_id,
            status="interrupted",
            result="orphan_recovery",
        )
        self.trace.finish_open_spans(
            category="llm",
            node_id=node_id,
            status="interrupted",
            result="orphan_recovery",
        )
        self.trace.finish_open_spans(
            category="node",
            node_id=node_id,
            status="interrupted",
            result="orphan_recovery",
        )
        state.active_llm_log = None
        self.state_mgr.save()

        validation = self._run_validators(node, resume=True)
        has_resume_command = any(
            isinstance(spec.get("command_succeeds"), dict)
            and "resume_command" in spec["command_succeeds"]
            for spec in node.validators
        )
        if not validation.passed and has_resume_command:
            self._print_validation(validation)
            print(ui.warn(
                "Validação leve de retomada falhou — executando a validação "
                "normal uma única vez antes de redelegar"
            ))
            validation = self._run_validators(node)
        self._print_validation(validation)
        if not validation.passed:
            state = self.state_mgr.load()
            state.node_status = "ready"
            state.active_llm_log = None
            state.blocked_reason = None
            self.state_mgr.save()
            print(ui.warn("Artefatos órfãos ainda falham — retomando delegação normal"))
            return False

        for output_path in node.outputs:
            self.state_mgr.record_artifact(Path(output_path).stem, output_path)
        self._maybe_auto_commit(node)
        self._record_node_summary(
            node,
            "NODE_SUMMARY:\n"
            "- fiz: recuperação de delegação interrompida\n"
            "- verificado: validators do node passaram sem nova chamada LLM",
        )

        if node.requires_approval and not self._auto_approve:
            print(ui.awaiting_approval(auto=False))
            self.state_mgr.set_pending_approval(node.id)
            self._start_human_wait(node, "node_requires_approval")
            return True

        next_id = self.graph.resolve_next(node.id)
        self._advance_state(node.id, next_id)
        print(ui.step_pass(next_id, "PASS (artefatos órfãos recuperados)"))
        return True

    def run(self, mode: str = "step"):
        try:
            return self._run_loop(mode)
        except LLMEpisodeBudgetExceeded as exc:
            self._finish_active_node_trace(
                status="error",
                result="BUDGET_EXHAUSTED",
                summary=str(exc),
            )
            print(ui.step_block(str(exc)))
            return None

    def _run_loop(self, mode: str = "step"):
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
        self._ensure_run_trace()

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
            if self._rewind_invalid_tdd_red(node, state):
                state = self.state_mgr.load()
                continue
            if self._rewind_stale_game_acceptance(node, state):
                state = self.state_mgr.load()
                continue

            if node.type == "end":
                usage_summary = summarize_llm_usage(
                    self._llm_log_dir(),
                    default_engine=state.llm_engine,
                    default_model=state.llm_model,
                )
                state.metrics["llm_usage"] = usage_summary
                state.metrics["tokens_used"] = usage_summary["totals"].get("total_all_tokens", 0)
                self.state_mgr.save()
                print(ui.process_complete(
                    state.metrics['steps_completed'], state.metrics['steps_total'],
                ))
                for line in format_llm_usage_lines(usage_summary):
                    print(ui.dim(line))
                # Commitar conhecimento produzido pelo ciclo
                ok, detail = commit_knowledge(
                    self.project_root,
                    label="pós-run — ciclo completo",
                    verify_hooks=self._verify_commit_hooks(),
                )
                print(ui.dim(detail))
                # Merge artefatos de volta para o repo original
                self._merge_on_end()
                self._fire_hooks("on_deliver")
                self._advance_state(node_id, None)
                self._finish_run_trace(status="ok", result="completed")
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
                review_state = self.state_mgr.load()
                if review_state.node_status == "blocked":
                    review_result = "BLOCKED"
                    review_summary = review_state.blocked_reason or "review bloqueada"
                elif review_state.node_status in ("awaiting_approval", "pending_fix"):
                    review_result = "AWAITING_APPROVAL"
                    review_summary = "review aguardando decisão"
                else:
                    review_result = "PASS"
                    review_summary = f"→ {review_state.current_node or 'fim'}"
                self._log_activity(
                    node_id,
                    node.title,
                    node.type,
                    review_result,
                    review_summary,
                    sprint=node_sprint,
                )
                if mode == "step":
                    break
                state = review_state
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
                    if self._maybe_rewind_visual_mismatch(blocked_reason):
                        continue
                    if self._maybe_rewind_gameplay_mismatch(blocked_reason):
                        continue
                    fix_count = self._auto_fix_counts.get(node_id, 0)
                    if mode == "mvp" and not _should_skip_auto_fix(blocked_reason) and fix_count < self._max_auto_fix:
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

            # Parallel group — fan-out/fan-in (opt-in via ft run/continue --parallel)
            if node.parallel_group and self.state_mgr.state.parallel_enabled:
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
                if self._maybe_rewind_visual_mismatch(blocked_reason):
                    continue
                if self._maybe_rewind_gameplay_mismatch(blocked_reason):
                    continue
                fix_count = self._auto_fix_counts.get(node_id, 0)
                if mode == "mvp" and not _should_skip_auto_fix(blocked_reason) and fix_count < self._max_auto_fix:
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
                    self._start_human_wait(node, "node_requires_approval")
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
            if self._active_node_trace is not None:
                trace_status = "error" if state.node_status == "blocked" else "ok"
                trace_result = (
                    "BLOCKED"
                    if state.node_status == "blocked"
                    else "AWAITING_APPROVAL"
                    if state.node_status == "awaiting_approval"
                    else "STEP_COMPLETE"
                )
                self._finish_active_node_trace(
                    status=trace_status,
                    result=trace_result,
                    summary=state.blocked_reason,
                )
            if state.node_status not in ("blocked", "awaiting_approval", "done", "completed"):
                print(ui.dim("  → ft continue   para continuar o próximo step"))

    _MAX_STREAM_RETRIES = 2

    def _delegate_with_stream_retry(self, **delegate_kwargs):
        """delegate_to_llm com retry automático quando o processo morre sem veredito.

        Stream interrompida, crash ou timeout do CLI (result.died) é falha de
        infraestrutura, não de conteúdo: retenta a mesma delegação até
        _MAX_STREAM_RETRIES vezes extras antes de devolver a falha. O trabalho
        parcial permanece no working tree, então cada tentativa continua de
        onde a anterior parou.
        """
        attempt = 0
        configured_timeout = delegate_kwargs.get("llm_timeout_seconds")
        deadline = (
            time.monotonic() + float(configured_timeout)
            if isinstance(configured_timeout, (int, float))
            and not isinstance(configured_timeout, bool)
            and configured_timeout > 0
            else None
        )
        log_path = delegate_kwargs.get("log_path")
        active_parent = (
            self._active_llm_traces.get(self._display_path(Path(log_path)))
            if isinstance(log_path, (str, Path))
            else None
        )
        while True:
            if deadline is not None:
                remaining = math.ceil(deadline - time.monotonic())
                if remaining <= 0:
                    return DelegateResult(
                        success=False,
                        output="deadline cumulativo da invocação LLM esgotado",
                        files_created=[],
                        files_modified=[],
                        died=True,
                    )
                delegate_kwargs["llm_timeout_seconds"] = max(1, remaining)
            provider_span: TraceSpan | None = None
            if active_parent is not None:
                provider_span = self.trace.begin_span(
                    category="llm_provider",
                    name="provider_attempt",
                    node_id=self.state_mgr.state.current_node,
                    parent_span_id=active_parent.span_id,
                    attempt_id=self._active_node_attempt_id,
                    invocation_id=f"{active_parent.span_id}:provider:{attempt + 1}",
                    ordinal=attempt + 1,
                    attributes={"stream_retry": attempt},
                )
            result = delegate_to_llm(**delegate_kwargs)
            if provider_span is not None:
                provider_span.finish(
                    status="ok" if result.success else "error",
                    result="success" if result.success else "died" if result.died else "failed",
                )
            if result.success or not getattr(result, "died", False):
                return result
            if attempt >= self._MAX_STREAM_RETRIES:
                return result
            attempt += 1
            print(ui.warn(
                "Delegação morreu sem veredito (stream/crash/timeout) — "
                f"retry automático {attempt}/{self._MAX_STREAM_RETRIES}"
            ))

    def _run_llm_step(self, node: Node):
        """Wrapper: garante env_teardown em qualquer saída (PASS, retry, block)."""
        try:
            return self._run_llm_step_inner(node)
        finally:
            if node.env_teardown:
                self._run_env_teardown(node)

    def _build_llm_task_context(
        self,
        node: Node,
        state: Any,
        selection: LLMSelection,
        *,
        allow_compact: bool = True,
    ) -> tuple[str, str | None, list[str]]:
        """Build provider-specific prompt context for one delegated attempt."""
        state_dict = {**state.__dict__, "_project_root": self.project_root}
        task_prompt = build_task_prompt(node, state_dict)
        # Modo autônomo: com --bypass-human-gates não há humano para responder
        # perguntas. A LLM decide no lugar do humano — responde as perguntas com
        # o default mais razoável, documenta a decisão, e nunca deixa o fluxo
        # bloqueado aguardando esclarecimento (senão nós de clareza re-loopam).
        if getattr(self, "_bypass_human_gates", False):
            task_prompt = (
                "MODO AUTÔNOMO (--bypass-human-gates ativo): NÃO há humano para "
                "responder perguntas neste run. Se sua tarefa levantaria perguntas "
                "ao stakeholder, RESPONDA VOCÊ MESMO com o default mais razoável e "
                "documente a decisão + justificativa. NUNCA deixe "
                "clarification_status: required nem bloqueie o fluxo aguardando "
                "decisão humana — use 'clear' após registrar suas escolhas.\n\n"
                f"{task_prompt}"
            )
        process_relative = self._project_relative_process_path()
        compact_bundle = (
            _opencode_compact_bundle_prompt(node, process_relative)
            if (
                allow_compact
                and selection.engine == "opencode"
                and _opencode_compact_bundles_enabled()
            )
            else None
        )
        if compact_bundle:
            task_prompt = compact_bundle
            print(ui.dim("  OpenCode: prompt compacto de file bundle"))

        if node.context_profile:
            task_prompt, deny_paths = self._compose_profile_context(
                node,
                task_prompt,
                state,
                selection,
            )
            # Context profiles replace HyperMode, process KB and cycle memory.
            return task_prompt, compact_bundle, deny_paths

        approval_msg = self.state_mgr.state.last_approval_message
        if approval_msg:
            task_prompt = (
                f"MENSAGEM DO STAKEHOLDER (aprovação do gate anterior):\n{approval_msg}\n\n"
                f"Leve esta mensagem em conta ao executar sua tarefa.\n\n"
                f"{task_prompt}"
            )
            print(ui.info("Contexto: mensagem do stakeholder injetada no prompt"))

        opencode_code_node = (
            selection.engine == "opencode"
            and node.type in {"build", "test_red", "test_green", "refactor"}
        )
        if (
            not node.context_profile
            and (node.type in ("discovery", "document", "retro") or opencode_code_node)
        ):
            existing = self._filter_no_pre_seed_docs(
                node,
                self._scan_hyper_mode_docs(node),
            )
            if existing:
                is_opencode = selection.engine == "opencode"
                task_prompt = self._hyper_mode_prompt_for_node(
                    node,
                    existing,
                    task_prompt,
                    default_preview_lines=30 if is_opencode else 60,
                    allow_followup_reads=opencode_code_node or not is_opencode,
                )
                label = "Hyper-mode code" if opencode_code_node else "Hyper-mode"
                print(f"  {label}: {len(existing)} docs existentes carregados")

        if node.type in ("build", "refactor", "retro") and not compact_bundle:
            interface_type = (
                state_dict.get("artifacts", {}).get("interface_type")
                or state_dict.get("interface_type")
            )
            lessons = (
                scan_kb_lessons(self._kb_path, interface_type=interface_type)
                if self._kb_path
                else []
            )
            if lessons:
                task_prompt = kb_lessons_prompt(lessons, task_prompt)
                print("  KB-mode: lições de runs anteriores injetadas")

        if opencode_code_node:
            if self._cycle_memory_path().exists():
                print(ui.dim("  cycle_memory: omitida para OpenCode em node de codigo"))
        else:
            task_prompt = self._inject_cycle_memory(task_prompt)

        return task_prompt, compact_bundle, []

    def _run_llm_step_inner(self, node: Node):
        """Delega ao LLM, valida resultado, avanca ou retenta."""
        state = self.state_mgr.state
        self._prepare_validator_snapshots(node)

        # Pre-seed check: se todos os outputs já existem e os validators passam,
        # pula delegação ao LLM — o artefato foi fornecido externamente (ex: --hipotese).
        # NÃO aplica a build/review nodes: um artefato de passo anterior não conta
        # como implementação/revisão atual depois que o código mudou.
        # NÃO aplica se node tiver no_pre_seed: true — node deve sempre rodar (ex: plano de voo).
        code_like_types = {"build", "review", "test_red", "test_green", "refactor"}
        if node.outputs and node.type not in code_like_types and not self._validator_snapshot_specs(node) and not getattr(node, "no_pre_seed", False):
            all_exist = all(
                (Path(self.project_root) / o).exists() for o in node.outputs
            )
            if all_exist:
                validation = self._run_validators(node)
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
        llm_selection = self._capture_delegation_llm_selection(state, node=node)
        effective_engine = llm_selection.engine
        last_attempt_engine = effective_engine
        task_prompt, opencode_compact_bundle, opencode_deny_read_paths = (
            self._build_llm_task_context(node, state, llm_selection)
        )

        # Determinar paths permitidos
        allowed = self._resolve_allowed_paths(node)

        if (
            effective_engine == "opencode"
            and node.id == "ft.smoke.01.run"
            and self._is_opencode_game_product(Path(self._work_dir))
        ):
            print(ui.info("OpenCode preflight: normalizando delivery stack de jogo antes do smoke"))
            self._write_opencode_delivery_stack(Path(self._work_dir))

        # env_setup: comandos determinísticos antes da delegação (não consome turns)
        if node.env_setup:
            if not self._run_env_setup(node):
                self.state_mgr.block(f"env_setup falhou no node {node.id}")
                return

        opencode_options = self._opencode_options_for_node(
            node,
            effective_engine,
            deny_read_paths=opencode_deny_read_paths,
        )
        if self._try_opencode_real_evidence_node(node, effective_engine):
            return
        game_deterministic_nodes = {
            "ft.acceptance.01.cli",
            "ft.e2e.01.browser",
            "ft.e2e.02.screenshots",
            "ft.final.01.visual_check",
        }
        if (
            effective_engine == "opencode"
            and node.id in game_deterministic_nodes
            and self._is_opencode_game_product(Path(self._work_dir))
        ):
            if self._try_opencode_deterministic_node(node, effective_engine, require_opt_in=False):
                return
        if _opencode_deterministic_fallbacks_enabled():
            if self._try_opencode_deterministic_node(node, effective_engine):
                return
        if effective_engine == "opencode" and node.id.startswith("ft.handoff."):
            if self._try_opencode_deterministic_node(node, effective_engine, require_opt_in=False):
                return
        if opencode_compact_bundle:
            if self._try_opencode_compact_bundle_node(node, state, effective_engine, allowed, opencode_options):
                return

        print(ui.info(f"Delegando ao LLM ({effective_engine})..."))
        state.node_status = "delegated"
        state.metrics["llm_calls"] = state.metrics.get("llm_calls", 0) + 1
        log_path = self._start_llm_log(
            state,
            node.id,
            "run",
            engine=llm_selection.engine,
            selection=llm_selection,
        )
        self.state_mgr.save()

        delegate_kwargs: dict = dict(
            task=task_prompt,
            project_root=self._work_dir,
            allowed_paths=allowed,
            llm_engine=effective_engine,
            llm_model=llm_selection.model,
            llm_effort=llm_selection.effort,
            log_path=log_path,
            stream_prefix=self._stream_prefix(effective_engine),
            llm_timeout_seconds=self._effective_llm_timeout(node),
        )
        self._apply_opencode_options(delegate_kwargs, opencode_options)
        if node.max_turns is not None:
            delegate_kwargs["max_turns"] = node.max_turns

        try:
            result = self._delegate_with_stream_retry(**delegate_kwargs)
        finally:
            self._clear_active_llm_log(state)

        if not result.success:
            if getattr(result, "rate_limited", False):
                self.state_mgr.block(
                    f"{RATE_LIMIT_MARKER} API do LLM indisponível (rate limit persistiu "
                    f"após todo o backoff) no node {node.id}"
                )
                return
            validation = self._run_validators(node)
            self._print_validation(validation)
            if self._try_repair_opencode_frontend_scaffold(node, effective_engine, validation):
                return
            if self._try_repair_opencode_frontend_implementation(node, effective_engine, validation):
                return
            if self._try_repair_api_contract(node, effective_engine, validation):
                return
            if self._try_repair_test_data(node, effective_engine, validation):
                return
            if validation.passed:
                print(ui.success("LLM encerrou com erro, mas validadores passaram — aceitando artefatos"))
                for output_path in node.outputs:
                    name = Path(output_path).stem
                    self.state_mgr.record_artifact(name, output_path)
                self._maybe_auto_commit(node)
                self._record_node_summary(node, getattr(result, "output", None) or str(result))

                if node.requires_approval and not self._auto_approve:
                    print(ui.awaiting_approval(auto=self._auto_approve))
                    self.state_mgr.set_pending_approval(node.id)
                    return

                next_id = self.graph.resolve_next(node.id)
                self._advance_state(node.id, next_id)
                print(ui.step_pass(next_id, "PASS (validators ok after LLM error)"))
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
        validation = self._run_validators(node)
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

        if self._try_repair_opencode_frontend_scaffold(node, effective_engine, validation):
            return
        if self._try_repair_opencode_frontend_implementation(node, effective_engine, validation):
            return
        if self._try_repair_api_contract(node, effective_engine, validation):
            return
        if self._try_repair_test_data(node, effective_engine, validation):
            return

        # Retry — com detecção de erro idêntico para early-BLOCKED
        if validation.retryable:
            previous_feedback = validation.feedback or ""
            for retry in range(1, self._max_node_retries + 1):
                current_feedback = validation.feedback or "validação falhou"
                enriched_feedback = self._enrich_validation_feedback(node, current_feedback)
                print(ui.retry(retry, self._max_node_retries))
                print(ui.info(f"Corrigindo automaticamente: {current_feedback}"))

                # Se o erro é idêntico ao da tentativa anterior, parar cedo
                if retry > 1 and current_feedback == previous_feedback:
                    print(ui.fail("Erro idêntico ao da tentativa anterior — bloqueio estrutural detectado"))
                    break

                previous_feedback = current_feedback
                retry_selection, retry_log_path = self._start_delegation_attempt(
                    state,
                    node,
                    f"retry-{retry}",
                )
                last_attempt_engine = retry_selection.engine
                retry_task_prompt, _retry_compact, retry_deny_paths = (
                    self._build_llm_task_context(
                        node,
                        state,
                        retry_selection,
                        allow_compact=False,
                    )
                )
                retry_opencode_options = self._opencode_options_for_node(
                    node,
                    retry_selection.engine,
                    deny_read_paths=retry_deny_paths,
                )
                try:
                    result = delegate_with_feedback(
                        original_task=retry_task_prompt,
                        feedback=enriched_feedback,
                        project_root=self._work_dir,
                        allowed_paths=self._delegate_allowed_paths(allowed),
                        llm_engine=retry_selection.engine,
                        llm_model=retry_selection.model,
                        llm_effort=retry_selection.effort,
                        max_turns=node.max_turns or 50,
                        log_path=retry_log_path,
                        stream_prefix=self._stream_prefix(retry_selection.engine),
                        opencode_deny_read_paths=retry_opencode_options.deny_read_paths,
                        opencode_restrict_tools=retry_opencode_options.restrict_tools,
                        opencode_steps=retry_opencode_options.steps,
                        opencode_deny_edit_tools=retry_opencode_options.deny_edit_tools,
                        opencode_early_success_paths=retry_opencode_options.early_success_paths,
                        opencode_capture_output_path=retry_opencode_options.capture_output_path,
                        llm_timeout_seconds=self._effective_llm_timeout(node),
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

                validation = self._run_validators(node)
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

                if self._try_repair_opencode_frontend_scaffold(node, last_attempt_engine, validation):
                    return
                if self._try_repair_opencode_frontend_implementation(node, last_attempt_engine, validation):
                    return
                if retry >= 1 and self._try_repair_api_contract(node, last_attempt_engine, validation):
                    return
                if retry >= 1 and self._try_repair_test_data(node, last_attempt_engine, validation):
                    return

        # Esgotou retries
        if self._try_repair_opencode_frontend_implementation(node, last_attempt_engine, validation):
            return
        if self._try_repair_api_contract(node, last_attempt_engine, validation):
            return
        if self._try_repair_test_data(node, last_attempt_engine, validation):
            return
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
        self._start_human_wait(node, "human_gate")

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

        allowed = self._resolve_allowed_paths(node)
        llm_selection = self._capture_delegation_llm_selection(state, node=node)
        effective_engine = llm_selection.engine
        opencode_options = self._opencode_options_for_node(node, effective_engine)
        state_dict = {**state.__dict__, "_project_root": self.project_root}
        original_task = build_task_prompt(node, state_dict)
        opencode_code_node = (
            effective_engine == "opencode"
            and node.type in {"build", "test_red", "test_green", "refactor"}
        )
        if (
            not node.context_profile
            and (node.type in ("discovery", "document", "retro") or opencode_code_node)
        ):
            existing = self._filter_no_pre_seed_docs(
                node,
                self._scan_hyper_mode_docs(node),
            )
            if existing:
                is_opencode = effective_engine == "opencode"
                original_task = self._hyper_mode_prompt_for_node(
                    node,
                    existing,
                    original_task,
                    default_preview_lines=30 if is_opencode else 60,
                    allow_followup_reads=opencode_code_node or not is_opencode,
                )
        if opencode_options.capture_output_path:
            fix_instruction = (
                f"Produza novamente o conteudo completo de "
                f"{opencode_options.capture_output_path}, corrigindo especificamente o erro. "
                f"Nao altere arquivos de estado ou de processo. "
                f"Nao responda DONE; retorne apenas o conteudo final do documento."
            )
        else:
            fix_instruction = (
                "Analise o erro, identifique a causa raiz e corrija os arquivos necessários. "
                "Não altere arquivos de estado ou de processo. "
                "Quando terminar, diga DONE."
            )

        prompt = (
            f"O processo travou no node '{node.id}' ({node.title}).\n\n"
            f"TAREFA ORIGINAL DO NODE:\n{original_task}\n\n"
            f"ERRO:\n{self._enrich_validation_feedback(node, blocked_reason)}\n\n"
            f"{history_block}"
            f"{fix_instruction}"
        )
        profile_deny_paths: list[str] = []
        if node.context_profile:
            prompt, profile_deny_paths = self._compose_profile_context(
                node,
                prompt,
                state,
                llm_selection,
            )
            opencode_options = self._opencode_options_for_node(
                node,
                effective_engine,
                deny_read_paths=profile_deny_paths,
            )

        log_path = self._start_llm_log(
            state,
            node.id,
            f"auto-fix-{self._auto_fix_counts.get(node.id, 0) + 1}",
            engine=llm_selection.engine,
            selection=llm_selection,
        )
        # Desbloquear antes de chamar o LLM
        state.node_status = "ready"
        state.blocked_reason = None
        self.state_mgr.save()

        try:
            fix_kwargs: dict = dict(
                task=prompt,
                project_root=self._work_dir,
                allowed_paths=allowed,
                llm_engine=effective_engine,
                llm_model=llm_selection.model,
                llm_effort=llm_selection.effort,
                max_turns=node.max_turns or 50,
                log_path=log_path,
                stream_prefix=self._stream_prefix(effective_engine),
                llm_timeout_seconds=self._effective_llm_timeout(node),
            )
            self._apply_opencode_options(fix_kwargs, opencode_options)
            result = self._delegate_with_stream_retry(**fix_kwargs)
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

        validation = self._run_validators(node)
        self._print_validation(validation)

        if validation.passed:
            print(ui.success("Auto-fix: correção aplicada — continuando"))
            next_id = self.graph.resolve_next(node.id)
            self._advance_state(node.id, next_id)
            return True

        if self._try_repair_opencode_frontend_scaffold(node, effective_engine, validation):
            return True
        if self._try_repair_opencode_frontend_implementation(node, effective_engine, validation):
            return True
        if self._try_repair_test_data(node, effective_engine, validation):
            return True

        print(ui.fail(f"Auto-fix: validators ainda falhando após correção"))
        self.state_mgr.block(f"Auto-fix insuficiente: {validation.feedback}")
        return False

    def _run_gate(self, node: Node):
        """Roda gate — validacao pura sem LLM. Em modo mvp, tenta corrigir via LLM."""
        print(ui.info("Rodando gate..."))
        validation = self._run_validators(node)
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

                gate_fix_selection, log_path = self._start_delegation_attempt(
                    state,
                    node,
                    f"gate-fix-{attempt}",
                )
                gate_fix_options: OpenCodeOptions | None = None
                if node.context_profile:
                    fix_prompt, gate_deny_paths = self._compose_profile_context(
                        node,
                        fix_prompt,
                        state,
                        gate_fix_selection,
                    )
                    gate_fix_options = self._opencode_options_for_node(
                        node,
                        gate_fix_selection.engine,
                        deny_read_paths=gate_deny_paths,
                    )
                try:
                    gate_fix_kwargs: dict = dict(
                        task=fix_prompt,
                        project_root=self._work_dir,
                        allowed_paths=self._delegate_allowed_paths(["src/", "tests/", "docs/", "main.py", "app.py", "server.py", "frontend/"]),
                        llm_engine=gate_fix_selection.engine,
                        llm_model=gate_fix_selection.model,
                        llm_effort=gate_fix_selection.effort,
                        log_path=log_path,
                        stream_prefix=self._stream_prefix(gate_fix_selection.engine),
                        llm_timeout_seconds=node.llm_timeout_seconds,
                    )
                    if gate_fix_options is not None:
                        self._apply_opencode_options(gate_fix_kwargs, gate_fix_options)
                    delegate_to_llm(**gate_fix_kwargs)
                finally:
                    self._clear_active_llm_log(state)

                # Re-validar
                validation = self._run_validators(node)
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
        validation = self._run_validators(node)
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
            verify_hooks=self._verify_commit_hooks(),
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
            branch_value = (
                str(state_dict.get(node.condition))
                if node.condition is not None
                else ""
            )
            episode_key = (node.episode_restart or {}).get(branch_value)
            if episode_key:
                self._restart_llm_episode(
                    state,
                    episode_key,
                    f"decision:{node.id}:{branch_value}",
                )
            ordered = [candidate.id for candidate in self.graph.nodes.values()]
            current_index = ordered.index(node.id)
            target_index = ordered.index(next_id)
            if target_index <= current_index:
                # A branch semântica volta a uma etapa já concluída. Um simples
                # advance manteria implement/review marcados como concluídos e
                # produziria progresso e artefatos incoerentes. Limpe também o
                # valor de roteamento para que uma retomada não reutilize o
                # veredicto anterior antes de uma nova review.
                if node.condition:
                    state.artifacts.pop(node.condition, None)
                    self.state_mgr.save()
                self._rewind_to_node(
                    next_id,
                    f"roteamento {node.id}: {branch_value}",
                )
            else:
                self._advance_state(node.id, next_id)
            chosen = next_id
            print(f"  DECISION: condicao='{node.condition}' → {chosen}")
        else:
            self.state_mgr.block(f"Decision sem branch valido: condicao={node.condition}")
            print(f"  DECISION BLOCK: nenhum branch valido")



    def _read_review_output(self, node: Node) -> str:
        for output_path in node.outputs:
            candidates = [Path(self._work_dir) / output_path, Path(self.project_root) / output_path]
            for full in candidates:
                if full.exists() and full.is_file():
                    return full.read_text(encoding="utf-8", errors="ignore")
        return ""

    def _review_source_mtime(self, node: Node) -> float:
        source_dirs: list[str] = []
        for spec in node.validators:
            cfg = spec.get("ui_criteria_coverage") if isinstance(spec, dict) else None
            if isinstance(cfg, dict) and cfg.get("source_dir"):
                source_dirs.append(str(cfg["source_dir"]))
        source_dirs.extend(["src/frontend/src", "project/frontend/src"])

        mtimes: list[float] = []
        for raw in dict.fromkeys(source_dirs):
            base = Path(self._work_dir) / raw
            if not base.exists():
                continue
            mtimes.extend(p.stat().st_mtime for p in base.rglob("*") if p.is_file())
        return max(mtimes, default=0.0)

    def _review_blocking_evidence_reason(self, node: Node, *, require_fresh: bool = True) -> str | None:
        markers = {
            "overflow": "evidencia visual indica overflow/layout quebrado",
            "blank": "evidencia visual indica tela em branco",
            "white-screen": "evidencia visual indica tela em branco",
            "tela-branca": "evidencia visual indica tela em branco",
            "error": "evidencia visual indica erro renderizado",
            "fail": "evidencia visual indica falha",
        }
        source_mtime = self._review_source_mtime(node) if require_fresh else 0.0
        for output_path in node.outputs:
            if not output_path.endswith("/"):
                continue
            base = Path(self._work_dir) / output_path
            if not base.exists():
                continue
            for path in sorted(p for p in base.rglob("*") if p.is_file()):
                if source_mtime and path.stat().st_mtime < source_mtime:
                    continue
                name = path.name.lower()
                for marker, reason in markers.items():
                    if marker in name and f"no-{marker}" not in name:
                        rel = path.relative_to(self._work_dir)
                        return f"{reason}: {rel}"
        return None

    def _write_review_rejection_report(self, node: Node, reason: str) -> None:
        report_path = next(
            (Path(self._work_dir) / output for output in node.outputs if output.endswith(".md")),
            None,
        )
        if not report_path:
            return
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            "# Expert Review\n\n"
            "Resultado: REJECTED\n\n"
            "## Motivo\n"
            f"- {reason}\n\n"
            "## Acao Esperada\n"
            "- Corrigir a falha apontada e refazer a evidencia visual.\n",
            encoding="utf-8",
        )

    def _build_review_task_context(
        self,
        node: Node,
        selection: LLMSelection,
    ) -> tuple[str, list[str]]:
        """Build review prompt and read restrictions for one provider attempt."""
        task_prompt = build_task_prompt(node, {})
        deny_read_paths: list[str] = []
        if node.context_profile:
            task_prompt, deny_read_paths = self._compose_profile_context(
                node,
                task_prompt,
                self.state_mgr.state,
                selection,
            )
            if selection.engine != "opencode":
                return task_prompt, []
        elif selection.engine == "opencode":
            output_doc_names = {
                Path(output).name
                for output in node.outputs
                if Path(output).parts and Path(output).parts[0] == "docs"
            }
            existing = {
                name: content
                for name, content in self._scan_hyper_mode_docs(node).items()
                if name not in output_doc_names
            }
            if existing:
                task_prompt = self._hyper_mode_prompt_for_node(
                    node,
                    existing,
                    task_prompt,
                    default_preview_lines=25,
                    allow_followup_reads=False,
                )
                deny_read_paths.extend(f"docs/{name}" for name in existing)
                print(f"  Hyper-mode review: {len(existing)} docs existentes carregados")
        else:
            return task_prompt, deny_read_paths

        missing_output_dirs = [
            output
            for output in node.outputs
            if output.endswith("/") and not (Path(self.project_root) / output).exists()
        ]
        for output in missing_output_dirs:
            deny_read_paths.append(output.rstrip("/"))
            deny_read_paths.append(output)
        if missing_output_dirs:
            dirs = ", ".join(missing_output_dirs)
            task_prompt = (
                f"{task_prompt}\n\n"
                "INSTRUCAO OPENCODE REVIEW:\n"
                f"- Estes diretorios de output ainda nao existem: {dirs}.\n"
                "- NAO tente le-los em loop. Crie o diretorio se precisar dele, "
                "ou registre no relatorio que a captura nao foi possivel.\n"
                "- Se os validadores nao exigem arquivos de screenshot, ausencia de "
                "screenshot fisico e nota menor: use APPROVED WITH NOTES, nao BLOCKED.\n"
                "- Use REJECTED/BLOCKED apenas para problema que exige parar o processo "
                "ou que impede um validador obrigatorio de passar.\n"
                "- A primeira escrita deve criar/atualizar o relatorio .md canonico.\n"
            )
        return task_prompt, deny_read_paths


    def _run_review(self, node: Node):
        """
        Sprint Expert Gate — delega ao LLM especialista para revisao.
        Le o relatorio produzido e verifica APPROVED/REJECTED.
        """
        state = self.state_mgr.state
        structured_review = bool(node.review_route_path)
        allowed = self._resolve_allowed_paths(node)
        llm_selection = self._capture_delegation_llm_selection(state, node=node)
        effective_engine = llm_selection.engine
        last_review_engine = effective_engine
        task_prompt, opencode_deny_read_paths = self._build_review_task_context(
            node,
            llm_selection,
        )
        opencode_options = self._opencode_options_for_node(
            node,
            effective_engine,
            deny_read_paths=opencode_deny_read_paths,
        )

        # Verificar se artefatos já existem e validators já passam (ex: retry após max-turns)
        early_check = self._run_validators(node)
        correction_policy = self.graph.meta.get("correction_policy", {})
        mandatory_reviews = (
            correction_policy.get("mandatory_after_implementation", [])
            if isinstance(correction_policy, dict)
            else []
        )
        requires_fresh_review = node.id in mandatory_reviews
        if (
            early_check.passed
            and not requires_fresh_review
            and not self._review_output_semantically_stale(node)
        ):
            print(ui.success("Expert Review: artefatos já existem e validação OK — pulando etapa"))
            for output_path in node.outputs:
                self.state_mgr.record_artifact(Path(output_path).stem, output_path)
            next_id = node.next
            self._advance_state(node.id, next_id, "PASS")
            return
        if early_check.passed:
            if requires_fresh_review:
                print(ui.warn("Expert Review: review obrigatório após implementação — regenerando"))
                self._remove_node_outputs_from_worktree(node.id)
            else:
                print(ui.warn("Expert Review: relatório pré-existente contradiz o produto — regenerando"))
        else:
            blocking_reason = (
                None
                if structured_review
                else self._review_blocking_evidence_reason(node)
            )
            if blocking_reason:
                self._write_review_rejection_report(node, blocking_reason)
                print(ui.fail("REVIEW REJECTED"))
                print(ui.dim(f"  Motivo: {blocking_reason[:300]}"))
                if node.on_fail:
                    self._handle_on_fail(node, blocking_reason)
                else:
                    self.state_mgr.block(f"Expert Review REJECTED:\n{blocking_reason[:500]}")
                return

        if (
            not structured_review
            and self._try_opencode_deterministic_review(node, effective_engine)
        ):
            return

        print(f"  Expert Review ({node.executor})...")
        state.metrics["llm_calls"] = state.metrics.get("llm_calls", 0) + 1
        review_log_path = self._start_llm_log(
            state,
            node.id,
            "review",
            engine=llm_selection.engine,
            selection=llm_selection,
        )
        self.state_mgr.save()

        review_kwargs: dict = dict(
            task=task_prompt,
            project_root=self._work_dir,
            allowed_paths=self._delegate_allowed_paths(allowed),
            llm_engine=effective_engine,
            llm_model=llm_selection.model,
            llm_effort=llm_selection.effort,
            log_path=review_log_path,
            stream_prefix=self._stream_prefix(effective_engine),
            llm_timeout_seconds=node.llm_timeout_seconds,
        )
        self._apply_opencode_options(review_kwargs, opencode_options)
        if node.max_turns is not None:
            review_kwargs["max_turns"] = node.max_turns

        try:
            result = self._delegate_with_stream_retry(**review_kwargs)
        finally:
            self._clear_active_llm_log(state)

        # Quando uma validação já foi executada depois da última possível
        # mutação do worktree, reutilize-a no fechamento. Alguns validadores
        # disparam suítes completas; executá-los novamente sem qualquer escrita
        # intermediária só acrescenta latência e não aumenta a confiança.
        post_delegation_validation: ValidationResult | None = None
        if not result.success:
            # Mesmo com falha do LLM (ex: max-turns atingido), verificar se os artefatos
            # foram produzidos e os validators passam — o LLM pode ter concluído antes de parar.
            pre_check = self._run_validators(node)
            if pre_check.passed:
                print(f"  REVIEW: LLM encerrou com erro mas artefatos OK — validadores passaram")
                result.success = True  # tratamos como sucesso
                post_delegation_validation = pre_check
            else:
                rejected_review_output = (
                    "" if structured_review else self._read_review_output(node)
                )
                rejected_verdict = (
                    _parse_review_verdict(rejected_review_output)
                    if rejected_review_output
                    else None
                )
                if rejected_verdict in _REVIEW_REJECT_VERDICTS:
                    reason = _extract_review_rejection_reason(
                        rejected_review_output,
                        rejected_verdict,
                    )
                    print(ui.fail("REVIEW REJECTED"))
                    print(ui.dim(f"  Motivo: {reason[:300]}"))
                    if node.on_fail:
                        self._handle_on_fail(
                            node,
                            reason or (pre_check.feedback or "review rejeitado"),
                        )
                    else:
                        self.state_mgr.block(
                            f"Expert Review {rejected_verdict}:\n{reason[:500]}"
                        )
                    return
            if not pre_check.passed and getattr(result, "rate_limited", False):
                self.state_mgr.block(
                    f"{RATE_LIMIT_MARKER} API do LLM indisponível (rate limit persistiu "
                    f"após todo o backoff) no review do node {node.id}"
                )
                return
            elif not structured_review and not pre_check.passed and self._try_opencode_deterministic_review(
                node,
                effective_engine,
                require_opt_in=False,
            ):
                return
            elif pre_check.retryable:
                print(f"  REVIEW: LLM falhou mas validadores deram feedback recuperável — finalizando relatório...")
                recovery_selection, retry_log_path = self._start_delegation_attempt(
                    state,
                    node,
                    "review-recovery",
                )
                last_review_engine = recovery_selection.engine
                recovery_task_prompt, recovery_deny_paths = (
                    self._build_review_task_context(node, recovery_selection)
                )
                recovery_options = self._opencode_options_for_node(
                    node,
                    recovery_selection.engine,
                    deny_read_paths=recovery_deny_paths,
                )
                try:
                    recovery_result = delegate_with_feedback(
                        original_task=recovery_task_prompt,
                        feedback=self._enrich_validation_feedback(
                            node,
                            _review_recovery_feedback(pre_check.feedback or "review incompleto"),
                        ),
                        project_root=self._work_dir,
                        allowed_paths=self._delegate_allowed_paths(allowed),
                        llm_engine=recovery_selection.engine,
                        llm_model=recovery_selection.model,
                        llm_effort=recovery_selection.effort,
                        max_turns=node.max_turns or 50,
                        log_path=retry_log_path,
                        stream_prefix=self._stream_prefix(recovery_selection.engine),
                        opencode_deny_read_paths=recovery_options.deny_read_paths,
                        opencode_restrict_tools=recovery_options.restrict_tools,
                        opencode_steps=recovery_options.steps,
                        opencode_deny_edit_tools=recovery_options.deny_edit_tools,
                        opencode_early_success_paths=recovery_options.early_success_paths,
                        opencode_capture_output_path=recovery_options.capture_output_path,
                        llm_timeout_seconds=node.llm_timeout_seconds,
                    )
                finally:
                    self._clear_active_llm_log(state)
                state.metrics["llm_calls"] = state.metrics.get("llm_calls", 0) + 1
                if getattr(recovery_result, "rate_limited", False):
                    self.state_mgr.block(
                        f"{RATE_LIMIT_MARKER} API do LLM indisponível (rate limit persistiu "
                        f"após todo o backoff) no recovery do review {node.id}"
                    )
                    return
                recovery_check = self._run_validators(node)
                if recovery_result.success or recovery_check.passed:
                    result.success = True
                    post_delegation_validation = recovery_check
                else:
                    blocking_reason = self._review_blocking_evidence_reason(node)
                    if blocking_reason:
                        self._write_review_rejection_report(node, blocking_reason)
                        print(ui.fail("REVIEW REJECTED"))
                        print(ui.dim(f"  Motivo: {blocking_reason[:300]}"))
                        if node.on_fail:
                            self._handle_on_fail(node, blocking_reason)
                        else:
                            self.state_mgr.block(f"Expert Review REJECTED:\n{blocking_reason[:500]}")
                        return
                    self._print_validation(recovery_check)
                    self.state_mgr.block(f"Review falhou: {recovery_result.output[:300] or result.output[:300]}")
                    print(f"  REVIEW BLOCK: LLM nao conseguiu revisar")
                    return
            elif not pre_check.passed:
                self.state_mgr.block(f"Review falhou: {result.output[:300]}")
                print(f"  REVIEW BLOCK: LLM nao conseguiu revisar")
                return

        # Registrar artefato do relatorio
        for output_path in node.outputs:
            name = Path(output_path).stem
            self.state_mgr.record_artifact(name, output_path)

        # Validar artefatos deterministicos
        validation = (
            post_delegation_validation
            if post_delegation_validation is not None
            else self._run_validators(node)
        )
        self._print_validation(validation)

        if not validation.passed:
            rejected_review_output = (
                "" if structured_review else self._read_review_output(node)
            )
            rejected_verdict = _parse_review_verdict(rejected_review_output) if rejected_review_output else None
            if rejected_verdict in _REVIEW_REJECT_VERDICTS:
                reason = _extract_review_rejection_reason(rejected_review_output, rejected_verdict)
                print(ui.fail(f"REVIEW REJECTED"))
                print(ui.dim(f"  Motivo: {reason[:300]}"))
                if node.on_fail:
                    self._handle_on_fail(node, reason or (validation.feedback or "review rejeitado"))
                else:
                    self.state_mgr.block(f"Expert Review {rejected_verdict}:\n{reason[:500]}")
                return
            if not structured_review and self._try_opencode_deterministic_review(
                node,
                last_review_engine,
                require_opt_in=False,
            ):
                return
            if validation.retryable:
                print(f"  REVIEW: validadores falharam — {validation.feedback or 'sem detalhes'} — retentando...")
                review_retry_selection, retry_log_path = self._start_delegation_attempt(
                    state,
                    node,
                    "review-retry",
                )
                last_review_engine = review_retry_selection.engine
                review_retry_task, review_retry_deny_paths = (
                    self._build_review_task_context(node, review_retry_selection)
                )
                review_retry_options = self._opencode_options_for_node(
                    node,
                    review_retry_selection.engine,
                    deny_read_paths=review_retry_deny_paths,
                )
                try:
                    delegate_with_feedback(
                        original_task=review_retry_task,
                        feedback=self._enrich_validation_feedback(node, validation.feedback or ""),
                        project_root=self._work_dir,
                        allowed_paths=self._delegate_allowed_paths(allowed),
                        llm_engine=review_retry_selection.engine,
                        llm_model=review_retry_selection.model,
                        llm_effort=review_retry_selection.effort,
                        max_turns=node.max_turns or 50,
                        log_path=retry_log_path,
                        stream_prefix=self._stream_prefix(review_retry_selection.engine),
                        opencode_deny_read_paths=review_retry_options.deny_read_paths,
                        opencode_restrict_tools=review_retry_options.restrict_tools,
                        opencode_steps=review_retry_options.steps,
                        opencode_deny_edit_tools=review_retry_options.deny_edit_tools,
                        opencode_early_success_paths=review_retry_options.early_success_paths,
                        opencode_capture_output_path=review_retry_options.capture_output_path,
                        llm_timeout_seconds=node.llm_timeout_seconds,
                    )
                finally:
                    self._clear_active_llm_log(state)
                state.metrics["llm_calls"] = state.metrics.get("llm_calls", 0) + 1
                validation = self._run_validators(node)
                self._print_validation(validation)

            if not validation.passed:
                if not structured_review and self._try_opencode_deterministic_review(
                    node,
                    last_review_engine,
                    require_opt_in=False,
                ):
                    return
                feedback = validation.feedback or "validadores falharam"
                if node.on_fail:
                    self._handle_on_fail(node, feedback)
                else:
                    self.state_mgr.block(f"Review: validadores falharam: {feedback}")
                return

        # Ler relatorio e verificar veredicto
        review_output = self._read_review_output(node)

        if structured_review:
            next_id = self.graph.resolve_next(node.id)
            self._advance_state(node.id, next_id, "STRUCTURED")
            print(f"  REVIEW STRUCTURED → proximo: {next_id}")
            return

        # Veredicto deterministico via parse do relatorio.
        # Use apenas vereditos explicitos; o corpo do review pode citar comandos
        # como `ft reject` ou screenshots como `confirm-reject.png`.
        verdict = _parse_review_verdict(review_output)
        if verdict in _REVIEW_REJECT_VERDICTS:
            reason = _extract_review_rejection_reason(review_output, verdict)
            print(ui.fail(f"REVIEW REJECTED"))
            print(ui.dim(f"  Motivo: {reason[:300]}"))
            if node.on_fail:
                self._handle_on_fail(node, reason or "review rejeitado")
            else:
                self.state_mgr.block(f"Expert Review REJECTED:\n{reason[:500]}")
            return

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
        llm_selection = self._capture_delegation_llm_selection(
            state,
            node=self.graph.nodes.get(state.current_node),
        )
        exploration_prompt = (
            f"MODO EXPLORAÇÃO — pedido do stakeholder:\n\n{request}\n\n"
            f"Implemente a mudança pedida. Diga DONE e liste arquivos alterados. "
            f"Diga BLOCKED se não conseguir."
        )
        exploration_options: OpenCodeOptions | None = None
        if node := self.graph.nodes.get(state.current_node):
            if node.context_profile:
                exploration_prompt, exploration_deny_paths = self._compose_profile_context(
                    node,
                    exploration_prompt,
                    state,
                    llm_selection,
                )
                exploration_options = self._opencode_options_for_node(
                    node,
                    llm_selection.engine,
                    deny_read_paths=exploration_deny_paths,
                )

        from ft.engine.delegate import delegate_to_llm
        exploration_kwargs: dict = dict(
            task=exploration_prompt,
            project_root=self._work_dir,
            allowed_paths=self._delegate_allowed_paths(allowed),
            llm_engine=llm_selection.engine,
            llm_model=llm_selection.model,
            llm_effort=llm_selection.effort,
            log_path=log_path,
            llm_timeout_seconds=node.llm_timeout_seconds,
        )
        if exploration_options is not None:
            self._apply_opencode_options(exploration_kwargs, exploration_options)
        result = delegate_to_llm(**exploration_kwargs)

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
            llm_selection = self._capture_delegation_llm_selection(
                state,
                node=node,
            )
            from ft.engine.delegate import delegate_to_llm
            report_prompt = (
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
            )
            report_options: OpenCodeOptions | None = None
            if node.context_profile:
                report_prompt, report_deny_paths = self._compose_profile_context(
                    node,
                    report_prompt,
                    state,
                    llm_selection,
                )
                report_options = self._opencode_options_for_node(
                    node,
                    llm_selection.engine,
                    deny_read_paths=report_deny_paths,
                )
            report_kwargs: dict = dict(
                task=report_prompt,
                project_root=self._work_dir,
                allowed_paths=self._delegate_allowed_paths(allowed),
                llm_engine=llm_selection.engine,
                llm_model=llm_selection.model,
                llm_effort=llm_selection.effort,
                log_path=str(self._llm_log_dir() / "exploration_report.log"),
                llm_timeout_seconds=node.llm_timeout_seconds,
            )
            if report_options is not None:
                self._apply_opencode_options(report_kwargs, report_options)
            report_result = delegate_to_llm(**report_kwargs)
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
        self._ensure_run_trace()
        print(f"\n  PARALLEL GROUP: {len(nodes)} tasks")
        for n in nodes:
            print(f"    → {n.id}: {n.title}")

        tasks = []
        queue_spans: dict[str, TraceSpan] = {}
        for n in nodes:
            allowed = self._resolve_allowed_paths(n)
            queue_ordinal = self.trace.next_ordinal("queue", n.id)
            queue_spans[n.id] = self.trace.begin_span(
                category="queue",
                name="parallel_slot",
                node_id=n.id,
                parent_span_id=self._run_trace_id,
                attempt_id=f"{n.id}:parallel:{queue_ordinal}",
                invocation_id=f"{n.id}:queue:{queue_ordinal}",
                ordinal=queue_ordinal,
                attributes={"max_slots": self.state_mgr.state.parallel_max_slots},
            )
            tasks.append({
                "node_id": n.id,
                "task_prompt": build_task_prompt(n, {}),
                "allowed_paths": allowed,
                "outputs": n.outputs,
                "delegate_kwargs": {"selection_node_id": n.id},
            })

        max_slots = max(1, int(self.state_mgr.state.parallel_max_slots or 2))
        par = ParallelRunner(project_root=self._work_dir, max_slots=max_slots)
        selection_lock = threading.Lock()

        def delegate_parallel(*, selection_node_id: str, **kwargs):
            parallel_node = self.graph.get_node(selection_node_id)
            queue_spans[selection_node_id].finish(
                status="ok",
                result="dispatched",
            )
            with selection_lock:
                selection = self._capture_delegation_llm_selection(
                    self.state_mgr.state,
                    node=parallel_node,
                )
                task_prompt, _compact, deny_paths = self._build_llm_task_context(
                    parallel_node,
                    self.state_mgr.state,
                    selection,
                    allow_compact=False,
                )
                options = self._opencode_options_for_node(
                    parallel_node,
                    selection.engine,
                    deny_read_paths=deny_paths,
                )
                log_path = self._build_llm_log_path(
                    parallel_node.id,
                    "parallel",
                    engine=selection.engine,
                )
                delegate_kwargs = {
                    **kwargs,
                    "task": task_prompt,
                    "llm_engine": selection.engine,
                    "llm_model": selection.model,
                    "llm_effort": selection.effort,
                    "stream_prefix": self._stream_prefix(selection.engine),
                    "log_path": str(log_path),
                    "llm_timeout_seconds": parallel_node.llm_timeout_seconds,
                }
                self._apply_opencode_options(delegate_kwargs, options)
            llm_ordinal = self.trace.next_ordinal("llm", parallel_node.id)
            llm_span = self.trace.begin_span(
                category="llm",
                name="parallel",
                node_id=parallel_node.id,
                parent_span_id=self._run_trace_id,
                attempt_id=f"{parallel_node.id}:parallel:{llm_ordinal}",
                invocation_id=f"{parallel_node.id}:llm:{llm_ordinal}",
                ordinal=llm_ordinal,
                attributes={
                    "engine": selection.engine,
                    "model": selection.model,
                    "effort": selection.effort,
                    "provenance": dict(selection.provenance),
                    "resolution": list(selection.resolution),
                    "log_path": self._display_path(log_path),
                    "parallel": True,
                },
            )
            try:
                result = delegate_to_llm(**delegate_kwargs)
            except BaseException as exc:
                llm_span.finish(status="error", result=type(exc).__name__)
                raise
            llm_span.finish(
                status="ok" if result.success else "error",
                result="success" if result.success else "failed",
            )
            return result

        try:
            results = par.run_parallel(
                tasks,
                delegate_parallel,
            )
        except ValueError as e:
            for span in queue_spans.values():
                span.finish(status="error", result="group_rejected")
            self.state_mgr.block(str(e))
            print(f"  PARALLEL BLOCK: {e}")
            return
        for span in queue_spans.values():
            span.finish(status="error", result="not_dispatched")

        # Fan-in determinístico: seguir a ordem do grupo no YAML, não a ordem
        # de término das threads — o último advance define o current_node, que
        # precisa ser a saída do grupo.
        group_order = {n.id: i for i, n in enumerate(nodes)}
        results = sorted(results, key=lambda r: group_order.get(r.node_id, len(nodes)))

        # Fan-in: merge + validar cada resultado
        all_passed = True
        for wt_result in results:
            node = self.graph.get_node(wt_result.node_id)
            if not wt_result.success:
                self.state_mgr.block(f"Parallel task falhou: {wt_result.node_id}")
                print(f"  PARALLEL FAIL: {wt_result.node_id}")
                detail = (wt_result.output or "").strip()
                if detail:
                    print(f"    → {detail[:300]}")
                all_passed = False
                continue

            # Merge worktree branch
            if wt_result.branch:
                _, ok, detail = par.merge_all([wt_result])[0]
            else:
                ok, detail = False, "sem branch"
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
            validation = self._run_validators(node)
            self._print_validation(validation)
            if validation.passed:
                next_id = self.graph.resolve_next(node.id)
                self._advance_state(node.id, next_id)
                print(f"  PARALLEL PASS: {node.id} → {next_id}")
                self._log_activity(node.id, node.title, "parallel", "PASS",
                                   f"→ {next_id or 'fim'}", sprint=node.sprint)
            else:
                self.state_mgr.block(
                    f"Validacao falhou apos merge: {node.id}: {validation.feedback}"
                )
                self._log_activity(node.id, node.title, "parallel", "BLOCKED",
                                   validation.feedback or "validação falhou após merge",
                                   sprint=node.sprint)
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
        self._finish_human_wait(node_id, "approved")
        if node.env_teardown:
            self._run_env_teardown(node)
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

    def _rewind_to_node(self, goto: str, message: str) -> bool:
        """Volta para um node anterior, descartando progresso posterior."""
        if goto not in self.graph.nodes:
            return False
        state = self.state_mgr.load()
        ordered = [n.id for n in self.graph.nodes.values()]
        try:
            target_idx = ordered.index(goto)
        except ValueError:
            return False
        removed = [
            n for n in state.completed_nodes
            if n in ordered and ordered.index(n) >= target_idx
        ]
        state.completed_nodes = [
            n for n in state.completed_nodes
            if n in ordered and ordered.index(n) < target_idx
        ]
        for node_id in removed:
            state.gate_log.pop(node_id, None)
            self._clear_validator_snapshots(node_id)
            node = self.graph.nodes.get(node_id)
            if node is None:
                continue
            output_paths = {str(path) for path in node.outputs}
            for artifact_name, artifact_path in list(state.artifacts.items()):
                if artifact_path in output_paths:
                    state.artifacts.pop(artifact_name, None)
        state.current_node = goto
        state.node_status = "ready"
        state.blocked_reason = None
        state.pending_fix = None
        state.pending_approval = None
        state.last_approval_message = message
        state.metrics["steps_completed"] = len(state.completed_nodes)
        cycle_memory = self._cycle_memory_path()
        if cycle_memory.exists():
            cycle_memory.unlink()
        self.state_mgr.save()
        print(ui.info(f"↩ Voltando para {goto} com contexto de correção"))
        return True




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
        self._finish_human_wait(node_id, "rejected")
        print(f"  REJEITADO: {node_id} — {reason}")
        if node.env_teardown:
            self._run_env_teardown(node)

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

        if retry and retry_node.llm_episode:
            self._restart_llm_episode(
                state,
                retry_node.llm_episode,
                f"human_rejection:{node_id}",
            )

        correction_policy = self.graph.meta.get("correction_policy", {})
        follow_graph = (
            isinstance(correction_policy, dict)
            and correction_policy.get("follow_graph_after_retry") is True
        )
        if retry and follow_graph:
            if not retry_node.executor.startswith("llm"):
                self.state_mgr.block(
                    f"Node de correção não é executável por LLM: {retry_node.id}"
                )
                return
            feedback = (
                f"REJEITADO PELO STAKEHOLDER no gate {node_id}:\n{reason}\n\n"
                "Corrija o escopo aprovado e siga novamente todos os nodes do grafo."
            )
            if not self._rewind_to_node(retry_node.id, feedback):
                self.state_mgr.block(f"Não foi possível retornar para {retry_node.id}")
            return

        if retry and retry_node.executor.startswith("llm"):
            # Reenviar ao LLM com feedback da rejeicao
            original_prompt = build_task_prompt(retry_node, {})

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
            retry_selection, retry_log_path = self._start_delegation_attempt(
                state,
                retry_node,
                "stakeholder-retry",
            )
            retry_engine = retry_selection.engine
            retry_deny_paths: list[str] = []
            if retry_node.context_profile:
                original_prompt, retry_deny_paths = self._compose_profile_context(
                    retry_node,
                    original_prompt,
                    state,
                    retry_selection,
                )
            opencode_options = self._opencode_options_for_node(
                retry_node,
                retry_engine,
                deny_read_paths=retry_deny_paths,
            )

            try:
                result = delegate_with_feedback(
                    original_task=original_prompt,
                    feedback=self._enrich_validation_feedback(
                        retry_node,
                        f"REJEITADO PELO STAKEHOLDER: {reason}",
                    ),
                    project_root=self._work_dir,
                    allowed_paths=self._delegate_allowed_paths(allowed),
                    llm_engine=retry_engine,
                    llm_model=retry_selection.model,
                    llm_effort=retry_selection.effort,
                    max_turns=retry_node.max_turns or 50,
                    log_path=retry_log_path,
                    stream_prefix=self._stream_prefix(retry_engine),
                    opencode_deny_read_paths=opencode_options.deny_read_paths,
                    opencode_restrict_tools=opencode_options.restrict_tools,
                    opencode_steps=opencode_options.steps,
                    opencode_deny_edit_tools=opencode_options.deny_edit_tools,
                    opencode_early_success_paths=opencode_options.early_success_paths,
                    opencode_capture_output_path=opencode_options.capture_output_path,
                    llm_timeout_seconds=self._effective_llm_timeout(retry_node),
                )
            finally:
                self._clear_active_llm_log(state)
            state.metrics["llm_calls"] = state.metrics.get("llm_calls", 0) + 1

            if result.success:
                validation = self._run_validators(retry_node)
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

    _LOG_TS_RE = re.compile(r"^\| (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \|")

    @staticmethod
    def _format_elapsed(seconds: float) -> str:
        total = max(0, int(seconds))
        hours, rest = divmod(total, 3600)
        minutes, secs = divmod(rest, 60)
        if hours:
            return f"{hours}h{minutes:02d}m"
        if minutes:
            return f"{minutes}m{secs:02d}s"
        return f"{secs}s"

    def _cycle_objective_from_input(self) -> str | None:
        """Lê a demanda pinada no ciclo sem depender de uma chamada LLM."""
        from ft.templates.input_policy import InputPolicy, InputPolicyError

        destinations: list[str] = []
        try:
            policy = InputPolicy.from_mapping(self.graph.meta.get("input_policy"))
        except InputPolicyError:
            policy = None
        if policy is not None and policy.destination:
            destinations.append(policy.destination)
        # Compatibilidade com processos feature/bug/tweak antigos que ainda
        # não declaravam formalmente a origem do objetivo no grafo pinado.
        if "docs/feature-request.md" not in destinations:
            destinations.append("docs/feature-request.md")

        root = Path(self.project_root).resolve()
        for destination in destinations:
            candidate = (root / destination).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                continue
            if not candidate.is_file():
                continue
            try:
                with candidate.open(encoding="utf-8") as source:
                    raw = source.read(16_384)
            except (OSError, UnicodeError):
                continue
            objective = _brief_cycle_objective(raw)
            if objective:
                return objective
        return None

    def _status_timing_labels(self) -> tuple[str | None, str | None]:
        """(tempo de ciclo, última atividade) para o status — best effort.

        Início do ciclo: timestamp da primeira linha da tabela do run log
        (INIT). Última atividade: mtime mais recente entre os llm_logs, o
        engine_state e o próprio run log — cobre delegação em andamento, cujo
        progresso aparece nos logs antes de qualquer transição de node.
        """
        now = datetime.now()
        log_path = Path(self.project_root) / self._log_filename

        started = None
        if log_path.is_file():
            try:
                with log_path.open(encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        m = self._LOG_TS_RE.match(line)
                        if m:
                            started = datetime.strptime(
                                m.group(1), "%Y-%m-%d %H:%M:%S"
                            )
                            break
            except OSError:
                started = None

        mtimes: list[float] = []
        llm_dir = self._llm_log_dir()
        if llm_dir.is_dir():
            mtimes.extend(
                p.stat().st_mtime for p in llm_dir.iterdir() if p.is_file()
            )
        for extra in (self.state_mgr.path, log_path):
            if extra.is_file():
                mtimes.append(extra.stat().st_mtime)

        runtime_label = (
            self._format_elapsed((now - started).total_seconds())
            if started
            else None
        )
        activity_label = None
        if mtimes:
            last = datetime.fromtimestamp(max(mtimes))
            fmt = "%H:%M:%S" if last.date() == now.date() else "%Y-%m-%d %H:%M:%S"
            activity_label = last.strftime(fmt)
        return runtime_label, activity_label

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

        print(ui.header(f"Process: {state.process_id} v{state.version}"))
        # Preserve the public text consumed by existing status parsers; model
        # and effort are additive lines instead of a breaking replacement.
        if state.current_cycle:
            print(ui.info(f"Ciclo: {state.current_cycle}"))
        persisted_objective = (
            _brief_cycle_objective(state.cycle_objective)
            if isinstance(state.cycle_objective, str)
            else None
        )
        cycle_objective = persisted_objective or self._cycle_objective_from_input()
        if cycle_objective:
            print(ui.info(f"Objetivo do Ciclo: {cycle_objective}"))
        print(ui.info(f"LLM engine: {state.llm_engine}"))
        if state.llm_model:
            print(ui.info(f"LLM model: {state.llm_model}"))
        if state.llm_effort:
            print(ui.info(f"LLM effort: {state.llm_effort}"))
        print(ui.info(f"Node atual: {state.current_node}"))
        print(ui.info(f"Status: {state.node_status}"))
        if current_sprint:
            print(ui.info(f"Sprint: {current_sprint}"))
        steps_done = state.metrics.get("steps_completed", 0)
        steps_total = state.metrics.get("steps_total", 0)
        current_step = steps_done + 1 if state.node_status not in ("done", "completed") else steps_done
        progress_line = f"Progresso: {current_step}/{steps_total} (passo atual)"
        runtime_label, activity_label = self._status_timing_labels()
        if runtime_label:
            progress_line += f" · ciclo rodando há {runtime_label}"
        if activity_label:
            progress_line += f" · última atividade {activity_label}"
        print(ui.info(progress_line))
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

        trace_report = build_run_report(
            self.trace.path,
            run_id=self.trace.run_id,
            log_root=self.project_root,
        )
        if trace_report.get("spans"):
            state = self.state_mgr.load()
            print(ui.header(f"Relatório — {state.process_id} / {state.llm_engine}"))
            print()
            print(
                f"  {'Node / operação':<48} {'Tipo':<10} {'Tent.':>6} "
                f"{'Tempo':>9} {'Out tok':>10}"
            )
            print(f"  {'-'*48} {'-'*10} {'-'*6} {'-'*9} {'-'*10}")
            visible = {"llm", "validator", "human", "queue", "close"}
            for span in trace_report["spans"]:
                category = span.get("category")
                if category not in visible:
                    continue
                duration_ms = span.get("duration_ms")
                seconds = int(duration_ms / 1000) if isinstance(duration_ms, int) else 0
                elapsed = self._format_elapsed(seconds)
                node_name = str(span.get("node_id") or span.get("name") or "—")
                if category == "validator":
                    node_name = f"{node_name} [{span.get('name')}]"
                ordinal = span.get("ordinal") or "—"
                metrics = span.get("metrics") or {}
                output_tokens = metrics.get("output_tokens")
                token_text = f"{output_tokens:,}" if isinstance(output_tokens, int) else "—"
                print(
                    f"  {node_name:<48.48} {str(category):<10} {str(ordinal):>6} "
                    f"{elapsed:>9} {token_text:>10}"
                )

            wall = trace_report.get("wall") or {}
            wall_ms = wall.get("duration_ms")
            wall_text = (
                self._format_elapsed(int(wall_ms / 1000))
                if isinstance(wall_ms, int)
                else "indisponível"
            )
            active = trace_report.get("active_time_ms") or {}
            print()
            print(f"  Tempo wall real : {wall_text} ({wall.get('status', 'unknown')})")
            print(
                "  Tempo ativo      : "
                + ", ".join(
                    f"{category}={self._format_elapsed(int(value / 1000))}"
                    for category, value in active.items()
                    if isinstance(value, int) and value > 0
                )
            )
            llm = trace_report.get("llm") or {}
            print(
                "  LLM              : "
                f"{llm.get('calls', 0)} chamada(s), "
                f"in={llm.get('input_tokens') if llm.get('input_tokens') is not None else '—'}, "
                f"out={llm.get('output_tokens') if llm.get('output_tokens') is not None else '—'}"
            )
            print(
                f"  Progresso        : {state.metrics.get('steps_completed', 0)}/"
                f"{state.metrics.get('steps_total', 0)} nodes"
            )
            return

        logs_dir = self._llm_log_dir()
        if not logs_dir.is_dir():
            print(ui.warn("Nenhum log LLM encontrado para o ciclo atual"))
            return

        files = sorted(
            [p for p in logs_dir.iterdir() if p.is_file() and p.suffix in {".jsonl", ".log"}],
            key=lambda f: f.stat().st_mtime,
        )
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
            seen_message_ids: set[str] = set()
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line or not line.startswith("{"):
                    continue
                try:
                    d = _json.loads(line)
                except Exception:
                    continue
                turns += 1
                message = d.get("message") if isinstance(d.get("message"), dict) else {}
                message_id = message.get("id")
                if message_id:
                    if message_id in seen_message_ids:
                        continue
                    seen_message_ids.add(message_id)
                usage = message.get("usage") or d.get("usage") or {}
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
        print(f"  Tempo LLM observado: {td_m}m{td_s:02d}s  ({total_dur/3600:.1f}h)")
        usage_summary = summarize_llm_usage(
            logs_dir,
            default_engine=state.llm_engine,
            default_model=state.llm_model,
        )
        for line in format_llm_usage_lines(usage_summary):
            print(line)

    @staticmethod
    def _print_validation(v: ValidationResult):
        for item in v.items:
            if item.passed:
                print(ui.validator_ok(item.detail))
            else:
                print(ui.validator_fail(item.detail))
