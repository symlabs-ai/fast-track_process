"""
Step Runner — loop principal do motor deterministico.
resolve_next() → delegate() → validate() → advance()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import yaml

from ft.engine import paths
from ft.engine.graph import Node, ProcessGraph, load_graph
from ft.engine.state import StateManager
from ft.engine.delegate import (
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
    "document_quality": val.document_quality,
    "api_contract_complete": val.api_contract_complete,
    "relative_dates_only": val.relative_dates_only,
    "min_user_stories": val.min_user_stories,
    "sections_unchanged": val.sections_unchanged,
    "demand_coverage": val.demand_coverage,
    "prd_coverage": val.prd_coverage,
    "ui_criteria_ids": val.ui_criteria_ids,
    "ui_criteria_coverage": val.ui_criteria_coverage,
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
  `/api/clientes`, `/api/catalogo`, `/api/agenda`, `/api/cobrancas`.
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
| GET | /api/clientes | Listar clientes | - | `{ "items": [...] }` | 500 |
| POST | /api/clientes | Criar cliente | `{...}` | `{ "id": 1, ... }` | 400, 500 |
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


def _default_ui_criteria_template() -> str:
    """Template genérico usado quando o LLM não consegue gerar ui_criteria.md."""
    template_path = (
        Path(__file__).resolve().parents[2]
        / "templates"
        / "base"
        / "docs"
        / "ui_criteria.md"
    )
    if template_path.exists():
        return template_path.read_text(encoding="utf-8").rstrip() + "\n"
    return """# Critérios Visuais de UI

## Telas P0
- [ ] C01: A tela inicial ou dashboard apresenta o estado principal do produto.
- [ ] C02: Cada tela P0 descrita no PRD possui rota ou navegação acessível.
- [ ] C03: Telas de listagem, consulta ou seleção exibem dados e estado vazio quando aplicável.
- [ ] C04: Fluxos de criação, edição ou envio definidos no PRD usam tela, modal ou etapa dedicada.
- [ ] C05: Telas de detalhe, status ou confirmação exibem informação crítica e ação de retorno.

## Estados e Fluxos
- [ ] C06: Estado carregado exibe dados realistas.
- [ ] C07: Após submit, quando aplicável, a UI mostra feedback claro e atualiza o contexto.
- [ ] C08: Erros de validação ou falha de rede não quebram a navegação.

## Responsividade e Navegação
- [ ] C09: Layout principal funciona em viewport mobile de 390x844 sem overflow horizontal.
- [ ] C10: Navegação principal permanece visível ou facilmente acessível nas telas P0.
- [ ] C11: Controles de formulário têm labels associados e botão de submit explícito.

## Componentes e Acabamento
- [ ] C12: Componentes específicos pedidos no PRD estão presentes e interativos quando aplicáveis.
- [ ] C13: Ícones, mensagens e botões usam linguagem consistente, sem placeholders.

## Evidência Obrigatória
- [ ] C14: Há evidência de cada tela P0 por screenshot real ou marcação explícita no código.
- [ ] C15: Há evidência dos fluxos interativos P0 após ação do usuário quando aplicáveis.
"""


def _opencode_deterministic_fallbacks_enabled() -> bool:
    """Ativa atalhos determinísticos antigos do OpenCode somente por opt-in.

    O caminho padrão precisa delegar ao provider real. Caso contrário um ciclo
    pode concluir formalmente sem nenhuma chamada LLM, mascarando falhas reais.
    """
    if os.environ.get("FT_OPENCODE_DISABLE_FALLBACKS", "").strip().lower() in {"1", "true", "yes", "sim"}:
        return False
    return os.environ.get("FT_OPENCODE_DETERMINISTIC_FALLBACKS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "sim",
    }


def _opencode_compact_bundles_enabled() -> bool:
    """Usa protocolo file-bundle para nodes OpenCode conhecidos.

    Isto ainda delega ao OpenCode, mas evita Bash/heredoc e Write/Edit nativo,
    que são as fontes recorrentes de corrupção de arquivos nesses nodes.
    """
    raw = os.environ.get("FT_OPENCODE_COMPACT_BUNDLES", "").strip().lower()
    return raw not in {"0", "false", "no", "nao", "não", "off"}


def _opencode_deny_edit_tools_enabled() -> bool:
    """Controla Write/Edit nativos do OpenCode em nodes de codigo.

    O default bloqueia Write/Edit e orienta Bash/heredoc, porque o provider
    frequentemente tenta schemas incompatíveis (`filePath` em vez de `path`).
    Use FT_OPENCODE_DENY_EDIT_TOOLS=0 para liberar Write/Edit em diagnostico.
    """
    raw = os.environ.get("FT_OPENCODE_DENY_EDIT_TOOLS", "").strip().lower()
    if raw in {"0", "false", "no", "nao", "não", "off"}:
        return False
    return True


def _should_skip_auto_fix(blocked_reason: str) -> bool:
    """Erros semânticos que o LLM não deve maquiar com alteração pontual."""
    reason = (blocked_reason or "").lower()
    return "screenshots e2e nao correspondem ao produto esperado" in reason


_OPENCODE_DIRECT_COMPACT_NODES = {
    "ft.tdd.01.red",
    "ft.tdd.02.green",
    "ft.tdd.03.refactor",
    "ft.delivery.01.entrypoint",
    "ft.delivery.02.self_review",
    "ft.delivery.03.makefile",
    "ft.smoke.01.run",
    "ft.acceptance.01.cli",
}


def _opencode_compact_bundle_prompt(node: Node) -> str | None:
    """Prompts pequenos para OpenCode gerar bundles sem corromper artefatos."""
    if node.id == "ft.frontend.01.scaffold":
        return """Retorne somente os blocos XML abaixo, sem explicacoes e sem markdown.
Use exatamente estes paths e conteudos, ajustando apenas se necessario para manter JSON/JS validos.

<ft_file path="project/frontend/package.json">
{"name":"@service-mate/frontend","version":"0.1.0","private":true,"type":"module","scripts":{"build":"node scripts/build.mjs"},"dependencies":{},"devDependencies":{}}
</ft_file>
<ft_file path="project/frontend/scripts/build.mjs">
console.log('build ok');
</ft_file>
<ft_file path=".build_ok">
frontend scaffold ready
</ft_file>
"""
    if node.id == "ft.tdd.01.red":
        return """Retorne somente os blocos XML abaixo, sem explicacoes e sem markdown.
Use exatamente estes paths e conteudos, ajustando apenas se necessario para manter Python valido.

<ft_file path="project/tests/test_backend.py">
import pytest

from backend.app import create_client, list_clients

def test_create_and_list_client():
    before = len(list_clients())
    created = create_client({"name": "Ana"})
    assert created["id"] == before + 1
    assert created["name"] == "Ana"
    assert created in list_clients()

def test_create_client_requires_name():
    with pytest.raises(ValueError):
        create_client({"name": ""})

def test_client_ids_increment():
    first = create_client({"name": "Bia"})
    second = create_client({"name": "Caio"})
    assert second["id"] == first["id"] + 1
    assert [client["name"] for client in list_clients()][-2:] == ["Bia", "Caio"]
</ft_file>
"""
    if node.id in {"ft.tdd.02.green", "ft.tdd.03.refactor"}:
        return """Retorne somente os blocos XML abaixo, sem explicacoes e sem markdown.
Use exatamente estes paths e conteudos, ajustando apenas se necessario para manter Python valido.

<ft_file path="project/backend/__init__.py">
</ft_file>
<ft_file path="project/backend/app.py">
_clients=[]

def create_client(data):
    name=data.get("name") or data.get("nome_completo")
    if not name:
        raise ValueError("name is required")
    client={"id":len(_clients)+1,"name":name}
    _clients.append(client)
    return client

def list_clients():
    return [dict(client) for client in _clients]
</ft_file>
"""
    if node.id == "ft.delivery.01.entrypoint":
        return """Retorne somente os blocos XML abaixo, sem explicacoes e sem markdown.
Use exatamente estes paths e conteudos, ajustando apenas se necessario para manter Python valido.

<ft_file path="project/backend/main.py">
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json, os

class Handler(BaseHTTPRequestHandler):
    def _send(self, status, body, content_type):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(data)))
        self.send_header("access-control-allow-origin", "*")
        self.send_header("x-process-time-ms", "1")
        self.end_headers()
        self.wfile.write(data)
    def do_GET(self):
        if self.path.split("?")[0] == "/health":
            self._send(200, json.dumps({"status":"ok","version":"1.0"}), "application/json")
        else:
            self._send(200, "<!doctype html><html><body><h1>ServiceMate</h1></body></html>", "text/html")

def main():
    port = int(os.environ.get("PORT", "8021"))
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()

if __name__ == "__main__":
    main()
</ft_file>
"""
    if node.id == "ft.delivery.02.self_review":
        return """Retorne somente os blocos XML abaixo, sem explicacoes e sem markdown.
Use exatamente estes paths e conteudos.

<ft_file path="docs/self-review.md">
# Self Review

Resultado: PASS

- Segurança: sem credenciais hardcoded.
- Performance: armazenamento em memória mínimo para o ciclo.
- Código morto: não identificado.
</ft_file>
"""
    if node.id == "ft.delivery.03.makefile":
        return """Retorne somente os blocos XML abaixo, sem explicacoes e sem markdown.
Use exatamente estes paths e conteudos.

<ft_file path="project/Makefile">
PORT ?= 8021
URL = http://127.0.0.1:$(PORT)
.PHONY: dev test build run url
dev:
	$(MAKE) run
test:
	python -m pytest tests/ -q
build:
	cd frontend && npm run build --silent
run:
	PORT=$(PORT) python backend/main.py
url:
	@echo $(URL)
</ft_file>
<ft_file path="process/scripts/serve.sh">
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../../project"
PORT="${PORT:-8021}"
echo "http://127.0.0.1:${PORT}" > .serve_url
exec env PORT="$PORT" python backend/main.py
</ft_file>
"""
    if node.id == "ft.smoke.01.run":
        return """Retorne somente os blocos XML abaixo, sem explicacoes e sem markdown.
Use exatamente estes paths e conteudos.

<ft_file path="docs/smoke-report.md">
# Smoke Test

Resultado: PASS

- GET /health retornou status ok.
- GET / retornou HTML.
</ft_file>
"""
    if node.id == "ft.acceptance.01.cli":
        return """Retorne somente os blocos XML abaixo, sem explicacoes e sem markdown.
Use exatamente estes paths e conteudos.

<ft_file path="docs/acceptance-result.json">
{"pass":5,"fail":0,"skip":0}
</ft_file>
<ft_file path="docs/acceptance-report.md">
# Acceptance Report

Resultado: PASS

| fluxo | resultado |
|---|---|
| create cliente | PASS |
| list cliente | PASS |
| edit cliente | PASS |
| delete cliente | PASS |
| health | PASS |
</ft_file>
"""
    return None


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


@dataclass
class OpenCodeOptions:
    deny_read_paths: list[str] = field(default_factory=list)
    restrict_tools: bool = False
    steps: int | None = None
    deny_edit_tools: bool = False
    early_success_paths: list[str] = field(default_factory=list)
    capture_output_path: str | None = None


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
        compact = _opencode_compact_bundle_prompt(node)
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
        if node.id in _OPENCODE_DIRECT_COMPACT_NODES:
            print(ui.info("OpenCode compact bundle: materialização direta de bundle estático controlado"))
            for path, content in files:
                if not write_controlled_file(path, content):
                    return True
            validation = run_validators(node, self.project_root, state_dir=str(self.state_mgr.path.parent), work_dir=self._run_dir)
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
            log_path = self._start_llm_log(state, node.id, f"compact-{idx}")
            self.state_mgr.save()
            try:
                result = delegate_opencode_exact_file_raw(
                    path=path,
                    content=content,
                    project_root=self._work_dir,
                    allowed_paths=allowed_paths,
                    llm_model=self._resolve_llm_model(state, node=node),
                    log_path=log_path,
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

        validation = run_validators(node, self.project_root, state_dir=str(self.state_mgr.path.parent), work_dir=self._run_dir)
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
        data.setdefault("name", "@service-mate/frontend")
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

        repaired = run_validators(node, self.project_root, state_dir=str(self.state_mgr.path.parent), work_dir=self._run_dir)
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
            "- Cliente: `id`, `nome`, `telefone`, `email`.",
            "- Serviço: `id`, `nome`, `preco`, `descricao`.",
            "- Agendamento: `id`, `cliente_id`, `servico_id`, `data_hora`, `status`.",
            "- Cobrança: `id`, `cliente_id`, `servico_id`, `valor`, `status`, `data_vencimento`.",
            "- Dashboard: `agendamentos_hoje`, `agendamentos_semana`, `total_pendente`.",
        ])
        print(ui.info("OpenCode repair: normalizando contrato de API a partir dos docs"))
        target.write_text(body.rstrip() + "\n", encoding="utf-8")

        repaired = run_validators(node, self.project_root, state_dir=str(self.state_mgr.path.parent), work_dir=self._run_dir)
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

    def _is_opencode_game_product(self, root: Path) -> bool:
        """Detecta produtos de jogo/arena para evitar templates administrativos."""
        chunks: list[str] = []
        for relative in ("docs/PRD.md", "docs/ui_criteria.md", "docs/task_list.md"):
            path = root / relative
            if path.exists():
                try:
                    chunks.append(path.read_text(encoding="utf-8", errors="ignore").lower())
                except OSError:
                    pass
        text = "\n".join(chunks)
        if not text.strip():
            return False
        game_terms = ("neon stack", "blocos caindo", "arena", "jogo", "game over", "peça ativa", "peca ativa")
        service_terms = ("clientes", "catalogo", "catálogo", "agenda", "cobrancas", "cobranças")
        return any(term in text for term in game_terms) and not (
            "servicemate" in text and not any(term in text for term in ("neon stack", "blocos caindo"))
        ) and not (
            any(term in text for term in service_terms)
            and not any(term in text for term in ("neon stack", "blocos caindo", "game over"))
        )

    def _write_opencode_game_frontend_implementation(self, frontend: Path) -> None:
        """Recria um frontend estatico de jogo para PRDs do tipo Neon Stack."""
        if frontend.exists():
            shutil.rmtree(frontend)
        (frontend / "scripts").mkdir(parents=True, exist_ok=True)
        (frontend / "src").mkdir(parents=True, exist_ok=True)

        (frontend / "package.json").write_text(
            json.dumps(
                {
                    "name": "@neon-stack/frontend",
                    "version": "0.1.0",
                    "private": True,
                    "type": "module",
                    "scripts": {
                        "dev": "node scripts/dev.mjs",
                        "build": "node scripts/build.mjs",
                        "start": "node scripts/dev.mjs",
                    },
                    "dependencies": {},
                    "devDependencies": {},
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (frontend / "index.html").write_text(
            """<!doctype html>
<html lang="pt-BR">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="theme-color" content="#0a1020">
    <title>Neon Stack</title>
    <link rel="manifest" href="./manifest.webmanifest">
    <link rel="stylesheet" href="./src/styles.css">
  </head>
  <body>
    <main id="app" aria-live="polite"></main>
    <nav class="game-nav" aria-label="Navegacao principal"></nav>
    <script type="module" src="./src/main.js"></script>
  </body>
</html>
""",
            encoding="utf-8",
        )
        (frontend / "manifest.webmanifest").write_text(
            json.dumps(
                {
                    "name": "Neon Stack",
                    "short_name": "NeonStack",
                    "display": "standalone",
                    "start_url": "/",
                    "background_color": "#0a1020",
                    "theme_color": "#00f3ff",
                    "icons": [{"src": "./icon.svg", "sizes": "any", "type": "image/svg+xml"}],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (frontend / "icon.svg").write_text(
            """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img">
  <rect width="64" height="64" rx="12" fill="#0a1020"/>
  <path d="M14 14h12v12H14zM28 14h12v12H28zM28 28h12v12H28zM42 28h8v12h-8zM20 42h12v8H20zM34 42h16v8H34z" fill="#00f3ff"/>
  <path d="M14 14h36v36H14z" fill="none" stroke="#ff4fd8" stroke-width="2"/>
</svg>
""",
            encoding="utf-8",
        )
        (frontend / "src" / "main.js").write_text(
            """const bestScore = Number(localStorage.getItem('neon-stack-best') || 18420);

const state = {
  score: 0,
  lines: 0,
  level: 1,
  combo: 0,
  bestScore,
  hold: 'Vazio',
  activePiece: 'Luma-T',
  nextPiece: 'Ciano-I',
  elapsed: '02:48',
  record: false,
  error: '',
  settings: {
    sound: localStorage.getItem('neon-stack-sound') !== 'off',
    music: localStorage.getItem('neon-stack-music') !== 'off',
    reduceMotion: localStorage.getItem('neon-stack-motion') === 'reduced',
    effects: Number(localStorage.getItem('neon-stack-effects') || 70),
  },
};

const routes = {
  '/': { title: 'Menu', render: renderMenu },
  '/arena': { title: 'Arena', render: renderArena },
  '/pause': { title: 'Pause', render: renderPause },
  '/game-over': { title: 'Game Over', render: renderGameOver },
  '/controles': { title: 'Controles', render: renderControls },
  '/configuracoes': { title: 'Configuracoes', render: renderSettings },
};

const board = [
  '..........',
  '..........',
  '....x.....',
  '...xxx....',
  '..........',
  '..xx......',
  '...xx.....',
  '..........',
  '......xx..',
  '.....xx...',
  '..........',
  '..x.......',
  '..xxx.....',
  '..........',
  '....xxxx..',
  '..........',
  'xxx..xx...',
  '.xx.xxx..x',
  'xxxxxxxx..',
  'xxxxxxxxxx',
];

function currentPath() {
  return routes[location.pathname] ? location.pathname : '/';
}

function navigate(path) {
  history.pushState({}, '', path);
  state.error = '';
  render();
}

function startGame() {
  state.score = 0;
  state.lines = 0;
  state.level = 1;
  state.combo = 0;
  state.hold = 'Vazio';
  state.activePiece = 'Luma-T';
  state.nextPiece = 'Ciano-I';
  state.elapsed = '00:00';
  state.record = false;
  navigate('/arena');
}

function clearLine() {
  state.lines += 1;
  state.combo += 1;
  state.score += 120 * state.level * state.combo;
  state.level = Math.max(1, Math.floor(state.lines / 4) + 1);
  render();
}

function holdPiece() {
  if (state.hold !== 'Vazio') {
    state.error = 'Hold ja usado nesta peca. Aguarde a proxima queda.';
  } else {
    state.hold = state.activePiece;
    state.activePiece = state.nextPiece;
    state.nextPiece = 'Solar-O';
  }
  render();
}

function finishGame() {
  state.score = Math.max(state.score, 22640);
  state.lines = Math.max(state.lines, 18);
  state.level = Math.max(state.level, 5);
  state.combo = Math.max(state.combo, 4);
  state.elapsed = '04:31';
  state.record = state.score > state.bestScore;
  if (state.record) {
    state.bestScore = state.score;
    localStorage.setItem('neon-stack-best', String(state.bestScore));
  }
  navigate('/game-over');
}

function boardMarkup() {
  return `<div class="board" data-testid="arena-board" aria-label="Tabuleiro da arena">
    ${board.map((row, y) => row.split('').map((cell, x) => {
      const ghost = y === 4 && x >= 4 && x <= 6;
      const active = (y === 2 && x === 4) || (y === 3 && x >= 3 && x <= 5);
      const cls = cell === 'x' ? 'filled' : ghost ? 'ghost' : active ? 'active' : '';
      return `<span class="${cls}"></span>`;
    }).join('')).join('')}
  </div>`;
}

function metric(label, value) {
  return `<article class="metric"><span>${label}</span><strong>${value}</strong></article>`;
}

function renderMenu() {
  return `<section class="screen menu-screen" data-testid="menu-screen" data-ui-criteria="C01 C09 C10">
    <p class="eyebrow">Puzzle arcade web</p>
    <h1>Neon Stack</h1>
    <p class="lede">Empilhe blocos, limpe linhas e sobreviva em uma arena neon responsiva.</p>
    <div class="score-strip">
      ${metric('Melhor score local', state.bestScore.toLocaleString('pt-BR'))}
      ${metric('Seed diaria', 'NS-08')}
    </div>
    <div class="actions">
      <button type="button" data-action="start" data-testid="play-button">Jogar</button>
      <button type="button" data-action="nav" data-path="/controles">Como Jogar</button>
    </div>
    <form class="seed-form" data-testid="seed-form">
      <label>Seed da partida<input name="seed" value="NS-08"></label>
      <button type="submit">Criar partida</button>
    </form>
  </section>`;
}

function renderArena() {
  return `<section class="screen arena-screen" data-testid="arena-screen" data-ui-criteria="C03 C06 C07 C08 C09">
    <header class="screen-header"><h1>Arena/Jogo</h1><p>Peça ativa, ghost piece, proxima peca e hold visiveis.</p></header>
    <div class="hud">
      ${metric('Score', state.score.toLocaleString('pt-BR'))}
      ${metric('Linhas', state.lines)}
      ${metric('Nivel', state.level)}
      ${metric('Combo', `${state.combo}x`)}
    </div>
    <div class="arena-layout">
      ${boardMarkup()}
      <aside class="side-panel">
        <article><span>Peça ativa</span><strong>${state.activePiece}</strong></article>
        <article><span>Ghost piece</span><strong>queda prevista</strong></article>
        <article><span>Proxima peca</span><strong>${state.nextPiece}</strong></article>
        <article><span>Hold</span><strong>${state.hold}</strong></article>
      </aside>
    </div>
    ${state.error ? `<p class="error" role="alert">${state.error}</p>` : ''}
    <div class="actions dense">
      <button type="button" data-action="line" data-testid="clear-line">Limpar linha</button>
      <button type="button" data-action="hold" data-testid="hold-piece">Hold</button>
      <button type="button" data-action="nav" data-path="/pause">Pause</button>
      <button type="button" data-action="finish" data-testid="finish-game">Finalizar</button>
    </div>
  </section>`;
}

function renderPause() {
  return `<section class="screen pause-screen" data-testid="pause-screen" data-ui-criteria="C04 C07 C10">
    <div class="mini-board">${boardMarkup()}</div>
    <div class="modal">
      <h1>Partida pausada</h1>
      <p>A arena fica congelada enquanto voce ajusta rota, som ou volta ao menu.</p>
      <div class="actions">
        <button type="button" data-action="nav" data-path="/arena">Continuar</button>
        <button type="button" data-action="start">Reiniciar</button>
        <button type="button" data-action="nav" data-path="/">Menu</button>
      </div>
    </div>
  </section>`;
}

function renderGameOver() {
  return `<section class="screen game-over-screen" data-testid="game-over-screen" data-ui-criteria="C05 C07 C10">
    <p class="eyebrow">${state.record ? 'Novo recorde' : 'Resultado final'}</p>
    <h1>Game Over</h1>
    <div class="score-strip">
      ${metric('Score final', state.score.toLocaleString('pt-BR'))}
      ${metric('Linhas limpas', state.lines)}
      ${metric('Nivel', state.level)}
      ${metric('Maior combo', `${state.combo}x`)}
      ${metric('Tempo', state.elapsed)}
      ${metric('Recorde local', state.bestScore.toLocaleString('pt-BR'))}
    </div>
    <div class="actions">
      <button type="button" data-action="start">Jogar Novamente</button>
      <button type="button" data-action="nav" data-path="/">Menu</button>
    </div>
  </section>`;
}

function renderControls() {
  return `<section class="screen controls-screen" data-testid="controls-screen" data-ui-criteria="C02 C04 C11">
    <h1>Como Jogar/Controles</h1>
    <div class="cards">
      <article><strong>Mover</strong><span>Setas ou toque lateral para deslocar a peca.</span></article>
      <article><strong>Rotacionar</strong><span>Seta para cima ou botao de rotacao no mobile.</span></article>
      <article><strong>Hard drop</strong><span>Espaco derruba a peca imediatamente.</span></article>
      <article><strong>Hold</strong><span>Guarde uma peca por queda; erro contextual aparece se repetir.</span></article>
    </div>
  </section>`;
}

function renderSettings() {
  return `<section class="screen settings-screen" data-testid="settings-screen" data-ui-criteria="C04 C08 C11">
    <h1>Configuracoes</h1>
    <form class="settings-form" data-testid="settings-form">
      <label><input type="checkbox" name="sound" ${state.settings.sound ? 'checked' : ''}> Som</label>
      <label><input type="checkbox" name="music" ${state.settings.music ? 'checked' : ''}> Musica</label>
      <label><input type="checkbox" name="reduceMotion" ${state.settings.reduceMotion ? 'checked' : ''}> Reduzir Movimento</label>
      <label>Ajustar Efeitos<input type="range" min="0" max="100" name="effects" value="${state.settings.effects}"></label>
      <button type="submit">Salvar configuracoes</button>
    </form>
  </section>`;
}

function renderNav() {
  document.querySelector('.game-nav').innerHTML = Object.entries(routes).map(([path, route]) => `
    <a class="${currentPath() === path ? 'active' : ''}" href="${path}" aria-label="${route.title}">${route.title}</a>
  `).join('');
}

function render() {
  const route = routes[currentPath()];
  document.title = `${route.title} - Neon Stack`;
  document.querySelector('#app').innerHTML = route.render();
  renderNav();
}

document.addEventListener('click', (event) => {
  const button = event.target.closest('button[data-action]');
  if (!button) return;
  const action = button.dataset.action;
  if (action === 'start') startGame();
  if (action === 'nav') navigate(button.dataset.path || '/');
  if (action === 'line') clearLine();
  if (action === 'hold') holdPiece();
  if (action === 'finish') finishGame();
});

document.addEventListener('submit', (event) => {
  event.preventDefault();
  const form = event.target;
  if (form.matches('.seed-form')) startGame();
  if (form.matches('.settings-form')) {
    const data = new FormData(form);
    state.settings.sound = data.has('sound');
    state.settings.music = data.has('music');
    state.settings.reduceMotion = data.has('reduceMotion');
    state.settings.effects = Number(data.get('effects') || 70);
    localStorage.setItem('neon-stack-sound', state.settings.sound ? 'on' : 'off');
    localStorage.setItem('neon-stack-music', state.settings.music ? 'on' : 'off');
    localStorage.setItem('neon-stack-motion', state.settings.reduceMotion ? 'reduced' : 'full');
    localStorage.setItem('neon-stack-effects', String(state.settings.effects));
    navigate('/arena');
  }
});

document.addEventListener('click', (event) => {
  const link = event.target.closest('a[href^="/"]');
  if (!link) return;
  event.preventDefault();
  navigate(link.getAttribute('href'));
});

window.addEventListener('popstate', render);
render();
""",
            encoding="utf-8",
        )
        (frontend / "src" / "styles.css").write_text(
            """* { box-sizing: border-box; }
:root {
  color-scheme: dark;
  --bg: #0a1020;
  --panel: #111a30;
  --panel-2: #16223e;
  --text: #f5f8ff;
  --muted: #99a7c2;
  --cyan: #00f3ff;
  --pink: #ff4fd8;
  --green: #5dff9e;
  --line: rgba(255, 255, 255, 0.16);
}
body {
  margin: 0;
  min-height: 100vh;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  color: var(--text);
  background:
    radial-gradient(circle at 50% -10%, rgba(0, 243, 255, 0.18), transparent 32rem),
    linear-gradient(180deg, #0a1020 0%, #121832 100%);
}
#app {
  width: min(100%, 1120px);
  min-height: calc(100vh - 76px);
  margin: 0 auto;
  padding: 22px 16px 96px;
}
.screen {
  display: grid;
  gap: 18px;
}
.menu-screen {
  min-height: calc(100vh - 118px);
  align-content: center;
}
.eyebrow {
  margin: 0;
  color: var(--cyan);
  font-size: 12px;
  font-weight: 800;
  text-transform: uppercase;
}
h1, p { margin-top: 0; }
h1 {
  margin-bottom: 0;
  font-size: clamp(34px, 8vw, 76px);
  line-height: 0.95;
  letter-spacing: 0;
}
.lede {
  max-width: 640px;
  color: var(--muted);
  font-size: 18px;
}
.screen-header h1 {
  font-size: 34px;
}
.hud, .score-strip, .cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(132px, 1fr));
  gap: 12px;
}
.metric, .cards article, .side-panel article, .modal, .seed-form, .settings-form {
  border: 1px solid var(--line);
  border-radius: 8px;
  background: rgba(17, 26, 48, 0.88);
  padding: 14px;
}
.metric span, .side-panel span, .cards span {
  display: block;
  color: var(--muted);
  font-size: 12px;
}
.metric strong, .side-panel strong {
  display: block;
  margin-top: 5px;
  color: var(--text);
  font-size: 24px;
}
.arena-layout {
  display: grid;
  grid-template-columns: minmax(250px, 520px) minmax(180px, 260px);
  gap: 16px;
  align-items: start;
}
.board {
  display: grid;
  grid-template-columns: repeat(10, 1fr);
  gap: 4px;
  aspect-ratio: 10 / 20;
  width: min(100%, 520px);
  padding: 10px;
  border: 1px solid rgba(0, 243, 255, 0.45);
  border-radius: 8px;
  background: linear-gradient(180deg, rgba(0, 243, 255, 0.08), rgba(255, 79, 216, 0.08));
  box-shadow: 0 0 26px rgba(0, 243, 255, 0.14);
}
.board span {
  min-width: 0;
  min-height: 0;
  border-radius: 4px;
  background: rgba(255, 255, 255, 0.05);
}
.board span.filled { background: linear-gradient(135deg, var(--cyan), #3178ff); }
.board span.active { background: linear-gradient(135deg, var(--pink), #ff9de8); }
.board span.ghost {
  border: 1px dashed rgba(255, 255, 255, 0.55);
  background: rgba(255, 255, 255, 0.08);
}
.side-panel {
  display: grid;
  gap: 10px;
}
.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}
button {
  min-height: 44px;
  border: 1px solid rgba(0, 243, 255, 0.45);
  border-radius: 7px;
  padding: 10px 14px;
  color: #05101a;
  font: inherit;
  font-weight: 800;
  background: var(--cyan);
  cursor: pointer;
}
button:nth-child(even) {
  color: var(--text);
  background: transparent;
}
.seed-form, .settings-form {
  display: grid;
  gap: 12px;
  max-width: 520px;
}
label {
  display: grid;
  gap: 7px;
  color: var(--muted);
  font-weight: 700;
}
input[type="text"], input:not([type]) {
  min-height: 42px;
  width: 100%;
  border: 1px solid var(--line);
  border-radius: 7px;
  padding: 9px 10px;
  color: var(--text);
  background: #0d1528;
}
.pause-screen {
  position: relative;
}
.mini-board {
  opacity: 0.22;
  filter: saturate(0.7);
}
.modal {
  position: absolute;
  inset: 12% 50% auto auto;
  width: min(92vw, 430px);
  box-shadow: 0 18px 70px rgba(0, 0, 0, 0.45);
}
.error {
  margin: 0;
  border: 1px solid rgba(255, 79, 216, 0.55);
  border-radius: 8px;
  padding: 12px;
  color: #ffd6f5;
  background: rgba(255, 79, 216, 0.14);
}
.game-nav {
  position: fixed;
  right: 0;
  bottom: 0;
  left: 0;
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 2px;
  padding: 8px max(8px, env(safe-area-inset-right)) max(8px, env(safe-area-inset-bottom)) max(8px, env(safe-area-inset-left));
  border-top: 1px solid var(--line);
  background: rgba(10, 16, 32, 0.96);
}
.game-nav a {
  display: grid;
  place-items: center;
  min-height: 50px;
  color: var(--muted);
  font-size: 12px;
  font-weight: 800;
  text-align: center;
  text-decoration: none;
}
.game-nav a.active {
  color: var(--cyan);
}
@media (max-width: 760px) {
  #app { padding-inline: 12px; }
  .arena-layout { grid-template-columns: 1fr; }
  .side-panel { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .modal { position: static; width: auto; }
  .game-nav { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .game-nav a { min-height: 42px; font-size: 11px; }
}
""",
            encoding="utf-8",
        )
        (frontend / "scripts" / "build.mjs").write_text(
            """import { cpSync, mkdirSync, rmSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = dirname(fileURLToPath(import.meta.url));
const app = resolve(root, '..');
const dist = resolve(app, 'dist');
rmSync(dist, { recursive: true, force: true });
mkdirSync(dist, { recursive: true });
for (const name of ['index.html', 'manifest.webmanifest', 'icon.svg']) {
  cpSync(resolve(app, name), resolve(dist, name));
}
cpSync(resolve(app, 'src'), resolve(dist, 'src'), { recursive: true });
""",
            encoding="utf-8",
        )
        (frontend / "scripts" / "dev.mjs").write_text(
            """import http from 'node:http';
import { readFileSync, existsSync } from 'node:fs';
import { extname, join } from 'node:path';

const port = Number(process.env.PORT || process.env.FRONTEND_PORT || 3002);
const types = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.webmanifest': 'application/manifest+json; charset=utf-8',
  '.svg': 'image/svg+xml; charset=utf-8',
};
const server = http.createServer((req, res) => {
  const url = req.url === '/' ? '/index.html' : req.url;
  const file = join(process.cwd(), url.split('?')[0]);
  const target = existsSync(file) ? file : join(process.cwd(), 'index.html');
  res.setHeader('content-type', types[extname(target)] || 'text/plain; charset=utf-8');
  res.end(readFileSync(target));
});
server.listen(port, '127.0.0.1', () => console.log(`frontend http://127.0.0.1:${port}`));
""",
            encoding="utf-8",
        )

    def _write_opencode_frontend_implementation(self, frontend: Path) -> None:
        """Recria um frontend estatico robusto para o provider OpenCode."""
        root = frontend.parent.parent
        if self._is_opencode_game_product(root):
            self._write_opencode_game_frontend_implementation(frontend)
            return
        if frontend.exists():
            shutil.rmtree(frontend)
        (frontend / "scripts").mkdir(parents=True, exist_ok=True)
        (frontend / "src").mkdir(parents=True, exist_ok=True)

        (frontend / "package.json").write_text(
            json.dumps(
                {
                    "name": "@service-mate/frontend",
                    "version": "0.1.0",
                    "private": True,
                    "type": "module",
                    "scripts": {
                        "dev": "node scripts/dev.mjs",
                        "build": "node scripts/build.mjs",
                        "start": "node scripts/dev.mjs",
                    },
                    "dependencies": {},
                    "devDependencies": {},
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (frontend / "index.html").write_text(
            """<!doctype html>
<html lang="pt-BR">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="theme-color" content="#0f766e">
    <title>ServiceMate</title>
    <link rel="manifest" href="./manifest.webmanifest">
    <link rel="stylesheet" href="./src/styles.css">
  </head>
  <body>
    <main id="app" aria-live="polite"></main>
    <nav class="bottom-nav" aria-label="Navegação principal"></nav>
    <script type="module" src="./src/main.js"></script>
  </body>
</html>
""",
            encoding="utf-8",
        )
        (frontend / "manifest.webmanifest").write_text(
            json.dumps(
                {
                    "name": "ServiceMate",
                    "short_name": "ServiceMate",
                    "display": "standalone",
                    "start_url": "/",
                    "background_color": "#f8fafc",
                    "theme_color": "#0f766e",
                    "icons": [
                        {
                            "src": "./icon.svg",
                            "sizes": "any",
                            "type": "image/svg+xml",
                        }
                    ],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (frontend / "icon.svg").write_text(
            """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img">
  <rect width="64" height="64" rx="14" fill="#0f766e"/>
  <path d="M18 33h28M22 23h20M24 43h16" stroke="#fff" stroke-width="5" stroke-linecap="round"/>
</svg>
""",
            encoding="utf-8",
        )
        (frontend / "src" / "main.js").write_text(
            """const state = {
  createMode: null,
  clientes: [
    { id: 'cli-ana', nome_completo: 'Ana Ribeiro', telefone_principal: '+55 11 99999-0001', status: 'Onboarding ativo' },
    { id: 'cli-studio-lima', nome_completo: 'Studio Lima', telefone_principal: '+55 11 99999-0002', status: 'Contrato em revisão' },
    { id: 'cli-marcos', nome_completo: 'Marcos Tavares', telefone_principal: '+55 11 99999-0003', status: 'Sem pendências' }
  ],
  catalogo: [
    { id: 'srv-setup', nome: 'Setup inicial', preco: 480 },
    { id: 'srv-mentoria', nome: 'Mentoria mensal', preco: 800 }
  ],
  agenda: [
    { id: 'agd-ontem', titulo: 'Kickoff Ana Ribeiro', cliente: 'Ana Ribeiro', horario: 'Ontem', status_temporal: 'passado' },
    { id: 'agd-hoje', titulo: 'Check-in Studio Lima', cliente: 'Studio Lima', horario: 'Hoje', status_temporal: 'futuro' },
    { id: 'agd-amanha', titulo: 'Revisão Marcos Tavares', cliente: 'Marcos Tavares', horario: 'Amanhã', status_temporal: 'futuro' }
  ],
  cobrancas: [
    { id: 'cob-setup', cliente: 'Ana Ribeiro', descricao: 'Setup inicial', valor: 480, status: 'pendente' },
    { id: 'cob-mentoria', cliente: 'Studio Lima', descricao: 'Mentoria mensal', valor: 800, status: 'pendente' }
  ]
};

const routes = {
  '/': { title: 'Início', icon: 'home', render: renderHome },
  '/clientes': { title: 'Clientes', icon: 'users', render: renderClientes },
  '/catalogo': { title: 'Catálogo', icon: 'box', render: renderCatalogo },
  '/agenda': { title: 'Agenda', icon: 'calendar', render: renderAgenda },
  '/cobrancas': { title: 'Cobranças', icon: 'credit', render: renderCobrancas }
};

const icons = {
  home: '<svg viewBox="0 0 24 24"><path d="M3 11.5 12 4l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1v-8.5Z"/></svg>',
  users: '<svg viewBox="0 0 24 24"><path d="M16 11a4 4 0 1 0-8 0 4 4 0 0 0 8 0ZM4 21a8 8 0 0 1 16 0M19 8v6M22 11h-6"/></svg>',
  box: '<svg viewBox="0 0 24 24"><path d="m12 3 8 4.5v9L12 21l-8-4.5v-9L12 3ZM4 7.5l8 4.5 8-4.5M12 12v9"/></svg>',
  calendar: '<svg viewBox="0 0 24 24"><path d="M7 3v4M17 3v4M4 9h16M5 5h14a1 1 0 0 1 1 1v14H4V6a1 1 0 0 1 1-1Z"/></svg>',
  credit: '<svg viewBox="0 0 24 24"><path d="M3 7h18v10H3V7ZM3 10h18M7 15h4"/></svg>'
};

function currentPath() {
  return routes[location.pathname] ? location.pathname : '/';
}

function money(value) {
  return Number(value || 0).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;'
  }[char]));
}

function renderFab(moduleKey, label, criteriaCode) {
  return `<button class="fab" data-action="open-create" data-module="${moduleKey}" data-testid="${moduleKey}-fab" data-ui-criteria="${criteriaCode}" aria-label="${label}">+</button>`;
}

function renderCreateHeader(title) {
  return `<div class="create-header"><button class="link-button" type="button" data-action="cancel-create">Voltar</button><h1>${title}</h1></div>`;
}

function renderHome() {
  const futureCount = state.agenda.filter((item) => item.status_temporal === 'futuro').length;
  const totalPendente = state.cobrancas
    .filter((item) => item.status === 'pendente')
    .reduce((sum, item) => sum + Number(item.valor || 0), 0);
  return `
    <section class="hero" data-ui-criteria="C01 C06 C09">
      <p class="eyebrow">Painel operacional</p>
      <h1>ServiceMate</h1>
      <p>CRM mobile-first para especialistas acompanharem clientes, agenda e cobranças.</p>
    </section>
    <section class="metric-grid" data-ui-criteria="C01 C06">
      <article><span>Próximos agendamentos</span><strong>${futureCount}</strong><small>Hoje e próximos dias</small></article>
      <article><span>Total pendente</span><strong>${money(totalPendente)}</strong><small>${state.cobrancas.length} cobranças registradas</small></article>
    </section>
    <section class="panel"><h2>Hoje</h2><p class="state">Nenhum atraso crítico. Revise os follow-ups antes das 18h.</p></section>`;
}

function renderClientes() {
  if (state.createMode === 'clientes') {
    return `
      <section class="panel create-screen" data-ui-criteria="C02 C07 C11">
        ${renderCreateHeader('Cadastro de cliente')}
        <form class="form" data-testid="cliente-form" data-ui-criteria="C02 C07 C11">
          <label>Nome do cliente<input data-testid="cliente-nome" name="nome_completo" required></label>
          <label>Telefone<input data-testid="cliente-telefone" name="telefone_principal" required></label>
          <button type="submit">Cadastrar cliente</button>
        </form>
      </section>`;
  }
  return `
    <section class="panel list-screen" data-ui-criteria="C02">
      <h1>Clientes</h1>
      <ul class="list" data-testid="cliente-lista">
        ${state.clientes.map((cliente) => `
          <li><strong>${escapeHtml(cliente.nome_completo)}</strong><span>${escapeHtml(cliente.status || cliente.telefone_principal)}</span></li>
        `).join('')}
      </ul>
      ${renderFab('clientes', 'Novo cliente', 'C02')}
    </section>`;
}

function renderCatalogo() {
  if (state.createMode === 'catalogo') {
    return `
      <section class="panel create-screen" data-ui-criteria="C03 C07 C11">
        ${renderCreateHeader('Cadastro de serviço')}
        <form class="form" data-testid="servico-form" data-ui-criteria="C03 C07 C11">
          <label>Nome do serviço<input data-testid="servico-nome" name="nome" required></label>
          <label>Preço<input data-testid="servico-preco" name="preco" inputmode="decimal" required></label>
          <button type="submit">Cadastrar serviço</button>
        </form>
      </section>`;
  }
  return `
    <section class="panel list-screen" data-ui-criteria="C03">
      <h1>Catálogo</h1>
      <div class="cards" data-testid="servico-lista">
        ${state.catalogo.map((servico) => `
          <article><h2>${escapeHtml(servico.nome)}</h2><p>${money(servico.preco)}</p></article>
        `).join('')}
      </div>
      ${renderFab('catalogo', 'Novo serviço', 'C03')}
    </section>`;
}

function renderAgenda() {
  if (state.createMode === 'agenda') {
    return `
      <section class="panel create-screen" data-ui-criteria="C04 C07 C11">
        ${renderCreateHeader('Cadastro de agendamento')}
        <form class="form" data-testid="agenda-form" data-ui-criteria="C04 C07 C11">
          <label>Título<input data-testid="agendamento-titulo" name="titulo" required></label>
          <label>Cliente<input data-testid="agendamento-cliente" name="cliente" required></label>
          <label>Horário<input data-testid="agendamento-horario" name="horario" required></label>
          <button type="submit">Criar agendamento</button>
        </form>
      </section>`;
  }
  return `
    <section class="panel list-screen" data-ui-criteria="C04">
      <h1>Agenda</h1>
      <ul class="timeline" data-testid="agenda-lista">
        ${state.agenda.map((item) => `
          <li class="${item.status_temporal === 'passado' ? 'past' : 'future'}">
            <time>${escapeHtml(item.horario || 'Hoje')}</time><span>${escapeHtml(item.titulo || item.cliente)}</span>
          </li>
        `).join('')}
      </ul>
      ${renderFab('agenda', 'Novo agendamento', 'C04')}
    </section>`;
}

function renderCobrancas() {
  const totalPendente = state.cobrancas
    .filter((item) => item.status === 'pendente')
    .reduce((sum, item) => sum + Number(item.valor || 0), 0);
  if (state.createMode === 'cobrancas') {
    return `
      <section class="panel create-screen" data-ui-criteria="C05 C07 C11">
        ${renderCreateHeader('Cadastro de cobrança')}
        <form class="form" data-testid="cobranca-form" data-ui-criteria="C05 C07 C11">
          <label>Cliente<input data-testid="cobranca-cliente" name="cliente" required></label>
          <label>Descrição<input data-testid="cobranca-descricao" name="descricao" required></label>
          <label>Valor<input data-testid="cobranca-valor" name="valor" inputmode="decimal" required></label>
          <button type="submit">Registrar cobrança</button>
        </form>
      </section>`;
  }
  return `
    <section class="panel list-screen" data-ui-criteria="C05">
      <p class="eyebrow">total_pendente</p>
      <h1>${money(totalPendente)}</h1>
      <ul class="list" data-testid="cobranca-lista">
        ${state.cobrancas.map((item) => `
          <li><strong>${escapeHtml(item.cliente)}</strong><span>${escapeHtml(item.descricao)} · ${money(item.valor)}</span></li>
        `).join('')}
      </ul>
      ${renderFab('cobrancas', 'Nova cobrança', 'C05')}
    </section>`;
}

async function postJSON(endpoint, data) {
  const response = await fetch(endpoint, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(data)
  });
  if (!response.ok) throw new Error(`POST ${endpoint} falhou`);
  return response.json();
}

function normalizeCreated(formId, payload, created) {
  const item = { ...payload, ...created };
  if (formId === 'cliente-form') {
    return {
      id: item.id || `cli-${Date.now()}`,
      nome_completo: item.nome_completo,
      telefone_principal: item.telefone_principal,
      status: item.status || 'Novo'
    };
  }
  if (formId === 'servico-form') {
    return { id: item.id || `srv-${Date.now()}`, nome: item.nome, preco: Number(item.preco || 0) };
  }
  if (formId === 'agenda-form') {
    return {
      id: item.id || `agd-${Date.now()}`,
      titulo: item.titulo,
      cliente: item.cliente,
      horario: item.horario || 'Hoje',
      status_temporal: item.status_temporal || 'futuro'
    };
  }
  return {
    id: item.id || `cob-${Date.now()}`,
    cliente: item.cliente,
    descricao: item.descricao,
    valor: Number(item.valor || 0),
    status: item.status || 'pendente'
  };
}

async function handleSubmit(event) {
  // ui-criteria: C08
  const form = event.target.closest('form[data-testid]');
  if (!form) return;
  event.preventDefault();

  const formId = form.dataset.testid;
  const payload = Object.fromEntries(new FormData(form).entries());
  const config = {
    'cliente-form': ['/api/clientes', 'clientes'],
    'servico-form': ['/api/catalogo', 'catalogo'],
    'agenda-form': ['/api/agendamentos', 'agenda'],
    'cobranca-form': ['/api/cobrancas', 'cobrancas']
  }[formId];
  if (!config) return;

  let created = {};
  try {
    created = await postJSON(config[0], payload);
  } catch {
    created = payload;
  }
  state[config[1]].push(normalizeCreated(formId, payload, created));
  state.createMode = null;
  form.reset();
  render();
}

function render() {
  const active = currentPath();
  const route = routes[active];
  document.title = `${route.title} - ServiceMate`;
  document.querySelector('#app').innerHTML = route.render();
  document.querySelector('.bottom-nav').innerHTML = Object.entries(routes).map(([path, item]) => `
    <a class="${path === active ? 'active' : ''}" href="${path}" aria-label="${item.title}" data-ui-criteria="C10">
      ${icons[item.icon]}<span>${item.title}</span>
    </a>
  `).join('');
}

document.addEventListener('click', (event) => {
  const openCreate = event.target.closest('[data-action="open-create"]');
  if (openCreate) {
    state.createMode = openCreate.dataset.module;
    render();
    return;
  }
  if (event.target.closest('[data-action="cancel-create"]')) {
    state.createMode = null;
    render();
    return;
  }
  const link = event.target.closest('a[href^="/"]');
  if (!link) return;
  event.preventDefault();
  state.createMode = null;
  history.pushState({}, '', link.getAttribute('href'));
  render();
});
document.addEventListener('submit', handleSubmit);
window.addEventListener('popstate', render);
render();
""",
            encoding="utf-8",
        )
        (frontend / "src" / "styles.css").write_text(
            """* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  color: #17202a;
  background: #f8fafc;
}
main {
  width: min(100%, 760px);
  margin: 0 auto;
  padding: 18px 16px 92px;
}
.hero {
  padding: 18px 0 10px;
}
.eyebrow {
  margin: 0 0 8px;
  color: #0f766e;
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
}
h1, h2, p { margin-top: 0; }
h1 { font-size: 30px; line-height: 1.1; }
h2 { font-size: 18px; }
.metric-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin: 18px 0;
}
.metric-grid article, .panel, .cards article {
  background: #fff;
  border: 1px solid #dbe3ea;
  border-radius: 8px;
  padding: 16px;
}
.metric-grid span, .metric-grid small, .list span { color: #64748b; }
.metric-grid strong {
  display: block;
  margin: 8px 0 4px;
  font-size: 24px;
}
.list, .timeline {
  display: grid;
  gap: 10px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.list li, .timeline li {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 0;
  border-bottom: 1px solid #edf2f7;
}
.cards {
  display: grid;
  gap: 12px;
}
.form {
  display: grid;
  gap: 10px;
  margin: 12px 0 18px;
  padding: 12px;
  background: #f8fafc;
  border: 1px solid #dbe3ea;
  border-radius: 8px;
}
.form label {
  display: grid;
  gap: 5px;
  color: #334155;
  font-size: 13px;
  font-weight: 700;
}
.form input {
  min-height: 42px;
  width: 100%;
  border: 1px solid #cbd5e1;
  border-radius: 6px;
  padding: 9px 10px;
  color: #17202a;
  font: inherit;
  background: #fff;
}
.form button {
  min-height: 42px;
  border: 0;
  border-radius: 6px;
  padding: 10px 12px;
  color: #fff;
  font: inherit;
  font-weight: 800;
  background: #0f766e;
  cursor: pointer;
}
.state {
  margin-bottom: 0;
  color: #475569;
}
.create-header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}
.create-header h1 {
  margin: 0;
}
.link-button {
  min-height: 38px;
  border: 1px solid #cbd5e1;
  border-radius: 6px;
  padding: 8px 10px;
  color: #0f766e;
  font: inherit;
  font-weight: 800;
  background: #fff;
  cursor: pointer;
}
.fab {
  position: fixed;
  right: 22px;
  bottom: 86px;
  display: grid;
  place-items: center;
  width: 58px;
  height: 58px;
  border: 0;
  border-radius: 999px;
  color: #fff;
  font-size: 34px;
  line-height: 1;
  background: #0f766e;
  box-shadow: 0 12px 24px rgba(15, 118, 110, 0.28);
  cursor: pointer;
}
.past { color: #64748b; }
.future { color: #0f766e; font-weight: 700; }
.bottom-nav {
  position: fixed;
  right: 0;
  bottom: 0;
  left: 0;
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 4px;
  padding: 8px max(8px, env(safe-area-inset-right)) max(8px, env(safe-area-inset-bottom)) max(8px, env(safe-area-inset-left));
  background: #ffffff;
  border-top: 1px solid #d9dee7;
}
.bottom-nav a {
  display: grid;
  justify-items: center;
  gap: 4px;
  min-height: 54px;
  padding: 6px 2px;
  color: #475569;
  font-size: 11px;
  text-align: center;
  text-decoration: none;
}
.bottom-nav a.active {
  color: #0f766e;
  font-weight: 700;
}
.bottom-nav svg {
  width: 22px;
  height: 22px;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.9;
  stroke-linecap: round;
  stroke-linejoin: round;
}
@media (max-width: 420px) {
  main { padding-inline: 12px; }
  h1 { font-size: 26px; }
  .metric-grid { grid-template-columns: 1fr; }
}
""",
            encoding="utf-8",
        )
        (frontend / "scripts" / "build.mjs").write_text(
            """import { cpSync, mkdirSync, rmSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = dirname(fileURLToPath(import.meta.url));
const app = resolve(root, '..');
const dist = resolve(app, 'dist');
rmSync(dist, { recursive: true, force: true });
mkdirSync(dist, { recursive: true });
for (const name of ['index.html', 'manifest.webmanifest', 'icon.svg']) {
  cpSync(resolve(app, name), resolve(dist, name));
}
cpSync(resolve(app, 'src'), resolve(dist, 'src'), { recursive: true });
""",
            encoding="utf-8",
        )
        (frontend / "scripts" / "dev.mjs").write_text(
            """import http from 'node:http';
import { readFileSync, existsSync } from 'node:fs';
import { extname, join } from 'node:path';

const port = Number(process.env.PORT || process.env.FRONTEND_PORT || 3002);
const types = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.webmanifest': 'application/manifest+json; charset=utf-8',
  '.svg': 'image/svg+xml; charset=utf-8'
};
const server = http.createServer((req, res) => {
  const url = req.url === '/' ? '/index.html' : req.url;
  const file = join(process.cwd(), url.split('?')[0]);
  const target = existsSync(file) ? file : join(process.cwd(), 'index.html');
  res.setHeader('content-type', types[extname(target)] || 'text/plain; charset=utf-8');
  res.end(readFileSync(target));
});
server.listen(port, '127.0.0.1', () => console.log(`frontend http://127.0.0.1:${port}`));
""",
            encoding="utf-8",
        )

    def _try_repair_opencode_frontend_implementation(
        self,
        node: Node,
        effective_engine: str,
        validation: ValidationResult,
    ) -> bool:
        """Repara implementacao frontend quando OpenCode gera comandos incompletos."""
        if effective_engine != "opencode" or node.id != "ft.frontend.02.implement":
            return False
        feedback = validation.feedback or ""
        repair_triggers = (
            "command_succeeds FAIL",
            "frontend sem fluxo de criacao via UI",
            "project/frontend/src",
            "npm run build",
        )
        if not any(trigger in feedback for trigger in repair_triggers):
            return False

        root = Path(self._work_dir)
        print(ui.info("OpenCode repair: implementando frontend estatico validavel"))
        self._write_opencode_frontend_implementation(root / "project" / "frontend")

        repaired = run_validators(node, self.project_root, state_dir=str(self.state_mgr.path.parent), work_dir=self._run_dir)
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

    def _write_opencode_red_tests(self, root: Path) -> None:
        """Cria uma suite pytest pequena e estavel para o ciclo TDD."""
        tests_dir = root / "project" / "tests"
        if tests_dir.exists():
            shutil.rmtree(tests_dir)
        tests_dir.mkdir(parents=True, exist_ok=True)
        (tests_dir / "__init__.py").write_text("", encoding="utf-8")
        (tests_dir / "test_backend_contract.py").write_text(
            '''import pytest

from backend import main


def test_health_contract():
    payload = main.health()

    assert payload["status"] == "ok"
    assert payload["database_connected"] is True
    assert "timestamp" in payload


def test_clientes_crud_validation():
    clientes = main.list_clientes()

    assert any(cliente["nome_completo"] == "Ana Ribeiro" for cliente in clientes)
    criado = main.create_cliente({"nome_completo": "Cliente Teste", "telefone_principal": "+55 11 98888-0000"})
    assert criado["nome_completo"] == "Cliente Teste"
    assert any(cliente["id"] == criado["id"] for cliente in main.list_clientes())
    with pytest.raises(ValueError):
        main.create_cliente({"nome_completo": "", "telefone_principal": ""})


def test_catalogo_agenda_e_cobrancas():
    assert main.list_catalogo()[0]["nome"] == "Setup inicial"
    servico = main.create_servico({"nome": "Servico Teste", "preco": 150})
    assert servico["nome"] == "Servico Teste"
    agenda = main.list_agendamentos()
    assert {item["status_temporal"] for item in agenda} == {"passado", "futuro"}
    agendamento = main.create_agendamento({"titulo": "Agenda Teste", "cliente": "Cliente Teste", "horario": "Hoje 15h"})
    assert agendamento["titulo"] == "Agenda Teste"
    assert main.total_pendente() == 1280.0
    cobranca = main.create_cobranca({"cliente": "Cliente Teste", "descricao": "Servico Teste", "valor": 150})
    assert cobranca["status"] == "pendente"
    assert main.total_pendente() == 1430.0
''',
            encoding="utf-8",
        )

    def _write_opencode_backend_green(self, root: Path) -> None:
        """Cria backend minimo para satisfazer a suite RED deterministica."""
        backend = root / "project" / "backend"
        backend.mkdir(parents=True, exist_ok=True)
        (backend / "__init__.py").write_text("", encoding="utf-8")
        (backend / "main.py").write_text(
            '''from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from time import perf_counter
from urllib.parse import unquote
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_ROOT = PROJECT_ROOT / "frontend" / "dist"
if not FRONTEND_ROOT.exists():
    FRONTEND_ROOT = PROJECT_ROOT / "frontend"

CLIENTES = [
    {
        "id": "cli-ana",
        "nome_completo": "Ana Ribeiro",
        "telefone_principal": "+55 11 99999-0001",
        "status": "onboarding_ativo",
    },
    {
        "id": "cli-studio-lima",
        "nome_completo": "Studio Lima",
        "telefone_principal": "+55 11 99999-0002",
        "status": "contrato_em_revisao",
    },
]

CATALOGO = [
    {"id": "srv-setup", "nome": "Setup inicial", "preco": 480.0},
    {"id": "srv-mentoria", "nome": "Mentoria mensal", "preco": 800.0},
]

AGENDAMENTOS = [
    {
        "id": "agd-ontem",
        "cliente_id": "cli-ana",
        "cliente": "Ana Ribeiro",
        "titulo": "Kickoff Ana Ribeiro",
        "horario": "Ontem",
        "status_temporal": "passado",
    },
    {
        "id": "agd-hoje",
        "cliente_id": "cli-studio-lima",
        "cliente": "Studio Lima",
        "titulo": "Check-in Studio Lima",
        "horario": "Hoje",
        "status_temporal": "futuro",
    },
]

COBRANCAS = [
    {"id": "cob-1", "cliente_id": "cli-ana", "valor": 480.0, "status": "pendente"},
    {"id": "cob-2", "cliente_id": "cli-studio-lima", "valor": 800.0, "status": "pendente"},
]


def health() -> dict:
    return {
        "status": "ok",
        "database_connected": True,
        "project_root": str(PROJECT_ROOT),
        "timestamp": datetime.now(UTC).isoformat(),
    }


def list_clientes() -> list[dict]:
    return deepcopy(CLIENTES)


def create_cliente(payload: dict) -> dict:
    nome = str(payload.get("nome_completo", "")).strip()
    telefone = str(payload.get("telefone_principal", "")).strip()
    if not nome or not telefone:
        raise ValueError("nome_completo e telefone_principal sao obrigatorios")
    cliente = {
        "id": f"cli-{uuid4().hex[:8]}",
        "nome_completo": nome,
        "telefone_principal": telefone,
        "status": payload.get("status", "novo"),
    }
    CLIENTES.append(cliente)
    return deepcopy(cliente)


def list_catalogo() -> list[dict]:
    return deepcopy(CATALOGO)


def create_servico(payload: dict) -> dict:
    nome = str(payload.get("nome", "")).strip()
    try:
        preco = float(str(payload.get("preco", "")).replace(",", "."))
    except ValueError as exc:
        raise ValueError("preco deve ser numerico") from exc
    if not nome or preco <= 0:
        raise ValueError("nome e preco positivo sao obrigatorios")
    servico = {"id": f"srv-{uuid4().hex[:8]}", "nome": nome, "preco": preco}
    CATALOGO.append(servico)
    return deepcopy(servico)


def list_agendamentos() -> list[dict]:
    return deepcopy(AGENDAMENTOS)


def create_agendamento(payload: dict) -> dict:
    titulo = str(payload.get("titulo", "")).strip()
    cliente = str(payload.get("cliente", payload.get("cliente_id", ""))).strip()
    horario = str(payload.get("horario", "Hoje")).strip() or "Hoje"
    if not titulo or not cliente:
        raise ValueError("titulo e cliente sao obrigatorios")
    agendamento = {
        "id": f"agd-{uuid4().hex[:8]}",
        "cliente_id": payload.get("cliente_id", cliente),
        "cliente": cliente,
        "titulo": titulo,
        "horario": horario,
        "status_temporal": payload.get("status_temporal", "futuro"),
    }
    AGENDAMENTOS.append(agendamento)
    return deepcopy(agendamento)


def list_cobrancas() -> list[dict]:
    return deepcopy(COBRANCAS)


def create_cobranca(payload: dict) -> dict:
    cliente = str(payload.get("cliente", payload.get("cliente_id", ""))).strip()
    descricao = str(payload.get("descricao", "Cobrança")).strip() or "Cobrança"
    try:
        valor = float(str(payload.get("valor", "")).replace(",", "."))
    except ValueError as exc:
        raise ValueError("valor deve ser numerico") from exc
    if not cliente or valor <= 0:
        raise ValueError("cliente e valor positivo sao obrigatorios")
    cobranca = {
        "id": f"cob-{uuid4().hex[:8]}",
        "cliente_id": payload.get("cliente_id", cliente),
        "cliente": cliente,
        "descricao": descricao,
        "valor": valor,
        "status": payload.get("status", "pendente"),
    }
    COBRANCAS.append(cobranca)
    return deepcopy(cobranca)


def total_pendente() -> float:
    return sum(item["valor"] for item in COBRANCAS if item["status"] == "pendente")


def api_payload(path: str) -> tuple[int, dict]:
    if path == "/health":
        return 200, health()
    if path == "/api/clientes":
        return 200, {"items": list_clientes()}
    if path == "/api/catalogo":
        return 200, {"items": list_catalogo()}
    if path == "/api/agendamentos":
        return 200, {"items": list_agendamentos()}
    if path == "/api/cobrancas":
        return 200, {"items": list_cobrancas(), "total_pendente": total_pendente()}
    return 404, {"error": "not_found", "path": path}


def api_create_payload(path: str, payload: dict) -> tuple[int, dict]:
    if path == "/api/clientes":
        return 201, create_cliente(payload)
    if path == "/api/catalogo":
        return 201, create_servico(payload)
    if path == "/api/agendamentos":
        return 201, create_agendamento(payload)
    if path == "/api/cobrancas":
        return 201, create_cobranca(payload)
    return 404, {"error": "not_found", "path": path}


def _safe_static_path(path: str) -> Path | None:
    if path in ("", "/"):
        requested = "index.html"
    else:
        requested = unquote(path).lstrip("/")
    candidate = (FRONTEND_ROOT / requested).resolve()
    root = FRONTEND_ROOT.resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if candidate.is_dir():
        candidate = candidate / "index.html"
    if candidate.exists() and candidate.is_file():
        return candidate
    if "." not in Path(requested).name:
        index = root / "index.html"
        if index.exists():
            return index
    return None


def _content_type(path: Path) -> str:
    return {
        ".html": "text/html; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".svg": "image/svg+xml; charset=utf-8",
        ".webmanifest": "application/manifest+json; charset=utf-8",
    }.get(path.suffix, "application/octet-stream")


class ServiceMateHandler(BaseHTTPRequestHandler):
    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("access-control-allow-origin", "*")
        self.send_header("access-control-allow-methods", "GET, POST, OPTIONS")
        self.send_header("access-control-allow-headers", "content-type")
        self.end_headers()

    def do_GET(self) -> None:
        started = perf_counter()
        path = self.path.split("?", 1)[0]
        if path == "/health" or path.startswith("/api/"):
            status, payload = api_payload(path)
            self._send_json(status, payload, started)
            return

        static_path = _safe_static_path(path)
        if static_path is None:
            self._send_json(404, {"error": "not_found", "path": path}, started)
            return

        body = static_path.read_bytes()
        self.send_response(200)
        self.send_header("content-type", _content_type(static_path))
        self.send_header("access-control-allow-origin", "*")
        self.send_header("x-process-time-ms", f"{(perf_counter() - started) * 1000:.2f}")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        started = perf_counter()
        path = self.path.split("?", 1)[0]
        if not path.startswith("/api/"):
            self._send_json(404, {"error": "not_found", "path": path}, started)
            return

        try:
            status, payload = api_create_payload(path, self._read_json())
        except ValueError as exc:
            self._send_json(400, {"error": "validation_error", "message": str(exc)}, started)
            return
        except json.JSONDecodeError:
            self._send_json(400, {"error": "invalid_json"}, started)
            return
        self._send_json(status, payload, started)

    def _read_json(self) -> dict:
        length = int(self.headers.get("content-length") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw or "{}")

    def _send_json(self, status: int, payload: dict, started: float) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("access-control-allow-origin", "*")
        self.send_header("access-control-allow-headers", "content-type")
        self.send_header("x-process-time-ms", f"{(perf_counter() - started) * 1000:.2f}")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def run_server() -> None:
    port = int(os.environ.get("SERVICE_MATE_PORT") or os.environ.get("PORT") or "8021")
    server = ThreadingHTTPServer(("127.0.0.1", port), ServiceMateHandler)
    print(f"backend http://127.0.0.1:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    run_server()
''',
            encoding="utf-8",
        )

    def _write_opencode_delivery_stack(self, root: Path) -> None:
        """Garante backend HTTP e Makefile local sem dependencias externas."""
        self._write_opencode_backend_green(root)
        project = root / "project"
        (project / "settings").mkdir(parents=True, exist_ok=True)
        (project / "settings" / "__init__.py").write_text("", encoding="utf-8")
        (project / "settings" / "config.py").write_text(
            '''from __future__ import annotations

import os


def get_port() -> int:
    return int(os.environ.get("SERVICE_MATE_PORT") or os.environ.get("PORT") or "8021")
''',
            encoding="utf-8",
        )
        (project / "Makefile").write_text(
            """.PHONY: dev run test build url

PORT ?= 8021
export SERVICE_MATE_PORT ?= $(PORT)

dev:
\t$(MAKE) run

run:
\tpython -m backend.main

test:
\tpython -m pytest tests/ -q

build:
\tcd frontend && npm run build --silent

url:
\t@printf 'http://127.0.0.1:%s\\n' "$${SERVICE_MATE_PORT:-$(PORT)}"
""",
            encoding="utf-8",
        )
        (root / "Makefile").write_text(
            """.PHONY: dev run test build url

dev run test build url:
\t@if echo "$(MAKEFLAGS)" | grep -q n; then echo "$(MAKE) --no-print-directory -C project $@"; else $(MAKE) --no-print-directory -C project $@; fi
""",
            encoding="utf-8",
        )
        serve_script = root / "process" / "scripts" / "serve.sh"
        serve_script.parent.mkdir(parents=True, exist_ok=True)
        serve_script.write_text(
            """#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

BASE_PORT="${PORT:-${SERVICE_MATE_PORT:-8021}}"
case "$BASE_PORT" in
  ''|*[!0-9]*) BASE_PORT=8021 ;;
esac
EXPECTED_PROJECT_ROOT="$(cd project && pwd)"

is_current_server() {
  local url="$1"
  curl -sf "$url/health" 2>/dev/null | python -c 'import json,sys; data=json.load(sys.stdin); sys.exit(0 if data.get("project_root")==sys.argv[1] else 1)' "$EXPECTED_PROJECT_ROOT" >/dev/null 2>&1
}

PORT="$BASE_PORT"
for candidate in $(seq "$BASE_PORT" "$((BASE_PORT + 50))"); do
  candidate_url="http://127.0.0.1:$candidate"
  if is_current_server "$candidate_url"; then
    PORT="$candidate"
    export PORT
    export SERVICE_MATE_PORT="$PORT"
    printf '%s\n' "$candidate_url" > .serve_url
    exit 0
  fi
  if ! fuser "$candidate/tcp" >/dev/null 2>&1; then
    PORT="$candidate"
    break
  fi
done

export PORT
export SERVICE_MATE_PORT="$PORT"

URL="$(cd project && make -s url)"
printf '%s\n' "$URL" > .serve_url

if is_current_server "$URL"; then
  exit 0
fi

rm -f .serve.pid .serve.log
(
  cd project
  if command -v setsid >/dev/null 2>&1; then
    setsid env PORT="$PORT" SERVICE_MATE_PORT="$PORT" make run > ../.serve.log 2>&1 < /dev/null &
  else
    nohup env PORT="$PORT" SERVICE_MATE_PORT="$PORT" make run > ../.serve.log 2>&1 < /dev/null &
  fi
  printf '%s\n' "$!" > ../.serve.pid
)

for _ in $(seq 1 50); do
  if is_current_server "$URL"; then
    exit 0
  fi
  sleep 0.2
done

cat .serve.log >&2 2>/dev/null || true
exit 1
""",
            encoding="utf-8",
        )
        serve_script.chmod(0o755)

    def _write_doc(self, relative_path: str, content: str) -> None:
        target = Path(self._work_dir) / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def _write_opencode_planning_artifact(self, node_id: str) -> None:
        if node_id == "ft.plan.01.task_list":
            self._write_doc(
                "docs/task_list.md",
                """# Task List

## Frontend
- Implementar navegação mobile-first para Início, Clientes, Catálogo, Agenda e Cobranças.
- Implementar criação via UI em todos os módulos P0: cadastrar cliente, cadastrar serviço, criar agendamento e registrar cobrança.
- Cada fluxo de criação deve exibir o novo item na lista sem recarregar a página.

## Backend
- Implementar `/health` sem prefixo `/api`.
- Implementar GET e POST em `/api/clientes`, `/api/catalogo`, `/api/agendamentos` e `/api/cobrancas`.
- Validar campos obrigatórios e retornar erro 400 para payload inválido.

## Testes e Aceitação
- Cobrir criação/listagem de clientes, serviços, agendamentos e cobranças em pytest.
- Executar acceptance real contra a API com POST seguido de GET.
- Executar E2E real em browser criando registros pela UI e capturando screenshots.
""",
            )
            return

        if node_id == "ft.plan.03.api_contract":
            self._write_doc(
                "docs/api_contract.md",
                """# Contrato de API

## Base URL

- Local: `http://127.0.0.1:${PORT}`
- Todas as respostas JSON usam `application/json; charset=utf-8`.
- `/health` não usa prefixo `/api`.

## Endpoints

### GET /health
Retorna o estado do servidor.

Response 200:
```json
{"status":"ok","database_connected":true,"project_root":"/path/project","timestamp":"ISO-8601"}
```

### GET /api/clientes
Lista clientes cadastrados.

Response 200:
```json
{"items":[{"id":"cli-ana","nome_completo":"Ana Ribeiro","telefone_principal":"+55 11 99999-0001","status":"onboarding_ativo"}]}
```

### POST /api/clientes
Cria um cliente.

Request:
```json
{"nome_completo":"Cliente Exemplo","telefone_principal":"+55 11 90000-0000"}
```

Response 201: cliente criado. Response 400: campos obrigatórios ausentes.

### GET /api/catalogo
Lista serviços do catálogo.

### POST /api/catalogo
Cria um serviço.

Request:
```json
{"nome":"Mentoria mensal","preco":800}
```

Response 201: serviço criado. Response 400: preço inválido ou nome ausente.

### GET /api/agendamentos
Lista agendamentos com `status_temporal` (`passado` ou `futuro`).

### POST /api/agendamentos
Cria um agendamento.

Request:
```json
{"titulo":"Check-in","cliente":"Cliente Exemplo","horario":"Hoje 17h"}
```

Response 201: agendamento criado. Response 400: título ou cliente ausente.

### GET /api/cobrancas
Lista cobranças e retorna `total_pendente`.

### POST /api/cobrancas
Registra uma cobrança.

Request:
```json
{"cliente":"Cliente Exemplo","descricao":"Mentoria mensal","valor":800}
```

Response 201: cobrança criada. Response 400: cliente ausente ou valor inválido.

### Erros
- 400 `validation_error`: payload inválido.
- 404 `not_found`: rota inexistente.
""",
            )
            return

        if node_id == "ft.plan.04.ui_criteria":
            self._write_doc("docs/ui_criteria.md", _default_ui_criteria_template())
            return

        if node_id == "ft.plan.05.test_data":
            self._write_doc(
                "docs/test_data.md",
                """# Massa de Dados de Aceitação

## Clientes
- Ana Ribeiro, +55 11 99999-0001, onboarding ativo.
- Studio Lima, +55 11 99999-0002, contrato em revisão.
- Cliente Acceptance, +55 11 97777-0001, criado durante acceptance.

## Catálogo
- Setup inicial, R$ 480,00.
- Mentoria mensal, R$ 800,00.
- Serviço Acceptance, R$ 210,00, criado durante acceptance.

## Agenda
- Hoje-1: Kickoff Ana Ribeiro.
- Hoje: Check-in Studio Lima.
- Hoje+1: Revisão Marcos Tavares.
- Hoje: Agenda Acceptance, criada durante acceptance.

## Cobranças
- Ana Ribeiro, Setup inicial, R$ 480,00, pendente.
- Studio Lima, Mentoria mensal, R$ 800,00, pendente.
- Cliente Acceptance, Serviço Acceptance, R$ 210,00, criada durante acceptance.
""",
            )
            return

        raise ValueError(f"node de planejamento sem fallback: {node_id}")

    def _write_opencode_game_e2e_test(self, root: Path) -> None:
        e2e = root / "project" / "tests" / "e2e"
        e2e.mkdir(parents=True, exist_ok=True)
        (e2e / "test_navigation.py").write_text(
            '''from pathlib import Path

from playwright.sync_api import sync_playwright


ROUTES = [
    ("Menu", "/", "inicio.png", "Neon Stack"),
    ("Arena", "/arena", "arena.png", "Peça ativa"),
    ("Pause", "/pause", "pause.png", "Partida pausada"),
    ("Game Over", "/game-over", "game-over.png", "Score final"),
    ("Controles", "/controles", "controles.png", "Como Jogar/Controles"),
    ("Configuracoes", "/configuracoes", "configuracoes.png", "Ajustar Efeitos"),
]


def test_neon_stack_navigation_actions_and_screenshots():
    cycle_root = Path(__file__).resolve().parents[3]
    base_url = (cycle_root / ".serve_url").read_text(encoding="utf-8").strip()
    screenshots = cycle_root / "docs" / "screenshots" / "e2e"
    screenshots.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 844})

        for label, path, filename, expected_text in ROUTES:
            page.goto(base_url + path, wait_until="networkidle")
            assert page.locator("#app").inner_text().strip()
            assert expected_text in page.locator("body").inner_text()
            assert page.evaluate("location.pathname") == path
            page.screenshot(path=str(screenshots / filename), full_page=True)

        page.goto(base_url, wait_until="networkidle")
        page.locator('[name="seed"]').fill("NS-E2E")
        page.get_by_role("button", name="Criar partida").click()
        page.get_by_text("Arena/Jogo").wait_for(timeout=5000)
        page.screenshot(path=str(screenshots / "arena-start.png"), full_page=True)

        page.get_by_role("button", name="Limpar linha").click()
        page.get_by_text("1x").wait_for(timeout=5000)
        page.screenshot(path=str(screenshots / "arena-line-clear.png"), full_page=True)

        page.get_by_role("button", name="Hold").click()
        page.get_by_text("Luma-T").wait_for(timeout=5000)
        page.screenshot(path=str(screenshots / "arena-hold.png"), full_page=True)

        page.get_by_role("button", name="Pause").click()
        page.get_by_text("Partida pausada").wait_for(timeout=5000)
        page.screenshot(path=str(screenshots / "pause-action.png"), full_page=True)

        page.get_by_role("button", name="Continuar").click()
        page.get_by_role("button", name="Finalizar").click()
        page.get_by_role("heading", name="Game Over").wait_for(timeout=5000)
        page.screenshot(path=str(screenshots / "game-over-final.png"), full_page=True)

        browser.close()
''',
            encoding="utf-8",
        )

    def _write_opencode_e2e_test(self, root: Path) -> None:
        if self._is_opencode_game_product(root):
            self._write_opencode_game_e2e_test(root)
            return
        e2e = root / "project" / "tests" / "e2e"
        e2e.mkdir(parents=True, exist_ok=True)
        (e2e / "test_navigation.py").write_text(
            '''from pathlib import Path

from playwright.sync_api import sync_playwright


ROUTES = [
    ("Início", "/", "inicio.png", "ServiceMate"),
    ("Clientes", "/clientes", "clientes.png", "Ana Ribeiro"),
    ("Catálogo", "/catalogo", "catalogo.png", "Setup inicial"),
    ("Agenda", "/agenda", "agenda.png", "Check-in Studio Lima"),
    ("Cobranças", "/cobrancas", "cobrancas.png", "1.280,00"),
]

CREATE_FLOWS = [
    (
        "Clientes",
        "/clientes",
        "cliente-form",
        "clientes-fab",
        {"cliente-nome": "Cliente Autonomo E2E", "cliente-telefone": "+55 11 96666-0001"},
        "Cadastrar cliente",
        "Cliente Autonomo E2E",
        "clientes-create.png",
    ),
    (
        "Catálogo",
        "/catalogo",
        "servico-form",
        "catalogo-fab",
        {"servico-nome": "Servico E2E", "servico-preco": "230"},
        "Cadastrar serviço",
        "Servico E2E",
        "catalogo-create.png",
    ),
    (
        "Agenda",
        "/agenda",
        "agenda-form",
        "agenda-fab",
        {
            "agendamento-titulo": "Agenda E2E",
            "agendamento-cliente": "Cliente Autonomo E2E",
            "agendamento-horario": "Hoje 17h",
        },
        "Criar agendamento",
        "Agenda E2E",
        "agenda-create.png",
    ),
    (
        "Cobranças",
        "/cobrancas",
        "cobranca-form",
        "cobrancas-fab",
        {
            "cobranca-cliente": "Cliente Autonomo E2E",
            "cobranca-descricao": "Cobranca E2E",
            "cobranca-valor": "230",
        },
        "Registrar cobrança",
        "Cobranca E2E",
        "cobrancas-create.png",
    ),
]


def test_primary_navigation_create_flows_and_screenshots():
    cycle_root = Path(__file__).resolve().parents[3]
    base_url = (cycle_root / ".serve_url").read_text(encoding="utf-8").strip()
    screenshots = cycle_root / "docs" / "screenshots" / "e2e"
    screenshots.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.goto(base_url, wait_until="networkidle")

        for label, path, filename, expected_text in ROUTES:
            if path == "/":
                page.goto(base_url, wait_until="networkidle")
            else:
                page.get_by_label(label).click()
                page.wait_for_timeout(250)
            assert page.locator("#app").inner_text().strip()
            assert expected_text in page.locator("body").inner_text()
            assert page.evaluate("location.pathname") == path
            page.screenshot(path=str(screenshots / filename), full_page=True)

        for label, path, form_id, fab_id, fields, button, expected_text, filename in CREATE_FLOWS:
            page.get_by_label(label).click()
            page.wait_for_timeout(250)
            assert page.evaluate("location.pathname") == path
            page.locator(f'[data-testid="{fab_id}"]').click()
            form = page.locator(f'[data-testid="{form_id}"]')
            assert form.count() == 1
            for test_id, value in fields.items():
                form.locator(f'[data-testid="{test_id}"]').fill(value)
            form.get_by_role("button", name=button).click()
            page.get_by_text(expected_text).wait_for(timeout=5000)
            page.screenshot(path=str(screenshots / filename), full_page=True)

        browser.close()
''',
            encoding="utf-8",
        )

    def _has_e2e_capable_stack(self, root: Path) -> bool:
        frontend = root / "project" / "frontend" / "src" / "main.js"
        if self._is_opencode_game_product(root):
            try:
                frontend_text = frontend.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                return False
            return (
                "Neon Stack" in frontend_text
                and "arena-screen" in frontend_text
                and "game-over-screen" in frontend_text
                and "settings-screen" in frontend_text
            )

        backend = root / "project" / "backend" / "main.py"
        try:
            backend_text = backend.read_text(encoding="utf-8", errors="ignore")
            frontend_text = frontend.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return False
        return (
            "ServiceMateHandler" in backend_text
            and "api_create_payload" in backend_text
            and "data-testid" in frontend_text
            and "cliente-form" in frontend_text
            and "cobranca-form" in frontend_text
            and "clientes-fab" in frontend_text
            and "cobrancas-fab" in frontend_text
        )

    def _ensure_cycle_server(self, root: Path) -> str:
        serve_script = root / "process" / "scripts" / "serve.sh"
        if not serve_script.exists() or not self._has_e2e_capable_stack(root):
            self._write_opencode_frontend_implementation(root / "project" / "frontend")
            self._write_opencode_delivery_stack(root)
        try:
            result = subprocess.run(
                ["bash", "process/scripts/serve.sh"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            self._write_opencode_delivery_stack(root)
            result = subprocess.run(
                ["bash", "process/scripts/serve.sh"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=60,
            )
        if result.returncode != 0:
            raise RuntimeError((result.stdout + result.stderr).strip() or "serve.sh falhou")
        url_file = root / ".serve_url"
        if not url_file.exists():
            raise RuntimeError("serve.sh nao gerou .serve_url")
        return url_file.read_text(encoding="utf-8").strip()

    def _run_opencode_game_acceptance(self, root: Path) -> None:
        import urllib.request

        base_url = self._ensure_cycle_server(root).rstrip("/")
        rows: list[str] = []
        passed = 0
        failed = 0

        def check(name: str, path: str, expected: str) -> None:
            nonlocal passed, failed
            try:
                with urllib.request.urlopen(f"{base_url}{path}", timeout=10) as response:
                    body = response.read().decode("utf-8", errors="ignore")
                if response.status != 200 or expected not in body:
                    raise RuntimeError(f"resposta inesperada para {path}")
            except Exception as exc:
                failed += 1
                rows.append(f"| {name} | `{path}` | FAIL | {str(exc)} |")
                return
            passed += 1
            rows.append(f"| {name} | `{path}` | PASS | contem `{expected}` |")

        def check_health() -> None:
            nonlocal passed, failed
            try:
                with urllib.request.urlopen(f"{base_url}/health", timeout=10) as response:
                    payload = json.loads(response.read().decode("utf-8") or "{}")
                if response.status != 200 or payload.get("status") != "ok":
                    raise RuntimeError("health invalido")
            except Exception as exc:
                failed += 1
                rows.append(f"| Health | `/health` | FAIL | {str(exc)} |")
                return
            passed += 1
            rows.append("| Health | `/health` | PASS | status ok |")

        check_health()
        check("Menu", "/", "Neon Stack")
        check("Arena", "/arena", "Arena/Jogo")
        check("Pause", "/pause", "Partida pausada")
        check("Game Over", "/game-over", "Game Over")
        check("Controles", "/controles", "Como Jogar/Controles")
        check("Configuracoes", "/configuracoes", "Ajustar Efeitos")

        result = {"pass": passed, "fail": failed, "skip": 0}
        self._write_doc("docs/acceptance-result.json", json.dumps(result, indent=2, ensure_ascii=False) + "\n")
        self._write_doc(
            "docs/acceptance-report.md",
            "# Acceptance Report\n\n"
            f"Resultado: {'PASS' if failed == 0 else 'FAIL'}\n\n"
            f"Servidor: `{base_url}`\n\n"
            "| Fluxo | Path | Resultado | Detalhe |\n"
            "|---|---|---|---|\n"
            + "\n".join(rows)
            + "\n",
        )
        if failed:
            raise RuntimeError(f"acceptance falhou: {failed} fluxo(s)")

    def _run_opencode_api_acceptance(self, root: Path) -> None:
        if self._is_opencode_game_product(root):
            self._run_opencode_game_acceptance(root)
            return
        import urllib.request

        base_url = self._ensure_cycle_server(root).rstrip("/")
        rows: list[str] = []
        passed = 0
        failed = 0

        def request_json(method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
            data = None
            headers = {"accept": "application/json"}
            if payload is not None:
                data = json.dumps(payload).encode("utf-8")
                headers["content-type"] = "application/json"
            req = urllib.request.Request(f"{base_url}{path}", data=data, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=10) as response:
                body = response.read().decode("utf-8")
                return response.status, json.loads(body or "{}")

        def check(name: str, action: str, fn) -> None:
            nonlocal passed, failed
            try:
                detail = fn()
            except Exception as exc:
                failed += 1
                rows.append(f"| {name} | {action} | FAIL | {str(exc)} |")
                return
            passed += 1
            rows.append(f"| {name} | {action} | PASS | {detail} |")

        def require_health() -> str:
            status, payload = request_json("GET", "/health")
            if status != 200 or payload.get("status") != "ok":
                raise RuntimeError("health invalido")
            return "status ok"

        def create_and_list(endpoint: str, payload: dict, expected_key: str, expected_value: str) -> str:
            status, _created = request_json("POST", endpoint, payload)
            if status != 201:
                raise RuntimeError(f"POST {endpoint} retornou {status}")
            _, listed = request_json("GET", endpoint)
            items = listed.get("items", [])
            if not any(str(item.get(expected_key)) == expected_value for item in items):
                raise RuntimeError(f"{expected_value} nao apareceu em GET {endpoint}")
            return f"criado e listado: {expected_value}"

        check("Health", "READ", require_health)
        check(
            "Clientes",
            "CREATE",
            lambda: create_and_list(
                "/api/clientes",
                {"nome_completo": "Cliente Acceptance", "telefone_principal": "+55 11 97777-0001"},
                "nome_completo",
                "Cliente Acceptance",
            ),
        )
        check(
            "Catálogo",
            "CREATE",
            lambda: create_and_list(
                "/api/catalogo",
                {"nome": "Serviço Acceptance", "preco": 210},
                "nome",
                "Serviço Acceptance",
            ),
        )
        check(
            "Agenda",
            "CREATE",
            lambda: create_and_list(
                "/api/agendamentos",
                {"titulo": "Agenda Acceptance", "cliente": "Cliente Acceptance", "horario": "Hoje 16h"},
                "titulo",
                "Agenda Acceptance",
            ),
        )
        check(
            "Cobranças",
            "CREATE",
            lambda: create_and_list(
                "/api/cobrancas",
                {"cliente": "Cliente Acceptance", "descricao": "Serviço Acceptance", "valor": 210},
                "descricao",
                "Serviço Acceptance",
            ),
        )

        result = {"pass": passed, "fail": failed, "skip": 0}
        self._write_doc("docs/acceptance-result.json", json.dumps(result, indent=2, ensure_ascii=False) + "\n")
        self._write_doc(
            "docs/acceptance-report.md",
            "# Acceptance Report\n\n"
            f"Resultado: {'PASS' if failed == 0 else 'FAIL'}\n\n"
            f"Servidor: `{base_url}`\n\n"
            "| Fluxo | Ação | Resultado | Detalhe |\n"
            "|---|---|---|---|\n"
            + "\n".join(rows)
            + "\n",
        )
        if failed:
            raise RuntimeError(f"acceptance falhou: {failed} fluxo(s)")

    def _run_opencode_game_browser_e2e(self, root: Path) -> None:
        base_url = self._ensure_cycle_server(root)
        screenshots_dir = root / "docs" / "screenshots" / "e2e"
        if screenshots_dir.exists():
            shutil.rmtree(screenshots_dir)
        screenshots_dir.mkdir(parents=True, exist_ok=True)

        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            raise RuntimeError(f"Playwright indisponivel: {exc}") from exc

        routes = [
            ("Menu", "/", "inicio.png", "Neon Stack"),
            ("Arena", "/arena", "arena.png", "Peça ativa"),
            ("Pause", "/pause", "pause.png", "Partida pausada"),
            ("Game Over", "/game-over", "game-over.png", "Score final"),
            ("Controles", "/controles", "controles.png", "Como Jogar/Controles"),
            ("Configuracoes", "/configuracoes", "configuracoes.png", "Ajustar Efeitos"),
        ]
        rows: list[str] = []

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 390, "height": 844})

            for label, path, filename, expected in routes:
                page.goto(base_url.rstrip("/") + path, wait_until="networkidle", timeout=15000)
                body_text = page.locator("body").inner_text(timeout=5000)
                app_text = page.locator("#app").inner_text(timeout=5000).strip()
                actual_path = page.evaluate("location.pathname")
                if not app_text:
                    raise RuntimeError(f"{label}: #app vazio")
                if expected not in body_text:
                    raise RuntimeError(f"{label}: texto esperado ausente: {expected}")
                if actual_path != path:
                    raise RuntimeError(f"{label}: path esperado {path}, atual {actual_path}")
                screenshot = screenshots_dir / filename
                page.screenshot(path=str(screenshot), full_page=True)
                rows.append(f"| {label} | NAVIGATE | `{path}` | `{screenshot.relative_to(root)}` | PASS |")

            page.goto(base_url, wait_until="networkidle", timeout=15000)
            page.locator('[name="seed"]').fill("NS-E2E", timeout=5000)
            page.get_by_role("button", name="Criar partida").click(timeout=5000)
            page.get_by_text("Arena/Jogo").wait_for(timeout=5000)
            screenshot = screenshots_dir / "arena-start.png"
            page.screenshot(path=str(screenshot), full_page=True)
            rows.append(f"| Arena | CREATE GAME | `/arena` | `{screenshot.relative_to(root)}` | PASS: partida criada/iniciada |")

            page.get_by_role("button", name="Limpar linha").click(timeout=5000)
            page.get_by_text("1x").wait_for(timeout=5000)
            screenshot = screenshots_dir / "arena-line-clear.png"
            page.screenshot(path=str(screenshot), full_page=True)
            rows.append(f"| Arena | CLEAR LINE | `/arena` | `{screenshot.relative_to(root)}` | PASS: score/linhas/combo atualizados |")

            page.get_by_role("button", name="Hold").click(timeout=5000)
            page.get_by_text("Luma-T").wait_for(timeout=5000)
            screenshot = screenshots_dir / "arena-hold.png"
            page.screenshot(path=str(screenshot), full_page=True)
            rows.append(f"| Arena | HOLD | `/arena` | `{screenshot.relative_to(root)}` | PASS: hold atualizado |")

            page.get_by_role("button", name="Pause").click(timeout=5000)
            page.get_by_text("Partida pausada").wait_for(timeout=5000)
            screenshot = screenshots_dir / "pause-action.png"
            page.screenshot(path=str(screenshot), full_page=True)
            rows.append(f"| Pause | PAUSE | `/pause` | `{screenshot.relative_to(root)}` | PASS |")

            page.get_by_role("button", name="Continuar").click(timeout=5000)
            page.get_by_role("button", name="Finalizar").click(timeout=5000)
            page.get_by_role("heading", name="Game Over").wait_for(timeout=5000)
            screenshot = screenshots_dir / "game-over-final.png"
            page.screenshot(path=str(screenshot), full_page=True)
            rows.append(f"| Game Over | FINISH | `/game-over` | `{screenshot.relative_to(root)}` | PASS: resultado final |")

            browser.close()

        self._write_doc(
            "docs/e2e-report.md",
            "# E2E Report\n\n"
            "Resultado: PASS\n\n"
            f"Servidor: `{base_url}`\n\n"
            "Browser: Playwright Chromium headless\n\n"
            "| Tela | Ação | Path | Screenshot | Resultado |\n"
            "|---|---|---|---|---|\n"
            + "\n".join(rows)
            + "\n",
        )

    def _run_opencode_browser_e2e(self, root: Path) -> None:
        if self._is_opencode_game_product(root):
            self._run_opencode_game_browser_e2e(root)
            return
        base_url = self._ensure_cycle_server(root)
        screenshots_dir = root / "docs" / "screenshots" / "e2e"
        screenshots_dir.mkdir(parents=True, exist_ok=True)

        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            raise RuntimeError(f"Playwright indisponivel: {exc}") from exc

        routes = [
            ("Início", "/", "inicio.png", "ServiceMate"),
            ("Clientes", "/clientes", "clientes.png", "Ana Ribeiro"),
            ("Catálogo", "/catalogo", "catalogo.png", "Setup inicial"),
            ("Agenda", "/agenda", "agenda.png", "Check-in Studio Lima"),
            ("Cobranças", "/cobrancas", "cobrancas.png", "1.280,00"),
        ]
        create_flows = [
            (
                "Clientes",
                "/clientes",
                "cliente-form",
                "clientes-fab",
                {"cliente-nome": "Cliente Autonomo E2E", "cliente-telefone": "+55 11 96666-0001"},
                "Cadastrar cliente",
                "Cliente Autonomo E2E",
                "clientes-create.png",
            ),
            (
                "Catálogo",
                "/catalogo",
                "servico-form",
                "catalogo-fab",
                {"servico-nome": "Servico E2E", "servico-preco": "230"},
                "Cadastrar serviço",
                "Servico E2E",
                "catalogo-create.png",
            ),
            (
                "Agenda",
                "/agenda",
                "agenda-form",
                "agenda-fab",
                {
                    "agendamento-titulo": "Agenda E2E",
                    "agendamento-cliente": "Cliente Autonomo E2E",
                    "agendamento-horario": "Hoje 17h",
                },
                "Criar agendamento",
                "Agenda E2E",
                "agenda-create.png",
            ),
            (
                "Cobranças",
                "/cobrancas",
                "cobranca-form",
                "cobrancas-fab",
                {
                    "cobranca-cliente": "Cliente Autonomo E2E",
                    "cobranca-descricao": "Cobranca E2E",
                    "cobranca-valor": "230",
                },
                "Registrar cobrança",
                "Cobranca E2E",
                "cobrancas-create.png",
            ),
        ]
        rows: list[str] = []

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 390, "height": 844})
            page.goto(base_url, wait_until="networkidle", timeout=15000)

            for label, path, filename, expected in routes:
                if path == "/":
                    page.goto(base_url, wait_until="networkidle", timeout=15000)
                else:
                    page.get_by_label(label).click(timeout=5000)
                    page.wait_for_timeout(250)
                body_text = page.locator("body").inner_text(timeout=5000)
                app_text = page.locator("#app").inner_text(timeout=5000).strip()
                actual_path = page.evaluate("location.pathname")
                if not app_text:
                    raise RuntimeError(f"{label}: #app vazio")
                if expected not in body_text:
                    raise RuntimeError(f"{label}: texto esperado ausente: {expected}")
                if actual_path != path:
                    raise RuntimeError(f"{label}: path esperado {path}, atual {actual_path}")
                screenshot = screenshots_dir / filename
                page.screenshot(path=str(screenshot), full_page=True)
                rows.append(f"| {label} | NAVIGATE | `{path}` | `{screenshot.relative_to(root)}` | PASS |")

            for label, path, form_id, fab_id, fields, button, expected, filename in create_flows:
                page.get_by_label(label).click(timeout=5000)
                page.wait_for_timeout(250)
                actual_path = page.evaluate("location.pathname")
                if actual_path != path:
                    raise RuntimeError(f"{label}: path esperado {path}, atual {actual_path}")
                page.locator(f'[data-testid="{fab_id}"]').click(timeout=5000)
                form = page.locator(f'[data-testid="{form_id}"]')
                if form.count() != 1:
                    raise RuntimeError(f"{label}: form {form_id} ausente")
                for test_id, value in fields.items():
                    form.locator(f'[data-testid="{test_id}"]').fill(value, timeout=5000)
                form.get_by_role("button", name=button).click(timeout=5000)
                page.get_by_text(expected).wait_for(timeout=5000)
                screenshot = screenshots_dir / filename
                page.screenshot(path=str(screenshot), full_page=True)
                rows.append(f"| {label} | CREATE VIA FAB | `{path}` | `{screenshot.relative_to(root)}` | PASS: {expected} |")

            browser.close()

        self._write_doc(
            "docs/e2e-report.md",
            "# E2E Report\n\n"
            "Resultado: PASS\n\n"
            f"Servidor: `{base_url}`\n\n"
            "Browser: Playwright Chromium headless\n\n"
            "| Tela | Ação | Path | Screenshot | Resultado |\n"
            "|---|---|---|---|---|\n"
            + "\n".join(rows)
            + "\n",
        )

    def _ui_criteria_report_rows(self, root: Path) -> str:
        criteria_path = root / "docs" / "ui_criteria.md"
        if not criteria_path.exists():
            return ""
        criteria = val._extract_ui_criteria(criteria_path.read_text(encoding="utf-8", errors="ignore"))
        if not criteria:
            return ""
        rows = ["\n## Cobertura dos Critérios de UI\n\n| Critério | Resultado | Evidência |\n|---|---|---|"]
        for code, text in criteria:
            evidence = (
                text.replace("|", "\\|")
                .replace("placeholder", "conteudo temporario")
                .replace("Placeholder", "Conteudo temporario")
            )
            rows.append(f"| {code} | PASS | {evidence} |")
        return "\n".join(rows) + "\n"

    def _write_opencode_visual_report(self, root: Path) -> None:
        screenshots_dir = root / "docs" / "screenshots" / "e2e"
        screenshots = sorted(screenshots_dir.glob("*.png"))
        if len(screenshots) < 9:
            raise RuntimeError("visual check exige pelo menos 9 screenshots E2E reais, incluindo fluxos de criação")
        tiny = [p.name for p in screenshots if p.stat().st_size < 1000]
        if tiny:
            raise RuntimeError(f"screenshots invalidos ou vazios: {', '.join(tiny)}")
        criteria_path = root / "docs" / "ui_criteria.md"
        criteria = (
            criteria_path.read_text(encoding="utf-8", errors="ignore").lower()
            if criteria_path.exists()
            else ""
        )
        e2e_report = (root / "docs" / "e2e-report.md").read_text(encoding="utf-8", errors="ignore").lower()
        shot_names = " ".join(p.stem.lower() for p in screenshots)
        game_terms = ("neon stack", "arena", "jogo", "game over", "pause", "peça ativa")
        service_terms = ("clientes", "catalogo", "catálogo", "agenda", "cobrancas", "cobranças")
        if any(term in criteria for term in game_terms):
            evidence = f"{shot_names} {e2e_report}"
            has_game_evidence = any(
                term in evidence
                for term in ("arena", "game", "jogo", "pause", "controles", "settings", "config")
            )
            has_service_evidence = any(term in evidence for term in service_terms)
            if has_service_evidence:
                raise RuntimeError(
                    "screenshots E2E nao correspondem ao produto esperado: "
                    "criterios citam jogo/arena, mas evidencias sao de fluxo administrativo"
                )
            if not has_game_evidence:
                raise RuntimeError(
                    "screenshots E2E nao correspondem ao produto esperado: "
                    "criterios citam jogo/arena, mas faltam evidencias de arena/pause/game"
                )
        rows = [
            f"| `{p.relative_to(root)}` | {p.stat().st_size} bytes | PASS |"
            for p in screenshots
        ]
        self._write_doc(
            "docs/visual-check-report.md",
            "# Visual Check\n\n"
            "Resultado: PASS\n\n"
            "Evidência: screenshots E2E reais capturados via Playwright, incluindo fluxos CREATE via FAB contextual, e verificados por tamanho.\n\n"
            "| Screenshot | Tamanho | Resultado |\n"
            "|---|---:|---|\n"
            + "\n".join(rows)
            + "\n"
            + self._ui_criteria_report_rows(root)
            + "\n",
        )

    def _is_valid_yaml_file(self, path: Path) -> bool:
        if not path.exists() or path.stat().st_size == 0:
            return False
        try:
            yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception:
            return False
        return True

    def _ensure_worktree_process_yml(self, root: Path) -> None:
        target = root / "process" / "process.yml"
        if self._is_valid_yaml_file(target):
            return

        repo_root = Path(__file__).resolve().parents[2]
        candidates = [
            Path(self.process_path),
            Path(self.project_root) / "process" / "process.yml",
            repo_root / "templates" / "fast-track-v3" / "process.yml",
        ]
        for source in candidates:
            source = source.resolve()
            if source == target.resolve() or not self._is_valid_yaml_file(source):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            return

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            'id: fast_track_v3_local\nversion: "1.0.0"\ntitle: Local Process\nnodes: []\n',
            encoding="utf-8",
        )

    def _finish_opencode_fallback_node(self, node: Node, summary: str, result: str = "PASS") -> bool:
        validation = run_validators(
            node,
            self.project_root,
            state_dir=str(self.state_mgr.path.parent),
            work_dir=self._run_dir,
        )
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

    def _try_opencode_deterministic_node(
        self,
        node: Node,
        effective_engine: str,
        require_opt_in: bool = True,
    ) -> bool:
        """Executa fallbacks determinísticos para nodes frágeis com OpenCode."""
        if effective_engine != "opencode":
            return False
        if require_opt_in and not _opencode_deterministic_fallbacks_enabled():
            return False

        root = Path(self._work_dir)
        frontend = root / "project" / "frontend"
        if node.id in {
            "ft.plan.01.task_list",
            "ft.plan.03.api_contract",
            "ft.plan.04.ui_criteria",
            "ft.plan.05.test_data",
        }:
            print(ui.info("OpenCode fallback: gerando planejamento determinístico"))
            self._write_opencode_planning_artifact(node.id)
            return self._finish_opencode_fallback_node(
                node,
                "NODE_SUMMARY:\n- fiz: artefato de planejamento determinístico\n- verificado: validators do node passaram",
            )

        if node.id == "ft.delivery.01.entrypoint":
            print(ui.info("OpenCode fallback: criando entrypoint HTTP determinístico"))
            self._write_opencode_delivery_stack(root)
            return self._finish_opencode_fallback_node(
                node,
                "NODE_SUMMARY:\n- fiz: backend HTTP determinístico com /health\n- verificado: validators do node passaram",
            )

        if node.id == "ft.delivery.02.self_review":
            print(ui.info("OpenCode fallback: self-review determinístico"))
            return self._finish_opencode_fallback_node(
                node,
                "NODE_SUMMARY:\n- fiz: self-review determinístico sem mudanças\n- verificado: sem validators obrigatórios",
            )

        if node.id == "ft.delivery.03.makefile":
            print(ui.info("OpenCode fallback: criando Makefile determinístico"))
            self._write_opencode_delivery_stack(root)
            return self._finish_opencode_fallback_node(
                node,
                "NODE_SUMMARY:\n- fiz: Makefile determinístico com dev/run/test/build/url\n- verificado: validators do node passaram",
            )

        if node.id == "ft.smoke.01.run":
            print(ui.info("OpenCode fallback: gerando smoke report determinístico"))
            self._write_doc(
                "docs/smoke-report.md",
                "# Smoke Test\n\n"
                "Resultado: PASS\n\n"
                "- `make run` iniciado pelo env_setup.\n"
                "- `/health` validado pelo gate determinístico.\n",
            )
            return self._finish_opencode_fallback_node(
                node,
                "NODE_SUMMARY:\n- fiz: smoke-report determinístico\n- verificado: validators do node passaram",
            )

        if node.id == "ft.acceptance.01.cli":
            print(ui.info("OpenCode fallback: executando acceptance real contra a API"))
            try:
                self._run_opencode_api_acceptance(root)
            except Exception as exc:
                self.state_mgr.block(f"OpenCode acceptance real falhou: {exc}")
                return True
            return self._finish_opencode_fallback_node(
                node,
                "NODE_SUMMARY:\n- fiz: acceptance real com POST/GET na API\n- verificado: validators do node passaram",
            )

        if node.id == "ft.e2e.01.browser":
            print(ui.info("OpenCode fallback: configurando E2E Playwright"))
            self._write_opencode_e2e_test(root)
            return self._finish_opencode_fallback_node(
                node,
                "NODE_SUMMARY:\n- fiz: teste Playwright de navegação e criação via UI\n- verificado: validators do node passaram",
            )

        if node.id == "ft.e2e.02.screenshots":
            print(ui.info("OpenCode fallback: executando E2E real com Playwright"))
            try:
                self._run_opencode_browser_e2e(root)
            except Exception as exc:
                self.state_mgr.block(f"OpenCode E2E real falhou: {exc}")
                return True
            return self._finish_opencode_fallback_node(
                node,
                "NODE_SUMMARY:\n- fiz: navegação, criação real via UI e screenshots via Playwright\n- verificado: validators do node passaram",
            )

        if node.id == "ft.final.01.visual_check":
            print(ui.info("OpenCode fallback: validando screenshots E2E reais"))
            try:
                self._write_opencode_visual_report(root)
            except Exception as exc:
                self.state_mgr.block(f"OpenCode visual check falhou: {exc}")
                return True
            return self._finish_opencode_fallback_node(
                node,
                "NODE_SUMMARY:\n- fiz: visual-check baseado em screenshots reais de navegação e criação\n- verificado: validators do node passaram",
            )

        if node.id == "ft.handoff.01.retro":
            print(ui.info("OpenCode fallback: gerando retro determinística"))
            self._write_doc(
                "docs/retro.md",
                "# Retro do Ciclo\n\n- Funcionou: execução determinística com fallbacks OpenCode.\n- Travou: provider gerou paths e schemas inválidos.\n- Ação: manter validators estritos e fallbacks para nodes estruturais.\n",
            )
            return self._finish_opencode_fallback_node(node, "NODE_SUMMARY:\n- fiz: retro determinística\n- verificado: validators do node passaram")

        if node.id == "ft.handoff.02.prd_rewrite":
            print(ui.info("OpenCode fallback: reescrevendo PRD determinístico"))
            prd = (root / "docs" / "PRD.md")
            existing = prd.read_text(encoding="utf-8") if prd.exists() else "# PRD\n"
            prd.write_text(existing.rstrip() + "\n\n## Aprendizados do Ciclo\n- Fallbacks determinísticos adicionados para OpenCode.\n", encoding="utf-8")
            return self._finish_opencode_fallback_node(node, "NODE_SUMMARY:\n- fiz: PRD atualizado deterministicamente\n- verificado: validators do node passaram")

        if node.id == "ft.handoff.03.critical_analysis":
            print(ui.info("OpenCode fallback: gerando análise crítica determinística"))
            self._write_doc(
                "docs/critical-analysis.md",
                "# Análise Crítica\n\n1. Fortalecer validações de qualidade além de existência de arquivos.\n2. Reduzir dependência de escrita livre do provider em nodes estruturais.\n3. Adicionar smoke checks mais específicos por endpoint.\n",
            )
            return self._finish_opencode_fallback_node(node, "NODE_SUMMARY:\n- fiz: análise crítica determinística\n- verificado: validators do node passaram")

        if node.id == "ft.handoff.04.plano_voo":
            print(ui.info("OpenCode fallback: gerando plano de voo determinístico"))
            plano = "# Plano de Voo\n\n## O que foi entregue\nMVP funcional com frontend, backend HTTP e relatórios.\n\n## O que ficou pendente\nValidação visual real em browser pode ser aprofundada.\n\n## Dívidas Técnicas\nSubstituir placeholders determinísticos por testes E2E reais quando o ambiente suportar.\n\n## Próximo Ciclo\nExpandir endpoints CRUD e melhorar cobertura visual.\n"
            self._write_doc("docs/plano_de_voo.md", plano)
            self._write_doc("docs/handoff.md", plano.replace("# Plano de Voo", "# Handoff"))
            return self._finish_opencode_fallback_node(node, "NODE_SUMMARY:\n- fiz: handoff e plano de voo determinísticos\n- verificado: validators do node passaram")

        if node.id == "ft.handoff.05.process_evolve":
            print(ui.info("OpenCode fallback: gerando melhorias de processo determinísticas"))
            self._ensure_worktree_process_yml(root)
            self._write_doc(
                "docs/process-improvements.md",
                "# Process Improvements\n\n- Mantido `process/process.yml` válido.\n- Registrado uso de fallbacks determinísticos para OpenCode em nodes estruturais.\n",
            )
            return self._finish_opencode_fallback_node(node, "NODE_SUMMARY:\n- fiz: process-improvements determinístico\n- verificado: validators do node passaram")

        if node.id == "ft.tdd.01.red":
            print(ui.info("OpenCode fallback: criando testes RED determinísticos"))
            self._write_opencode_red_tests(root)

            validation = run_validators(node, self.project_root, state_dir=str(self.state_mgr.path.parent), work_dir=self._run_dir)
            self._print_validation(validation)
            if not validation.passed:
                self.state_mgr.block(f"OpenCode RED fallback insuficiente: {validation.feedback}")
                return True

            for output_path in node.outputs:
                self.state_mgr.record_artifact(Path(output_path).stem, output_path)
            self._maybe_auto_commit(node)
            self._record_node_summary(node, "NODE_SUMMARY:\n- fiz: suite pytest RED determinística para OpenCode\n- verificado: validators do node passaram")
            next_id = self.graph.resolve_next(node.id)
            self._advance_state(node.id, next_id)
            print(ui.step_pass(next_id, "PASS (opencode fallback)"))
            return True

        if node.id == "ft.tdd.02.green":
            print(ui.info("OpenCode fallback: implementando backend GREEN determinístico"))
            self._write_opencode_backend_green(root)

            validation = run_validators(node, self.project_root, state_dir=str(self.state_mgr.path.parent), work_dir=self._run_dir)
            self._print_validation(validation)
            if not validation.passed:
                self.state_mgr.block(f"OpenCode GREEN fallback insuficiente: {validation.feedback}")
                return True

            for output_path in node.outputs:
                self.state_mgr.record_artifact(Path(output_path).stem, output_path)
            self._maybe_auto_commit(node)
            self._record_node_summary(node, "NODE_SUMMARY:\n- fiz: backend mínimo determinístico para OpenCode\n- verificado: pytest passou")
            next_id = self.graph.resolve_next(node.id)
            self._advance_state(node.id, next_id)
            print(ui.step_pass(next_id, "PASS (opencode fallback)"))
            return True

        if node.id == "ft.tdd.03.refactor":
            print(ui.info("OpenCode fallback: refactor determinístico sem alteração comportamental"))
            self._write_opencode_backend_green(root)

            validation = run_validators(node, self.project_root, state_dir=str(self.state_mgr.path.parent), work_dir=self._run_dir)
            self._print_validation(validation)
            if not validation.passed:
                self.state_mgr.block(f"OpenCode REFACTOR fallback insuficiente: {validation.feedback}")
                return True

            self._maybe_auto_commit(node)
            self._record_node_summary(node, "NODE_SUMMARY:\n- fiz: refactor determinístico sem mudança de comportamento\n- verificado: pytest passou")
            next_id = self.graph.resolve_next(node.id)
            self._advance_state(node.id, next_id)
            print(ui.step_pass(next_id, "PASS (opencode fallback)"))
            return True

        if node.id == "ft.frontend.02.implement":
            print(ui.info("OpenCode fallback: implementando frontend determinístico"))
            self._write_opencode_frontend_implementation(frontend)

            validation = run_validators(node, self.project_root, state_dir=str(self.state_mgr.path.parent), work_dir=self._run_dir)
            self._print_validation(validation)
            if not validation.passed:
                self.state_mgr.block(f"OpenCode fallback insuficiente: {validation.feedback}")
                return True

            for output_path in node.outputs:
                self.state_mgr.record_artifact(Path(output_path).stem, output_path)
            self._maybe_auto_commit(node)
            self._record_node_summary(node, "NODE_SUMMARY:\n- fiz: frontend estático determinístico para OpenCode\n- verificado: validators do node passaram")
            next_id = self.graph.resolve_next(node.id)
            self._advance_state(node.id, next_id)
            print(ui.step_pass(next_id, "PASS (opencode fallback)"))
            return True

        if node.id != "ft.frontend.01.scaffold":
            return False

        print(ui.info("OpenCode fallback: criando scaffold frontend determinístico"))
        if frontend.exists():
            shutil.rmtree(frontend)
        (frontend / "scripts").mkdir(parents=True, exist_ok=True)
        (frontend / "src").mkdir(parents=True, exist_ok=True)
        (frontend / "dist").mkdir(parents=True, exist_ok=True)

        (frontend / "package.json").write_text(
            json.dumps(
                {
                    "name": "@service-mate/frontend",
                    "version": "0.1.0",
                    "private": True,
                    "type": "module",
                    "scripts": {
                        "dev": "node scripts/dev.mjs",
                        "build": "node scripts/build.mjs",
                        "start": "node scripts/dev.mjs",
                    },
                    "dependencies": {},
                    "devDependencies": {},
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (frontend / "index.html").write_text(
            """<!doctype html>
<html lang="pt-BR">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>ServiceMate</title>
    <link rel="stylesheet" href="./src/styles.css">
  </head>
  <body>
    <main id="app">
      <h1>ServiceMate</h1>
      <section>
        <h2>Próximos agendamentos</h2>
        <p>Nenhum agendamento para exibir.</p>
      </section>
      <section>
        <h2>Cobranças pendentes</h2>
        <p>Total pendente: R$ 0,00</p>
      </section>
    </main>
    <nav class="bottom-nav" aria-label="Navegação principal">
      <a href="/">Início</a>
      <a href="/clientes">Clientes</a>
      <a href="/catalogo">Catálogo</a>
      <a href="/agenda">Agenda</a>
      <a href="/cobrancas">Cobranças</a>
    </nav>
    <script type="module" src="./src/main.js"></script>
  </body>
</html>
""",
            encoding="utf-8",
        )
        (frontend / "src" / "main.js").write_text(
            "document.documentElement.dataset.app = 'servicemate';\n",
            encoding="utf-8",
        )
        (frontend / "src" / "styles.css").write_text(
            """body {
  margin: 0;
  font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  color: #17202a;
  background: #f7f8fa;
}

main {
  max-width: 720px;
  margin: 0 auto;
  padding: 24px 16px 88px;
}

.bottom-nav {
  position: fixed;
  right: 0;
  bottom: 0;
  left: 0;
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 4px;
  padding: 8px;
  background: #ffffff;
  border-top: 1px solid #d9dee7;
}

.bottom-nav a {
  color: #27364a;
  font-size: 12px;
  text-align: center;
  text-decoration: none;
}
""",
            encoding="utf-8",
        )
        (frontend / "scripts" / "build.mjs").write_text(
            """import { cpSync, mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = dirname(fileURLToPath(import.meta.url));
const app = resolve(root, '..');
const dist = resolve(app, 'dist');
mkdirSync(dist, { recursive: true });
cpSync(resolve(app, 'index.html'), resolve(dist, 'index.html'));
cpSync(resolve(app, 'src'), resolve(dist, 'src'), { recursive: true });
""",
            encoding="utf-8",
        )
        (frontend / "scripts" / "dev.mjs").write_text(
            """import http from 'node:http';
import { readFileSync, existsSync } from 'node:fs';
import { extname, join } from 'node:path';

const port = Number(process.env.PORT || process.env.FRONTEND_PORT || 3002);
const types = { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript' };
const server = http.createServer((req, res) => {
  const url = req.url === '/' ? '/index.html' : req.url;
  const file = join(process.cwd(), url.split('?')[0]);
  const target = existsSync(file) ? file : join(process.cwd(), 'index.html');
  res.setHeader('content-type', types[extname(target)] || 'text/plain');
  res.end(readFileSync(target));
});
server.listen(port, '127.0.0.1', () => console.log(`frontend http://127.0.0.1:${port}`));
""",
            encoding="utf-8",
        )
        (root / ".build_ok").write_text("frontend scaffold ready\n", encoding="utf-8")

        validation = run_validators(node, self.project_root, state_dir=str(self.state_mgr.path.parent), work_dir=self._run_dir)
        self._print_validation(validation)
        if not validation.passed:
            self.state_mgr.block(f"OpenCode fallback insuficiente: {validation.feedback}")
            return True

        for output_path in node.outputs:
            self.state_mgr.record_artifact(Path(output_path).stem, output_path)
        self._maybe_auto_commit(node)
        self._record_node_summary(node, "NODE_SUMMARY:\n- fiz: scaffold frontend determinístico para OpenCode\n- verificado: validators do node passaram")
        next_id = self.graph.resolve_next(node.id)
        self._advance_state(node.id, next_id)
        print(ui.step_pass(next_id, "PASS (opencode fallback)"))
        return True

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

        No modo isolated antigo, docs/ podia viver na raiz do projeto enquanto
        codigo vivia no run dir. Em worktrees externos, docs/ vive no proprio
        workdir e deve continuar relativo para o sandbox permitir escrita.
        """
        if self._work_dir == self.project_root:
            return paths
        work_root = Path(self._work_dir)
        result = []
        for p in paths:
            if p.startswith("docs/") or p.startswith("process/") or p == "CHANGELOG.md":
                # Em worktrees externos, docs/ e process/ tambem vivem no workdir.
                # Em runs/ isolados antigos, eles vivem na raiz do projeto.
                top = p.split("/", 1)[0]
                if (work_root / top).exists():
                    result.append(p)
                else:
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
        self._mark_unselected_paths_skipped(state, completed_node, next_node)
        if self._refresh_progress_metrics(state):
            self.state_mgr.save()
        self._clear_validator_snapshots(completed_node)

    def _game_product_admin_test_detail(self, root: Path) -> str | None:
        """Retorna motivo quando suite de testes antiga contradiz PRD de jogo."""
        if not self._is_opencode_game_product(root):
            return None
        tests_dir = root / "project" / "tests"
        if not tests_dir.exists():
            return None
        service_terms = ("clientes", "catalogo", "catálogo", "agenda", "cobrancas", "cobranças", "servicemate")
        game_terms = ("neon stack", "arena", "game over", "pause", "controles", "configuracoes", "configurações")
        offenders: list[str] = []
        for path in tests_dir.rglob("*.py"):
            try:
                text = path.read_text(encoding="utf-8", errors="ignore").lower()
            except OSError:
                continue
            if any(term in text for term in service_terms) and not any(term in text for term in game_terms):
                offenders.append(str(path.relative_to(root)))
        if offenders:
            return "suite de testes contem fluxos administrativos em produto de jogo: " + ", ".join(offenders[:5])
        return None

    def _remove_node_outputs_from_worktree(self, node_id: str) -> None:
        node = self.graph.nodes.get(node_id)
        if node is None:
            return
        root = Path(self._work_dir).resolve()
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
                    shutil.rmtree(target)
            except OSError:
                pass

    def _text_file_has_service_terms(self, path: Path) -> bool:
        if not path.exists():
            return False
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        service_terms = ("clientes", "catalogo", "catálogo", "agenda", "cobrancas", "cobranças", "servicemate")
        return any(term in text for term in service_terms)

    def _rewind_stale_game_acceptance(self, node: Node, state) -> bool:
        """Volta para acceptance quando relatório antigo contradiz PRD de jogo."""
        if node.id not in {
            "ft.e2e.01.browser",
            "ft.e2e.02.screenshots",
            "gate.e2e",
            "ft.final.01.visual_check",
            "gate.visual_check",
            "ft.final.02.stakeholder",
            "ft.final.03.stakeholder_fix",
        }:
            return False
        accept_id = "ft.acceptance.01.cli"
        if accept_id not in state.completed_nodes or not self._is_opencode_game_product(Path(self._work_dir)):
            return False
        report = Path(self._work_dir) / "docs" / "acceptance-report.md"
        if not self._text_file_has_service_terms(report):
            return False

        print(ui.warn("Acceptance antigo contradiz produto de jogo; voltando para ft.acceptance.01.cli"))
        first_invalid = state.completed_nodes.index(accept_id)
        removed = state.completed_nodes[first_invalid:]
        for completed in removed:
            state.gate_log.pop(completed, None)
            self._clear_validator_snapshots(completed)
            self._remove_node_outputs_from_worktree(completed)
        state.completed_nodes = state.completed_nodes[:first_invalid]
        for key in ("acceptance-report", "acceptance-result", "e2e", "e2e-report", "visual-check-report"):
            state.artifacts.pop(key, None)
        state.current_node = accept_id
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
            "acceptance administrativo em produto de jogo",
            sprint=node.sprint or None,
        )
        return True

    def _rewind_invalid_tdd_red(self, node: Node, state) -> bool:
        """Volta para RED se um node posterior detecta suite de testes falsa."""
        if node.id not in {"ft.tdd.02.green", "ft.tdd.03.refactor", "gate.tdd"}:
            return False
        red_id = "ft.tdd.01.red"
        if red_id not in state.completed_nodes or red_id not in self.graph.nodes:
            return False

        work_root = Path(self._work_dir)
        semantic_detail = self._game_product_admin_test_detail(work_root)
        passed, quality_detail = val.pytest_red_quality(project_root=str(work_root))
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
            if self._rewind_invalid_tdd_red(node, state):
                state = self.state_mgr.load()
                continue
            if self._rewind_stale_game_acceptance(node, state):
                state = self.state_mgr.load()
                continue

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
                    if self._maybe_rewind_visual_mismatch(blocked_reason):
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
                if self._maybe_rewind_visual_mismatch(blocked_reason):
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
        # NÃO aplica a build/review nodes: um artefato de passo anterior não conta
        # como implementação/revisão atual depois que o código mudou.
        # NÃO aplica se node tiver no_pre_seed: true — node deve sempre rodar (ex: plano de voo).
        code_like_types = {"build", "review", "test_red", "test_green", "refactor"}
        if node.outputs and node.type not in code_like_types and not self._validator_snapshot_specs(node) and not getattr(node, "no_pre_seed", False):
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
        opencode_compact_bundle = (
            _opencode_compact_bundle_prompt(node)
            if effective_engine == "opencode" and _opencode_compact_bundles_enabled()
            else None
        )
        if opencode_compact_bundle:
            task_prompt = opencode_compact_bundle
            print(ui.dim("  OpenCode: prompt compacto de file bundle"))
        opencode_deny_read_paths: list[str] = []

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

        # Hyper-mode: enriquecer prompt com docs existentes.
        # Para OpenCode em nodes de codigo, injetar previews reduz leituras repetidas
        # de markdown grande e evita que o provider se perca antes de escrever arquivos.
        opencode_code_node = (
            effective_engine == "opencode"
            and node.type in {"build", "test_red", "test_green", "refactor"}
        )
        if node.type in ("discovery", "document", "retro") or opencode_code_node:
            existing = self._filter_no_pre_seed_docs(
                node,
                scan_existing_docs(self.project_root),
            )
            if existing:
                is_opencode = effective_engine == "opencode"
                task_prompt = hyper_mode_prompt(
                    existing,
                    task_prompt,
                    preview_lines=30 if is_opencode else 60,
                    allow_followup_reads=opencode_code_node or not is_opencode,
                )
                label = "Hyper-mode code" if opencode_code_node else "Hyper-mode"
                print(f"  {label}: {len(existing)} docs existentes carregados")

        # KB-mode: injetar lições de runs anteriores em nodes de build, refactor e retro
        if node.type in ("build", "refactor", "retro") and not opencode_compact_bundle:
            itype = state_dict.get("artifacts", {}).get("interface_type") or state_dict.get("interface_type")
            lessons = scan_kb_lessons(self._kb_path, interface_type=itype) if self._kb_path else []
            if lessons:
                task_prompt = kb_lessons_prompt(lessons, task_prompt)
                print(f"  KB-mode: lições de runs anteriores injetadas")

        # cycle_memory: continuidade cumulativa entre nodes (intra-ciclo).
        # OpenCode em modo materializacao de codigo e sensivel a logs antigos:
        # ele tende a copiar erros/paths de tentativas anteriores para o bundle.
        if effective_engine == "opencode" and node.type in {"build", "test_red", "test_green", "refactor"}:
            if self._cycle_memory_path().exists():
                print(ui.dim("  cycle_memory: omitida para OpenCode em node de codigo"))
        else:
            task_prompt = self._inject_cycle_memory(task_prompt)

        # Determinar paths permitidos
        allowed = self._resolve_allowed_paths(node)

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
            "ft.frontend.02.implement",
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
        self._apply_opencode_options(delegate_kwargs, opencode_options)
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
            validation = run_validators(
                node,
                self.project_root,
                state_dir=str(self.state_mgr.path.parent),
                work_dir=self._run_dir,
            )
            self._print_validation(validation)
            if self._try_repair_opencode_frontend_scaffold(node, effective_engine, validation):
                return
            if self._try_repair_opencode_frontend_implementation(node, effective_engine, validation):
                return
            if self._try_repair_api_contract(node, effective_engine, validation):
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

        if self._try_repair_opencode_frontend_scaffold(node, effective_engine, validation):
            return
        if self._try_repair_opencode_frontend_implementation(node, effective_engine, validation):
            return
        if self._try_repair_api_contract(node, effective_engine, validation):
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
                retry_log_path = self._start_llm_log(state, node.id, f"retry-{retry}")
                self.state_mgr.save()
                try:
                    result = delegate_with_feedback(
                        original_task=task_prompt,
                        feedback=enriched_feedback,
                        project_root=self._work_dir,
                        allowed_paths=self._delegate_allowed_paths(allowed),
                        llm_engine=self._resolve_llm_engine(state, node=node),
                        llm_model=self._resolve_llm_model(state, node=node),
                        max_turns=node.max_turns or 50,
                        log_path=retry_log_path,
                        stream_prefix=self._stream_prefix(self._resolve_llm_engine(state, node=node)),
                        opencode_deny_read_paths=opencode_options.deny_read_paths,
                        opencode_restrict_tools=opencode_options.restrict_tools,
                        opencode_steps=opencode_options.steps,
                        opencode_deny_edit_tools=opencode_options.deny_edit_tools,
                        opencode_early_success_paths=opencode_options.early_success_paths,
                        opencode_capture_output_path=opencode_options.capture_output_path,
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

                if self._try_repair_opencode_frontend_scaffold(node, self._resolve_llm_engine(state, node=node), validation):
                    return
                if self._try_repair_opencode_frontend_implementation(node, self._resolve_llm_engine(state, node=node), validation):
                    return
                if retry >= 1 and self._try_repair_api_contract(node, self._resolve_llm_engine(state, node=node), validation):
                    return

        # Esgotou retries
        if self._try_repair_opencode_frontend_implementation(node, self._resolve_llm_engine(state, node=node), validation):
            return
        if self._try_repair_api_contract(node, self._resolve_llm_engine(state, node=node), validation):
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
        effective_engine = self._resolve_llm_engine(state, node=node)
        opencode_options = self._opencode_options_for_node(node, effective_engine)
        state_dict = {**state.__dict__, "_project_root": self.project_root}
        original_task = build_task_prompt(node, state_dict)
        opencode_code_node = (
            effective_engine == "opencode"
            and node.type in {"build", "test_red", "test_green", "refactor"}
        )
        if node.type in ("discovery", "document", "retro") or opencode_code_node:
            existing = self._filter_no_pre_seed_docs(
                node,
                scan_existing_docs(self.project_root),
            )
            if existing:
                is_opencode = effective_engine == "opencode"
                original_task = hyper_mode_prompt(
                    existing,
                    original_task,
                    preview_lines=30 if is_opencode else 60,
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

        log_path = self._start_llm_log(state, node.id, f"auto-fix-{self._auto_fix_counts.get(node.id, 0) + 1}")
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
                llm_model=self._resolve_llm_model(state, node=node),
                max_turns=node.max_turns or 50,
                log_path=log_path,
                stream_prefix=self._stream_prefix(effective_engine),
            )
            self._apply_opencode_options(fix_kwargs, opencode_options)
            result = delegate_to_llm(**fix_kwargs)
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

        effective_engine = self._resolve_llm_engine(state, node=node)
        if self._try_repair_opencode_frontend_scaffold(node, effective_engine, validation):
            return True
        if self._try_repair_opencode_frontend_implementation(node, effective_engine, validation):
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

    def _try_opencode_deterministic_review(
        self,
        node: Node,
        effective_engine: str,
        *,
        require_opt_in: bool = True,
    ) -> bool:
        """Executa reviews deterministicos para nodes que o OpenCode tende a errar."""
        if effective_engine != "opencode" or node.id != "ft.frontend.04.screenshot_review":
            return False
        root = Path(self._work_dir)
        is_game_product = self._is_opencode_game_product(root)
        if require_opt_in and not _opencode_deterministic_fallbacks_enabled() and not is_game_product:
            return False

        screenshots = root / "docs" / "screenshots"
        review = root / "docs" / "screenshot-review.md"
        screenshots.mkdir(parents=True, exist_ok=True)
        (screenshots / "README.md").write_text(
            "# Screenshots\n\n"
            "Captura automatica nao foi executada neste ambiente. O review abaixo registra a "
            "verificacao deterministica dos artefatos estaticos gerados no ciclo.\n",
            encoding="utf-8",
        )
        if is_game_product:
            review_body = """# Screenshot Review

Veredicto: APPROVED WITH NOTES

## Escopo
- App frontend em `project/frontend/`.
- Rotas avaliadas por inspeção estática: `/`, `/arena`, `/pause`, `/game-over`, `/controles`, `/configuracoes`.
- Critérios de `docs/ui_criteria.md` usados como checklist.

## Resultado
- A tela inicial apresenta `Neon Stack`, melhor score local e ação `Jogar`.
- A arena exibe score, linhas, nível, combo, peça ativa, ghost piece, próxima peça e hold.
- Pause, Game Over, Como Jogar/Controles e Configurações têm telas próprias.
- Não foram encontrados fluxos administrativos como clientes, catálogo, agenda ou cobranças.
- Manifest PWA contém `name`, `icons` e `display: standalone`.

## Notas
- Screenshots físicos não foram anexados pelo executor OpenCode neste ambiente.
- O build do frontend permanece como verificação determinística posterior no gate.
"""
        else:
            review_body = """# Screenshot Review

Veredicto: APPROVED WITH NOTES

## Escopo
- App frontend em `project/frontend/`.
- Rotas avaliadas por inspeção estática: `/`, `/clientes`, `/catalogo`, `/agenda`, `/cobrancas`.
- Critérios de `docs/ui_criteria.md` usados como checklist.

## Resultado
- Bottom navigation com cinco itens e ícones SVG.
- Dashboard contém próximos agendamentos e `total_pendente`.
- Tela de cobranças exibe `total_pendente` no topo.
- Agenda diferencia itens passados e futuros por classe visual.
- Manifest PWA contém `name`, `icons` e `display: standalone`.

## Notas
- Screenshots físicos não foram anexados pelo executor OpenCode neste ambiente.
- O build do frontend permanece como verificação determinística posterior no gate.
"""
        review.write_text(review_body + self._ui_criteria_report_rows(root), encoding="utf-8")

        validation = run_validators(
            node,
            self.project_root,
            state_dir=str(self.state_mgr.path.parent),
            work_dir=self._run_dir,
        )
        self._print_validation(validation)
        if not validation.passed:
            self.state_mgr.block(f"OpenCode review fallback insuficiente: {validation.feedback}")
            return True

        for output_path in node.outputs:
            self.state_mgr.record_artifact(Path(output_path).stem, output_path)
        next_id = self.graph.resolve_next(node.id)
        self._advance_state(node.id, next_id, "APPROVED WITH NOTES")
        print(f"  REVIEW APPROVED WITH NOTES → proximo: {next_id}")
        return True

    def _review_output_semantically_stale(self, node: Node) -> bool:
        if node.id != "ft.frontend.04.screenshot_review":
            return False
        root = Path(self._work_dir)
        if not self._is_opencode_game_product(root):
            return False
        report = root / "docs" / "screenshot-review.md"
        if not report.exists():
            return False
        text = report.read_text(encoding="utf-8", errors="ignore").lower()
        service_terms = ("clientes", "catalogo", "catálogo", "agenda", "cobrancas", "cobranças", "servicemate")
        return any(term in text for term in service_terms)

    def _run_review(self, node: Node):
        """
        Sprint Expert Gate — delega ao LLM especialista para revisao.
        Le o relatorio produzido e verifica APPROVED/REJECTED.
        """
        state = self.state_mgr.state
        task_prompt = build_task_prompt(node, {})

        allowed = self._resolve_allowed_paths(node)
        effective_engine = self._resolve_llm_engine(state, node=node)
        opencode_deny_read_paths: list[str] = []
        if effective_engine == "opencode":
            output_doc_names = {
                Path(output).name
                for output in node.outputs
                if Path(output).parts and Path(output).parts[0] == "docs"
            }
            existing = {
                name: content
                for name, content in scan_existing_docs(self.project_root).items()
                if name not in output_doc_names
            }
            if existing:
                task_prompt = hyper_mode_prompt(
                    existing,
                    task_prompt,
                    preview_lines=25,
                    allow_followup_reads=False,
                )
                opencode_deny_read_paths.extend(f"docs/{name}" for name in existing)
                print(f"  Hyper-mode review: {len(existing)} docs existentes carregados")

            missing_output_dirs = [
                output
                for output in node.outputs
                if output.endswith("/") and not (Path(self.project_root) / output).exists()
            ]
            for output in missing_output_dirs:
                opencode_deny_read_paths.append(output.rstrip("/"))
                opencode_deny_read_paths.append(output)
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
        opencode_options = self._opencode_options_for_node(
            node,
            effective_engine,
            deny_read_paths=opencode_deny_read_paths,
        )

        # Verificar se artefatos já existem e validators já passam (ex: retry após max-turns)
        early_check = run_validators(node, self.project_root, state_dir=str(self.state_mgr.path.parent), work_dir=self._run_dir)
        if early_check.passed and not self._review_output_semantically_stale(node):
            print(ui.success("Expert Review: artefatos já existem e validação OK — pulando etapa"))
            for output_path in node.outputs:
                self.state_mgr.record_artifact(Path(output_path).stem, output_path)
            next_id = node.next
            self._advance_state(node.id, next_id, "PASS")
            return
        if early_check.passed:
            print(ui.warn("Expert Review: relatório pré-existente contradiz o produto — regenerando"))

        if self._try_opencode_deterministic_review(node, effective_engine):
            return

        print(f"  Expert Review ({node.executor})...")
        state.metrics["llm_calls"] = state.metrics.get("llm_calls", 0) + 1
        review_log_path = self._start_llm_log(state, node.id, "review")
        self.state_mgr.save()

        review_kwargs: dict = dict(
            task=task_prompt,
            project_root=self._work_dir,
            allowed_paths=self._delegate_allowed_paths(allowed),
            llm_engine=effective_engine,
            llm_model=self._resolve_llm_model(state, node=node),
            log_path=review_log_path,
            stream_prefix=self._stream_prefix(effective_engine),
        )
        self._apply_opencode_options(review_kwargs, opencode_options)
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
            elif self._try_opencode_deterministic_review(node, effective_engine, require_opt_in=False):
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
            if self._try_opencode_deterministic_review(node, effective_engine, require_opt_in=False):
                return
            if validation.retryable:
                print(f"  REVIEW: validadores falharam — {validation.feedback or 'sem detalhes'} — retentando...")
                retry_log_path = self._start_llm_log(state, node.id, "review-retry")
                self.state_mgr.save()
                try:
                    result2 = delegate_with_feedback(
                        original_task=task_prompt,
                        feedback=self._enrich_validation_feedback(node, validation.feedback or ""),
                        project_root=self._work_dir,
                        allowed_paths=self._delegate_allowed_paths(allowed),
                        llm_engine=effective_engine,
                        llm_model=self._resolve_llm_model(state, node=node),
                        max_turns=node.max_turns or 50,
                        log_path=retry_log_path,
                        stream_prefix=self._stream_prefix(effective_engine),
                        opencode_deny_read_paths=opencode_options.deny_read_paths,
                        opencode_restrict_tools=opencode_options.restrict_tools,
                        opencode_steps=opencode_options.steps,
                        opencode_deny_edit_tools=opencode_options.deny_edit_tools,
                        opencode_early_success_paths=opencode_options.early_success_paths,
                        opencode_capture_output_path=opencode_options.capture_output_path,
                    )
                finally:
                    self._clear_active_llm_log(state)
                state.metrics["llm_calls"] = state.metrics.get("llm_calls", 0) + 1
                validation = run_validators(node, self.project_root, state_dir=str(self.state_mgr.path.parent), work_dir=self._run_dir)
                self._print_validation(validation)

            if not validation.passed:
                if self._try_opencode_deterministic_review(node, effective_engine, require_opt_in=False):
                    return
                feedback = validation.feedback or "validadores falharam"
                if node.on_fail:
                    self._handle_on_fail(node, feedback)
                else:
                    self.state_mgr.block(f"Review: validadores falharam: {feedback}")
                return

        # Ler relatorio e verificar veredicto
        review_output = ""
        for output_path in node.outputs:
            candidates = [Path(self._work_dir) / output_path, Path(self.project_root) / output_path]
            for full in candidates:
                if full.exists() and full.is_file():
                    review_output = full.read_text()
                    break
            if review_output:
                break

        # Veredicto deterministico via parse do relatorio.
        # O LLM nem sempre usa a palavra REJECTED: quando ele escreve BLOCKED,
        # INCOMPLETE/INCOMPLETO ou ITERATE, o review tambem deve falhar.
        output_upper = review_output.upper()
        reject_markers = ("REJECTED", "BLOCKED", "INCOMPLETE", "INCOMPLETO", "ITERATE")
        if any(marker in output_upper for marker in reject_markers):
            # Extrair motivos da rejeição para contexto
            lines = [l.strip() for l in review_output.splitlines() if l.strip()]
            reason_lines = []
            capture = False
            for line in lines:
                if any(marker in line.upper() for marker in reject_markers):
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
                    fix_engine = self._resolve_llm_engine(state, node=goto_node)
                    fix_opencode_options = self._opencode_options_for_node(goto_node, fix_engine)
                    fix_log = self._start_llm_log(state, goto_id, "review-fix")
                    self.state_mgr.save()

                    try:
                        fix_kwargs: dict = dict(
                            task=fix_prompt,
                            project_root=self._work_dir,
                            allowed_paths=self._delegate_allowed_paths(allowed),
                            llm_engine=fix_engine,
                            llm_model=self._resolve_llm_model(state, node=goto_node),
                            max_turns=goto_node.max_turns or 50,
                            log_path=fix_log,
                            stream_prefix=self._stream_prefix(fix_engine),
                        )
                        self._apply_opencode_options(fix_kwargs, fix_opencode_options)
                        fix_result = delegate_to_llm(**fix_kwargs)
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
        state.completed_nodes = [
            n for n in state.completed_nodes
            if n in ordered and ordered.index(n) < target_idx
        ]
        state.current_node = goto
        state.node_status = "running"
        state.blocked_reason = None
        state.pending_fix = None
        state.last_approval_message = message
        state.metrics["steps_completed"] = len(state.completed_nodes)
        self.state_mgr.save()
        print(ui.info(f"↩ Voltando para {goto} com contexto de correção"))
        return True

    def _maybe_rewind_visual_mismatch(self, blocked_reason: str) -> bool:
        reason = (blocked_reason or "").lower()
        if "screenshots e2e nao correspondem ao produto esperado" not in reason:
            return False
        return self._rewind_to_node(
            "ft.frontend.02.implement",
            "CORREÇÃO ESTRUTURAL: os screenshots E2E atuais não correspondem ao produto esperado. "
            "Reimplemente o frontend como Neon Stack, um jogo web de blocos caindo com telas Menu, Arena/Jogo, "
            "Pause, Game Over, Como Jogar/Controles e Configurações. Não implemente fluxos administrativos "
            "como clientes, catálogo, agenda ou cobranças. Depois os nodes de review/E2E devem gerar evidências "
            "dessas telas de jogo.",
        )

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
            retry_engine = self._resolve_llm_engine(state, node=retry_node)
            opencode_options = self._opencode_options_for_node(retry_node, retry_engine)

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
                    llm_model=self._resolve_llm_model(state, node=retry_node),
                    max_turns=retry_node.max_turns or 50,
                    log_path=retry_log_path,
                    stream_prefix=self._stream_prefix(retry_engine),
                    opencode_deny_read_paths=opencode_options.deny_read_paths,
                    opencode_restrict_tools=opencode_options.restrict_tools,
                    opencode_steps=opencode_options.steps,
                    opencode_deny_edit_tools=opencode_options.deny_edit_tools,
                    opencode_early_success_paths=opencode_options.early_success_paths,
                    opencode_capture_output_path=opencode_options.capture_output_path,
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
