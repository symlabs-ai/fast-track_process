"""Exploração LLM avulsa, progressiva e sem permissão de escrita no projeto.

O protocolo de transporte fica no CLI: este módulo recebe chunks de texto já
normalizados por ``on_chunk`` e retorna o resultado/exit code do executor. A
granularidade depende do provider: Claude e Gemini expõem deltas; Codex e
OpenCode expõem mensagens/parts conforme suas CLIs as concluem.
"""

from __future__ import annotations

from dataclasses import dataclass
import io
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from typing import Callable

from ft.engine.delegate import (
    DEFAULT_PROGRESS_PROBE_INTERVAL,
    ExecutorIdleTimeout,
    _env_positive_int,
    _executor_idle_grace_seconds,
    _executor_idle_timeout_seconds,
    _executor_max_wall_timeout_seconds,
    _wait_for_process,
    _workspace_progress_paths,
    _workspace_progress_snapshot,
)


SUPPORTED_AGENTS = {"claude", "codex", "gemini", "opencode"}
DEFAULT_TIMEOUT_SECONDS = 1_800


class ExploreConfigurationError(ValueError):
    """Seleção/provider não permite uma execução read-only segura."""


@dataclass(frozen=True)
class ExploreResult:
    returncode: int
    text: str
    error: str | None = None
    session_id: str | None = None
    session_resumed: bool = False
    usage: dict[str, int] | None = None
    cost_usd: float | None = None


def _normalized_effort(effort: str | None) -> str | None:
    if effort is None:
        return None
    value = str(effort).strip()
    if not value or value.lower() == "default":
        return None
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ExploreConfigurationError("effort contém valor inválido")
    return value


def _normalized_session_id(session_id: str | None) -> str | None:
    if session_id is None:
        return None
    value = str(session_id).strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}", value):
        raise ExploreConfigurationError("session id contém valor inválido")
    return value


def _normalized_usage(value: object) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None

    def count(key: str, fallback: str | None = None) -> int:
        raw = value.get(key, value.get(fallback, 0) if fallback else 0)
        try:
            return max(0, int(raw or 0))
        except (TypeError, ValueError):
            return 0

    input_details = value.get("input_tokens_details")
    output_details = value.get("output_tokens_details")
    cached = count("cached_input_tokens")
    reasoning = count("reasoning_output_tokens")
    if isinstance(input_details, dict):
        try:
            cached = max(cached, int(input_details.get("cached_tokens") or 0))
        except (TypeError, ValueError):
            pass
    if isinstance(output_details, dict):
        try:
            reasoning = max(reasoning, int(output_details.get("reasoning_tokens") or 0))
        except (TypeError, ValueError):
            pass
    usage = {
        "input_tokens": count("input_tokens", "prompt_tokens"),
        "cached_input_tokens": max(0, cached),
        "output_tokens": count("output_tokens", "completion_tokens"),
        "reasoning_output_tokens": max(0, reasoning),
        "total_tokens": count("total_tokens"),
    }
    if not usage["total_tokens"]:
        usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
    return usage


def _read_only_prompt(request: str) -> str:
    return (
        "MODO EXPLORE READ-ONLY. Responda ao pedido do usuário em Markdown seguro. "
        "Você pode inspecionar o projeto, mas não pode criar, editar, apagar, mover "
        "ou formatar arquivos, executar comandos mutáveis, alterar Git/estado do FT "
        "nem iniciar serviços. Se uma mudança for solicitada, explique ou proponha "
        "a mudança sem aplicá-la.\n\nPedido:\n" + request
    )


def build_read_only_command(
    *,
    agent: str,
    prompt: str,
    project_root: str | Path,
    model: str | None = None,
    effort: str | None = None,
    persist_session: bool = False,
    resume_session: str | None = None,
) -> list[str]:
    """Monta a CLI nativa com a política read-only mais forte do provider."""

    selected = str(agent).strip().lower()
    if selected not in SUPPORTED_AGENTS:
        raise ExploreConfigurationError(f"agent desconhecido: {agent}")
    root = str(Path(project_root).resolve())
    normalized_effort = _normalized_effort(effort)
    normalized_session = _normalized_session_id(resume_session)
    task = _read_only_prompt(prompt)

    if (persist_session or normalized_session) and selected != "codex":
        raise ExploreConfigurationError(
            "sessão persistente standalone está disponível somente para Codex"
        )

    if selected == "claude":
        command = [
            "claude",
            "--output-format", "stream-json",
            "--include-partial-messages",
            "--verbose",
            "--permission-mode", "plan",
            "--allowedTools", "Read,Glob,Grep",
            "--no-session-persistence",
        ]
        if model:
            command += ["--model", str(model)]
        if normalized_effort:
            command += ["--effort", normalized_effort]
        command += ["-p", task]
        return command

    if selected == "codex":
        if normalized_session:
            command = [
                "codex", "exec", "resume",
                "-c", 'sandbox_mode="read-only"',
                "--skip-git-repo-check", "--json",
            ]
        else:
            command = [
                "codex", "exec",
                "--sandbox", "read-only",
                "--skip-git-repo-check",
            ]
            if not persist_session:
                command.append("--ephemeral")
            command += ["--json", "-C", root]
        if normalized_effort:
            command += ["-c", f"model_reasoning_effort={json.dumps(normalized_effort)}"]
        if model:
            command += ["-m", str(model)]
        if normalized_session:
            command.append(normalized_session)
        command.append(task)
        return command

    if selected == "gemini":
        command = [
            "gemini",
            "--approval-mode", "plan",
            "--output-format", "stream-json",
        ]
        if model:
            command += ["--model", str(model)]
        # A CLI Gemini atual não anuncia um flag de effort. O valor continua
        # aceito pelo contrato comum, mas não é inventado como argumento.
        command += ["--prompt", task]
        return command

    command = [
        "opencode", "run",
        "--dir", root,
        "--pure",
        "--format", "json",
    ]
    if model:
        command += ["--model", str(model)]
    if normalized_effort:
        command += ["--variant", normalized_effort]
    command.append(task)
    return command


class ExploreStreamNormalizer:
    """Converte streams heterogêneos em chunks de texto sem duplicar finais."""

    def __init__(self, agent: str):
        self.agent = agent.strip().lower()
        self.parts: list[str] = []
        self.provider_error: str | None = None
        self._claude_saw_delta = False
        self._fallback: str | None = None
        self.session_id: str | None = None
        self.usage: dict[str, int] | None = None
        self.cost_usd: float | None = None

    @property
    def text(self) -> str:
        return "".join(self.parts)

    def _append(self, value: object, *, separator: str = "") -> list[str]:
        if not isinstance(value, str) or not value:
            return []
        chunk = (separator if self.parts else "") + value
        self.parts.append(chunk)
        return [chunk]

    def feed(self, raw_line: str) -> list[str]:
        line = raw_line.strip()
        if not line.startswith("{"):
            return []
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return []
        if not isinstance(event, dict):
            return []

        event_type = str(event.get("type") or "")
        if self.agent == "codex" and event_type == "thread.started":
            thread_id = event.get("thread_id")
            if isinstance(thread_id, str) and thread_id.strip():
                self.session_id = thread_id.strip()
        usage = _normalized_usage(event.get("usage"))
        if usage is not None:
            self.usage = usage
        raw_cost = event.get("total_cost_usd")
        if isinstance(raw_cost, (int, float)) and raw_cost >= 0:
            self.cost_usd = float(raw_cost)
        if event_type == "error":
            self.provider_error = str(
                event.get("message") or event.get("error") or "provider retornou erro"
            )
            return []

        if self.agent == "claude":
            if event_type == "stream_event":
                inner = event.get("event") or {}
                delta = inner.get("delta") or {} if isinstance(inner, dict) else {}
                if (
                    isinstance(delta, dict)
                    and delta.get("type") == "text_delta"
                    and isinstance(delta.get("text"), str)
                ):
                    self._claude_saw_delta = True
                    return self._append(delta["text"])
            elif event_type == "assistant" and not self._claude_saw_delta:
                message = event.get("message") or {}
                content = message.get("content") or [] if isinstance(message, dict) else []
                chunks: list[str] = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        chunks.extend(self._append(block.get("text"), separator="\n\n"))
                return chunks
            elif event_type == "result":
                if event.get("is_error"):
                    self.provider_error = str(event.get("result") or "Claude retornou erro")
                elif isinstance(event.get("result"), str):
                    self._fallback = event["result"]
            return []

        if self.agent == "codex":
            if event_type == "item.completed":
                item = event.get("item") or {}
                if isinstance(item, dict) and item.get("type") == "agent_message":
                    return self._append(item.get("text"), separator="\n\n")
            return []

        if self.agent == "gemini":
            if event_type == "message" and event.get("role") == "assistant":
                value = event.get("content")
                if value is None:
                    value = event.get("text")
                return self._append(value)
            if event_type == "result":
                status = str(event.get("status") or "").lower()
                if status and status not in {"success", "ok", "completed"}:
                    self.provider_error = str(event.get("error") or event.get("message") or status)
                for key in ("response", "text", "result"):
                    if isinstance(event.get(key), str):
                        self._fallback = event[key]
                        break
            return []

        part = event.get("part")
        if isinstance(part, dict) and part.get("type") == "text":
            return self._append(part.get("text"))
        return []

    def finish(self) -> list[str]:
        if self.parts or not self._fallback:
            return []
        return self._append(self._fallback)


def _timeout_seconds() -> int:
    """Resolve the legacy explore value as an inactivity-window hint."""
    raw = os.environ.get("FT_EXPLORE_TIMEOUT", "").strip()
    if raw:
        try:
            value = int(raw)
        except ValueError as exc:
            raise ExploreConfigurationError("FT_EXPLORE_TIMEOUT deve ser inteiro") from exc
        if value <= 0:
            raise ExploreConfigurationError("FT_EXPLORE_TIMEOUT deve ser positivo")
        return value
    return DEFAULT_TIMEOUT_SECONDS


def _opencode_environment(runtime: Path) -> dict[str, str]:
    env = dict(os.environ)
    for name in ("data", "cache", "state", "tmp", "npm-cache"):
        (runtime / name).mkdir(parents=True, exist_ok=True)
    env["XDG_DATA_HOME"] = str(runtime / "data")
    env["XDG_CACHE_HOME"] = str(runtime / "cache")
    env["XDG_STATE_HOME"] = str(runtime / "state")
    env["TMPDIR"] = str(runtime / "tmp")
    env["npm_config_cache"] = str(runtime / "npm-cache")

    config: dict = {}
    existing = env.get("OPENCODE_CONFIG_CONTENT", "")
    if existing:
        try:
            parsed = json.loads(existing)
            if isinstance(parsed, dict):
                config = parsed
        except json.JSONDecodeError:
            pass
    permission = config.get("permission")
    permission = dict(permission) if isinstance(permission, dict) else {}
    permission.update({
        "external_directory": "deny",
        "edit": "deny",
        "bash": "deny",
    })
    config["permission"] = permission
    env["OPENCODE_CONFIG_CONTENT"] = json.dumps(config, ensure_ascii=False)
    return env


def _wrap_opencode_read_only(command: list[str], runtime: Path) -> list[str]:
    bwrap = shutil.which("bwrap")
    if not bwrap:
        raise ExploreConfigurationError(
            "OpenCode standalone read-only exige bubblewrap (bwrap)"
        )
    return [
        bwrap,
        "--ro-bind", "/", "/",
        "--dev-bind", "/dev", "/dev",
        "--proc", "/proc",
        "--bind", str(runtime), str(runtime),
        *command,
    ]


def _honest_returncode(returncode: int | None) -> int:
    if returncode is None:
        return 1
    if returncode < 0:
        return 128 + abs(returncode)
    return returncode


def _descendant_pids(parent_pid: int) -> list[int]:
    """Snapshot best-effort dos descendentes para o timeout interno.

    O provider fica no mesmo process group do ``ft`` para que um supervisor
    externo (como o backend da UI) possa cancelar e recolher toda a árvore com
    ``killpg(ft_pid)``. Como consequência, o timeout interno não pode matar o
    grupo sem matar o próprio CLI; neste caminho usamos /proc para alcançar os
    descendentes antes de terminar o provider.
    """

    proc_root = Path("/proc")
    if not proc_root.is_dir():
        return []
    children: dict[int, list[int]] = {}
    for candidate in proc_root.iterdir():
        if not candidate.name.isdigit():
            continue
        try:
            status = (candidate / "status").read_text(
                encoding="utf-8", errors="replace"
            )
        except OSError:
            continue
        match = re.search(r"(?m)^PPid:\s+(\d+)\s*$", status)
        if not match:
            continue
        children.setdefault(int(match.group(1)), []).append(int(candidate.name))

    descendants: list[int] = []
    pending = list(children.get(parent_pid, []))
    while pending:
        pid = pending.pop()
        descendants.append(pid)
        pending.extend(children.get(pid, []))
    return descendants


def _stop_process(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    descendants = _descendant_pids(proc.pid)
    for pid in reversed(descendants):
        try:
            os.kill(pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass
    try:
        proc.terminate()
    except (AttributeError, OSError):
        pass
    try:
        proc.wait(timeout=2)
    except (AttributeError, subprocess.TimeoutExpired):
        pass
    for pid in reversed(descendants):
        try:
            os.kill(pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
    try:
        proc.kill()
    except (AttributeError, OSError):
        pass


def run_read_only_explore(
    *,
    request: str,
    project_root: str | Path,
    agent: str,
    model: str | None = None,
    effort: str | None = None,
    persist_session: bool = False,
    resume_session: str | None = None,
    on_chunk: Callable[[str], None] | None = None,
) -> ExploreResult:
    """Executa uma consulta standalone e entrega chunks à medida que chegam."""

    root = Path(project_root).resolve()
    command = build_read_only_command(
        agent=agent,
        prompt=request,
        project_root=root,
        model=model,
        effort=effort,
        persist_session=persist_session,
        resume_session=resume_session,
    )
    selected = agent.strip().lower()
    runtime: tempfile.TemporaryDirectory[str] | None = None
    env = dict(os.environ)
    try:
        if selected == "opencode":
            runtime = tempfile.TemporaryDirectory(prefix="ft-explore-")
            runtime_path = Path(runtime.name).resolve()
            env = _opencode_environment(runtime_path)
            command = _wrap_opencode_read_only(command, runtime_path)

        try:
            proc = subprocess.Popen(
                command,
                cwd=root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=env,
                # Deliberadamente herda o process group do `ft`: o backend da
                # UI cria `ft` como session leader e cancela via killpg(ft_pid).
                # Separar o provider aqui o deixaria órfão no cancel/timeout.
                start_new_session=False,
            )
        except FileNotFoundError as exc:
            return ExploreResult(127, "", f"executor não encontrado: {exc.filename}")
        except OSError as exc:
            return ExploreResult(126, "", f"não foi possível iniciar executor: {exc}")

        assert proc.stdout is not None
        assert proc.stderr is not None
        normalizer = ExploreStreamNormalizer(selected)
        stderr_parts: list[str] = []
        activity = {"last": time.time(), "started": time.time()}

        def pump_stdout(stream: io.TextIOBase) -> None:
            for line in iter(stream.readline, ""):
                activity["last"] = time.time()
                activity.setdefault("first", activity["last"])
                for chunk in normalizer.feed(line):
                    if on_chunk is not None:
                        on_chunk(chunk)

        def pump_stderr(stream: io.TextIOBase) -> None:
            for line in iter(stream.readline, ""):
                # Debug/progress logs do not become user text, but they are an
                # observable liveness signal just like stdout.
                activity["last"] = time.time()
                activity.setdefault("first", activity["last"])
                stderr_parts.append(line)

        stdout_thread = threading.Thread(target=pump_stdout, args=(proc.stdout,), daemon=True)
        stderr_thread = threading.Thread(target=pump_stderr, args=(proc.stderr,), daemon=True)
        stdout_thread.start()
        stderr_thread.start()

        idle_timeout = _executor_idle_timeout_seconds(
            selected,
            _timeout_seconds(),
        )
        max_wall_timeout = _executor_max_wall_timeout_seconds(selected)
        workspace_paths = _workspace_progress_paths(str(root), None)
        while True:
            try:
                returncode, _early = _wait_for_process(
                    proc,
                    timeout=max_wall_timeout,
                    activity=activity,
                    idle_timeout=idle_timeout,
                    idle_grace=_executor_idle_grace_seconds(selected),
                    workspace_probe=lambda: _workspace_progress_snapshot(
                        workspace_paths,
                        str(root),
                    ),
                    progress_probe_interval=(
                        _env_positive_int("FT_WORKTREE_PROGRESS_INTERVAL")
                        or DEFAULT_PROGRESS_PROBE_INTERVAL
                    ),
                )
                break
            except ExecutorIdleTimeout:
                _stop_process(proc)
                stdout_thread.join(timeout=2)
                stderr_thread.join(timeout=2)
                return ExploreResult(
                    124,
                    normalizer.text,
                    "[INACTIVITY_TIMEOUT] executor sem produtividade observável "
                    f"por {idle_timeout} segundos",
                    session_id=normalizer.session_id if (persist_session or resume_session) else None,
                    session_resumed=bool(resume_session),
                    usage=normalizer.usage,
                    cost_usd=normalizer.cost_usd,
                )
            except subprocess.TimeoutExpired:
                _stop_process(proc)
                stdout_thread.join(timeout=2)
                stderr_thread.join(timeout=2)
                return ExploreResult(
                    124,
                    normalizer.text,
                    "[MAX_WALL_TIMEOUT] executor excedeu o teto absoluto opt-in "
                    f"de {max_wall_timeout} segundos",
                    session_id=normalizer.session_id if (persist_session or resume_session) else None,
                    session_resumed=bool(resume_session),
                    usage=normalizer.usage,
                    cost_usd=normalizer.cost_usd,
                )

        stdout_thread.join(timeout=2)
        stderr_thread.join(timeout=2)
        returncode = _honest_returncode(returncode)
        for chunk in normalizer.finish():
            if on_chunk is not None:
                on_chunk(chunk)
        text = normalizer.text
        stderr_text = "".join(stderr_parts).strip()
        if returncode != 0:
            error = normalizer.provider_error or stderr_text or f"executor saiu com código {returncode}"
            return ExploreResult(
                returncode, text, error,
                normalizer.session_id if (persist_session or resume_session) else None,
                bool(resume_session), normalizer.usage, normalizer.cost_usd,
            )
        if normalizer.provider_error:
            return ExploreResult(
                1, text, normalizer.provider_error,
                normalizer.session_id if (persist_session or resume_session) else None,
                bool(resume_session), normalizer.usage, normalizer.cost_usd,
            )
        if not text.strip():
            return ExploreResult(
                1, "", stderr_text or "executor terminou sem resposta",
                normalizer.session_id if (persist_session or resume_session) else None,
                bool(resume_session), normalizer.usage,
                normalizer.cost_usd,
            )
        return ExploreResult(
            0, text,
            session_id=normalizer.session_id if (persist_session or resume_session) else None,
            session_resumed=bool(resume_session), usage=normalizer.usage,
            cost_usd=normalizer.cost_usd,
        )
    finally:
        if runtime is not None:
            runtime.cleanup()
