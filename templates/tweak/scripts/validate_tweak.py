#!/usr/bin/env python3
"""Fast deterministic guardrails for the ``tweak`` feature template."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unicodedata

import yaml


REQUEST_PATH = Path("docs/feature-request.md")
BASELINE_PATH = Path("docs/tweak-baseline.yml")
REPORT_PATH = Path("docs/tweak-report.md")
GUARD_PATH = Path("state/tweak-guard.json")
FOCAL_RECEIPT_PATH = Path("state/tweak-focal.json")
ATTEMPT_PATH = Path("state/tweak-attempt.json")
MAX_REQUEST_CHARS = 1_200
MAX_FILES = 4
MAX_CHANGED_LINES = 160
MAX_FILE_BYTES = 256_000
MAX_PATCH_BYTES = 256_000
FOCAL_TIMEOUT_SECONDS = 60
QUICK_TIMEOUT_SECONDS = 90
MAX_REPORT_CHARS = 24_000
ALLOWED_TEXT_SUFFIXES = frozenset(
    {
        ".css",
        ".go",
        ".html",
        ".htm",
        ".java",
        ".js",
        ".cjs",
        ".json",
        ".jsx",
        ".kt",
        ".less",
        ".md",
        ".php",
        ".py",
        ".rb",
        ".rs",
        ".sass",
        ".scss",
        ".svelte",
        ".svg",
        ".toml",
        ".ts",
        ".cts",
        ".mjs",
        ".mts",
        ".tsx",
        ".txt",
        ".vue",
        ".xml",
        ".yaml",
        ".yml",
    }
)
CYCLE_PATHS = frozenset(
    {
        REQUEST_PATH,
        BASELINE_PATH,
        REPORT_PATH,
        GUARD_PATH,
        FOCAL_RECEIPT_PATH,
        ATTEMPT_PATH,
    }
)

_VISUAL_RE = re.compile(
    r"\b(?:ui|ux|frontend|interface|tela|pagina|page|botao|button|cor|color|"
    r"css|estilo|style|layout|icone|icon|modal|menu|sidebar|navbar|visual|"
    r"tema|theme|padding|margin|espacamento|spacing|tipografia|typography|"
    r"responsiv|hover)\b"
)
_COPY_RE = re.compile(
    r"\b(?:texto|text|copy|label|rotulo|titulo|title|tooltip|placeholder|"
    r"mensagem|message|wording)\b"
)
_BROAD_RE = re.compile(
    r"\b(?:todos|todas|globalmente|sistema inteiro|aplicacao inteira|"
    r"entire app|whole app|across the app|multiplas areas|frontend e backend)\b"
)
_ACTION_RE = re.compile(
    r"\b(?:mude|troque|ajuste|ajustar|altere|alterar|coloque|deixe|reduza|"
    r"aumente|alinhe|corrija|corrigir|remova|oculte|mostre|renomeie|envie|"
    r"enviar|submeta|submeter|change|replace|adjust|reduce|increase|align|"
    r"fix|remove|hide|show|update|set|rename|send|submit)\b"
)
_LOCAL_BEHAVIOR_RE = re.compile(
    r"\b(?:valor padrao|default value|ordenacao|sort|filtro|filter|toggle|"
    r"contador|counter|formatacao|format|validacao local|local validation|"
    r"condicao|condition|atalho|shortcut|calculo|calculation|subtotal|link|"
    r"confirmacao|confirmation|envio|send|submit|enter|ctrl)\b"
)
_RISK_PATTERNS = (
    (
        "dependências ou lockfiles",
        re.compile(
            r"\b(?:dependencia|dependency|dependencies|lockfile|package lock|"
            r"biblioteca|library|npm install|pnpm add|yarn add|pip install|"
            r"upgrade de pacote|atualiz(?:e|ar) o pacote)\b"
        ),
    ),
    (
        "banco, schema ou migração",
        re.compile(
            r"\b(?:banco de dados|database|migration|migracao|migrar dados|"
            r"schema|tabela|column|coluna|indice|index do banco)\b"
        ),
    ),
    (
        "autenticação, autorização ou segurança",
        re.compile(
            r"\b(?:auth|autenticacao|authentication|autorizacao|authorization|"
            r"permiss\w*|permission\w*|security|seguranca|oauth|token|senha|"
            r"password|login|sessao|session|credencial\w*|credential\w*)\b"
        ),
    ),
    (
        "API ou contrato público",
        re.compile(
            r"\b(?:api|endpoint|graphql|webhook|openapi|swagger|contrato publico|"
            r"public contract|payload|request body|response body)\b"
        ),
    ),
    (
        "CI/CD, deploy ou infraestrutura",
        re.compile(
            r"\b(?:ci/cd|pipeline|github actions|deploy|deployment|infra|infraestrutura|"
            r"infrastructure|terraform|kubernetes|docker|container|producao)\b"
        ),
    ),
    (
        "refactor ou nova capacidade",
        re.compile(
            r"\b(?:refator|refactor|novo fluxo|new flow|fluxo de login|login flow|"
            r"nova pagina|new page|"
            r"novo servico|new service|nova feature|new feature|arquitetura)\b"
        ),
    ),
)

_FORBIDDEN_COMPONENTS = frozenset(
    {
        ".ft",
        ".github",
        "api",
        "auth",
        "authentication",
        "authorization",
        "ci",
        "contracts",
        "database",
        "deploy",
        "deployment",
        "infra",
        "infrastructure",
        "k8s",
        "kubernetes",
        "migration",
        "migrations",
        "permissions",
        "schema",
        "schemas",
        "security",
        "terraform",
        "workflows",
    }
)
_FORBIDDEN_NAMES = frozenset(
    {
        "alembic.ini",
        "bun.lock",
        "bun.lockb",
        "cargo.lock",
        "cargo.toml",
        "composer.json",
        "composer.lock",
        "gemfile",
        "gemfile.lock",
        "go.mod",
        "go.sum",
        "makefile",
        "package-lock.json",
        "package.json",
        "pipfile",
        "pipfile.lock",
        "pnpm-lock.yaml",
        "poetry.lock",
        "pyproject.toml",
        "setup.cfg",
        "setup.py",
        "uv.lock",
        "yarn.lock",
    }
)
_SENSITIVE_PATH_TOKEN_RE = re.compile(
    r"(?:^|[^a-z0-9])(?:api|auth|authentication|authorization|security|"
    r"permission|permissions|migration|migrations|schema|schemas|database|"
    r"deploy|infra|terraform)(?:[^a-z0-9]|$)"
)
_SENSITIVE_PATH_PREFIX_RE = re.compile(
    r"^(?:api|auth|security|migration|schema|database|deploy|infra)"
    r"(?:client|service|route|controller|model|config|_|-|\.|$)"
)


class TweakValidationError(ValueError):
    """A deterministic, user-facing tweak validation failure."""


def _normalize_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(
        character for character in decomposed if not unicodedata.combining(character)
    ).lower()


def _find_root(start: Path) -> Path:
    current = start.resolve()
    for candidate in (current, *current.parents):
        manifest = candidate / ".ft" / "manifest.yml"
        if manifest.is_file() and not manifest.is_symlink():
            return candidate
    raise TweakValidationError("raiz FT não encontrada")


def _safe_path(root: Path, relative: Path, *, allow_missing: bool = False) -> Path:
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise TweakValidationError(f"path inseguro: {relative}")
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise TweakValidationError(f"path não pode conter symlink: {relative}")
    if not allow_missing and not current.is_file():
        raise TweakValidationError(f"arquivo ausente: {relative.as_posix()}")
    return current


def _read_bounded(root: Path, relative: Path, limit: int) -> str:
    path = _safe_path(root, relative)
    try:
        with path.open("r", encoding="utf-8", errors="strict") as handle:
            content = handle.read(limit + 1)
    except (OSError, UnicodeError) as exc:
        raise TweakValidationError(f"não foi possível ler {relative}: {exc}") from exc
    if len(content) > limit:
        raise TweakValidationError(
            f"{relative.as_posix()} excede o limite de {limit} caracteres"
        )
    return content


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink() or path.is_symlink():
        raise TweakValidationError(f"destino não pode ser symlink: {path}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _git(root: Path, *arguments: str, timeout: int = 10) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *arguments],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TweakValidationError(f"git falhou: {type(exc).__name__}") from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise TweakValidationError(
            f"git {' '.join(arguments)} falhou: {detail or result.returncode}"
        )
    return result.stdout


def _request_body(content: str) -> str:
    stripped = content.lstrip()
    if not stripped.startswith("---\n"):
        return content.strip()
    _start, separator, remainder = stripped.partition("\n---\n")
    return remainder.strip() if separator else content.strip()


def _classify_request(request: str) -> str:
    if not request:
        raise TweakValidationError("demanda vazia")
    if len(request) > MAX_REQUEST_CHARS:
        raise TweakValidationError(
            f"demanda excede {MAX_REQUEST_CHARS} caracteres; use --template feature"
        )
    task_lines = [
        line
        for line in request.splitlines()
        if re.match(r"^\s*(?:[-*+] |\d+[.)] )", line)
    ]
    if len(task_lines) > 1:
        raise TweakValidationError(
            "demanda contém múltiplas tarefas; use --template feature"
        )
    normalized = _normalize_text(request)
    if _BROAD_RE.search(normalized):
        raise TweakValidationError(
            "demanda parece ampla ou transversal; use --template feature"
        )
    for label, pattern in _RISK_PATTERNS:
        if pattern.search(normalized):
            raise TweakValidationError(
                f"demanda envolve {label}; use --template feature"
            )
    if not _ACTION_RE.search(normalized):
        raise TweakValidationError(
            "demanda não contém uma ação focal inequívoca; use --template feature"
        )
    if _COPY_RE.search(normalized):
        return "copy"
    if _VISUAL_RE.search(normalized):
        return "visual"
    if _LOCAL_BEHAVIOR_RE.search(normalized):
        return "minor_behavior"
    raise TweakValidationError(
        "demanda não identifica um alvo local seguro; use --template feature"
    )


def _product_root(root: Path) -> str:
    candidates = [
        name
        for name in ("project", "src")
        if (root / name / "Makefile").is_file()
        and not (root / name).is_symlink()
        and not (root / name / "Makefile").is_symlink()
    ]
    if len(candidates) != 1:
        raise TweakValidationError(
            "esperado exatamente um Makefile em project/ ou src/"
        )
    return candidates[0]


def _reject_hidden_index_entries(root: Path) -> None:
    """Reject assume-unchanged/skip-worktree and other non-normal index tags."""
    raw = _git(
        root,
        "ls-files",
        "-v",
        "-z",
        "--",
        "project",
        "src",
        "test",
        "tests",
    )
    offenders: list[str] = []
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        decoded = os.fsdecode(entry)
        if len(decoded) < 3 or decoded[1] != " ":
            offenders.append(decoded)
            continue
        tag, relative = decoded[0], decoded[2:]
        # Normal cached files are tagged H. Lower-case tags denote
        # assume-unchanged; S denotes skip-worktree. Other tags are likewise
        # unsafe for a diff-based scope guard.
        if tag != "H":
            offenders.append(f"{tag} {relative}")
    if offenders:
        raise TweakValidationError(
            "índice Git contém flags/estados que escondem mudanças: "
            + ", ".join(offenders[:8])
        )


def _sha256_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _command_preflight(root: Path) -> None:
    request_content = _read_bounded(root, REQUEST_PATH, MAX_REQUEST_CHARS + 512)
    request = _request_body(request_content)
    classification = _classify_request(request)
    product_root = _product_root(root)
    base_commit = _git(root, "rev-parse", "HEAD").decode("ascii").strip()
    if not re.fullmatch(r"[0-9a-f]{40,64}", base_commit):
        raise TweakValidationError("HEAD Git inválido")
    _reject_hidden_index_entries(root)
    dirty = _git(
        root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--",
        "project",
        "src",
        "test",
        "tests",
    ).decode("utf-8", errors="replace")
    if dirty.strip():
        raise TweakValidationError(
            "baseline do produto já contém mudanças; reinicie a partir de checkout limpo"
        )

    baseline = {
        "schema_version": 1,
        "kind": "ft.tweak.baseline",
        "base_commit": base_commit,
        "product_root": product_root,
        "classification": classification,
        "request_sha256": _sha256_text(request_content),
        "max_files": MAX_FILES,
        "max_changed_lines": MAX_CHANGED_LINES,
        "max_file_bytes": MAX_FILE_BYTES,
        "max_patch_bytes": MAX_PATCH_BYTES,
    }
    baseline_text = yaml.safe_dump(
        baseline,
        allow_unicode=True,
        sort_keys=False,
    )
    baseline_path = _safe_path(root, BASELINE_PATH, allow_missing=True)
    _atomic_write(baseline_path, baseline_text)
    guard = {
        **baseline,
        "baseline_sha256": _sha256_text(baseline_text),
    }
    guard_path = _safe_path(root, GUARD_PATH, allow_missing=True)
    _atomic_write(
        guard_path,
        json.dumps(guard, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
    )
    for runtime_path in (ATTEMPT_PATH, FOCAL_RECEIPT_PATH):
        _safe_path(root, runtime_path, allow_missing=True).unlink(missing_ok=True)
    print(
        "tweak preflight PASS: "
        f"classification={classification}, product_root={product_root}, "
        f"limits={MAX_FILES} files/{MAX_CHANGED_LINES} lines"
    )


def _load_guard(root: Path) -> dict[str, object]:
    guard_text = _read_bounded(root, GUARD_PATH, 8_000)
    try:
        guard = json.loads(guard_text)
    except json.JSONDecodeError as exc:
        raise TweakValidationError("guard interno inválido") from exc
    expected_keys = {
        "schema_version",
        "kind",
        "base_commit",
        "product_root",
        "classification",
        "request_sha256",
        "max_files",
        "max_changed_lines",
        "max_file_bytes",
        "max_patch_bytes",
        "baseline_sha256",
    }
    if not isinstance(guard, dict) or set(guard) != expected_keys:
        raise TweakValidationError("guard interno incompleto")
    if (
        guard.get("schema_version") != 1
        or guard.get("kind") != "ft.tweak.baseline"
        or guard.get("max_files") != MAX_FILES
        or guard.get("max_changed_lines") != MAX_CHANGED_LINES
        or guard.get("max_file_bytes") != MAX_FILE_BYTES
        or guard.get("max_patch_bytes") != MAX_PATCH_BYTES
        or guard.get("product_root") not in {"project", "src"}
        or not isinstance(guard.get("base_commit"), str)
        or not re.fullmatch(r"[0-9a-f]{40,64}", str(guard.get("base_commit")))
        or not isinstance(guard.get("request_sha256"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", str(guard.get("request_sha256")))
        or not isinstance(guard.get("baseline_sha256"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", str(guard.get("baseline_sha256")))
    ):
        raise TweakValidationError("guard interno possui valores inválidos")
    baseline_text = _read_bounded(root, BASELINE_PATH, 8_000)
    if _sha256_text(baseline_text) != guard["baseline_sha256"]:
        raise TweakValidationError("baseline do tweak foi alterada")
    request_text = _read_bounded(root, REQUEST_PATH, MAX_REQUEST_CHARS + 512)
    if _sha256_text(request_text) != guard["request_sha256"]:
        raise TweakValidationError("demanda original do tweak foi alterada")
    return guard


def _changed_entries(root: Path, base_commit: str) -> list[tuple[str, str]]:
    raw = _git(root, "diff", "--name-status", "-z", base_commit, "--")
    tokens = [token for token in raw.split(b"\0") if token]
    entries: list[tuple[str, str]] = []
    index = 0
    while index < len(tokens):
        status_text = os.fsdecode(tokens[index])
        index += 1
        status_code = status_text[:1]
        if not status_code:
            raise TweakValidationError("status Git vazio")
        if status_code in {"R", "C"}:
            if index + 1 >= len(tokens):
                raise TweakValidationError("status Git de rename/copy inválido")
            old_path = os.fsdecode(tokens[index])
            new_path = os.fsdecode(tokens[index + 1])
            index += 2
            entries.extend([(status_code, old_path), (status_code, new_path)])
        else:
            if index >= len(tokens):
                raise TweakValidationError("status Git sem path")
            entries.append((status_code, os.fsdecode(tokens[index])))
            index += 1

    untracked = _git(root, "ls-files", "--others", "--exclude-standard", "-z", "--")
    tracked_paths = {relative for _status, relative in entries}
    for raw_path in untracked.split(b"\0"):
        if not raw_path:
            continue
        relative = os.fsdecode(raw_path)
        if relative not in tracked_paths:
            entries.append(("A", relative))
    return entries


def _working_tree_fingerprint(root: Path, base_commit: str) -> str:
    """Fingerprint every non-cycle working-tree change, including untracked files."""
    entries = sorted(
        _changed_entries(root, base_commit), key=lambda item: (item[1], item[0])
    )
    if len(entries) > 64:
        raise TweakValidationError("working tree amplo demais para validação focal")
    digest = hashlib.sha256()
    for status_code, relative in entries:
        if _is_cycle_path(root, relative):
            continue
        path = Path(relative)
        if path.is_absolute() or not path.parts or ".." in path.parts:
            raise TweakValidationError(f"path inseguro no working tree: {relative}")
        digest.update(status_code.encode("ascii", errors="replace"))
        digest.update(b"\0")
        digest.update(os.fsencode(relative))
        digest.update(b"\0")
        candidate = root.joinpath(*path.parts)
        if not candidate.exists() and not candidate.is_symlink():
            digest.update(b"<missing>\0")
            continue
        if candidate.is_symlink():
            raise TweakValidationError(f"symlink não permitido: {relative}")
        metadata = candidate.lstat()
        if not stat.S_ISREG(metadata.st_mode):
            raise TweakValidationError(
                f"somente arquivos regulares são permitidos: {relative}"
            )
        if metadata.st_size > MAX_FILE_BYTES:
            raise TweakValidationError(
                f"arquivo possui {metadata.st_size} bytes; "
                f"limite do tweak é {MAX_FILE_BYTES}: {relative}"
            )
        digest.update(f"{stat.S_IMODE(metadata.st_mode):04o}".encode("ascii"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(candidate.read_bytes()).digest())
        digest.update(b"\0")
    return digest.hexdigest()


def _load_focal_receipt(root: Path) -> dict[str, object]:
    receipt_text = _read_bounded(root, FOCAL_RECEIPT_PATH, 16_000)
    try:
        receipt = json.loads(receipt_text)
    except json.JSONDecodeError as exc:
        raise TweakValidationError("recibo da validação focal inválido") from exc
    expected_keys = {
        "schema_version",
        "kind",
        "baseline_sha256",
        "attempt_id",
        "count",
        "argv",
        "command",
        "exit_code",
        "timed_out",
        "diff_sha256",
        "consumed",
    }
    if not isinstance(receipt, dict) or set(receipt) != expected_keys:
        raise TweakValidationError("recibo da validação focal incompleto")
    argv = receipt.get("argv")
    if (
        receipt.get("schema_version") != 1
        or receipt.get("kind") != "ft.tweak.focal"
        or not isinstance(receipt.get("baseline_sha256"), str)
        or not isinstance(receipt.get("attempt_id"), str)
        or not re.fullmatch(r"[0-9a-f]{32}", str(receipt.get("attempt_id")))
        or not isinstance(receipt.get("count"), int)
        or isinstance(receipt.get("count"), bool)
        or int(receipt.get("count")) < 0
        or not isinstance(argv, list)
        or not argv
        or not all(
            isinstance(argument, str) and "\0" not in argument for argument in argv
        )
        or not isinstance(receipt.get("command"), str)
        or receipt.get("command") != shlex.join(argv)
        or not isinstance(receipt.get("exit_code"), int)
        or isinstance(receipt.get("exit_code"), bool)
        or not isinstance(receipt.get("timed_out"), bool)
        or not isinstance(receipt.get("diff_sha256"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", str(receipt.get("diff_sha256")))
        or not isinstance(receipt.get("consumed"), bool)
    ):
        raise TweakValidationError("recibo da validação focal possui valores inválidos")
    return receipt


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=0.25)
    except subprocess.TimeoutExpired:
        pass
    # Always target the group with SIGKILL after the grace period. The shell
    # may already be reaped while a descendant that ignored SIGTERM remains.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        pass


def _enable_child_subreaper() -> None:
    """Adopt double-forked/setsid descendants so they cannot escape cleanup."""
    if not sys.platform.startswith("linux"):
        raise TweakValidationError(
            "tweak exige Linux para isolar processos dos checks/builds"
        )
    libc = ctypes.CDLL(None, use_errno=True)
    prctl = getattr(libc, "prctl", None)
    if prctl is None:
        raise TweakValidationError("kernel não oferece PR_SET_CHILD_SUBREAPER")
    prctl.argtypes = [
        ctypes.c_int,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
    ]
    prctl.restype = ctypes.c_int
    if prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        error_number = ctypes.get_errno()
        raise TweakValidationError(
            f"não foi possível ativar subreaper: errno {error_number}"
        )


def _reap_adopted_children() -> None:
    while True:
        try:
            pid, _status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid == 0:
            return


def _direct_child_pids(parent_pid: int) -> list[int]:
    children_path = Path(f"/proc/{parent_pid}/task/{parent_pid}/children")
    try:
        return [int(value) for value in children_path.read_text().split()]
    except (FileNotFoundError, PermissionError, ProcessLookupError, ValueError):
        return []


def _all_descendant_pids() -> set[int]:
    descendants: set[int] = set()
    pending = _direct_child_pids(os.getpid())
    while pending:
        pid = pending.pop()
        if pid in descendants:
            continue
        descendants.add(pid)
        pending.extend(_direct_child_pids(pid))
    return descendants


def _signal_descendants(process_signal: signal.Signals) -> set[int]:
    _reap_adopted_children()
    descendants = _all_descendant_pids()
    for pid in sorted(descendants, reverse=True):
        try:
            os.kill(pid, process_signal)
        except ProcessLookupError:
            pass
    return descendants


def _terminate_all_descendants() -> None:
    """Terminate and reap descendants, including daemonized new sessions."""
    deadline = time.monotonic() + 0.25
    while time.monotonic() < deadline:
        if not _signal_descendants(signal.SIGTERM):
            return
        time.sleep(0.01)
    for _ in range(100):
        if not _signal_descendants(signal.SIGKILL):
            return
        time.sleep(0.01)
    remaining = sorted(_all_descendant_pids())
    if remaining:
        raise TweakValidationError(
            "não foi possível encerrar descendentes do comando: "
            + ", ".join(str(pid) for pid in remaining[:8])
        )


def _load_attempt(root: Path) -> dict[str, object]:
    attempt_text = _read_bounded(root, ATTEMPT_PATH, 4_000)
    try:
        attempt = json.loads(attempt_text)
    except json.JSONDecodeError as exc:
        raise TweakValidationError("tentativa do tweak inválida") from exc
    expected_keys = {"schema_version", "kind", "baseline_sha256", "attempt_id"}
    if not isinstance(attempt, dict) or set(attempt) != expected_keys:
        raise TweakValidationError("tentativa do tweak incompleta")
    if (
        attempt.get("schema_version") != 1
        or attempt.get("kind") != "ft.tweak.attempt"
        or not isinstance(attempt.get("baseline_sha256"), str)
        or not isinstance(attempt.get("attempt_id"), str)
        or not re.fullmatch(r"[0-9a-f]{32}", str(attempt.get("attempt_id")))
    ):
        raise TweakValidationError("tentativa do tweak possui valores inválidos")
    return attempt


def _command_begin(root: Path) -> None:
    guard = _load_guard(root)
    focal_path = _safe_path(root, FOCAL_RECEIPT_PATH, allow_missing=True)
    focal_path.unlink(missing_ok=True)
    attempt = {
        "schema_version": 1,
        "kind": "ft.tweak.attempt",
        "baseline_sha256": guard["baseline_sha256"],
        "attempt_id": os.urandom(16).hex(),
    }
    _atomic_write(
        _safe_path(root, ATTEMPT_PATH, allow_missing=True),
        json.dumps(attempt, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
    )
    print(f"tweak attempt READY: {attempt['attempt_id']}")


def _command_focal(root: Path, argv: list[str]) -> None:
    guard = _load_guard(root)
    attempt = _load_attempt(root)
    if attempt["baseline_sha256"] != guard["baseline_sha256"]:
        raise TweakValidationError("tentativa focal pertence a outra baseline")
    if not argv or any("\0" in argument for argument in argv):
        raise TweakValidationError("informe um comando focal após `focal --`")

    count = 0
    focal_path = _safe_path(root, FOCAL_RECEIPT_PATH, allow_missing=True)
    if focal_path.exists():
        previous = _load_focal_receipt(root)
        if previous["baseline_sha256"] != guard["baseline_sha256"]:
            raise TweakValidationError("recibo focal pertence a outra baseline")
        if previous["attempt_id"] != attempt["attempt_id"]:
            raise TweakValidationError("recibo focal pertence a outra tentativa")
        count = int(previous["count"])

    product_root = root / str(guard["product_root"])
    _enable_child_subreaper()
    timed_out = False
    try:
        process = subprocess.Popen(argv, cwd=product_root, start_new_session=True)
    except OSError as exc:
        raise TweakValidationError(f"não foi possível iniciar comando focal: {exc}") from exc
    try:
        exit_code = process.wait(timeout=FOCAL_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        timed_out = True
        exit_code = 124
    # A focal check may not leave daemons or delayed writers behind. Clean the
    # complete process group after every outcome, before fingerprinting.
    _terminate_process_group(process)
    _terminate_all_descendants()
    if exit_code == 0 and not timed_out:
        count += 1

    receipt = {
        "schema_version": 1,
        "kind": "ft.tweak.focal",
        "baseline_sha256": guard["baseline_sha256"],
        "attempt_id": attempt["attempt_id"],
        "count": count,
        "argv": argv,
        "command": shlex.join(argv),
        "exit_code": exit_code,
        "timed_out": timed_out,
        "diff_sha256": _working_tree_fingerprint(root, str(guard["base_commit"])),
        "consumed": False,
    }
    _atomic_write(
        focal_path,
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
    )
    if timed_out:
        raise TweakValidationError(
            f"comando focal excedeu {FOCAL_TIMEOUT_SECONDS}s e sua árvore foi encerrada"
        )
    if exit_code != 0:
        raise TweakValidationError(f"comando focal saiu com código {exit_code}")
    print(f"tweak focal PASS: {receipt['command']}")


def _command_quick(root: Path) -> None:
    guard = _load_guard(root)
    environment = os.environ.copy()
    for inherited_flag in ("MAKEFLAGS", "MFLAGS", "GNUMAKEFLAGS"):
        environment.pop(inherited_flag, None)
    product_root = root / str(guard["product_root"])
    _enable_child_subreaper()
    try:
        process = subprocess.Popen(
            ["make", "-C", str(product_root), "build"],
            cwd=root,
            env=environment,
            start_new_session=True,
        )
    except OSError as exc:
        raise TweakValidationError(f"não foi possível iniciar build rápido: {exc}") from exc
    timed_out = False
    try:
        exit_code = process.wait(timeout=QUICK_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        timed_out = True
        exit_code = 124
    # Make recipes must not leave delayed writers/daemons beyond the scope
    # check that runs immediately after this build.
    _terminate_process_group(process)
    _terminate_all_descendants()
    if timed_out:
        raise TweakValidationError(
            f"build rápido excedeu {QUICK_TIMEOUT_SECONDS}s e sua árvore foi encerrada"
        )
    if exit_code != 0:
        raise TweakValidationError(f"build rápido saiu com código {exit_code}")
    print("tweak quick build PASS")


def _is_cycle_path(root: Path, relative: str) -> bool:
    try:
        path = Path(relative)
    except (OSError, ValueError):
        return False
    if path in CYCLE_PATHS:
        return True
    # StepRunner's activity log is a runtime artifact consumed by `ft runs`,
    # close/archive and duration reports. Ignore only its exact root-level
    # filename; arbitrary `*_log.md` paths remain guarded product changes.
    return len(path.parts) == 1 and path.name == f"{root.name}_log.md"


def _forbidden_product_path(relative: str) -> str | None:
    if ":" in relative or any(ord(character) < 32 for character in relative):
        return "nome de arquivo inseguro"
    path = Path(relative)
    lowered_parts = tuple(part.lower() for part in path.parts)
    if any(part in _FORBIDDEN_COMPONENTS for part in lowered_parts):
        return "área sensível"
    for part in lowered_parts:
        normalized = _normalize_text(part)
        stem = _normalize_text(Path(part).stem)
        if _SENSITIVE_PATH_TOKEN_RE.search(normalized) or _SENSITIVE_PATH_PREFIX_RE.search(
            stem
        ):
            return "área sensível"
    name = path.name.lower()
    if name in _FORBIDDEN_NAMES:
        return "dependência, build ou infraestrutura"
    if (
        name.startswith("requirements")
        or name.startswith("dockerfile")
        or name.startswith("compose.")
        or name.startswith("docker-compose")
        or name.startswith("openapi")
        or name.startswith("swagger")
        or name.startswith("schema.")
        or name == ".env"
        or name.startswith(".env.")
    ):
        return "configuração sensível"
    if path.suffix.lower() not in ALLOWED_TEXT_SUFFIXES:
        return "tipo de arquivo não textual ou não permitido"
    return None


def _base_file_size(root: Path, base_commit: str, relative: str) -> int:
    result = subprocess.run(
        ["git", "-C", str(root), "cat-file", "-s", f"{base_commit}:{relative}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=5,
        check=False,
    )
    if result.returncode != 0:
        return 0
    try:
        return int(result.stdout.strip())
    except ValueError as exc:
        raise TweakValidationError(f"tamanho Git inválido: {relative}") from exc


def _validate_patch_bytes(root: Path, base_commit: str, changed_paths: list[str]) -> int:
    patch = _git(
        root,
        "diff",
        "--no-ext-diff",
        "--no-color",
        "--unified=0",
        base_commit,
        "--",
        *changed_paths,
    )
    patch_bytes = len(patch)
    for relative in changed_paths:
        tracked = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--error-unmatch", "--", relative],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        ).returncode == 0
        if not tracked:
            candidate = _safe_path(root, Path(relative))
            # Account for the full payload plus the small diff headers Git
            # would emit for a newly added file. `git diff` omits untracked
            # files, so a line-only guard is not sufficient here.
            patch_bytes += candidate.stat().st_size + (2 * len(os.fsencode(relative))) + 128
    if patch_bytes > MAX_PATCH_BYTES:
        raise TweakValidationError(
            f"patch possui {patch_bytes} bytes; limite do tweak é {MAX_PATCH_BYTES}"
        )
    return patch_bytes


def _line_delta(root: Path, base_commit: str, relative: str) -> int:
    tracked = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--error-unmatch", "--", relative],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=5,
    ).returncode == 0
    if tracked:
        raw = _git(root, "diff", "--numstat", base_commit, "--", relative)
        line = raw.decode("utf-8", errors="replace").strip().splitlines()
        if not line:
            return 0
        columns = line[0].split("\t", 2)
        if len(columns) < 2 or columns[0] == "-" or columns[1] == "-":
            raise TweakValidationError(f"arquivo binário não permitido: {relative}")
        try:
            return int(columns[0]) + int(columns[1])
        except ValueError as exc:
            raise TweakValidationError(f"numstat inválido: {relative}") from exc
    candidate = _safe_path(root, Path(relative))
    if candidate.stat().st_size > MAX_FILE_BYTES:
        raise TweakValidationError(
            f"arquivo possui {candidate.stat().st_size} bytes; "
            f"limite do tweak é {MAX_FILE_BYTES}: {relative}"
        )
    data = candidate.read_bytes()
    if b"\0" in data:
        raise TweakValidationError(f"arquivo binário não permitido: {relative}")
    return len(data.decode("utf-8", errors="replace").splitlines())


def _validate_report(
    root: Path,
    guard: dict[str, object],
    changed_paths: list[str],
) -> dict[str, object]:
    report = _read_bounded(root, REPORT_PATH, MAX_REPORT_CHARS)
    if re.search(r"(?mi)^\s*Resultado:\s*ESCALATE\s*$", report):
        raise TweakValidationError(
            "implementação declarou ESCALATE; aborte e use --template feature"
        )
    if not re.search(r"(?mi)^\s*Resultado:\s*IMPLEMENTED\s*$", report):
        raise TweakValidationError(
            "tweak-report deve conter a linha exata Resultado: IMPLEMENTED"
        )
    if not re.search(r"(?mi)^\s*Validação focal:\s*PASS\s*$", report):
        raise TweakValidationError("tweak-report deve registrar Validação focal: PASS")
    command_lines = re.findall(r"(?mi)^\s*Comando focal:\s*(.+?)\s*$", report)
    if len(command_lines) != 1 or not command_lines[0].strip():
        raise TweakValidationError(
            "tweak-report deve registrar exatamente um Comando focal"
        )
    if not re.search(r"(?mi)^\s*Risco residual:\s*\S.+$", report):
        raise TweakValidationError("tweak-report deve registrar Risco residual")
    missing = [relative for relative in changed_paths if relative not in report]
    if missing:
        raise TweakValidationError(
            "tweak-report não lista todos os arquivos alterados: " + ", ".join(missing)
        )
    receipt = _load_focal_receipt(root)
    attempt = _load_attempt(root)
    if attempt["baseline_sha256"] != guard["baseline_sha256"]:
        raise TweakValidationError("tentativa focal pertence a outra baseline")
    if receipt["baseline_sha256"] != guard["baseline_sha256"]:
        raise TweakValidationError("recibo focal pertence a outra baseline")
    if receipt["attempt_id"] != attempt["attempt_id"]:
        raise TweakValidationError("recibo focal pertence a outra tentativa")
    if receipt["count"] != 1:
        raise TweakValidationError(
            "execute exatamente um comando focal por tentativa de implementação"
        )
    if receipt["timed_out"] or receipt["exit_code"] != 0:
        raise TweakValidationError("o comando focal registrado não passou")
    try:
        reported_argv = shlex.split(command_lines[0].strip())
    except ValueError as exc:
        raise TweakValidationError("Comando focal do relatório possui aspas inválidas") from exc
    if reported_argv != receipt["argv"]:
        raise TweakValidationError(
            "Comando focal do relatório não corresponde ao comando realmente executado"
        )
    fingerprint = _working_tree_fingerprint(root, str(guard["base_commit"]))
    if receipt["diff_sha256"] != fingerprint:
        raise TweakValidationError(
            "produto mudou depois da validação focal; execute-a novamente"
        )
    return receipt


def _command_implementation(root: Path) -> None:
    guard = _load_guard(root)
    # Surface an intentional escalation before generic "no product diff"
    # errors so the operator gets the correct next command immediately.
    report_probe = _read_bounded(root, REPORT_PATH, MAX_REPORT_CHARS)
    if re.search(r"(?mi)^\s*Resultado:\s*ESCALATE\s*$", report_probe):
        raise TweakValidationError(
            "implementação declarou ESCALATE; aborte e use --template feature"
        )
    base_commit = str(guard["base_commit"])
    _git(root, "merge-base", "--is-ancestor", base_commit, "HEAD")
    _reject_hidden_index_entries(root)
    entries = _changed_entries(root, base_commit)

    allowed_roots = {str(guard["product_root"]), "test", "tests"}
    changed: dict[str, str] = {}
    unexpected: list[str] = []
    for status_code, relative in entries:
        if _is_cycle_path(root, relative):
            continue
        path = Path(relative)
        if (
            path.is_absolute()
            or not path.parts
            or ".." in path.parts
            or path.parts[0] not in allowed_roots
        ):
            unexpected.append(relative)
            continue
        if status_code not in {"A", "M"}:
            raise TweakValidationError(
                f"tweak não permite delete/rename/copy ({status_code}): {relative}"
            )
        changed[relative] = status_code

    if unexpected:
        raise TweakValidationError(
            "tweak alterou paths fora do produto/testes: " + ", ".join(sorted(unexpected))
        )
    changed_paths = sorted(changed)
    if not changed_paths:
        raise TweakValidationError("nenhum arquivo de produto/teste foi alterado")
    if len(changed_paths) > MAX_FILES:
        raise TweakValidationError(
            f"diff possui {len(changed_paths)} arquivos; limite do tweak é {MAX_FILES}"
        )

    total_lines = 0
    for relative in changed_paths:
        reason = _forbidden_product_path(relative)
        if reason:
            raise TweakValidationError(f"path proibido ({reason}): {relative}")
        candidate = _safe_path(root, Path(relative))
        metadata = candidate.lstat()
        if not stat.S_ISREG(metadata.st_mode):
            raise TweakValidationError(f"somente arquivos regulares são permitidos: {relative}")
        if metadata.st_size > MAX_FILE_BYTES:
            raise TweakValidationError(
                f"arquivo possui {metadata.st_size} bytes; "
                f"limite do tweak é {MAX_FILE_BYTES}: {relative}"
            )
        base_size = _base_file_size(root, base_commit, relative)
        if base_size > MAX_FILE_BYTES:
            raise TweakValidationError(
                f"versão base possui {base_size} bytes; "
                f"limite do tweak é {MAX_FILE_BYTES}: {relative}"
            )
        total_lines += _line_delta(root, base_commit, relative)
    if total_lines > MAX_CHANGED_LINES:
        raise TweakValidationError(
            f"diff possui {total_lines} linhas adicionadas+removidas; "
            f"limite do tweak é {MAX_CHANGED_LINES}"
        )

    patch_bytes = _validate_patch_bytes(root, base_commit, changed_paths)

    check = subprocess.run(
        ["git", "-C", str(root), "diff", "--check", base_commit, "--", *changed_paths],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=10,
        check=False,
    )
    if check.returncode != 0:
        raise TweakValidationError(
            "git diff --check falhou: " + check.stdout.strip()[:500]
        )
    receipt = _validate_report(root, guard, changed_paths)
    if not receipt["consumed"]:
        receipt["consumed"] = True
        focal_path = _safe_path(root, FOCAL_RECEIPT_PATH)
        _atomic_write(
            focal_path,
            json.dumps(
                receipt,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
        )
    print(
        "tweak implementation PASS: "
        f"{len(changed_paths)} file(s), {total_lines} changed line(s), "
        f"{patch_bytes} patch byte(s)"
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("preflight", "begin", "implementation", "focal", "quick"),
    )
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        root = _find_root(Path.cwd())
        if arguments.command == "preflight":
            if arguments.arguments:
                raise TweakValidationError("preflight não aceita argumentos extras")
            _command_preflight(root)
        elif arguments.command == "begin":
            if arguments.arguments:
                raise TweakValidationError("begin não aceita argumentos extras")
            _command_begin(root)
        elif arguments.command == "implementation":
            if arguments.arguments:
                raise TweakValidationError("implementation não aceita argumentos extras")
            _command_implementation(root)
        elif arguments.command == "quick":
            if arguments.arguments:
                raise TweakValidationError("quick não aceita argumentos extras")
            _command_quick(root)
        else:
            focal_arguments = list(arguments.arguments)
            if focal_arguments[:1] == ["--"]:
                focal_arguments.pop(0)
            _command_focal(root, focal_arguments)
    except (OSError, subprocess.TimeoutExpired, TweakValidationError, yaml.YAMLError) as exc:
        print(f"tweak validation FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
