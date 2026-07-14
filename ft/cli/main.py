"""
ft engine CLI — comandos do motor deterministico.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import sys

import yaml

from ft.engine import paths
from ft.engine.layout import (
    canonical_project_root,
    latest_cycle_artifact,
    manifest_llm_defaults,
    migrate_legacy_layout,
    process_digest,
    process_digest_matches,
    read_manifest,
    resolve_project_process,
    update_manifest_llm_defaults,
    validate_local_process_path,
)
from ft.engine.llm_capabilities import discover_llm_capabilities
from ft.engine.llm_usage import format_llm_usage_lines, summarize_llm_usage
from ft.engine.process_improvements import (
    ProcessImprovementError,
    load_process_improvement_review,
    process_improvement_close_readiness,
    resolve_global_process_candidate,
)
from ft.engine.runner import StepRunner
from ft.engine.validators.artifacts import (
    backlog_pending_decisions,
    backlog_referenced_decisions,
    features_catalog_valid,
    features_summary,
    implemented_backlog_covered_by_features,
    project_backlog_summary,
)


# Sequências ANSI (para higienizar texto do estado antes de exibir).
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _oneline(s: str | None, limit: int = 100) -> str:
    """Colapsa para UMA linha (sem \\n) e sem ANSI, truncado. Necessário para
    texto livre do estado (ex. blocked_reason, um dump de review multi-linha)
    que, cru, quebraria o heartbeat sobrescrito com \\r e vazaria a cor."""
    if not s:
        return ""
    s = " ".join(_ANSI_RE.sub("", str(s)).split())
    return s[:limit] + ("…" if len(s) > limit else "")


def add_llm_engine_flags(parser):
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--claude", nargs="?", const=True, metavar="MODEL",
                       help="Usar Claude CLI (opcional: modelo, ex: --claude opus)")
    group.add_argument("--codex", nargs="?", const=True, metavar="MODEL",
                       help="Usar Codex CLI (opcional: modelo, ex: --codex gpt-5.3)")
    group.add_argument("--gemini", nargs="?", const=True, metavar="MODEL",
                       help="Usar Gemini CLI (opcional: modelo, ex: --gemini gemini-2.5-pro)")
    group.add_argument("--opencode", nargs="?", const=True, metavar="MODEL",
                       help="Usar OpenCode CLI (default: pgx/zai-org_glm-4.7-flash)")
    parser.add_argument(
        "--effort",
        metavar="LEVEL",
        help="Effort de raciocínio do modelo (provider-specific; default omite override)",
    )


def resolve_bypass_human_gates(args) -> bool:
    """Human gates so sao pulados com o flag EXPLICITO --bypass-human-gates.

    --auto NAO implica bypass (PV-9 vibeos, 2026-07-06): modo autonomo avanca
    sozinho entre nodes LLM/validators, mas PARA em human_gate aguardando
    ft approve / ft reject.
    """
    return bool(getattr(args, "bypass_human_gates", False))


def apply_parallel_flags(runner, args) -> None:
    """Persiste no estado do run a escolha de paralelismo intra-processo.

    --parallel habilita o fan-out de nodes com parallel_group no YAML;
    --no-parallel desabilita num run já iniciado; --max-parallel ajusta os
    worktrees simultâneos. Persistido em ft_state.yml, então ft continue,
    ft approve --auto e ft retry honram a escolha sem re-passar flags.
    """
    parallel = bool(getattr(args, "parallel", False))
    no_parallel = bool(getattr(args, "no_parallel", False))
    max_parallel = getattr(args, "max_parallel", None)
    if not parallel and not no_parallel and max_parallel is None:
        return
    state = runner.state_mgr.load()
    if parallel:
        state.parallel_enabled = True
    if no_parallel:
        state.parallel_enabled = False
    if max_parallel is not None:
        state.parallel_max_slots = max(1, int(max_parallel))
    runner.state_mgr.save()


def resolve_run_mode(args) -> str:
    """Resolve o modo de execução a partir dos flags: --auto → 'mvp' (avança
    até o próximo human gate), --sprint → 'sprint' (até fim da sprint), senão
    'step' (um node). Compartilhado por `continue` e `approve`."""
    if getattr(args, "auto", False):
        return "mvp"
    if getattr(args, "sprint", False):
        return "sprint"
    return "step"


def _cycle_complete(state) -> bool:
    """True se o ciclo JÁ concluiu (node_status done, ou current_node None mas
    com nós já completos). Distingue de um estado NOVO (nunca rodou, sem nós
    completos) — evita que `continue` num ciclo pronto chame init_state e
    reinicie tudo do zero."""
    if getattr(state, "node_status", "") == "done":
        return True
    return state.current_node is None and bool(getattr(state, "completed_nodes", None))


def resolve_llm_engine(args) -> str | None:
    if getattr(args, "codex", None) is not None:
        return "codex"
    if getattr(args, "claude", None) is not None:
        return "claude"
    if getattr(args, "gemini", None) is not None:
        return "gemini"
    if getattr(args, "opencode", None) is not None:
        return "opencode"
    return None


def resolve_llm_model(args) -> str | None:
    """Extrai o modelo passado junto à flag de engine (ex: --codex gpt-5.3)."""
    for attr in ("claude", "codex", "gemini", "opencode"):
        val = getattr(args, attr, None)
        if val is not None and val is not True:
            return str(val)
    return None


def resolve_llm_effort(args) -> str | None:
    """Return an explicit effort, preserving ``default`` as an override.

    O runner distingue ausência do flag de ``--effort default``: o segundo
    limpa um effort herdado do ciclo sem inventar um catálogo global.
    """
    value = getattr(args, "effort", None)
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    return normalized


def engine_root() -> Path:
    """Raiz do repositório do engine (onde templates/ e kb/ vivem)."""
    return Path(__file__).resolve().parent.parent.parent


def _template_process_file(template_dir: Path) -> Path | None:
    """Resolve o YAML de processo sem confundi-lo com environment.yml."""
    canonical = template_dir / "process.yml"
    if canonical.is_file():
        return canonical
    legacy = sorted(
        path for path in template_dir.glob("*.yml")
        if path.name != "environment.yml"
    )
    return legacy[0] if legacy else None


def _template_entrypoint(template_dir: Path) -> str:
    """Retorna o comando dono do template; templates legados pertencem ao init."""
    process_file = _template_process_file(template_dir)
    if process_file is None:
        return "init"
    try:
        payload = yaml.safe_load(process_file.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return "init"
    if not isinstance(payload, dict):
        return "init"
    policy = payload.get("execution_policy") or {}
    if not isinstance(policy, dict):
        return "init"
    return str(policy.get("entrypoint") or "init")


def available_templates(entrypoint: str | None = "run") -> list[str]:
    """Discover templates for the universal run entrypoint or a tool workflow."""
    if entrypoint in {None, "run"}:
        from ft.templates import TemplateCatalog

        return list(TemplateCatalog(engine_root() / "templates").names())
    templates_root = engine_root() / "templates"
    if not templates_root.is_dir():
        return []
    return sorted(
        item.name
        for item in templates_root.iterdir()
        if item.is_dir()
        and _template_process_file(item) is not None
        and (entrypoint is None or _template_entrypoint(item) == entrypoint)
    )


def _print_template_options(entrypoint: str = "run") -> None:
    available = available_templates(entrypoint)
    if available:
        print(f"  Templates disponíveis: {', '.join(available)}")


def _guard_engine_repo(root: Path) -> None:
    """Impede usar o repositório do engine/template como projeto.

    Override para desenvolvimento do próprio engine: FT_ALLOW_ENGINE_REPO=1.
    """
    if os.environ.get("FT_ALLOW_ENGINE_REPO"):
        return
    if root.resolve() == engine_root().resolve():
        print("ERRO: este é o repositório do ft engine/template — não pode ser usado como projeto.")
        print("  Crie um projeto novo: ft init <nome>")
        _print_template_options()
        print("  Depois execute: ft run <path-do-projeto> --template <template>")
        print("  (override para desenvolvimento do engine: FT_ALLOW_ENGINE_REPO=1)")
        sys.exit(1)


def _run_environment_script(
    project_root: Path,
    template: str,
    script: str,
) -> bool:
    """Run an optional script adjacent to one explicit local template."""
    import subprocess

    project_root = project_root.resolve()
    process_path = resolve_project_process(project_root, template)
    if process_path is None:
        return False
    script_path = process_path.parent / "scripts" / script
    if not script_path.exists():
        return False

    result = subprocess.run(
        [str(script_path)],
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=300,
    )
    output = (result.stdout or result.stderr).strip()
    if output:
        print(output)
    if result.returncode != 0:
        print(
            f"  ERRO: {script_path.relative_to(project_root)} falhou "
            f"com exit code {result.returncode}"
        )
        sys.exit(result.returncode)
    return True


def find_project_root() -> Path:
    """Encontra a raiz do projeto subindo até o layout .ft versionado."""
    current = Path.cwd()
    for parent in [current, *current.parents]:
        if paths.project_manifest(parent).is_file():
            return parent
    return current


def _capability_agent(
    capabilities: dict[str, object],
    agent_id: str,
) -> dict[str, object] | None:
    agents = capabilities.get("agents", [])
    if not isinstance(agents, list):
        return None
    return next(
        (
            agent
            for agent in agents
            if isinstance(agent, dict) and agent.get("id") == agent_id
        ),
        None,
    )


def _capability_model(
    agent: dict[str, object],
    model_id: str,
) -> dict[str, object] | None:
    models = agent.get("models", [])
    if not isinstance(models, list):
        return None
    return next(
        (
            model
            for model in models
            if isinstance(model, dict) and model.get("id") == model_id
        ),
        None,
    )


def _overlay_project_llm_defaults(
    capabilities: dict[str, object],
    project_root: Path,
) -> dict[str, object]:
    """Add saved, provider-reported and effective defaults to a fresh probe."""

    existing_errors = capabilities.get("errors")
    if isinstance(existing_errors, list):
        capabilities["errors"] = [
            error
            for error in existing_errors
            if not isinstance(error, dict)
            or error.get("code") != "invalid_saved_default"
        ]

    saved_agent, saved_model, saved_effort = manifest_llm_defaults(project_root)
    raw_defaults = capabilities.get("defaults")
    cli_defaults = raw_defaults if isinstance(raw_defaults, dict) else {}
    reported = {
        "agent": cli_defaults.get("agent"),
        "models": cli_defaults.get("models", {}),
        "efforts": cli_defaults.get("efforts", {}),
        "source": "provider_cli",
    }
    saved = {
        "agent": saved_agent,
        "model": saved_model,
        "effort": saved_effort,
        "source": "project_manifest",
    }

    # Claude is FT's executor default when the project has no persisted agent.
    # A null model/effort intentionally means "let that provider choose".
    effective_agent = saved_agent or str(reported.get("agent") or "claude")
    effective_model = saved_model
    effective_effort = saved_effort
    if any(value is not None for value in (saved_agent, saved_model, saved_effort)):
        effective_source = "project_manifest"
    elif reported.get("agent"):
        effective_source = "provider_cli"
    else:
        effective_source = "ft_default"
    valid = True
    reason: str | None = None

    agent = _capability_agent(capabilities, effective_agent)
    if agent is None:
        valid = False
        reason = f"Agente salvo não foi anunciado pela descoberta: {effective_agent}"
    elif not agent.get("available"):
        valid = False
        reason = str(agent.get("reason") or f"Agente indisponível: {effective_agent}")
    else:
        if effective_model is None:
            reported_model = agent.get("default_model")
            effective_model = str(reported_model) if reported_model else None

        model = _capability_model(agent, effective_model) if effective_model else None
        if effective_model is not None and model is None:
            valid = False
            reason = (
                f"Modelo salvo não está disponível para {effective_agent}: "
                f"{effective_model}"
            )
        elif model is not None:
            advertised_efforts = model.get("efforts")
            if effective_effort is None:
                reported_effort = model.get("default_effort")
                effective_effort = str(reported_effort) if reported_effort else None
            elif not isinstance(advertised_efforts, list) or effective_effort not in advertised_efforts:
                valid = False
                reason = (
                    f"Effort salvo não é compatível com {effective_agent}/"
                    f"{effective_model}: {effective_effort}"
                )

    effective = {
        "agent": effective_agent,
        "model": effective_model,
        "effort": effective_effort,
        "valid": valid,
        "reason": reason,
        "source": effective_source,
    }
    capabilities["defaults"] = {
        **cli_defaults,
        "saved": saved,
        "reported": reported,
        "effective": effective,
    }
    if not valid:
        errors = capabilities.setdefault("errors", [])
        if isinstance(errors, list):
            errors.append(
                {
                    "code": "invalid_saved_default",
                    "message": reason or "Default LLM salvo é inválido",
                }
            )
    return capabilities


def _print_llm_json(payload: dict[str, object], compact: bool) -> None:
    print(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=None if compact else 2,
            separators=(",", ":") if compact else None,
        )
    )


def _fail_llm_command(
    capabilities: dict[str, object],
    *,
    code: str,
    message: str,
    compact: bool,
) -> None:
    capabilities["updated"] = False
    errors = capabilities.setdefault("errors", [])
    if isinstance(errors, list):
        errors.append({"code": code, "message": message})
    _print_llm_json(capabilities, compact)
    raise SystemExit(2)


def cmd_llm_capabilities(args) -> None:
    """Probe providers afresh and expose their project-default overlay."""

    root = canonical_project_root(find_project_root())
    capabilities = discover_llm_capabilities(cwd=root)
    _overlay_project_llm_defaults(capabilities, root)
    _print_llm_json(capabilities, bool(getattr(args, "json", False)))


def cmd_llm_defaults(args) -> None:
    """Validate and atomically persist one project LLM default selection."""

    root = canonical_project_root(find_project_root())
    compact = bool(getattr(args, "json", False))
    capabilities = discover_llm_capabilities(cwd=root)
    _overlay_project_llm_defaults(capabilities, root)

    manifest_path = paths.project_manifest(root)
    if not manifest_path.is_file():
        _fail_llm_command(
            capabilities,
            code="project_not_initialized",
            message="Projeto sem .ft/manifest.yml; execute ft init primeiro",
            compact=compact,
        )

    agent_id = str(args.agent).strip().lower()
    model_id = str(args.model).strip()
    requested_effort = getattr(args, "effort", None)
    effort = str(requested_effort).strip() if requested_effort is not None else None
    if not effort or effort.lower() == "default":
        effort = None

    agent = _capability_agent(capabilities, agent_id)
    if agent is None:
        _fail_llm_command(
            capabilities,
            code="agent_unknown",
            message=f"Agente não anunciado pela descoberta: {agent_id}",
            compact=compact,
        )
    if not agent.get("available"):
        _fail_llm_command(
            capabilities,
            code="agent_unavailable",
            message=str(agent.get("reason") or f"Agente indisponível: {agent_id}"),
            compact=compact,
        )

    model = _capability_model(agent, model_id)
    if model is None or not model.get("available", True):
        _fail_llm_command(
            capabilities,
            code="model_unavailable",
            message=f"Modelo não disponível para {agent_id}: {model_id}",
            compact=compact,
        )

    advertised_efforts = model.get("efforts")
    if effort is not None and (
        not isinstance(advertised_efforts, list) or effort not in advertised_efforts
    ):
        _fail_llm_command(
            capabilities,
            code="effort_unsupported",
            message=f"Effort não compatível com {agent_id}/{model_id}: {effort}",
            compact=compact,
        )

    try:
        update_manifest_llm_defaults(
            root,
            llm_engine=agent_id,
            llm_model=model_id,
            llm_effort=effort,
        )
    except (FileNotFoundError, OSError, ValueError) as exc:
        _fail_llm_command(
            capabilities,
            code="manifest_update_failed",
            message=str(exc),
            compact=compact,
        )

    _overlay_project_llm_defaults(capabilities, root)
    capabilities["updated"] = True
    capabilities["manifest"] = ".ft/manifest.yml"
    _print_llm_json(capabilities, compact)


def _resolve_pinned_process(root: Path, raw_path: str) -> Path:
    """Resolve a state-owned process path strictly inside the local catalog."""
    relative = Path(raw_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"process_path inválido no state: {raw_path}")
    try:
        return validate_local_process_path(root, relative, require_registered=True)
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"processo local fixado no ciclo não existe: {raw_path}"
        ) from exc
    except ValueError as exc:
        raise ValueError(f"process_path inválido no ciclo: {raw_path}: {exc}") from exc


def _is_cycle_dir(d: Path) -> bool:
    """Verifica se é um diretório de ciclo válido.

    Aceita qualquer diretório dentro de ~/.ft/worktrees/<project>/
    que contenha um state/ ou que siga o padrão legado 'NN' / 'cycle-NN[-...]'.
    """
    name = d.name
    if name.isdigit():
        return True
    if name.startswith("cycle-"):
        return True
    # Nomes livres (ex: cycle-03-claude, my-feature) — aceitar se tiver state/
    if (d / "state" / "engine_state.yml").exists():
        return True
    return False


def _cycle_num_strict(d: Path) -> int | None:
    """Número do ciclo de 'cycle-NN', 'cycle-NN-engine' ou 'NN'; None se não-numérico."""
    name = d.name
    try:
        if name.startswith("cycle-"):
            return int(name[6:].split("-")[0])
        return int(name)
    except ValueError:
        return None


def _cycle_num(d: Path) -> int:
    """Chave de ordenação de ciclos. Nomes sem número (ex.: worktree 'claude')
    ordenam pelo mtime — mais recente ganha, sem quebrar o sort."""
    n = _cycle_num_strict(d)
    if n is not None:
        return n
    try:
        return int(d.stat().st_mtime)
    except OSError:
        return 0


_TERMINAL_STATUSES = {"done", "completed", "failed", "aborted", "cancelled", "canceled"}
_RUNTIME_STATUSES = _TERMINAL_STATUSES | {
    "ready",
    "preparing",
    "running",
    "delegated",
    "validating",
    "blocked",
    "awaiting_approval",
    "pending_fix",
    "exploring",
}


def _is_active_state_data(data: dict) -> bool:
    """True se o state representa um ciclo ainda acionável pelo usuário."""
    if not isinstance(data, dict):
        return False
    node_status = data.get("node_status", "")
    current_node = data.get("current_node", "")
    if node_status in _TERMINAL_STATUSES or not current_node:
        return False
    if _is_pristine_state(data):
        return False
    return True


def _state_represents_runtime(data: dict) -> bool:
    """True quando há um ciclo real para consultar ou encerrar.

    Um state recém-inicializado com ``current_node`` ainda representa um ciclo,
    mesmo antes do primeiro node concluir. Já o arquivo continuous legado que
    contém apenas metadados/defaults (``current_node: null``, zero progresso)
    não pode ressuscitar um processo depois que a worktree foi fechada.
    """
    if not isinstance(data, dict) or not data:
        return False
    node_status = data.get("node_status")
    if not isinstance(node_status, str) or node_status not in _RUNTIME_STATUSES:
        return False
    if data.get("current_node"):
        return True
    if node_status in _TERMINAL_STATUSES:
        return True

    evidence_fields = (
        "completed_nodes",
        "gate_log",
        "artifacts",
        "pending_approval",
        "last_approval_message",
        "pending_fix",
        "exploration_log",
        "active_llm_log",
        "last_llm_log",
        "blocked_reason",
    )
    if any(data.get(key) for key in evidence_fields):
        return True

    metrics = data.get("metrics") or {}
    if isinstance(metrics, dict):
        return any(
            key != "steps_total" and value not in (0, 0.0, None, "", [], {})
            for key, value in metrics.items()
        )
    return False


def _state_data(path: Path) -> dict:
    import yaml as _yaml

    try:
        data = _yaml.safe_load(path.read_text()) or {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _latest_archived_cycle(root: Path) -> tuple[Path, dict] | None:
    """Retorna o ciclo fechado mais recente para contexto de leitura."""
    archive_home = paths.project_cycles_dir(root)
    if not archive_home.is_dir():
        return None
    candidates = [
        cycle
        for cycle in archive_home.iterdir()
        if cycle.is_dir()
        and _is_cycle_dir(cycle)
        and (cycle / "cycle.yml").is_file()
    ]
    if not candidates:
        return None
    latest = max(
        candidates,
        key=lambda cycle: (_cycle_num(cycle), cycle.stat().st_mtime),
    )
    return latest, _state_data(latest / "cycle.yml")


def _print_no_active_cycle(root: Path) -> None:
    """Saída neutra: não associa um processo default a um ciclo inexistente."""
    from ft.engine import ui as _ui

    print(_ui.header("Fast Track"))
    print(_ui.info("Status: nenhum ciclo ativo"))
    archived = _latest_archived_cycle(root)
    if archived:
        cycle, data = archived
        status = str(data.get("status") or "arquivado")
        if status in {"done", "completed"}:
            status = "concluído"
        print(_ui.info(f"Último ciclo: {cycle.name} ({status})"))
        print(_ui.dim(f"Histórico: .ft/cycles/{cycle.name}/"))
    print(_ui.dim("Use `ft runs` para consultar ciclos ativos e arquivados."))


def _ensure_runtime_selected(args, runner=None) -> bool:
    """Validate that a command selected one concrete, pinned cycle."""
    if runner is not None:
        state = runner.state_mgr.load()
        data = vars(state) if hasattr(state, "__dict__") else {}
        if _state_represents_runtime(data):
            return True
        runner_root = getattr(runner, "project_root", None)
        root = Path(runner_root) if runner_root else find_project_root()
        _print_no_active_cycle(root)
        return False
    _select_cycle_for_command(
        find_project_root(),
        getattr(args, "cycle", None),
    )
    return True


def _select_cycle_for_command(root: Path, requested: str | None = None):
    """Select exactly one open cycle and render domain errors without traceback.

    There is deliberately no "latest" fallback.  From the owning checkout one
    cycle can be inferred; two or more require ``--cycle``.  From inside a run
    worktree, that worktree is the implicit explicit selection.
    """
    from ft.runs import (
        AmbiguousCycleError,
        CycleNotFoundError,
        CycleNotReadyError,
        NoCycleError,
        select_cycle,
    )

    location = Path(root).resolve()
    owner = canonical_project_root(location)
    selected = requested
    if selected is None and paths.is_worktree_path(location):
        selected = location.name
    try:
        return select_cycle(owner, selected, include_terminal=True)
    except (
        AmbiguousCycleError,
        CycleNotFoundError,
        CycleNotReadyError,
        NoCycleError,
    ) as exc:
        from ft.engine import ui as _ui

        print(_ui.fail(str(exc)))
        print(_ui.info("Use `ft runs` para listar os ciclos disponíveis."))
        raise SystemExit(2) from exc


def _api_health_check(project_root: Path, llm_engine: str = "claude") -> None:
    """Testa conectividade com a API antes de iniciar a run.

    Faz POST mínimo ao endpoint de messages. Aceita 200/429/529
    (API funcionando). Aborta em 400/403/405 com mensagem clara.
    """
    import json
    import urllib.error
    import urllib.request
    from ft.engine import ui as _ui

    if llm_engine.lower().strip() != "claude":
        return

    if os.environ.get("FT_SKIP_HEALTH_CHECK"):
        return

    # Resolver base_url
    settings_file = project_root / ".claude" / "settings.local.json"
    base_url = None
    if settings_file.exists():
        try:
            data = json.loads(settings_file.read_text())
            base_url = data.get("env", {}).get("ANTHROPIC_BASE_URL")
        except (json.JSONDecodeError, KeyError):
            pass

    if not base_url:
        base_url = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    url = f"{base_url}/v1/messages"
    payload = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "ping"}],
    }).encode()

    headers = {
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    if api_key:
        headers["x-api-key"] = api_key

    try:
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            host = base_url.split("//")[-1].split("/")[0]
            print(_ui.info(f"API health check: {resp.status} OK ({host})"))
    except urllib.error.HTTPError as e:
        code = e.code
        body = e.read().decode(errors="ignore")[:200]
        if code in (429, 529) or (code == 404 and "model" in body):
            # Rate limit, overloaded ou modelo desconhecido = API respondeu e autenticou
            host = base_url.split("//")[-1].split("/")[0]
            print(_ui.info(f"API health check: {code} ({host}) — API acessível"))
        else:
            print(_ui.fail(f"API health check: {code} — {body}"))
            if code == 403:
                print("    → Acesso negado. Verifique credenciais ou rode: ft setup-env")
            elif code == 405:
                print("    → Rota inválida. Verifique ANTHROPIC_BASE_URL.")
            raise SystemExit(1)
    except Exception as e:
        from ft.engine import ui as _ui
        print(_ui.info(f"API health check: timeout/erro ({e}) — continuando"))


def _validate_cycle_name(name: str | None) -> str | None:
    """Valida nome explícito de ciclo informado pelo usuário."""
    if name is None:
        return None
    name = str(name).strip()
    if not name:
        raise ValueError("nome de ciclo vazio")
    if name in {".", ".."} or "/" in name or "\\" in name:
        raise ValueError("nome de ciclo deve ser relativo e não pode conter barras")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", name):
        raise ValueError("nome de ciclo deve usar apenas letras, números, '.', '_' ou '-'")
    return name


def _single_fix_target_path(instruction: str, root: Path) -> str | None:
    """Extrai um path relativo unico citado numa instrucao de `ft fix`.

    Usado para OpenCode operar em modo capture/file-content quando o fix mira
    um arquivo especifico, evitando chamadas nativas de Edit/Write instaveis.
    """
    candidates: list[str] = []
    pattern = r"(?<![A-Za-z0-9_.])((?:project|src|tests|docs|\.ft/process)/(?:[A-Za-z0-9_.@%+=-]+/)*[A-Za-z0-9_.@%+=-]+)"
    for match in re.finditer(pattern, instruction):
        rel = match.group(1).strip().strip("'\"`.,;:)")
        path = Path(rel)
        if path.is_absolute() or ".." in path.parts:
            continue
        target = (root / path).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError:
            continue
        if target.exists() and target.is_file():
            candidates.append(path.as_posix())
    unique = sorted(set(candidates))
    return unique[0] if len(unique) == 1 else None


def _postprocess_opencode_fix_capture(runner, capture_path: str | None) -> str | None:
    """Valida artefato capturado por OpenCode e aplica reparos determinísticos conhecidos."""
    if not capture_path:
        return None
    root = Path(getattr(runner, "_work_dir", runner.project_root))
    target = root / capture_path
    if not target.exists() or target.suffix != ".py":
        return None

    import py_compile

    try:
        py_compile.compile(str(target), doraise=True)
        if capture_path == "project/tests/e2e/test_navigation.py" and hasattr(runner, "_write_opencode_e2e_test"):
            text = target.read_text(encoding="utf-8", errors="ignore")
            if "outerHTML" in text and "arena-board" in text:
                runner._write_opencode_e2e_test(root)
                py_compile.compile(str(target), doraise=True)
                return "E2E determinístico regravado: canvas agora compara toDataURL(), não outerHTML"
        return None
    except py_compile.PyCompileError as exc:
        if capture_path == "project/tests/e2e/test_navigation.py" and hasattr(runner, "_write_opencode_e2e_test"):
            runner._write_opencode_e2e_test(root)
            py_compile.compile(str(target), doraise=True)
            return f"arquivo Python inválido gerado pelo OpenCode; E2E determinístico regravado ({exc.msg})"
        raise


def _try_apply_opencode_arena_board_fix(runner, instruction: str) -> str | None:
    """Repair estreito para o contrato E2E de arena canvas em produtos de jogo."""
    norm = instruction.lower()
    if "arena-board" not in norm or "canvas" not in norm:
        return None
    root = Path(getattr(runner, "_work_dir", runner.project_root))
    changed: list[str] = []
    for rel in ("project/frontend/src/main.js", "project/frontend/dist/src/main.js"):
        target = root / rel
        if not target.exists() or not target.is_file():
            continue
        text = target.read_text(encoding="utf-8", errors="ignore")
        if 'data-testid="arena-board"' in text:
            continue
        updated = text.replace(
            '<canvas id="arena-canvas" ',
            '<canvas id="arena-canvas" data-testid="arena-board" ',
            1,
        )
        if updated == text:
            updated = text.replace(
                '<canvas id="arena-canvas"',
                '<canvas id="arena-canvas" data-testid="arena-board"',
                1,
            )
        if updated != text:
            target.write_text(updated, encoding="utf-8")
            changed.append(rel)
    if not changed:
        return None
    return "arena canvas atualizado com data-testid=arena-board em " + ", ".join(changed)


def _worktree_root_from_state(state_path: Path) -> Path | None:
    """Se o state mora dentro de um worktree, retorna o root desse worktree."""
    # state_path é algo como ~/.ft/worktrees/<proj>/cycle-NN/state/engine_state.yml
    # O root do worktree é o parent de state/ → cycle-NN/
    candidate = state_path.parent.parent
    git_file = candidate / ".git"
    if git_file.exists() and git_file.is_file():
        # É um worktree (arquivo .git aponta para o repo original)
        return candidate if paths.project_manifest(candidate).is_file() else None
    # Pode ser diretório simples (sem git) dentro da raiz de worktrees
    if (
        paths.is_worktree_path(candidate)
        and (candidate / "state").is_dir()
        and (
            paths.project_manifest(candidate).is_file()
        )
    ):
        return candidate
    return None


def get_runner(
    llm_engine: str | None = None,
    llm_model: str | None = None,
    verbose: bool = False,
    cycle: str | None = None,
    llm_effort: str | None = None,
) -> StepRunner:
    location = find_project_root()
    root = canonical_project_root(location)
    record = _select_cycle_for_command(location, cycle)
    state_path = record.state_path
    effective_root = record.worktree

    pinned_path = None
    pinned_digest = None
    pinned_immutable = False
    if state_path and state_path.is_file():
        try:
            state_payload = yaml.safe_load(state_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise ValueError(f"state inválido em {state_path}: {exc}") from exc
        if isinstance(state_payload, dict):
            pinned_path = state_payload.get("process_path")
            pinned_digest = state_payload.get("process_digest")
            pinned_immutable = bool(state_payload.get("process_immutable", False))

    # Every run is immutable and resumes exclusively from its pinned template.
    if pinned_path:
        process_path = _resolve_pinned_process(effective_root, str(pinned_path))
        if pinned_digest:
            payload = yaml.safe_load(process_path.read_text(encoding="utf-8")) or {}
            execution = payload.get("execution_policy", {}) if isinstance(payload, dict) else {}
            if (
                (
                    pinned_immutable
                    or (
                        isinstance(execution, dict)
                        and execution.get("runtime_source") == "local_only"
                    )
                )
                and not process_digest_matches(process_path, pinned_digest)
            ):
                raise ValueError(
                    f"processo local do ciclo divergiu do digest fixado: {pinned_path}"
                )
    else:
        raise ValueError(
            f"ciclo {record.name} não fixa process_path; "
            "migre ou recrie o ciclo com `ft run --template <T>`"
        )

    return StepRunner(
        process_path=process_path,
        state_path=state_path,
        project_root=effective_root,
        llm_engine=llm_engine,
        llm_model=llm_model,
        llm_effort=llm_effort,
        llm_defaults_root=root,
        verbose=verbose,
    )


def cmd_init(args):
    """Initialize, diagnose, or repair the common project workspace."""
    from ft.engine import ui as _ui
    from ft.project import bootstrap_project, check_project, repair_project

    raw_target = Path(getattr(args, "name", None) or ".").expanduser()
    root = raw_target if raw_target.is_absolute() else Path.cwd() / raw_target
    root = root.resolve()
    _guard_engine_repo(root)

    def print_issues(issues) -> None:
        for issue in issues:
            marker = "AVISO" if issue.severity == "warning" else "ERRO"
            suffix = f" ({issue.path})" if issue.path else ""
            print(f"  {marker} [{issue.code}] {issue.message}{suffix}")

    if getattr(args, "check", False):
        result = check_project(root)
        print(f"  Projeto: {result.root}")
        print(f"  Status: {result.status}")
        print_issues(result.issues)
        if not result.healthy:
            print(_ui.info("Reparo seguro disponível: ft init --fix"))
            raise SystemExit(1)
        return

    if getattr(args, "fix", False):
        result = repair_project(root)
        print(f"  Projeto: {result.root}")
        for action in result.actions:
            print(f"  ✓ {action}")
        print_issues(result.remaining)
        if result.backup_dir:
            print(f"  Backup/relatório: {result.backup_dir}")
        if result.errors:
            print(_ui.fail("Reparo incompleto; os erros acima exigem intervenção."))
            raise SystemExit(1)
        print(_ui.success("Workspace Fast Track saudável."))
        return

    adopt = getattr(args, "adopt", False)
    result = bootstrap_project(root, adopt=adopt)
    print(f"  Projeto: {result.root}")
    for action in result.actions:
        print(f"  ✓ {action}")
    if result.status == "unchanged":
        print(_ui.success("Workspace Fast Track já estava inicializado e saudável."))
    else:
        print(_ui.success("Workspace Fast Track inicializado."))
    if adopt:
        import subprocess as _subprocess

        status = _subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=root, capture_output=True, text=True, timeout=30, check=False,
        )
        if status.returncode == 0 and status.stdout.strip():
            print(_ui.warn(
                "Adoção deixou arquivos legados fora do Git; commite-os antes de "
                "ft run (worktrees exigem checkout limpo)."
            ))
    print("  Nenhum template foi selecionado. Inicie um ciclo com:")
    print("    ft run . --template <template>")


def cmd_continue(args):
    import sys
    sys.stdout.reconfigure(line_buffering=True)
    if not _ensure_runtime_selected(args):
        return
    runner = get_runner(llm_engine=resolve_llm_engine(args), llm_model=resolve_llm_model(args), llm_effort=resolve_llm_effort(args), verbose=getattr(args, "verbose", False), cycle=getattr(args, "cycle", None))
    runner._bypass_human_gates = resolve_bypass_human_gates(args)

    from ft.engine.state import StateLockError
    try:
        state = runner.state_mgr.claim()
    except StateLockError as exc:
        from ft.engine import ui as _ui

        print(_ui.fail(str(exc)))
        sys.exit(1)

    # Ciclo já concluído? NÃO reiniciar do zero (footgun: continue num ciclo
    # done chamava init_state e recomeçava tudo).
    if _cycle_complete(state):
        from ft.engine import ui as _ui
        if runner.audit_completed_cycle():
            print(_ui.fail("Ciclo concluído reaberto: evidência final contradiz o PRD."))
            print(_ui.info("Estado atual: BLOCKED. Corrija o processo/produto pelo fluxo ft antes de fechar novamente."))
            return
        template = getattr(state, "template_id", None) or "<template>"
        print(_ui.warn(
            "Ciclo já concluído — nada a retomar. Novo ciclo: "
            f"ft run . --template {template}"
        ))
        return
    # Inicializar estado só se nunca rodou
    if state.current_node is None:
        runner.init_state()
    apply_parallel_flags(runner, args)

    mode = resolve_run_mode(args)
    recovered = runner.recover_orphaned_delegation(mode=mode)
    if recovered:
        recovered_state = runner.state_mgr.load()
        if (
            mode == "step"
            or recovered_state.node_status == "awaiting_approval"
            or recovered_state.current_node is None
        ):
            return
    runner.run(mode=mode)


def cmd_status(args):
    def _runner_for(cycle):
        return get_runner(
            llm_engine=resolve_llm_engine(args),
            llm_model=resolve_llm_model(args),
            llm_effort=resolve_llm_effort(args),
            verbose=getattr(args, "verbose", False),
            cycle=cycle,
        )

    def _print_status(runner):
        if getattr(args, "report", False):
            runner.status_report()
        else:
            runner.status(full=getattr(args, "full", False))

    requested = getattr(args, "cycle", None)
    if requested is None:
        # Vários ciclos abertos sem --cycle: um bloco de status rotulado por
        # ciclo, em vez de erro — mesmo contrato do multi-status pré-registry.
        # A pré-resolução espelha _select_cycle_for_command, mas trata a
        # ambiguidade como fan-out em vez de SystemExit.
        from ft.runs import AmbiguousCycleError, select_cycle

        location = find_project_root().resolve()
        owner = canonical_project_root(location)
        implicit = location.name if paths.is_worktree_path(location) else None
        try:
            select_cycle(owner, implicit, include_terminal=True)
        except AmbiguousCycleError as error:
            from ft.engine import ui as _ui

            for index, cycle_id in enumerate(error.cycle_ids):
                if index:
                    print()
                print(_ui.header(f"Ciclo: {cycle_id}"))
                _print_status(_runner_for(cycle_id))
            return
        except Exception:
            # Sem ciclo/erros de seleção: o caminho normal abaixo reporta
            # com a mensagem canônica.
            pass
    _print_status(_runner_for(requested))


def _truncate_visible(s: str, width: int, reset: str = "") -> str:
    """Trunca `s` (pode conter ANSI) para `width` COLUNAS VISÍVEIS — sequências
    ANSI não contam. Garante `reset` no fim (cor não vaza). Evita que uma linha
    longa de heartbeat quebre em várias e empilhe sob o overwrite com \\r."""
    if width <= 0:
        return s
    out: list[str] = []
    vis = 0
    i = 0
    truncated = False
    while i < len(s):
        m = _ANSI_RE.match(s, i)
        if m:
            out.append(m.group(0))
            i = m.end()
            continue
        if vis >= width:
            truncated = True
            break
        out.append(s[i])
        vis += 1
        i += 1
    res = "".join(out)
    if truncated and vis > 0:
        res = res[:-1] + "…"
    if reset and not res.endswith(reset):
        res += reset
    return res


def _think_snippet(text: str | None, n: int = 70) -> str:
    """Últimos ~n chars do raciocínio, numa linha só — para o heartbeat mostrar
    SOBRE o que o worker está pensando."""
    return " ".join((text or "").split())[-n:]


def _track_heartbeat(raw: str, ctx: dict) -> str | None:
    """Atualiza o contexto do heartbeat de ``ft log --follow``.

    Recebe uma linha crua do stream-json e o dict de contexto ``ctx`` (mutado
    in-place: escreve ``ctx["desc"]`` com uma descrição legível do último
    evento). Retorna um fragmento de thinking quando houver, senão ``None``.

    Extraído para nível de módulo para ser testável — não depende de nada do
    escopo de ``cmd_log``.
    """
    import json as _json

    if not raw.startswith("{"):
        return None
    try:
        ev = _json.loads(raw)
    except Exception:
        return None
    etype = ev.get("type", "")
    if etype == "stream_event":
        inner = ev.get("event", {})
        if inner.get("type") == "content_block_delta":
            delta = inner.get("delta", {})
            if delta.get("type") == "thinking_delta":
                frag = delta.get("thinking", "")
                if frag:
                    # tail rolante do raciocínio, para o heartbeat mostrar SOBRE
                    # o que ele está pensando quando o pensamento fica denso.
                    ctx["think"] = (ctx.get("think", "") + frag)[-200:]
                snip = _think_snippet(ctx.get("think"))
                ctx["desc"] = f"raciocinando: …{snip}" if snip else "raciocinando"
                return frag
        return None
    if etype == "system":
        subtype = ev.get("subtype", "")
        if subtype == "thinking_tokens":
            toks = ev.get("estimated_tokens", 0)
            snippet = _think_snippet(ctx.get("think"))
            ctx["desc"] = f"pensando (~{toks} tokens)" + (f": …{snippet}" if snippet else "")
        elif subtype == "init":
            # Evento de abertura de sessão: expõe modelo, modo de permissão e
            # nº de ferramentas em vez de um "evento system" opaco.
            model = ev.get("model") or "?"
            ctx["model"] = model
            n_tools = len(ev.get("tools") or [])
            mode = ev.get("permissionMode") or ""
            mode_txt = f", {mode}" if mode else ""
            ctx["desc"] = f"sessão iniciada ({model}, {n_tools} tools{mode_txt})"
        elif subtype:
            ctx["desc"] = f"evento system/{subtype}"
        else:
            ctx["desc"] = "evento system"
    elif etype == "user":
        ctx["desc"] = "resultado de ferramenta recebido, processando"
    elif etype == "assistant":
        # Mostra o que o worker está fazendo (ferramenta + alvo, ou trecho do
        # texto) em vez de um "gerando resposta" genérico.
        msg = ev.get("message", {}) or {}
        if msg.get("model"):
            ctx["model"] = msg.get("model")
        blocks = msg.get("content", []) or []
        tool = next((b for b in blocks if b.get("type") == "tool_use"), None)
        if tool:
            name = tool.get("name") or "ferramenta"
            inp = tool.get("input") or {}
            target = str(
                inp.get("file_path") or inp.get("command")
                or inp.get("pattern") or inp.get("path") or ""
            )
            # Para ferramentas de arquivo, mostra só o basename.
            if name in ("Read", "Edit", "Write", "NotebookEdit") and "/" in target:
                target = target.rsplit("/", 1)[-1]
            target = " ".join(target.split())  # colapsa quebras/espaços
            ctx["desc"] = f"{name}: {target}" if target else name
        else:
            txt = next(
                (b.get("text", "") for b in blocks
                 if b.get("type") == "text" and b.get("text", "").strip()),
                "",
            )
            if txt:
                ctx["desc"] = "escrevendo: " + " ".join(txt.split())
            else:
                think = next((b.get("thinking", "") for b in blocks
                              if b.get("type") == "thinking"), None)
                if think is not None:
                    snip = _think_snippet(think)
                    ctx["desc"] = f"raciocinando: …{snip}" if snip else "raciocinando"
                else:
                    ctx["desc"] = "gerando resposta"
    elif etype == "result":
        # Evento final do worker: resume desfecho, turnos, tempo e custo em vez
        # de um "evento result" opaco.
        head = "resultado com erro" if ev.get("is_error") else "resultado ok"
        subtype = ev.get("subtype") or ""
        parts: list[str] = []
        if subtype and subtype != "success":
            parts.append(subtype)
        if ev.get("num_turns") is not None:
            parts.append(f"{ev['num_turns']} turnos")
        dur = ev.get("duration_ms")
        if isinstance(dur, (int, float)):
            parts.append(f"{dur / 1000:.1f}s")
        cost = ev.get("total_cost_usd")
        if isinstance(cost, (int, float)):
            parts.append(f"US$ {cost:.2f}")
        ctx["desc"] = head + (" — " + " · ".join(parts) if parts else "")
    elif etype:
        ctx["desc"] = f"evento {etype}"
    return None


def _log_model_prefix(model: object | None) -> str:
    """Prefixo estável para emissões formatadas de `ft log`."""
    return f"[{model}] " if model else ""


def _needs_block_blank(prev_is_bash: bool, cur_is_bash: bool) -> bool:
    """True quando a transição de linha cruza a borda de um bloco bash — ou
    seja, entra (não-bash→bash) ou sai (bash→não-bash). Linha em branco só nas
    bordas: bashes consecutivos ficam colados, lidos como um bloco só."""
    return prev_is_bash != cur_is_bash


def _wait_reason(node_status: str | None, pending_approval: str | None,
                 blocked_reason: str | None, node: str | None,
                 orchestrator_alive: bool = True) -> tuple[str | None, str | None]:
    """Motivo REAL da espera, derivado do estado do engine (não do log).

    Retorna (kind, texto): kind ∈ {"gate", "blocked", "stalled", None}. None
    significa que a espera é genuinamente pelo LLM/ferramenta (comportamento
    normal do heartbeat). "stalled" = o node não é gate nem bloqueio, mas nenhum
    orquestrador está vivo para avançá-lo — o ciclo está parado.
    """
    if node_status == "done":
        return "done", "ciclo COMPLETO"
    if pending_approval or node_status == "awaiting_approval":
        gate = pending_approval or node or "?"
        return "gate", f"aguardando APROVAÇÃO em {gate} — ft approve / ft reject"
    if node_status == "blocked":
        return "blocked", f"BLOQUEADO em {node or '?'}: {_oneline(blocked_reason) or 'sem motivo registrado'}"
    if not orchestrator_alive:
        return "stalled", f"ciclo PARADO em {node or '?'} — rode `ft continue --auto`"
    return None, None


def _orchestrator_alive(state_mgr, st) -> bool:
    """True se o processo que segura o lock do estado ainda está vivo. O lock é
    reescrito com o pid a cada save e nunca liberado, então um pid morto = o
    orquestrador saiu (ciclo parado)."""
    try:
        from ft.engine.state import lock_owner_is_alive

        return lock_owner_is_alive(getattr(st, "_lock", None))
    except Exception:
        return True  # na dúvida, não alarma falso


def _fmt_elapsed(seconds: float) -> str:
    """Formata um intervalo de silêncio como 'há Ns' ou 'há Nmin Ss'."""
    s = max(0, int(seconds))
    if s < 60:
        return f"há {s}s"
    return f"há {s // 60} min {s % 60:02d}s"


def _fmt_duration(seconds: float | int | None) -> str:
    """Formata duração total de ciclo em português curto."""
    if seconds is None:
        return "desconhecida"
    s = max(0, int(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}min {sec:02d}s"
    if m:
        return f"{m}min {sec:02d}s"
    return f"{sec}s"


def _run_log_path_for(root: Path) -> Path | None:
    candidates = sorted(root.glob("*_log.md"), key=lambda p: p.stat().st_mtime if p.exists() else 0)
    return candidates[-1] if candidates else None


def _run_log_duration_seconds(root: Path) -> int | None:
    """Duração aproximada do ciclo pelo primeiro e último timestamp do run log."""
    from datetime import datetime as _dt
    import re as _re

    log_path = _run_log_path_for(root)
    if not log_path or not log_path.exists():
        return None
    timestamps: list[_dt] = []
    for line in log_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        match = _re.match(r"\|\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s*\|", line)
        if not match:
            continue
        try:
            timestamps.append(_dt.strptime(match.group(1), "%Y-%m-%d %H:%M:%S"))
        except ValueError:
            continue
    if len(timestamps) < 2:
        return None
    return int((timestamps[-1] - timestamps[0]).total_seconds())


def _file_exists_mark(root: Path, relative_path: str) -> str:
    return "✓" if (root / relative_path).exists() else "✗"


def _backlog_report_line(root: Path) -> str:
    backlog = root / "docs" / "PROJECT_BACKLOG.md"
    if not backlog.exists():
        return "—"
    summary = project_backlog_summary(project_root=str(root))
    total = int(summary.get("total") or 0)
    by_status = summary.get("by_status") if isinstance(summary.get("by_status"), dict) else {}
    done = int(by_status.get("done", 0) or 0) + int(by_status.get("accepted", 0) or 0)
    undecided = summary.get("undecided_p0_p1") or []
    pending_txt = f"; P0/P1 sem decisão: {len(undecided)}" if undecided else "; P0/P1 sem decisão: 0"
    return f"{done}/{total} done{pending_txt}"


def _features_report_line(root: Path) -> str:
    catalog = root / "docs" / "FEATURES.md"
    if not catalog.exists():
        return "—"
    summary = features_summary(project_root=str(root))
    total = int(summary.get("total") or 0)
    by_status = summary.get("by_status") if isinstance(summary.get("by_status"), dict) else {}
    active = int(by_status.get("active", 0) or 0)
    deprecated = int(by_status.get("deprecated", 0) or 0)
    removed = int(by_status.get("removed", 0) or 0)
    return f"{active}/{total} active; deprecated: {deprecated}; removed: {removed}"


def _cycle_completion_report(runner) -> list[str]:
    """Resumo útil para `ft log` quando o ciclo selecionado já terminou."""
    state = runner.state_mgr.load()
    root = Path(runner._work_dir)
    metrics = state.metrics or {}
    done = metrics.get("steps_completed", len(state.completed_nodes))
    total = metrics.get("steps_total", "?")
    cycle_name = root.name
    url = "—"
    serve_file = root / ".serve_url"
    if serve_file.exists():
        url = serve_file.read_text(encoding="utf-8", errors="ignore").strip() or "—"
    duration = _fmt_duration(_run_log_duration_seconds(root))
    engine = state.llm_engine or "?"
    model = state.llm_model or ("pgx/zai-org_glm-4.7-flash" if engine == "opencode" else "default")
    llm_calls = metrics.get("llm_calls", "?")
    usage_summary = metrics.get("llm_usage") if isinstance(metrics.get("llm_usage"), dict) else None
    if not usage_summary:
        usage_summary = summarize_llm_usage(
            runner.state_mgr.path.parent / "llm_logs",
            default_engine=engine,
            default_model=state.llm_model,
        )
    usage_lines = format_llm_usage_lines(
        usage_summary
    )
    tests = [
        ("Acceptance", "docs/acceptance-result.json"),
        ("E2E report", "docs/e2e-report.md"),
        ("Visual check", "docs/visual-check-report.md"),
        ("Handoff", "docs/handoff.md"),
    ]
    artifacts = ", ".join(f"{_file_exists_mark(root, path)} {label}" for label, path in tests)
    backlog_line = _backlog_report_line(root)
    features_line = _features_report_line(root)

    return [
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "  CICLO COMPLETO",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"  Ciclo:      {cycle_name}",
        f"  Progresso:  {done}/{total} steps",
        f"  Duração:    {duration}",
        f"  LLM:        {engine} ({model})",
        f"  LLM calls:  {llm_calls}",
        *usage_lines,
        f"  Testar em:  {url}",
        f"  Backlog:    {backlog_line}",
        f"  Features:   {features_line}",
        f"  Worktree:   {root}",
        f"  Artefatos:  {artifacts}",
        "",
        "  Comandos úteis:",
        f"    ft status --cycle {cycle_name} --full",
        "    ft runs",
        f"    cd {root} && make -C project build && make -C project test",
        f"    cd {root} && python -m pytest project/tests/e2e -q",
        "",
    ]


def _node_from_log_name(name: str) -> str | None:
    """Extrai o id do node do nome do log (``TIMESTAMP__<node>__sufixo.log``)."""
    parts = name.split("__")
    return parts[1] if len(parts) >= 2 and parts[1] else None


def _log_mtime(path) -> float:
    """mtime do log (última escrita) — âncora do contador de silêncio. Assim
    reabrir `ft log -f` continua do silêncio real, em vez de zerar o relógio."""
    import time as _t
    try:
        return path.stat().st_mtime
    except OSError:
        return _t.time()


def cmd_log(args):
    """Mostra/acompanha o log LLM do ciclo selecionado, formatado para leitura humana."""
    import time as _time
    from ft.engine.delegate import _format_stream_line
    from ft.engine import ui as _ui

    # `ft log` puro (nenhum parâmetro) → help explicando os parâmetros.
    # Para ver as últimas linhas sem acompanhar, use `ft log -n 30`.
    if not (args.follow or args.raw or args.path or args.lines is not None):
        args._parser.print_help()
        return
    lines = args.lines if args.lines is not None else 30

    runner = get_runner(
        llm_engine=resolve_llm_engine(args),
        llm_model=resolve_llm_model(args),
        llm_effort=resolve_llm_effort(args),
        cycle=getattr(args, "cycle", None),
    )
    if not _ensure_runtime_selected(args, runner):
        return

    def _current_log() -> Path | None:
        state = runner.state_mgr.load()
        rel = state.active_llm_log or state.last_llm_log
        if rel:
            p = Path(rel)
            if not p.is_absolute():
                p = Path(runner._work_dir) / rel
            if p.exists():
                return p
        # Fallback: arquivo mais recente em llm_logs/
        log_dir = runner.state_mgr.path.parent / "llm_logs"
        if log_dir.is_dir():
            logs = sorted(log_dir.glob("*.log"), key=lambda f: f.stat().st_mtime)
            if logs:
                return logs[-1]
        return None

    _engine = runner._resolve_llm_engine()
    _last_out: list[str | None] = [None]
    _md = getattr(args, "markdown", False)
    _tty = sys.stdout.isatty()
    try:
        _initial_state = runner.state_mgr.load()
        _initial_model = getattr(_initial_state, "llm_model", None)
    except Exception:
        _initial_model = None
    _model_ctx: dict = {"desc": "", "model": _initial_model}

    def _paint(s: str) -> str:
        return _ui.paint_stream_line(s) if _md else s

    def _model_prefix() -> str:
        prefix = _log_model_prefix(_model_ctx.get("model"))
        return _ui.dim(prefix) if _md else prefix

    # Heartbeat "vivo": num terminal, a linha de silêncio se sobrescreve
    # (carriage return, sem newline) em vez de acumular uma linha por tick.
    # `_hb_live` marca que há uma linha pendente a ser apagada antes de imprimir
    # conteúdo de verdade. Fora de terminal (pipe), cai no comportamento antigo.
    _hb_live = [False]

    def _clear_hb() -> None:
        """Apaga a linha de heartbeat viva antes de imprimir conteúdo real."""
        if _hb_live[0]:
            if _tty:
                sys.stdout.write("\r\033[K")
                sys.stdout.flush()
            _hb_live[0] = False

    # Espaçamento (modo markdown): um BLOCO de comandos bash consecutivos é
    # isolado por uma única linha em branco no topo e outra no fim. Os bashes
    # do meio ficam colados, lidos como um bloco só. `_prev_bash` detecta as
    # transições de/para o bloco — o branco só sai na borda, não a cada comando.
    _prev_bash = [False]

    def _space_for(is_bash: bool) -> None:
        if not _md:
            return
        if _needs_block_blank(_prev_bash[0], is_bash):
            print(flush=True)      # borda do bloco bash (abre ou fecha)
        _prev_bash[0] = is_bash

    def _emit(out_plain: str) -> None:
        """Imprime uma linha de conteúdo (não-raw) já formatada, com o
        espaçamento de borda de bloco bash do modo markdown."""
        _clear_hb()
        _space_for(out_plain.startswith("$ "))
        print(_model_prefix() + _paint(out_plain), flush=True)

    def _fmt(line: str) -> str | None:
        _track_heartbeat(line.strip(), _model_ctx)
        out = _format_stream_line(_engine, line)
        if not out or (out.startswith("event ") and not args.raw):
            return None
        # Stream parcial repete o mesmo bloco várias vezes — dedupe consecutivo
        if out == _last_out[0]:
            return None
        _last_out[0] = out
        return out

    log_path = _current_log()
    if log_path is None:
        print(_ui.warn("Nenhum log LLM encontrado para o ciclo selecionado"), flush=True)
        return

    if args.path:
        print(log_path, flush=True)
        return

    print(_ui.dim(f"── {log_path.name} ──"), flush=True)
    with log_path.open(errors="replace") as f:
        raw_lines = f.readlines()
    shown = [x for x in (line.rstrip() if args.raw else _fmt(line) for line in raw_lines) if x]
    for out in shown[-lines:]:
        if args.raw:
            print(out, flush=True)
        else:
            _emit(out)

    try:
        selected_state = runner.state_mgr.load()
    except Exception:
        selected_state = None
    if selected_state and selected_state.node_status in ("done", "completed") and not args.raw:
        for line in _cycle_completion_report(runner):
            print(line, flush=True)
        if args.follow:
            return

    if not args.follow:
        return

    # Follow: acompanha o arquivo e troca sozinho quando o engine abre um log novo.
    # Heartbeat: se ficar >15s sem linha impressa, mostra o que o worker está fazendo
    # (thinking tokens, último evento) para não parecer travado.
    _track = _track_heartbeat

    try:
        f = log_path.open(errors="replace")
        f.seek(0, 2)
        idle = 0.0
        last_print = _time.time()
        hb = {"desc": "", "t": _log_mtime(log_path), "model": _model_ctx.get("model")}

        def _heartbeat() -> None:
            nonlocal last_print
            now = _time.time()
            if now - last_print >= 15.0:
                elapsed = _fmt_elapsed(now - hb["t"])
                # Consulta o estado do engine: a espera pode ser por um GATE
                # humano ou um BLOQUEIO — não pelo LLM. O log sozinho não sabe.
                kind = text = st = None
                try:
                    st = runner.state_mgr.load()
                    node = st.current_node or _node_from_log_name(log_path.name)
                    kind, text = _wait_reason(st.node_status, st.pending_approval,
                                              st.blocked_reason, node,
                                              _orchestrator_alive(runner.state_mgr, st))
                except Exception:
                    pass
                if kind == "done":
                    line = f"  {_ui.BOLD_GREEN}✓ {text}{_ui.RESET}"
                elif kind == "gate":
                    line = f"  {_ui.BOLD_YELLOW}⏸ {text} · {elapsed}{_ui.RESET}"
                elif kind == "blocked":
                    line = f"  {_ui.BOLD_RED}⛔ {text} · {elapsed}{_ui.RESET}"
                elif kind == "stalled":
                    line = f"  {_ui.BOLD_YELLOW}⚠ {text} · {elapsed}{_ui.RESET}"
                elif hb["desc"]:
                    model_txt = f"[{hb.get('model')}] " if hb.get("model") else ""
                    line = _ui.dim(f"  ⋯ {model_txt}{hb['desc']} · {elapsed}")
                else:
                    node = (st.current_node if st else None) or _node_from_log_name(log_path.name)
                    node_ctx = f" ({node})" if node else ""
                    model_txt = f"[{hb.get('model')}] " if hb.get("model") else ""
                    line = _ui.dim(f"  ⋯ {model_txt}aguardando eventos do LLM{node_ctx} · {elapsed}")
                # Cinto de segurança: um heartbeat é SEMPRE uma linha. Qualquer
                # \n vindo de texto do estado quebraria o overwrite com \r e
                # vazaria a cor para o resto do log.
                line = line.replace("\n", " ")
                if _tty:
                    # Trunca à largura do terminal: uma linha que quebra em duas
                    # faz o overwrite com \r empilhar (o \r\033[K limpa só a
                    # última). Uma linha só = overwrite limpo.
                    import shutil as _shutil
                    cols = _shutil.get_terminal_size((80, 24)).columns
                    line = _truncate_visible(line, cols - 1, _ui.RESET)
                    # Sobrescreve a mesma linha (\r + limpa até o fim), sem newline:
                    # o contador de silêncio atualiza no lugar, sem empilhar linhas.
                    sys.stdout.write("\r\033[K" + line)
                    sys.stdout.flush()
                    _hb_live[0] = True
                else:
                    print(line, flush=True)
                last_print = now

        think_buf = ""

        def _flush_think(force: bool = False) -> None:
            nonlocal think_buf, last_print
            while "\n" in think_buf:
                head, think_buf = think_buf.split("\n", 1)
                if head.strip():
                    _clear_hb()
                    _space_for(False)  # texto de raciocínio fecha bloco bash aberto
                    msg = f"✻ {head.strip()}"
                    rendered = _paint(msg) if _md else _ui.dim(msg)
                    print(_model_prefix() + rendered, flush=True)
                    last_print = _time.time()
            if force and think_buf.strip():
                _clear_hb()
                _space_for(False)
                msg = f"✻ {think_buf.strip()}"
                rendered = _paint(msg) if _md else _ui.dim(msg)
                print(_model_prefix() + rendered, flush=True)
                think_buf = ""
                last_print = _time.time()

        while True:
            line = f.readline()
            if line:
                idle = 0.0
                hb["t"] = _time.time()  # marca atividade — silêncio conta a partir daqui
                frag = _track(line.strip(), hb)
                if hb.get("model"):
                    _model_ctx["model"] = hb.get("model")
                if frag is not None and not args.raw:
                    think_buf += frag
                    _flush_think()
                    continue
                out = line.rstrip() if args.raw else _fmt(line)
                if out:
                    _flush_think(force=True)
                    if args.raw:
                        print(out, flush=True)
                    else:
                        _emit(out)
                    last_print = _time.time()
                else:
                    _heartbeat()
                continue
            _time.sleep(0.5)
            idle += 0.5
            _heartbeat()
            if idle >= 3.0:
                idle = 0.0
                newer = _current_log()
                if newer and newer != log_path:
                    _clear_hb()
                    f.close()
                    log_path = newer
                    print(_ui.dim(f"── {log_path.name} ──"), flush=True)
                    f = log_path.open(errors="replace")
                    hb["desc"] = ""
                    hb["model"] = _model_ctx.get("model")
                    hb["t"] = _log_mtime(log_path)
    except KeyboardInterrupt:
        pass
    finally:
        # Fixa a linha de heartbeat viva com um newline para não deixar o prompt
        # do shell colado nela.
        if _hb_live[0] and _tty:
            sys.stdout.write("\n")
            sys.stdout.flush()
        try:
            f.close()
        except Exception:
            pass


def cmd_runs(args):
    """Mostra ciclos ativos no runtime e ciclos fechados em .ft/cycles/."""
    from ft.engine import ui as _ui

    project_root = Path(args.project).resolve()
    _guard_engine_repo(project_root)

    # Runtime ganha de um arquivo histórico com o mesmo nome.
    cycles: dict[str, tuple[Path, bool]] = {}
    archive_home = paths.project_cycles_dir(project_root)
    if archive_home.is_dir():
        for cycle in archive_home.iterdir():
            if cycle.is_dir() and _is_cycle_dir(cycle):
                cycles[cycle.name] = (cycle, True)

    wt_home = paths.worktrees_home(project_root)
    if wt_home.is_dir():
        for cycle in wt_home.iterdir():
            if cycle.is_dir() and _is_cycle_dir(cycle):
                state_file = cycle / "state" / "engine_state.yml"
                state_data = _state_data(state_file) if state_file.is_file() else {}
                if state_data and _is_pristine_cycle_dir(cycle, state_data):
                    continue
                cycles[cycle.name] = (cycle, False)

    if not cycles:
        print(_ui.warn("Nenhum ciclo encontrado"))
        return

    import yaml as _yaml

    rows = []
    for cycle, archived in sorted(cycles.values(), key=lambda item: _cycle_num(item[0])):
        # Serve URL — buscar .serve_url na raiz do ciclo
        serve_url = "—"
        serve_file = cycle / ".serve_url"
        if serve_file.exists():
            serve_url = serve_file.read_text().strip()

        state_data = {}
        state_path = cycle / ("cycle.yml" if archived else "state/engine_state.yml")
        if state_path.exists():
            try:
                state_data = _yaml.safe_load(state_path.read_text()) or {}
            except Exception:
                pass
        if not state_data:
            continue  # ciclo vazio/fantasma — sem estado

        if archived:
            progress = state_data.get("progress", {})
            steps_done = progress.get("completed", 0)
            steps_total = progress.get("total", "?")
        else:
            steps_done = state_data.get("metrics", {}).get("steps_completed", len(state_data.get("completed_nodes", [])))
            steps_total = state_data.get("metrics", {}).get("steps_total", "?")
        current_node = state_data.get("current_node") or ""
        node_status = state_data.get("status" if archived else "node_status", "")

        # Timestamp da última entrada no log de atividade
        ts = "—"
        log = cycle / "cycle-log.md" if archived else next(cycle.glob("*_log.md"), None)
        if log and log.is_file():
            lines = [line for line in log.read_text().splitlines() if line.startswith("| 2")]
            if lines:
                last = lines[-1].split("|")
                ts = last[1].strip()[11:16] if len(last) > 1 else "—"

        # Node a exibir
        if not current_node:
            node = "DONE" if node_status == "done" else "—"
        else:
            node = current_node

        # Status colorido (sem a indentação padrão dos helpers — é célula de tabela)
        if node_status == "done":
            status_str = _ui.success(node).lstrip()
        elif node_status == "blocked":
            status_str = _ui.fail(node).lstrip()
        elif node_status == "awaiting_approval":
            status_str = _ui.warn(f"⏸ {node}").lstrip()
        elif node_status == "delegated":
            status_str = f"⟳ {node}"
        else:
            status_str = node

        source = "archive" if archived else "runtime"
        steps_label = f"{steps_done}/{steps_total}".replace("unknown", "?")
        # Só o histórico arquivado sai do default: um ciclo done ainda no
        # runtime tem worktree aberto e precisa de ft close — continua visível.
        finished = archived and node_status in ("done", "completed")
        if finished and not getattr(args, "done", False):
            continue
        rows.append((cycle.name, steps_label, ts, status_str, serve_url, source))

    # Tabela com larguras dinâmicas; o STATUS carrega cores ANSI, então o
    # padding usa a largura visível (códigos de escape não contam coluna).
    def _visible_len(text: str) -> int:
        return len(_ANSI_RE.sub("", text))

    def _pad_visible(text: str, width: int) -> str:
        return text + " " * max(0, width - _visible_len(text))

    def _col(header: str, values, measure=len) -> int:
        return max(len(header), max((measure(v) for v in values), default=0))

    if not rows:
        print(_ui.info("Nenhum ciclo ativo. Use ft runs --done para incluir os concluídos."))
        return

    w_name = _col("CICLO", (r[0] for r in rows))
    w_steps = _col("STEPS", (r[1] for r in rows))
    w_ts = _col("ÚLT.", (r[2] for r in rows))
    w_status = _col("STATUS", (r[3] for r in rows), _visible_len)
    w_source = _col("FONTE", (r[5] for r in rows))

    print()
    print(
        f"  {'CICLO':<{w_name}}  {'STEPS':>{w_steps}}  {'ÚLT.':>{w_ts}}  "
        f"{'STATUS':<{w_status}}  {'FONTE':<{w_source}}  URL"
    )
    print(
        f"  {'─' * w_name}  {'─' * w_steps}  {'─' * w_ts}  "
        f"{'─' * w_status}  {'─' * w_source}  {'─' * 3}"
    )
    for name, steps, ts, node_str, url, source in rows:
        print(
            f"  {name:<{w_name}}  {steps:>{w_steps}}  {ts:>{w_ts}}  "
            f"{_pad_visible(node_str, w_status)}  {source:<{w_source}}  {url}"
        )
    print()


def cmd_approve(args):
    runner = get_runner(llm_engine=resolve_llm_engine(args), llm_model=resolve_llm_model(args), llm_effort=resolve_llm_effort(args), verbose=getattr(args, "verbose", False), cycle=getattr(args, "cycle", None))
    if not _ensure_runtime_selected(args, runner):
        return
    runner._bypass_human_gates = resolve_bypass_human_gates(args)
    message = getattr(args, "message", None)
    runner.approve(message=message)
    # Continuar automaticamente após aprovação, no modo pedido (--auto avança
    # sozinho até o próximo human gate, sem o dança approve-step + continue).
    if not args.no_continue:
        runner.run(mode=resolve_run_mode(args))


def cmd_reject(args):
    runner = get_runner(llm_engine=resolve_llm_engine(args), llm_model=resolve_llm_model(args), llm_effort=resolve_llm_effort(args), verbose=getattr(args, "verbose", False), cycle=getattr(args, "cycle", None))
    if not _ensure_runtime_selected(args, runner):
        return
    retry = not args.no_retry
    runner.reject(args.reason, retry=retry)
    correction_policy = runner.graph.meta.get("correction_policy", {})
    follow_graph = (
        isinstance(correction_policy, dict)
        and correction_policy.get("follow_graph_after_retry") is True
    )
    state = runner.state_mgr.load()
    if retry and follow_graph and state.node_status == "ready":
        runner.run(mode="mvp")


def _active_exploration_cycle(
    root: Path,
    requested: str | None = None,
) -> str | None:
    """Return an unambiguous cycle currently stopped in exploration."""
    from ft.runs import CycleRegistry

    owner = canonical_project_root(root)
    registry = CycleRegistry(owner)
    if requested is not None:
        record = _select_cycle_for_command(root, requested)
        return record.name if record.status == "exploring" else None
    exploring = [
        record
        for record in registry.open_cycles(include_terminal=False)
        if record.status == "exploring"
    ]
    if len(exploring) > 1:
        _select_cycle_for_command(root, None)
    return exploring[0].name if exploring else None


def _explicit_explore_selection(args) -> tuple[str | None, str | None]:
    """Compatibiliza --agent/--model com os flags históricos --codex etc."""

    legacy_agent = resolve_llm_engine(args)
    requested_agent = getattr(args, "agent", None)
    agent = str(requested_agent).strip().lower() if requested_agent else legacy_agent
    if requested_agent and legacy_agent and agent != legacy_agent:
        raise ValueError("use --agent ou o flag histórico do provider, não ambos")

    legacy_model = resolve_llm_model(args)
    requested_model = getattr(args, "model", None)
    model = str(requested_model).strip() if requested_model else legacy_model
    if requested_model and legacy_model and model != legacy_model:
        raise ValueError("use --model ou o modelo junto ao flag do provider, não ambos")
    return agent, model


def _standalone_explore_selection(
    args,
    root: Path,
) -> tuple[str, str | None, str | None]:
    explicit_agent, explicit_model = _explicit_explore_selection(args)
    manifest_agent, manifest_model, manifest_effort = manifest_llm_defaults(root)
    agent = (
        explicit_agent
        or manifest_agent
        or os.environ.get("FT_LLM_ENGINE", "").strip().lower()
        or "claude"
    )
    same_as_manifest = bool(manifest_agent and agent == manifest_agent)
    model = explicit_model or (manifest_model if same_as_manifest else None)

    requested_effort = getattr(args, "effort", None)
    if requested_effort is None:
        effort = manifest_effort if same_as_manifest else None
    else:
        effort = str(requested_effort).strip() or None
        if effort and effort.lower() == "default":
            effort = None
    return agent, model, effort


def _print_explore_json(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)


def cmd_explore(args):
    """Exploração legada no node ou consulta standalone read-only sem ciclo."""
    from ft.engine import ui as _ui

    root = canonical_project_root(find_project_root())
    stream_json = bool(getattr(args, "stream_json", False))
    force_standalone = bool(getattr(args, "standalone", False) or stream_json)
    exploration_cycle = (
        None
        if force_standalone
        else _active_exploration_cycle(root, getattr(args, "cycle", None))
    )
    if exploration_cycle:
        # Compatibilidade integral: quando o grafo possui uma exploração ativa,
        # request/finish/skip continuam usando estado, logs e worktree históricos.
        explicit_agent, explicit_model = _explicit_explore_selection(args)
        runner = get_runner(
            llm_engine=explicit_agent,
            llm_model=explicit_model,
            llm_effort=resolve_llm_effort(args),
            verbose=getattr(args, "verbose", False),
            cycle=exploration_cycle,
        )
        runner._bypass_human_gates = resolve_bypass_human_gates(args)
        if getattr(args, "finish", False):
            runner.explore_finish()
        elif getattr(args, "skip", False):
            runner.explore_skip()
        else:
            request = getattr(args, "request", None)
            if not request:
                state = runner.state_mgr.load()
                log = state.exploration_log or []
                print(_ui.exploration_start("Exploração Livre", len(log)))
                return
            runner.explore_request(request)
        return

    if getattr(args, "finish", False) or getattr(args, "skip", False):
        message = "--finish/--skip exigem um node exploration ativo"
        if stream_json:
            _print_explore_json({"type": "error", "code": "legacy_node_required", "message": message, "exit_code": 2})
        else:
            print(_ui.fail(message), file=sys.stderr)
        raise SystemExit(2)

    request = str(getattr(args, "request", None) or "").strip()
    if not request:
        message = "Informe um prompt: ft explore \"sua pergunta\""
        if stream_json:
            _print_explore_json({"type": "error", "code": "prompt_required", "message": message, "exit_code": 2})
        else:
            print(_ui.fail(message), file=sys.stderr)
        raise SystemExit(2)

    from ft.engine.read_only_explore import (
        ExploreConfigurationError,
        run_read_only_explore,
    )

    try:
        agent, model, effort = _standalone_explore_selection(args, root)
        if stream_json:
            _print_explore_json({
                "type": "start",
                "agent": agent,
                "model": model,
                "effort": effort,
                "mode": "standalone",
                "read_only": True,
            })

        sequence = 0

        def on_chunk(text: str) -> None:
            nonlocal sequence
            sequence += 1
            if stream_json:
                _print_explore_json({"type": "chunk", "seq": sequence, "text": text})
            else:
                print(text, end="", flush=True)

        result = run_read_only_explore(
            request=request,
            project_root=root,
            agent=agent,
            model=model,
            effort=effort,
            on_chunk=on_chunk,
        )
    except (ExploreConfigurationError, ValueError) as exc:
        if stream_json:
            _print_explore_json({
                "type": "error",
                "code": "invalid_configuration",
                "message": str(exc),
                "exit_code": 2,
            })
        else:
            print(_ui.fail(str(exc)), file=sys.stderr)
        raise SystemExit(2)

    if result.returncode == 0:
        if stream_json:
            _print_explore_json({
                "type": "result",
                "ok": True,
                "text": result.text,
                "exit_code": 0,
            })
        elif result.text and not result.text.endswith("\n"):
            print()
        return

    message = result.error or f"executor saiu com código {result.returncode}"
    if stream_json:
        _print_explore_json({
            "type": "error",
            "code": "executor_failed",
            "message": message,
            "text": result.text,
            "exit_code": result.returncode,
        })
    else:
        if result.text and not result.text.endswith("\n"):
            print()
        print(_ui.fail(message), file=sys.stderr)
    raise SystemExit(result.returncode)


def cmd_evolve(args):
    """Evolui o processo (local e/ou global) em paralelo ao ciclo.

    Usa o contexto do ciclo para derivar melhorias, mas nunca avança steps:
    o playbook roda num workspace descartável em runtime_home e as mudanças
    só chegam aos alvos reais via apply determinístico pós-validação.
    """
    from ft.engine import evolve as _evolve
    from ft.engine import ui as _ui

    sys.stdout.reconfigure(line_buffering=True)
    root = find_project_root().resolve()

    include_project = bool(getattr(args, "project_target", False))
    include_global = bool(getattr(args, "global_target", False))
    try:
        targets = _evolve.resolve_targets(
            root,
            include_project=include_project,
            include_global=include_global,
            engine_root=engine_root(),
        )
    except _evolve.EvolveError as exc:
        print(_ui.fail(str(exc)))
        print(_ui.info("Uso: ft evolve [diretriz] --project e/ou --global"))
        sys.exit(1)

    # Mudanças globais ficam uncommitted no checkout do engine para revisão —
    # sem git não há revisão possível (ex.: instalação de wheel).
    if include_global and not (engine_root() / ".git").exists():
        print(_ui.fail(
            "--global exige um checkout git do engine; "
            f"{engine_root()} não é um repositório"
        ))
        sys.exit(1)

    template = str(getattr(args, "template", None) or "evolve_process")
    available = available_templates("evolve")
    if template not in available:
        choices = ", ".join(available) if available else "nenhum"
        print(_ui.fail(
            f"template '{template}' não pertence ao entrypoint evolve. "
            f"Templates disponíveis: {choices}"
        ))
        sys.exit(1)

    try:
        workspace = _evolve.prepare_workspace(
            root,
            template_dir=engine_root() / "templates" / template,
            targets=targets,
            directive=getattr(args, "directive", None),
            cycle=getattr(args, "cycle", None),
        )
    except (_evolve.EvolveError, ValueError) as exc:
        print(_ui.fail(str(exc)))
        sys.exit(1)

    print(_ui.header("ft evolve — evolução de processo"))
    print(f"  Workspace: {workspace.root}")
    print(f"  Contexto:  {workspace.context_label}")
    print(f"  Alvos:     {', '.join(targets.labels)}")
    print(_ui.dim("  O ciclo atual não é afetado — nenhum step avança."))

    manifest_engine, manifest_model, manifest_effort = manifest_llm_defaults(root)
    runner = StepRunner(
        process_path=workspace.process_file,
        state_path=workspace.state_file,
        project_root=workspace.root,
        llm_engine=resolve_llm_engine(args) or manifest_engine,
        llm_model=resolve_llm_model(args) or manifest_model,
        llm_effort=resolve_llm_effort(args) or manifest_effort,
        verbose=getattr(args, "verbose", False),
    )
    runner.init_state()
    runner.run(mode="mvp")

    state = runner.state_mgr.load()
    if not _cycle_complete(state):
        print(_ui.fail(
            f"Evolução não concluiu ({state.current_node} — {state.node_status})."
        ))
        print(_ui.info(f"Workspace preservado para inspeção: {workspace.root}"))
        sys.exit(1)

    errors = _evolve.validate_staged(workspace)
    if errors:
        print(_ui.fail("Staging inválido — nada foi aplicado:"))
        for error in errors:
            print(f"  ✗ {error}")
        print(_ui.info(f"Workspace preservado para inspeção: {workspace.root}"))
        sys.exit(1)

    report = workspace.report_dir / "evolution-report.md"
    changes = _evolve.diff_staged(workspace)
    if not changes:
        print(_ui.warn("O playbook não alterou nenhum arquivo de processo."))
        if report.is_file():
            print(_ui.info(f"Relatório: {report}"))
        return

    print()
    print(_ui.header(f"Mudanças staged ({len(changes)})"))
    for change in changes:
        print(f"  {change.status:8s} {change.target}: {change.relative}")
    expected_changes = _evolve.change_fingerprint(changes)

    if getattr(args, "dry_run", False):
        print(_ui.info("--dry-run: nada foi aplicado."))
        print(_ui.info(f"Staging preservado: {workspace.targets_dir}"))
        if report.is_file():
            print(_ui.info(f"Relatório: {report}"))
        return

    if not getattr(args, "yes", False):
        try:
            answer = input("Aplicar aos alvos reais? [s/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt, OSError):
            answer = ""
        if answer not in {"s", "sim", "y", "yes"}:
            print(_ui.warn(
                "Não aplicado. Workspace preservado — rode novamente com --yes "
                "para aplicar sem prompt."
            ))
            return

    if include_project:
        from ft.engine.layout import (
            _assert_no_exclusive_startup,
            _manifest_write_lock,
        )

        with _manifest_write_lock(root):
            _assert_no_exclusive_startup(root)
            final_errors = _evolve.validate_staged(workspace)
            final_changes = _evolve.diff_staged(workspace)
            if final_errors or (
                _evolve.change_fingerprint(final_changes) != expected_changes
            ):
                print(_ui.fail(
                    "Alvos ou staging mudaram durante a revisão; nada foi "
                    "aplicado. Revise o diff novamente."
                ))
                sys.exit(1)
            applied = _evolve.apply_staged(workspace, final_changes)
    else:
        applied = _evolve.apply_staged(workspace, changes)
    print(_ui.success(f"{len(applied)} arquivo(s) aplicado(s):"))
    for line in applied:
        print(f"  {line}")
    if include_project:
        print(_ui.info(f"Revise no projeto: git -C {root} diff .ft/process"))
    if include_global:
        print(_ui.info(f"Revise no engine: git -C {engine_root()} diff templates"))
    if report.is_file():
        print(_ui.info(f"Relatório: {report}"))


def _prompt_merge_strategy(work: Path) -> tuple[str, list[str] | None]:
    """Prompt interativo para escolher estratégia de merge no ft close."""
    from ft.engine import ui as _ui

    # Listar pastas disponíveis no worktree
    available = sorted(
        p.name + ("/" if p.is_dir() else "")
        for p in work.iterdir()
        if not p.name.startswith(".") and p.name != "state"
    )

    print()
    print(_ui.header("Como deseja fazer o merge?"))
    print()
    print("  [1] Full      — merge completo (código + docs + histórico FT)")
    print("  [2] Docs only — apenas docs/ e .ft/")
    print("  [3] Selective — escolher pastas específicas")
    print("  [4] None      — não mergear nada (descartar tudo)")
    print()

    choice = input("Escolha [1/2/3/4] (default: 1): ").strip() or "1"

    if choice == "1":
        return "full", None
    elif choice == "2":
        return "docs", None
    elif choice == "3":
        print()
        print(f"  Pastas disponíveis: {' '.join(available)}")
        print()
        raw = input("Quais paths mergear? (separados por espaço): ").strip()
        if not raw:
            print(_ui.warn("Nenhum path informado — cancelando merge"))
            return "none", None
        paths = raw.split()
        return "selective", paths
    elif choice == "4":
        return "none", None
    else:
        print(_ui.warn(f"Opção inválida: {choice} — usando full"))
        return "full", None


def cmd_process_candidates(args):
    """List or resolve global process candidates produced by the current cycle."""
    from ft.engine import ui as _ui

    project_root = find_project_root()
    try:
        runner = get_runner(
            verbose=getattr(args, "verbose", False),
            cycle=getattr(args, "cycle", None),
        )
        root = Path(runner.project_root)
    except (FileNotFoundError, RuntimeError, ValueError):
        # Sem ciclo/runtime ativo, ainda é útil listar a revisão já arquivada
        # no checkout principal.
        root = project_root
    current_review = root / "docs" / "process-improvements.yml"
    candidate_id = getattr(args, "candidate_id", None)
    status = getattr(args, "status", None)

    if bool(candidate_id) != bool(status):
        print(_ui.fail("Informe candidate_id e --status juntos para resolver um candidato."))
        return

    if candidate_id:
        if not current_review.is_file():
            print(_ui.fail("docs/process-improvements.yml não existe no ciclo aberto."))
            print(_ui.info("Ciclos arquivados são imutáveis; resolva candidatos antes de ft close."))
            return
        try:
            review = resolve_global_process_candidate(
                root,
                candidate_id,
                status=status,
                reason=getattr(args, "reason", "") or "",
                reference=getattr(args, "reference", "") or "",
            )
        except ProcessImprovementError as exc:
            print(_ui.fail(f"Não foi possível resolver {candidate_id}: {exc}"))
            return
        resolved = next(
            item for item in review.global_candidates if item.improvement_id == candidate_id
        )
        print(_ui.success(f"{candidate_id}: {resolved.status}"))
        print(_ui.dim(f"  {resolved.reason}"))
        if resolved.reference:
            print(_ui.dim(f"  referência: {resolved.reference}"))
        return

    try:
        if current_review.is_file():
            review = load_process_improvement_review(root)
            source = current_review
        else:
            archived_root = project_root if paths.is_worktree_path(root) else root
            archived = latest_cycle_artifact(archived_root, "process-improvements.yml")
            if archived is None:
                print(_ui.info("Nenhuma revisão estruturada de processo encontrada."))
                return
            review = load_process_improvement_review(
                archived.parent,
                path=archived.name,
                report_path="process-improvements.md",
            )
            source = archived
    except ProcessImprovementError as exc:
        print(_ui.fail(f"Revisão de processo inválida: {exc}"))
        return

    print(_ui.header("Candidatos de Processo"))
    print(_ui.dim(f"  fonte: {source}"))
    if not review.global_candidates:
        print(_ui.info("Nenhum candidato global nesta revisão."))
        return
    for item in review.global_candidates:
        marker = "!" if item.status == "pending" else "✓"
        print(f"  {marker} {item.improvement_id} [{item.target}] {item.status} — {item.title}")
        if item.reason:
            print(_ui.dim(f"      {item.reason}"))
        if item.reference:
            print(_ui.dim(f"      referência: {item.reference}"))


_DRIFT_STATE_LABELS = {
    "in_sync": "em sincronia",
    "fast_forward": "fast-forward disponível",
    "local_fork": "fork local (global não mudou)",
    "diverged": "divergente (fork local + global evoluiu)",
    "diverged_no_base": "divergente sem ancestral (merge 3-way indisponível)",
    "template_missing": "template global ausente",
    "broken": "registro quebrado",
}


def _drift_scan(root: Path, process_name: str | None = None):
    from ft.engine import process_update as pu

    return pu.scan_processes(
        root, engine_root() / "templates", process_name=process_name
    )


def _warn_process_drift(root: Path, process_name: str) -> None:
    """Aviso não-bloqueante quando o template global do processo evoluiu.

    Preflight informativo: nunca escreve nada e nunca levanta exceção — um
    drift jamais deve impedir um ciclo de começar.
    """
    from ft.engine import ui as _ui

    try:
        from ft.engine import process_update as pu

        for state in _drift_scan(root, process_name):
            if state.state in pu.ACTIONABLE_STATES:
                print(_ui.info(
                    f"template global '{state.template_id}' evoluiu desde a "
                    f"materialização ({_DRIFT_STATE_LABELS[state.state]}). "
                    f"Sincronize com: ft process update {state.name}"
                ))
    except Exception:
        pass


def _validate_staged_process(staged_dir: Path) -> tuple[bool, str]:
    """Valida o grafo do bundle em staging antes de qualquer apply."""
    from ft.engine import process_update as pu
    from ft.engine.graph import load_graph
    from ft.engine.process_validator import format_report, validate_process
    from ft.engine.runner import VALIDATOR_REGISTRY

    process_file = pu.template_process_file(staged_dir)
    if process_file is None:
        return False, "staging não contém process.yml"
    try:
        graph = load_graph(process_file)
    except (ValueError, FileNotFoundError) as exc:
        return False, f"YAML inválido: {exc}"
    report = validate_process(graph, VALIDATOR_REGISTRY)
    if not report.passed:
        return False, format_report(report, len(graph.nodes))
    return True, ""


def _print_staged_diff(local_dir: Path, staging_dir: Path, changed: list[str]) -> None:
    """Mostra o diff arquivo a arquivo entre o fork local e o staging."""
    import subprocess as _sp

    for entry in changed:
        action, _, relative = entry.partition(": ")
        if action != "atualizado":
            print(f"    {entry}")
            continue
        print(f"    {entry}")
        sys.stdout.flush()
        _sp.run(
            [
                "git", "diff", "--no-index", "--color",
                str(local_dir / relative), str(staging_dir / relative),
            ],
            check=False,
        )


def _confirm(prompt: str) -> bool:
    try:
        return input(f"{prompt} [s/N]: ").strip().lower() in {"s", "sim", "y", "yes"}
    except (EOFError, KeyboardInterrupt, OSError):
        return False


def _process_update_runtime_guard(
    root: Path,
    target_names: set[str],
) -> list[str]:
    """Bloqueia somente runtimes capazes de observar um processo alvo.

    Worktrees isoladas com ``process_path`` canônico provam disjunção. O modo
    continuous compartilha o checkout; states legados/ambíguos não provam
    identidade. Batches reservam seu próprio template, inclusive para workers
    ainda não criados, mas não impedem sincronizar outro bundle.

    Retorna descrições dos runtimes conhecidos e disjuntos, úteis para deixar
    explícito por que o update foi permitido.
    """
    if not target_names:
        return []

    blockers: list[str] = []
    disjoint: list[str] = []
    for active in _active_run_records(root):
        if not active.isolated:
            blockers.append(
                f"{active.description}: runtime continuous compartilha o checkout"
            )
        elif active.process_name is None:
            blockers.append(
                f"{active.description}: ciclo ativo sem process_path canônico"
            )
        elif active.process_name in target_names:
            blockers.append(
                f"{active.description}: ciclo ativo usa o processo "
                f"'{active.process_name}'"
            )
        else:
            disjoint.append(
                f"{active.description} usa '{active.process_name}'"
            )

    if blockers:
        details = "\n".join(f"  - {item}" for item in blockers)
        targets = ", ".join(sorted(target_names))
        raise RuntimeError(
            "ft process update bloqueado por ciclo ativo com possível "
            f"sobreposição aos processos [{targets}]:\n{details}\n"
            "Encerre/ pause o runtime concorrente ou atualize somente um "
            "processo comprovadamente disjunto."
        )
    return disjoint


def _apply_staged_process_update(root: Path, expected, staging: Path) -> Path:
    """CAS + guard final + apply sob o lock compartilhado do projeto."""
    from ft.engine import process_update as pu
    from ft.engine.layout import (
        _assert_no_exclusive_startup,
        _manifest_write_lock,
    )

    with _manifest_write_lock(root):
        _assert_no_exclusive_startup(root)
        current_states = _drift_scan(root, expected.name)
        if len(current_states) != 1:
            raise RuntimeError(
                f"processo '{expected.name}' mudou durante o update; rode novamente"
            )
        current = current_states[0]
        before = (
            expected.state,
            expected.local_digest,
            expected.global_digest,
            expected.base_digest,
        )
        now = (
            current.state,
            current.local_digest,
            current.global_digest,
            current.base_digest,
        )
        if now != before:
            raise RuntimeError(
                f"processo '{expected.name}' mudou durante o update; "
                "nenhuma substituição foi aplicada — rode novamente"
            )
        _process_update_runtime_guard(root, {expected.name})
        return pu.apply_update(root, current, staging)


def cmd_process_update(args):
    """Sincroniza processos locais com os templates globais do engine."""
    import shutil

    from ft.engine import process_update as pu
    from ft.engine import ui as _ui
    from ft.engine.layout import (
        _assert_no_exclusive_startup,
        _manifest_write_lock,
    )

    root = find_project_root().resolve()
    if not paths.project_manifest(root).is_file():
        raise ValueError(
            "ft process update exige um projeto inicializado (.ft/manifest.yml)"
        )
    if paths.is_worktree_path(root):
        raise RuntimeError(
            "rode ft process update no checkout principal, não na worktree de um ciclo"
        )

    name = getattr(args, "name", None)
    # Mesmo --check é read-only, mas precisa de um snapshot coerente enquanto
    # outro apply pode estar trocando o diretório do bundle.
    with _manifest_write_lock(root):
        states = _drift_scan(root, name)
    if not states:
        if name:
            print(_ui.fail(f"processo '{name}' não está registrado no manifest"))
            sys.exit(1)
        print(_ui.info("nenhum processo local registrado no manifest"))
        return

    print(_ui.header("Processos Locais × Templates Globais"))
    for state in states:
        marker = "✓" if state.state in {"in_sync", "local_fork"} else "!"
        label = _DRIFT_STATE_LABELS.get(state.state, state.state)
        print(f"  {marker} {state.name:<16} {label}")
        if state.detail:
            print(_ui.dim(f"      {state.detail}"))

    actionable = [s for s in states if s.state in pu.ACTIONABLE_STATES]
    if getattr(args, "check", False):
        sys.exit(1 if actionable else 0)
    if not actionable:
        print(_ui.success("nada a atualizar"))
        return

    fast_forwards = [s for s in actionable if s.state == "fast_forward"]
    diverged = [s for s in actionable if s.state == "diverged"]
    orphaned = [s for s in actionable if s.state == "diverged_no_base"]
    mutating_names = {state.name for state in (*fast_forwards, *diverged)}
    disjoint_runtimes = _process_update_runtime_guard(root, mutating_names)
    if disjoint_runtimes:
        print(_ui.info(
            "runtime(s) ativo(s) em processos disjuntos; update seletivo permitido"
        ))
        for runtime in disjoint_runtimes:
            print(_ui.dim(f"    {runtime}"))
    pending = 0

    for state in orphaned:
        pending += 1
        print(_ui.fail(
            f"{state.name}: local e global divergem e o ancestral se perdeu "
            "(materializado antes do snapshot base). Porte o diff manualmente "
            "ou remova o fork e rematerialize."
        ))

    if fast_forwards:
        print()
        print(_ui.info(
            f"{len(fast_forwards)} fast-forward(s) seguro(s): "
            + ", ".join(s.name for s in fast_forwards)
        ))
        if getattr(args, "yes", False) or _confirm("Aplicar fast-forward(s)?"):
            for state in fast_forwards:
                with _manifest_write_lock(root):
                    _assert_no_exclusive_startup(root)
                    staging, changed = pu.prepare_fast_forward(root, state)
                    ok, why = _validate_staged_process(staging)
                    if not ok:
                        pending += 1
                        print(_ui.fail(
                            f"{state.name}: template global inválido — {why}"
                        ))
                        shutil.rmtree(staging, ignore_errors=True)
                        continue
                    backup = _apply_staged_process_update(root, state, staging)
                print(_ui.success(f"{state.name}: atualizado ({len(changed)} arquivo(s))"))
                for entry in changed:
                    print(_ui.dim(f"    {entry}"))
                print(_ui.dim(f"    backup do fork anterior: {backup.relative_to(root)}"))
        else:
            pending += len(fast_forwards)
            print(_ui.info("fast-forwards não aplicados"))

    for state in diverged:
        print()
        print(_ui.header(f"Merge 3-way: {state.name}"))
        # O staging precisa sobreviver fora do lock enquanto o usuário lê o
        # diff e decide. Um nome por invocação evita que dois prompts
        # concorrentes sobrescrevam o candidato um do outro.
        from uuid import uuid4

        staging_root = pu.staging_dir_for(root, state.name)
        staging = staging_root.parent / (
            f"{state.name}-{os.getpid()}-{uuid4().hex}"
        )
        with _manifest_write_lock(root):
            _assert_no_exclusive_startup(root)
            pu.ensure_base_snapshot(state)
            result = pu.build_merge_staging(state, staging)

            ok, why = (
                _validate_staged_process(staging)
                if result.clean
                else (False, "")
            )

        # Nunca mantenha o lock compartilhado durante output volumoso ou
        # input humano: states e batches de processos disjuntos precisam
        # continuar progredindo enquanto este update aguarda confirmação.
        if not result.clean:
            pending += 1
            print(_ui.fail(
                f"{state.name}: {len(result.conflicts)} conflito(s) — "
                + ", ".join(result.conflicts)
            ))
            print(_ui.info(
                "staging preservado com marcadores diff3 em "
                f"{staging.relative_to(root)} — resolva manualmente e copie "
                "para o fork, ou descarte o diretório"
            ))
            continue

        if not ok:
            pending += 1
            print(_ui.fail(
                f"{state.name}: merge textualmente limpo, mas inválido — {why}"
            ))
            shutil.rmtree(staging, ignore_errors=True)
            continue

        print(_ui.info(f"merge limpo ({len(result.changed)} mudança(s)):"))
        _print_staged_diff(state.local_dir, staging, result.changed)
        if _confirm(f"Aplicar update em '{state.name}'?"):
            backup = _apply_staged_process_update(root, state, staging)
            print(_ui.success(f"{state.name}: atualizado"))
            print(_ui.dim(
                f"    backup do fork anterior: {backup.relative_to(root)}"
            ))
        else:
            pending += 1
            shutil.rmtree(staging, ignore_errors=True)
            print(_ui.info(f"{state.name}: mantido como está"))

    if pending:
        sys.exit(1)


def _cmd_close_locked_body(args, merge_lock):
    """Encerra o ciclo ativo: merge interativo + remove worktree + limpa branch."""
    import subprocess as _sp
    from ft.engine import ui as _ui

    runner = get_runner(llm_engine=resolve_llm_engine(args), llm_model=resolve_llm_model(args), llm_effort=resolve_llm_effort(args), verbose=getattr(args, "verbose", False), cycle=getattr(args, "cycle", None))
    if not _ensure_runtime_selected(args, runner):
        return
    state = runner.state_mgr.load()
    graph = getattr(runner, "graph", None)
    graph_meta = getattr(graph, "meta", {}) if graph is not None else {}
    if not isinstance(graph_meta, dict):
        graph_meta = {}
    close_policy = graph_meta.get("close_policy", {})
    if not isinstance(close_policy, dict):
        close_policy = {}

    # Verificar se o ciclo terminou
    terminal = {"done", "completed"}
    if state.node_status not in terminal and not getattr(args, "force", False):
        print(_ui.fail(f"Ciclo ainda ativo: {state.current_node} ({state.node_status})"))
        print(_ui.warn("Use --force para encerrar mesmo assim, ou ft approve/continue para finalizar"))
        return

    # 1. Determinar estratégia de merge
    merge_strategy = getattr(args, "merge", None)
    merge_paths = None
    declared_merge = close_policy.get("merge")
    if declared_merge:
        declared_merge = str(declared_merge)
        if declared_merge not in {"full", "docs", "selective", "none"}:
            print(_ui.fail(f"close_policy.merge inválido: {declared_merge}"))
            return
        if merge_strategy and merge_strategy != declared_merge and not getattr(args, "force", False):
            print(_ui.fail(
                f"Este processo exige merge '{declared_merge}', recebido '{merge_strategy}'."
            ))
            print(_ui.info("Use a estratégia declarada ou --force para sobrescrever conscientemente."))
            return
        if merge_strategy is None:
            merge_strategy = declared_merge

    if merge_strategy == "selective":
        raw_paths = getattr(args, "merge_paths", None)
        if raw_paths:
            merge_paths = raw_paths.split()
        else:
            merge_strategy = None  # Forçar prompt

    work = Path(runner.project_root)
    if not getattr(args, "force", False):
        backlog_file = work / "docs" / "PROJECT_BACKLOG.md"
        backlog_policy = close_policy.get("backlog", {})
        if not isinstance(backlog_policy, dict):
            backlog_policy = {}
        backlog_mode = backlog_policy.get("mode", "global")
        if not isinstance(backlog_mode, str) or backlog_mode not in {
            "global",
            "referenced",
            "none",
        }:
            print(_ui.fail("Backlog do produto não está pronto para fechar este ciclo."))
            print(_ui.warn(
                f"close_policy.backlog.mode desconhecido: {backlog_mode}"
            ))
            return
        if backlog_mode != "none" and (
            backlog_mode == "referenced" or backlog_file.exists()
        ):
            if backlog_mode == "referenced":
                references_path = backlog_policy.get("references_path")
                if not references_path:
                    backlog_ok, backlog_detail = False, (
                        "close_policy.backlog.references_path é obrigatório no modo referenced"
                    )
                else:
                    backlog_ok, backlog_detail = backlog_referenced_decisions(
                        references_path=str(references_path),
                        backlog_path=str(
                            backlog_policy.get("backlog_path", "docs/PROJECT_BACKLOG.md")
                        ),
                        reference_field=(
                            str(backlog_policy["reference_field"])
                            if backlog_policy.get("reference_field")
                            else None
                        ),
                        required_count=(
                            int(backlog_policy["required_count"])
                            if backlog_policy.get("required_count") is not None
                            else None
                        ),
                        accepted_statuses=backlog_policy.get("accepted_statuses"),
                        project_root=str(work),
                    )
            elif backlog_mode == "global":
                backlog_ok, backlog_detail = backlog_pending_decisions(
                    project_root=str(work)
                )
            if not backlog_ok:
                print(_ui.fail("Backlog do produto não está pronto para fechar este ciclo."))
                print(_ui.warn(backlog_detail))
                print(_ui.info("Atualize docs/PROJECT_BACKLOG.md ou use ft close --force para encerrar conscientemente."))
                return

        artifact_policy = graph_meta.get("artifact_policy", {}) if isinstance(graph_meta, dict) else {}
        canonical = artifact_policy.get("canonical", []) if isinstance(artifact_policy, dict) else []
        requires_features = "docs/FEATURES.md" in {str(item) for item in canonical}
        if requires_features:
            catalog_ok, catalog_detail = features_catalog_valid(project_root=str(work))
            coverage_ok, coverage_detail = implemented_backlog_covered_by_features(
                project_root=str(work)
            )
            if not catalog_ok or not coverage_ok:
                print(_ui.fail("Catálogo de features está ausente ou inconsistente com o backlog entregue."))
                if not catalog_ok:
                    print(_ui.warn(catalog_detail))
                if not coverage_ok:
                    print(_ui.warn(coverage_detail))
                print(_ui.info(
                    "Atualize docs/FEATURES.md ou use ft close --force para encerrar conscientemente."
                ))
                return

        process_ok, process_detail = process_improvement_close_readiness(work)
        if not process_ok:
            print(_ui.fail("Há candidatos de melhoria global sem disposição explícita."))
            print(_ui.warn(process_detail))
            print(_ui.info("Liste com: ft process-candidates"))
            print(_ui.info(
                "Depois de revisar o global, resolva com: "
                "ft process-candidates PI-NNN --status promoted|deferred|rejected "
                "--reason \"...\" [--reference \"commit/path\"]"
            ))
            print(_ui.info("Use ft close --force apenas para ignorar conscientemente esta governança."))
            return

    merge_ok = True
    if merge_strategy:
        # Via CLI flags (não-interativo)
        with merge_lock.suspend():
            merge_ok = runner.merge_on_close(merge_strategy, merge_paths)
    else:
        # Prompt interativo
        wt = runner._detect_worktree()
        if wt:
            strategy, paths = _prompt_merge_strategy(work)
            with merge_lock.suspend():
                merge_ok = runner.merge_on_close(strategy, paths)
        # Se não é worktree, nada a mergear

    if merge_ok is False:
        # NUNCA destruir worktree/branch com merge falho — os commits do ciclo
        # só existem lá. (Lição vibeos cycle-02: close removeu branch com
        # conflitos abertos; recuperação exigiu resgate via SHA solto.)
        print(_ui.fail("Merge falhou — worktree e branch PRESERVADOS."))
        print(_ui.warn("Resolva o merge (ou use --merge none) e rode ft close novamente."))
        return

    # 2. Descobrir se estamos num worktree
    git_file = work / ".git"
    is_worktree = git_file.exists() and git_file.is_file()

    if is_worktree and not getattr(args, "keep_worktree", False):
        gitdir_line = git_file.read_text().strip()
        if gitdir_line.startswith("gitdir:"):
            gitdir = Path(gitdir_line.split(":", 1)[1].strip())
            original_root = gitdir.parent.parent.parent

            branch = _sp.run(
                ["git", "branch", "--show-current"],
                cwd=work, capture_output=True, text=True,
            ).stdout.strip()

            # Remover worktree
            result = _sp.run(
                ["git", "worktree", "remove", str(work), "--force"],
                cwd=original_root, capture_output=True, text=True,
            )
            if result.returncode == 0:
                print(_ui.success(f"Worktree removido: {work.name}"))
            else:
                print(_ui.warn(f"Worktree não removido: {result.stderr.strip()[:200]}"))

            # Remover branch
            if branch:
                result = _sp.run(
                    ["git", "branch", "-D", branch],
                    cwd=original_root, capture_output=True, text=True,
                )
                if result.returncode == 0:
                    print(_ui.success(f"Branch removida: {branch}"))
                else:
                    print(_ui.dim(f"Branch {branch} não removida: {result.stderr.strip()[:100]}"))
    elif is_worktree:
        print(_ui.dim("Worktree preservado (--keep-worktree)"))

    print(_ui.success("Ciclo encerrado."))
    print(_ui.warn("Verificação pós-close antes de entregar ao stakeholder:"))
    print("    1. Reinstale dependências (npm install / poetry install) — o ciclo pode ter adicionado novas")
    print("    2. Limpe caches de build antigos (ex.: .next/) e reinicie os servidores do checkout promovido")
    print("    3. Confirme HTTP 200 em TODAS as rotas principais (não só / e /health)")
    print("    4. Exercite o fluxo novo do ciclo de verdade — 'passou no worktree' não implica 'funciona no projeto'")


def cmd_close(args):
    """Serialize merge/removal while independent runs keep executing."""
    from ft.runs import close_merge_lock

    owner = canonical_project_root(find_project_root())
    with close_merge_lock(owner) as merge_lock:
        _cmd_close_locked_body(args, merge_lock)


def cmd_graph(args):
    if not _ensure_runtime_selected(args):
        return
    runner = get_runner(llm_engine=resolve_llm_engine(args), llm_model=resolve_llm_model(args), llm_effort=resolve_llm_effort(args), verbose=getattr(args, "verbose", False), cycle=getattr(args, "cycle", None))
    runner.status(full=True)


def _validate_project_structure(root: Path) -> tuple[list[str], list[str]]:
    """Valida a estrutura comum do workspace e o catálogo local.
    Retorna (errors, warnings)."""
    errors = []
    warnings = []

    manifest_path = paths.project_manifest(root)
    if not manifest_path.is_file():
        errors.append("arquivo '.ft/manifest.yml' ausente")
    else:
        try:
            manifest = read_manifest(root)
            processes = manifest.get("processes", {})
            if isinstance(processes, dict):
                for process_name in processes:
                    if resolve_project_process(root, str(process_name)) is None:
                        errors.append(
                            f"processo '{process_name}' ausente ou fora do path canônico"
                        )
        except ValueError as exc:
            errors.append(str(exc))

    return errors, warnings


def cmd_validate(args):
    """Valida o YAML do processo."""
    from ft.engine.graph import load_graph
    from ft.engine.process_validator import validate_process, format_report
    from ft.engine.runner import VALIDATOR_REGISTRY

    root = find_project_root()

    # --- Validação de estrutura do projeto ---
    print("\nValidando estrutura do projeto...\n")
    struct_errors, struct_warnings = _validate_project_structure(root)
    structure_passed = len(struct_errors) == 0
    if structure_passed:
        print("  \u2705 Estrutura: manifesto V3 e catálogo local válidos")
    else:
        for e in struct_errors:
            print(f"  \u274c {e}")
    for w in struct_warnings:
        print(f"  \u26a0\ufe0f  {w}")
    warn_note = f" ({len(struct_warnings)} warnings)" if struct_warnings else ""
    err_note = f" ({len(struct_errors)} erros)" if struct_errors else ""
    print(f"\n  Estrutura: {'PASS' if structure_passed else 'FAIL'}{err_note}{warn_note}")

    # --- Validação do YAML ---
    print()
    process_path = resolve_project_process(root, str(args.template))
    if not process_path:
        print(
            f"ERRO: template local não materializado: {args.template}. "
            f"Use `ft run . --template {args.template}` para materializá-lo."
        )
        sys.exit(1)

    rel = process_path.relative_to(root) if process_path.is_relative_to(root) else process_path
    print(f"Validando {rel}...\n")

    try:
        graph = load_graph(process_path)
    except (ValueError, FileNotFoundError) as e:
        print(f"  \u274c Erro ao carregar YAML: {e}")
        sys.exit(1)

    report = validate_process(graph, VALIDATOR_REGISTRY)
    total = len(graph.nodes)
    print(format_report(report, total))

    overall_pass = structure_passed and report.passed
    sys.exit(0 if overall_pass else 1)


def cmd_lint_process(args):
    """Lint semântico — usa LLM para detectar especificidades de projeto no YAML."""
    import json as _json

    root = find_project_root()

    process_path = resolve_project_process(root, str(args.template))
    if not process_path:
        print(
            f"ERRO: template local não materializado: {args.template}. "
            f"Use `ft run . --template {args.template}` para materializá-lo."
        )
        sys.exit(1)

    yaml_content = process_path.read_text()
    rel_path = process_path.relative_to(root) if process_path.is_relative_to(root) else process_path

    print(f"\nLint semântico: {rel_path}\n")

    prompt = (
        "Você é um validador de processos YAML do Fast Track.\n\n"
        "REGRA FUNDAMENTAL: O YAML de processo é pura orquestração. Ele define sequência "
        "de passos, executor, e validators. Ele NÃO deve conter especificidades de projeto.\n\n"
        "VIOLAÇÕES (error) — reporte se encontrar nos prompts ou títulos:\n"
        "- Nomes de produto/projeto (ex: 'ft-studio', 'Pokemon', 'YouNews', qualquer nome próprio)\n"
        "- Specs de design hardcoded (ex: 'Activity Bar 40px', '#0a0a1a', 'fts-*', '180x60px', cores hex)\n"
        "- Tech stack hardcoded (ex: 'Svelte + Vite', 'React', 'js-yaml', 'Flask', nomes de frameworks/libs)\n"
        "- Checklist de validação específica (em vez de 'leia ui_guidelines.md e valide')\n"
        "- Estrutura de projeto específica detalhada (ex: lista de componentes, nomes de arquivos do projeto)\n\n"
        "WARNINGS — reporte como warning:\n"
        "- Nomes de screenshots muito específicos do projeto (ex: 'graph.png', 'drawer-open.png')\n\n"
        "ACEITO — NÃO reporte:\n"
        "- Caminhos genéricos de artefatos (docs/PRD.md, docs/ui_guidelines.md, docs/tech_stack.md)\n"
        "- Validators genéricos (file_exists, has_sections, command_succeeds)\n"
        "- Estrutura de pastas genérica (frontend/src/, docs/screenshots/, frontend/dist/)\n"
        "- IDs de nodes, títulos descritivos genéricos, nomes de sprints\n"
        "- Comandos de build genéricos (npm run build, npm install, npx serve)\n"
        "- Referências a ferramentas genéricas (Playwright, curl)\n"
        "- Instruções genéricas ('Leia docs/ui_guidelines.md e siga')\n\n"
        "YAML DO PROCESSO:\n"
        "---\n"
        f"{yaml_content}\n"
        "---\n\n"
        "Responda APENAS com JSON (sem markdown, sem ```), no formato:\n"
        '{"violations": [\n'
        '  {"level": "error"|"warning", "node_id": "...", "excerpt": "trecho curto", '
        '"reason": "motivo", "suggestion": "como corrigir"}\n'
        '], "verdict": "PASS"|"FAIL"}\n\n'
        "Se não houver violações: {\"violations\": [], \"verdict\": \"PASS\"}\n"
        "verdict=FAIL se houver pelo menos 1 error. Warnings sozinhos = PASS."
    )

    from ft.engine.delegate import delegate_to_llm

    manifest_engine, manifest_model, manifest_effort = manifest_llm_defaults(root)
    engine = resolve_llm_engine(args) or manifest_engine or "claude"
    model = resolve_llm_model(args) or manifest_model
    effort = resolve_llm_effort(args) or manifest_effort
    result = delegate_to_llm(
        task=prompt,
        project_root=str(root),
        allowed_paths=[],
        max_turns=5,
        llm_engine=engine,
        llm_model=model,
        llm_effort=effort,
    )

    output = result.output.strip()
    start = output.find("{")
    end = output.rfind("}") + 1

    if start < 0 or end <= start:
        print(f"  Erro ao parsear resposta do LLM:\n{output[:500]}")
        sys.exit(1)

    try:
        data = _json.loads(output[start:end])
    except _json.JSONDecodeError:
        print(f"  JSON inválido na resposta do LLM:\n{output[start:end][:500]}")
        sys.exit(1)

    violations = data.get("violations", [])

    if not violations:
        print("  \u2705 Nenhuma especificidade de projeto detectada")
        print("\n  Resultado: PASS")
        sys.exit(0)

    errors = [v for v in violations if v.get("level") == "error"]
    warnings = [v for v in violations if v.get("level") == "warning"]

    for v in violations:
        icon = "\u274c" if v.get("level") == "error" else "\u26a0\ufe0f "
        node = v.get("node_id", "?")
        excerpt = v.get("excerpt", "")
        reason = v.get("reason", "")
        suggestion = v.get("suggestion", "")
        print(f"  {icon} {node}: \"{excerpt}\"")
        print(f"     \u2192 {reason}")
        if suggestion:
            print(f"     Sugestão: {suggestion}")
        print()

    has_errors = len(errors) > 0
    status = "FAIL" if has_errors else "PASS"
    parts = []
    if errors:
        parts.append(f"{len(errors)} erro(s)")
    if warnings:
        parts.append(f"{len(warnings)} warning(s)")
    print(f"  Resultado: {status} ({', '.join(parts)})")

    sys.exit(1 if has_errors else 0)


def cmd_retry(args):
    """Reseta o estado blocked do node atual e retenta sem aplicar correção."""
    from ft.engine import ui as _ui

    runner = get_runner(llm_engine=resolve_llm_engine(args),
                        llm_model=resolve_llm_model(args),
                        llm_effort=resolve_llm_effort(args),
                        verbose=getattr(args, "verbose", False),
                        cycle=getattr(args, "cycle", None))
    if not _ensure_runtime_selected(args, runner):
        return
    runner._bypass_human_gates = resolve_bypass_human_gates(args)

    state = runner.state_mgr.load()
    if state.node_status != "blocked":
        from ft.engine.state import lock_owner_is_alive

        orphaned_delegation = (
            state.node_status == "delegated"
            and not lock_owner_is_alive(state._lock)
        )
        if orphaned_delegation:
            print(_ui.warn("Delegação órfã detectada — limpando estado antes do retry"))
            state.active_llm_log = None
        else:
            print(_ui.warn(f"Node atual não está bloqueado (status: {state.node_status})"))
            return

    node_id = state.current_node
    print(_ui.info(f"Retentando node: {node_id}"))

    # Limpar estado bloqueado e reset do contador de auto-fix
    state.node_status = "ready"
    state.blocked_reason = None
    runner.state_mgr.save()
    runner._auto_fix_counts.pop(node_id, None)

    mode = "mvp" if getattr(args, "auto", False) else "step"
    runner.run(mode=mode)


def cmd_fix(args):
    """Injeta instrução de correção e retoma o ciclo (on_fail) ou delega ao LLM (blocked)."""
    from ft.engine import ui as _ui

    instruction = args.instruction
    runner = get_runner(llm_engine=resolve_llm_engine(args),
                        llm_model=resolve_llm_model(args),
                        llm_effort=resolve_llm_effort(args),
                        verbose=getattr(args, "verbose", False),
                        cycle=getattr(args, "cycle", None))
    if not _ensure_runtime_selected(args, runner):
        return

    # Modo 1: pending_fix (on_fail event) — injeta instrução e volta ao goto
    if runner.apply_fix(instruction):
        mode = "mvp" if getattr(args, "auto", False) else "step"
        runner.run(mode=mode)
        return

    # Modo 2: blocked genérico — delega ao LLM para corrigir arquivos
    from ft.engine.delegate import delegate_to_llm
    root = runner.project_root
    state_path = runner.state_mgr.path
    blocked_context = ""
    if state_path.exists():
        state = runner.state_mgr.load()
        if state.blocked_reason:
            blocked_context = (
                f"\n\nCONTEXTO: O processo parou no node '{state.current_node}' com o erro:\n"
                f"{state.blocked_reason}\n"
            )

    prompt = (
        f"O usuário pediu a seguinte correção:\n\n"
        f"{instruction}\n"
        f"{blocked_context}\n"
        f"Analise o problema, faça as alterações necessárias nos arquivos do projeto, "
        f"e diga DONE quando terminar."
    )

    state = runner.state_mgr.load()
    fix_node = None
    if state and state.current_node and state.current_node in runner.graph.nodes:
        fix_node = runner.graph.get_node(state.current_node)
    fix_selection = runner._capture_delegation_llm_selection(
        state,
        node=fix_node,
    )
    fix_engine = fix_selection.engine
    fix_model = fix_selection.model
    fix_effort = fix_selection.effort
    fix_allowed_paths = ["project/", "src/", "tests/", "docs/", "main.py", "app.py", "server.py",
                         "frontend/", ".ft/process/"]
    opencode_capture_output_path = None
    if fix_engine == "opencode" and fix_node is not None:
        outputs = [str(output) for output in getattr(fix_node, "outputs", []) if not str(output).endswith("/")]
        if getattr(fix_node, "type", None) in {"discovery", "document", "retro"} and len(outputs) == 1:
            opencode_capture_output_path = outputs[0]
            fix_allowed_paths = [opencode_capture_output_path]
    elif fix_engine == "opencode":
        inferred_path = _single_fix_target_path(instruction, Path(root))
        if inferred_path:
            opencode_capture_output_path = inferred_path
            fix_allowed_paths = [inferred_path]

    if fix_engine == "opencode":
        repair_note = _try_apply_opencode_arena_board_fix(runner, instruction)
        if repair_note:
            print(_ui.success("Correção aplicada"))
            print(_ui.warn(repair_note))
            return

    if opencode_capture_output_path:
        target = Path(root) / opencode_capture_output_path
        if target.exists() and target.is_file():
            current = target.read_text(encoding="utf-8", errors="ignore")
            prompt += (
                f"\n\nARQUIVO ALVO: {opencode_capture_output_path}\n"
                "CONTEUDO ATUAL ENTRE MARCADORES:\n"
                "<<<FT_CURRENT_FILE>>>\n"
                f"{current.rstrip()}\n"
                "<<<FT_END_CURRENT_FILE>>>\n\n"
                "Retorne o conteudo completo atualizado desse unico arquivo. "
                "Nao retorne diff, explicacao, markdown fence ou DONE."
            )
        if opencode_capture_output_path.startswith("project/tests/e2e/"):
            frontend_source = Path(root) / "project" / "frontend" / "src" / "main.js"
            if frontend_source.exists() and frontend_source.is_file():
                prompt += (
                    "\n\nCONTEXTO DA UI ATUAL (somente leitura): project/frontend/src/main.js\n"
                    "<<<FT_UI_SOURCE>>>\n"
                    f"{frontend_source.read_text(encoding='utf-8', errors='ignore').rstrip()}\n"
                    "<<<FT_END_UI_SOURCE>>>"
                )

    if fix_engine == "opencode" and opencode_capture_output_path == "project/tests/e2e/test_navigation.py":
        try:
            pre_note = _postprocess_opencode_fix_capture(runner, opencode_capture_output_path)
        except Exception:
            pre_note = None
        if pre_note and state and state.node_status != "blocked":
            print(_ui.success("Correção aplicada"))
            print(_ui.warn(pre_note))
            print(_ui.info("Para continuar o processo: ft continue --auto"))
            return

    print(_ui.info(f"Aplicando correção: {instruction}"))
    fix_kwargs = dict(
        task=prompt,
        project_root=str(root),
        allowed_paths=fix_allowed_paths,
        llm_engine=fix_engine,
        llm_model=fix_model,
        llm_effort=fix_effort,
    )
    if opencode_capture_output_path:
        fix_kwargs["opencode_capture_output_path"] = opencode_capture_output_path
    result = delegate_to_llm(**fix_kwargs)

    if result.success:
        postprocess_note = None
        if fix_engine == "opencode" and opencode_capture_output_path:
            try:
                postprocess_note = _postprocess_opencode_fix_capture(runner, opencode_capture_output_path)
            except Exception as exc:
                print(_ui.fail(f"Correção aplicada, mas artefato capturado é inválido: {exc}"))
                return
        print(_ui.success("Correção aplicada"))
        if postprocess_note:
            print(_ui.warn(postprocess_note))
        state = runner.state_mgr.load()
        if state.node_status == "blocked":
            mode = "mvp" if getattr(args, "auto", False) else "step"
            node_id = state.current_node
            node = runner.graph.get_node(node_id) if node_id and node_id in runner.graph.nodes else None
            if node is not None:
                from ft.engine.runner import run_validators

                print(_ui.info("Validando correção..."))
                validation = run_validators(
                    node,
                    runner.project_root,
                    state_dir=str(runner.state_mgr.path.parent),
                    work_dir=runner._run_dir,
                )
                runner._print_validation(validation)
                if validation.passed:
                    for output_path in node.outputs:
                        runner.state_mgr.record_artifact(Path(output_path).stem, output_path)
                    runner._maybe_auto_commit(node)
                    runner._record_node_summary(
                        node,
                        "NODE_SUMMARY:\n"
                        "- fiz: correção via ft fix\n"
                        "- verificado: validators do node passaram\n"
                        f"- instrução: {instruction}",
                    )
                    if node.requires_approval and not runner._auto_approve:
                        fixed_state = runner.state_mgr.load()
                        fixed_state.node_status = "ready"
                        fixed_state.blocked_reason = None
                        runner.state_mgr.save()
                        print(_ui.awaiting_approval(auto=runner._auto_approve))
                        runner.state_mgr.set_pending_approval(node.id)
                        return

                    next_id = runner.graph.resolve_next(node.id)
                    runner._advance_state(node.id, next_id)
                    print(_ui.step_pass(next_id))
                    if getattr(args, "auto", False):
                        runner.run(mode="mvp")
                    return

                print(_ui.warn("Correção aplicada, mas validators ainda falham — reexecutando node."))

            state = runner.state_mgr.load()
            state.node_status = "running"
            state.blocked_reason = None
            state.last_approval_message = instruction
            runner.state_mgr.save()
            print(_ui.info("Estado desbloqueado — continuando..."))
            runner.run(mode=mode)
        else:
            print(_ui.info("Para continuar o processo: ft continue --auto"))
    else:
        print(_ui.fail(f"LLM não conseguiu aplicar: {result.output[:300]}"))


def _cmd_abort_locked_body(args):
    """Aborta o ciclo: descarta worktree e branch sem merge nenhum."""
    import subprocess as _sp
    from ft.engine import ui as _ui

    root = find_project_root()
    cycle = _select_cycle_for_command(root, getattr(args, "cycle", None))
    work = cycle.worktree
    git_file = work / ".git"
    is_git_worktree = git_file.exists() and git_file.is_file()
    if not is_git_worktree:
        raise RuntimeError(f"ciclo {cycle.name} não possui worktree Git válida")

    original_root = None
    branch = ""
    if is_git_worktree:
        gitdir_line = git_file.read_text().strip()
        if not gitdir_line.startswith("gitdir:"):
            print(_ui.fail("Formato .git inválido — não é worktree."))
            return

        gitdir = Path(gitdir_line.split(":", 1)[1].strip())
        original_root = gitdir.parent.parent.parent

        branch = _sp.run(
            ["git", "branch", "--show-current"],
            cwd=work, capture_output=True, text=True,
        ).stdout.strip()

    # Confirmação
    print()
    print(_ui.warn(f"ABORT: vai descartar TUDO do ciclo em {work.name}"))
    print(_ui.dim(f"  Worktree: {work}"))
    if branch:
        print(_ui.dim(f"  Branch:   {branch}"))
    print(_ui.dim("  Nenhum merge será feito — todo código será perdido."))
    print()
    if not getattr(args, "force", False):
        confirm = input("Confirma? [s/N]: ").strip().lower()
        if confirm not in ("s", "sim", "y", "yes"):
            print(_ui.dim("Abortado pelo usuário."))
            return

    # Matar servidores que possam estar rodando
    for pid_file in (".serve_backend.pid", ".serve_frontend.pid", ".serve.pid"):
        pf = work / pid_file
        if pf.exists():
            try:
                pid = int(pf.read_text().strip())
                os.kill(pid, 15)
            except (ValueError, ProcessLookupError, OSError):
                pass

    if is_git_worktree and original_root is not None:
        result = _sp.run(
            ["git", "worktree", "remove", str(work), "--force"],
            cwd=original_root, capture_output=True, text=True,
        )
        if result.returncode == 0:
            print(_ui.success(f"Worktree removido: {work.name}"))
        else:
            print(_ui.fail(f"Erro ao remover worktree: {result.stderr.strip()[:200]}"))
            return
    # Remover branch
    if branch and original_root is not None:
        result = _sp.run(
            ["git", "branch", "-D", branch],
            cwd=original_root, capture_output=True, text=True,
        )
        if result.returncode == 0:
            print(_ui.success(f"Branch removida: {branch}"))

    print(_ui.success("Ciclo abortado. Nenhum merge realizado."))


def cmd_abort(args):
    from ft.runs import close_merge_lock

    owner = canonical_project_root(find_project_root())
    with close_merge_lock(owner):
        _cmd_abort_locked_body(args)


def cmd_cancel(args):
    """Cancela o run ativo com justificativa."""
    from datetime import datetime
    from ft.engine import ui as _ui
    from ft.engine.state import lock_owner_is_alive, mutate_state_payload

    root = find_project_root()
    reason = args.reason

    cycle = _select_cycle_for_command(root, getattr(args, "cycle", None))
    state_path = cycle.state_path

    outcome: dict[str, object] = {}

    class _CannotVerifyCancelOwner(RuntimeError):
        pass

    def cancel(data: dict) -> None:
        current_node = data.get("current_node")
        outcome["current_node"] = current_node
        if current_node is None:
            return

        # O SIGTERM acontece dentro da mesma transação que grava cancelled.
        # Assim o runner alvo não consegue intercalar um save entre os dois.
        lock = data.get("_lock") or {}
        raw_pid = lock.get("pid") if isinstance(lock, dict) else None
        try:
            pid = int(raw_pid) if raw_pid is not None else None
        except (TypeError, ValueError):
            pid = None
        if pid:
            if lock_owner_is_alive(lock, require_identity=True):
                try:
                    os.kill(pid, 15)  # SIGTERM
                    outcome["terminated_pid"] = pid
                except OSError:
                    pass
            elif lock_owner_is_alive(lock):
                raise _CannotVerifyCancelOwner(
                    f"não foi possível validar a identidade do PID {pid}; "
                    "o cancelamento não alterou o state"
                )

        data["node_status"] = "cancelled"
        data["blocked_reason"] = f"CANCELADO: {reason}"
        data["_lock"] = None

    try:
        data = mutate_state_payload(state_path, cancel)
    except _CannotVerifyCancelOwner as exc:
        print(_ui.fail(str(exc)))
        return
    if data is None:
        print(_ui.warn("Nenhum run ativo encontrado."))
        return
    current_node = outcome.get("current_node")
    if current_node is None:
        print(_ui.warn("Processo já finalizado — nada para cancelar."))
        return
    if outcome.get("terminated_pid"):
        print(_ui.info(f"Processo PID {outcome['terminated_pid']} encerrado"))

    completed = data.get("completed_nodes", [])
    total = data.get("metrics", {}).get("steps_total", "?")

    # Gerar relatório de cancelamento (determinístico)
    run_dir = state_path.parent.parent  # <runtime-cycle>/state/ → <runtime-cycle>/
    cancel_report = run_dir / "CANCELLED.md"
    cancel_report_rel = Path("CANCELLED.md")
    try:
        cancel_report_display = cancel_report.relative_to(root)
    except ValueError:
        cancel_report_display = cancel_report
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    gate_log = data.get("gate_log", {})
    blocked = data.get("blocked_reason", "")
    artifacts = data.get("artifacts", {})

    # Base determinística
    base_report = (
        f"# Run Cancelado\n\n"
        f"**Data:** {ts}\n"
        f"**Node atual:** {current_node}\n"
        f"**Progresso:** {len(completed)}/{total} steps\n"
        f"**Steps concluídos:** {', '.join(completed) if completed else 'nenhum'}\n"
        f"**Gates:** {', '.join(f'{k}={v}' for k, v in gate_log.items()) if gate_log else 'nenhum'}\n"
        f"**Artefatos:** {', '.join(artifacts.keys()) if artifacts else 'nenhum'}\n"
        f"**Último bloqueio:** {blocked or 'nenhum'}\n\n"
        f"## Motivo do cancelamento\n\n"
        f"{reason}\n"
    )

    print(_ui.header("Run cancelado"))
    print(_ui.info(f"Node: {current_node} ({len(completed)}/{total} steps)"))
    print(_ui.info(f"Motivo: {reason}"))

    # Análise LLM do cancelamento
    print(_ui.info("Gerando análise do cancelamento..."))
    from ft.engine.delegate import delegate_to_llm
    llm_engine = resolve_llm_engine(args) or data.get("llm_engine") or "claude"
    llm_model = resolve_llm_model(args) or data.get("llm_model")
    llm_effort = resolve_llm_effort(args) or data.get("llm_effort")

    analysis_prompt = (
        f"Um run do processo Fast Track foi cancelado. Analise o contexto e produza "
        f"um relatório de encerramento.\n\n"
        f"DADOS DO RUN:\n{base_report}\n\n"
        f"PRODUZA uma análise com:\n"
        f"## Análise do cancelamento\n"
        f"- O que foi concluído e o que ficou pendente\n"
        f"- Se o motivo do cancelamento indica problema de produto ou de processo\n"
        f"- Recomendação: retomar este run (ft continue) ou iniciar novo (ft run)\n\n"
        f"## Aprendizados para o próximo ciclo\n"
        f"- O que o ciclo parcial ensinou\n"
        f"- O que deveria mudar no próximo run\n\n"
        f"Escreva o relatório completo em: {cancel_report_rel}\n"
        f"Comece com o conteúdo base que já preparei, e adicione as seções de análise.\n"
        f"Ao final diga DONE."
    )

    # Salvar base primeiro (fallback se LLM falhar)
    cancel_report.write_text(base_report)

    result = delegate_to_llm(
        task=analysis_prompt,
        project_root=str(run_dir),
        allowed_paths=[str(cancel_report_rel)],
        max_turns=10,
        llm_engine=llm_engine,
        llm_model=llm_model,
        llm_effort=llm_effort,
    )

    if result.success:
        print(_ui.success("Relatório de cancelamento gerado com análise"))
    else:
        print(_ui.warn("LLM não disponível — relatório base salvo sem análise"))

    print(_ui.dim(f"Relatório: {cancel_report_display}"))
    print(_ui.info("Para iniciar um novo run: ft run . --template <T>"))


def cmd_setup_env(args):
    """Executa o script opcional de provisionamento do ambiente do projeto."""
    import os
    key = os.environ.get("SYM_GATEWAY_PROJECT_KEY")
    if not key:
        print("  ✗ SYM_GATEWAY_PROJECT_KEY não definida\n")
        print("    Exporte antes de rodar:")
        print("      export SYM_GATEWAY_PROJECT_KEY=sk-sym_...")
        print("      export SYM_GATEWAY_ADMIN_KEY=sk-sym_...  # opcional")
        sys.exit(1)
    project_root = Path(args.project).resolve() if args.project else find_project_root()
    if not _run_environment_script(
        project_root,
        str(args.template),
        "register_gateway.sh",
    ):
        print(
            "  ✗ register_gateway.sh não encontrado ao lado do template "
            f"{args.template}"
        )
        print("    Materialize-o primeiro: ft run . --template symgateway")
        sys.exit(1)
    print(f"  Projeto: {project_root}")


def cmd_migrate_layout(args):
    """Migrate legacy layouts or remove V2's default process selection."""
    from ft.engine import ui as _ui
    from ft.engine.layout import _read_manifest_file
    from ft.project import migrate_v2_manifest

    project_root = Path(args.project).resolve()
    _guard_engine_repo(project_root)
    manifest_path = paths.project_manifest(project_root)
    if manifest_path.is_file():
        # Read structure without applying the current-layout validator first:
        # schema V1 and hybrid V2 are precisely the inputs this command exists
        # to migrate.  Only a canonical named V2 manifest takes the narrow
        # manifest-only path; every flat/hybrid form goes to the layout mover.
        manifest = _read_manifest_file(manifest_path)
        has_legacy_layout_keys = any(
            key in manifest for key in ("process", "template", "origin_template")
        )
        if (
            manifest.get("schema_version") == 2
            and not has_legacy_layout_keys
        ):
            result = migrate_v2_manifest(project_root, dry_run=args.dry_run)
            prefix = "Planejado" if args.dry_run else "Migrado"
            print(_ui.success(f"{prefix}: {project_root}"))
            for action in result.actions:
                print(_ui.info(action))
            if result.backup_path:
                print(_ui.dim(f"Backup: {result.backup_path}"))
            if args.dry_run:
                print(_ui.dim("Nenhum arquivo foi alterado."))
            return
    actions = migrate_legacy_layout(
        project_root,
        dry_run=args.dry_run,
        cycle_id=args.cycle_id,
    )
    prefix = "Planejado" if args.dry_run else "Migrado"
    print(_ui.success(f"{prefix}: {project_root}"))
    for action in actions:
        print(_ui.info(action))
    if args.dry_run:
        print(_ui.dim("Nenhum arquivo foi alterado."))


def _normalize_hipotese(
    hipotese_path: Path,
    project_root: Path,
    llm_engine: str = "claude",
    llm_model: str | None = None,
    llm_effort: str | None = None,
) -> None:
    """Verifica se hipotese.md está no formato correto; corrige via LLM se não estiver.

    Critérios obrigatórios (espelham os validators do node ft.mdd.01.hipotese):
      - pelo menos 10 linhas
      - seção ## Problema
      - seção ## Oportunidade
    """
    from ft.engine.validators.artifacts import file_exists, min_lines, has_sections
    from ft.engine.delegate import delegate_to_llm

    rel = str(hipotese_path.relative_to(project_root))

    ok_exists, _ = file_exists(rel, project_root=str(project_root))
    ok_lines, _ = min_lines(rel, 10, project_root=str(project_root))
    ok_sections, _ = has_sections(rel, ["Problema", "Oportunidade"], project_root=str(project_root))

    if ok_exists and ok_lines and ok_sections:
        print("  hipotese.md validada — formato OK")
        return

    missing = []
    if not ok_lines:
        missing.append("menos de 10 linhas")
    if not ok_sections:
        missing.append("seções obrigatórias ausentes (## Problema e/ou ## Oportunidade)")

    print(f"  hipotese.md fora do formato ({', '.join(missing)}) — corrigindo via LLM...")

    conteudo = hipotese_path.read_text()
    prompt = f"""O usuário forneceu uma hipótese de produto em formato livre.
Reformate-a no padrão obrigatório, preservando TODO o conteúdo original — não invente informações.

Conteúdo fornecido:
---
{conteudo}
---

Formato obrigatório:
- Arquivo markdown com pelo menos 10 linhas
- Seção ## Problema — descreva o problema que o produto resolve
- Seção ## Oportunidade — descreva a oportunidade de mercado/negócio
- Pode ter outras seções adicionais se o conteúdo original as tiver

Escreva o arquivo corrigido em: docs/hipotese.md
Ao final diga DONE."""

    result = delegate_to_llm(task=prompt, project_root=str(project_root),
                             allowed_paths=["docs/"], max_turns=5,
                             llm_engine=llm_engine,
                             llm_model=llm_model,
                             llm_effort=llm_effort)

    if not result.success:
        print("  AVISO: LLM não conseguiu corrigir hipotese.md — o processo vai solicitar reescrita")
        return

    # Re-validar após correção
    ok_lines2, _ = min_lines(rel, 10, project_root=str(project_root))
    ok_sections2, _ = has_sections(rel, ["Problema", "Oportunidade"], project_root=str(project_root))
    if ok_lines2 and ok_sections2:
        print("  hipotese.md corrigida e validada")
    else:
        print("  AVISO: hipotese.md ainda fora do formato após correção — o processo vai solicitar reescrita")


def _resolve_run_mode(
    project_root: Path,
    process_path: str | Path | None = None,
) -> str:
    """Lê run_mode de environment.yml. Default: isolated."""
    from ft.engine.hooks import load_environment
    env = load_environment(str(project_root), process_path=process_path)
    return env.get("run_mode", "isolated")


def _is_pristine_state(data: dict) -> bool:
    """True para state recém-inicializado, sem execução real de node."""
    if data.get("node_status") != "ready":
        return False

    progress_keys = (
        "completed_nodes",
        "gate_log",
        "artifacts",
        "pending_approval",
        "last_approval_message",
        "pending_fix",
        "exploration_log",
        "active_llm_log",
        "last_llm_log",
        "blocked_reason",
    )
    if any(data.get(key) for key in progress_keys):
        return False

    metrics = data.get("metrics") or {}
    for key, value in metrics.items():
        if key == "steps_total":
            continue
        if value not in (0, 0.0, None, "", [], {}):
            return False

    return True


def _is_pristine_cycle_dir(cycle_dir: Path, data: dict) -> bool:
    """Só remove ciclo vazio: state pristine + nenhum artefato além de log INIT."""
    if not _is_pristine_state(data):
        return False

    allowed_files = {
        Path("state") / "engine_state.yml",
    }
    for path in cycle_dir.rglob("*"):
        if path.is_dir():
            continue
        rel = path.relative_to(cycle_dir)
        if rel in allowed_files:
            continue
        if len(rel.parts) == 1 and path.name.endswith("_log.md"):
            continue
        return False
    return True


def _is_empty_cycle_dir(cycle_dir: Path) -> bool:
    """True para ciclo criado antes do state, sem qualquer arquivo de trabalho."""
    for path in cycle_dir.rglob("*"):
        if path.is_file():
            return False
    return True


def _cleanup_pristine_runs(project_root: Path) -> int:
    """Remove worktrees runtime que foram apenas inicializados e abandonados."""
    import shutil
    import yaml as _yaml

    removed = 0
    for cycles_root in (paths.worktrees_home(project_root),):
        if not cycles_root.is_dir():
            continue
        for cycle_dir in list(cycles_root.iterdir()):
            if not cycle_dir.is_dir() or not _is_cycle_dir(cycle_dir):
                continue
            state = cycle_dir / "state" / "engine_state.yml"
            if not state.exists():
                if _is_empty_cycle_dir(cycle_dir):
                    shutil.rmtree(cycle_dir)
                    removed += 1
                continue
            try:
                data = _yaml.safe_load(state.read_text()) or {}
            except Exception:
                continue
            if _is_pristine_cycle_dir(cycle_dir, data):
                if _runtime_lock_owner_is_alive(data.get("_lock")):
                    continue
                shutil.rmtree(cycle_dir)
                removed += 1
    return removed


@dataclass(frozen=True)
class _ActiveRunRecord:
    """Runtime ativo suficiente para provar (ou não) disjunção de processo."""

    description: str
    state_path: Path
    process_name: str | None
    isolated: bool


def _startup_reservation_payload(
    process_relative: Path | None,
    *,
    isolated: bool = True,
    exclusive: bool = False,
) -> dict:
    """State mínimo que torna o startup visível antes de construir o runner."""
    from ft.engine.state import process_start_identity

    process_path = process_relative.as_posix() if process_relative is not None else None
    return {
        "process_path": process_path,
        "current_node": "__preparing__",
        "node_status": "preparing",
        "isolated": isolated,
        "exclusive": exclusive,
        "completed_nodes": [],
        "metrics": {"steps_completed": 0, "steps_total": 0},
        "_lock": {
            "owner": "ft_startup",
            "pid": os.getpid(),
            "pid_start": process_start_identity(os.getpid()),
        },
    }


def _write_startup_reservation(
    reservation_path: Path,
    process_relative: Path | None,
    *,
    isolated: bool = True,
    exclusive: bool = False,
) -> None:
    """Persiste uma reserva atômica usando o mesmo lock do state normal."""
    from ft.engine.state import StateManager

    StateManager(reservation_path)._write_raw(
        _startup_reservation_payload(
            process_relative,
            isolated=isolated,
            exclusive=exclusive,
        )
    )


def _release_startup_reservation(
    project_root: Path,
    reservation: Path,
) -> None:
    """Remove somente uma reserva pertencente a esta invocação."""
    from ft.engine.layout import _manifest_write_lock

    with _manifest_write_lock(project_root):
        if not reservation.exists():
            return
        try:
            payload = yaml.safe_load(reservation.read_text(encoding="utf-8")) or {}
        except (OSError, UnicodeError, yaml.YAMLError):
            return
        lock = payload.get("_lock", {}) if isinstance(payload, dict) else {}
        if isinstance(lock, dict) and lock.get("pid") == os.getpid():
            reservation.unlink(missing_ok=True)


def _release_continuous_startup(project_root: Path) -> None:
    _release_startup_reservation(
        project_root,
        paths.continuous_startup_path(project_root),
    )


@contextmanager
def _suspend_startup_exclusively(
    project_root: Path,
    reservation: Path,
    process_relative: Path | None,
    *,
    isolated: bool,
):
    """Solta o flock sem permitir mutações durante hooks Git arbitrários.

    Hooks e ``git worktree add`` podem observar o repositório inteiro, não só
    o processo selecionado. Enquanto executam, a reserva fica deliberadamente
    sem identidade de processo: comandos mutantes falham rápido no guard em
    vez de deadlockar ou expor um bundle parcialmente substituído ao hook.
    """
    from ft.engine.layout import _suspend_manifest_write_lock

    _write_startup_reservation(
        reservation,
        None,
        isolated=isolated,
        exclusive=True,
    )
    try:
        with _suspend_manifest_write_lock(project_root):
            yield
    finally:
        _write_startup_reservation(
            reservation,
            process_relative,
            isolated=isolated,
        )


def _state_process_name(data: dict) -> str | None:
    """Extrai somente o nome fixado pelo path canônico do processo local.

    ``process_id`` identifica o grafo e ``template_id`` identifica a origem;
    ambos podem divergir do nome do fork registrado. Estados antigos ou paths
    ambíguos retornam ``None`` para que mutações falhem de modo conservador.
    """
    raw = data.get("process_path")
    if not isinstance(raw, str) or not raw or raw != raw.strip():
        return None
    candidate = Path(raw)
    if candidate.is_absolute() or ".." in candidate.parts:
        return None
    parts = candidate.parts
    if len(parts) != 4 or parts[:2] != (".ft", "process"):
        return None
    name = parts[2]
    if not name or name in {".", ".."} or parts[3] != "process.yml":
        return None
    try:
        paths.project_named_process_dir(Path("."), name)
    except ValueError:
        return None
    if candidate.as_posix() != f".ft/process/{name}/process.yml":
        return None
    return name


def _runtime_lock_owner_is_alive(lock: object) -> bool:
    """Valida PID de runtime sem confundir um PID reciclado com o dono.

    Reservas de versões anteriores não têm ``pid_start``; para elas mantemos
    o comportamento conservador de considerar um PID vivo como proprietário.
    """
    if not isinstance(lock, dict):
        return False
    try:
        pid = int(lock.get("pid"))
    except (TypeError, ValueError):
        return False
    if not _is_pid_alive(pid):
        return False
    recorded = lock.get("pid_start")
    if recorded in (None, ""):
        return True

    from ft.engine.state import process_start_identity

    current = process_start_identity(pid)
    return current is not None and str(recorded) == current


def _active_run_records(project_root: Path) -> list[_ActiveRunRecord]:
    """Enumera todos os runtimes ativos, preservando a ordem histórica.

    Continuous vem primeiro; worktrees vêm da mais nova para a mais antiga.
    Um state não vazio mas ilegível permanece como runtime de identidade
    desconhecida: para comandos mutantes é mais seguro bloquear que ignorar.
    """
    records: list[_ActiveRunRecord] = []

    def collect(state_path: Path, cycle_name: str, *, isolated: bool) -> None:
        if not state_path.exists():
            return
        try:
            loaded = yaml.safe_load(state_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError):
            records.append(_ActiveRunRecord(
                description=f"{cycle_name} (estado inválido)",
                state_path=state_path,
                process_name=None,
                isolated=isolated,
            ))
            return
        if loaded in (None, {}):
            if isolated:
                records.append(_ActiveRunRecord(
                    description=f"{cycle_name} (estado vazio)",
                    state_path=state_path,
                    process_name=None,
                    isolated=True,
                ))
            return
        if not isinstance(loaded, dict):
            records.append(_ActiveRunRecord(
                description=f"{cycle_name} (estado inválido)",
                state_path=state_path,
                process_name=None,
                isolated=isolated,
            ))
            return
        if (
            loaded.get("node_status") == "preparing"
            or loaded.get("current_node") == "__preparing__"
        ):
            raw_lock = loaded.get("_lock", {})
            raw_pid = raw_lock.get("pid") if isinstance(raw_lock, dict) else None
            try:
                parsed_pid = int(raw_pid)
            except (TypeError, ValueError):
                parsed_pid = 0
            if parsed_pid <= 0:
                records.append(_ActiveRunRecord(
                    description=f"{cycle_name} (reserva de state inválida)",
                    state_path=state_path,
                    process_name=None,
                    isolated=isolated,
                ))
                return
            if not _runtime_lock_owner_is_alive(raw_lock):
                return
        if not _is_active_state_data(loaded):
            return
        node = loaded.get("current_node", "?")
        status = loaded.get("node_status", "?")
        records.append(_ActiveRunRecord(
            description=f"{cycle_name} ({node} — {status})",
            state_path=state_path,
            process_name=_state_process_name(loaded),
            isolated=isolated,
        ))

    collect(
        paths.continuous_state_path(project_root),
        "modo continuous",
        isolated=False,
    )

    # O startup continuous pode executar hooks, triage e health-check antes de
    # criar engine_state.yml. A reserva fecha essa janela; marcadores de um PID
    # morto são inertes e não deixam o projeto bloqueado após uma falha abrupta.
    startup = paths.continuous_startup_path(project_root)
    if startup.exists():
        try:
            payload = yaml.safe_load(startup.read_text(encoding="utf-8")) or {}
        except (OSError, UnicodeError, yaml.YAMLError):
            payload = None
        if isinstance(payload, dict):
            raw_lock = payload.get("_lock", {})
            raw_pid = raw_lock.get("pid") if isinstance(raw_lock, dict) else None
            try:
                parsed_pid = int(raw_pid)
            except (TypeError, ValueError):
                parsed_pid = 0
            if parsed_pid <= 0:
                records.append(_ActiveRunRecord(
                    description="modo continuous (reserva inválida)",
                    state_path=startup,
                    process_name=None,
                    isolated=False,
                ))
            elif _runtime_lock_owner_is_alive(raw_lock):
                records.append(_ActiveRunRecord(
                    description="modo continuous (preparando state)",
                    state_path=startup,
                    process_name=_state_process_name(payload),
                    isolated=False,
                ))
        else:
            records.append(_ActiveRunRecord(
                description="modo continuous (reserva inválida)",
                state_path=startup,
                process_name=None,
                isolated=False,
            ))

    startup_home = paths.startup_reservations_home(project_root)
    if startup_home.is_dir():
        for reservation in sorted(startup_home.glob("*.yml")):
            try:
                payload = yaml.safe_load(
                    reservation.read_text(encoding="utf-8")
                ) or {}
            except (OSError, UnicodeError, yaml.YAMLError):
                payload = None
            if not isinstance(payload, dict):
                records.append(_ActiveRunRecord(
                    description="startup de ciclo (reserva inválida)",
                    state_path=reservation,
                    process_name=None,
                    isolated=True,
                ))
                continue
            raw_lock = payload.get("_lock", {})
            raw_pid = raw_lock.get("pid") if isinstance(raw_lock, dict) else None
            try:
                parsed_pid = int(raw_pid)
            except (TypeError, ValueError):
                parsed_pid = 0
            if parsed_pid <= 0 or payload.get("isolated") is not True:
                records.append(_ActiveRunRecord(
                    description="startup de ciclo (reserva inválida)",
                    state_path=reservation,
                    process_name=None,
                    isolated=True,
                ))
            elif _runtime_lock_owner_is_alive(raw_lock):
                records.append(_ActiveRunRecord(
                    description=f"startup de ciclo (PID {parsed_pid} — preparando snapshot)",
                    state_path=reservation,
                    process_name=_state_process_name(payload),
                    isolated=True,
                ))

    wt_home = paths.worktrees_home(project_root)
    if wt_home.is_dir():
        candidates = sorted(
            [d for d in wt_home.iterdir() if d.is_dir() and _is_cycle_dir(d)],
            key=_cycle_num,
            reverse=True,
        )
        for wt in candidates:
            state_path = wt / "state" / "engine_state.yml"
            if not state_path.exists():
                # O startup atual sempre publica uma reserva antes de criar a
                # worktree. Sem state e sem reserva viva, este diretório é um
                # órfão inerte (por exemplo, SIGKILL após `git worktree add`),
                # não um runtime capaz de observar updates no checkout raiz.
                continue
            else:
                collect(
                    state_path,
                    wt.name,
                    isolated=True,
                )
    return records


def _is_pid_alive(pid: int) -> bool:
    """Verifica se um PID está rodando."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _select_run_process(args, project_root: Path) -> tuple[Path, Path | None]:
    """Resolve ``--template`` local-first and materialize it exactly once."""
    from ft.templates import resolve_template

    root = project_root.resolve()
    template = str(getattr(args, "template", "") or "").strip()
    if not template:
        raise ValueError("--template é obrigatório")
    resolved = resolve_template(root, template)
    selected = resolved.process_file.resolve()
    relative = selected.relative_to(root)
    if resolved.materialized:
        print(f"  Template materializado: {relative}")
    else:
        print(f"  Template local: {relative}")
    if resolved.source_drift:
        print(
            "  AVISO: o catálogo global mudou; o fork local foi preservado "
            "sem sobrescrita"
        )
    return selected, relative


@dataclass(frozen=True)
class _PreparedRunRuntime:
    project_root: Path
    state_path: Path
    run_mode: str
    effective_engine: str
    process_path_at_root: Path
    process_relative: Path | None
    manifest_model: str | None
    manifest_effort: str | None
    continuous_state_existed: bool


def _prepare_run_runtime(
    args,
    *,
    source_project_root: Path,
    process_path_at_root: Path,
    process_relative: Path | None,
    explicit_cycle_name: str | None,
    inherited_engine: str | None = None,
) -> _PreparedRunRuntime:
    """Create one immutable Git worktree without blocking other active runs."""
    from ft.runs import RunCoordinator

    root = source_project_root.resolve()
    if process_relative is None:
        raise ValueError("o template precisa estar materializado em .ft/process/")

    manifest_engine, manifest_model, manifest_effort = manifest_llm_defaults(root)
    effective_engine = (
        resolve_llm_engine(args)
        or manifest_engine
        or os.environ.get("FT_LLM_ENGINE", "").strip().lower()
        or "claude"
    )
    prepared = RunCoordinator(root).prepare(
        template_id=str(args.template),
        process_path=process_path_at_root,
        process_digest=process_digest(process_path_at_root),
        requested_cycle=explicit_cycle_name,
    )
    print(f"  RunMode: isolated → {prepared.worktree}")
    return _PreparedRunRuntime(
        project_root=prepared.worktree,
        state_path=prepared.state_path,
        run_mode="isolated",
        effective_engine=effective_engine,
        process_path_at_root=process_path_at_root,
        process_relative=process_relative,
        manifest_model=manifest_model,
        manifest_effort=manifest_effort,
        continuous_state_existed=False,
    )


def cmd_run(args):
    """Materialize one template and execute it in a new isolated cycle."""
    import sys
    sys.stdout.reconfigure(line_buffering=True)

    source_project_root = Path(args.project).resolve()
    project_root = source_project_root
    if not project_root.is_dir():
        raise ValueError(
            f"projeto inexistente: {project_root}. Execute `ft init {args.project}` primeiro"
        )
    _guard_engine_repo(project_root)
    if not paths.project_manifest(project_root).is_file():
        raise ValueError(
            f"repositório Fast Track não inicializado em {project_root}; execute `ft init`"
        )

    # Materialization changes only versioned FT metadata.  It is committed
    # before the cycle snapshot, while the short project-preparation lock keeps
    # two concurrent starts from observing a half-published catalog update.
    from ft.engine.git_ops import (
        commit_staged_knowledge,
        stage_knowledge,
        verify_hooks_from_process_meta,
    )
    from ft.runs import RunCoordinator, project_prep_lock

    coordinator = RunCoordinator(project_root)
    with project_prep_lock(project_root) as preparation:
        coordinator.preflight()
        process_path_at_root, process_relative = _select_run_process(
            args,
            source_project_root,
        )
        process_payload = yaml.safe_load(
            process_path_at_root.read_text(encoding="utf-8")
        ) or {}
        if not isinstance(process_payload, dict):
            raise ValueError(f"template inválido: {process_path_at_root}")
        ok, staged, detail = stage_knowledge(str(project_root))
        if not ok:
            raise RuntimeError(detail)
        if staged:
            # Preserve the project lease while arbitrary Git hooks run.  Peer
            # startups wait; descendants spawned by a hook fail fast instead
            # of deadlocking their parent commit.
            with preparation.suspend():
                ok, detail = commit_staged_knowledge(
                    str(project_root),
                    label=f"materialize template {args.template}",
                    verify_hooks=verify_hooks_from_process_meta(process_payload),
                )
            if not ok:
                raise RuntimeError(detail)
        print(f"  {detail}")
        coordinator.preflight()

    # Resolve and validate input before allocating a cycle, so a missing
    # required request never leaves an unused branch/worktree behind.
    from ft.templates import load_input_policy

    input_policy = load_input_policy(process_path_at_root)
    input_file = getattr(args, "demand_input", None)
    if input_file is not None:
        input_file = Path(input_file).expanduser()
        if not input_file.is_absolute():
            input_file = Path.cwd() / input_file
    acquired_input = input_policy.acquire(
        request=getattr(args, "request", None),
        input_file=input_file,
        prompt_fn=lambda prompt: input(f"{prompt}: "),
    )

    try:
        explicit_cycle_name = _validate_cycle_name(getattr(args, "cycle_name", None))
    except ValueError as e:
        from ft.engine import ui as _ui
        print(_ui.fail(f"--cycle-name inválido: {e}"))
        sys.exit(1)

    prepared = _prepare_run_runtime(
        args,
        source_project_root=source_project_root,
        process_path_at_root=process_path_at_root,
        process_relative=process_relative,
        explicit_cycle_name=explicit_cycle_name,
    )
    project_root = prepared.project_root
    state_path = prepared.state_path
    _effective_engine = prepared.effective_engine
    process_path_at_root = prepared.process_path_at_root
    process_relative = prepared.process_relative
    manifest_model = prepared.manifest_model
    manifest_effort = prepared.manifest_effort

    if acquired_input is not None:
        input_text, _input_source = acquired_input
        staged_input = input_policy.stage(project_root, request=input_text)
        if staged_input is not None:
            print(
                "  Input do template: "
                f"{staged_input.destination.relative_to(project_root)}"
            )

    # Resolver YAML do processo dentro do ambiente efetivo.
    if process_relative is not None and project_root != source_project_root:
        process_path = project_root / process_relative
    elif process_path_at_root.is_file():
        process_path = process_path_at_root
    else:
        raise RuntimeError("a worktree não contém o template pinado")

    # Handoff é histórico de ciclo, mas precisa voltar ao contexto transitório
    # durante a execução seguinte. O close o arquiva novamente com a nova versão.
    if not args.from_project:
        docs_dir = project_root / "docs"
        for filename in ("handoff.md", "plano_de_voo.md"):
            source = latest_cycle_artifact(project_root, filename)
            target = docs_dir / filename
            if source and not target.exists():
                import shutil as _shutil
                _shutil.copy2(source, target)
                print(f"  Contexto anterior: {source.relative_to(project_root)} → docs/{filename}")

    llm_model = resolve_llm_model(args) or manifest_model
    llm_effort = resolve_llm_effort(args) or manifest_effort

    runner = StepRunner(
        process_path=process_path,
        state_path=state_path,
        project_root=project_root,
        llm_engine=_effective_engine,
        llm_model=llm_model,
        llm_effort=llm_effort,
        llm_defaults_root=source_project_root,
        llm_engine_is_override=resolve_llm_engine(args) is not None,
        llm_model_is_override=resolve_llm_model(args) is not None,
        llm_effort_is_override=resolve_llm_effort(args) is not None,
        verbose=getattr(args, "verbose", False),
    )
    runner._bypass_human_gates = resolve_bypass_human_gates(args)

    # Disparar hooks on_env_setup se definidos no environment.yml
    from ft.engine.hooks import run_hooks
    run_hooks(
        "on_env_setup",
        str(project_root),
        getattr(runner, "_environment", None),
        process_path=process_path,
    )

    import shutil

    # Copiar plano_de_voo do ciclo anterior se fornecido
    if args.from_project:
        source_project = Path(args.from_project).resolve()
        src = latest_cycle_artifact(source_project, "plano_de_voo.md")
        dst_docs = project_root / "docs"
        dst = dst_docs / "plano_de_voo.md"
        if src and src.exists():
            if src.resolve() == dst.resolve():
                print("  plano_de_voo.md já está em docs/ (mesmo projeto)")
            else:
                dst_docs.mkdir(parents=True, exist_ok=True)
                shutil.copy(src, dst)
                print(f"  plano_de_voo.md copiado de {args.from_project}")
        else:
            print(f"  AVISO: --from-project sem plano_de_voo.md em {paths.project_cycles_dir(source_project)}")

    # Copiar e normalizar hipótese inicial se fornecida (pre-seed de ft.mdd.01.hipotese)
    if args.hipotese:
        src = Path(args.hipotese)
        if not src.exists():
            print(f"ERRO: arquivo de hipótese não encontrado: {src}")
            sys.exit(1)
        dst_docs = project_root / "docs"
        dst_docs.mkdir(parents=True, exist_ok=True)
        dst = dst_docs / "hipotese.md"
        shutil.copy(src, dst)
        print(f"  hipotese.md copiado de {src}")
        _normalize_hipotese(
            dst,
            project_root,
            llm_engine=_effective_engine,
            llm_model=llm_model,
            llm_effort=llm_effort,
        )

    # Health check da API antes de começar
    _api_health_check(project_root, _effective_engine)

    # Initialize the pinned state and run.  Other project cycles are independent
    # and remain free to progress in their own worktrees.
    runner.init_state()
    apply_parallel_flags(runner, args)
    runner.run(mode="mvp")


def main():
    parser = argparse.ArgumentParser(
        prog="ft",
        description="ft engine — motor deterministico de processos"
    )
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Modo verboso: mostra output do LLM no terminal")
    sub = parser.add_subparsers(dest="command")

    # init
    init = sub.add_parser(
        "init",
        help="Inicializar ou diagnosticar a base comum do Fast Track",
    )
    init.add_argument(
        "name",
        nargs="?",
        help="Diretório do projeto (default: diretório atual)",
    )
    init_mode = init.add_mutually_exclusive_group()
    init_mode.add_argument(
        "--check",
        action="store_true",
        help="Diagnosticar sem alterar arquivos ou o repositório Git",
    )
    init_mode.add_argument(
        "--fix",
        action="store_true",
        help="Reparar com segurança uma inicialização Fast Track quebrada",
    )
    init_mode.add_argument(
        "--adopt",
        action="store_true",
        help=(
            "Adotar um diretório legado: cria o repositório Git se não existir "
            "e aceita arquivos ainda não commitados"
        ),
    )

    # resume (alias: continue para backward compat)
    cont = sub.add_parser("resume", aliases=["continue"], help="Retomar o processo")
    add_llm_engine_flags(cont)
    cont.add_argument("--step", action="store_true", default=True, help="Avancar 1 step (default)")
    cont.add_argument("--sprint", action="store_true", help="Avancar ate fim da sprint")
    cont.add_argument("--auto", action="store_true", help="Avancar ate MVP (modo autonomo; PARA em human_gates)")
    cont.add_argument("--bypass-human-gates", action="store_true", dest="bypass_human_gates",
                      help="Pular human_gates automaticamente (LLM decide)")
    cont.add_argument("--cycle", help="Ciclo específico a retomar (ex: cycle-07)")
    cont.add_argument("--parallel", action="store_true",
                      help="Honrar parallel_group do processo (persiste no estado do run)")
    cont.add_argument("--no-parallel", action="store_true", dest="no_parallel",
                      help="Desabilitar paralelismo intra-processo num run já iniciado")
    cont.add_argument("--max-parallel", dest="max_parallel", type=int, metavar="N",
                      help="Máximo de worktrees simultâneos por parallel_group (default: 2)")

    # status
    st = sub.add_parser("status", help="Estado atual")
    add_llm_engine_flags(st)
    st.add_argument("--full", "-f", action="store_true", help="Mostrar grafo e artefatos")
    st.add_argument("--report", "-r", action="store_true", help="Relatório de tempo e tokens por node")
    st.add_argument("--cycle", help="Ciclo específico a consultar (ex: cycle-10-opencode)")

    # log — acompanhar o log LLM do ciclo ativo
    lg = sub.add_parser("log", help="Mostrar/acompanhar o log LLM do ciclo ativo")
    add_llm_engine_flags(lg)
    lg.add_argument("--follow", "-f", "--tail", action="store_true", dest="follow", help="Acompanhar em tempo real (troca de log sozinho quando o node muda)")
    lg.add_argument("--lines", "-n", type=int, default=None, help="Quantas linhas mostrar inicialmente (default: 30)")
    lg.set_defaults(_parser=lg)
    lg.add_argument("--raw", action="store_true", help="NDJSON cru, sem formatação")
    lg.add_argument("--markdown", "-m", action="store_true", help="Realça a saída por cor/ênfase: comandos bash, ferramentas, resposta e raciocínio")
    lg.add_argument("--path", action="store_true", help="Só imprimir o caminho do log ativo")
    lg.add_argument("--cycle", help="Ciclo específico a acompanhar (ex: cycle-10-opencode)")

    # runs — tabela comparativa de todos os ciclos
    ru2 = sub.add_parser("runs", help="Ciclos ativos no runtime e fechados em .ft/cycles/")
    ru2.add_argument("--done", action="store_true",
                     help="Incluir também os ciclos concluídos (default: só ativos)")
    ru2.add_argument("project", nargs="?", default=".", help="Diretório do projeto")

    llm_capabilities = sub.add_parser(
        "llm-capabilities",
        help="Descobrir agentes, modelos, efforts e defaults via CLIs instaladas",
    )
    llm_capabilities.add_argument(
        "--json",
        action="store_true",
        help="Emitir JSON compacto para integração",
    )

    llm_defaults = sub.add_parser(
        "llm-defaults",
        help="Validar e persistir os defaults LLM do projeto",
    )
    llm_defaults.add_argument(
        "--agent",
        required=True,
        choices=["claude", "codex", "opencode"],
        help="Coding agent padrão",
    )
    llm_defaults.add_argument(
        "--model",
        required=True,
        metavar="MODEL",
        help="Modelo anunciado pelo probe fresco do agent",
    )
    llm_defaults.add_argument(
        "--effort",
        metavar="LEVEL",
        help="Effort anunciado pelo modelo; omita ou use default para o provider escolher",
    )
    llm_defaults.add_argument(
        "--json",
        action="store_true",
        help="Emitir JSON compacto para integração",
    )

    # approve
    ap = sub.add_parser("approve", help="Aprovar artefato pendente")
    add_llm_engine_flags(ap)
    ap.add_argument("message", nargs="?", default=None,
                    help="Nota opcional registrada no log (ex: 'Aprovado após revisão')")
    ap.add_argument("--no-continue", action="store_true", help="Nao continuar automaticamente")
    ap.add_argument("--auto", action="store_true", help="Após aprovar, avança sozinho até o próximo human gate (modo autônomo)")
    ap.add_argument("--sprint", action="store_true", help="Após aprovar, avança até o fim da sprint")
    ap.add_argument("--bypass-human-gates", action="store_true", help="Pular human_gates automaticamente (LLM decide)")
    ap.add_argument("--cycle", help="Ciclo específico a aprovar (ex: cycle-12-f01-busca)")

    # reject
    rj = sub.add_parser("reject", help="Rejeitar artefato pendente")
    add_llm_engine_flags(rj)
    rj.add_argument("reason", help="Motivo da rejeicao")
    rj.add_argument("--no-retry", action="store_true", help="Nao reenviar ao LLM apos rejeicao")
    rj.add_argument("--cycle", help="Ciclo específico a rejeitar (ex: cycle-12-f01-busca)")

    # graph
    graph = sub.add_parser("graph", help="Mostrar grafo com status")
    add_llm_engine_flags(graph)
    graph.add_argument("--cycle", help="Ciclo específico a consultar")

    # validate
    validate = sub.add_parser("validate", help="Validar um template local materializado")
    validate.add_argument("--template", "-t", required=True, metavar="TEMPLATE")

    # lint-process
    lp = sub.add_parser("lint-process", help="Lint semântico — detecta especificidades de projeto no YAML")
    add_llm_engine_flags(lp)
    lp.add_argument("--template", "-t", required=True, metavar="TEMPLATE")

    # explore
    ex = sub.add_parser(
        "explore",
        help="Pergunta read-only ao LLM; preserva o modo legado em node exploration",
    )
    add_llm_engine_flags(ex)
    ex.add_argument("request", nargs="?", help="Prompt ao LLM (entre aspas)")
    ex.add_argument(
        "--agent",
        choices=["claude", "codex", "gemini", "opencode"],
        help="Provider standalone (alternativa a --claude/--codex/--gemini/--opencode)",
    )
    ex.add_argument(
        "--model",
        metavar="MODEL",
        help="Modelo standalone (alternativa ao modelo junto ao flag do provider)",
    )
    ex.add_argument(
        "--stream-json",
        action="store_true",
        help="Forçar standalone e emitir NDJSON progressivo: start, chunk, result/error",
    )
    ex.add_argument(
        "--standalone",
        action="store_true",
        help="Forçar consulta read-only independente, mesmo com node exploration ativo",
    )
    ex.add_argument("--finish", action="store_true", help="Encerrar exploração e gerar relatório")
    ex.add_argument("--skip", action="store_true", help="Pular o node de exploração sem gerar relatório")
    ex.add_argument("--cycle", help="Ciclo específico para exploração ligada ao grafo")

    # evolve
    ev = sub.add_parser(
        "evolve",
        help="Evoluir o processo em paralelo ao ciclo (não avança steps)",
    )
    add_llm_engine_flags(ev)
    ev.add_argument("directive", nargs="?",
                    help="Diretriz para orientar a evolução (entre aspas; opcional)")
    ev.add_argument("--template", "-t", default="evolve_process", metavar="TEMPLATE",
                    help="Playbook de evolução com entrypoint evolve (default: evolve_process)")
    ev.add_argument("--project", dest="project_target", action="store_true",
                    help="Aplicar melhorias no fork local .ft/process/ do projeto")
    ev.add_argument("--global", dest="global_target", action="store_true",
                    help="Aplicar melhorias no template global do engine")
    ev.add_argument("--cycle", metavar="NAME",
                    help="Ciclo de onde derivar contexto (omitido somente se houver exatamente um)")
    ev.add_argument("--dry-run", dest="dry_run", action="store_true",
                    help="Derivar e validar melhorias sem aplicar nos alvos")
    ev.add_argument("--yes", "-y", action="store_true",
                    help="Aplicar sem confirmação interativa")

    # retry
    rt = sub.add_parser("retry", help="Retenta o node atual bloqueado sem aplicar correção")
    add_llm_engine_flags(rt)
    rt.add_argument("--auto", action="store_true", help="Continuar em modo MVP após retry")
    rt.add_argument("--bypass-human-gates", action="store_true", dest="bypass_human_gates",
                    help="Pular human_gates automaticamente após retry (LLM decide)")
    rt.add_argument("--cycle", help="Ciclo específico a retentar (ex: cycle-12-f01-busca)")

    # fix
    fx = sub.add_parser("fix", help="Corrigir problema e desbloquear o ciclo")
    add_llm_engine_flags(fx)
    fx.add_argument("instruction", help="Descrição do que corrigir (entre aspas)")
    fx.add_argument("--auto", action="store_true", help="Continuar em modo MVP após correção")
    fx.add_argument("--cycle", help="Ciclo específico a corrigir")

    # close
    cl = sub.add_parser("close", help="Encerrar ciclo: merge artefatos, remover worktree")
    add_llm_engine_flags(cl)
    cl.add_argument("--keep-worktree", action="store_true", dest="keep_worktree",
                     help="Preservar o worktree no disco (não remover)")
    cl.add_argument("--force", action="store_true",
                     help="Encerrar mesmo se o ciclo não terminou")
    cl.add_argument("--merge", choices=["full", "docs", "selective", "none"],
                     help="Estratégia de merge (sem prompt interativo)")
    cl.add_argument("--merge-paths", dest="merge_paths",
                     help="Paths para merge selective (separados por espaço, entre aspas)")
    cl.add_argument("--cycle", help="Ciclo específico a encerrar (ex: cycle-12-f01-busca)")

    # process-candidates
    pc = sub.add_parser(
        "process-candidates",
        help="Listar ou resolver candidatos de melhoria do processo global",
    )
    pc.add_argument("candidate_id", nargs="?", help="ID PI-NNN a resolver")
    pc.add_argument(
        "--status",
        choices=["promoted", "deferred", "rejected"],
        help="Disposição registrada pelo mantenedor",
    )
    pc.add_argument("--reason", help="Justificativa obrigatória da disposição")
    pc.add_argument(
        "--reference",
        help="Commit/path que comprova promoção (obrigatório para promoted)",
    )
    pc.add_argument("--cycle", help="Ciclo específico a consultar")

    # process (gestão dos processos locais frente aos templates globais)
    proc = sub.add_parser(
        "process",
        help="Gerenciar processos locais materializados",
    )
    proc_sub = proc.add_subparsers(dest="process_command", required=True)
    proc_update = proc_sub.add_parser(
        "update",
        help="Sincronizar processos locais com os templates globais",
        description=(
            "Sem nome: varre todos os processos do manifest. Fork intocado com "
            "global evoluído é fast-forward; fork customizado com global "
            "evoluído passa por merge 3-way (git merge-file) com aprovação. "
            "Nada é aplicado sem staging, validação e backup."
        ),
    )
    proc_update.add_argument(
        "name", nargs="?", help="Processo específico (default: todos)"
    )
    proc_update.add_argument(
        "--check",
        action="store_true",
        help="Só relatório, sem escrever nada (exit 1 se houver drift acionável)",
    )
    proc_update.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Aplicar fast-forwards sem confirmação (merges sempre confirmam)",
    )

    # abort
    ab = sub.add_parser("abort", help="Abortar ciclo: descarta worktree e branch sem merge")
    add_llm_engine_flags(ab)
    ab.add_argument("--force", action="store_true", help="Abortar sem prompt de confirmação")
    ab.add_argument("--cycle", help="Ciclo específico a abortar")

    # cancel
    ca = sub.add_parser("cancel", help="Cancelar o run ativo com justificativa")
    add_llm_engine_flags(ca)
    ca.add_argument("reason", help="Motivo do cancelamento (entre aspas)")
    ca.add_argument("--cycle", help="Ciclo específico a cancelar")

    # setup-env
    se = sub.add_parser(
        "setup-env",
        help="Executar register_gateway.sh ao lado de um template local",
    )
    se.add_argument("--project", help="Diretório do projeto (default: CWD ou raiz detectada)")
    se.add_argument("--template", "-t", required=True, metavar="TEMPLATE")

    migrate = sub.add_parser(
        "migrate-layout",
        help="Migrar layout v1 para .ft/process/<template>/",
    )
    migrate.add_argument("project", nargs="?", default=".", help="Diretório do projeto")
    migrate.add_argument("--dry-run", action="store_true", help="Mostrar mudanças sem mover arquivos")
    migrate.add_argument(
        "--cycle-id",
        default="legacy-unscoped",
        help="ID para arquivar os artefatos soltos do último ciclo",
    )

    # run — materializa um template e inicia um ciclo externo independente
    ru = sub.add_parser("run", help="Executar um template em um novo ciclo isolado")
    add_llm_engine_flags(ru)
    ru.add_argument("project", help="Diretório de um projeto já inicializado")
    ru.add_argument("--from-project", metavar="PATH",
                    help="Copiar plano_de_voo.md do ciclo anterior (para retomada de ciclo)")
    ru.add_argument("--hipotese", metavar="FILE",
                    help="Arquivo hipotese.md pré-escrito (pula ft.mdd.01.hipotese)")
    ru.add_argument("--input", metavar="FILE", dest="demand_input",
                    help="Arquivo de entrada aceito pelo template selecionado")
    ru.add_argument("--request", metavar="TEXT",
                    help="Demanda em texto livre aceita pelo template selecionado")
    ru.add_argument("--bypass-human-gates", action="store_true", dest="bypass_human_gates",
                    help="Pular human_gates automaticamente (LLM decide)")
    ru.add_argument("--cycle-name", metavar="NAME",
                    help="Nome explícito do ciclo isolado (ex: cycle-11-opencode). "
                         "Falha se o diretório já existir.")
    ru.add_argument("--template", "-t", required=True, metavar="TEMPLATE",
                    help="Template local ou global a executar")
    ru.add_argument("--auto", action="store_true",
                    help="Avançar em modo autônomo até MVP (PARA em human_gates; "
                         "para pular use --bypass-human-gates)")
    ru.add_argument("--parallel", action="store_true",
                    help="Honrar parallel_group do processo: nodes independentes "
                         "rodam em worktrees paralelos (fan-out/fan-in com merge)")
    ru.add_argument("--max-parallel", dest="max_parallel", type=int, metavar="N",
                    help="Máximo de worktrees simultâneos por parallel_group (default: 2)")

    args = parser.parse_args()

    # Guard global: o ft opera sempre num repo de projeto, nunca no template/engine.
    # run/runs recebem o path do projeto como argumento e validam no próprio cmd_;
    # todos os demais comandos resolvem o projeto a partir do CWD.
    if args.command not in (None, "init", "run", "runs", "migrate-layout"):
        _guard_engine_repo(find_project_root())

    try:
        if args.command == "init":
            cmd_init(args)
        elif args.command in ("resume", "continue"):
            cmd_continue(args)
        elif args.command == "status":
            cmd_status(args)
        elif args.command == "log":
            cmd_log(args)
        elif args.command == "approve":
            cmd_approve(args)
        elif args.command == "reject":
            cmd_reject(args)
        elif args.command == "graph":
            cmd_graph(args)
        elif args.command == "validate":
            cmd_validate(args)
        elif args.command == "lint-process":
            cmd_lint_process(args)
        elif args.command == "explore":
            cmd_explore(args)
        elif args.command == "evolve":
            cmd_evolve(args)
        elif args.command == "retry":
            cmd_retry(args)
        elif args.command == "fix":
            cmd_fix(args)
        elif args.command == "close":
            cmd_close(args)
        elif args.command == "process-candidates":
            cmd_process_candidates(args)
        elif args.command == "process":
            if args.process_command == "update":
                cmd_process_update(args)
        elif args.command == "abort":
            cmd_abort(args)
        elif args.command == "cancel":
            cmd_cancel(args)
        elif args.command == "setup-env":
            cmd_setup_env(args)
        elif args.command == "migrate-layout":
            cmd_migrate_layout(args)
        elif args.command == "run":
            cmd_run(args)
        elif args.command == "runs":
            cmd_runs(args)
        elif args.command == "llm-capabilities":
            cmd_llm_capabilities(args)
        elif args.command == "llm-defaults":
            cmd_llm_defaults(args)
        else:
            parser.print_help()
    except KeyboardInterrupt:
        print("\n  Interrompido pelo usuário.")
        sys.exit(130)
    except Exception as e:
        if os.environ.get("FT_DEBUG"):
            raise
        _print_crash(e)
        sys.exit(1)


def _print_crash(exc: Exception) -> None:
    """Formata exceção não-tratada de forma legível para o usuário."""
    import traceback
    from ft.engine.ui import BOLD_RED, RED, DIM, RESET, BOLD_WHITE, YELLOW

    # Extrair traceback
    tb = traceback.extract_tb(exc.__traceback__)

    print(f"\n{BOLD_RED}{'━' * 54}{RESET}")
    print(f"  {BOLD_RED}Erro inesperado{RESET}: {BOLD_WHITE}{type(exc).__name__}{RESET}")
    print(f"  {RED}{exc}{RESET}")
    print(f"{BOLD_RED}{'━' * 54}{RESET}")

    if tb:
        print(f"\n  {YELLOW}Onde aconteceu:{RESET}")
        # Mostrar apenas os frames relevantes (do ft/, não de stdlib)
        relevant = [f for f in tb if "/ft/" in f.filename or "test" in f.filename]
        frames = relevant if relevant else tb[-3:]
        for frame in frames:
            # Simplificar path: mostrar a partir de ft/
            path = frame.filename
            for prefix in ("/ft/", "/tests/"):
                idx = path.find(prefix)
                if idx >= 0:
                    path = path[idx + 1:]
                    break
            print(f"    {DIM}•{RESET} {path}:{frame.lineno} → {DIM}{frame.name}(){RESET}")
            if frame.line:
                print(f"      {DIM}{frame.line.strip()}{RESET}")

    print(f"\n  {DIM}Para o traceback completo, rode com: FT_DEBUG=1 ft ...{RESET}\n")


if __name__ == "__main__":
    main()
