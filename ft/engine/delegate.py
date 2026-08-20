"""
LLM Executor — interface para chamar Claude Code ou Codex como executor de construcao.
O LLM so constroi. Nao decide nada sobre o processo.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from ft.engine.llm_activity import (
    activity_log_path,
    append_activity,
    write_activity,
)

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
# O claude CLI (stream-json) emite eventos informativos "rate_limit_event" com
# "status":"allowed" mesmo em chamadas bem-sucedidas; sem remover essas linhas
# antes da busca, o padrão acima descarta respostas boas em loop de backoff.
_RATE_LIMIT_INFO_EVENT = re.compile(
    r"^.*\"type\"\s*:\s*\"rate_limit_event\".*\"status\"\s*:\s*\"allowed\".*$",
    re.MULTILINE,
)


def _rate_limit_signal(text: str) -> bool:
    return bool(_RATE_LIMIT_PATTERNS.search(_RATE_LIMIT_INFO_EVENT.sub("", text)))


def _attempt_rate_limited(llm_engine: str, returncode: int, output: str) -> bool:
    """Decide se uma tentativa deve entrar em backoff de rate limit.

    Para o claude o exit code é confiável e o texto do agente pode discutir
    "rate limit" legitimamente (ex.: pesquisa de viabilidade técnica), então
    uma tentativa bem-sucedida nunca conta. Outros CLIs podem sair com 0
    mesmo rate-limitados, e para eles o sinal textual é mantido.
    """
    if llm_engine == "claude" and returncode == 0:
        return False
    return _rate_limit_signal(output)
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
DEFAULT_EXECUTOR_TIMEOUT = 1_800
DEFAULT_CODEX_ULTRA_TIMEOUT = 3_600
DEFAULT_STREAM_IDLE_TIMEOUT = 480
DEFAULT_CODEX_IDLE_GRACE = 120
DEFAULT_PROGRESS_PROBE_INTERVAL = 5

_SOURCE_SUFFIXES = frozenset(
    {
        ".c",
        ".cc",
        ".cpp",
        ".cs",
        ".css",
        ".go",
        ".h",
        ".hpp",
        ".html",
        ".java",
        ".js",
        ".jsx",
        ".kt",
        ".kts",
        ".m",
        ".mm",
        ".php",
        ".py",
        ".rb",
        ".rs",
        ".scala",
        ".sh",
        ".sql",
        ".swift",
        ".ts",
        ".tsx",
        ".vue",
    }
)


@dataclass
class _SandboxMount:
    path: Path
    is_file: bool = False
    placeholder: bool = False


class ExecutorIdleTimeout(subprocess.TimeoutExpired):
    """Executor ficou vivo, mas sem emitir nova saída por tempo demais."""


@dataclass(frozen=True)
class _ProcessLiveness:
    alive: bool
    process_count: int = 0
    cpu_ticks: int = 0
    read_chars: int = 0
    write_chars: int = 0
    read_bytes: int = 0
    write_bytes: int = 0
    fd_count: int = 0
    socket_count: int = 0


@dataclass(frozen=True)
class _WorkspaceProgressSnapshot:
    digest: str
    file_count: int = 0
    total_bytes: int = 0
    source_file_count: int = 0
    source_bytes: int = 0


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


def _env_nonnegative_int(*names: str) -> int | None:
    """Lê o primeiro inteiro >= 0 definido em env entre os nomes dados."""
    for name in names:
        raw = os.environ.get(name, "").strip()
        if not raw:
            continue
        try:
            value = int(raw)
        except ValueError:
            continue
        if value >= 0:
            return value
    return None


def _normalize_executor_effort(value: str | None, *, source: str = "llm_effort") -> str | None:
    """Return a CLI-safe effort value, or None for provider defaults."""
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized or normalized.lower() == "default":
        return None
    if not re.fullmatch(r"[A-Za-z0-9_-]+", normalized):
        raise ValueError(f"{source} contém valor inválido")
    return normalized


def _normalize_codex_profile(value: str | None) -> str | None:
    """Return a CLI-safe Codex profile name without allowing option injection."""
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", normalized):
        raise ValueError("FT_CODEX_PROFILE contém valor inválido")
    return normalized


def _normalize_codex_auth(value: str | None) -> str | None:
    """Normalize the optional per-node Codex authentication route."""
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if not normalized:
        return None
    if normalized != "chatgpt":
        raise ValueError("codex_auth deve ser 'chatgpt' quando definido")
    return normalized


def _normalize_workflow_id(value: str | None) -> str | None:
    """Return a SymGateway-safe workflow label."""
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", normalized):
        raise ValueError("workflow_id contém valor inválido")
    return normalized


def _normalize_ft_cycle(value: str | None) -> str | None:
    """Return a SymGateway-safe Fast Track cycle label."""
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", normalized):
        raise ValueError("ft_cycle contém valor inválido")
    return normalized


def _symgateway_workflow_url(
    base_url: str,
    workflow_id: str,
    ft_cycle: str | None = None,
) -> str | None:
    """Add or replace the workflow and Fast Track segments of a gateway URL."""
    parsed = urlsplit(str(base_url).strip())
    if parsed.scheme not in {"http", "https"} or parsed.hostname != "symgateway.symlabs.ai":
        return None

    segments = [segment for segment in parsed.path.split("/") if segment]
    if "w" in segments:
        workflow_index = segments.index("w")
        if workflow_index + 1 < len(segments):
            segments[workflow_index + 1] = workflow_id
        else:
            segments.append(workflow_id)
    elif segments and segments[-1] == "v1":
        segments[-1:-1] = ["w", workflow_id]
    else:
        segments.extend(["w", workflow_id])

    if ft_cycle:
        insertion_index = segments.index("v1") if "v1" in segments else len(segments)
        segments[insertion_index:insertion_index] = [
            "t",
            workflow_id,
            "c",
            ft_cycle,
        ]

    return urlunsplit(parsed._replace(path="/" + "/".join(segments)))


def _codex_workflow_override(
    profile: str | None,
    workflow_id: str | None,
    ft_cycle: str | None = None,
) -> tuple[str, str] | None:
    """Resolve the profile provider and its workflow-scoped SymGateway URL."""
    if not profile or not workflow_id:
        return None
    codex_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    candidates = [codex_home / f"{profile}.config.toml", codex_home / "config.toml"]
    for config_path in candidates:
        try:
            config = tomllib.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            continue
        profile_config = config
        profiles = config.get("profiles")
        if isinstance(profiles, dict) and isinstance(profiles.get(profile), dict):
            profile_config = {**config, **profiles[profile]}
        provider_id = profile_config.get("model_provider")
        providers = profile_config.get("model_providers") or config.get("model_providers")
        if (
            not isinstance(provider_id, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]+", provider_id)
            or not isinstance(providers, dict)
            or not isinstance(providers.get(provider_id), dict)
        ):
            continue
        base_url = providers[provider_id].get("base_url")
        if not isinstance(base_url, str):
            continue
        routed_url = _symgateway_workflow_url(base_url, workflow_id, ft_cycle)
        if routed_url:
            return provider_id, routed_url
    return None


def _claude_project_settings(project_root: str | None) -> dict[str, str]:
    """Read only Claude env settings, including from a linked worktree's main checkout."""
    if not project_root:
        return {}
    roots = [Path(project_root).resolve()]
    try:
        worktrees = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        worktrees = None
    if worktrees is not None and worktrees.returncode == 0:
        for line in worktrees.stdout.splitlines():
            if line.startswith("worktree "):
                candidate = Path(line.removeprefix("worktree ")).resolve()
                if candidate not in roots:
                    roots.append(candidate)

    for root in roots:
        settings_path = root / ".claude" / "settings.local.json"
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        settings_env = settings.get("env")
        if not isinstance(settings_env, dict):
            continue
        return {
            name: value
            for name in ("ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY")
            if isinstance((value := settings_env.get(name)), str) and value
        }
    return {}


def _executor_timeout_seconds(llm_engine: str, llm_effort: str | None = None) -> int:
    """Resolve the legacy executor interval, now used as an inactivity alias."""
    engine = llm_engine.strip().lower()
    specific_name = f"FT_{engine.upper()}_EXECUTOR_TIMEOUT"
    configured = _env_positive_int(specific_name, "FT_LLM_EXECUTOR_TIMEOUT")
    if configured is not None:
        return configured
    effective_effort = _normalize_executor_effort(llm_effort)
    if engine == "codex":
        effective_effort = (
            _normalize_executor_effort(
                os.environ.get("FT_CODEX_REASONING_EFFORT"),
                source="FT_CODEX_REASONING_EFFORT",
            )
            or effective_effort
        )
    if engine == "codex" and effective_effort == "ultra":
        return DEFAULT_CODEX_ULTRA_TIMEOUT
    return DEFAULT_EXECUTOR_TIMEOUT


def _executor_idle_timeout_seconds(
    llm_engine: str,
    node_timeout: int | None = None,
) -> int:
    """Resolve the global rolling inactivity lease for one executor."""
    engine = llm_engine.strip().lower()
    configured = _env_positive_int(
        f"FT_{engine.upper()}_IDLE_TIMEOUT",
        "FT_LLM_IDLE_TIMEOUT",
    )
    if configured is not None:
        return configured
    legacy_configured = _env_positive_int(
        f"FT_{engine.upper()}_EXECUTOR_TIMEOUT",
        "FT_LLM_EXECUTOR_TIMEOUT",
    )
    if legacy_configured is not None:
        return legacy_configured
    if node_timeout is not None:
        return node_timeout
    return DEFAULT_STREAM_IDLE_TIMEOUT


def _executor_max_wall_timeout_seconds(llm_engine: str) -> int | None:
    """Resolve an opt-in absolute safety cap; productive runs are uncapped by default."""
    engine = llm_engine.strip().lower()
    return _env_positive_int(
        f"FT_{engine.upper()}_MAX_WALL_TIMEOUT",
        "FT_LLM_MAX_WALL_TIMEOUT",
    )


def _executor_idle_grace_seconds(llm_engine: str) -> int:
    """Resolve the final confirmation window after all progress probes stagnate."""
    engine = llm_engine.strip().lower()
    configured = _env_nonnegative_int(
        f"FT_{engine.upper()}_IDLE_GRACE",
        "FT_LLM_IDLE_GRACE",
    )
    if configured is not None:
        return configured
    return DEFAULT_CODEX_IDLE_GRACE if engine == "codex" else 0


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


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "sim", "on"}


def _path_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _workspace_progress_paths(
    project_root: str,
    allowed_paths: list[str] | None,
) -> list[Path]:
    """Resolve the whole isolated worktree as the global productivity scope."""
    root = Path(project_root).resolve()
    # ``allowed_paths`` continua sendo a fronteira de escrita, mas não a
    # fronteira de observação: todo ciclo roda em worktree isolada e qualquer
    # produção real dentro dela é um sinal válido de progresso. Manter o
    # argumento preserva a API interna usada pelos callers antigos.
    _ = allowed_paths
    return [root]


def _is_engine_runtime_progress_path(relative: Path) -> bool:
    """Ignore files whose growth is caused by the supervisor itself."""
    parts = relative.parts
    if parts and parts[0] == "state":
        return True
    return (
        len(parts) == 1
        and relative.name.startswith("cycle-")
        and relative.name.endswith("_log.md")
    )


def _workspace_progress_snapshot(paths: list[Path], project_root: str) -> _WorkspaceProgressSnapshot:
    """Fingerprint authored worktree files without reading their contents.

    Em repositórios Git, arquivos versionados e novos não ignorados são
    enumerados pelo índice. Isso observa toda a worktree sem atravessar caches
    pesados como ``build/``, ``node_modules/`` ou ``.venv/``. O fallback
    recursivo mantém a política funcional fora de Git.
    """
    root = Path(project_root).resolve()
    digest = hashlib.blake2b(digest_size=20)
    file_count = 0
    total_bytes = 0
    source_file_count = 0
    source_bytes = 0

    def record(path: Path, *, missing: bool = False) -> None:
        nonlocal file_count, total_bytes, source_file_count, source_bytes
        try:
            relative_path = path.relative_to(root)
            relative = relative_path.as_posix()
        except ValueError:
            return
        if relative == ".git" or relative.startswith(".git/"):
            return
        if _is_engine_runtime_progress_path(relative_path):
            return
        if missing:
            digest.update(f"M\0{relative}\0".encode("utf-8", errors="surrogateescape"))
            return
        try:
            metadata = path.lstat()
        except OSError:
            return
        kind = "D" if path.is_dir() and not path.is_symlink() else "F"
        digest.update(
            (
                f"{kind}\0{relative}\0{metadata.st_mode}\0"
                f"{metadata.st_size}\0{metadata.st_mtime_ns}\0"
            ).encode("utf-8", errors="surrogateescape")
        )
        if kind != "F":
            return
        file_count += 1
        total_bytes += metadata.st_size
        if path.suffix.lower() in _SOURCE_SUFFIXES:
            source_file_count += 1
            source_bytes += metadata.st_size

    git_paths: list[Path] | None = None
    if paths == [root] and (root / ".git").exists():
        try:
            listed = subprocess.run(
                [
                    "git",
                    "ls-files",
                    "--cached",
                    "--others",
                    "--exclude-standard",
                    "-z",
                ],
                cwd=root,
                capture_output=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            listed = None
        if listed is not None and listed.returncode == 0:
            git_paths = []
            for raw in listed.stdout.split(b"\0"):
                if not raw:
                    continue
                relative = Path(os.fsdecode(raw))
                if relative.is_absolute() or ".." in relative.parts:
                    continue
                # Preserve symlink metadata instead of resolving its target
                # outside the worktree.
                git_paths.append(root / relative)

    if git_paths is not None:
        for path in sorted(set(git_paths), key=str):
            record(path, missing=not path.exists() and not path.is_symlink())
        return _WorkspaceProgressSnapshot(
            digest=digest.hexdigest(),
            file_count=file_count,
            total_bytes=total_bytes,
            source_file_count=source_file_count,
            source_bytes=source_bytes,
        )

    for watched in paths:
        if not watched.exists() and not watched.is_symlink():
            record(watched, missing=True)
            continue
        if watched.is_file() or watched.is_symlink():
            record(watched)
            continue
        pending = [watched]
        while pending:
            directory = pending.pop()
            try:
                entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
            except OSError:
                continue
            for entry in reversed(entries):
                path = Path(entry.path)
                try:
                    relative = path.relative_to(root)
                except ValueError:
                    continue
                if ".git" in relative.parts:
                    continue
                if _is_engine_runtime_progress_path(relative):
                    continue
                try:
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(path)
                    else:
                        record(path)
                except OSError:
                    continue

    return _WorkspaceProgressSnapshot(
        digest=digest.hexdigest(),
        file_count=file_count,
        total_bytes=total_bytes,
        source_file_count=source_file_count,
        source_bytes=source_bytes,
    )


def _workspace_progress_diagnostics(
    baseline: _WorkspaceProgressSnapshot,
    current: _WorkspaceProgressSnapshot,
) -> dict[str, int]:
    return {
        "files_delta": current.file_count - baseline.file_count,
        "bytes_delta": current.total_bytes - baseline.total_bytes,
        "source_files_delta": current.source_file_count - baseline.source_file_count,
        "source_bytes_delta": current.source_bytes - baseline.source_bytes,
    }


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


def _append_opencode_runtime_diagnostics(runtime_dir: Path, log_path: str | None) -> None:
    """Preserva logs internos do OpenCode antes do sandbox temporário sumir."""
    if not log_path:
        return
    diagnostics = [
        ("opencode.log", runtime_dir / "data" / "opencode" / "log" / "opencode.log"),
        ("frecency.jsonl", runtime_dir / "state" / "opencode" / "frecency.jsonl"),
    ]
    chunks: list[str] = []
    for label, source in diagnostics:
        if not source.exists() or not source.is_file():
            continue
        try:
            text = source.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if not text.strip():
            continue
        limit = 120_000 if label == "opencode.log" else 20_000
        if len(text) > limit:
            text = text[-limit:]
            text = f"[truncated to last {limit} chars]\n{text}"
        chunks.append(f"\n\n--- OPENCODE INTERNAL {label} ---\n{text.rstrip()}\n")
    if chunks:
        with Path(log_path).open("a", encoding="utf-8") as f:
            f.write("".join(chunks))


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


def _stop_process_tree(
    proc: subprocess.Popen,
    terminate_timeout: int | float = 5,
    kill_timeout: int | float = 5,
    *,
    process_group: int | None = None,
) -> None:
    """Encerra o processo e, quando possível, todo o process group dele."""
    if proc.poll() is not None and process_group is None:
        return

    pgid = process_group
    use_group = pgid is not None
    if pgid is None:
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
        pass

    try:
        proc.wait(timeout=terminate_timeout)
    except subprocess.TimeoutExpired:
        pass

    try:
        if use_group and pgid is not None:
            os.killpg(pgid, signal.SIGKILL)
        elif proc.poll() is None:
            proc.kill()
    except ProcessLookupError:
        pass

    try:
        proc.wait(timeout=kill_timeout)
    except subprocess.TimeoutExpired:
        pass


def _supervised_command(cmd: list[str]) -> list[str]:
    """Wrap one command in an isolated Linux subreaper supervisor."""
    if not sys.platform.startswith("linux"):
        return cmd
    supervisor = Path(__file__).with_name("process_supervisor.py")
    return [sys.executable, str(supervisor), "--", *cmd]


def _linux_process_tree(root_pid: int) -> list[int]:
    """Return root plus descendants using procfs without reading command lines."""
    pending = [root_pid]
    seen: set[int] = set()
    while pending:
        pid = pending.pop()
        if pid in seen:
            continue
        seen.add(pid)
        try:
            children = Path(f"/proc/{pid}/task/{pid}/children").read_text(
                encoding="utf-8"
            )
        except OSError:
            continue
        pending.extend(
            int(value)
            for value in children.split()
            if value.isdigit()
        )
    return sorted(seen)


def _process_liveness_snapshot(proc: subprocess.Popen) -> _ProcessLiveness:
    """Collect non-sensitive weak liveness counters for one supervised tree."""
    if proc.poll() is not None:
        return _ProcessLiveness(alive=False)
    if not sys.platform.startswith("linux"):
        return _ProcessLiveness(alive=True, process_count=1)

    pids = _linux_process_tree(proc.pid)
    cpu_ticks = 0
    read_chars = 0
    write_chars = 0
    read_bytes = 0
    write_bytes = 0
    fd_count = 0
    socket_count = 0
    observed_processes = 0
    for pid in pids:
        proc_root = Path(f"/proc/{pid}")
        try:
            stat_tail = (proc_root / "stat").read_text(
                encoding="utf-8"
            ).rpartition(") ")[2].split()
            cpu_ticks += int(stat_tail[11]) + int(stat_tail[12])
            observed_processes += 1
        except (OSError, ValueError, IndexError):
            continue
        try:
            for line in (proc_root / "io").read_text(encoding="utf-8").splitlines():
                key, _, raw_value = line.partition(":")
                if key == "rchar":
                    read_chars += int(raw_value.strip())
                elif key == "wchar":
                    write_chars += int(raw_value.strip())
                elif key == "read_bytes":
                    read_bytes += int(raw_value.strip())
                elif key == "write_bytes":
                    write_bytes += int(raw_value.strip())
        except (OSError, ValueError):
            pass
        try:
            for descriptor in (proc_root / "fd").iterdir():
                fd_count += 1
                try:
                    if os.readlink(descriptor).startswith("socket:["):
                        socket_count += 1
                except OSError:
                    pass
        except OSError:
            pass
    return _ProcessLiveness(
        alive=True,
        process_count=observed_processes,
        cpu_ticks=cpu_ticks,
        read_chars=read_chars,
        write_chars=write_chars,
        read_bytes=read_bytes,
        write_bytes=write_bytes,
        fd_count=fd_count,
        socket_count=socket_count,
    )


def _liveness_diagnostics(
    baseline: _ProcessLiveness,
    current: _ProcessLiveness,
) -> dict[str, int]:
    return {
        "processes_delta": current.process_count - baseline.process_count,
        "sockets_delta": current.socket_count - baseline.socket_count,
        "fds_delta": current.fd_count - baseline.fd_count,
        "cpu_delta_ticks": current.cpu_ticks - baseline.cpu_ticks,
        "read_delta_chars": current.read_chars - baseline.read_chars,
        "write_delta_chars": current.write_chars - baseline.write_chars,
        "read_delta_bytes": current.read_bytes - baseline.read_bytes,
        "write_delta_bytes": current.write_bytes - baseline.write_bytes,
    }


def _has_productive_liveness(
    baseline: _ProcessLiveness,
    current: _ProcessLiveness,
) -> bool:
    # rchar/wchar also count pipe, socket and keepalive traffic. A remote LLM
    # process can therefore emit the same small heartbeat forever while doing
    # no observable work. Keep those counters for diagnostics, but renew the
    # inactivity lease only on CPU, topology/fd changes or actual storage I/O.
    return current.alive and (
        current.process_count != baseline.process_count
        or current.socket_count != baseline.socket_count
        or current.fd_count != baseline.fd_count
        # One scheduler tick per probe is typical event-loop polling. Require
        # at least two ticks before CPU alone can renew the lease.
        or current.cpu_ticks - baseline.cpu_ticks >= 2
        or current.read_bytes != baseline.read_bytes
        or current.write_bytes != baseline.write_bytes
    )


def _wait_for_process(
    proc: subprocess.Popen,
    timeout: float | None,
    early_success_paths: list[Path] | None = None,
    early_success_grace: int = 20,
    activity: dict[str, float] | None = None,
    idle_timeout: int | float | None = None,
    idle_grace: int | float = 0,
    on_idle_grace: Callable[[dict[str, int]], None] | None = None,
    workspace_probe: Callable[[], _WorkspaceProgressSnapshot] | None = None,
    progress_probe_interval: int | float = DEFAULT_PROGRESS_PROBE_INTERVAL,
    on_progress: Callable[[str, dict[str, int]], None] | None = None,
) -> tuple[int, bool]:
    """Wait using a rolling productivity lease plus an optional absolute cap."""
    if not hasattr(proc, "poll"):
        return proc.wait(timeout=timeout), False

    started = time.monotonic()
    started_wall = time.time()
    progress_probe_interval = max(0.05, float(progress_probe_interval))
    satisfied_since: float | None = None
    observed_strong_activity = (
        activity.get("last", started_wall) if activity else started_wall
    )
    liveness_baseline = _process_liveness_snapshot(proc)
    workspace_baseline = workspace_probe() if workspace_probe is not None else None
    last_probe = started
    idle_grace_deadline: float | None = None

    def renew(source: str, diagnostics: dict[str, int]) -> None:
        nonlocal observed_strong_activity, idle_grace_deadline
        if activity is None:
            return
        observed_strong_activity = time.time()
        activity["last"] = observed_strong_activity
        key = f"{source}_renewals"
        activity[key] = activity.get(key, 0.0) + 1.0
        idle_grace_deadline = None
        if on_progress is not None:
            on_progress(source, diagnostics)

    while True:
        returncode = proc.poll()
        if returncode is not None:
            return returncode, False
        now = time.monotonic()
        elapsed = now - started
        if timeout is not None and elapsed >= timeout:
            raise subprocess.TimeoutExpired(proc.args, timeout)
        if idle_timeout and activity:
            last_activity = activity.get("last", started_wall)
            if last_activity != observed_strong_activity:
                observed_strong_activity = last_activity
                liveness_baseline = _process_liveness_snapshot(proc)
                idle_grace_deadline = None
            idle_age = time.time() - activity.get("last", started_wall)
            should_probe = (
                now - last_probe >= progress_probe_interval
                or idle_age >= idle_timeout
            )
            if should_probe:
                current_liveness = _process_liveness_snapshot(proc)
                if _has_productive_liveness(liveness_baseline, current_liveness):
                    renew(
                        "process",
                        _liveness_diagnostics(liveness_baseline, current_liveness),
                    )
                liveness_baseline = current_liveness

                if workspace_probe is not None:
                    current_workspace = workspace_probe()
                    if (
                        workspace_baseline is not None
                        and current_workspace.digest != workspace_baseline.digest
                    ):
                        renew(
                            "workspace",
                            _workspace_progress_diagnostics(
                                workspace_baseline,
                                current_workspace,
                            ),
                        )
                    workspace_baseline = current_workspace
                last_probe = now

            idle_age = time.time() - activity.get("last", started_wall)
            if idle_age >= idle_timeout:
                if idle_grace_deadline is None and idle_grace > 0:
                    idle_grace_deadline = now + idle_grace
                    if on_idle_grace is not None:
                        on_idle_grace(
                            {
                                "processes": liveness_baseline.process_count,
                                "sockets": liveness_baseline.socket_count,
                                "fds": liveness_baseline.fd_count,
                                "grace_seconds": max(0, int(idle_grace)),
                            }
                        )
                if idle_grace_deadline is None or now >= idle_grace_deadline:
                    raise ExecutorIdleTimeout(proc.args, idle_timeout)
        if early_success_paths and _paths_have_content(early_success_paths):
            if satisfied_since is None:
                satisfied_since = now
            elif now - satisfied_since >= early_success_grace:
                _stop_process_tree(proc)
                return 0, True
        else:
            satisfied_since = None
        remaining = None if timeout is None else max(0.01, timeout - elapsed)
        time.sleep(1.0 if remaining is None else min(1.0, remaining))


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
    root = Path(project_root).resolve()
    runtime_path = Path(runtime_dir).resolve()
    runtime_path.mkdir(parents=True, exist_ok=True)
    hidden_state = runtime_path / "hidden-state"
    hidden_state.mkdir(parents=True, exist_ok=True)

    wrapped = [
        bwrap,
        "--ro-bind", "/", "/",
        "--dev-bind", "/dev", "/dev",
        "--proc", "/proc",
        "--bind", str(runtime_path), str(runtime_path),
    ]
    state_path = (root / "state").resolve()
    if _path_relative_to(state_path, root) and state_path.is_dir():
        wrapped += ["--ro-bind", str(hidden_state), str(state_path)]
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
        permission["*"] = "deny"
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
    provider_id, _, model_id = effective_model.partition("/")
    context_limit = _env_positive_int("FT_OPENCODE_CONTEXT_LIMIT", "FT_OPENCODE_CONTEXT_WINDOW")
    output_limit = _env_positive_int("FT_OPENCODE_OUTPUT_LIMIT", "FT_OPENCODE_MAX_OUTPUT")
    if effective_model == DEFAULT_OPENCODE_MODEL:
        context_limit = context_limit or DEFAULT_OPENCODE_CONTEXT_LIMIT
        output_limit = output_limit or DEFAULT_OPENCODE_OUTPUT_LIMIT

    providers: dict | None = None
    provider: dict | None = None

    def ensure_provider_config() -> dict:
        nonlocal providers, provider
        if providers is None:
            current_providers = config.get("provider")
            providers = current_providers if isinstance(current_providers, dict) else {}
        if provider is None:
            current_provider = providers.get(provider_id) if provider_id else {}
            provider = current_provider if isinstance(current_provider, dict) else {}
        providers[provider_id] = provider
        config["provider"] = providers
        return provider

    if context_limit is not None:
        output_limit = output_limit or DEFAULT_OPENCODE_OUTPUT_LIMIT
        if provider_id and model_id:
            provider_config = ensure_provider_config()
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
            provider_config["models"] = models

    provider_timeout = _env_positive_int("FT_OPENCODE_PROVIDER_TIMEOUT", "FT_OPENCODE_TIMEOUT")
    chunk_timeout = _env_positive_int("FT_OPENCODE_CHUNK_TIMEOUT", "FT_OPENCODE_PROVIDER_CHUNK_TIMEOUT")
    header_timeout = _env_positive_int("FT_OPENCODE_HEADER_TIMEOUT", "FT_OPENCODE_PROVIDER_HEADER_TIMEOUT")
    if provider_id and any(value is not None for value in (provider_timeout, chunk_timeout, header_timeout)):
        provider_config = ensure_provider_config()
        options = provider_config.get("options")
        if not isinstance(options, dict):
            options = {}
        if provider_timeout is not None:
            options["timeout"] = provider_timeout
        if chunk_timeout is not None:
            options["chunkTimeout"] = chunk_timeout
        if header_timeout is not None:
            options["headerTimeout"] = header_timeout
        provider_config["options"] = options

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
    workflow_id: str | None = None,
    ft_cycle: str | None = None,
) -> dict[str, str]:
    """Monta env do executor, aplicando hardening específico por provider."""
    env = dict(os.environ if base_env is None else base_env)
    engine = llm_engine.lower().strip()
    normalized_workflow = _normalize_workflow_id(workflow_id)
    normalized_ft_cycle = _normalize_ft_cycle(ft_cycle)
    if engine == "claude" and normalized_workflow:
        for name, value in _claude_project_settings(project_root).items():
            env[name] = value
        base_url = env.get("ANTHROPIC_BASE_URL")
        if base_url:
            routed_url = _symgateway_workflow_url(
                base_url,
                normalized_workflow,
                normalized_ft_cycle,
            )
            if routed_url:
                env["ANTHROPIC_BASE_URL"] = routed_url
    if engine == "opencode":
        env.setdefault("CI", "1")
        env.setdefault("COREPACK_ENABLE_DOWNLOAD_PROMPT", "0")
        env.setdefault("npm_config_yes", "true")
        env.setdefault("NPM_CONFIG_YES", "true")
        env.setdefault("npm_config_audit", "false")
        env.setdefault("npm_config_fund", "false")
        env.setdefault("npm_config_update_notifier", "false")
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
    # True quando o processo do LLM morreu sem emitir veredito (DONE/BLOCKED):
    # stream interrompida, crash ou timeout. Falha de infraestrutura, não de
    # conteúdo — o runner pode retentar a delegação automaticamente.
    died: bool = False
    # Identificador de conversa retornado pelo provider. O engine persiste esse
    # valor fora do worktree e o reutiliza somente quando o processo opta por
    # session_policy. Não é credencial.
    session_id: str | None = None
    session_resumed: bool = False
    # Falha específica de retomada (sessão expirada, removida ou inválida).
    # Permite ao runner reidratar uma conversa nova sem classificar o problema
    # como erro de conteúdo do node.
    session_error: bool = False
    timings: dict[str, float] = field(default_factory=dict)


def _build_executor_command(
    llm_engine: str,
    prompt: str,
    project_root: str,
    max_turns: int,
    model: str | None = None,
    effort: str | None = None,
    session_id: str | None = None,
    resume_session: bool = False,
    workflow_id: str | None = None,
    ft_cycle: str | None = None,
    codex_auth: str | None = None,
) -> list[str]:
    """Monta o comando do executor não-interativo com bypass habilitado."""
    engine = llm_engine.lower().strip()
    normalized_effort = _normalize_executor_effort(effort)
    normalized_workflow = _normalize_workflow_id(workflow_id)
    normalized_ft_cycle = _normalize_ft_cycle(ft_cycle)
    normalized_codex_auth = _normalize_codex_auth(codex_auth)

    if normalized_codex_auth and engine != "codex":
        raise ValueError("codex_auth só pode ser usado com o executor Codex")

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
        if normalized_effort:
            cmd += ["--effort", normalized_effort]
        if session_id:
            cmd += ["--resume" if resume_session else "--session-id", session_id]
        cmd += ["-p", prompt]
        return cmd

    if engine == "codex":
        cmd = ["codex"]
        profile = (
            None
            if normalized_codex_auth == "chatgpt"
            else _normalize_codex_profile(os.environ.get("FT_CODEX_PROFILE"))
        )
        if profile:
            cmd += ["--profile", profile]
        cmd.append("exec")
        if session_id and resume_session:
            cmd.append("resume")
        if normalized_codex_auth == "chatgpt":
            # A excecao inclui todo o node, não apenas a chamada image_gen.
            # Force the built-in provider and auth so project config cannot
            # silently route this execution back through a custom provider.
            cmd += [
                "-c",
                'model_provider="openai"',
                "-c",
                'forced_login_method="chatgpt"',
            ]
        workflow_override = _codex_workflow_override(
            profile,
            normalized_workflow,
            normalized_ft_cycle,
        )
        if workflow_override:
            provider_id, routed_url = workflow_override
            # Codex applies profile layers after root-level config overrides.
            # Keep this override in the deepest subcommand scope so the profile
            # cannot restore its generic /w/orchestration base URL.
            cmd += [
                "-c",
                f"model_providers.{provider_id}.base_url={json.dumps(routed_url)}",
            ]
        reasoning_effort = (
            _normalize_executor_effort(
                os.environ.get("FT_CODEX_REASONING_EFFORT"),
                source="FT_CODEX_REASONING_EFFORT",
            )
            or normalized_effort
        )
        if reasoning_effort:
            cmd += ["-c", f"model_reasoning_effort={json.dumps(reasoning_effort)}"]
        cmd += [
            "--dangerously-bypass-approvals-and-sandbox",
            "--skip-git-repo-check",
            "--json",
        ]
        # ``codex exec resume`` não aceita -C. O subprocesso já recebe cwd,
        # portanto o diretório continua determinístico nos dois modos.
        if not (session_id and resume_session):
            cmd += ["-C", project_root]
        if model:
            cmd += ["-m", model]
        if session_id and resume_session:
            cmd.append(session_id)
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
        if not _env_falsey("FT_OPENCODE_AUTO"):
            cmd.append("--auto")
        if not _env_falsey("FT_OPENCODE_PURE"):
            cmd.append("--pure")
        configured_variant = (os.environ.get("FT_OPENCODE_VARIANT") or "").strip()
        if configured_variant:
            # Preserve the legacy env sentinel while allowing an explicit
            # provider variant literally named "none".
            if configured_variant.lower() not in {"0", "false", "no", "off", "none"}:
                cmd += ["--variant", configured_variant]
        elif normalized_effort:
            cmd += ["--variant", normalized_effort]
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
    activity_log_path(path).unlink(missing_ok=True)
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with path.open("w", encoding="utf-8") as f:
        f.write("# LLM Delegate Log\n")
        f.write(f"started_at: {started_at}\n")
        f.write(f"llm_engine: {llm_engine}\n")
        f.write(f"command: {' '.join(cmd)}\n")
        f.write("\n## Prompt\n\n")
        f.write(prompt)
        if not prompt.endswith("\n"):
            f.write("\n")
        f.write("\n## Output\n\n")


def _stream_oneline(value: object) -> str:
    """Colapsa texto de evento em uma linha sem cortar conteúdo."""
    return " ".join(str(value or "").strip().split())


def _clip_stream_status(value: object, limit: int = 120) -> str:
    """Resumo curto para heartbeat/status ao vivo, sempre com reticencias."""
    text = _stream_oneline(value)
    if limit <= 0 or len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


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
                    return f"→ {_stream_oneline(block.get('text', ''))}"
                if btype == "thinking":
                    t = _stream_oneline(block.get("thinking"))
                    if t:
                        return f"✻ {t}"
        if etype == "result":
            return f"result: {_stream_oneline(event.get('result', ''))}"
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


def _extract_provider_session_id(llm_engine: str, raw_output: str) -> str | None:
    """Extrai o identificador durável de conversa dos streams JSONL."""
    engine = llm_engine.lower().strip()
    for line in raw_output.splitlines():
        text = line.strip()
        if not text.startswith("{"):
            continue
        try:
            event = json.loads(text)
        except json.JSONDecodeError:
            continue
        if engine == "codex" and event.get("type") == "thread.started":
            value = event.get("thread_id")
        elif engine == "claude":
            value = event.get("session_id")
            if value is None and isinstance(event.get("message"), dict):
                value = event["message"].get("session_id")
        else:
            value = None
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


_SESSION_ERROR_PATTERNS = re.compile(
    r"(?:session|conversation|thread)[^\n]{0,100}"
    r"(?:not found|does not exist|unknown|invalid|expired|deleted|unable to resume)|"
    r"(?:resume|resuming)[^\n]{0,100}(?:failed|error|invalid)",
    re.IGNORECASE,
)


def _is_session_resume_error(output: str, *, resumed: bool) -> bool:
    return bool(resumed and _SESSION_ERROR_PATTERNS.search(output or ""))


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


_OPENCODE_INTERNAL_LOG_RE = re.compile(r"^timestamp=\S+\s+level=\S+\s+run=\S+\s+message=")


def _is_opencode_internal_log_line(line: str) -> bool:
    """Identifica logs internos do OpenCode que nao indicam progresso do modelo."""
    return bool(_OPENCODE_INTERNAL_LOG_RE.match(line.strip()))


def _clean_opencode_capture_text(text: str) -> str:
    """Remove ruído do OpenCode antes de gravar artifact capturado."""
    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text).strip()
    text = re.sub(r"\n?\[tool_calls\]\s*\(None\)\s*$", "", text).strip()
    blocked_tail = re.search(r"\n+BLOCKED:\s+.*\Z", text, re.DOTALL)
    if blocked_tail and len([line for line in text[:blocked_tail.start()].splitlines() if line.strip()]) >= 3:
        text = text[:blocked_tail.start()].rstrip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 2:
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
    lines = text.splitlines()
    first_heading = next(
        (idx for idx, line in enumerate(lines) if re.match(r"^#{1,6}\s+\S", line.strip())),
        None,
    )
    if first_heading and first_heading > 0:
        prelude = "\n".join(lines[:first_heading]).strip().lower()
        if re.search(r"\b(i need to|i'll|let me|we need to|vou|preciso|need to)\b", prelude):
            text = "\n".join(lines[first_heading:]).strip()
    return text


def _validate_opencode_script(script: str) -> str | None:
    """Recusa scripts obviamente fora do escopo antes de executar no worktree."""
    if not script.strip():
        return "script vazio"
    forbidden = [
        r"\bsudo\b",
        r"\bsu\s+-",
        r"\brm\s+-rf\s+/",
        r"\bmkfs\b",
        r"\bmount\b",
        r"\bumount\b",
        r"\bdd\s+if=",
        r">\s*/(?:etc|usr|bin|sbin|lib|var|home)\b",
        r"\b(?:cat|tee|python3?|node|mkdir|cp|mv|touch|chmod|chown)\b[^\n;&|]*\s/(?:etc|usr|bin|sbin|lib|var|home)\b",
    ]
    for pattern in forbidden:
        if re.search(pattern, script):
            return f"script contem comando/path proibido: {pattern}"
    return None


def _timeout_stream_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _run_opencode_script(
    script: str,
    project_root: str,
    allowed_paths: list[str] | None,
    env: dict[str, str],
    log_path: str | None,
    runtime_dir: str | None,
    timeout_seconds: float | None = None,
) -> tuple[bool, str]:
    """Executa o Bash script gerado pelo OpenCode e retorna output combinado."""
    invalid = _validate_opencode_script(script)
    if invalid:
        return False, f"[OPENCODE_SCRIPT_INVALID] {invalid}\n"

    if runtime_dir:
        script_path = Path(runtime_dir).resolve() / "opencode-generated.sh"
        script_path.write_text(script, encoding="utf-8")
    else:
        tmp = tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".sh", delete=False)
        try:
            tmp.write(script)
            script_path = Path(tmp.name)
        finally:
            tmp.close()

    cmd: list[str] = ["bash", str(script_path)]
    mounts: list[_SandboxMount] = []
    if runtime_dir and not _env_falsey("FT_OPENCODE_SANDBOX"):
        cmd, mounts = _wrap_opencode_sandbox_command(
            cmd,
            project_root=project_root,
            allowed_paths=allowed_paths,
            runtime_dir=runtime_dir,
        )

    header = "\n## OpenCode generated script\n\n```bash\n" + script.rstrip() + "\n```\n\n## Script output\n\n"
    if log_path:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        with Path(log_path).open("a", encoding="utf-8") as f:
            f.write(header)

    effective_timeout = 1800.0 if timeout_seconds is None else timeout_seconds
    proc: subprocess.Popen[str] | None = None
    try:
        proc = subprocess.Popen(
            _supervised_command(cmd),
            cwd=project_root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        stdout, stderr = proc.communicate(timeout=effective_timeout)
        returncode = proc.returncode
    except subprocess.TimeoutExpired as initial_timeout:
        assert proc is not None
        _stop_process_tree(
            proc,
            terminate_timeout=2,
            kill_timeout=1,
            process_group=proc.pid,
        )
        try:
            stdout, stderr = proc.communicate(timeout=1)
        except subprocess.TimeoutExpired as cleanup_timeout:
            stdout = _timeout_stream_text(cleanup_timeout.stdout) or _timeout_stream_text(
                initial_timeout.stdout
            )
            stderr = _timeout_stream_text(cleanup_timeout.stderr) or _timeout_stream_text(
                initial_timeout.stderr
            )
            for pipe in (proc.stdout, proc.stderr):
                if pipe is not None:
                    pipe.close()
        shown_timeout = (
            "1800"
            if timeout_seconds is None
            else f"{effective_timeout:.2f}".rstrip("0").rstrip(".")
        )
        output = (
            _timeout_stream_text(stdout)
            + _timeout_stream_text(stderr)
            + f"\n[TIMEOUT] Script excedeu {shown_timeout} segundos.\n"
        )
        if log_path:
            with Path(log_path).open("a", encoding="utf-8") as f:
                f.write(output)
        _cleanup_empty_placeholders(mounts)
        return False, output
    finally:
        if not runtime_dir:
            try:
                script_path.unlink()
            except OSError:
                pass

    output = (stdout or "") + (stderr or "")
    if log_path:
        with Path(log_path).open("a", encoding="utf-8") as f:
            f.write(output)
    _cleanup_empty_placeholders(mounts)
    return returncode == 0, output


def _parse_opencode_file_bundle(text: str) -> tuple[dict[str, str], str | None]:
    """Parseia blocos <ft_file path="...">...</ft_file>."""
    if '<ft_file path=\\"' in text:
        text = text.replace('\\"', '"')
    files: dict[str, str] = {}
    xml_matches = list(re.finditer(r'<(?:ft_file|file)\s+path="([^"]+)">\n?(.*?)\n?</(?:ft_file|file)>', text, re.DOTALL))
    if xml_matches:
        for match in xml_matches:
            path = match.group(1).strip()
            content = match.group(2)
            if not path:
                return {}, "path vazio no file bundle"
            if "..." in content:
                return {}, f"conteudo truncado com reticencias em {path}"
            files[path] = content.rstrip() + "\n"
        return files, None

    # Fallback para o protocolo inicial, mantido para compatibilidade com logs antigos.
    current_path: str | None = None
    current_lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if current_path is None:
            match = re.fullmatch(r"<<<FT_FILE:(.+?)>>>", line)
            if match:
                current_path = match.group(1).strip()
                current_lines = []
            elif line:
                # Ignora cercas acidentais, mas recusa prosa fora do protocolo.
                if line not in {"```", "```text", "```markdown"}:
                    return {}, f"linha fora do protocolo file bundle: {line[:80]}"
            continue
        if line == "<<<FT_END_FILE>>>":
            if not current_path:
                return {}, "path vazio no file bundle"
            files[current_path] = "\n".join(current_lines).rstrip() + "\n"
            current_path = None
            current_lines = []
        else:
            current_lines.append(raw_line)
    if current_path is not None:
        return {}, f"arquivo sem <<<FT_END_FILE>>>: {current_path}"
    if not files:
        return {}, "nenhum arquivo no file bundle"
    return files, None


def _write_scope_allows(path: str, project_root: str, allowed_paths: list[str] | None) -> bool:
    """Confirma se um path relativo esta dentro do escopo de escrita permitido."""
    if not path or Path(path).is_absolute():
        return False
    root = Path(project_root).resolve()
    target = (root / path).resolve()
    if not _path_relative_to(target, root):
        return False
    for raw in allowed_paths or []:
        value = str(raw).strip()
        if not value:
            continue
        allowed = (root / value.rstrip("/")).resolve() if not Path(value).is_absolute() else Path(value).resolve()
        if not _path_relative_to(allowed, root):
            continue
        is_dir = value.endswith("/") or not _looks_like_file_path(value, allowed)
        if is_dir and (target == allowed or _path_relative_to(target, allowed)):
            return True
        if not is_dir and target == allowed:
            return True
    return False


def _canonicalize_opencode_bundle_path(
    rel_path: str,
    files: dict[str, str],
    project_root: str,
    allowed_paths: list[str] | None,
) -> str:
    """Corrige omissões comuns de prefixo em bundles OpenCode.

    Em nodes de frontend o prompt força `project/frontend/...`, mas alguns
    modelos retornam `scripts/...` ou `package.json`. Prefixar esses caminhos é
    seguro quando o escopo permite `project` e o bundle já indica frontend.
    """
    cleaned = rel_path.strip()
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    if _write_scope_allows(cleaned, project_root, allowed_paths):
        return cleaned
    frontend_indicators = (
        any(path.startswith("project/frontend/") for path in files)
        or any(str(raw).strip().rstrip("/") in {"project", "project/frontend"} for raw in allowed_paths or [])
    )
    frontend_roots = ("package.json", "package-lock.json", "index.html", "vite.config.js", "vite.config.mjs")
    frontend_dirs = ("scripts/", "src/", "public/")
    frontend_aliases = ("frontend/", "package/frontend/")
    for alias in frontend_aliases:
        if frontend_indicators and cleaned.startswith(alias):
            candidate = f"project/frontend/{cleaned.removeprefix(alias)}"
            if _write_scope_allows(candidate, project_root, allowed_paths):
                return candidate
    if frontend_indicators and (
        cleaned in frontend_roots or any(cleaned.startswith(prefix) for prefix in frontend_dirs)
    ):
        candidate = f"project/frontend/{cleaned}"
        if _write_scope_allows(candidate, project_root, allowed_paths):
            return candidate
    return cleaned


def _materialize_opencode_file_bundle(
    bundle: str,
    project_root: str,
    allowed_paths: list[str] | None,
    log_path: str | None,
) -> tuple[bool, str]:
    """Grava arquivos descritos pelo OpenCode dentro do escopo permitido."""
    files, error = _parse_opencode_file_bundle(bundle)
    if error:
        return False, f"[OPENCODE_BUNDLE_INVALID] {error}\n"
    root = Path(project_root).resolve()
    written: list[str] = []
    for rel_path, content in files.items():
        materialized_path = _canonicalize_opencode_bundle_path(
            rel_path,
            files=files,
            project_root=project_root,
            allowed_paths=allowed_paths,
        )
        if not _write_scope_allows(materialized_path, project_root, allowed_paths):
            return False, f"[OPENCODE_BUNDLE_INVALID] path fora do escopo permitido: {rel_path}\n"
        target = (root / materialized_path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(materialized_path)
    output = "Arquivos materializados pelo engine:\n" + "\n".join(f"- {path}" for path in written) + "\n"
    if log_path:
        with Path(log_path).open("a", encoding="utf-8") as f:
            f.write("\n## OpenCode file bundle materialized\n\n")
            f.write(output)
    return True, output


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
        cmd = _stream_oneline(input_data.get("command") or "")
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
                cmd = _clip_stream_status(item.get("command") or "", 80)
                return f"$ {cmd}"
            if itype == "agent_message":
                msg = _clip_stream_status(item.get("text") or "", 80)
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
            return _clip_stream_status(text, 80) if text else None
        try:
            event = json.loads(text)
        except json.JSONDecodeError:
            return _clip_stream_status(text, 80) if text else None
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
                    snippet = _clip_stream_status(block.get("text", ""), 80)
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
            return _clip_stream_status(text, 80)
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
                    def _useful(line: str) -> bool:
                        s = line.strip()
                        if not s or len(s) < 8:
                            return False
                        if s.startswith("#") or s.startswith("---") or s.startswith("==="):
                            return False
                        if s.startswith("```") or s in ("DONE", "BLOCKED"):
                            return False
                        if s.startswith("{"):  # raw JSON line — skip
                            return False
                        return True
                    lines = [
                        line.strip()
                        for line in output_section.splitlines()
                        if _useful(line)
                    ]
                    if lines:
                        status = _clip_stream_status(lines[-1], 120)
                except Exception:
                    pass
            ts = time.strftime("%H:%M:%S")
            msg = f"  ⟳ [{ts}] {status} ({elapsed}s)"
            # Atualiza in-place: só sobrescreve a linha corrente sem avançar
            print(f"\r{msg:<{term_width}}", end="", flush=True)
            time.sleep(10)

    log_file = None
    activity_file = None
    heartbeat = None
    try:
        if log_path:
            log_file = Path(log_path).open("a", encoding="utf-8")
            activity_path = activity_log_path(log_path)
            activity_path.parent.mkdir(parents=True, exist_ok=True)
            activity_file = activity_path.open("a", encoding="utf-8")

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
                reconcile_note = f"# ft: reconciliado via transcript ({reason})"
                log_file.write(f"{reconcile_note}\n{synth}\n")
                if activity_file:
                    write_activity(
                        activity_file,
                        reconcile_note,
                        source="engine",
                    )
                    write_activity(
                        activity_file,
                        synth,
                        source="reconciled_stream",
                    )
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
                        _stop_process_tree(proc, process_group=proc.pid)
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
            line_counts_as_activity = not (
                llm_engine == "opencode" and _is_opencode_internal_log_line(line)
            )
            if line_counts_as_activity:
                last_data = time.time()
                if activity is not None:
                    activity["last"] = last_data
                    activity.setdefault("first", last_data)
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
                if activity_file:
                    write_activity(activity_file, line, source="stream")
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
                                            t = _stream_oneline(block.get("text", ""))
                                            if t:
                                                decoded = f"→ {t}"
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
                                        cmd_text = _stream_oneline(item.get("command") or "")
                                        decoded = f"$ {cmd_text}"
                                    elif itype == "agent_message":
                                        msg_text = _stream_oneline(item.get("text") or "")
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
                    last_status[0] = _clip_stream_status(stripped, 120)
                status = _live_status(llm_engine, line, ctx)
                if status:
                    status = _clip_stream_status(status, 120)
                    last_status[0] = status
                    elapsed = int(time.time() - start_time)
                    # Inline: sempre nova linha — cada ação fica visível no scrollback
                    if status != printed_status[0]:
                        _print_inline(status, elapsed)
    finally:
        if log_file:
            log_file.close()
        if activity_file:
            activity_file.close()
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
    raw_output: bool = False,
    llm_effort: str | None = None,
    llm_timeout_seconds: int | None = None,
    llm_session_id: str | None = None,
    llm_session_resume: bool = False,
    workflow_id: str | None = None,
    ft_cycle: str | None = None,
    codex_auth: str | None = None,
) -> DelegateResult:
    """
    Chama o executor LLM configurado como subprocesso para executar uma tarefa de construcao.

    O LLM recebe um prompt restritivo: so pode escrever nos paths permitidos,
    nao pode editar ft_state.yml, nao pode tomar decisoes de processo.
    """
    if llm_timeout_seconds is not None and (
        isinstance(llm_timeout_seconds, bool)
        or not isinstance(llm_timeout_seconds, int)
        or llm_timeout_seconds <= 0
    ):
        raise ValueError("llm_timeout_seconds deve ser um inteiro positivo")
    max_wall_timeout = _executor_max_wall_timeout_seconds(llm_engine)
    max_wall_deadline = (
        time.monotonic() + max_wall_timeout
        if max_wall_timeout is not None
        else None
    )
    delegate_started_wall = time.time()

    paths_str = ", ".join(allowed_paths) if allowed_paths else "src/, tests/, docs/"
    opencode_capture_mode = bool(
        llm_engine.lower().strip() == "opencode" and opencode_capture_output_path
    )
    opencode_bundle_mode = bool(
        llm_engine.lower().strip() == "opencode"
        and opencode_deny_edit_tools
        and not opencode_capture_mode
        and _env_truthy("FT_OPENCODE_BUNDLE_MODE")
    )
    opencode_script_mode = bool(
        llm_engine.lower().strip() == "opencode"
        and opencode_deny_edit_tools
        and not opencode_capture_mode
        and not opencode_bundle_mode
        and _env_truthy("FT_OPENCODE_SCRIPT_MODE")
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
    autonomy_rule = (
        "- Seja objetivo: quando tiver informacao suficiente, aja. Nao reanalise fatos ja estabelecidos, "
        "nao faca pesquisas amplas sem necessidade e nao explique raciocinio interno.\n"
        "- Mantenha o escopo estrito do node. Nao adicione funcionalidades, refactors, abstracoes, "
        "fallbacks ou validacoes fora do que a tarefa e os validadores pedem.\n"
        "- Baseie progresso e conclusoes em evidencia real de arquivos, comandos ou validadores que voce "
        "acabou de observar. Se algo nao foi verificado, declare como nao verificado no NODE_SUMMARY.\n"
        "- NUNCA encerre, mate ou reinicie processos que nao tenham sido iniciados por esta propria "
        "delegacao. Em conflito de porta, use uma porta alternativa somente quando o contrato permitir; "
        "caso contrario, responda BLOCKED com a identidade do listener existente.\n"
        "- Voce esta operando de forma autonoma. Nao pergunte se deve prosseguir em acoes reversiveis "
        "e coerentes com a tarefa; prossiga ate DONE ou BLOCKED.\n"
    )
    if raw_output:
        write_tool_rule = (
            "- NAO use ferramentas de escrita; esta tarefa deve retornar somente texto estruturado.\n"
            "- Retorne exatamente o formato solicitado pela tarefa, sem markdown, sem explicacoes "
            "e sem texto antes ou depois."
        )
        completion_rule = (
            "- Nao inclua DONE, NODE_SUMMARY ou lista de arquivos.\n"
            "- Se nao conseguir produzir o formato solicitado, responda apenas: BLOCKED: <motivo>."
        )
    elif opencode_capture_mode:
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
    elif opencode_bundle_mode:
        write_tool_rule = (
            "- NAO use ferramentas. NAO use Read, Glob, Grep, List, Bash, Write, Edit ou Patch.\n"
            "- Responda SOMENTE com blocos XML de arquivo no protocolo abaixo; o engine gravara os arquivos.\n"
            "- Para cada arquivo, use exatamente:\n"
            "<ft_file path=\"path/relativo\">\n"
            "conteudo completo do arquivo\n"
            "</ft_file>\n"
            "- Use apenas paths relativos dentro dos paths permitidos. Nunca use /tmp, /home ou paths absolutos.\n"
            "- Inclua o conteudo completo dos arquivos, nao diffs e nao trechos parciais.\n"
            "- Nao inclua explicacoes, DONE, NODE_SUMMARY, cercas markdown ou comandos shell."
        )
        completion_rule = (
            "- Se nao conseguir produzir os arquivos, responda apenas: BLOCKED: <motivo>.\n"
            "- Caso contrario, retorne somente os blocos <ft_file path=\"...\">."
        )
    elif opencode_script_mode:
        write_tool_rule = (
            "- NAO use ferramentas. NAO use Read, Glob, Grep, List, Bash, Write, Edit ou Patch.\n"
            "- Responda SOMENTE com um script Bash completo que o engine executara no diretorio de trabalho.\n"
            "- O script deve comecar com `set -euo pipefail`.\n"
            "- O script deve criar/modificar os arquivos reais usando paths relativos permitidos.\n"
            "- O script deve criar diretorios pai antes de escrever arquivos.\n"
            "- O script deve rodar os comandos de validacao relevantes antes de terminar.\n"
            "- Nao inclua explicacoes, DONE, NODE_SUMMARY ou cercas markdown; retorne apenas o script."
        )
        completion_rule = (
            "- Se nao conseguir produzir o script, responda apenas: BLOCKED: <motivo>.\n"
            "- Caso contrario, retorne somente o script Bash final."
        )
    elif opencode_deny_edit_tools:
        write_tool_rule = (
            "- OBRIGATORIO: antes de dizer DONE, use Bash para criar ou modificar "
            "cada arquivo de saida esperado. NAO use Write/Edit/Patch neste node; "
            "o OpenCode pode corromper nomes de arquivos quando escreve codigo/JSON por edit.\n"
            "- Para criar arquivos, use comandos independentes com paths explicitos, por exemplo: "
            "`mkdir -p project/frontend && cat > project/frontend/package.json <<'EOF' ... EOF`. "
            "Nao dependa de `cd` persistente entre comandos.\n"
            "- Ao usar redirecionamento (`>`), o destino deve estar dentro dos paths permitidos. "
            "Nunca escreva arquivos soltos na raiz do worktree, como `package-temp` ou `package.json`, "
            "a menos que esse path esteja explicitamente permitido.\n"
            "- Se receber `No such file or directory`, corrija criando o diretorio pai com `mkdir -p` "
            "no mesmo comando. Se receber `Read-only file system`, voce tentou escrever fora do "
            "escopo permitido; reescreva no path permitido relativo, nao em path absoluto inventado.\n"
            "- Se o contrato pedir `project/...`, crie somente paths abaixo de `project/`; "
            "nao crie `frontend/`, `backend/`, `src/` ou outros diretorios de produto na raiz.\n"
            "- NAO rode comandos interativos como `npm init`, `npm create` ou `npx` sem `--yes`. "
            "Crie arquivos de configuracao manualmente ou use flags nao-interativas.\n"
            "- Cada comando Bash deve ser completo e independente: use "
            "`(cd project/frontend && npm run build --silent)` em vez de depender de um `cd` anterior.\n"
            "- NAO leia ou liste `node_modules`, `.git`, `state/llm_logs` ou dumps grandes. "
            "Use checks pontuais nos arquivos que voce acabou de criar."
        )
    else:
        placeholder_rule = ""
        if llm_engine.lower().strip() == "opencode":
            placeholder_rule = (
                "- Se um arquivo de saida ja existir vazio, trate-o como placeholder "
                "do sandbox. Nao leia esse arquivo antes de escrever; sobrescreva-o "
                "com Write/Edit/Patch.\n"
                "- Ao usar Write no OpenCode, use exatamente os campos `path` e `content`; "
                "nunca use `filePath`.\n"
                "- Ao usar Edit no OpenCode, use exatamente os campos `path`, `oldString`, "
                "`newString` e opcionalmente `replaceAll`; nunca use `filePath`.\n"
                "- NAO rode comandos interativos como `npm init`, `npm create` ou `npx` sem `--yes`. "
                "Crie arquivos de configuracao manualmente ou use flags nao-interativas.\n"
                "- Cada comando Bash deve ser completo e independente; nao dependa de `cd` persistente.\n"
                "- NAO leia ou liste `node_modules`, `.git`, `state/llm_logs` ou dumps grandes.\n"
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
- Para executar build e testes, voce PODE ler toolchains, SDKs, caches e dependencias
  de sistema referenciados pelo ambiente ou pelo projeto (por exemplo Android SDK e
  cache do Gradle). Isso nao autoriza escrever nesses locais nem ler outros checkouts.
- Escreva APENAS nos paths permitidos: {paths_str}
- Use o CONTEXTO EXISTENTE do prompt como fonte primaria. Evite reler arquivos
  markdown grandes que ja apareceram no prompt; se precisar de um detalhe,
  busque apenas o trecho minimo necessario dentro do diretorio de trabalho.
{deny_reads_rule}
{restricted_tools_rule}
- NAO edite ft_state.yml ou qualquer arquivo de estado do motor
- NAO tome decisoes sobre o processo (o motor decide)
- NAO use `git checkout`, `git reset`, `git restore`, `git clean` ou `git revert`
  para descartar mudancas do worktree. Corrija incrementalmente os arquivos
  necessarios; o ciclo pode conter alteracoes validas de tentativas anteriores.
{autonomy_rule}
{write_tool_rule}
{completion_rule}
"""

    cmd = _build_executor_command(
        llm_engine,
        prompt,
        project_root,
        max_turns,
        model=llm_model,
        effort=llm_effort,
        session_id=llm_session_id,
        resume_session=llm_session_resume,
        workflow_id=workflow_id,
        ft_cycle=ft_cycle,
        codex_auth=codex_auth,
    )
    if opencode_capture_mode or opencode_bundle_mode or opencode_script_mode:
        cmd = _opencode_capture_command(cmd)

    # Linux limita cada argumento de execve a ~128 KiB (MAX_ARG_STRLEN).
    # Prompts hyper-mode estouram isso ([Errno 7] Argument list too long) —
    # acima do limiar, o prompt sai do argv e vai via stdin. Claude lê stdin
    # quando ``-p`` fica sem argumento; Codex lê quando recebe ``-`` no lugar
    # do prompt (inclusive em ``exec resume``).
    stdin_prompt: str | None = None
    engine = llm_engine.lower().strip()
    if (
        engine in {"claude", "codex"}
        and cmd
        and cmd[-1] == prompt
        and len(prompt.encode("utf-8")) > _MAX_ARGV_PROMPT_BYTES
    ):
        if engine == "claude":
            cmd = cmd[:-1]  # mantém o -p final
        else:
            cmd[-1] = "-"
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
        opencode_text_only=opencode_capture_mode or opencode_bundle_mode or opencode_script_mode,
        workflow_id=workflow_id,
        ft_cycle=ft_cycle,
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
    # Capture mode is text-only: the engine writes the final response after the
    # executor exits. If an output file already exists from a previous attempt,
    # early-success would stop the retry before the model emits text and then
    # overwrite the artifact with stream noise.
    early_success_paths = (
        []
        if opencode_capture_mode or opencode_bundle_mode or opencode_script_mode
        else _resolve_existing_file_paths(project_root, opencode_early_success_paths)
    )
    early_success_grace = _env_positive_int("FT_OPENCODE_EARLY_SUCCESS_GRACE") or 20

    if log_path and llm_engine != "codex":
        _write_log_preamble(log_path, llm_engine, cmd, prompt)
    elif log_path:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)

    idle_timeout = _executor_idle_timeout_seconds(llm_engine, llm_timeout_seconds)
    idle_grace = _executor_idle_grace_seconds(llm_engine)
    idle_retries = 0
    progress_probe_interval = (
        _env_positive_int("FT_WORKTREE_PROGRESS_INTERVAL")
        or DEFAULT_PROGRESS_PROBE_INTERVAL
    )
    workspace_paths = _workspace_progress_paths(project_root, allowed_paths)
    if llm_engine.lower().strip() == "opencode":
        configured_retries = _env_nonnegative_int("FT_OPENCODE_IDLE_RETRIES")
        idle_retries = configured_retries if configured_retries is not None else 2
        if opencode_capture_mode:
            idle_timeout = _env_positive_int(
                "FT_OPENCODE_CAPTURE_IDLE_TIMEOUT"
            ) or min(idle_timeout or DEFAULT_STREAM_IDLE_TIMEOUT, 120)
            capture_retries = _env_nonnegative_int("FT_OPENCODE_CAPTURE_IDLE_RETRIES")
            idle_retries = capture_retries if capture_retries is not None else 0

    cleaned_runtime = False
    attempt_activity: dict[str, float] = {}
    progress_reported_at: dict[str, float] = {}

    def _cleanup_delegate_runtime() -> None:
        nonlocal cleaned_runtime, sandbox_tmp
        if cleaned_runtime:
            return
        _cleanup_empty_placeholders(sandbox_mounts)
        if sandbox_tmp is not None:
            _append_opencode_runtime_diagnostics(Path(sandbox_tmp.name), log_path)
            sandbox_tmp.cleanup()
            sandbox_tmp = None
        cleaned_runtime = True

    def _append_log(message: str) -> None:
        if log_path:
            with Path(log_path).open("a", encoding="utf-8") as f:
                f.write(message)
            append_activity(log_path, message, source="engine")

    def _record_idle_grace(diagnostics: dict[str, int]) -> None:
        summary = (
            "\n[PRODUCTIVITY_CHECK] Nenhuma progressão observável; "
            f"processos={diagnostics['processes']} "
            f"sockets={diagnostics['sockets']} "
            f"fds={diagnostics['fds']} "
            f"verificação_final={diagnostics['grace_seconds']}s.\n"
        )
        print(
            "  ! Janela de inatividade atingida; worktree/processo estagnados — "
            f"verificação final por {diagnostics['grace_seconds']}s"
        )
        _append_log(summary)

    def _record_progress(source: str, diagnostics: dict[str, int]) -> None:
        now = time.monotonic()
        previous = progress_reported_at.get(source)
        if previous is not None and now - previous < 60:
            return
        progress_reported_at[source] = now
        if source == "workspace":
            detail = (
                f"files_delta={diagnostics['files_delta']} "
                f"bytes_delta={diagnostics['bytes_delta']} "
                f"source_files_delta={diagnostics['source_files_delta']} "
                f"source_bytes_delta={diagnostics['source_bytes_delta']}"
            )
        else:
            detail = (
                f"cpu_delta_ticks={diagnostics['cpu_delta_ticks']} "
                f"io_delta_chars="
                f"{diagnostics['read_delta_chars'] + diagnostics['write_delta_chars']} "
                f"processes_delta={diagnostics['processes_delta']}"
            )
        _append_log(f"\n[PRODUCTIVITY_RENEWED] source={source} {detail}\n")

    def _remaining_max_wall() -> float | None:
        if max_wall_deadline is None:
            return None
        return max(0.0, max_wall_deadline - time.monotonic())

    def _max_wall_message() -> str:
        return (
            "\n[MAX_WALL_TIMEOUT] Teto absoluto opt-in da delegação excedeu "
            f"{max_wall_timeout} segundos.\n"
        )

    def _stop_process(proc: subprocess.Popen) -> None:
        _stop_process_tree(
            proc,
            terminate_timeout=2,
            kill_timeout=1,
            process_group=proc.pid,
        )

    def _run_executor_attempt() -> tuple[int, bool, str, str | None]:
        """Executa uma tentativa. failure_kind: idle | max_wall | timeout | None."""
        nonlocal attempt_activity
        remaining = _remaining_max_wall()
        if remaining is not None and remaining <= 0:
            msg = _max_wall_message()
            _append_log(msg)
            return 124, False, msg, "max_wall"
        attempt_timeout = max(0.01, remaining) if remaining is not None else None

        # Chamar executor em modo nao-interativo, com streaming para arquivo.
        # PATH completo: o template v3 tem frontend Node (npm/vite) — a poda antiga
        # de nvm/node quebrava os nodes de frontend (worker sem npm reporta BLOCKED).
        proc = subprocess.Popen(
            _supervised_command(cmd),
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
        activity = {"last": time.time(), "started": time.time()}
        attempt_activity = activity
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
                timeout=attempt_timeout,
                early_success_paths=early_success_paths,
                early_success_grace=early_success_grace,
                activity=activity,
                idle_timeout=idle_timeout,
                idle_grace=idle_grace,
                on_idle_grace=_record_idle_grace,
                workspace_probe=lambda: _workspace_progress_snapshot(
                    workspace_paths,
                    project_root,
                ),
                progress_probe_interval=progress_probe_interval,
                on_progress=_record_progress,
            )
        except ExecutorIdleTimeout:
            _stop_process(proc)
            reader.join(timeout=5)
            msg = (
                "\n[INACTIVITY_TIMEOUT] Nenhuma atividade observável no stream, "
                "worktree ou processo por "
                f"{idle_timeout} segundos.\n"
            )
            _append_log(msg)
            return 124, False, output_holder["output"] + msg, "idle"
        except subprocess.TimeoutExpired:
            _stop_process(proc)
            reader.join(timeout=5)
            msg = _max_wall_message()
            failure_kind = "max_wall"
            _append_log(msg)
            return 124, False, output_holder["output"] + msg, failure_kind
        except BaseException:
            _stop_process(proc)
            reader.join(timeout=5)
            raise

        reader.join(timeout=5)
        if reader.is_alive():
            _stop_process(proc)
            if proc.stdout is not None:
                proc.stdout.close()
            reader.join(timeout=1)
            msg = "\n[STREAM_TIMEOUT] Saída do executor não foi encerrada.\n"
            _append_log(msg)
            return 124, False, output_holder["output"] + msg, "timeout"
        early_success_msg = ""
        if early_success:
            early_success_msg = (
                "\n[EARLY_SUCCESS] Outputs esperados existem; encerrando OpenCode "
                "para validação determinística.\n"
            )
            _append_log(early_success_msg)
        return returncode, early_success, output_holder["output"] + early_success_msg, None

    def _extract_output(raw: str, engine: str) -> str:
        if opencode_capture_mode or opencode_bundle_mode or opencode_script_mode:
            return _extract_opencode_json_text(raw)
        if engine == "codex":
            return _extract_codex_output(raw)
        if engine == "claude":
            return _extract_claude_json_output(raw)
        return raw

    def _delegate_timings() -> dict[str, float]:
        now_wall = time.time()
        started_wall = attempt_activity.get("started")
        first_wall = attempt_activity.get("first")
        timings = {
            "provider_wall_seconds": round(
                max(0.0, time.time() - delegate_started_wall),
                3,
            ),
        }
        if started_wall is not None and first_wall is not None:
            timings["startup_to_first_event_seconds"] = round(
                max(0.0, first_wall - started_wall),
                3,
            )
            timings["turn_after_first_event_seconds"] = round(
                max(0.0, now_wall - first_wall),
                3,
            )
        for key in ("workspace_renewals", "process_renewals"):
            value = attempt_activity.get(key)
            if value is not None:
                timings[key] = float(value)
        return timings

    try:
        max_wall_exhausted = False
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
                failed_output = _extract_output(raw_output, llm_engine)
                _cleanup_delegate_runtime()
                return DelegateResult(
                    success=False,
                    output=failed_output,
                    files_created=[],
                    files_modified=[],
                    session_id=(
                        _extract_provider_session_id(llm_engine, raw_output)
                        or llm_session_id
                    ),
                    session_resumed=bool(
                        llm_session_id and llm_session_resume
                    ),
                    session_error=_is_session_resume_error(
                        failed_output,
                        resumed=bool(llm_session_id and llm_session_resume),
                    ),
                    timings=_delegate_timings(),
                )
            break

        provider_session_id = (
            _extract_provider_session_id(llm_engine, raw_output)
            or llm_session_id
        )
        output = _extract_output(raw_output, llm_engine)

        # Detectar rate limit e fazer retry com backoff exponencial
        if _attempt_rate_limited(llm_engine, returncode, output):
            _backoff_schedule = _rate_limit_backoff_schedule()
            for attempt, wait in enumerate(_backoff_schedule, start=1):
                print(f"\n  ⚠️  Rate limit detectado ({llm_engine}). "
                      f"Aguardando {wait}s antes da tentativa {attempt}/{len(_backoff_schedule)}…")
                remaining = _remaining_max_wall()
                if remaining is not None and wait >= remaining:
                    max_wall_exhausted = True
                    returncode = 124
                    output = _max_wall_message()
                    _append_log(output)
                    break
                time.sleep(wait)
                rc2, _early_success2, raw2, failure2 = _run_executor_attempt()
                out2 = _extract_output(raw2, llm_engine)
                if failure2:
                    output = out2
                    returncode = rc2
                    max_wall_exhausted = failure2 == "max_wall"
                    break
                if not _attempt_rate_limited(llm_engine, rc2, out2):
                    output = out2
                    returncode = rc2
                    break
                output = out2  # última tentativa falhou também

        success = returncode == 0
        died = False
        if opencode_capture_mode and opencode_capture_output_path:
            captured = _clean_opencode_capture_text(output)
            capture_blocked = captured.lstrip().upper().startswith("BLOCKED")
            if returncode != 0:
                success = False
            elif not captured:
                success = False
                output = f"{output}\n[CAPTURE_EMPTY] OpenCode nao retornou conteudo gravavel."
            elif capture_blocked:
                success = False
                output = captured
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
        elif opencode_bundle_mode:
            bundle = _clean_opencode_capture_text(output)
            bundle_blocked = bundle.lstrip().upper().startswith("BLOCKED")
            if returncode != 0:
                success = False
            elif not bundle:
                success = False
                output = f"{output}\n[OPENCODE_BUNDLE_EMPTY] OpenCode nao retornou arquivos materializaveis."
            elif bundle_blocked:
                success = False
                output = bundle
            else:
                bundle_ok, bundle_output = _materialize_opencode_file_bundle(
                    bundle,
                    project_root=project_root,
                    allowed_paths=allowed_paths,
                    log_path=log_path,
                )
                success = returncode == 0 and bundle_ok
                if success:
                    output = (
                        "DONE\n"
                        "File bundle gerado pelo OpenCode e materializado pelo engine.\n"
                        f"{bundle_output}"
                    )
                else:
                    output = (
                        "BLOCKED: file bundle gerado pelo OpenCode falhou.\n"
                        f"{bundle_output}"
                    )
        elif opencode_script_mode:
            script = _clean_opencode_capture_text(output)
            script_blocked = script.lstrip().upper().startswith("BLOCKED")
            if returncode != 0:
                success = False
            elif not script:
                success = False
                output = f"{output}\n[OPENCODE_SCRIPT_EMPTY] OpenCode nao retornou script executavel."
            elif script_blocked:
                success = False
                output = script
            else:
                remaining = _remaining_max_wall()
                if remaining is not None and remaining <= 0:
                    max_wall_exhausted = True
                    script_ok = False
                    script_output = _max_wall_message()
                    _append_log(script_output)
                else:
                    script_deadline_limited = (
                        remaining is not None and remaining < 1800.0
                    )
                    script_ok, script_output = _run_opencode_script(
                        script,
                        project_root=project_root,
                        allowed_paths=allowed_paths,
                        env=_env,
                        log_path=log_path,
                        runtime_dir=(
                            sandbox_tmp.name if sandbox_tmp is not None else None
                        ),
                        timeout_seconds=(
                            min(1800.0, remaining)
                            if remaining is not None
                            else None
                        ),
                    )
                    if (
                        script_deadline_limited
                        and not script_ok
                        and "[TIMEOUT]" in script_output
                    ):
                        max_wall_exhausted = True
                        script_output += _max_wall_message()
                        _append_log(_max_wall_message())
                success = returncode == 0 and script_ok
                if success:
                    output = (
                        "DONE\n"
                        "Script gerado pelo OpenCode e executado pelo engine.\n"
                        f"{script_output}"
                    )
                else:
                    output = (
                        "BLOCKED: script gerado pelo OpenCode falhou.\n"
                        f"{script_output}"
                    )
        else:
            token = _final_protocol_token(output)
            success = returncode == 0 and token != "BLOCKED"
            died = returncode != 0 and token is None
        rate_limited = (
            not max_wall_exhausted
            and (not success)
            and _rate_limit_signal(output)
        )
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
        died=died and not rate_limited,
        session_id=provider_session_id,
        session_resumed=bool(llm_session_id and llm_session_resume),
        session_error=_is_session_resume_error(
            output,
            resumed=bool(llm_session_id and llm_session_resume),
        ),
        timings=_delegate_timings(),
    )


def delegate_opencode_file_bundle_raw(
    prompt: str,
    project_root: str,
    allowed_paths: list[str] | None = None,
    llm_model: str | None = None,
    log_path: str | None = None,
    llm_effort: str | None = None,
) -> DelegateResult:
    """Ecoa/materializa um bundle sob a política global de produtividade."""
    result = delegate_to_llm(
        task=prompt,
        project_root=project_root,
        allowed_paths=allowed_paths,
        max_turns=1,
        llm_engine="opencode",
        llm_model=llm_model or DEFAULT_OPENCODE_MODEL,
        llm_effort=llm_effort,
        log_path=log_path,
        opencode_restrict_tools=True,
        raw_output=True,
    )
    bundle = _clean_opencode_capture_text(result.output)
    if not result.success:
        return DelegateResult(False, bundle or result.output, [], [])
    if bundle.lstrip().upper().startswith("BLOCKED"):
        return DelegateResult(False, bundle, [], [])
    ok, materialized = _materialize_opencode_file_bundle(
        bundle,
        project_root=project_root,
        allowed_paths=allowed_paths,
        log_path=log_path,
    )
    return DelegateResult(ok, materialized if ok else f"BLOCKED: {materialized}", [], [])


def delegate_opencode_exact_file_raw(
    path: str,
    content: str,
    project_root: str,
    allowed_paths: list[str] | None = None,
    llm_model: str | None = None,
    log_path: str | None = None,
    llm_effort: str | None = None,
) -> DelegateResult:
    """Ecoa conteúdo e grava um path sob a política global de produtividade."""
    prompt = f"Retorne exatamente este texto, sem markdown e sem explicacoes:\n{content}"
    result = delegate_to_llm(
        task=prompt,
        project_root=project_root,
        allowed_paths=allowed_paths,
        max_turns=1,
        llm_engine="opencode",
        llm_model=llm_model or DEFAULT_OPENCODE_MODEL,
        llm_effort=llm_effort,
        log_path=log_path,
        opencode_restrict_tools=True,
        raw_output=True,
    )
    output = _clean_opencode_capture_text(result.output).strip()
    if not result.success:
        return DelegateResult(False, output or result.output, [], [])
    if output.lstrip().upper().startswith("BLOCKED"):
        return DelegateResult(False, output, [], [])
    expected = content.strip()
    if output.strip() != expected:
        if path.endswith(".json"):
            try:
                parsed = json.loads(output)
                output = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
            except json.JSONDecodeError:
                pass
        elif expected in output:
            output = expected
        if path.endswith(".json"):
            try:
                json.loads(output)
            except json.JSONDecodeError:
                return DelegateResult(
                    False,
                    "BLOCKED: OpenCode nao retornou JSON valido para o arquivo esperado.",
                    [],
                    [],
                )
        elif output.strip() != expected:
            output = expected
    if not _write_scope_allows(path, project_root, allowed_paths):
        return DelegateResult(False, f"BLOCKED: path fora do escopo permitido: {path}", [], [])
    target = (Path(project_root).resolve() / path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(output.rstrip() + "\n", encoding="utf-8")
    return DelegateResult(True, f"DONE\nArquivo gravado pelo engine: {path}\n", [path], [])


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
    llm_effort: str | None = None,
    llm_timeout_seconds: int | None = None,
    llm_session_id: str | None = None,
    llm_session_resume: bool = False,
    workflow_id: str | None = None,
    ft_cycle: str | None = None,
    codex_auth: str | None = None,
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
        llm_effort=llm_effort,
        max_turns=max_turns,
        log_path=log_path,
        stream_prefix=stream_prefix,
        opencode_deny_read_paths=opencode_deny_read_paths,
        opencode_restrict_tools=opencode_restrict_tools,
        opencode_steps=opencode_steps,
        opencode_deny_edit_tools=opencode_deny_edit_tools,
        opencode_early_success_paths=opencode_early_success_paths,
        opencode_capture_output_path=opencode_capture_output_path,
        llm_timeout_seconds=llm_timeout_seconds,
        llm_session_id=llm_session_id,
        llm_session_resume=llm_session_resume,
        workflow_id=workflow_id,
        ft_cycle=ft_cycle,
        codex_auth=codex_auth,
    )
