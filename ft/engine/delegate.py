"""
LLM Executor — interface para chamar Claude Code ou Codex como executor de construcao.
O LLM so constroi. Nao decide nada sobre o processo.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

# Padrões que indicam rate limit / quota esgotada no output do LLM
_RATE_LIMIT_PATTERNS = re.compile(
    r"rate[ _.-]?limit|"
    r"(?:api error|http|status|status_code|code|error)[^\n]{0,80}\b429\b|"
    r"\b429\b[^\n]{0,80}(?:rate[ _.-]?limit|too[ _.-]?many[ _.-]?requests)|"
    r"quota[ _.-]?exceeded|resource[ _.-]?exhausted|"
    r"too[ _.-]?many[ _.-]?requests|overloaded|try[ _.-]?again[ _.-]?in|"
    r"RESOURCE_EXHAUSTED|rateLimitExceeded",
    re.IGNORECASE,
)
# Cronograma default de backoff: ~1h40 de espera acumulada (fora o tempo de
# execução de cada tentativa) — dimensionado para atravessar indisponibilidades
# longas da API, não só picos momentâneos.
# Override por env: FT_RATE_LIMIT_BACKOFF="60,120,240" (segundos, CSV).
_RATE_LIMIT_WAIT = [60, 120, 240, 480, 900, 1800, 1800, 1800]

# Acima deste tamanho o prompt não cabe com folga num argumento de execve
# (MAX_ARG_STRLEN ≈ 128 KiB no Linux) e vai via stdin.
_MAX_ARGV_PROMPT_BYTES = 100_000

DEFAULT_OPENCODE_MODEL = "pgx/zai-org_glm-4.7-flash"
DEFAULT_OPENCODE_CONTEXT_LIMIT = 200_000
DEFAULT_OPENCODE_OUTPUT_LIMIT = 32_768


@dataclass
class _SandboxMount:
    path: Path
    is_file: bool = False
    placeholder: bool = False


class ExecutorIdleTimeout(subprocess.TimeoutExpired):
    """Executor ficou vivo, mas sem emitir nova saída por tempo demais."""


def _env_positive_int(*names: str) -> int | None:
    """Lê o primeiro inteiro positivo definido em env entre os nomes dados."""
    for name in names:
        raw = os.environ.get(name, "").strip()
        if not raw:
            continue
        try:
            value = int(raw)
        except ValueError:
            continue
        if value > 0:
            return value
    return None


def _opencode_read_patterns(paths: list[str], project_root: str | None = None) -> list[str]:
    """Expande paths de leitura negada para formas relativas e absolutas."""
    patterns: list[str] = []
    root = Path(project_root).resolve() if project_root else None
    for raw in paths:
        path = raw.strip()
        if not path:
            continue
        variants = [path]
        if not path.startswith("/"):
            variants.append(f"*/{path}")
            if root is not None:
                variants.append(str(root / path))
        for variant in variants:
            if variant not in patterns:
                patterns.append(variant)
    return patterns


def _env_falsey(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"0", "false", "no", "off"}


def _path_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _looks_like_file_path(raw_path: str, path: Path) -> bool:
    if raw_path.endswith("/"):
        return False
    if path.exists():
        return path.is_file() or path.is_symlink()
    name = path.name
    return (
        "." in name
        or name in {"Makefile", "Dockerfile", "Procfile"}
        or name.startswith(".")
    )


def _prepare_opencode_sandbox_mounts(
    project_root: str,
    allowed_paths: list[str] | None,
) -> list[_SandboxMount]:
    """Prepara mounts writable do OpenCode, restritos aos allowed_paths."""
    root = Path(project_root).resolve()
    mounts: list[_SandboxMount] = []
    seen: set[Path] = set()

    for raw in allowed_paths or []:
        value = str(raw).strip()
        if not value:
            continue
        path = Path(value)
        target = path.resolve() if path.is_absolute() else (root / path).resolve()
        if not _path_relative_to(target, root):
            continue
        is_file = _looks_like_file_path(value, target)
        placeholder = False
        if is_file:
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                target.touch()
                placeholder = True
        else:
            target.mkdir(parents=True, exist_ok=True)
        if target not in seen:
            mounts.append(_SandboxMount(target, is_file=is_file, placeholder=placeholder))
            seen.add(target)

    mounts.sort(key=lambda item: (item.is_file, len(str(item.path))))
    return mounts


def _cleanup_empty_placeholders(mounts: list[_SandboxMount]) -> None:
    for mount in mounts:
        if not mount.placeholder:
            continue
        try:
            if mount.path.is_file() and mount.path.stat().st_size == 0:
                mount.path.unlink()
        except OSError:
            pass


def _resolve_existing_file_paths(project_root: str, paths: list[str] | None) -> list[Path]:
    root = Path(project_root).resolve()
    resolved: list[Path] = []
    for raw in paths or []:
        value = str(raw).strip()
        if not value or value.endswith("/"):
            continue
        path = Path(value)
        target = path.resolve() if path.is_absolute() else (root / path).resolve()
        if _path_relative_to(target, root):
            resolved.append(target)
    return list(dict.fromkeys(resolved))


def _paths_have_content(paths: list[Path]) -> bool:
    if not paths:
        return False
    for path in paths:
        try:
            if not path.is_file() or path.stat().st_size <= 0:
                return False
        except OSError:
            return False
    return True


def _stop_process_tree(proc: subprocess.Popen, terminate_timeout: int = 5, kill_timeout: int = 5) -> None:
    """Encerra o processo e, quando possível, todo o process group dele."""
    if proc.poll() is not None:
        return

    use_group = False
    pgid: int | None = None
    try:
        pgid = os.getpgid(proc.pid)
        use_group = pgid != os.getpgrp()
    except OSError:
        pass

    try:
        if use_group and pgid is not None:
            os.killpg(pgid, signal.SIGTERM)
        else:
            proc.terminate()
    except ProcessLookupError:
        return

    try:
        proc.wait(timeout=terminate_timeout)
    except subprocess.TimeoutExpired:
        pass

    should_kill = proc.poll() is None
    if use_group and pgid is not None:
        try:
            os.killpg(pgid, 0)
            should_kill = True
        except OSError:
            should_kill = proc.poll() is None

    if not should_kill:
        return

    try:
        if use_group and pgid is not None:
            os.killpg(pgid, signal.SIGKILL)
        else:
            proc.kill()
    except ProcessLookupError:
        return

    try:
        proc.wait(timeout=kill_timeout)
    except subprocess.TimeoutExpired:
        pass


def _wait_for_process(
    proc: subprocess.Popen,
    timeout: int,
    early_success_paths: list[Path] | None = None,
    early_success_grace: int = 20,
    activity: dict[str, float] | None = None,
    idle_timeout: int | None = None,
) -> tuple[int, bool]:
    """Espera o processo, podendo encerrar cedo quando outputs já existem."""
    if not hasattr(proc, "poll"):
        return proc.wait(timeout=timeout), False

    started = time.time()
    satisfied_since: float | None = None
    while True:
        returncode = proc.poll()
        if returncode is not None:
            return returncode, False
        now = time.time()
        if now - started > timeout:
            raise subprocess.TimeoutExpired(proc.args, timeout)
        if idle_timeout and activity:
            last_activity = activity.get("last", started)
            if now - last_activity > idle_timeout:
                raise ExecutorIdleTimeout(proc.args, idle_timeout)
        if early_success_paths and _paths_have_content(early_success_paths):
            if satisfied_since is None:
                satisfied_since = now
            elif now - satisfied_since >= early_success_grace:
                _stop_process_tree(proc)
                return 0, True
        else:
            satisfied_since = None
        time.sleep(1)


def _wrap_opencode_sandbox_command(
    cmd: list[str],
    project_root: str,
    allowed_paths: list[str] | None,
    runtime_dir: str,
) -> tuple[list[str], list[_SandboxMount]]:
    """Envolve o OpenCode em bubblewrap: worktree read-only, allowlist writable."""
    if _env_falsey("FT_OPENCODE_SANDBOX"):
        return cmd, []
    bwrap = shutil.which("bwrap")
    if not bwrap:
        print("  ! FT_OPENCODE_SANDBOX: bwrap não encontrado — seguindo sem sandbox de filesystem.")
        return cmd, []

    mounts = _prepare_opencode_sandbox_mounts(project_root, allowed_paths)
    runtime_path = Path(runtime_dir).resolve()
    runtime_path.mkdir(parents=True, exist_ok=True)

    wrapped = [
        bwrap,
        "--ro-bind", "/", "/",
        "--dev-bind", "/dev", "/dev",
        "--proc", "/proc",
        "--bind", str(runtime_path), str(runtime_path),
    ]
    for mount in mounts:
        wrapped += ["--bind", str(mount.path), str(mount.path)]
    wrapped += cmd
    return wrapped, mounts


def _opencode_runtime_config(
    existing: str | None = None,
    deny_read_paths: list[str] | None = None,
    project_root: str | None = None,
    restrict_tools: bool = False,
    steps: int | None = None,
    model: str | None = None,
    deny_edit_tools: bool = False,
    text_only: bool = False,
) -> str:
    """Config inline para isolar OpenCode no workdir e poupar contexto."""
    config: dict = {}
    if existing:
        try:
            parsed = json.loads(existing)
            if isinstance(parsed, dict):
                config = parsed
        except json.JSONDecodeError:
            config = {}

    permission = config.get("permission")
    if isinstance(permission, str):
        permission = {"*": permission}
    elif not isinstance(permission, dict):
        permission = {}
    permission["external_directory"] = "deny"

    if deny_read_paths:
        read_permission = permission.get("read")
        if isinstance(read_permission, str):
            read_rules = {"*": read_permission}
        elif isinstance(read_permission, dict):
            read_rules = dict(read_permission)
        else:
            read_rules = {}
        read_rules.setdefault("*", "allow")
        read_rules.setdefault("*.env", "deny")
        read_rules.setdefault("*.env.*", "deny")
        read_rules.setdefault("*.env.example", "allow")
        for pattern in _opencode_read_patterns(deny_read_paths, project_root=project_root):
            read_rules[pattern] = "deny"
        permission["read"] = read_rules

    if restrict_tools:
        permission["bash"] = "deny"
        permission["glob"] = "deny"
        permission["grep"] = "deny"
        permission["list"] = "deny"
    if deny_edit_tools:
        permission["edit"] = "deny"
    if text_only:
        permission["bash"] = "deny"
        permission["glob"] = "deny"
        permission["grep"] = "deny"
        permission["list"] = "deny"
        permission["read"] = "deny"
        permission["edit"] = "deny"

    config["permission"] = permission

    if steps is not None:
        agent = config.get("agent")
        if not isinstance(agent, dict):
            agent = {}
        build_agent = agent.get("build")
        if not isinstance(build_agent, dict):
            build_agent = {}
        build_agent["steps"] = steps
        build_agent["maxSteps"] = steps
        agent["build"] = build_agent
        config["agent"] = agent

    effective_model = model or DEFAULT_OPENCODE_MODEL
    context_limit = _env_positive_int("FT_OPENCODE_CONTEXT_LIMIT", "FT_OPENCODE_CONTEXT_WINDOW")
    output_limit = _env_positive_int("FT_OPENCODE_OUTPUT_LIMIT", "FT_OPENCODE_MAX_OUTPUT")
    if effective_model == DEFAULT_OPENCODE_MODEL:
        context_limit = context_limit or DEFAULT_OPENCODE_CONTEXT_LIMIT
        output_limit = output_limit or DEFAULT_OPENCODE_OUTPUT_LIMIT
    if context_limit is not None:
        output_limit = output_limit or DEFAULT_OPENCODE_OUTPUT_LIMIT
        provider_id, _, model_id = effective_model.partition("/")
        if provider_id and model_id:
            providers = config.get("provider")
            if not isinstance(providers, dict):
                providers = {}
            provider = providers.get(provider_id)
            if not isinstance(provider, dict):
                provider = {}
            models = provider.get("models")
            if not isinstance(models, dict):
                models = {}
            model_config = models.get(model_id)
            if not isinstance(model_config, dict):
                model_config = {}
            limit = model_config.get("limit")
            if not isinstance(limit, dict):
                limit = {}
            limit["context"] = context_limit
            limit["output"] = output_limit
            model_config["limit"] = limit
            models[model_id] = model_config
            provider["models"] = models
            providers[provider_id] = provider
            config["provider"] = providers

    compaction = config.get("compaction")
    if not isinstance(compaction, dict):
        compaction = {}
    compaction.update({
        "auto": True,
        "prune": True,
        "reserved": 10000,
    })
    config["compaction"] = compaction

    return json.dumps(config, ensure_ascii=False)


def _executor_env(
    llm_engine: str,
    base_env: dict[str, str] | None = None,
    opencode_deny_read_paths: list[str] | None = None,
    project_root: str | None = None,
    opencode_restrict_tools: bool = False,
    opencode_steps: int | None = None,
    opencode_model: str | None = None,
    opencode_deny_edit_tools: bool = False,
    opencode_text_only: bool = False,
) -> dict[str, str]:
    """Monta env do executor, aplicando hardening específico por provider."""
    env = dict(os.environ if base_env is None else base_env)
    if llm_engine.lower().strip() == "opencode":
        env["OPENCODE_CONFIG_CONTENT"] = _opencode_runtime_config(
            env.get("OPENCODE_CONFIG_CONTENT"),
            deny_read_paths=opencode_deny_read_paths,
            project_root=project_root,
            restrict_tools=opencode_restrict_tools,
            steps=opencode_steps,
            model=opencode_model,
            deny_edit_tools=opencode_deny_edit_tools,
            text_only=opencode_text_only,
        )
    return env


def _feed_stdin(proc: subprocess.Popen, prompt: str) -> None:
    """Escreve o prompt no stdin do executor e fecha o pipe (EOF sinaliza fim)."""
    try:
        assert proc.stdin is not None
        proc.stdin.write(prompt)
        proc.stdin.close()
    except (BrokenPipeError, OSError):
        pass  # executor morreu antes de ler o prompt — o wait() reporta o erro


def _rate_limit_backoff_schedule() -> list[int]:
    """Cronograma de backoff para rate limit, configurável via FT_RATE_LIMIT_BACKOFF."""
    raw = os.environ.get("FT_RATE_LIMIT_BACKOFF", "").strip()
    if raw:
        try:
            schedule = [int(x) for x in raw.split(",") if x.strip()]
            if schedule:
                return schedule
        except ValueError:
            print(f"  ⚠️  FT_RATE_LIMIT_BACKOFF inválido ({raw!r}) — usando cronograma default.")
    return list(_RATE_LIMIT_WAIT)


@dataclass
class DelegateResult:
    success: bool
    output: str
    files_created: list[str]
    files_modified: list[str]
    # True quando a falha foi rate limit da API que persistiu após todo o
    # backoff — o runner NÃO deve tratar como falha de conteúdo (não consome
    # auto-fix; pausa o run para retomada via ft continue).
    rate_limited: bool = False


def _build_executor_command(
    llm_engine: str,
    prompt: str,
    project_root: str,
    max_turns: int,
    model: str | None = None,
) -> list[str]:
    """Monta o comando do executor não-interativo com bypass habilitado."""
    engine = llm_engine.lower().strip()

    if engine == "claude":
        cmd = [
            "claude",
            "--output-format", "stream-json",
            "--verbose",
            "--dangerously-skip-permissions",
            "--max-turns", str(max_turns),
        ]
        if model:
            cmd += ["--model", model]
        cmd += ["-p", prompt]
        return cmd

    if engine == "codex":
        cmd = [
            "codex",
            "exec",
            "--dangerously-bypass-approvals-and-sandbox",
            "--skip-git-repo-check",
            "--json",
            "-C", project_root,
        ]
        if model:
            cmd += ["-m", model]
        cmd.append(prompt)
        return cmd

    if engine == "gemini":
        cmd = ["gemini", "--yolo"]
        if model:
            cmd += ["-m", model]
        cmd += ["-p", prompt]
        return cmd

    if engine == "opencode":
        cmd = [
            "opencode",
            "run",
            "--dir", project_root,
            "-m", model or DEFAULT_OPENCODE_MODEL,
        ]
        if not _env_falsey("FT_OPENCODE_PURE"):
            cmd.append("--pure")
        variant = (os.environ.get("FT_OPENCODE_VARIANT") or "minimal").strip()
        if variant and variant.lower() not in {"0", "false", "no", "off", "none"}:
            cmd += ["--variant", variant]
        debug_enabled = os.environ.get("FT_OPENCODE_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}
        print_logs = debug_enabled or os.environ.get("FT_OPENCODE_PRINT_LOGS", "").strip().lower() in {
            "1", "true", "yes", "on"
        }
        log_level = (os.environ.get("FT_OPENCODE_LOG_LEVEL") or ("DEBUG" if debug_enabled else "")).strip().upper()
        if print_logs:
            cmd.append("--print-logs")
        if log_level:
            cmd += ["--log-level", log_level]
        if os.environ.get("FT_OPENCODE_THINKING", "").strip().lower() in {"1", "true", "yes", "on"}:
            cmd.append("--thinking")
        cmd.append(prompt)
        return cmd

    raise ValueError(f"Executor LLM desconhecido: {llm_engine}")


def _write_log_preamble(log_path: str, llm_engine: str, cmd: list[str], prompt: str) -> None:
    """Escreve cabeçalho útil para inspeção de um step delegado."""
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with path.open("w", encoding="utf-8") as f:
        f.write(f"# LLM Delegate Log\n")
        f.write(f"started_at: {started_at}\n")
        f.write(f"llm_engine: {llm_engine}\n")
        f.write(f"command: {' '.join(cmd)}\n")
        f.write("\n## Prompt\n\n")
        f.write(prompt)
        if not prompt.endswith("\n"):
            f.write("\n")
        f.write("\n## Output\n\n")


def _format_stream_line(llm_engine: str, line: str) -> str:
    """Formata linhas do stream para observação humana no terminal."""
    text = line.rstrip()
    if llm_engine == "claude":
        if not text.startswith("{"):
            return text
        try:
            event = json.loads(text)
        except json.JSONDecodeError:
            return text
        etype = event.get("type", "")
        if etype == "stream_event":
            # Chunks parciais (--include-partial-messages): consumidos por quem
            # agrega (ft log); no stream linha-a-linha sao ruido.
            return ""
        if etype == "assistant":
            msg = event.get("message", {})
            for block in msg.get("content", []):
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "tool_use":
                    return _describe_tool_call(block.get("name", ""), block.get("input", {}))
                if btype == "text":
                    return f"→ {block.get('text', '').strip()[:120]}"
                if btype == "thinking":
                    t = (block.get("thinking") or "").strip()
                    if t:
                        return f"✻ {t[:120]}"
        if etype == "result":
            return f"result: {event.get('result', '')[:80]}"
        return f"event {etype}"
    if llm_engine != "codex":
        return text

    if not text.startswith("{"):
        return text

    try:
        event = json.loads(text)
    except json.JSONDecodeError:
        return text

    event_type = event.get("type", "unknown")
    if event_type == "thread.started":
        return f"event thread.started thread_id={event.get('thread_id')}"
    if event_type == "turn.started":
        return "event turn.started"
    if event_type == "turn.completed":
        usage = event.get("usage", {})
        return (
            "event turn.completed "
            f"input_tokens={usage.get('input_tokens', 0)} "
            f"output_tokens={usage.get('output_tokens', 0)}"
        )
    if event_type == "item.completed":
        item = event.get("item", {})
        item_type = item.get("type")
        if item_type == "agent_message":
            return f"agent_message {item.get('text', '').strip()}"
        return f"item.completed type={item_type}"
    if event_type == "error":
        return f"error {event.get('message', text)}"

    return f"event {event_type}"


def _final_protocol_token(output: str) -> str | None:
    """Último token de protocolo (DONE/BLOCKED) emitido como marcador.

    Só conta o token no início de linha (admitindo decoração markdown leve),
    como o protocolo pede — citar a palavra em prosa NÃO conta. Lição vibeos
    cycle-02: um plano de voo que discutia nodes BLOCKED em prosa era tratado
    como falha pelo antigo `"BLOCKED" in output`. O ÚLTIMO token vence: um
    worker que menciona um bloqueio e encerra com DONE está reportando sucesso.
    """
    token = None
    for m in re.finditer(r"^[\s*_`#>\-]*(DONE|BLOCKED)\b", output, re.MULTILINE):
        token = m.group(1)
    return token


def _extract_codex_output(raw_output: str) -> str:
    """Extrai a resposta final do agent a partir do stream JSONL do Codex."""
    messages: list[str] = []
    errors: list[str] = []

    for line in raw_output.splitlines():
        text = line.strip()
        if not text.startswith("{"):
            continue
        try:
            event = json.loads(text)
        except json.JSONDecodeError:
            continue

        if event.get("type") == "item.completed":
            item = event.get("item", {})
            if item.get("type") == "agent_message" and item.get("text"):
                messages.append(item["text"])
        elif event.get("type") == "error":
            errors.append(json.dumps(event, ensure_ascii=False))

    if messages:
        return "\n\n".join(messages)
    if errors:
        return "\n".join(errors)
    return raw_output


def _extract_opencode_json_text(raw_output: str) -> str:
    """Extrai texto de `opencode run --format json`."""
    messages: list[str] = []
    for line in raw_output.splitlines():
        text = line.strip()
        if not text.startswith("{"):
            continue
        try:
            event = json.loads(text)
        except json.JSONDecodeError:
            continue
        part = event.get("part")
        if not isinstance(part, dict) or part.get("type") != "text":
            continue
        value = part.get("text")
        if isinstance(value, str) and value.strip():
            messages.append(value)
    return "\n".join(messages).strip() or raw_output.strip()


def _clean_opencode_capture_text(text: str) -> str:
    """Remove ruído do OpenCode antes de gravar artifact capturado."""
    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text).strip()
    text = re.sub(r"\n?\[tool_calls\]\s*\(None\)\s*$", "", text).strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 2 and lines[-1].strip() == "```":
            text = "\n".join(lines[1:-1]).strip()
    return text


def _opencode_capture_command(cmd: list[str]) -> list[str]:
    """Força JSON limpo e desliga logs verbosos no modo capture."""
    if not cmd or cmd[0] != "opencode":
        return cmd
    prompt = cmd[-1]
    cleaned: list[str] = []
    skip_next = False
    for arg in cmd[:-1]:
        if skip_next:
            skip_next = False
            continue
        if arg in {"--print-logs", "--thinking"}:
            continue
        if arg in {"--log-level", "--format"}:
            skip_next = True
            continue
        cleaned.append(arg)
    cleaned += ["--format", "json", prompt]
    return cleaned


def _extract_claude_json_output(raw_output: str) -> str:
    """Extrai texto final do stream-json do Claude CLI (uma linha JSON por evento)."""
    # Primeiro tenta pegar o campo result do evento final
    for line in reversed(raw_output.splitlines()):
        text = line.strip()
        if not text.startswith("{"):
            continue
        try:
            event = json.loads(text)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "result":
            result = event.get("result", "")
            if result:
                return result

    # Fallback: concatenar textos de mensagens assistant
    parts: list[str] = []
    for line in raw_output.splitlines():
        text = line.strip()
        if not text.startswith("{"):
            continue
        try:
            event = json.loads(text)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "assistant":
            msg = event.get("message", {})
            for block in msg.get("content", []):
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block["text"])
    if parts:
        return "\n\n".join(parts)
    return raw_output


def _describe_tool_call(name: str, input_data: dict) -> str:
    """Formata uma tool call do Claude em texto curto para display."""
    name_lower = name.lower()
    if name_lower in ("read", "readfile"):
        path = input_data.get("file_path") or input_data.get("path", "")
        return f"Read {path}"
    if name_lower in ("write", "writefile"):
        path = input_data.get("file_path") or input_data.get("path", "")
        return f"Write {path}"
    if name_lower == "edit":
        path = input_data.get("file_path") or input_data.get("path", "")
        return f"Edit {path}"
    if name_lower == "bash":
        cmd = (input_data.get("command") or "")[:60].replace("\n", " ")
        return f"$ {cmd}"
    if name_lower == "glob":
        pat = input_data.get("pattern", "")
        return f"Glob {pat}"
    if name_lower == "grep":
        pat = input_data.get("pattern", "")
        return f"Grep {pat}"
    if name_lower == "notebookedit":
        return "NotebookEdit"
    # Generic
    return f"[{name}]"


def _live_status(llm_engine: str, line: str, ctx: dict) -> str | None:
    """Extrai texto curto para a linha de status ao vivo. Retorna None para linhas sem interesse."""
    text = line.rstrip()
    if llm_engine == "codex":
        if not text.startswith("{"):
            return None
        try:
            event = json.loads(text)
        except json.JSONDecodeError:
            return None
        etype = event.get("type", "")
        if etype == "turn.started":
            ctx["turn"] = ctx.get("turn", 0) + 1
            return f"turn {ctx['turn']}"
        if etype == "item.completed":
            item = event.get("item", {})
            itype = item.get("type", "")
            if itype == "command_execution":
                cmd = (item.get("command") or "")[:60].replace("\n", " ")
                return f"$ {cmd}"
            if itype == "agent_message":
                msg = (item.get("text") or "").strip().replace("\n", " ")[:60]
                return f"→ {msg}" if msg else None
            if itype == "tool_call":
                name = item.get("name") or item.get("tool", "")
                return f"tool {name}"
        if etype == "turn.completed":
            usage = event.get("usage", {})
            tok = usage.get("output_tokens", 0)
            ctx["tokens"] = ctx.get("tokens", 0) + tok
            return f"turn {ctx.get('turn', '?')} done · {ctx['tokens']:,} out tok"
        return None
    elif llm_engine == "claude":
        if not text.startswith("{"):
            return text[:80] if text else None
        try:
            event = json.loads(text)
        except json.JSONDecodeError:
            return text[:80] if text else None
        etype = event.get("type", "")
        if etype == "assistant":
            msg = event.get("message", {})
            for block in msg.get("content", []):
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "tool_use":
                    desc = _describe_tool_call(block.get("name", ""), block.get("input", {}))
                    ctx["last_tool"] = desc
                    return desc
                if btype == "text":
                    snippet = block.get("text", "").strip().replace("\n", " ")[:80]
                    if snippet:
                        return f"→ {snippet}"
        if etype == "result":
            tok = event.get("usage", {}).get("output_tokens", 0) or 0
            ctx["tokens"] = ctx.get("tokens", 0) + tok
            if tok:
                return f"done · {ctx['tokens']:,} out tok"
        return None
    else:
        # Outros engines: plain text
        if text and not text.startswith("["):
            return text[:80]
        return None


_STALL_RECONCILE_SECS = 120.0


def _claude_session_transcript(cwd: str, session_id: str) -> "Path | None":
    """Path do transcript da sessão em ~/.claude/projects/<slug>/<sid>.jsonl.

    Slug do Claude Code: path absoluto do cwd com [/_.] -> "-".
    """
    import re as _re
    if not cwd or not session_id:
        return None
    slug = _re.sub(r"[/_.]", "-", str(Path(cwd).resolve()))
    return Path.home() / ".claude" / "projects" / slug / f"{session_id}.jsonl"


def _transcript_terminal_output(transcript: "Path | None") -> str | None:
    """Se a sessão já terminou segundo o transcript, retorna o texto final; senão None.

    Terminal = último assistant com bloco text, sem tool_use pendente e
    stop_reason end_turn (padrão de reconciliação do sym_doctor).
    """
    if transcript is None or not transcript.exists():
        return None
    try:
        lines = transcript.read_text(errors="replace").splitlines()
    except Exception:
        return None
    for line in reversed(lines[-300:]):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        etype = entry.get("type", "")
        if etype == "result":
            return str(entry.get("result", "")) or None
        if etype != "assistant":
            continue
        msg = entry.get("message", {})
        content = msg.get("content", [])
        if any(isinstance(b, dict) and b.get("type") == "tool_use" for b in content):
            return None  # ainda no meio de tools — não é terminal
        texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        if texts and msg.get("stop_reason") in ("end_turn", "stop_sequence", None):
            return "\n".join(t for t in texts if t).strip() or None
        return None
    return None


def _stream_process_output(
    proc: subprocess.Popen,
    llm_engine: str,
    log_path: str | None = None,
    stream_prefix: str | None = None,
    activity: dict[str, float] | None = None,
) -> str:
    """Consome stdout/stderr combinado do subprocesso, gravando em arquivo e espelhando no terminal."""
    import shutil as _shutil
    import threading
    import select
    chunks: list[str] = []
    stream = proc.stdout
    assert stream is not None

    ctx: dict = {}
    term_width = _shutil.get_terminal_size((80, 20)).columns - 4
    last_status: list[str] = ["aguardando LLM..."]
    printed_status: list[str] = [""]  # último status que gerou uma nova linha
    start_time = time.time()

    def _print_inline(status: str, elapsed: int) -> None:
        """Evento ao vivo: imprime linha permanente no scrollback."""
        ts = time.strftime("%H:%M:%S")
        msg = f"  ⟳ [{ts}] {status} ({elapsed}s)"
        # Limpa a linha do heartbeat (que estava em \r), imprime e avança
        print(f"\r{msg:<{term_width}}", flush=True)
        printed_status[0] = status

    def _print_heartbeat():
        """Heartbeat a cada 10s: atualiza timer in-place na linha atual."""
        while proc.poll() is None:
            elapsed = int(time.time() - start_time)
            status = last_status[0]
            if log_path:
                try:
                    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                    # Ler apenas o que vem após "## Output" — ignora o prompt
                    output_marker = "## Output"
                    idx = content.rfind(output_marker)
                    if idx != -1:
                        output_section = content[idx + len(output_marker):]
                    else:
                        output_section = content[-1024:]
                    def _useful(l: str) -> bool:
                        s = l.strip()
                        if not s or len(s) < 8:
                            return False
                        if s.startswith("#") or s.startswith("---") or s.startswith("==="):
                            return False
                        if s.startswith("```") or s in ("DONE", "BLOCKED"):
                            return False
                        if s.startswith("{"):  # raw JSON line — skip
                            return False
                        return True
                    lines = [l.strip() for l in output_section.splitlines() if _useful(l)]
                    if lines:
                        status = lines[-1][:120]
                except Exception:
                    pass
            ts = time.strftime("%H:%M:%S")
            msg = f"  ⟳ [{ts}] {status} ({elapsed}s)"
            # Atualiza in-place: só sobrescreve a linha corrente sem avançar
            print(f"\r{msg:<{term_width}}", end="", flush=True)
            time.sleep(10)

    log_file = None
    heartbeat = None
    try:
        if log_path:
            log_file = Path(log_path).open("a", encoding="utf-8")

        if not stream_prefix:
            heartbeat = threading.Thread(target=_print_heartbeat, daemon=True)
            heartbeat.start()

        import queue as _queue
        line_q: "_queue.Queue[str | None]" = _queue.Queue()

        def _pump() -> None:
            try:
                for _l in iter(stream.readline, ""):
                    line_q.put(_l)
            finally:
                line_q.put(None)

        pump = threading.Thread(target=_pump, daemon=True)
        pump.start()

        session_meta: dict = {"sid": None, "cwd": None, "saw_result": False}
        last_data = time.time()

        def _reconcile_from_transcript(reason: str) -> str | None:
            """Tenta recuperar o desfecho no transcript da sessão do Claude."""
            if llm_engine != "claude" or session_meta["saw_result"]:
                return None
            tp = _claude_session_transcript(session_meta["cwd"] or "", session_meta["sid"] or "")
            final = _transcript_terminal_output(tp)
            if final is None:
                return None
            synth = json.dumps({
                "type": "assistant",
                "message": {"role": "assistant", "content": [{"type": "text", "text": final}]},
                "ft_reconciled_from": str(tp),
            })
            if log_file:
                log_file.write(f"# ft: reconciliado via transcript ({reason})\n{synth}\n")
                log_file.flush()
            return synth + "\n"

        while True:
            try:
                line = line_q.get(timeout=1.0)
            except _queue.Empty:
                if (time.time() - last_data) >= _STALL_RECONCILE_SECS and proc.poll() is None:
                    synth = _reconcile_from_transcript("pipe sem dados, sessão concluída")
                    if synth is not None:
                        chunks.append(synth)
                        proc.kill()
                        break
                    last_data = time.time()  # não re-checar a cada 1s
                continue
            if line is None:
                # EOF: se o pipe morreu sem evento result, tentar o transcript
                if proc.poll() is None:
                    proc.wait(timeout=30)
                synth = _reconcile_from_transcript("EOF sem result")
                if synth is not None:
                    chunks.append(synth)
                break
            last_data = time.time()
            if activity is not None:
                activity["last"] = last_data
            _stripped_probe = line.strip()
            if _stripped_probe.startswith("{"):
                try:
                    _ev = json.loads(_stripped_probe)
                    _et = _ev.get("type", "")
                    if _et == "system" and _ev.get("subtype") == "init":
                        session_meta["sid"] = _ev.get("session_id")
                        session_meta["cwd"] = _ev.get("cwd")
                    elif _et == "result":
                        session_meta["saw_result"] = True
                except json.JSONDecodeError:
                    pass
            chunks.append(line)
            if log_file:
                log_file.write(line)
                # Para engines JSON (claude stream-json, codex --json),
                # também escreve linha legível logo após o JSON bruto
                if llm_engine in ("claude", "codex"):
                    stripped = line.strip()
                    if stripped.startswith("{"):
                        try:
                            event = json.loads(stripped)
                            etype = event.get("type", "")
                            decoded: str | None = None
                            if llm_engine == "claude":
                                if etype == "assistant":
                                    msg = event.get("message", {})
                                    for block in msg.get("content", []):
                                        if not isinstance(block, dict):
                                            continue
                                        btype = block.get("type")
                                        if btype == "tool_use":
                                            decoded = _describe_tool_call(
                                                block.get("name", ""), block.get("input", {})
                                            )
                                            break
                                        if btype == "text":
                                            t = block.get("text", "").strip().replace("\n", " ")
                                            if t:
                                                decoded = f"→ {t[:120]}"
                                            break
                                elif etype == "result":
                                    tok = event.get("usage", {}).get("output_tokens", 0) or 0
                                    if tok:
                                        decoded = f"done · {tok:,} output tokens"
                            else:  # codex
                                if etype == "item.completed":
                                    item = event.get("item", {})
                                    itype = item.get("type", "")
                                    if itype == "command_execution":
                                        cmd_text = (item.get("command") or "")[:80].replace("\n", " ")
                                        decoded = f"$ {cmd_text}"
                                    elif itype == "agent_message":
                                        msg_text = (item.get("text") or "").strip().replace("\n", " ")[:120]
                                        if msg_text:
                                            decoded = f"→ {msg_text}"
                                    elif itype == "tool_call":
                                        decoded = f"tool {item.get('name') or item.get('tool', '')}"
                                elif etype == "turn.completed":
                                    usage = event.get("usage", {})
                                    tok = usage.get("output_tokens", 0)
                                    if tok:
                                        decoded = f"done · {tok:,} output tokens"
                            if decoded:
                                log_file.write(f"{decoded}\n")
                        except (json.JSONDecodeError, Exception):
                            pass
                log_file.flush()
            if stream_prefix:
                print(f"  {stream_prefix} {_format_stream_line(llm_engine, line)}")
            else:
                # Atualiza last_status com qualquer linha não-vazia do LLM
                stripped = line.strip()
                if stripped and not stripped.startswith("{"):
                    last_status[0] = stripped[:120]
                status = _live_status(llm_engine, line, ctx)
                if status:
                    status = status[:120]
                    last_status[0] = status
                    elapsed = int(time.time() - start_time)
                    # Inline: sempre nova linha — cada ação fica visível no scrollback
                    if status != printed_status[0]:
                        _print_inline(status, elapsed)
    finally:
        if log_file:
            log_file.close()
        if not stream_prefix:
            # Limpa a linha de status ao terminar
            print(f"\r{' ' * (term_width)}\r", end="", flush=True)

    return "".join(chunks)


def delegate_to_llm(
    task: str,
    project_root: str = ".",
    allowed_paths: list[str] | None = None,
    max_turns: int = 50,
    llm_engine: str = "claude",
    llm_model: str | None = None,
    log_path: str | None = None,
    stream_prefix: str | None = None,
    opencode_deny_read_paths: list[str] | None = None,
    opencode_restrict_tools: bool = False,
    opencode_steps: int | None = None,
    opencode_deny_edit_tools: bool = False,
    opencode_early_success_paths: list[str] | None = None,
    opencode_capture_output_path: str | None = None,
) -> DelegateResult:
    """
    Chama o executor LLM configurado como subprocesso para executar uma tarefa de construcao.

    O LLM recebe um prompt restritivo: so pode escrever nos paths permitidos,
    nao pode editar ft_state.yml, nao pode tomar decisoes de processo.
    """
    paths_str = ", ".join(allowed_paths) if allowed_paths else "src/, tests/, docs/"
    opencode_capture_mode = bool(
        llm_engine.lower().strip() == "opencode" and opencode_capture_output_path
    )
    deny_reads = list(dict.fromkeys(opencode_deny_read_paths or []))
    deny_reads_rule = ""
    if deny_reads:
        deny_reads_rule = (
            "\n- NAO use Read/Grep/Glob nestes arquivos ja resumidos no prompt: "
            f"{', '.join(deny_reads)}. Esses reads serao bloqueados para poupar contexto."
        )
    restricted_tools_rule = ""
    if opencode_restrict_tools:
        restricted_tools_rule = (
            "\n- NAO use shell/bash/list/grep/glob. Escreva o arquivo de saida "
            "diretamente usando apenas o contexto presente no prompt."
            "\n- Para OpenCode em modo restrito, sua PRIMEIRA tool call deve ser "
            "Write/Edit/Patch no arquivo de saida esperado. NAO use Read antes "
            "da primeira escrita; se faltar detalhe, produza um best-effort "
            "conciso com o contexto injetado."
        )

    completion_rule = (
        "- Quando terminar, diga DONE e liste os arquivos criados/modificados\n"
        "- Se encontrar um problema que nao consegue resolver, diga BLOCKED e explique o motivo\n"
        "- ANTES do DONE, emita um bloco NODE_SUMMARY (max 10 linhas) neste formato:\n"
        "NODE_SUMMARY:\n"
        "- fiz: <o que foi feito, 1-2 linhas>\n"
        "- decisoes: <decisoes tomadas e porque, se houver>\n"
        "- verificado: <o que voce RODOU e confirmou funcionando>\n"
        "- assumido: <o que voce assumiu SEM testar, se houver>\n"
        "- armadilhas: <pegadinhas que o proximo node precisa saber, se houver>"
    )
    if opencode_capture_mode:
        write_tool_rule = (
            f"- NAO use ferramentas. NAO use Read, Glob, Grep, List, Bash, Write, Edit ou Patch.\n"
            f"- Responda SOMENTE com o conteudo completo que deve ser gravado em "
            f"{opencode_capture_output_path}.\n"
            "- Nao inclua cercas de codigo markdown envolvendo o documento.\n"
            "- O engine gravara o arquivo no path permitido depois da sua resposta."
        )
        completion_rule = (
            "- Se nao conseguir produzir o documento, responda apenas: BLOCKED: <motivo>.\n"
            "- Caso contrario, nao inclua DONE, NODE_SUMMARY ou lista de arquivos; "
            "retorne apenas o conteudo final do documento."
        )
    elif opencode_deny_edit_tools:
        write_tool_rule = (
            "- OBRIGATORIO: antes de dizer DONE, use Bash para criar ou modificar "
            "cada arquivo de saida esperado. NAO use Write/Edit/Patch neste node; "
            "o OpenCode pode corromper nomes de arquivos quando escreve codigo/JSON por edit.\n"
            "- Para criar arquivos, use comandos independentes com paths explicitos, por exemplo: "
            "`mkdir -p project/frontend && cat > project/frontend/package.json <<'EOF' ... EOF`. "
            "Nao dependa de `cd` persistente entre comandos.\n"
            "- Se o contrato pedir `project/...`, crie somente paths abaixo de `project/`; "
            "nao crie `frontend/`, `backend/`, `src/` ou outros diretorios de produto na raiz."
        )
    else:
        placeholder_rule = ""
        if llm_engine.lower().strip() == "opencode":
            placeholder_rule = (
                "- Se um arquivo de saida ja existir vazio, trate-o como placeholder "
                "do sandbox. Nao leia esse arquivo antes de escrever; sobrescreva-o "
                "com Write/Edit/Patch.\n"
            )
        write_tool_rule = (
            placeholder_rule
            +
            "- OBRIGATORIO: antes de dizer DONE, use uma ferramenta de escrita\n"
            "  (Write/Edit/Patch) para criar ou modificar cada arquivo de saida esperado.\n"
            "  Nao declare que um arquivo foi criado sem antes executar a escrita real."
        )

    prompt = f"""Voce e um executor de construcao. Sua unica tarefa:

{task}

REGRAS:
- DIRETORIO DE TRABALHO: {project_root} — todo o seu trabalho acontece DENTRO dele,
  com paths RELATIVOS. NUNCA leia ou escreva fora dele (nem em outros checkouts do
  mesmo projeto), exceto paths absolutos explicitamente listados abaixo. Se algum
  documento citar um caminho absoluto fora do diretorio de trabalho, IGNORE o caminho
  e use o equivalente relativo local.
- Escreva APENAS nos paths permitidos: {paths_str}
- Use o CONTEXTO EXISTENTE do prompt como fonte primaria. Evite reler arquivos
  markdown grandes que ja apareceram no prompt; se precisar de um detalhe,
  busque apenas o trecho minimo necessario dentro do diretorio de trabalho.
{deny_reads_rule}
{restricted_tools_rule}
- NAO edite ft_state.yml ou qualquer arquivo de estado do motor
- NAO tome decisoes sobre o processo (o motor decide)
{write_tool_rule}
{completion_rule}
"""

    cmd = _build_executor_command(llm_engine, prompt, project_root, max_turns, model=llm_model)
    if opencode_capture_mode:
        cmd = _opencode_capture_command(cmd)

    # Linux limita cada argumento de execve a ~128 KiB (MAX_ARG_STRLEN).
    # Prompts hyper-mode estouram isso ([Errno 7] Argument list too long) —
    # acima do limiar, o prompt sai do argv e vai via stdin (o Claude CLI lê
    # o prompt de stdin quando -p vem sem argumento).
    stdin_prompt: str | None = None
    if (
        llm_engine.lower().strip() == "claude"
        and cmd
        and cmd[-1] == prompt
        and len(prompt.encode("utf-8")) > _MAX_ARGV_PROMPT_BYTES
    ):
        cmd = cmd[:-1]  # mantém o -p final; prompt segue via stdin
        stdin_prompt = prompt
        print(f"  ⚠️  Prompt grande ({len(prompt) // 1024} KiB) — enviando via stdin.")

    _env = _executor_env(
        llm_engine,
        opencode_deny_read_paths=deny_reads,
        project_root=project_root,
        opencode_restrict_tools=opencode_restrict_tools,
        opencode_steps=opencode_steps,
        opencode_model=llm_model or DEFAULT_OPENCODE_MODEL,
        opencode_deny_edit_tools=opencode_deny_edit_tools,
        opencode_text_only=opencode_capture_mode,
    )
    sandbox_tmp: tempfile.TemporaryDirectory | None = None
    sandbox_mounts: list[_SandboxMount] = []
    if llm_engine.lower().strip() == "opencode" and not _env_falsey("FT_OPENCODE_SANDBOX"):
        sandbox_tmp = tempfile.TemporaryDirectory(prefix="ft-opencode-")
        runtime = Path(sandbox_tmp.name)
        for dirname in ("data", "cache", "state", "tmp", "npm-cache"):
            (runtime / dirname).mkdir(parents=True, exist_ok=True)
        _env = dict(_env)
        _env.setdefault("XDG_DATA_HOME", str(runtime / "data"))
        _env.setdefault("XDG_CACHE_HOME", str(runtime / "cache"))
        _env.setdefault("XDG_STATE_HOME", str(runtime / "state"))
        _env.setdefault("TMPDIR", str(runtime / "tmp"))
        _env.setdefault("npm_config_cache", str(runtime / "npm-cache"))
        cmd, sandbox_mounts = _wrap_opencode_sandbox_command(
            cmd,
            project_root=project_root,
            allowed_paths=[] if opencode_capture_mode else allowed_paths,
            runtime_dir=sandbox_tmp.name,
        )
    early_success_paths = _resolve_existing_file_paths(project_root, opencode_early_success_paths)
    early_success_grace = _env_positive_int("FT_OPENCODE_EARLY_SUCCESS_GRACE") or 20

    if log_path and llm_engine != "codex":
        _write_log_preamble(log_path, llm_engine, cmd, prompt)
    elif log_path:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)

    idle_timeout = None
    idle_retries = 0
    if llm_engine.lower().strip() == "opencode":
        idle_timeout = _env_positive_int("FT_OPENCODE_IDLE_TIMEOUT") or 480
        idle_retries = _env_positive_int("FT_OPENCODE_IDLE_RETRIES") or 2

    cleaned_runtime = False

    def _cleanup_delegate_runtime() -> None:
        nonlocal cleaned_runtime, sandbox_tmp
        if cleaned_runtime:
            return
        _cleanup_empty_placeholders(sandbox_mounts)
        if sandbox_tmp is not None:
            sandbox_tmp.cleanup()
            sandbox_tmp = None
        cleaned_runtime = True

    def _append_log(message: str) -> None:
        if log_path:
            with Path(log_path).open("a", encoding="utf-8") as f:
                f.write(message)

    def _stop_process(proc: subprocess.Popen) -> None:
        _stop_process_tree(proc)

    def _run_executor_attempt() -> tuple[int, bool, str, str | None]:
        """Executa uma tentativa do executor. failure_kind: idle | timeout | None."""
        # Chamar executor em modo nao-interativo, com streaming para arquivo.
        # PATH completo: o template v3 tem frontend Node (npm/vite) — a poda antiga
        # de nvm/node quebrava os nodes de frontend (worker sem npm reporta BLOCKED).
        proc = subprocess.Popen(
            cmd,
            cwd=project_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE if stdin_prompt is not None else None,
            text=True,
            bufsize=1,
            env=_env,
            start_new_session=True,
        )
        output_holder: dict[str, str] = {"output": ""}
        activity = {"last": time.time()}
        reader = threading.Thread(
            target=lambda: output_holder.__setitem__(
                "output",
                _stream_process_output(
                    proc,
                    llm_engine=llm_engine,
                    log_path=log_path,
                    stream_prefix=stream_prefix,
                    activity=activity,
                ),
            ),
            daemon=True,
        )
        reader.start()

        # Alimentar stdin só depois do reader ativo: o reader drena o stdout do
        # filho enquanto escrevemos, evitando deadlock de pipes cheios.
        if stdin_prompt is not None:
            _feed_stdin(proc, stdin_prompt)

        try:
            returncode, early_success = _wait_for_process(
                proc,
                timeout=1800,
                early_success_paths=early_success_paths,
                early_success_grace=early_success_grace,
                activity=activity,
                idle_timeout=idle_timeout,
            )
        except ExecutorIdleTimeout:
            _stop_process(proc)
            reader.join(timeout=5)
            msg = f"\n[IDLE_TIMEOUT] Executor sem nova saída por {idle_timeout} segundos.\n"
            _append_log(msg)
            return 124, False, output_holder["output"] + msg, "idle"
        except subprocess.TimeoutExpired:
            _stop_process(proc)
            reader.join(timeout=5)
            msg = "\n[TIMEOUT] Executor excedeu 1800 segundos.\n"
            _append_log(msg)
            return 124, False, output_holder["output"] + msg, "timeout"
        except BaseException:
            _stop_process(proc)
            reader.join(timeout=5)
            raise

        reader.join(timeout=5)
        early_success_msg = ""
        if early_success:
            early_success_msg = (
                "\n[EARLY_SUCCESS] Outputs esperados existem; encerrando OpenCode "
                "para validação determinística.\n"
            )
            _append_log(early_success_msg)
        return returncode, early_success, output_holder["output"] + early_success_msg, None

    def _extract_output(raw: str, engine: str) -> str:
        if opencode_capture_mode:
            return _extract_opencode_json_text(raw)
        if engine == "codex":
            return _extract_codex_output(raw)
        if engine == "claude":
            return _extract_claude_json_output(raw)
        return raw

    try:
        idle_attempt = 0
        while True:
            returncode, _early_success, raw_output, failure_kind = _run_executor_attempt()
            if failure_kind == "idle" and idle_attempt < idle_retries:
                idle_attempt += 1
                retry_msg = (
                    f"\n[IDLE_RETRY] Retentando OpenCode apos inatividade "
                    f"({idle_attempt}/{idle_retries}).\n"
                )
                print(f"  ! OpenCode sem saída nova; retry {idle_attempt}/{idle_retries}")
                _append_log(retry_msg)
                continue
            if failure_kind:
                _cleanup_delegate_runtime()
                return DelegateResult(
                    success=False,
                    output=_extract_output(raw_output, llm_engine),
                    files_created=[],
                    files_modified=[],
                )
            break

        output = _extract_output(raw_output, llm_engine)

        # Detectar rate limit e fazer retry com backoff exponencial
        if _RATE_LIMIT_PATTERNS.search(output):
            _backoff_schedule = _rate_limit_backoff_schedule()
            for attempt, wait in enumerate(_backoff_schedule, start=1):
                print(f"\n  ⚠️  Rate limit detectado ({llm_engine}). "
                      f"Aguardando {wait}s antes da tentativa {attempt}/{len(_backoff_schedule)}…")
                time.sleep(wait)
                rc2, _early_success2, raw2, failure2 = _run_executor_attempt()
                out2 = _extract_output(raw2, llm_engine)
                if failure2:
                    output = out2
                    returncode = rc2
                    break
                if not _RATE_LIMIT_PATTERNS.search(out2):
                    output = out2
                    returncode = rc2
                    break
                output = out2  # última tentativa falhou também

        token = _final_protocol_token(output)
        success = returncode == 0 and token != "BLOCKED"
        rate_limited = (not success) and bool(_RATE_LIMIT_PATTERNS.search(output))
        if success and opencode_capture_mode and opencode_capture_output_path:
            captured = _clean_opencode_capture_text(output)
            if not captured:
                success = False
                output = f"{output}\n[CAPTURE_EMPTY] OpenCode nao retornou conteudo gravavel."
            else:
                root = Path(project_root).resolve()
                target = (root / opencode_capture_output_path).resolve()
                if not _path_relative_to(target, root):
                    success = False
                    output = f"{output}\n[CAPTURE_PATH_INVALID] Path fora do projeto: {opencode_capture_output_path}"
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(captured.rstrip() + "\n", encoding="utf-8")
                    output = f"DONE\nArquivo gravado pelo engine: {opencode_capture_output_path}\n"
        _cleanup_delegate_runtime()
    except BaseException:
        _cleanup_delegate_runtime()
        raise

    # Extrair arquivos criados/modificados do git status
    git_result = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"],
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    modified = git_result.stdout.strip().splitlines() if git_result.stdout.strip() else []

    git_untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    created = git_untracked.stdout.strip().splitlines() if git_untracked.stdout.strip() else []

    return DelegateResult(
        success=success,
        output=output,
        files_created=created,
        files_modified=modified,
        rate_limited=rate_limited,
    )


def delegate_with_feedback(
    original_task: str,
    feedback: str,
    project_root: str = ".",
    allowed_paths: list[str] | None = None,
    llm_engine: str = "claude",
    llm_model: str | None = None,
    max_turns: int = 50,
    log_path: str | None = None,
    stream_prefix: str | None = None,
    opencode_deny_read_paths: list[str] | None = None,
    opencode_restrict_tools: bool = False,
    opencode_steps: int | None = None,
    opencode_deny_edit_tools: bool = False,
    opencode_early_success_paths: list[str] | None = None,
    opencode_capture_output_path: str | None = None,
) -> DelegateResult:
    """Re-delega com feedback especifico dos validadores."""
    retry_task = f"""TAREFA ORIGINAL:
{original_task}

RESULTADO DA VALIDACAO (FALHOU):
{feedback}

CORRIJA especificamente os itens que falharam.
Nao modifique o que ja esta funcionando."""

    return delegate_to_llm(
        task=retry_task,
        project_root=project_root,
        allowed_paths=allowed_paths,
        llm_engine=llm_engine,
        llm_model=llm_model,
        max_turns=max_turns,
        log_path=log_path,
        stream_prefix=stream_prefix,
        opencode_deny_read_paths=opencode_deny_read_paths,
        opencode_restrict_tools=opencode_restrict_tools,
        opencode_steps=opencode_steps,
        opencode_deny_edit_tools=opencode_deny_edit_tools,
        opencode_early_success_paths=opencode_early_success_paths,
        opencode_capture_output_path=opencode_capture_output_path,
    )
