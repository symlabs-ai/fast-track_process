#!/usr/bin/env python3
"""Deterministic RED→GREEN and governance validators for ``template bug-fast``."""

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
REVIEW_MD_PATH = Path("docs/bug-review.md")
REVIEW_PATH = Path("docs/bug-review.yml")
FIX_BASELINE_PATH = Path("docs/bug-fix-baseline.yml")
FIX_REVIEW_MD_PATH = Path("docs/bug-fix-review.md")
FIX_REVIEW_PATH = Path("docs/bug-fix-review.yml")
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
MAX_TOTAL_FILES = 24
MAX_CHANGED_LINES = 500
MAX_FIX_FILES = 4
MAX_FIX_TOTAL_FILES = 12
MAX_FIX_CHANGED_LINES = 250
MAX_CAPTURE_CHARS = 30_000
REGRESSION_TIMEOUT_SECONDS = 90
FULL_COMMAND_TIMEOUT_SECONDS = 180
PRODUCT_CHANGE_ROOTS = frozenset({"project", "src", "test", "tests"})
DERIVED_ARTIFACT_DIRS = frozenset({"artifacts", "generated", "receipts"})
DERIVED_ARTIFACT_SUFFIXES = (
    "-package.json",
    "-receipt.json",
    "-result.json",
)
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


def _write_text(root: Path, relative: Path, content: str) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content.rstrip() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _write_yaml(root: Path, relative: Path, payload: dict[str, Any]) -> None:
    _write_text(
        root,
        relative,
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
    )


def _load_yaml(root: Path, relative: Path) -> dict[str, Any]:
    try:
        payload = yaml.safe_load(_read(root, relative)) or {}
    except yaml.YAMLError as exc:
        raise BugValidationError(f"YAML inválido em {relative}: {exc}") from exc
    if not isinstance(payload, dict):
        raise BugValidationError(f"{relative} deve conter um mapping YAML")
    return payload


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


_BUG_CHANGELOG_HEADINGS = {"### Corrigido", "### Fixed"}
_UNRELEASED_HEADING = re.compile(
    r"^[ \t]*##[ \t]+(?:unreleased|\[unreleased\])[ \t]*(?:#+[ \t]*)?$",
    re.IGNORECASE,
)
_LEVEL_TWO_HEADING = re.compile(r"^[ \t]*##(?:[ \t]+|$)")


def _unreleased_bounds(lines: list[str]) -> tuple[int, int] | None:
    """Return the first Unreleased section without including its heading."""
    for start, line in enumerate(lines):
        if not _UNRELEASED_HEADING.fullmatch(line):
            continue
        end = next(
            (
                index
                for index in range(start + 1, len(lines))
                if _LEVEL_TWO_HEADING.match(lines[index])
            ),
            len(lines),
        )
        return start + 1, end
    return None


def _validate_changelog_insertions(
    original: str,
    current: str,
    new_bug_entry: str,
) -> None:
    """Allow a focal changelog insertion without permitting history rewrites."""
    original_lines = original.splitlines()
    current_lines = current.splitlines()
    inserted_lines: list[tuple[int, str]] = []
    original_index = 0

    for current_index, line in enumerate(current_lines):
        if (
            original_index < len(original_lines)
            and line == original_lines[original_index]
        ):
            original_index += 1
        else:
            inserted_lines.append((current_index, line))

    if original_index != len(original_lines):
        raise BugValidationError(
            "CHANGELOG.md removeu, editou ou reordenou conteúdo histórico"
        )

    inserted_text = [
        line.strip() for _index, line in inserted_lines if line.strip()
    ]
    if inserted_text.count(new_bug_entry) != 1:
        raise BugValidationError(
            "CHANGELOG.md deve inserir exatamente a nova linha #BUG"
        )

    headings = [
        line for line in inserted_text if line in _BUG_CHANGELOG_HEADINGS
    ]
    unexpected = [
        line
        for line in inserted_text
        if line != new_bug_entry and line not in _BUG_CHANGELOG_HEADINGS
    ]
    if unexpected:
        raise BugValidationError(
            "CHANGELOG.md contém texto novo além da entrada #BUG e do heading permitido"
        )
    if len(headings) > 1:
        raise BugValidationError(
            "CHANGELOG.md pode inserir no máximo um heading ### Corrigido/### Fixed"
        )

    original_unreleased = _unreleased_bounds(original_lines)
    current_unreleased = _unreleased_bounds(current_lines)
    if original_unreleased is not None and current_unreleased is not None:
        start, end = current_unreleased
        misplaced = [
            line.strip() or "<linha em branco>"
            for index, line in inserted_lines
            if not start <= index < end
        ]
        if misplaced:
            raise BugValidationError(
                "CHANGELOG.md deve limitar inserções à seção Unreleased"
            )

        original_start, original_end = original_unreleased
        original_heading_lines = original_lines[original_start:original_end]
    else:
        # Changelogs sem uma seção Unreleased continuam suportados. Nesse
        # layout flat, o arquivo inteiro é a única seção disponível.
        original_heading_lines = original_lines
    original_headings = {
        line.strip()
        for line in original_heading_lines
        if line.strip() in _BUG_CHANGELOG_HEADINGS
    }
    if headings and original_headings:
        raise BugValidationError(
            "CHANGELOG.md já possuía heading ### Corrigido/### Fixed"
        )


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
        "limits": {
            "max_primary_files": MAX_FILES,
            "max_total_files": MAX_TOTAL_FILES,
            "max_changed_lines": MAX_CHANGED_LINES,
        },
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
    return [
        path
        for path in _changed_paths(root, str(baseline["base_commit"]))
        if Path(path).parts and Path(path).parts[0] in PRODUCT_CHANGE_ROOTS
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


def _is_derived_artifact_path(relative: str) -> bool:
    path = Path(relative)
    lowered_parts = {part.lower() for part in path.parts}
    name = path.name.lower()
    return bool(
        lowered_parts.intersection(DERIVED_ARTIFACT_DIRS)
        or name.endswith(DERIVED_ARTIFACT_SUFFIXES)
    )


def _validate_change_budget(
    changed: list[str],
    *,
    primary_limit: int,
    total_limit: int,
    label: str,
) -> tuple[int, int]:
    primary = [
        relative
        for relative in changed
        if not _is_derived_artifact_path(relative)
    ]
    derived_count = len(changed) - len(primary)
    if len(changed) > total_limit:
        raise BugValidationError(
            f"{label} alterou {len(changed)} arquivos no total; limite {total_limit}"
        )
    if len(primary) > primary_limit:
        raise BugValidationError(
            f"{label} alterou {len(primary)} arquivos primários; "
            f"limite {primary_limit} (mais {derived_count} derivado(s))"
        )
    return len(primary), derived_count


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
        if path.parts[0] == ".ft" and not relative.startswith(
            ".ft/process/bug-fast/"
        ):
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
            "bug declarou ESCALATE; aborte e use ft run . --template feature"
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
    if path.parts[0] in PRODUCT_CHANGE_ROOTS:
        return True
    if path.parts[0] == "state":
        return True
    if relative in {
        "docs/feature-request.md",
        BASELINE_PATH.as_posix(),
        REPORT_PATH.as_posix(),
        VALIDATION_PATH.as_posix(),
        REVIEW_MD_PATH.as_posix(),
        REVIEW_PATH.as_posix(),
        FIX_BASELINE_PATH.as_posix(),
        FIX_REVIEW_MD_PATH.as_posix(),
        FIX_REVIEW_PATH.as_posix(),
        "docs/stakeholder-feedback.md",
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
    for relative in (
        GREEN_PATH,
        REVIEW_MD_PATH,
        REVIEW_PATH,
        FIX_BASELINE_PATH,
        FIX_REVIEW_MD_PATH,
        FIX_REVIEW_PATH,
    ):
        (root / relative).unlink(missing_ok=True)
    if (root / RED_PATH).is_file():
        _validate_red_receipt(root)
        print("bug attempt READY: RED preservado; produza novo GREEN")
    else:
        print("bug attempt READY: execute RED antes da correção")


def command_begin_fix(root: Path) -> None:
    _fix_baseline(root)
    for relative in (GREEN_PATH, FIX_REVIEW_MD_PATH, FIX_REVIEW_PATH):
        (root / relative).unlink(missing_ok=True)
    _validate_red_receipt(root)
    print("bug fix READY: âncora e RED preservados; produza novo GREEN")


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
    primary_count, derived_count = _validate_change_budget(
        changed,
        primary_limit=MAX_FILES,
        total_limit=MAX_TOTAL_FILES,
        label="bug",
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
        f"{changed_lines} linha(s), {primary_count} primário(s) e "
        f"{derived_count} derivado(s)"
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


def _required_text(payload: dict[str, Any], key: str, label: Path) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise BugValidationError(f"{label}: {key} deve ser texto não vazio")
    return value.strip()


def _review_findings(
    payload: dict[str, Any],
    *,
    label: Path,
    fix_review: bool,
) -> list[dict[str, str]]:
    raw = payload.get("findings")
    if not isinstance(raw, list):
        raise BugValidationError(f"{label}: findings deve ser lista")
    findings: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise BugValidationError(f"{label}: finding {index} deve ser mapping")
        identifier = str(item.get("id") or "").upper()
        if not re.fullmatch(r"B-\d{2,}", identifier) or identifier in seen:
            raise BugValidationError(
                f"{label}: finding {index} exige ID B-NN único"
            )
        status = str(item.get("status") or "").upper()
        expected_statuses = {"PASS", "FAIL"} if fix_review else {"OPEN"}
        if status not in expected_statuses:
            raise BugValidationError(
                f"{label}: status de {identifier} deve ser "
                + "/".join(sorted(expected_statuses))
            )
        evidence = str(item.get("evidence") or "").strip()
        if len(evidence) < 4:
            raise BugValidationError(
                f"{label}: {identifier} exige evidence verificável"
            )
        finding = {
            "id": identifier,
            "status": status,
            "evidence": evidence,
        }
        if not fix_review:
            route = str(item.get("route") or "").lower()
            if route not in {"fix", "scope"}:
                raise BugValidationError(
                    f"{label}: route de {identifier} deve ser fix/scope"
                )
            finding["route"] = route
        findings.append(finding)
        seen.add(identifier)
    return findings


def _assert_review_paths(
    root: Path,
    markdown_path: Path,
    paths: list[str],
    required_tokens: list[str],
) -> None:
    markdown = _read(root, markdown_path)
    missing_paths = [path for path in paths if path not in markdown]
    if missing_paths:
        raise BugValidationError(
            f"{markdown_path}: não menciona path auditado: "
            + ", ".join(missing_paths)
        )
    upper = markdown.upper()
    missing_tokens = [token for token in required_tokens if token.upper() not in upper]
    if missing_tokens:
        raise BugValidationError(
            f"{markdown_path}: não contém "
            + ", ".join(missing_tokens)
        )


def validate_review(root: Path) -> dict[str, Any]:
    command_verify(root)
    baseline = _baseline(root)
    receipt = _load_json(root, VALIDATION_PATH)
    payload = _load_yaml(root, REVIEW_PATH)
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise BugValidationError(f"{REVIEW_PATH}: schema_version deve ser 1")
    verdict = str(payload.get("verdict") or "").upper()
    route = str(payload.get("review_route") or "").lower()
    if verdict not in {"APPROVED", "REJECTED"}:
        raise BugValidationError(f"{REVIEW_PATH}: verdict inválido")
    if route not in {"approved", "fix", "scope"}:
        raise BugValidationError(f"{REVIEW_PATH}: review_route inválida")
    _required_text(payload, "summary", REVIEW_PATH)
    if payload.get("base_commit") != baseline.get("base_commit"):
        raise BugValidationError(f"{REVIEW_PATH}: base_commit diverge da baseline")
    if payload.get("receipt_fingerprint") != receipt.get("fingerprint"):
        raise BugValidationError(
            f"{REVIEW_PATH}: receipt_fingerprint diverge do receipt atual"
        )

    raw_checks = payload.get("checks")
    if not isinstance(raw_checks, list):
        raise BugValidationError(f"{REVIEW_PATH}: checks deve ser lista")
    expected_ids = {"regression", "minimal_delta", "receipt", "scope"}
    checks: dict[str, str] = {}
    for item in raw_checks:
        if not isinstance(item, dict):
            raise BugValidationError(f"{REVIEW_PATH}: check deve ser mapping")
        identifier = str(item.get("id") or "").lower()
        status = str(item.get("status") or "").upper()
        evidence = str(item.get("evidence") or "").strip()
        if (
            identifier not in expected_ids
            or identifier in checks
            or status not in {"PASS", "FAIL"}
            or len(evidence) < 4
        ):
            raise BugValidationError(
                f"{REVIEW_PATH}: check inválido/duplicado: {identifier or '<vazio>'}"
            )
        checks[identifier] = status
    if set(checks) != expected_ids:
        raise BugValidationError(
            f"{REVIEW_PATH}: checks devem ser exatamente "
            + ", ".join(sorted(expected_ids))
        )

    findings = _review_findings(payload, label=REVIEW_PATH, fix_review=False)
    if verdict == "APPROVED":
        if route != "approved" or findings or set(checks.values()) != {"PASS"}:
            raise BugValidationError(
                f"{REVIEW_PATH}: APPROVED exige rota approved, checks PASS e findings vazio"
            )
    else:
        if route == "approved" or not findings or "FAIL" not in checks.values():
            raise BugValidationError(
                f"{REVIEW_PATH}: REJECTED exige rota fix/scope, finding e check FAIL"
            )
        finding_routes = {finding["route"] for finding in findings}
        if route == "fix" and finding_routes != {"fix"}:
            raise BugValidationError(
                f"{REVIEW_PATH}: rota fix não pode ocultar achado de scope"
            )
        if route == "scope" and "scope" not in finding_routes:
            raise BugValidationError(
                f"{REVIEW_PATH}: rota scope exige achado marcado scope"
            )

    changed = _changed_product_paths(root, baseline)
    _assert_review_paths(
        root,
        REVIEW_MD_PATH,
        changed,
        ["RED", "GREEN", "receipt", "escopo"],
    )
    for finding in findings:
        if finding["id"] not in _read(root, REVIEW_MD_PATH).upper():
            raise BugValidationError(
                f"{REVIEW_MD_PATH}: não contém {finding['id']}"
            )
    print(f"bug review PASS: {verdict}/{route}")
    return payload


def _fix_baseline(
    root: Path,
    *,
    require_current_review: bool = True,
) -> dict[str, Any]:
    payload = _load_yaml(root, FIX_BASELINE_PATH)
    if (
        payload.get("schema_version") != SCHEMA_VERSION
        or payload.get("kind") != "ft.bug.fix-baseline"
    ):
        raise BugValidationError(f"{FIX_BASELINE_PATH}: schema/kind inválido")
    base_commit = str(payload.get("base_commit") or "")
    if not re.fullmatch(r"[0-9a-fA-F]{7,64}", base_commit):
        raise BugValidationError(f"{FIX_BASELINE_PATH}: base_commit inválido")
    exists = _git(root, "cat-file", "-e", f"{base_commit}^{{commit}}")
    if exists.returncode != 0:
        raise BugValidationError(f"{FIX_BASELINE_PATH}: commit âncora ausente")
    if payload.get("source_review") != REVIEW_PATH.as_posix():
        raise BugValidationError(f"{FIX_BASELINE_PATH}: source_review inválido")
    source_sha = _sha256_path(root / REVIEW_PATH)
    if (
        require_current_review
        and payload.get("source_review_sha256") != source_sha
    ):
        raise BugValidationError(f"{FIX_BASELINE_PATH}: review fonte mudou")
    findings = payload.get("findings")
    initial_paths = payload.get("initial_product_paths")
    if (
        not isinstance(findings, list)
        or not findings
        or not all(isinstance(item, str) and re.fullmatch(r"B-\d{2,}", item) for item in findings)
        or not isinstance(initial_paths, list)
        or not initial_paths
        or not all(isinstance(item, str) for item in initial_paths)
    ):
        raise BugValidationError(f"{FIX_BASELINE_PATH}: achados/paths inválidos")
    return payload


def command_prepare_fix(root: Path) -> None:
    review = validate_review(root)
    if (
        str(review.get("verdict") or "").upper() != "REJECTED"
        or str(review.get("review_route") or "").lower() != "fix"
    ):
        raise BugValidationError("prepare-fix exige review REJECTED com rota fix")
    refreshed = False
    if (root / FIX_BASELINE_PATH).is_file():
        previous = _fix_baseline(root, require_current_review=False)
        if previous.get("source_review_sha256") == _sha256_path(root / REVIEW_PATH):
            print("bug fix baseline REUSED")
            return
        refreshed = True

    baseline = _baseline(root)
    head = _head(root)
    pending_product = [
        path
        for path in _changed_paths(root, head)
        if Path(path).parts and Path(path).parts[0] in PRODUCT_CHANGE_ROOTS
    ]
    if pending_product:
        raise BugValidationError(
            "prepare-fix exige implementação revisada já commitada: "
            + ", ".join(pending_product)
        )
    findings = _review_findings(review, label=REVIEW_PATH, fix_review=False)
    receipt = _load_json(root, VALIDATION_PATH)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "kind": "ft.bug.fix-baseline",
        "base_commit": head,
        "source_review": REVIEW_PATH.as_posix(),
        "source_review_sha256": _sha256_path(root / REVIEW_PATH),
        "receipt_fingerprint": receipt["fingerprint"],
        "findings": [finding["id"] for finding in findings],
        "initial_product_paths": _changed_product_paths(root, baseline),
        "limits": {
            "max_primary_files": MAX_FIX_FILES,
            "max_total_files": MAX_FIX_TOTAL_FILES,
            "max_changed_lines": MAX_FIX_CHANGED_LINES,
        },
    }
    _write_yaml(root, FIX_BASELINE_PATH, payload)
    action = "REFRESHED" if refreshed else "PASS"
    print(f"bug fix baseline {action}: {FIX_BASELINE_PATH}")


def _fix_changed_product_paths(root: Path, anchor: dict[str, Any]) -> list[str]:
    return [
        path
        for path in _changed_paths(root, str(anchor["base_commit"]))
        if Path(path).parts and Path(path).parts[0] in PRODUCT_CHANGE_ROOTS
    ]


def validate_fix_implementation(root: Path) -> None:
    anchor = _fix_baseline(root)
    validate_implementation(root)
    changed = _fix_changed_product_paths(root, anchor)
    if not changed:
        raise BugValidationError("fix focal não alterou o produto desde a âncora")
    primary_count, derived_count = _validate_change_budget(
        changed,
        primary_limit=MAX_FIX_FILES,
        total_limit=MAX_FIX_TOTAL_FILES,
        label="fix",
    )
    changed_lines = _changed_line_count(
        root,
        {"base_commit": anchor["base_commit"]},
        changed,
    )
    if changed_lines > MAX_FIX_CHANGED_LINES:
        raise BugValidationError(
            f"fix alterou {changed_lines} linhas; limite {MAX_FIX_CHANGED_LINES}"
        )
    print(
        f"bug fix implementation PASS: {len(changed)} arquivo(s), "
        f"{changed_lines} linha(s), {primary_count} primário(s) e "
        f"{derived_count} derivado(s)"
    )


def validate_fix_review(root: Path) -> dict[str, Any]:
    anchor = _fix_baseline(root)
    payload = _load_yaml(root, FIX_REVIEW_PATH)
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise BugValidationError(f"{FIX_REVIEW_PATH}: schema_version deve ser 1")
    verdict = str(payload.get("verdict") or "").upper()
    route = str(payload.get("review_route") or "").lower()
    if verdict not in {"APPROVED", "REJECTED"}:
        raise BugValidationError(f"{FIX_REVIEW_PATH}: verdict inválido")
    if route not in {"approved", "fix", "full_review", "scope"}:
        raise BugValidationError(f"{FIX_REVIEW_PATH}: review_route inválida")
    _required_text(payload, "summary", FIX_REVIEW_PATH)
    if payload.get("source_review") != REVIEW_PATH.as_posix():
        raise BugValidationError(f"{FIX_REVIEW_PATH}: source_review inválido")
    if payload.get("base_commit") != anchor.get("base_commit"):
        raise BugValidationError(f"{FIX_REVIEW_PATH}: base_commit diverge da âncora")
    if payload.get("receipt_fingerprint") != anchor.get("receipt_fingerprint"):
        raise BugValidationError(
            f"{FIX_REVIEW_PATH}: receipt_fingerprint diverge do receipt ancorado"
        )
    findings = _review_findings(payload, label=FIX_REVIEW_PATH, fix_review=True)
    expected_ids = set(str(item) for item in anchor["findings"])
    if {finding["id"] for finding in findings} != expected_ids:
        raise BugValidationError(
            f"{FIX_REVIEW_PATH}: deve listar exatamente os B-* originais"
        )
    failed = [finding for finding in findings if finding["status"] == "FAIL"]
    changed = _fix_changed_product_paths(root, anchor)
    escaped = sorted(set(changed) - set(anchor["initial_product_paths"]))
    if verdict == "APPROVED":
        if route != "approved" or failed or escaped:
            raise BugValidationError(
                f"{FIX_REVIEW_PATH}: APPROVED exige todos PASS e delta contido"
            )
    else:
        if route == "approved" or not failed:
            raise BugValidationError(
                f"{FIX_REVIEW_PATH}: REJECTED exige B-* FAIL e rota corretiva"
            )
        if escaped and route not in {"full_review", "scope"}:
            raise BugValidationError(
                f"{FIX_REVIEW_PATH}: delta fora da âncora exige full_review/scope"
            )
        if route == "full_review" and not escaped:
            raise BugValidationError(
                f"{FIX_REVIEW_PATH}: full_review exige path fora da âncora"
            )
    _assert_review_paths(
        root,
        FIX_REVIEW_MD_PATH,
        changed,
        list(expected_ids),
    )
    print(f"bug fix review PASS: {verdict}/{route}")
    return payload


def _assert_current_review_approved(root: Path) -> None:
    if (root / REVIEW_PATH).is_file():
        raw_review = _load_yaml(root, REVIEW_PATH)
        if (
            str(raw_review.get("verdict") or "").upper() == "APPROVED"
            and str(raw_review.get("review_route") or "").lower() == "approved"
        ):
            validate_review(root)
            return
    if (root / FIX_REVIEW_PATH).is_file():
        fix_review = validate_fix_review(root)
        if (
            str(fix_review.get("verdict") or "").upper() == "APPROVED"
            and str(fix_review.get("review_route") or "").lower() == "approved"
        ):
            return
    review = validate_review(root)
    if (
        str(review.get("verdict") or "").upper() != "APPROVED"
        or str(review.get("review_route") or "").lower() != "approved"
    ):
        raise BugValidationError("estado atual não possui review APPROVED")


def command_scope_block(_root: Path) -> None:
    raise BugValidationError(
        "escopo excede bug-fast; use `ft abort --cycle <id>` e "
        "`ft run . --template feature-fast --request ...`"
    )


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


def _safe_cell(value: str, *, limit: int | None = None) -> str:
    compact = re.sub(r"\s+", " ", value.replace("|", "/")).strip()
    return compact if limit is None else compact[:limit].rstrip()


def _append_cell(current: str, addition: str) -> str:
    current = current.strip()
    if addition.lower() in current.lower():
        return current
    if not current or current in {"—", "-"}:
        return addition
    return f"{current}; {addition}"


def _render_table_row(
    document: str,
    *,
    identifier: str,
    updates: dict[str, str],
    defaults: dict[str, str] | None = None,
    allow_insert: bool,
) -> str:
    lines = document.splitlines()
    table: tuple[int, int, list[str]] | None = None
    row_index: int | None = None
    for index in range(len(lines) - 1):
        raw_headers = [
            cell.strip() for cell in lines[index].strip().strip("|").split("|")
        ]
        normalized = [_normalize(cell) for cell in raw_headers]
        if (
            not lines[index].strip().startswith("|")
            or "id" not in normalized
            or "---" not in lines[index + 1]
        ):
            continue
        end = index + 2
        while end < len(lines):
            stripped = lines[end].strip()
            if not (stripped.startswith("|") and stripped.endswith("|")):
                break
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if cells and cells[0].upper() == identifier:
                row_index = end
            end += 1
        table = (index, end, normalized)
        if row_index is not None:
            break
    if table is None:
        raise BugValidationError("tabela canônica com coluna ID ausente")
    _header, insert_at, headers = table
    if row_index is not None:
        cells = [
            cell.strip() for cell in lines[row_index].strip().strip("|").split("|")
        ]
        if len(cells) != len(headers):
            raise BugValidationError(f"linha de {identifier} possui colunas inválidas")
    elif allow_insert:
        defaults = defaults or {}
        cells = [defaults.get(header, "—") for header in headers]
    else:
        raise BugValidationError(f"registro canônico ausente: {identifier}")
    for column, value in updates.items():
        normalized = _normalize(column)
        if normalized not in headers:
            continue
        cells[headers.index(normalized)] = _safe_cell(value)
    if "id" not in headers:
        raise BugValidationError("tabela canônica não possui coluna ID")
    cells[headers.index("id")] = identifier
    rendered = "| " + " | ".join(cells) + " |"
    if row_index is None:
        lines.insert(insert_at, rendered)
    else:
        lines[row_index] = rendered
    return "\n".join(lines).rstrip() + "\n"


def _row_from_text(document: str, identifier: str) -> dict[str, str] | None:
    return _records_by_id(_markdown_records(document)).get(identifier)


def _reconciled_backlog_text(
    baseline: dict[str, Any],
    metadata: dict[str, str],
    report: str,
) -> str:
    original = str(dict(baseline["documentation_text"])["docs/PROJECT_BACKLOG.md"])
    identifier = metadata["backlog_item"]
    existing = _row_from_text(original, identifier)
    evidence = (
        "`docs/bug-report.md`; `docs/bug-validation.json`; "
        "`docs/bug-review.md`; `docs/bug-result.md`"
    )
    notes = "Correção bug-fast aceita após RED→GREEN e review independente"
    updates = {"status": "accepted", "estado": "accepted"}
    defaults: dict[str, str] | None = None
    if existing is not None:
        current_evidence = _row_value(existing, "evidencia", "evidence")
        current_notes = _row_value(existing, "decisao_notas", "notas", "notes")
        updates.update(
            {
                "evidencia": _append_cell(current_evidence, evidence),
                "evidence": _append_cell(current_evidence, evidence),
                "decisao_notas": _append_cell(current_notes, notes),
                "notas": _append_cell(current_notes, notes),
                "notes": _append_cell(current_notes, notes),
            }
        )
    else:
        priority = {
            "critical": "P0",
            "high": "P0",
            "medium": "P1",
            "low": "P2",
        }[metadata["severity"]]
        symptom = _safe_cell(_section(report, "Sintoma"), limit=120)
        expected = _safe_cell(
            _section(report, "Comportamento esperado"),
            limit=180,
        )
        defaults = {
            "id": identifier,
            "tipo": "Bug",
            "type": "Bug",
            "prioridade": priority,
            "priority": priority,
            "status": "accepted",
            "estado": "accepted",
            "origem": "`docs/bug-report.md`",
            "origin": "`docs/bug-report.md`",
            "titulo": symptom,
            "title": symptom,
            "criterios_de_aceite": expected,
            "acceptance_criteria": expected,
            "evidencia": evidence,
            "evidence": evidence,
            "decisao_notas": notes,
            "notas": notes,
            "notes": notes,
        }
        updates = defaults
    return _render_table_row(
        original,
        identifier=identifier,
        updates=updates,
        defaults=defaults,
        allow_insert=existing is None,
    )


def _reconciled_features_text(
    baseline: dict[str, Any],
    metadata: dict[str, str],
) -> str:
    original = str(dict(baseline["documentation_text"])["docs/FEATURES.md"])
    identifier = metadata["target_feature"]
    existing = _row_from_text(original, identifier)
    if existing is None:
        raise BugValidationError(f"FEATURES baseline não contém {identifier}")
    backlog = _row_value(existing, "backlog")
    backlog_ids = [
        match.group(0).upper() for match in PB_RE.finditer(backlog)
    ]
    if metadata["backlog_item"] not in backlog_ids:
        backlog_ids.append(metadata["backlog_item"])
    evidence = (
        "`docs/bug-report.md`; `docs/bug-validation.json`; "
        "`docs/bug-review.md`; `docs/bug-result.md`"
    )
    notes = f"{metadata['backlog_item']} corrigido por bug-fast com RED→GREEN"
    current_evidence = _row_value(existing, "evidencia", "evidence")
    current_notes = _row_value(existing, "notas", "notes")
    updates = {
        "backlog": ", ".join(backlog_ids),
        "evidencia": _append_cell(current_evidence, evidence),
        "evidence": _append_cell(current_evidence, evidence),
        "ultima_evolucao": "bug-fast",
        "last_evolution": "bug-fast",
        "notas": _append_cell(current_notes, notes),
        "notes": _append_cell(current_notes, notes),
    }
    return _render_table_row(
        original,
        identifier=identifier,
        updates=updates,
        allow_insert=False,
    )


def _reconciled_changelog_text(
    baseline: dict[str, Any],
    metadata: dict[str, str],
    report: str,
) -> str:
    original = str(dict(baseline["documentation_text"])["CHANGELOG.md"])
    summary = _safe_cell(_section(report, "Correção"), limit=180).rstrip(".")
    entry = (
        f"- #BUG {metadata['backlog_item']} / {metadata['target_feature']} "
        f"— {summary}."
    )
    if entry in original.splitlines():
        return original.rstrip() + "\n"
    lines = original.splitlines()
    bounds = _unreleased_bounds(lines)
    if bounds is None:
        section_start, section_end = 0, len(lines)
    else:
        section_start, section_end = bounds
    fixed_index = next(
        (
            index
            for index in range(section_start, section_end)
            if lines[index].strip() in _BUG_CHANGELOG_HEADINGS
        ),
        None,
    )
    if fixed_index is not None:
        insert_at = fixed_index + 1
        while insert_at < section_end and not lines[insert_at].strip():
            insert_at += 1
        lines.insert(insert_at, entry)
    else:
        insert_at = section_start
        while insert_at < section_end and not lines[insert_at].strip():
            insert_at += 1
        insertion = ["### Corrigido", "", entry, ""]
        lines[insert_at:insert_at] = insertion
    return "\n".join(lines).rstrip() + "\n"


def _bug_result_text(
    root: Path,
    metadata: dict[str, str],
    report: str,
) -> str:
    receipt = _load_json(root, VALIDATION_PATH)
    changed = _changed_product_paths(root, _baseline(root))
    commands = [
        " ".join(str(token) for token in command)
        for command in receipt.get("commands", [])
        if isinstance(command, list)
    ]
    changed_lines = "\n".join(f"- `{path}`" for path in changed)
    command_lines = "\n".join(f"- `{command}`" for command in commands)
    return (
        "# Resultado do Bug Fast\n\n"
        f"- Backlog: {metadata['backlog_item']}\n"
        f"- Feature: {metadata['target_feature']}\n"
        f"- Severidade: {metadata['severity']}\n"
        f"- Receipt: `{receipt.get('fingerprint')}`\n\n"
        "## Sintoma\n\n"
        f"{_section(report, 'Sintoma')}\n\n"
        "## Causa raiz\n\n"
        f"{_section(report, 'Causa raiz')}\n\n"
        "## Correção\n\n"
        f"{_section(report, 'Correção')}\n\n"
        "## Evidência RED → GREEN\n\n"
        f"- Comando: `{receipt.get('regression_argv')}`\n"
        "- O mesmo teste congelado falhou em RED e passou em GREEN.\n\n"
        "## Build e testes\n\n"
        f"{command_lines}\n\n"
        "## Review independente\n\n"
        "- Estado atual aprovado em `docs/bug-review.yml` ou "
        "`docs/bug-fix-review.yml`.\n\n"
        "## Arquivos de produto alterados\n\n"
        f"{changed_lines}\n\n"
        "## Risco residual\n\n"
        f"{_section(report, 'Risco')}\n"
    )


def command_reconcile_apply(root: Path) -> None:
    command_verify(root)
    _assert_current_review_approved(root)
    baseline = _baseline(root)
    metadata, report = _report_contract(root)
    rendered = {
        Path("docs/PROJECT_BACKLOG.md"): _reconciled_backlog_text(
            baseline, metadata, report
        ),
        Path("docs/FEATURES.md"): _reconciled_features_text(baseline, metadata),
        Path("CHANGELOG.md"): _reconciled_changelog_text(
            baseline, metadata, report
        ),
        RESULT_PATH: _bug_result_text(root, metadata, report),
    }
    originals = dict(baseline["documentation_text"])
    for relative, content in rendered.items():
        target = root / relative
        current = (
            target.read_text(encoding="utf-8", errors="replace")
            if target.is_file()
            else ""
        )
        expected_original = (
            str(originals.get(relative.as_posix()) or "")
            if relative != RESULT_PATH
            else ""
        )
        if current.rstrip() not in {expected_original.rstrip(), content.rstrip()}:
            raise BugValidationError(
                f"reconcile-apply recusou estado parcial/alheio em {relative}"
            )
    for relative, content in rendered.items():
        _write_text(root, relative, content)
    validate_reconcile(root)


def validate_reconcile(root: Path) -> None:
    command_verify(root)
    _assert_current_review_approved(root)
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
            {
                "status",
                "estado",
                "evidencia",
                "evidence",
                "decisao_notas",
                "notas",
                "notes",
            },
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
    _validate_changelog_insertions(
        original_changelog,
        changelog,
        new_entries[0],
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
            "begin-fix",
            "status",
            "red",
            "green",
            "implementation",
            "fix-implementation",
            "full",
            "verify",
            "review",
            "prepare-fix",
            "fix-review",
            "scope-block",
            "reconcile-apply",
            "reconcile",
            "final",
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
            "begin-fix": command_begin_fix,
            "status": command_status,
            "implementation": validate_implementation,
            "fix-implementation": validate_fix_implementation,
            "full": command_full,
            "verify": command_verify,
            "review": validate_review,
            "prepare-fix": command_prepare_fix,
            "fix-review": validate_fix_review,
            "scope-block": command_scope_block,
            "reconcile-apply": command_reconcile_apply,
            "reconcile": validate_reconcile,
            "final": validate_reconcile,
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
