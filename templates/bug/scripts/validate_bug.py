#!/usr/bin/env python3
"""Deterministic RED→GREEN and governance validators for ``template bug``."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import unicodedata
from typing import Any

import yaml


SCHEMA_VERSION = 1
BASELINE_PATH = Path("docs/bug-baseline.yml")
REPORT_PATH = Path("docs/bug-report.md")
VALIDATION_PATH = Path("docs/bug-validation.json")
RESULT_PATH = Path("docs/bug-result.md")
RED_PATH = Path("state/bug-red.json")
GREEN_PATH = Path("state/bug-green.json")
PB_RE = re.compile(r"\bPB-\d+[A-Z]?\b", re.IGNORECASE)
FEAT_RE = re.compile(r"\bFEAT-\d{3}\b", re.IGNORECASE)
ALLOWED_TEST_EXECUTABLES = {
    "bundle",
    "bun",
    "deno",
    "node",
    "npm",
    "npx",
    "php",
    "phpunit",
    "pnpm",
    "python",
    "python3",
    "pytest",
    "ruby",
    "yarn",
}
FORBIDDEN_PATH_PARTS = {
    ".github",
    ".gitlab",
    "auth",
    "authentication",
    "authorization",
    "ci",
    "infra",
    "infrastructure",
    "migration",
    "migrations",
    "security",
    "terraform",
}
FORBIDDEN_FILENAMES = {
    "cargo.lock",
    "composer.lock",
    "dockerfile",
    "gemfile.lock",
    "package-lock.json",
    "package.json",
    "pnpm-lock.yaml",
    "poetry.lock",
    "pyproject.toml",
    "requirements.txt",
    "uv.lock",
    "yarn.lock",
}
MAX_FILES = 8
MAX_CHANGED_LINES = 500
MAX_CAPTURE_CHARS = 30_000
REGRESSION_TIMEOUT_SECONDS = 90
FULL_COMMAND_TIMEOUT_SECONDS = 180
_INLINE_EVAL_FLAGS = frozenset({"-c", "-e", "--eval", "--evaluate"})
_ASSERTION_FAILURE_RE = re.compile(
    r"(?i)(?:assert(?:ion|ionerror|ionfailederror)?|failed asserting|"
    r"expect(?:ed|\()|\breceived\b|\bactual\b|---\s+FAIL:|"
    r"\bFAILURE\b|\bFailure:\b|assert_eq|assert_ne|panicked at)"
)
_INFRASTRUCTURE_FAILURE_RE = re.compile(
    r"(?i)(?:no module named|module not found|cannot find module|"
    r"command not found|permission denied|syntaxerror|syntax error|"
    r"importerror|error collecting|failed to collect|collection error|"
    r"unknown option|unrecognized option|invalid option|internal error|"
    r"segmentation fault|could not compile|compilation failed)"
)


class BugValidationError(ValueError):
    """A deterministic, user-facing bug process violation."""


def _find_root(explicit: Path | None = None) -> Path:
    if explicit is not None:
        root = explicit.resolve()
        if not (root / ".ft/manifest.yml").is_file():
            raise BugValidationError(f"raiz FT inválida: {root}")
        return root
    current = Path(__file__).resolve().parent
    for candidate in (current, *current.parents):
        if (candidate / ".ft/manifest.yml").is_file():
            return candidate
    raise BugValidationError("raiz do projeto FT não encontrada")


def _read(root: Path, relative: str | Path) -> str:
    path = root / relative
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError) as exc:
        raise BugValidationError(f"arquivo obrigatório escapa da raiz: {relative}") from exc
    if path.is_symlink():
        raise BugValidationError(f"arquivo obrigatório não pode ser symlink: {relative}")
    if not path.is_file():
        raise BugValidationError(f"arquivo obrigatório ausente: {relative}")
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        raise BugValidationError(f"arquivo obrigatório vazio: {relative}")
    return text


def _write_json(root: Path, relative: Path, payload: dict[str, Any]) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _load_json(root: Path, relative: Path) -> dict[str, Any]:
    try:
        payload = json.loads((root / relative).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BugValidationError(f"receipt ausente: {relative}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise BugValidationError(f"receipt inválido em {relative}: {exc}") from exc
    if not isinstance(payload, dict):
        raise BugValidationError(f"receipt deve ser objeto JSON: {relative}")
    return payload


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _markdown_records(text: str) -> list[dict[str, str]]:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not (stripped.startswith("|") and stripped.endswith("|")):
            continue
        headers = [_normalize(cell) for cell in stripped.strip("|").split("|")]
        if "id" not in headers or index + 1 >= len(lines):
            continue
        separator = lines[index + 1].strip()
        if not (separator.startswith("|") and "---" in separator):
            continue
        records: list[dict[str, str]] = []
        for row_line in lines[index + 2 :]:
            row = row_line.strip()
            if not (row.startswith("|") and row.endswith("|")):
                break
            cells = [cell.strip() for cell in row.strip("|").split("|")]
            if len(cells) == len(headers):
                records.append(dict(zip(headers, cells)))
        return records
    return []


def _row_value(row: dict[str, str], *names: str) -> str:
    for name in names:
        value = row.get(_normalize(name), "")
        if value:
            return value
    return ""


def _records_by_id(records: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {
        _row_value(row, "id").upper(): row
        for row in records
        if _row_value(row, "id")
    }


def _detect_product_root(root: Path) -> str:
    candidates = [
        relative
        for relative in ("project", "src")
        if (root / relative / "Makefile").is_file()
        and not (root / relative).is_symlink()
        and not (root / relative / "Makefile").is_symlink()
    ]
    if len(candidates) != 1:
        detail = ", ".join(candidates) if candidates else "nenhum"
        raise BugValidationError(
            "esperado exatamente um produto com Makefile em project/ ou src/; "
            f"encontrado: {detail}"
        )
    return candidates[0]


def _git(root: Path, *args: str, timeout: int = 30) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BugValidationError(f"falha ao executar git {' '.join(args)}: {exc}") from exc


def _head(root: Path) -> str:
    result = _git(root, "rev-parse", "HEAD")
    value = result.stdout.decode(errors="replace").strip()
    if result.returncode != 0 or not re.fullmatch(r"[0-9a-fA-F]{7,64}", value):
        raise BugValidationError("bug exige repositório Git com commit inicial")
    return value


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_path(path: Path) -> str | None:
    return _sha256_bytes(path.read_bytes()) if path.is_file() else None


def _frontmatter(text: str) -> dict[str, object]:
    if not text.lstrip().startswith("---"):
        raise BugValidationError("docs/bug-report.md: frontmatter YAML ausente")
    parts = text.lstrip().split("---", 2)
    if len(parts) < 3:
        raise BugValidationError("docs/bug-report.md: frontmatter não foi fechado")
    try:
        payload = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError as exc:
        raise BugValidationError(f"frontmatter do bug inválido: {exc}") from exc
    if not isinstance(payload, dict):
        raise BugValidationError("frontmatter do bug deve ser mapping")
    return payload


def _section(text: str, name: str) -> str:
    match = re.search(
        rf"(?ims)^##\s+{re.escape(name)}\s*$\n(.*?)(?=^##\s+|\Z)", text
    )
    return match.group(1).strip() if match else ""


def _bug_entries(text: str, tag: str) -> list[str]:
    pattern = re.compile(
        rf"(?mi)^[ \t]*(?:[-*+][ \t]+)?#{re.escape(tag)}(?=[ \t]|$)[^\r\n]*$"
    )
    return [match.group(0).strip() for match in pattern.finditer(text)]


def _write_baseline(root: Path) -> None:
    target = root / BASELINE_PATH
    if target.exists():
        return
    product_root = _detect_product_root(root)
    request = _read(root, "docs/feature-request.md")
    backlog = _markdown_records(_read(root, "docs/PROJECT_BACKLOG.md"))
    features = _markdown_records(_read(root, "docs/FEATURES.md"))
    changelog = (
        (root / "CHANGELOG.md").read_text(encoding="utf-8", errors="replace")
        if (root / "CHANGELOG.md").is_file()
        else ""
    )
    payload = {
        "schema_version": SCHEMA_VERSION,
        "kind": "ft.bug.baseline",
        "base_commit": _head(root),
        "product_root": product_root,
        "request_sha256": _sha256_bytes(request.encode("utf-8")),
        "project_backlog": backlog,
        "features": features,
        "bug_changelog_entries": _bug_entries(changelog, "BUG"),
        "feature_changelog_entries": _bug_entries(changelog, "FEAT"),
        "documentation_sha256": {
            "CHANGELOG.md": _sha256_path(root / "CHANGELOG.md"),
            "docs/PROJECT_BACKLOG.md": _sha256_path(root / "docs/PROJECT_BACKLOG.md"),
            "docs/FEATURES.md": _sha256_path(root / "docs/FEATURES.md"),
        },
        "documentation_text": {
            "CHANGELOG.md": changelog,
            "docs/PROJECT_BACKLOG.md": _read(root, "docs/PROJECT_BACKLOG.md"),
            "docs/FEATURES.md": _read(root, "docs/FEATURES.md"),
        },
        "limits": {"max_files": MAX_FILES, "max_changed_lines": MAX_CHANGED_LINES},
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _baseline(root: Path) -> dict[str, Any]:
    try:
        payload = yaml.safe_load(_read(root, BASELINE_PATH)) or {}
    except yaml.YAMLError as exc:
        raise BugValidationError(f"baseline YAML inválida: {exc}") from exc
    if not isinstance(payload, dict):
        raise BugValidationError("baseline deve ser mapping")
    if payload.get("schema_version") != SCHEMA_VERSION or payload.get("kind") != "ft.bug.baseline":
        raise BugValidationError("baseline do bug possui schema/kind inválido")
    if payload.get("product_root") not in {"project", "src"}:
        raise BugValidationError("baseline possui product_root inválido")
    return payload


def _changed_paths(root: Path, base_commit: str) -> list[str]:
    tracked = _git(root, "diff", "--name-only", "-z", base_commit, "--")
    untracked = _git(root, "ls-files", "--others", "--exclude-standard", "-z")
    if tracked.returncode != 0 or untracked.returncode != 0:
        raise BugValidationError("não foi possível enumerar o diff do bug")
    values: set[str] = set()
    for content in (tracked.stdout, untracked.stdout):
        values.update(
            raw.decode("utf-8", errors="replace")
            for raw in content.split(b"\0")
            if raw
        )
    return sorted(values)


def _changed_product_paths(root: Path, baseline: dict[str, Any]) -> list[str]:
    prefix = str(baseline["product_root"]).rstrip("/") + "/"
    return [
        path
        for path in _changed_paths(root, str(baseline["base_commit"]))
        if path.startswith(prefix)
    ]


def _is_test_path(relative: str) -> bool:
    path = Path(relative)
    lowered_parts = {part.lower() for part in path.parts}
    name = path.name.lower()
    return bool(
        {"test", "tests", "__tests__"}.intersection(lowered_parts)
        or name.startswith("test_")
        or name.endswith("_test.go")
        or "_test." in name
        or ".test." in name
        or ".spec." in name
    )


def _test_hashes(root: Path, paths: list[str]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for relative in paths:
        path = root / relative
        try:
            path.resolve().relative_to(root.resolve())
        except (OSError, ValueError) as exc:
            raise BugValidationError(f"teste escapa da raiz: {relative}") from exc
        if path.is_symlink():
            raise BugValidationError(f"teste não pode ser symlink: {relative}")
        if not path.is_file():
            raise BugValidationError(f"teste alterado ausente: {relative}")
        hashes[relative] = _sha256_bytes(path.read_bytes())
    return hashes


def _assert_test_hashes(root: Path, expected: dict[str, object]) -> None:
    if not expected:
        raise BugValidationError("receipt RED não registrou testes")
    current = _test_hashes(root, sorted(str(path) for path in expected))
    if current != expected:
        raise BugValidationError(
            "teste de regressão mudou depois do RED; aborte e reinicie o bug"
        )


def _command_argv(raw: list[str]) -> list[str]:
    argv = list(raw)
    if argv and argv[0] == "--":
        argv = argv[1:]
    if not argv:
        raise BugValidationError("comando de regressão ausente")
    executable = Path(argv[0]).name.lower()
    if executable not in ALLOWED_TEST_EXECUTABLES:
        raise BugValidationError(
            "comando RED/GREEN deve usar runner focal direto: "
            + ", ".join(sorted(ALLOWED_TEST_EXECUTABLES))
        )
    if executable in {"node", "python", "python3", "ruby"} and any(
        argument in _INLINE_EVAL_FLAGS for argument in argv[1:]
    ):
        raise BugValidationError(
            "comando RED/GREEN deve apontar para um teste, sem código inline"
        )
    return argv


def _command_mentions_test(argv: list[str], test_paths: list[str], product_root: str) -> bool:
    tokens = {token.replace("\\", "/").lstrip("./") for token in argv[1:]}
    for relative in test_paths:
        product_relative = relative.removeprefix(product_root.rstrip("/") + "/")
        parent = Path(product_relative).parent.as_posix()
        candidates = {relative, product_relative, Path(relative).name, parent}
        if any(
            token == candidate or token.endswith("/" + candidate)
            for token in tokens
            for candidate in candidates
        ):
            return True
    return False


def _run_regression(root: Path, product_root: str, argv: list[str]) -> tuple[int, str]:
    try:
        result = subprocess.run(
            argv,
            cwd=root / product_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=REGRESSION_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as exc:
        raise BugValidationError(f"runner de regressão ausente: {argv[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise BugValidationError("comando de regressão excedeu 90 segundos") from exc
    return result.returncode, result.stdout[-MAX_CAPTURE_CHARS:]


def _assert_red_failure_output(output: str) -> None:
    """Reject exit-1 receipts that look like setup/collection failures."""
    if not output.strip():
        raise BugValidationError("RED falhou sem saída que demonstre o defeito")
    infrastructure = _INFRASTRUCTURE_FAILURE_RE.search(output)
    if infrastructure is not None:
        raise BugValidationError(
            "RED falhou por infraestrutura/coleta, não por regressão: "
            + infrastructure.group(0)
        )
    if _ASSERTION_FAILURE_RE.search(output) is None:
        raise BugValidationError(
            "RED exit 1 não contém uma falha de asserção reconhecível"
        )


def _validation_paths(root: Path) -> list[str]:
    result = _git(root, "ls-files", "-c", "-o", "--exclude-standard", "-z", "--")
    if result.returncode != 0:
        raise BugValidationError("git ls-files falhou ao gerar fingerprint")
    selected: list[str] = []
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        relative = raw.decode("utf-8", errors="replace")
        path = Path(relative)
        if not path.parts:
            continue
        if path.parts[0] in {"docs", "state"} or relative == "CHANGELOG.md":
            continue
        if len(path.parts) == 1 and (
            path.suffix == ".log"
            or path.name.startswith("cycle-")
            or path.name in {".serve.pid", ".serve_url", ".serve.log"}
        ):
            continue
        if path.parts[0] == ".ft" and not relative.startswith(".ft/process/bug/"):
            continue
        selected.append(relative)
    return sorted(set(selected))


def _fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    for relative in _validation_paths(root):
        path = root / relative
        digest.update(relative.encode("utf-8") + b"\0")
        if path.is_symlink():
            raise BugValidationError(
                f"link simbólico não permitido no escopo verificável: {relative}"
            )
        elif path.is_file():
            executable = path.stat().st_mode & 0o111
            digest.update(f"file:{executable:o}\0".encode("ascii") + path.read_bytes())
        else:
            digest.update(b"missing\0")
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _changed_line_count(root: Path, baseline: dict[str, Any], paths: list[str]) -> int:
    if not paths:
        return 0
    result = _git(
        root,
        "diff",
        "--numstat",
        str(baseline["base_commit"]),
        "--",
        *paths,
    )
    if result.returncode != 0:
        raise BugValidationError("git diff --numstat falhou")
    total = 0
    tracked_paths: set[str] = set()
    for line in result.stdout.decode(errors="replace").splitlines():
        added, removed, relative = line.split("\t", 2)
        tracked_paths.add(relative)
        if added.isdigit() and removed.isdigit():
            total += int(added) + int(removed)
        else:
            raise BugValidationError(f"arquivo binário não permitido: {relative}")
    for relative in set(paths) - tracked_paths:
        path = root / relative
        data = path.read_bytes()
        if b"\0" in data:
            raise BugValidationError(f"arquivo binário não permitido: {relative}")
        total += len(data.decode("utf-8", errors="replace").splitlines())
    return total


def _report_contract(root: Path) -> tuple[dict[str, str], str]:
    report = _read(root, REPORT_PATH)
    if re.search(r"(?mi)^\s*Resultado:\s*ESCALATE\s*$", report):
        raise BugValidationError(
            "bug declarou ESCALATE; aborte e use ft feature --template feature"
        )
    if len(re.findall(r"(?mi)^\s*Resultado:\s*FIXED\s*$", report)) != 1:
        raise BugValidationError("bug-report exige exatamente `Resultado: FIXED`")
    metadata_raw = _frontmatter(report)
    metadata = {
        "backlog_item": str(metadata_raw.get("backlog_item") or "").upper(),
        "target_feature": str(metadata_raw.get("target_feature") or "").upper(),
        "severity": str(metadata_raw.get("severity") or "").lower(),
    }
    if not PB_RE.fullmatch(metadata["backlog_item"]):
        raise BugValidationError("bug-report exige backlog_item PB-NNN")
    if not FEAT_RE.fullmatch(metadata["target_feature"]):
        raise BugValidationError("bug-report exige target_feature FEAT-NNN existente")
    if metadata["severity"] not in {"low", "medium", "high", "critical"}:
        raise BugValidationError("severity deve ser low, medium, high ou critical")
    for section in (
        "Sintoma",
        "Comportamento esperado",
        "Causa raiz",
        "Regressão",
        "Correção",
        "Risco",
    ):
        if not _section(report, section):
            raise BugValidationError(f"bug-report sem seção preenchida: {section}")
    return metadata, report


def _validate_red_receipt(root: Path) -> dict[str, Any]:
    baseline = _baseline(root)
    red = _load_json(root, RED_PATH)
    output = red.get("output")
    argv = red.get("argv")
    if (
        red.get("schema_version") != SCHEMA_VERSION
        or red.get("kind") != "ft.bug.red"
        or red.get("exit_code") != 1
        or red.get("base_commit") != baseline.get("base_commit")
        or red.get("request_sha256") != baseline.get("request_sha256")
        or not isinstance(argv, list)
        or not argv
        or not all(isinstance(item, str) for item in argv)
        or not isinstance(output, str)
        or red.get("output_sha256") != _sha256_bytes(output.encode("utf-8"))
    ):
        raise BugValidationError("receipt RED possui schema, vínculo ou conteúdo inválido")
    _assert_red_failure_output(output)
    _assert_test_hashes(root, dict(red.get("test_hashes") or {}))
    return red


def _validate_green_receipt(root: Path, red: dict[str, Any]) -> dict[str, Any]:
    baseline = _baseline(root)
    green = _load_json(root, GREEN_PATH)
    if (
        green.get("schema_version") != SCHEMA_VERSION
        or green.get("kind") != "ft.bug.green"
        or green.get("exit_code") != 0
        or green.get("base_commit") != baseline.get("base_commit")
        or green.get("request_sha256") != baseline.get("request_sha256")
        or green.get("argv") != red.get("argv")
        or green.get("test_hashes") != red.get("test_hashes")
        or not isinstance(green.get("output_sha256"), str)
        or not isinstance(green.get("fingerprint"), str)
    ):
        raise BugValidationError("receipt GREEN possui schema, vínculo ou conteúdo inválido")
    return green


def _allowed_implementation_path(root: Path, relative: str, product_root: str) -> bool:
    path = Path(relative)
    if not path.parts or path.is_absolute() or ".." in path.parts:
        return False
    if relative.startswith(product_root.rstrip("/") + "/"):
        return True
    if path.parts[0] == "state":
        return True
    if relative in {
        "docs/feature-request.md",
        BASELINE_PATH.as_posix(),
        REPORT_PATH.as_posix(),
        VALIDATION_PATH.as_posix(),
    }:
        return True
    if len(path.parts) == 1 and (
        path.name in {".serve.pid", ".serve_url", ".serve.log"}
        or path.name == f"{root.name}_log.md"
    ):
        return True
    return False


def _assert_implementation_scope(root: Path, baseline: dict[str, Any]) -> None:
    product_root = str(baseline["product_root"])
    unexpected = [
        relative
        for relative in _changed_paths(root, str(baseline["base_commit"]))
        if not _allowed_implementation_path(root, relative, product_root)
    ]
    if unexpected:
        raise BugValidationError(
            "bug alterou paths fora do produto selecionado: "
            + ", ".join(sorted(unexpected))
        )


def _without_markdown_rows(text: str, allowed_ids: set[str]) -> str:
    kept: list[str] = []
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        cells = [cell.strip().upper() for cell in stripped.strip("|").split("|")]
        if (
            stripped.startswith("|")
            and stripped.endswith("|")
            and cells
            and cells[0] in allowed_ids
        ):
            continue
        kept.append(line)
    return "".join(kept)


def _assert_document_structure_preserved(
    baseline: dict[str, Any],
    current_backlog_text: str,
    current_features_text: str,
    backlog_id: str,
    feature_id: str,
) -> None:
    original = dict(baseline.get("documentation_text") or {})
    original_backlog = original.get("docs/PROJECT_BACKLOG.md")
    original_features = original.get("docs/FEATURES.md")
    if not isinstance(original_backlog, str) or not isinstance(original_features, str):
        raise BugValidationError("baseline não preservou os documentos canônicos")
    if _without_markdown_rows(original_backlog, {backlog_id}) != _without_markdown_rows(
        current_backlog_text, {backlog_id}
    ):
        raise BugValidationError(
            "PROJECT_BACKLOG alterou estrutura/prosa fora do PB selecionado"
        )
    if _without_markdown_rows(original_features, {feature_id}) != _without_markdown_rows(
        current_features_text, {feature_id}
    ):
        raise BugValidationError(
            "FEATURES alterou estrutura/prosa fora da FEAT selecionada"
        )


def _assert_bug_identifiers(root: Path, metadata: dict[str, str]) -> None:
    baseline = _baseline(root)
    features = _records_by_id(list(baseline.get("features") or []))
    if metadata["target_feature"] not in features:
        raise BugValidationError(
            f"target_feature não existe na baseline: {metadata['target_feature']}"
        )
    backlog = _records_by_id(list(baseline.get("project_backlog") or []))
    request = _read(root, "docs/feature-request.md")
    reserved = re.search(
        r"(?mi)^\s*reserved_backlog_item\s*:\s*(PB-\d+[A-Z]?)\s*$", request
    )
    explicit = PB_RE.search(request)
    required = None
    if reserved:
        required = reserved.group(1).upper()
    elif explicit:
        required = explicit.group(0).upper()
    if required and metadata["backlog_item"] != required:
        raise BugValidationError(
            f"backlog_item deve preservar a reserva/demanda: {required}"
        )
    if metadata["backlog_item"] not in backlog and required is None:
        numbers = [
            int(match.group(1))
            for identifier in backlog
            if (match := re.fullmatch(r"PB-(\d+)[A-Z]?", identifier))
        ]
        expected = f"PB-{max(numbers, default=0) + 1:03d}"
        if metadata["backlog_item"] != expected:
            raise BugValidationError(
                f"novo backlog_item deve usar o próximo ID livre: {expected}"
            )


def command_baseline(root: Path) -> None:
    _write_baseline(root)
    baseline = _baseline(root)
    request = _read(root, "docs/feature-request.md")
    if _sha256_bytes(request.encode("utf-8")) != baseline.get("request_sha256"):
        raise BugValidationError("baseline existente pertence a outra demanda")
    print(f"bug baseline PASS: {BASELINE_PATH}")


def command_begin(root: Path) -> None:
    baseline = _baseline(root)
    request = _read(root, "docs/feature-request.md")
    if _sha256_bytes(request.encode("utf-8")) != baseline.get("request_sha256"):
        raise BugValidationError("demanda mudou depois do preflight")
    (root / GREEN_PATH).unlink(missing_ok=True)
    if (root / RED_PATH).is_file():
        _validate_red_receipt(root)
        print("bug attempt READY: RED preservado; produza novo GREEN")
    else:
        print("bug attempt READY: execute RED antes da correção")


def command_status(root: Path) -> None:
    red = "ready" if (root / RED_PATH).is_file() else "missing"
    green = "ready" if (root / GREEN_PATH).is_file() else "missing"
    print(f"bug RED={red} GREEN={green}")


def command_red(root: Path, raw_argv: list[str]) -> None:
    baseline = _baseline(root)
    argv = _command_argv(raw_argv)
    if (root / RED_PATH).is_file():
        red = _validate_red_receipt(root)
        if red.get("argv") != argv:
            raise BugValidationError("RED já registrado com outro comando")
        print("bug RED REUSED: teste congelado e comando idêntico")
        return
    changed = _changed_product_paths(root, baseline)
    test_paths = [path for path in changed if _is_test_path(path)]
    non_test = [path for path in changed if not _is_test_path(path)]
    if not test_paths:
        raise BugValidationError("RED exige teste novo ou alterado")
    if non_test:
        raise BugValidationError(
            "código de produto mudou antes do RED: " + ", ".join(non_test)
        )
    if not _command_mentions_test(argv, test_paths, str(baseline["product_root"])):
        raise BugValidationError("comando RED deve mencionar o teste alterado")
    before = _fingerprint(root)
    exit_code, output = _run_regression(root, str(baseline["product_root"]), argv)
    after = _fingerprint(root)
    if after != before:
        raise BugValidationError(
            "comando RED alterou arquivos versionáveis; teste focal deve ser sem efeitos"
        )
    if exit_code != 1:
        raise BugValidationError(
            f"RED deve falhar por teste com exit 1; recebeu exit {exit_code}"
        )
    _assert_red_failure_output(output)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "kind": "ft.bug.red",
        "base_commit": baseline["base_commit"],
        "request_sha256": baseline["request_sha256"],
        "argv": argv,
        "exit_code": exit_code,
        "output": output,
        "output_sha256": _sha256_bytes(output.encode("utf-8")),
        "test_hashes": _test_hashes(root, test_paths),
    }
    _write_json(root, RED_PATH, payload)
    print(f"bug RED PASS: {len(test_paths)} teste(s), exit 1")


def command_green(root: Path, raw_argv: list[str]) -> None:
    baseline = _baseline(root)
    red = _validate_red_receipt(root)
    argv = _command_argv(raw_argv)
    if red.get("argv") != argv:
        raise BugValidationError("GREEN deve usar exatamente o mesmo argv do RED")
    _assert_test_hashes(root, dict(red.get("test_hashes") or {}))
    changed = _changed_product_paths(root, baseline)
    non_test = [path for path in changed if not _is_test_path(path)]
    if not non_test:
        raise BugValidationError("GREEN exige uma correção em código de produto")
    before = _fingerprint(root)
    exit_code, output = _run_regression(root, str(baseline["product_root"]), argv)
    after = _fingerprint(root)
    if after != before:
        raise BugValidationError(
            "comando GREEN alterou arquivos versionáveis; teste focal deve ser sem efeitos"
        )
    if exit_code != 0:
        raise BugValidationError(f"GREEN falhou com exit {exit_code}\n{output[-2000:]}")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "kind": "ft.bug.green",
        "base_commit": baseline["base_commit"],
        "request_sha256": baseline["request_sha256"],
        "argv": argv,
        "exit_code": exit_code,
        "output_sha256": _sha256_bytes(output.encode("utf-8")),
        "test_hashes": red["test_hashes"],
        "fingerprint": after,
    }
    _write_json(root, GREEN_PATH, payload)
    print("bug GREEN PASS: mesmo comando/teste do RED")


def validate_implementation(root: Path) -> str:
    baseline = _baseline(root)
    _assert_implementation_scope(root, baseline)
    metadata, report = _report_contract(root)
    _assert_bug_identifiers(root, metadata)
    request = _read(root, "docs/feature-request.md")
    if _sha256_bytes(request.encode("utf-8")) != baseline.get("request_sha256"):
        raise BugValidationError("demanda mudou depois do preflight")
    documentation = dict(baseline.get("documentation_sha256") or {})
    for relative in ("CHANGELOG.md", "docs/PROJECT_BACKLOG.md", "docs/FEATURES.md"):
        if _sha256_path(root / relative) != documentation.get(relative):
            raise BugValidationError(f"{relative} só pode mudar após o aceite")

    red = _validate_red_receipt(root)
    green = _validate_green_receipt(root, red)
    command_lines = re.findall(
        r"(?mi)^\s*Comando de regressão:\s*(.+?)\s*$", report
    )
    if len(command_lines) != 1:
        raise BugValidationError("bug-report exige um Comando de regressão")
    try:
        reported_argv = shlex.split(command_lines[0])
    except ValueError as exc:
        raise BugValidationError("Comando de regressão possui aspas inválidas") from exc
    if reported_argv != red.get("argv"):
        raise BugValidationError("comando do relatório diverge do RED/GREEN")
    signature_lines = re.findall(r"(?mi)^\s*Assinatura RED:\s*(.+?)\s*$", report)
    if len(signature_lines) != 1 or len(signature_lines[0].strip()) < 4:
        raise BugValidationError("bug-report exige uma Assinatura RED objetiva")
    if signature_lines[0].strip().lower() not in str(red.get("output") or "").lower():
        raise BugValidationError("Assinatura RED não aparece na falha registrada")
    current_fingerprint = _fingerprint(root)
    if green.get("fingerprint") != current_fingerprint:
        raise BugValidationError("produto mudou depois do GREEN; execute GREEN novamente")

    changed = _changed_product_paths(root, baseline)
    if len(changed) > MAX_FILES:
        raise BugValidationError(
            f"bug alterou {len(changed)} arquivos de produto; limite {MAX_FILES}"
        )
    changed_lines = _changed_line_count(root, baseline, changed)
    if changed_lines > MAX_CHANGED_LINES:
        raise BugValidationError(
            f"bug alterou {changed_lines} linhas; limite {MAX_CHANGED_LINES}"
        )
    for relative in changed:
        path = Path(relative)
        lowered = {part.lower() for part in path.parts}
        if lowered.intersection(FORBIDDEN_PATH_PARTS) or path.name.lower() in FORBIDDEN_FILENAMES:
            raise BugValidationError(
                f"escopo sensível não permitido em template bug: {relative}"
            )
    if not any(_is_test_path(path) for path in changed):
        raise BugValidationError("bug não alterou teste de regressão")
    if not any(not _is_test_path(path) for path in changed):
        raise BugValidationError("bug não alterou código de produto")
    print(
        f"bug implementation PASS: {len(changed)} arquivo(s), "
        f"{changed_lines} linha(s)"
    )
    return current_fingerprint


def command_full(root: Path) -> None:
    before = validate_implementation(root)
    baseline = _baseline(root)
    product_root = str(baseline["product_root"])
    commands = [
        ["make", "-C", product_root, "build"],
        ["make", "-C", product_root, "test"],
    ]
    for command in commands:
        try:
            result = subprocess.run(
                ["env", "-u", "MAKEFLAGS", "-u", "MFLAGS", "-u", "GNUMAKEFLAGS", *command],
                cwd=root,
                timeout=FULL_COMMAND_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise BugValidationError(
                f"validação completa excedeu timeout: {' '.join(command)}"
            ) from exc
        if result.returncode != 0:
            raise BugValidationError(
                f"validação completa falhou ({result.returncode}): {' '.join(command)}"
            )
    after = _fingerprint(root)
    if before != after:
        raise BugValidationError("inputs executáveis mudaram durante build/test")
    red = _load_json(root, RED_PATH)
    green = _load_json(root, GREEN_PATH)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "kind": "ft.bug.validation",
        "result": "pass",
        "product_root": product_root,
        "regression_argv": red["argv"],
        "red_output_sha256": red["output_sha256"],
        "green_output_sha256": green["output_sha256"],
        "test_hashes": red["test_hashes"],
        "commands": commands,
        "fingerprint": after,
    }
    _write_json(root, VALIDATION_PATH, payload)
    print(f"bug full validation PASS: {VALIDATION_PATH}")


def command_verify(root: Path) -> None:
    receipt = _load_json(root, VALIDATION_PATH)
    if (
        receipt.get("schema_version") != SCHEMA_VERSION
        or receipt.get("kind") != "ft.bug.validation"
        or receipt.get("result") != "pass"
    ):
        raise BugValidationError("receipt final possui schema/kind/result inválido")
    _assert_test_hashes(root, dict(receipt.get("test_hashes") or {}))
    if receipt.get("fingerprint") != _fingerprint(root):
        raise BugValidationError("receipt final não corresponde ao produto atual")
    print("bug validation receipt VERIFIED")


def _assert_unrelated_unchanged(
    baseline_rows: list[dict[str, str]],
    current_rows: list[dict[str, str]],
    allowed: set[str],
    label: str,
) -> None:
    before = _records_by_id(baseline_rows)
    after = _records_by_id(current_rows)
    for identifier, row in before.items():
        if identifier not in allowed and after.get(identifier) != row:
            raise BugValidationError(f"{label}: registro alheio mudou: {identifier}")
    unexpected = set(after) - set(before) - allowed
    if unexpected:
        raise BugValidationError(
            f"{label}: registros alheios criados: {', '.join(sorted(unexpected))}"
        )


def _assert_row_changes_limited(
    before: dict[str, str],
    after: dict[str, str],
    allowed_columns: set[str],
    label: str,
) -> None:
    changed = {
        column
        for column in set(before) | set(after)
        if before.get(column, "") != after.get(column, "")
    }
    forbidden = sorted(changed - allowed_columns)
    if forbidden:
        raise BugValidationError(
            f"{label}: colunas imutáveis mudaram: {', '.join(forbidden)}"
        )


def validate_reconcile(root: Path) -> None:
    command_verify(root)
    baseline = _baseline(root)
    metadata, _report = _report_contract(root)
    backlog_id = metadata["backlog_item"]
    feature_id = metadata["target_feature"]
    current_backlog_text = _read(root, "docs/PROJECT_BACKLOG.md")
    current_features_text = _read(root, "docs/FEATURES.md")
    current_backlog = _markdown_records(current_backlog_text)
    current_features = _markdown_records(current_features_text)
    baseline_backlog = list(baseline.get("project_backlog") or [])
    baseline_features = list(baseline.get("features") or [])
    _assert_document_structure_preserved(
        baseline,
        current_backlog_text,
        current_features_text,
        backlog_id,
        feature_id,
    )
    _assert_unrelated_unchanged(
        baseline_backlog, current_backlog, {backlog_id}, "PROJECT_BACKLOG"
    )
    _assert_unrelated_unchanged(
        baseline_features, current_features, {feature_id}, "FEATURES"
    )
    if set(_records_by_id(current_features)) != set(_records_by_id(baseline_features)):
        raise BugValidationError("bug não pode criar ou remover FEAT")
    backlog_row = _records_by_id(current_backlog).get(backlog_id)
    if backlog_row is None:
        raise BugValidationError(f"PROJECT_BACKLOG não contém {backlog_id}")
    status = _normalize(_row_value(backlog_row, "status", "estado"))
    if status not in {"done", "accepted"}:
        raise BugValidationError(f"{backlog_id} deve terminar done/accepted")
    feature_row = _records_by_id(current_features).get(feature_id)
    if feature_row is None or backlog_id not in " ".join(feature_row.values()).upper():
        raise BugValidationError(f"{feature_id} não referencia {backlog_id}")
    original_backlog_row = _records_by_id(baseline_backlog).get(backlog_id)
    if original_backlog_row is not None:
        _assert_row_changes_limited(
            original_backlog_row,
            backlog_row,
            {"status", "estado", "evidencia", "evidence", "decisao_notas", "notas"},
            backlog_id,
        )
    original_feature_row = _records_by_id(baseline_features)[feature_id]
    _assert_row_changes_limited(
        original_feature_row,
        feature_row,
        {
            "backlog",
            "evidencia",
            "evidence",
            "ultima_evolucao",
            "last_evolution",
            "notas",
            "notes",
        },
        feature_id,
    )

    changelog = _read(root, "CHANGELOG.md")
    current_entries = _bug_entries(changelog, "BUG")
    baseline_entries = list(baseline.get("bug_changelog_entries") or [])
    if Counter(baseline_entries) - Counter(current_entries):
        raise BugValidationError("CHANGELOG.md removeu entrada #BUG histórica")
    new_counter = Counter(current_entries) - Counter(baseline_entries)
    new_entries = list(new_counter.elements())
    if len(new_entries) != 1:
        raise BugValidationError(
            "CHANGELOG.md deve adicionar exatamente uma entrada iniciada por #BUG"
        )
    entry = new_entries[0].upper()
    if backlog_id not in entry or feature_id not in entry:
        raise BugValidationError("entrada #BUG deve conter PB e FEAT do bug")
    if _bug_entries(changelog, "FEAT") != list(
        baseline.get("feature_changelog_entries") or []
    ):
        raise BugValidationError("template bug não pode criar ou alterar entrada #FEAT")
    original_changelog = dict(baseline.get("documentation_text") or {}).get(
        "CHANGELOG.md"
    )
    if not isinstance(original_changelog, str):
        raise BugValidationError("baseline não preservou CHANGELOG.md")
    removed = False
    remaining: list[str] = []
    for line in changelog.splitlines(keepends=True):
        if not removed and line.strip() == new_entries[0]:
            removed = True
            continue
        remaining.append(line)
    if not removed or "".join(remaining) != original_changelog:
        raise BugValidationError(
            "CHANGELOG.md só pode inserir a nova linha #BUG, preservando o restante"
        )
    result = _read(root, RESULT_PATH)
    for required in (backlog_id, feature_id, "RED", "GREEN"):
        if required.upper() not in result.upper():
            raise BugValidationError(f"bug-result não contém evidência obrigatória: {required}")
    print("bug reconcile PASS: PB/FEAT existentes e entrada #BUG única")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path)
    parser.add_argument(
        "mode",
        choices=(
            "baseline",
            "begin",
            "status",
            "red",
            "green",
            "implementation",
            "full",
            "verify",
            "reconcile",
        ),
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        root = _find_root(args.root)
        handlers = {
            "baseline": command_baseline,
            "begin": command_begin,
            "status": command_status,
            "implementation": validate_implementation,
            "full": command_full,
            "verify": command_verify,
            "reconcile": validate_reconcile,
        }
        if args.mode == "red":
            command_red(root, args.command)
        elif args.mode == "green":
            command_green(root, args.command)
        else:
            if args.command:
                raise BugValidationError(f"argumentos inesperados em {args.mode}")
            handlers[args.mode](root)
    except BugValidationError as exc:
        print(f"bug validation FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
