#!/usr/bin/env python3
"""Deterministic validators for the local ``feature`` process.

The script deliberately validates artifacts only. Test/build execution remains
owned by ``process.yml`` so command output is visible in the engine gate log.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unicodedata
from typing import Iterable

import yaml


AC_RE = re.compile(r"\bAC-\d{2,3}\b", re.IGNORECASE)
PB_RE = re.compile(r"\bPB-\d+[A-Z]?\b", re.IGNORECASE)
FEAT_RE = re.compile(r"\bFEAT-\d{3}\b", re.IGNORECASE)
CLARIFICATION_RE = re.compile(
    r"^\s*clarification_status\s*:\s*(required|clear)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
BASELINE_PATH = "docs/feature-baseline.yml"
RESERVATION_PATH = "docs/feature-id-reservation.yml"
EVIDENCE_PATH = "docs/feature-evidence.yml"
REVIEW_ROUTE_PATH = "docs/feature-review.yml"
RECONCILIATION_PATH = "docs/feature-reconciliation.yml"
RECEIPT_PATH = "docs/feature-validation.json"
DOCUMENTATION_PATHS = (
    "CHANGELOG.md",
    "docs/PRD.md",
    "docs/TECH_STACK.md",
    "docs/tech_stack.md",
    "docs/ui_criteria.md",
    "docs/api_contract.md",
    "docs/test_data.md",
    "docs/PROJECT_BACKLOG.md",
    "docs/FEATURES.md",
)
RECONCILIATION_PATHS = frozenset(DOCUMENTATION_PATHS)
REQUIRED_RECONCILIATION_PATHS = frozenset(
    {"CHANGELOG.md", "docs/PROJECT_BACKLOG.md", "docs/FEATURES.md"}
)


class FeatureValidationError(ValueError):
    """A user-facing feature artifact violation."""


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _has_tagged_feature_changelog_entry(text: str, backlog: str) -> bool:
    """Return whether this backlog has an entry led by the canonical tag."""
    pattern = re.compile(
        rf"(?m)^[ \t]*(?:[-*+][ \t]+)?#FEAT(?=[ \t]|$)"
        rf"[^\r\n]*\b{re.escape(backlog)}\b"
    )
    return pattern.search(text) is not None


def _read(root: Path, relative: str) -> str:
    path = root / relative
    if not path.is_file():
        raise FeatureValidationError(f"arquivo obrigatório ausente: {relative}")
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        raise FeatureValidationError(f"arquivo obrigatório vazio: {relative}")
    return text


def _read_yaml(root: Path, relative: str) -> dict[str, object]:
    text = _read(root, relative)
    try:
        payload = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise FeatureValidationError(f"{relative}: YAML inválido: {exc}") from exc
    if not isinstance(payload, dict):
        raise FeatureValidationError(f"{relative}: esperado mapping YAML")
    return payload


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
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


def _atomic_write_yaml(path: Path, payload: dict[str, object]) -> None:
    _atomic_write_text(
        path,
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
    )


def _frontmatter(text: str, path: str) -> dict[str, object]:
    if not text.lstrip().startswith("---"):
        raise FeatureValidationError(f"{path}: frontmatter YAML ausente")
    parts = text.lstrip().split("---", 2)
    if len(parts) < 3:
        raise FeatureValidationError(f"{path}: frontmatter YAML não foi fechado")
    try:
        data = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError as exc:
        raise FeatureValidationError(f"{path}: frontmatter YAML inválido: {exc}") from exc
    if not isinstance(data, dict):
        raise FeatureValidationError(f"{path}: frontmatter deve ser um mapping")
    return data


def _section(text: str, names: Iterable[str]) -> str:
    alternatives = "|".join(re.escape(name) for name in names)
    match = re.search(
        rf"(?ims)^##\s+(?:{alternatives})\s*$\n(.*?)(?=^##\s+|\Z)",
        text,
    )
    return match.group(1).strip() if match else ""


def _require_sections(text: str, path: str) -> None:
    expected = {
        "Objetivo": ("Objetivo", "Objective"),
        "Comportamento Esperado": ("Comportamento Esperado", "Expected Behavior"),
        "Critérios de Aceite": ("Critérios de Aceite", "Criterios de Aceite", "Acceptance Criteria"),
        "Fora do Escopo": ("Fora do Escopo", "Out of Scope"),
        "Restrições": ("Restrições", "Restricoes", "Constraints"),
    }
    missing = [label for label, aliases in expected.items() if not _section(text, aliases)]
    if missing:
        raise FeatureValidationError(f"{path}: seções ausentes/vazias: {', '.join(missing)}")


def _acceptance_ids(feature_text: str) -> list[str]:
    content = _section(
        feature_text,
        ("Critérios de Aceite", "Criterios de Aceite", "Acceptance Criteria"),
    )
    ids = [match.group(0).upper() for match in AC_RE.finditer(content)]
    if not ids:
        raise FeatureValidationError("docs/feature.md: nenhum critério AC-* encontrado")
    duplicates = sorted({item for item in ids if ids.count(item) > 1})
    if duplicates:
        raise FeatureValidationError(
            "docs/feature.md: critérios duplicados: " + ", ".join(duplicates)
        )
    return ids


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
            if len(cells) != len(headers):
                continue
            records.append(dict(zip(headers, cells)))
        if records or any(name in headers for name in ("status", "backlog")):
            return records
    return []


def _row_value(row: dict[str, str], *names: str) -> str:
    for name in names:
        value = row.get(_normalize(name), "")
        if value:
            return value
    return ""


def _find_row(records: list[dict[str, str]], identifier: str) -> dict[str, str] | None:
    wanted = identifier.upper()
    for row in records:
        match = re.search(r"\b(?:PB-\d+[A-Z]?|FEAT-\d{3})\b", _row_value(row, "id"), re.I)
        if match and match.group(0).upper() == wanted:
            return row
    return None


def _records_by_id(records: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    indexed: dict[str, dict[str, str]] = {}
    for row in records:
        identifier = _row_value(row, "id").upper()
        if identifier:
            indexed[identifier] = row
    return indexed


def _detect_product_root(root: Path) -> str:
    candidates = [
        relative
        for relative in ("project", "src")
        if (root / relative / "Makefile").is_file()
    ]
    if not candidates:
        if (root / "Makefile").is_file():
            return "."
        raise FeatureValidationError(
            "Makefile do produto ausente; esperado em project/Makefile, "
            "src/Makefile ou Makefile na raiz"
        )
    if len(candidates) > 1:
        raise FeatureValidationError(
            "mais de um diretório de produto possui Makefile: "
            + ", ".join(candidates)
        )
    return candidates[0]


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_baseline(root: Path, product_root: str) -> None:
    path = root / BASELINE_PATH
    if path.exists():
        return
    payload = {
        "version": 2,
        "product_root": product_root,
        "project_backlog": _markdown_records(_read(root, "docs/PROJECT_BACKLOG.md")),
        "features": _markdown_records(_read(root, "docs/FEATURES.md")),
        "documentation_sha256": {
            relative: _sha256(root / relative)
            for relative in DOCUMENTATION_PATHS
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _load_baseline(
    root: Path,
) -> tuple[list[dict[str, str]], list[dict[str, str]], str]:
    text = _read(root, BASELINE_PATH)
    try:
        payload = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise FeatureValidationError(f"{BASELINE_PATH}: YAML inválido: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("version") not in {1, 2}:
        raise FeatureValidationError(f"{BASELINE_PATH}: versão ausente ou inválida")
    backlog = payload.get("project_backlog")
    features = payload.get("features")
    product_root = payload.get("product_root")
    if not isinstance(backlog, list) or not isinstance(features, list):
        raise FeatureValidationError(f"{BASELINE_PATH}: tabelas da baseline ausentes")
    if product_root not in {"project", "src", "."}:
        raise FeatureValidationError(f"{BASELINE_PATH}: product_root ausente ou inválido")
    if not all(isinstance(row, dict) for row in [*backlog, *features]):
        raise FeatureValidationError(f"{BASELINE_PATH}: registros inválidos")
    return backlog, features, str(product_root)


def _baseline_documentation(root: Path) -> dict[str, str | None]:
    text = _read(root, BASELINE_PATH)
    try:
        payload = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise FeatureValidationError(f"{BASELINE_PATH}: YAML inválido: {exc}") from exc
    values = payload.get("documentation_sha256", {})
    if values is None:
        return {}
    if not isinstance(values, dict):
        raise FeatureValidationError(
            f"{BASELINE_PATH}: documentation_sha256 inválido"
        )
    return {
        str(path): str(digest) if digest is not None else None
        for path, digest in values.items()
    }


def _assert_unrelated_records_unchanged(
    *,
    baseline: list[dict[str, str]],
    current: list[dict[str, str]],
    allowed_ids: set[str],
    label: str,
) -> None:
    before = _records_by_id(baseline)
    after = _records_by_id(current)
    changed = sorted(
        identifier
        for identifier in before.keys() | after.keys()
        if identifier not in allowed_ids and before.get(identifier) != after.get(identifier)
    )
    if changed:
        raise FeatureValidationError(
            f"{label}: registros alheios à feature foram alterados: {', '.join(changed)}"
        )


def _feature_contract(root: Path) -> tuple[dict[str, str], str, list[str]]:
    text = _read(root, "docs/feature.md")
    raw = _frontmatter(text, "docs/feature.md")
    metadata = {str(key): str(value).strip() for key, value in raw.items()}
    required = ("type", "target_feature", "backlog_item", "priority", "interface")
    missing = [name for name in required if not metadata.get(name)]
    if missing:
        raise FeatureValidationError(
            "docs/feature.md: campos de frontmatter ausentes: " + ", ".join(missing)
        )

    feature_type = metadata["type"].lower()
    target = metadata["target_feature"].upper()
    backlog = metadata["backlog_item"].upper()
    priority = metadata["priority"].upper()
    interface = metadata["interface"].lower()
    if feature_type not in {"new", "evolution", "improvement"}:
        raise FeatureValidationError(f"docs/feature.md: type inválido: {feature_type}")
    if priority not in {"P0", "P1", "P2"}:
        raise FeatureValidationError(f"docs/feature.md: priority inválida: {priority}")
    if interface not in {"ui", "api", "internal", "mixed"}:
        raise FeatureValidationError(f"docs/feature.md: interface inválida: {interface}")
    if not PB_RE.fullmatch(backlog):
        raise FeatureValidationError(f"docs/feature.md: backlog_item inválido: {backlog}")
    if feature_type == "new" and target != "NEW":
        raise FeatureValidationError("docs/feature.md: type=new exige target_feature: new")
    if feature_type != "new" and not FEAT_RE.fullmatch(target):
        raise FeatureValidationError(
            "docs/feature.md: evolution/improvement exige target_feature FEAT-NNN"
        )

    _require_sections(text, "docs/feature.md")
    acceptance_ids = _acceptance_ids(text)

    backlog_records = _markdown_records(_read(root, "docs/PROJECT_BACKLOG.md"))
    if _find_row(backlog_records, backlog) is None:
        raise FeatureValidationError(f"PROJECT_BACKLOG não contém {backlog}")
    request_backlogs = {
        match.group(0).upper()
        for match in PB_RE.finditer(_read(root, "docs/feature-request.md"))
    }
    if request_backlogs != {backlog}:
        raise FeatureValidationError(
            "docs/feature.md deve preservar o único PB da demanda: "
            + (", ".join(sorted(request_backlogs)) or "nenhum")
        )

    feature_records = _markdown_records(_read(root, "docs/FEATURES.md"))
    if target != "NEW" and _find_row(feature_records, target) is None:
        raise FeatureValidationError(f"FEATURES não contém target_feature {target}")

    metadata.update(
        {
            "type": feature_type,
            "target_feature": target,
            "backlog_item": backlog,
            "priority": priority,
            "interface": interface,
        }
    )
    return metadata, text, acceptance_ids


def _assert_ac_pass(report: str, acceptance_ids: list[str], path: str) -> None:
    failures: list[str] = []
    for acceptance_id in acceptance_ids:
        evidence = [line for line in report.splitlines() if acceptance_id in line.upper()]
        if not evidence or not any(
            re.search(r"\bPASS(?:ED)?\b", line, re.I) and not re.search(r"\bFAIL(?:ED)?\b", line, re.I)
            for line in evidence
        ):
            failures.append(acceptance_id)
    if failures:
        raise FeatureValidationError(
            f"{path}: AC sem evidência PASS: {', '.join(failures)}"
        )


def _exact_review_status(value: str) -> str | None:
    """Return a status only when the whole Markdown cell is PASS or FAIL."""
    clean = value.strip()
    clean = clean.replace("**", "").replace("__", "").replace("`", "").strip()
    return clean if clean in {"PASS", "FAIL"} else None


def _review_ac_statuses(
    report: str,
    acceptance_ids: list[str],
    path: str,
) -> dict[str, str]:
    """Extract the dedicated PASS/FAIL value for every acceptance criterion.

    Review evidence may legitimately mention technical identifiers such as the
    edge kind ``fail``.  Consequently, status detection is deliberately tied
    to the cell immediately after an AC identifier (or to ``AC-NN: STATUS``),
    instead of scanning the whole evidence line for PASS/FAIL words.
    """
    expected = set(acceptance_ids)
    found: dict[str, set[str]] = {acceptance_id: set() for acceptance_id in acceptance_ids}

    for line in report.splitlines():
        if "|" in line:
            cells = [cell.strip() for cell in line.split("|")]
            for index, cell in enumerate(cells[:-1]):
                cell_ids = {
                    match.group(0).upper()
                    for match in AC_RE.finditer(cell)
                    if match.group(0).upper() in expected
                }
                if not cell_ids:
                    continue
                status = _exact_review_status(cells[index + 1])
                if status:
                    for acceptance_id in cell_ids:
                        found[acceptance_id].add(status)
            continue

        for acceptance_id in acceptance_ids:
            match = re.search(
                rf"\b{re.escape(acceptance_id)}\b\s*(?::|[-–—])\s*"
                r"(?:\*\*|__|`)?(PASS|FAIL)(?:\*\*|__|`)?\b",
                line,
            )
            if match:
                found[acceptance_id].add(match.group(1))

    missing = [acceptance_id for acceptance_id, statuses in found.items() if not statuses]
    ambiguous = [
        acceptance_id
        for acceptance_id, statuses in found.items()
        if len(statuses) > 1
    ]
    if missing or ambiguous:
        details: list[str] = []
        if missing:
            details.append("sem status PASS/FAIL: " + ", ".join(missing))
        if ambiguous:
            details.append("com status ambíguo: " + ", ".join(ambiguous))
        raise FeatureValidationError(f"{path}: " + "; ".join(details))

    return {
        acceptance_id: next(iter(statuses))
        for acceptance_id, statuses in found.items()
    }


def validate_baseline(root: Path) -> None:
    request = _read(root, "docs/feature-request.md")
    _read(root, "docs/PRD.md")
    backlog_text = _read(root, "docs/PROJECT_BACKLOG.md")
    _read(root, "docs/FEATURES.md")
    request_backlogs = sorted(
        {
            match.group(0).upper()
            for match in PB_RE.finditer(request)
        }
    )
    if len(request_backlogs) != 1:
        raise FeatureValidationError(
            "docs/feature-request.md deve referenciar exatamente um PB-* "
            "preexistente para permitir ciclos independentes e paralelos"
        )
    request_row = _find_row(_markdown_records(backlog_text), request_backlogs[0])
    if request_row is None:
        raise FeatureValidationError(
            f"PROJECT_BACKLOG não contém {request_backlogs[0]}"
        )
    request_status = _normalize(_row_value(request_row, "status", "estado"))
    if request_status not in {"planned", "ready", "in_progress"}:
        raise FeatureValidationError(
            f"{request_backlogs[0]} não está aberto para execução feature: "
            f"status={request_status or 'ausente'}"
        )
    product_root = _detect_product_root(root)
    makefile_path = f"{product_root}/Makefile"
    makefile = _read(root, makefile_path)
    missing_targets = [
        target for target in ("test", "build")
        if not re.search(rf"(?m)^{re.escape(target)}\s*:", makefile)
    ]
    if missing_targets:
        raise FeatureValidationError(
            f"{makefile_path} sem targets obrigatórios: " + ", ".join(missing_targets)
        )
    _write_baseline(root, product_root)


def validate_discovery(root: Path) -> None:
    discovery = _read(root, "docs/feature-discovery.md")
    questions = _read(root, "docs/feature-questions.md")
    _read(root, "docs/feature.md")
    _read(root, "docs/feature-plan.md")
    workset_text = _read(root, "docs/feature-workset.yml")
    try:
        workset = yaml.safe_load(workset_text) or {}
    except yaml.YAMLError as exc:
        raise FeatureValidationError(
            f"docs/feature-workset.yml: YAML inválido: {exc}"
        ) from exc
    if not isinstance(workset, dict) or workset.get("schema_version") != 1:
        raise FeatureValidationError(
            "docs/feature-workset.yml exige schema_version: 1"
        )
    workset_paths = workset.get("paths")
    if not isinstance(workset_paths, list) or not all(
        isinstance(path, str) and path.strip() for path in workset_paths
    ):
        raise FeatureValidationError(
            "docs/feature-workset.yml exige paths como lista de strings"
        )
    invalid_workset_paths = [
        path
        for path in workset_paths
        if Path(path).is_absolute()
        or ".." in Path(path).parts
        or not Path(path).parts
    ]
    if invalid_workset_paths:
        raise FeatureValidationError(
            "docs/feature-workset.yml contém paths inválidos: "
            + ", ".join(invalid_workset_paths)
        )
    # O workset é deliberadamente apenas uma dica focal. Paths previstos pelo
    # discovery podem ainda não existir e nunca restringem o write_scope.
    match = CLARIFICATION_RE.search(discovery)
    if not match:
        raise FeatureValidationError(
            "docs/feature-discovery.md sem clarification_status: required|clear"
        )
    if match.group(1).lower() == "required":
        if "?" not in questions:
            raise FeatureValidationError(
                "clarification_status=required exige perguntas em feature-questions.md"
            )
        return

    metadata, _, acceptance_ids = _feature_contract(root)
    plan = _read(root, "docs/feature-plan.md")
    required_refs = [metadata["backlog_item"], *acceptance_ids]
    if metadata["target_feature"] != "NEW":
        required_refs.append(metadata["target_feature"])
    missing = [reference for reference in required_refs if reference not in plan.upper()]
    if missing:
        raise FeatureValidationError(
            "docs/feature-plan.md sem referências obrigatórias: " + ", ".join(missing)
        )


def _git_common_dir(root: Path) -> Path:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FeatureValidationError(f"não foi possível localizar git common dir: {exc}") from exc
    if result.returncode != 0 or not result.stdout.strip():
        raise FeatureValidationError(
            "reserva de IDs exige worktree Git: "
            + (result.stderr.strip() or f"exit {result.returncode}")
        )
    common = Path(result.stdout.strip())
    if not common.is_absolute():
        common = root / common
    return common.resolve()


def _feature_number(identifier: str) -> int | None:
    match = FEAT_RE.fullmatch(identifier.upper())
    return int(identifier.split("-", 1)[1]) if match else None


def validate_reserve(root: Path) -> None:
    metadata, _, _ = _feature_contract(root)
    backlog = metadata["backlog_item"]
    target = metadata["target_feature"]
    owner_root = str(root.resolve())
    common_dir = _git_common_dir(root)
    registry_path = common_dir / "ft-feature-id-reservations.yml"
    lock_path = common_dir / "ft-feature-id-reservations.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            try:
                registry = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
            except FileNotFoundError:
                registry = {}
            except yaml.YAMLError as exc:
                raise FeatureValidationError(f"registry de IDs inválido: {exc}") from exc
            if not isinstance(registry, dict):
                raise FeatureValidationError("registry de IDs deve ser mapping")
            if registry.get("schema_version") not in {None, 1}:
                raise FeatureValidationError("registry de IDs possui schema_version inválido")
            reservations = registry.get("reservations", [])
            if not isinstance(reservations, list) or not all(
                isinstance(item, dict) for item in reservations
            ):
                raise FeatureValidationError("registry de IDs possui reservations inválidas")

            request_type = metadata["type"]
            own = next(
                (
                    item
                    for item in reservations
                    if item.get("backlog_item") == backlog
                    and item.get("worktree_root") == owner_root
                    and item.get("request_type") in {None, request_type}
                    and item.get("target_feature") in {None, target}
                ),
                None,
            )
            for item in reservations:
                if (
                    item.get("backlog_item") != backlog
                    or item.get("worktree_root") == owner_root
                ):
                    continue
                other_root = item.get("worktree_root")
                if isinstance(other_root, str) and Path(other_root).exists():
                    raise FeatureValidationError(
                        f"{backlog} já está reservado pelo ciclo em {other_root}; "
                        "ciclos paralelos devem usar PBs distintos"
                    )

            if own is not None:
                final_feature_id = str(own.get("feature_id") or "")
            elif metadata["type"] == "new":
                feature_records = _markdown_records(_read(root, "docs/FEATURES.md"))
                used = {
                    number
                    for number in (
                        _feature_number(_row_value(row, "id"))
                        for row in feature_records
                    )
                    if number is not None
                }
                used.update(
                    number
                    for number in (
                        _feature_number(str(item.get("feature_id") or ""))
                        for item in reservations
                    )
                    if number is not None
                )
                final_feature_id = f"FEAT-{max(used, default=0) + 1:03d}"
            else:
                final_feature_id = target

            if not FEAT_RE.fullmatch(final_feature_id):
                raise FeatureValidationError(
                    f"reserva produziu feature_id inválido: {final_feature_id or 'vazio'}"
                )
            if own is None:
                reservations.append(
                    {
                        "backlog_item": backlog,
                        "feature_id": final_feature_id,
                        "worktree_root": owner_root,
                        "request_type": request_type,
                        "target_feature": target,
                    }
                )
                _atomic_write_yaml(
                    registry_path,
                    {"schema_version": 1, "reservations": reservations},
                )
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    _atomic_write_yaml(
        root / RESERVATION_PATH,
        {
            "schema_version": 1,
            "backlog_item": backlog,
            "target_feature": target,
            "final_feature_id": final_feature_id,
            "request_type": metadata["type"],
            "reservation_owner": owner_root,
        },
    )


def _changed_product_paths(root: Path, product_root: str) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FeatureValidationError(f"não foi possível consultar git status: {exc}") from exc
    if result.returncode != 0:
        raise FeatureValidationError(
            "git status falhou: " + (result.stderr.strip() or f"exit {result.returncode}")
        )
    raw_paths: list[str] = []
    for line in result.stdout.splitlines():
        raw = line[3:].strip()
        if " -> " in raw:
            raw = raw.split(" -> ", 1)[1]
        raw_paths.append(raw)
    # Também contar mudanças JÁ COMMITADAS no ciclo: o node de implementação
    # auto-commita (ex.: "[feature.implement]"), deixando o working tree limpo —
    # sem isto o validador concluiria "nada mudou" mesmo com a feature pronta.
    # Aditivo (união com o working tree): só pode adicionar detecção.
    for base_ref in ("main", "master"):
        try:
            mb = subprocess.run(
                ["git", "merge-base", "HEAD", base_ref],
                cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=20, check=False,
            )
            if mb.returncode != 0 or not mb.stdout.strip():
                continue
            diff = subprocess.run(
                ["git", "diff", "--name-only", f"{mb.stdout.strip()}..HEAD"],
                cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=20, check=False,
            )
            if diff.returncode == 0:
                raw_paths.extend(p.strip() for p in diff.stdout.splitlines() if p.strip())
            break
        except (OSError, subprocess.TimeoutExpired):
            continue
    paths: list[str] = []
    seen: set[str] = set()
    for raw in raw_paths:
        if raw in seen:
            continue
        seen.add(raw)
        if product_root == ".":
            # Produto na raiz: docs/.ft/CHANGELOG são evidência do ciclo, não produto.
            first = raw.split("/", 1)[0]
            if first in {"docs", ".ft", ".git", "state"} or raw == "CHANGELOG.md":
                continue
            paths.append(raw)
        elif raw.startswith(f"{product_root}/"):
            paths.append(raw)
    return paths


def validate_implementation(root: Path) -> None:
    _feature_contract(root)
    _, _, product_root = _load_baseline(root)
    changed = _changed_product_paths(root, product_root)
    if not changed:
        raise FeatureValidationError(
            f"implementação não alterou nenhum arquivo em {product_root}/"
        )
    if not any("test" in path.lower() or "spec" in path.lower() for path in changed):
        raise FeatureValidationError("implementação não alterou nenhum arquivo de teste")


def _existing_relative_paths(root: Path, values: object, label: str) -> list[str]:
    if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
        raise FeatureValidationError(f"{label}: esperado lista de paths")
    normalized: list[str] = []
    for raw in values:
        candidate = Path(raw)
        if candidate.is_absolute() or ".." in candidate.parts or not candidate.parts:
            raise FeatureValidationError(f"{label}: path inválido: {raw}")
        path = root / candidate
        try:
            path.resolve(strict=True).relative_to(root)
        except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
            raise FeatureValidationError(f"{label}: path ausente/fora da raiz: {raw}") from exc
        if not path.is_file():
            raise FeatureValidationError(f"{label}: evidência deve ser arquivo: {raw}")
        normalized.append(candidate.as_posix())
    return normalized


def validate_evidence(root: Path) -> None:
    _, _, acceptance_ids = _feature_contract(root)
    report = _read(root, "docs/implementation-report.md")
    _assert_ac_pass(report, acceptance_ids, "docs/implementation-report.md")
    payload = _read_yaml(root, EVIDENCE_PATH)
    if payload.get("schema_version") != 1:
        raise FeatureValidationError(f"{EVIDENCE_PATH}: schema_version deve ser 1")
    if payload.get("receipt") != RECEIPT_PATH:
        raise FeatureValidationError(
            f"{EVIDENCE_PATH}: receipt deve ser {RECEIPT_PATH}"
        )
    try:
        receipt = json.loads(_read(root, RECEIPT_PATH))
    except json.JSONDecodeError as exc:
        raise FeatureValidationError(f"{RECEIPT_PATH}: JSON inválido: {exc}") from exc
    receipt_commands = receipt.get("commands") if isinstance(receipt, dict) else None
    if payload.get("commands") != receipt_commands:
        raise FeatureValidationError(
            f"{EVIDENCE_PATH}: commands devem corresponder exatamente ao receipt"
        )
    acceptance = payload.get("acceptance")
    if not isinstance(acceptance, list) or not all(isinstance(item, dict) for item in acceptance):
        raise FeatureValidationError(f"{EVIDENCE_PATH}: acceptance deve ser lista")
    indexed = {str(item.get("id") or "").upper(): item for item in acceptance}
    if set(indexed) != set(acceptance_ids) or len(indexed) != len(acceptance):
        raise FeatureValidationError(
            f"{EVIDENCE_PATH}: acceptance deve conter exatamente "
            + ", ".join(acceptance_ids)
        )
    for acceptance_id in acceptance_ids:
        item = indexed[acceptance_id]
        if item.get("status") not in {"PASS", "FAIL"}:
            raise FeatureValidationError(
                f"{EVIDENCE_PATH}: {acceptance_id} sem status PASS/FAIL"
            )
        tests = _existing_relative_paths(
            root, item.get("tests"), f"{EVIDENCE_PATH}:{acceptance_id}:tests"
        )
        if not tests:
            raise FeatureValidationError(
                f"{EVIDENCE_PATH}: {acceptance_id} deve referenciar ao menos um teste"
            )
        _existing_relative_paths(
            root,
            item.get("artifacts", []),
            f"{EVIDENCE_PATH}:{acceptance_id}:artifacts",
        )


def validate_review(root: Path) -> None:
    _, _, acceptance_ids = _feature_contract(root)
    report = _read(root, "docs/feature-review.md")
    route = _read_yaml(root, REVIEW_ROUTE_PATH)
    if route.get("schema_version") != 1:
        raise FeatureValidationError(f"{REVIEW_ROUTE_PATH}: schema_version deve ser 1")
    review_route = route.get("review_route")
    verdict = route.get("verdict")
    if review_route not in {"approved", "implementation", "evidence", "scope"}:
        raise FeatureValidationError(f"{REVIEW_ROUTE_PATH}: review_route inválida")
    if verdict not in {"APPROVED", "REJECTED"}:
        raise FeatureValidationError(f"{REVIEW_ROUTE_PATH}: verdict inválido")
    if not isinstance(route.get("summary"), str) or not str(route["summary"]).strip():
        raise FeatureValidationError(f"{REVIEW_ROUTE_PATH}: summary obrigatório")
    if (review_route == "approved") != (verdict == "APPROVED"):
        raise FeatureValidationError(
            f"{REVIEW_ROUTE_PATH}: approved exige APPROVED; demais rotas exigem REJECTED"
        )
    statuses = _review_ac_statuses(
        report,
        acceptance_ids,
        "docs/feature-review.md",
    )
    if review_route == "approved":
        failed = [acceptance_id for acceptance_id, status in statuses.items() if status == "FAIL"]
        if failed:
            raise FeatureValidationError(
                "docs/feature-review.md: Resultado APPROVED exige todos os AC como PASS; "
                "FAIL em " + ", ".join(failed)
            )


def _table_cells(line: str) -> list[str]:
    stripped = line.strip()
    if not (stripped.startswith("|") and stripped.endswith("|")):
        return []
    return [cell.strip() for cell in stripped.strip("|").split("|")]


def _row_identifier(line: str) -> str | None:
    cells = _table_cells(line)
    if not cells:
        return None
    match = re.fullmatch(r"(?:PB-\d+[A-Z]?|FEAT-\d{3})", cells[0], re.I)
    return match.group(0).upper() if match else None


def _replace_markdown_row(
    document: str,
    *,
    identifier: str,
    replacement: object,
    label: str,
    allow_insert: bool,
) -> str:
    if not isinstance(replacement, str) or "\n" in replacement.strip("\n"):
        raise FeatureValidationError(f"{label}: deve ser uma única linha Markdown")
    normalized = replacement.strip()
    if _row_identifier(normalized) != identifier:
        raise FeatureValidationError(
            f"{label}: a primeira coluna deve ser exatamente {identifier}"
        )
    replacement_cells = _table_cells(normalized)
    lines = document.splitlines()
    candidate_tables: list[tuple[int, int, int]] = []
    existing_index: int | None = None
    existing_table: tuple[int, int, int] | None = None
    for index in range(len(lines) - 1):
        headers = _table_cells(lines[index])
        separator = _table_cells(lines[index + 1])
        if not headers or not separator or "---" not in lines[index + 1]:
            continue
        if _normalize(headers[0]) != "id":
            continue
        row_index = index + 2
        while row_index < len(lines) and _table_cells(lines[row_index]):
            if _row_identifier(lines[row_index]) == identifier:
                existing_index = row_index
            row_index += 1
        table = (index, row_index, len(headers))
        candidate_tables.append(table)
        if existing_index is not None and index < existing_index < row_index:
            existing_table = table
            break

    selected = existing_table or (candidate_tables[0] if candidate_tables else None)
    if selected is None:
        raise FeatureValidationError(f"{label}: tabela canônica com coluna ID ausente")
    _, insert_at, column_count = selected
    if len(replacement_cells) != column_count:
        raise FeatureValidationError(
            f"{label}: esperado {column_count} colunas; recebidas "
            f"{len(replacement_cells)}"
        )
    if existing_index is not None:
        lines[existing_index] = normalized
    elif allow_insert:
        lines.insert(insert_at, normalized)
    else:
        raise FeatureValidationError(f"{label}: registro {identifier} ausente")
    return "\n".join(lines).rstrip() + "\n"


def _insert_changelog_entry(document: str, entry: object, backlog: str) -> str:
    if not isinstance(entry, str) or "\n" in entry.strip("\n"):
        raise FeatureValidationError(
            "changelog_entry deve ser uma única linha Markdown"
        )
    normalized = entry.strip()
    if not _has_tagged_feature_changelog_entry(normalized, backlog):
        raise FeatureValidationError(
            f"changelog_entry de {backlog} deve iniciar com #FEAT"
        )
    lines = document.splitlines()
    if normalized in (line.strip() for line in lines):
        return document.rstrip() + "\n"
    heading_index = next(
        (index for index, line in enumerate(lines) if line.startswith("## ")),
        next(
            (index for index, line in enumerate(lines) if line.startswith("# ")),
            -1,
        ),
    )
    insert_at = heading_index + 1
    while insert_at < len(lines) and not lines[insert_at].strip():
        insert_at += 1
    lines.insert(insert_at, normalized)
    return "\n".join(lines).rstrip() + "\n"


def _validated_reconciliation_proposal(root: Path) -> dict[str, str]:
    metadata, _, _ = _feature_contract(root)
    reservation = _read_yaml(root, RESERVATION_PATH)
    proposal = _read_yaml(root, RECONCILIATION_PATH)
    if proposal.get("schema_version") != 1:
        raise FeatureValidationError(f"{RECONCILIATION_PATH}: schema_version deve ser 1")
    backlog = metadata["backlog_item"]
    target = metadata["target_feature"]
    final_feature_id = str(reservation.get("final_feature_id") or "").upper()
    expected = {
        "backlog_item": backlog,
        "target_feature": target,
        "final_feature_id": final_feature_id,
    }
    mismatched = [
        key
        for key, value in expected.items()
        if str(proposal.get(key) or "").upper() != value
    ]
    if mismatched:
        raise FeatureValidationError(
            f"{RECONCILIATION_PATH}: IDs divergem da reserva/contrato: "
            + ", ".join(mismatched)
        )
    allowed_keys = {
        "schema_version",
        "backlog_item",
        "target_feature",
        "final_feature_id",
        "backlog_row",
        "feature_row",
        "changelog_entry",
        "documentation",
    }
    extra_keys = sorted(set(proposal) - allowed_keys)
    missing_keys = sorted(
        {
            "backlog_row",
            "feature_row",
            "changelog_entry",
        }
        - set(proposal)
    )
    if extra_keys or missing_keys:
        details: list[str] = []
        if extra_keys:
            details.append("não permitidos: " + ", ".join(extra_keys))
        if missing_keys:
            details.append("ausentes: " + ", ".join(missing_keys))
        raise FeatureValidationError(
            f"{RECONCILIATION_PATH}: campos " + "; ".join(details)
        )

    proposed_backlog_text = _replace_markdown_row(
        _read(root, "docs/PROJECT_BACKLOG.md"),
        identifier=backlog,
        replacement=proposal.get("backlog_row"),
        label="backlog_row",
        allow_insert=False,
    )
    proposed_features_text = _replace_markdown_row(
        _read(root, "docs/FEATURES.md"),
        identifier=final_feature_id,
        replacement=proposal.get("feature_row"),
        label="feature_row",
        allow_insert=metadata["type"] == "new",
    )
    proposed_changelog_text = _insert_changelog_entry(
        _read(root, "CHANGELOG.md"),
        proposal.get("changelog_entry"),
        backlog,
    )
    rendered_files: dict[str, str] = {
        "CHANGELOG.md": proposed_changelog_text,
        "docs/PROJECT_BACKLOG.md": proposed_backlog_text,
        "docs/FEATURES.md": proposed_features_text,
    }
    documentation = proposal.get("documentation", {})
    if not isinstance(documentation, dict):
        raise FeatureValidationError(
            f"{RECONCILIATION_PATH}: documentation deve ser mapping"
        )
    for raw_path, content in documentation.items():
        if (
            not isinstance(raw_path, str)
            or raw_path not in RECONCILIATION_PATHS
            or raw_path in REQUIRED_RECONCILIATION_PATHS
        ):
            raise FeatureValidationError(
                f"{RECONCILIATION_PATH}: path canônico não autorizado: {raw_path}"
            )
        if not isinstance(content, str) or not content.strip():
            raise FeatureValidationError(
                f"{RECONCILIATION_PATH}: conteúdo vazio/inválido para {raw_path}"
            )
        rendered_files[raw_path] = content.rstrip() + "\n"

    baseline_backlog, baseline_features, _ = _load_baseline(root)
    proposed_backlog = _markdown_records(proposed_backlog_text)
    _assert_unrelated_records_unchanged(
        baseline=baseline_backlog,
        current=proposed_backlog,
        allowed_ids={backlog},
        label="PROJECT_BACKLOG proposto",
    )
    backlog_row = _find_row(proposed_backlog, backlog)
    status = _normalize(_row_value(backlog_row or {}, "status", "estado"))
    if status not in {"done", "accepted"}:
        raise FeatureValidationError(
            f"PROJECT_BACKLOG proposto: {backlog} deve terminar done/accepted"
        )

    proposed_features = _markdown_records(proposed_features_text)
    baseline_ids = set(_records_by_id(baseline_features))
    proposed_ids = set(_records_by_id(proposed_features))
    if metadata["type"] == "new":
        new_ids = proposed_ids - baseline_ids
        if new_ids != {final_feature_id}:
            raise FeatureValidationError(
                "FEATURES proposta deve criar somente o ID reservado "
                f"{final_feature_id}; encontrados {', '.join(sorted(new_ids)) or 'nenhum'}"
            )
        allowed = {final_feature_id}
    else:
        new_ids = proposed_ids - baseline_ids
        if new_ids:
            raise FeatureValidationError(
                "FEATURES proposta não pode criar IDs em evolution/improvement: "
                + ", ".join(sorted(new_ids))
            )
        allowed = {target}
    _assert_unrelated_records_unchanged(
        baseline=baseline_features,
        current=proposed_features,
        allowed_ids=allowed,
        label="FEATURES proposta",
    )
    final_row = _find_row(proposed_features, final_feature_id)
    if final_row is None or backlog not in _row_value(final_row, "backlog").upper():
        raise FeatureValidationError(
            f"FEATURES proposta: {final_feature_id} deve referenciar {backlog}"
        )
    if not _has_tagged_feature_changelog_entry(proposed_changelog_text, backlog):
        raise FeatureValidationError(
            f"CHANGELOG proposto: entrada de {backlog} deve iniciar com #FEAT"
        )
    return rendered_files


def validate_proposal(root: Path) -> None:
    _validated_reconciliation_proposal(root)


def apply_reconciliation(root: Path) -> None:
    files = _validated_reconciliation_proposal(root)
    for relative, content in sorted(files.items()):
        assert isinstance(relative, str) and isinstance(content, str)
        target = root / relative
        current = root
        for part in Path(relative).parts:
            current = current / part
            if current.is_symlink():
                raise FeatureValidationError(
                    f"aplicação recusada: componente symlink em {relative}"
                )
        try:
            target.parent.resolve().relative_to(root)
        except (OSError, RuntimeError, ValueError) as exc:
            raise FeatureValidationError(
                f"aplicação recusada fora da raiz: {relative}"
            ) from exc
        _atomic_write_text(target, content)
    validate_reconcile(root)


def validate_reconcile(root: Path) -> None:
    metadata, _, acceptance_ids = _feature_contract(root)
    backlog = metadata["backlog_item"]
    target = metadata["target_feature"]
    reservation = _read_yaml(root, RESERVATION_PATH)
    final_feature_id = str(reservation.get("final_feature_id") or "").upper()
    if reservation.get("schema_version") != 1 or not FEAT_RE.fullmatch(final_feature_id):
        raise FeatureValidationError(f"{RESERVATION_PATH}: reserva inválida")
    if str(reservation.get("backlog_item") or "").upper() != backlog:
        raise FeatureValidationError(f"{RESERVATION_PATH}: backlog diverge de {backlog}")
    backlog_records = _markdown_records(_read(root, "docs/PROJECT_BACKLOG.md"))
    baseline_backlog, baseline_features, _ = _load_baseline(root)
    _assert_unrelated_records_unchanged(
        baseline=baseline_backlog,
        current=backlog_records,
        allowed_ids={backlog},
        label="PROJECT_BACKLOG",
    )
    backlog_row = _find_row(backlog_records, backlog)
    if backlog_row is None:
        raise FeatureValidationError(f"PROJECT_BACKLOG não contém {backlog}")
    status = _normalize(_row_value(backlog_row, "status", "estado"))
    if status not in {"done", "accepted"}:
        raise FeatureValidationError(f"{backlog} deve terminar done/accepted; atual: {status or 'vazio'}")

    feature_records = _markdown_records(_read(root, "docs/FEATURES.md"))
    baseline_feature_ids = set(_records_by_id(baseline_features))
    current_feature_ids = set(_records_by_id(feature_records))
    if metadata["type"] == "new":
        new_feature_ids = current_feature_ids - baseline_feature_ids
        if new_feature_ids != {final_feature_id}:
            raise FeatureValidationError(
                "FEATURES: feature new exige exatamente o ID reservado "
                f"{final_feature_id}; encontrados "
                + (", ".join(sorted(new_feature_ids)) or "nenhum")
            )
        allowed_feature_ids = new_feature_ids
    else:
        new_feature_ids = current_feature_ids - baseline_feature_ids
        if new_feature_ids:
            raise FeatureValidationError(
                "FEATURES: evolution/improvement não pode criar FEAT nova: "
                + ", ".join(sorted(new_feature_ids))
            )
        allowed_feature_ids = {target}
    _assert_unrelated_records_unchanged(
        baseline=baseline_features,
        current=feature_records,
        allowed_ids=allowed_feature_ids,
        label="FEATURES",
    )
    referencing = [
        row for row in feature_records
        if backlog in _row_value(row, "backlog").upper()
    ]
    if metadata["type"] == "new":
        if len(referencing) != 1:
            raise FeatureValidationError(
                f"feature new exige exatamente uma FEAT referenciando {backlog}; encontradas {len(referencing)}"
            )
        final_id = _row_value(referencing[0], "id").upper()
        if final_id != final_feature_id:
            raise FeatureValidationError(
                f"ID final {final_id} diverge da reserva {final_feature_id}"
            )
    else:
        target_row = _find_row(feature_records, target)
        if target_row is None or backlog not in _row_value(target_row, "backlog").upper():
            raise FeatureValidationError(f"{target} não foi reconciliada com {backlog}")

    result = _read(root, "docs/feature-result.md")
    _assert_ac_pass(result, acceptance_ids, "docs/feature-result.md")
    if backlog not in result.upper():
        raise FeatureValidationError(f"docs/feature-result.md não referencia {backlog}")

    changelog = _read(root, "CHANGELOG.md")
    baseline_documentation = _baseline_documentation(root)
    baseline_changelog = baseline_documentation.get("CHANGELOG.md")
    if baseline_changelog is not None and _sha256(root / "CHANGELOG.md") == baseline_changelog:
        raise FeatureValidationError("CHANGELOG.md não foi atualizado neste ciclo")
    if backlog not in changelog.upper():
        raise FeatureValidationError(f"CHANGELOG.md não referencia {backlog}")
    if not _has_tagged_feature_changelog_entry(changelog, backlog):
        raise FeatureValidationError(
            f"CHANGELOG.md: entrada de {backlog} deve usar `#FEAT` como "
            "primeiro token (bullet opcional)"
        )

    required_documentation = (
        "CHANGELOG.md",
        "docs/PROJECT_BACKLOG.md",
        "docs/FEATURES.md",
    )
    documentation_section = _section(
        result,
        ("Documentação atualizada", "Documentacao atualizada", "Updated Documentation"),
    )
    if not documentation_section:
        raise FeatureValidationError(
            "docs/feature-result.md sem seção `Documentação atualizada`"
        )
    missing_documentation = [
        relative
        for relative in required_documentation
        if relative not in documentation_section
    ]
    if missing_documentation:
        raise FeatureValidationError(
            "docs/feature-result.md não lista documentação obrigatória: "
            + ", ".join(missing_documentation)
        )


VALIDATORS = {
    "baseline": validate_baseline,
    "discovery": validate_discovery,
    "reserve": validate_reserve,
    "implementation": validate_implementation,
    "evidence": validate_evidence,
    "review": validate_review,
    "proposal": validate_proposal,
    "apply-reconcile": apply_reconciliation,
    "reconcile": validate_reconcile,
}
READ_ONLY_VALIDATOR_MODES = tuple(
    mode for mode in VALIDATORS if mode not in {"reserve", "apply-reconcile"}
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="Raiz do projeto/worktree")
    parser.add_argument("mode", choices=[*VALIDATORS, "all"])
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = Path(args.root).resolve()
    # ``all`` permanece diagnóstico: nunca reserva IDs nem aplica documentos.
    modes = list(READ_ONLY_VALIDATOR_MODES) if args.mode == "all" else [args.mode]
    try:
        for mode in modes:
            VALIDATORS[mode](root)
    except FeatureValidationError as exc:
        print(f"feature validation FAIL [{args.mode}]: {exc}", file=sys.stderr)
        return 1
    print(f"feature validation PASS [{args.mode}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
