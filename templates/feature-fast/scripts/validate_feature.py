#!/usr/bin/env python3
"""Deterministic validators for the local ``feature`` process.

The script deliberately validates artifacts only. Test/build execution remains
owned by ``process.yml`` so command output is visible in the engine gate log.
"""

from __future__ import annotations

import argparse
import fcntl
import fnmatch
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
FINDING_RE = re.compile(r"\bF-\d{2,3}\b", re.IGNORECASE)
PRE_FINDING_RE = re.compile(r"\bP-\d{2,3}\b", re.IGNORECASE)
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
FIX_BASELINE_PATH = "docs/feature-fix-baseline.yml"
FIX_REVIEW_PATH = "docs/feature-fix-review.md"
FIX_REVIEW_ROUTE_PATH = "docs/feature-fix-review.yml"
RECONCILIATION_PATH = "docs/feature-reconciliation.yml"
RECEIPT_PATH = "docs/feature-validation.json"
RECEIPT_BASELINE_PATH = "docs/feature-receipt-baseline.yml"
IMPACT_PATH = "docs/feature-impact.yml"
PRE_REVIEW_PATH = "docs/feature-pre-review.md"
PRE_REVIEW_ROUTE_PATH = "docs/feature-pre-review.yml"
REVIEW_CONTEXT_PATH = "docs/feature-review-context.yml"
MAX_ACCEPTANCE_CRITERIA = 6
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
FIX_CONTRACT_PATHS = (
    "docs/feature.md",
    "docs/feature-plan.md",
    "docs/feature-workset.yml",
    "docs/ui_criteria.md",
    "docs/api_contract.md",
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


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _safe_relative_path(raw: object, label: str, *, allow_glob: bool = False) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise FeatureValidationError(f"{label}: path vazio/inválido")
    value = raw.strip()
    lexical = value
    if allow_glob:
        wildcard_at = min(
            (index for token in ("*", "?", "[") if (index := value.find(token)) >= 0),
            default=len(value),
        )
        lexical = value[:wildcard_at].rstrip("/")
    candidate = Path(lexical or ".")
    full_candidate = Path(value)
    if (
        value.startswith("/")
        or candidate.is_absolute()
        or full_candidate.is_absolute()
        or ".." in full_candidate.parts
    ):
        raise FeatureValidationError(f"{label}: path fora da raiz: {value}")
    return Path(value).as_posix()


def _workset_contract(
    root: Path,
) -> tuple[list[str], list[dict[str, object]]]:
    payload = _read_yaml(root, "docs/feature-workset.yml")
    if payload.get("schema_version") != 1:
        raise FeatureValidationError(
            "docs/feature-workset.yml exige schema_version: 1"
        )
    raw_paths = payload.get("paths")
    if not isinstance(raw_paths, list) or not raw_paths:
        raise FeatureValidationError(
            "docs/feature-workset.yml exige paths como lista não vazia"
        )
    paths = [
        _safe_relative_path(
            raw,
            "docs/feature-workset.yml:paths",
            allow_glob=True,
        )
        for raw in raw_paths
    ]
    raw_dependencies = payload.get("receipt_dependencies", [])
    if not isinstance(raw_dependencies, list) or not all(
        isinstance(item, dict) for item in raw_dependencies
    ):
        raise FeatureValidationError(
            "docs/feature-workset.yml: receipt_dependencies deve ser lista"
        )
    dependencies: list[dict[str, object]] = []
    identifiers: set[str] = set()
    for index, item in enumerate(raw_dependencies):
        identifier = str(item.get("id") or "").strip().lower()
        label = f"docs/feature-workset.yml:receipt_dependencies[{index}]"
        if (
            not re.fullmatch(r"[a-z][a-z0-9_-]{1,39}", identifier)
            or identifier == "product"
            or identifier in identifiers
        ):
            raise FeatureValidationError(f"{label}: id inválido/duplicado")
        identifiers.add(identifier)
        mode = str(item.get("mode") or "").strip().lower()
        if mode not in {"automated", "physical"}:
            raise FeatureValidationError(
                f"{label}: mode deve ser automated|physical"
            )
        receipt = _safe_relative_path(item.get("receipt"), f"{label}:receipt")
        raw_patterns = item.get("depends_on")
        if not isinstance(raw_patterns, list) or not raw_patterns:
            raise FeatureValidationError(
                f"{label}: depends_on deve ser lista não vazia"
            )
        patterns = [
            _safe_relative_path(
                pattern,
                f"{label}:depends_on",
                allow_glob=True,
            )
            for pattern in raw_patterns
        ]
        dependencies.append(
            {
                "id": identifier,
                "mode": mode,
                "receipt": receipt,
                "depends_on": patterns,
            }
        )
    return paths, dependencies


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
    _workset_contract(root)
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
    if len(acceptance_ids) > MAX_ACCEPTANCE_CRITERIA:
        raise FeatureValidationError(
            "ciclo feature-fast excede o limite de "
            f"{MAX_ACCEPTANCE_CRITERIA} ACs ({len(acceptance_ids)} encontrados); "
            "divida a demanda em fatias verticais independentes de 4–6 ACs e "
            "mantenha somente a primeira fatia neste ciclo"
        )
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


def _git_visible_files(root: Path) -> list[str]:
    try:
        result = subprocess.run(
            [
                "git",
                "ls-files",
                "-c",
                "-o",
                "--exclude-standard",
                "-z",
                "--",
            ],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FeatureValidationError(
            f"não foi possível enumerar arquivos para o impacto: {exc}"
        ) from exc
    if result.returncode != 0:
        raise FeatureValidationError(
            "git ls-files falhou: "
            + (
                result.stderr.decode("utf-8", errors="replace").strip()
                or f"exit {result.returncode}"
            )
        )
    files: list[str] = []
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        relative = os.fsdecode(raw)
        candidate = root / relative
        if candidate.is_file():
            files.append(Path(relative).as_posix())
    return sorted(set(files))


def _dependency_matches(path: str, pattern: str) -> bool:
    normalized = Path(pattern).as_posix()
    if any(token in normalized for token in ("*", "?", "[")):
        return fnmatch.fnmatchcase(path, normalized)
    prefix = normalized.rstrip("/")
    return path == prefix or path.startswith(f"{prefix}/")


def _dependency_snapshot(
    root: Path,
    patterns: list[str],
    *,
    visible_files: list[str] | None = None,
) -> dict[str, object]:
    files = visible_files if visible_files is not None else _git_visible_files(root)
    matched = sorted(
        path
        for path in files
        if any(_dependency_matches(path, pattern) for pattern in patterns)
    )
    records = [
        {"path": path, "sha256": _sha256(root / path)}
        for path in matched
    ]
    return {
        "fingerprint": _canonical_digest(records),
        "file_count": len(records),
        "paths": matched,
    }


def _receipt_lanes(root: Path) -> list[dict[str, object]]:
    _, dependencies = _workset_contract(root)
    _, _, product_root = _load_baseline(root)
    product_pattern = "**" if product_root == "." else f"{product_root}/"
    return [
        {
            "id": "product",
            "mode": "automated",
            "receipt": RECEIPT_PATH,
            "depends_on": [product_pattern],
        },
        *dependencies,
    ]


def _product_visible_files(
    visible_files: list[str],
    product_root: str,
) -> list[str]:
    if product_root != ".":
        prefix = f"{product_root}/"
        return [path for path in visible_files if path.startswith(prefix)]
    return [
        path
        for path in visible_files
        if path != "CHANGELOG.md"
        and path.split("/", 1)[0] not in {".ft", ".git", "docs", "state"}
    ]


def prepare_receipt_baseline(root: Path) -> None:
    _feature_contract(root)
    visible = _git_visible_files(root)
    _, _, product_root = _load_baseline(root)
    lanes: list[dict[str, object]] = []
    for lane in _receipt_lanes(root):
        patterns = [str(item) for item in lane["depends_on"]]
        snapshot = _dependency_snapshot(
            root,
            patterns,
            visible_files=(
                _product_visible_files(visible, product_root)
                if lane["id"] == "product"
                else visible
            ),
        )
        lanes.append({**lane, "baseline": snapshot})
    _atomic_write_yaml(
        root / RECEIPT_BASELINE_PATH,
        {
            "schema_version": 1,
            "feature_sha256": _sha256(root / "docs/feature.md"),
            "workset_sha256": _sha256(root / "docs/feature-workset.yml"),
            "lanes": lanes,
        },
    )


def _receipt_baseline(root: Path) -> dict[str, object]:
    payload = _read_yaml(root, RECEIPT_BASELINE_PATH)
    if payload.get("schema_version") != 1:
        raise FeatureValidationError(
            f"{RECEIPT_BASELINE_PATH}: schema_version deve ser 1"
        )
    if payload.get("feature_sha256") != _sha256(root / "docs/feature.md"):
        raise FeatureValidationError(
            f"{RECEIPT_BASELINE_PATH}: contrato mudou após a baseline de receipts"
        )
    if payload.get("workset_sha256") != _sha256(
        root / "docs/feature-workset.yml"
    ):
        raise FeatureValidationError(
            f"{RECEIPT_BASELINE_PATH}: workset mudou após a baseline de receipts"
        )
    lanes = payload.get("lanes")
    if not isinstance(lanes, list) or not lanes or not all(
        isinstance(item, dict) for item in lanes
    ):
        raise FeatureValidationError(f"{RECEIPT_BASELINE_PATH}: lanes inválidas")
    current_contract = _receipt_lanes(root)
    baseline_contract = [
        {
            key: item.get(key)
            for key in ("id", "mode", "receipt", "depends_on")
        }
        for item in lanes
    ]
    if baseline_contract != current_contract:
        raise FeatureValidationError(
            f"{RECEIPT_BASELINE_PATH}: grafo de dependências foi alterado"
        )
    return payload


def _semantic_key(path: str) -> str | None:
    stem = Path(path).stem.casefold()
    stem = re.sub(r"^(?:test|spec)[_-]?", "", stem)
    stem = re.sub(r"(?:[_-]?(?:test|tests|spec|specs))$", "", stem)
    stem = re.sub(r"[^a-z0-9]+", "", stem)
    return stem if len(stem) >= 3 else None


def _related_product_paths(
    root: Path,
    changed: list[str],
    product_root: str,
    *,
    visible_files: list[str],
) -> list[str]:
    keys = {_semantic_key(path) for path in changed}
    keys.discard(None)
    if not keys:
        return []
    if product_root == ".":
        candidates = [
            path
            for path in visible_files
            if path.split("/", 1)[0] not in {".ft", "docs", "state"}
            and path != "CHANGELOG.md"
        ]
    else:
        prefix = f"{product_root}/"
        candidates = [path for path in visible_files if path.startswith(prefix)]
    return sorted(
        path
        for path in candidates
        if _semantic_key(path) in keys
    )


def _build_impact(root: Path) -> dict[str, object]:
    validate_implementation(root)
    baseline = _receipt_baseline(root)
    _, _, product_root = _load_baseline(root)
    changed = sorted(_changed_product_paths(root, product_root))
    workset, _ = _workset_contract(root)
    visible = _git_visible_files(root)
    related = _related_product_paths(
        root,
        changed,
        product_root,
        visible_files=visible,
    )
    impact_paths = sorted(set([*workset, *changed, *related]))
    impact_keys = sorted(
        key
        for key in {_semantic_key(path) for path in [*changed, *related]}
        if key is not None
    )

    lanes: list[dict[str, object]] = []
    raw_lanes = baseline["lanes"]
    assert isinstance(raw_lanes, list)
    for lane in raw_lanes:
        assert isinstance(lane, dict)
        patterns = [str(item) for item in lane["depends_on"]]
        current = _dependency_snapshot(
            root,
            patterns,
            visible_files=(
                _product_visible_files(visible, product_root)
                if lane["id"] == "product"
                else visible
            ),
        )
        previous = lane.get("baseline")
        if not isinstance(previous, dict):
            raise FeatureValidationError(
                f"{RECEIPT_BASELINE_PATH}: baseline ausente em {lane.get('id')}"
            )
        impacted = current["fingerprint"] != previous.get("fingerprint")
        lanes.append(
            {
                "id": lane["id"],
                "mode": lane["mode"],
                "receipt": lane["receipt"],
                "depends_on": patterns,
                "baseline_fingerprint": previous.get("fingerprint"),
                "current_fingerprint": current["fingerprint"],
                "file_count": current["file_count"],
                "impacted": impacted,
                "reuse_allowed": not impacted,
            }
        )

    identity = {
        "feature_sha256": _sha256(root / "docs/feature.md"),
        "plan_sha256": _sha256(root / "docs/feature-plan.md"),
        "workset_sha256": _sha256(root / "docs/feature-workset.yml"),
        "product_files": [
            {"path": path, "sha256": _sha256(root / path)}
            for path in changed
        ],
        "impact_paths": impact_paths,
        "impact_keys": impact_keys,
        "lanes": lanes,
    }
    return {
        "schema_version": 1,
        "pre_review_id": _canonical_digest(identity),
        "changed_product_paths": changed,
        "related_paths": related,
        "impact_paths": impact_paths,
        "impact_keys": impact_keys,
        "receipt_lanes": lanes,
    }


def prepare_impact(root: Path) -> None:
    _atomic_write_yaml(root / IMPACT_PATH, _build_impact(root))


def _current_impact(root: Path) -> dict[str, object]:
    stored = _read_yaml(root, IMPACT_PATH)
    current = _build_impact(root)
    if stored != current:
        raise FeatureValidationError(
            f"{IMPACT_PATH}: impacto obsoleto; regenere antes da revisão"
        )
    return stored


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


def validate_pre_review(root: Path) -> None:
    _, _, acceptance_ids = _feature_contract(root)
    impact = _current_impact(root)
    report = _read(root, PRE_REVIEW_PATH)
    route = _read_yaml(root, PRE_REVIEW_ROUTE_PATH)
    if route.get("schema_version") != 1:
        raise FeatureValidationError(
            f"{PRE_REVIEW_ROUTE_PATH}: schema_version deve ser 1"
        )
    if route.get("review_id") != impact.get("pre_review_id"):
        raise FeatureValidationError(
            f"{PRE_REVIEW_ROUTE_PATH}: review_id diverge do impacto atual"
        )
    review_route = route.get("review_route")
    verdict = route.get("verdict")
    if review_route not in {"approved", "implementation", "scope"}:
        raise FeatureValidationError(
            f"{PRE_REVIEW_ROUTE_PATH}: review_route inválida"
        )
    if verdict not in {"APPROVED", "REJECTED"}:
        raise FeatureValidationError(
            f"{PRE_REVIEW_ROUTE_PATH}: verdict inválido"
        )
    if (review_route == "approved") != (verdict == "APPROVED"):
        raise FeatureValidationError(
            f"{PRE_REVIEW_ROUTE_PATH}: approved exige APPROVED; "
            "demais rotas exigem REJECTED"
        )
    if not isinstance(route.get("summary"), str) or not str(
        route["summary"]
    ).strip():
        raise FeatureValidationError(
            f"{PRE_REVIEW_ROUTE_PATH}: summary obrigatório"
        )
    statuses = _review_ac_statuses(
        report,
        acceptance_ids,
        PRE_REVIEW_PATH,
    )
    if review_route == "approved":
        failed = [
            acceptance_id
            for acceptance_id, status in statuses.items()
            if status == "FAIL"
        ]
        if failed:
            raise FeatureValidationError(
                f"{PRE_REVIEW_PATH}: APPROVED exige todos os AC como PASS; "
                "FAIL em " + ", ".join(failed)
            )
    if review_route == "implementation" and not PRE_FINDING_RE.search(report):
        raise FeatureValidationError(
            f"{PRE_REVIEW_PATH}: rejeição de implementação exige achados P-01..."
        )


def _receipt_record_for_lane(
    root: Path,
    lane: dict[str, object],
) -> dict[str, object]:
    receipt = _safe_relative_path(
        lane.get("receipt"),
        f"{IMPACT_PATH}:{lane.get('id')}:receipt",
    )
    path = root / receipt
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
        raise FeatureValidationError(
            f"receipt obrigatório ausente/fora da raiz: {receipt}"
        ) from exc
    if not resolved.is_file():
        raise FeatureValidationError(f"receipt deve ser arquivo: {receipt}")

    patterns = [str(item) for item in lane.get("depends_on", [])]
    visible = _git_visible_files(root)
    _, _, product_root = _load_baseline(root)
    dependency_files = [
        path
        for path in (
            _product_visible_files(visible, product_root)
            if lane.get("id") == "product"
            else visible
        )
        if any(_dependency_matches(path, pattern) for pattern in patterns)
    ]
    newest_dependency = max(
        ((root / relative).stat().st_mtime for relative in dependency_files),
        default=0.0,
    )
    impacted = lane.get("impacted") is True
    if impacted and path.stat().st_mtime < newest_dependency:
        raise FeatureValidationError(
            f"receipt {receipt} está obsoleto para a lane {lane.get('id')}; "
            "as dependências mudaram e o ensaio deve ser reexecutado"
        )
    return {
        "id": lane.get("id"),
        "mode": lane.get("mode"),
        "receipt": receipt,
        "receipt_sha256": _sha256(path),
        "dependency_fingerprint": lane.get("current_fingerprint"),
        "decision": "rerun" if impacted else "reuse",
    }


def _build_review_context(root: Path) -> dict[str, object]:
    validate_pre_review(root)
    validate_evidence(root)
    impact = _current_impact(root)
    raw_lanes = impact.get("receipt_lanes")
    if not isinstance(raw_lanes, list) or not all(
        isinstance(item, dict) for item in raw_lanes
    ):
        raise FeatureValidationError(f"{IMPACT_PATH}: receipt_lanes inválidas")
    receipts = [
        _receipt_record_for_lane(root, lane)
        for lane in raw_lanes
    ]
    identity = {
        "pre_review_id": impact.get("pre_review_id"),
        "feature_sha256": _sha256(root / "docs/feature.md"),
        "plan_sha256": _sha256(root / "docs/feature-plan.md"),
        "evidence_sha256": _sha256(root / EVIDENCE_PATH),
        "implementation_report_sha256": _sha256(
            root / "docs/implementation-report.md"
        ),
        "receipts": receipts,
    }
    product = next(
        (item for item in receipts if item.get("id") == "product"),
        None,
    )
    if not isinstance(product, dict):
        raise FeatureValidationError("grafo de receipts não contém lane product")
    try:
        product_payload = json.loads(_read(root, RECEIPT_PATH))
    except json.JSONDecodeError as exc:
        raise FeatureValidationError(f"{RECEIPT_PATH}: JSON inválido: {exc}") from exc
    product_fingerprint = (
        product_payload.get("fingerprint")
        if isinstance(product_payload, dict)
        else None
    )
    if not isinstance(product_fingerprint, str) or not product_fingerprint:
        raise FeatureValidationError(
            f"{RECEIPT_PATH}: fingerprint obrigatório"
        )
    return {
        "schema_version": 1,
        "review_id": _canonical_digest(identity),
        "pre_review_id": impact.get("pre_review_id"),
        "receipt_fingerprint": product_fingerprint,
        "impact_paths": impact.get("impact_paths"),
        "impact_keys": impact.get("impact_keys"),
        "receipts": receipts,
    }


def prepare_review_context(root: Path) -> None:
    _atomic_write_yaml(root / REVIEW_CONTEXT_PATH, _build_review_context(root))


def _current_review_context(root: Path) -> dict[str, object]:
    stored = _read_yaml(root, REVIEW_CONTEXT_PATH)
    current = _build_review_context(root)
    if stored != current:
        raise FeatureValidationError(
            f"{REVIEW_CONTEXT_PATH}: contexto/review_id obsoleto"
        )
    return stored


def validate_review(root: Path) -> None:
    _, _, acceptance_ids = _feature_contract(root)
    context = _current_review_context(root)
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
    if route.get("review_id") != context.get("review_id"):
        raise FeatureValidationError(
            f"{REVIEW_ROUTE_PATH}: review_id diverge do contexto atual"
        )
    if route.get("receipt_fingerprint") != context.get(
        "receipt_fingerprint"
    ):
        raise FeatureValidationError(
            f"{REVIEW_ROUTE_PATH}: receipt_fingerprint diverge do contexto atual"
        )
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
    elif review_route == "implementation" and not _review_findings(report):
        raise FeatureValidationError(
            "docs/feature-review.md: REJECTED de implementação exige achados "
            "numerados F-01, F-02 etc."
        )


def _git_stdout(root: Path, args: list[str], *, binary: bool = False) -> str | bytes:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=not binary,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FeatureValidationError(f"git {' '.join(args)} falhou: {exc}") from exc
    if result.returncode != 0:
        stderr = (
            result.stderr.decode("utf-8", errors="replace")
            if binary and isinstance(result.stderr, bytes)
            else str(result.stderr)
        )
        raise FeatureValidationError(
            f"git {' '.join(args)} falhou: "
            + (stderr.strip() or f"exit {result.returncode}")
        )
    return result.stdout


def _git_head(root: Path) -> str:
    value = str(_git_stdout(root, ["rev-parse", "HEAD"])).strip()
    if not re.fullmatch(r"[0-9a-f]{40,64}", value):
        raise FeatureValidationError("HEAD Git ausente ou inválido para correção focal")
    return value


def _git_changed_paths_since(root: Path, base_commit: str) -> list[str]:
    if not re.fullmatch(r"[0-9a-f]{7,64}", base_commit):
        raise FeatureValidationError(f"{FIX_BASELINE_PATH}: base_commit inválido")
    _git_stdout(root, ["cat-file", "-e", f"{base_commit}^{{commit}}"])
    tracked = _git_stdout(
        root,
        ["diff", "--name-only", "-z", base_commit, "--"],
        binary=True,
    )
    untracked = _git_stdout(
        root,
        ["ls-files", "--others", "--exclude-standard", "-z"],
        binary=True,
    )
    values: list[str] = []
    for raw in (tracked, untracked):
        assert isinstance(raw, bytes)
        for value in raw.decode("utf-8", errors="replace").split("\0"):
            relative = value.strip()
            candidate = Path(relative)
            if (
                not relative
                or candidate.is_absolute()
                or ".." in candidate.parts
                or not candidate.parts
                or relative in values
            ):
                continue
            values.append(candidate.as_posix())
    return sorted(values)


def _review_findings(report: str) -> list[str]:
    findings: list[str] = []
    for match in FINDING_RE.finditer(report):
        identifier = match.group(0).upper()
        if identifier not in findings:
            findings.append(identifier)
    return findings


def _finding_statuses(
    report: str,
    finding_ids: list[str],
) -> dict[str, str]:
    expected = set(finding_ids)
    found: dict[str, set[str]] = {
        finding_id: set() for finding_id in finding_ids
    }
    for line in report.splitlines():
        if "|" in line:
            cells = [cell.strip() for cell in line.split("|")]
            for index, cell in enumerate(cells[:-1]):
                ids = {
                    match.group(0).upper()
                    for match in FINDING_RE.finditer(cell)
                    if match.group(0).upper() in expected
                }
                if not ids:
                    continue
                status = _exact_review_status(cells[index + 1])
                if status:
                    for finding_id in ids:
                        found[finding_id].add(status)
            continue
        for finding_id in finding_ids:
            match = re.search(
                rf"\b{re.escape(finding_id)}\b\s*(?::|[-–—])\s*"
                r"(?:\*\*|__|`)?(PASS|FAIL)(?:\*\*|__|`)?\b",
                line,
            )
            if match:
                found[finding_id].add(match.group(1))

    missing = [finding_id for finding_id, statuses in found.items() if not statuses]
    ambiguous = [
        finding_id for finding_id, statuses in found.items()
        if len(statuses) > 1
    ]
    if missing or ambiguous:
        details: list[str] = []
        if missing:
            details.append("sem status PASS/FAIL: " + ", ".join(missing))
        if ambiguous:
            details.append("status ambíguo: " + ", ".join(ambiguous))
        raise FeatureValidationError(
            f"{FIX_REVIEW_PATH}: " + "; ".join(details)
        )
    return {
        finding_id: next(iter(statuses))
        for finding_id, statuses in found.items()
    }


def _fix_baseline(root: Path) -> dict[str, object]:
    payload = _read_yaml(root, FIX_BASELINE_PATH)
    if payload.get("schema_version") != 1:
        raise FeatureValidationError(
            f"{FIX_BASELINE_PATH}: schema_version deve ser 1"
        )
    base_commit = payload.get("base_commit")
    if not isinstance(base_commit, str) or not re.fullmatch(
        r"[0-9a-f]{40,64}", base_commit
    ):
        raise FeatureValidationError(f"{FIX_BASELINE_PATH}: base_commit inválido")
    findings = payload.get("findings")
    if (
        not isinstance(findings, list)
        or not findings
        or not all(
            isinstance(item, str) and FINDING_RE.fullmatch(item)
            for item in findings
        )
        or len(findings) != len(set(findings))
    ):
        raise FeatureValidationError(f"{FIX_BASELINE_PATH}: findings inválidos")
    workset = payload.get("workset")
    if (
        not isinstance(workset, list)
        or not workset
        or not all(isinstance(item, str) and item for item in workset)
    ):
        raise FeatureValidationError(f"{FIX_BASELINE_PATH}: workset inválido")
    impact_paths = payload.get("impact_paths")
    if (
        not isinstance(impact_paths, list)
        or not impact_paths
        or not all(isinstance(item, str) and item for item in impact_paths)
    ):
        raise FeatureValidationError(
            f"{FIX_BASELINE_PATH}: impact_paths inválidos"
        )
    impact_keys = payload.get("impact_keys")
    if (
        not isinstance(impact_keys, list)
        or not all(
            isinstance(item, str) and re.fullmatch(r"[a-z0-9]{3,}", item)
            for item in impact_keys
        )
    ):
        raise FeatureValidationError(
            f"{FIX_BASELINE_PATH}: impact_keys inválidos"
        )
    source_review_id = payload.get("source_review_id")
    if not isinstance(source_review_id, str) or not re.fullmatch(
        r"sha256:[0-9a-f]{64}",
        source_review_id,
    ):
        raise FeatureValidationError(
            f"{FIX_BASELINE_PATH}: source_review_id inválido"
        )
    contract_sha256 = payload.get("contract_sha256")
    if not isinstance(contract_sha256, dict):
        raise FeatureValidationError(
            f"{FIX_BASELINE_PATH}: contract_sha256 inválido"
        )
    receipt_fingerprint = payload.get("receipt_fingerprint")
    if not isinstance(receipt_fingerprint, str) or not re.fullmatch(
        r"sha256:[0-9a-f]{64}",
        receipt_fingerprint,
    ):
        raise FeatureValidationError(
            f"{FIX_BASELINE_PATH}: receipt_fingerprint inválido"
        )
    return payload


def prepare_fix(root: Path) -> None:
    validate_review(root)
    review_context = _current_review_context(root)
    impact = _current_impact(root)
    review_route = _read_yaml(root, REVIEW_ROUTE_PATH)
    if (
        review_route.get("verdict") != "REJECTED"
        or review_route.get("review_route") != "implementation"
    ):
        raise FeatureValidationError(
            "prepare-fix exige review REJECTED com review_route=implementation"
        )
    report = _read(root, "docs/feature-review.md")
    findings = _review_findings(report)
    if not findings:
        raise FeatureValidationError(
            "docs/feature-review.md: review de implementação rejeitada deve "
            "numerar achados F-01, F-02 etc."
        )
    workset, _ = _workset_contract(root)
    impact_paths = impact.get("impact_paths")
    impact_keys = impact.get("impact_keys")
    if not isinstance(impact_paths, list) or not isinstance(impact_keys, list):
        raise FeatureValidationError(f"{IMPACT_PATH}: impacto inválido")
    source_sha256 = {
        relative: _sha256(root / relative)
        for relative in ("docs/feature-review.md", REVIEW_ROUTE_PATH)
    }
    contract_sha256 = {
        relative: _sha256(root / relative)
        for relative in FIX_CONTRACT_PATHS
    }
    try:
        receipt = json.loads(_read(root, RECEIPT_PATH))
    except json.JSONDecodeError as exc:
        raise FeatureValidationError(
            f"{RECEIPT_PATH}: JSON inválido: {exc}"
        ) from exc
    receipt_fingerprint = (
        receipt.get("fingerprint") if isinstance(receipt, dict) else None
    )
    if not isinstance(receipt_fingerprint, str) or not re.fullmatch(
        r"sha256:[0-9a-f]{64}",
        receipt_fingerprint,
    ):
        raise FeatureValidationError(
            f"{RECEIPT_PATH}: fingerprint obrigatório antes do fix"
        )
    _atomic_write_yaml(
        root / FIX_BASELINE_PATH,
        {
            "schema_version": 1,
            "base_commit": _git_head(root),
            "source_review": REVIEW_ROUTE_PATH,
            "source_review_id": review_context["review_id"],
            "source_sha256": source_sha256,
            "findings": findings,
            "workset": workset,
            "impact_paths": sorted(
                {str(item).strip() for item in impact_paths if str(item).strip()}
            ),
            "impact_keys": sorted(
                {str(item).strip() for item in impact_keys if str(item).strip()}
            ),
            "contract_sha256": contract_sha256,
            "receipt_fingerprint": receipt_fingerprint,
        },
    )


def _product_paths_from_delta(
    root: Path,
    base_commit: str,
    product_root: str,
) -> list[str]:
    changed = _git_changed_paths_since(root, base_commit)
    if product_root == ".":
        return [
            path for path in changed
            if path != "CHANGELOG.md"
            and path.split("/", 1)[0] not in {
                ".ft", ".git", "docs", "state",
            }
        ]
    prefix = f"{product_root}/"
    return [path for path in changed if path.startswith(prefix)]


def validate_fix_implementation(root: Path) -> None:
    _feature_contract(root)
    baseline = _fix_baseline(root)
    _, _, product_root = _load_baseline(root)
    changed = _product_paths_from_delta(
        root,
        str(baseline["base_commit"]),
        product_root,
    )
    if not changed:
        raise FeatureValidationError(
            "correção focal não alterou nenhum arquivo de produto desde a âncora"
        )


def validate_fix_receipts(root: Path) -> None:
    """Require only the receipt lanes whose declared dependencies changed.

    The impact remains anchored to the pre-implementation dependency baseline.
    A previously refreshed physical receipt therefore stays valid across a
    focal fix unless that fix makes one of its dependency files newer.
    """
    impact = _build_impact(root)
    raw_lanes = impact.get("receipt_lanes")
    if not isinstance(raw_lanes, list) or not all(
        isinstance(item, dict) for item in raw_lanes
    ):
        raise FeatureValidationError(f"{IMPACT_PATH}: receipt_lanes inválidas")
    for lane in raw_lanes:
        _receipt_record_for_lane(root, lane)


def _path_covered_by_workset(path: str, workset: list[str]) -> bool:
    for raw in workset:
        normalized = Path(raw).as_posix().rstrip("/")
        if normalized in {"", "."}:
            return True
        if any(token in normalized for token in ("*", "?", "[")):
            if fnmatch.fnmatchcase(path, normalized):
                return True
            continue
        if path == normalized or path.startswith(f"{normalized}/"):
            return True
    return False


def validate_fix_review(root: Path) -> None:
    _feature_contract(root)
    baseline = _fix_baseline(root)
    base_commit = str(baseline["base_commit"])
    _git_stdout(root, ["cat-file", "-e", f"{base_commit}^{{commit}}"])

    source_sha256 = baseline.get("source_sha256")
    assert isinstance(source_sha256, dict)
    changed_sources = [
        relative
        for relative, expected in source_sha256.items()
        if not isinstance(relative, str)
        or _sha256(root / relative) != expected
    ]
    if changed_sources:
        raise FeatureValidationError(
            f"{FIX_BASELINE_PATH}: revisão fonte foi alterada: "
            + ", ".join(str(item) for item in changed_sources)
        )

    route = _read_yaml(root, FIX_REVIEW_ROUTE_PATH)
    if route.get("schema_version") != 1:
        raise FeatureValidationError(
            f"{FIX_REVIEW_ROUTE_PATH}: schema_version deve ser 1"
        )
    review_route = route.get("review_route")
    verdict = route.get("verdict")
    valid_routes = {
        "approved", "implementation", "evidence", "full_review", "scope"
    }
    if review_route not in valid_routes:
        raise FeatureValidationError(
            f"{FIX_REVIEW_ROUTE_PATH}: review_route inválida"
        )
    if verdict not in {"APPROVED", "REJECTED"}:
        raise FeatureValidationError(f"{FIX_REVIEW_ROUTE_PATH}: verdict inválido")
    if (review_route == "approved") != (verdict == "APPROVED"):
        raise FeatureValidationError(
            f"{FIX_REVIEW_ROUTE_PATH}: approved exige APPROVED; "
            "demais rotas exigem REJECTED"
        )
    if not isinstance(route.get("summary"), str) or not str(
        route["summary"]
    ).strip():
        raise FeatureValidationError(
            f"{FIX_REVIEW_ROUTE_PATH}: summary obrigatório"
        )
    if route.get("source_review") != REVIEW_ROUTE_PATH:
        raise FeatureValidationError(
            f"{FIX_REVIEW_ROUTE_PATH}: source_review deve ser {REVIEW_ROUTE_PATH}"
        )
    if route.get("source_review_id") != baseline.get("source_review_id"):
        raise FeatureValidationError(
            f"{FIX_REVIEW_ROUTE_PATH}: source_review_id diverge da âncora"
        )
    if route.get("base_commit") != base_commit:
        raise FeatureValidationError(
            f"{FIX_REVIEW_ROUTE_PATH}: base_commit diverge da âncora"
        )

    if route.get("receipt_fingerprint") != baseline.get("receipt_fingerprint"):
        raise FeatureValidationError(
            f"{FIX_REVIEW_ROUTE_PATH}: receipt_fingerprint diverge do receipt ancorado"
        )

    finding_ids = [str(item) for item in baseline["findings"]]
    findings = route.get("findings")
    if not isinstance(findings, list) or not all(
        isinstance(item, dict) for item in findings
    ):
        raise FeatureValidationError(
            f"{FIX_REVIEW_ROUTE_PATH}: findings deve ser lista"
        )
    indexed = {
        str(item.get("id") or "").upper(): item for item in findings
    }
    if set(indexed) != set(finding_ids) or len(indexed) != len(findings):
        raise FeatureValidationError(
            f"{FIX_REVIEW_ROUTE_PATH}: findings deve conter exatamente "
            + ", ".join(finding_ids)
        )
    for finding_id in finding_ids:
        item = indexed[finding_id]
        if item.get("status") not in {"PASS", "FAIL"}:
            raise FeatureValidationError(
                f"{FIX_REVIEW_ROUTE_PATH}: {finding_id} sem status PASS/FAIL"
            )
        if not isinstance(item.get("evidence"), str) or not str(
            item["evidence"]
        ).strip():
            raise FeatureValidationError(
                f"{FIX_REVIEW_ROUTE_PATH}: {finding_id} sem evidence"
            )

    report_statuses = _finding_statuses(
        _read(root, FIX_REVIEW_PATH),
        finding_ids,
    )
    mismatched = [
        finding_id for finding_id in finding_ids
        if indexed[finding_id]["status"] != report_statuses[finding_id]
    ]
    if mismatched:
        raise FeatureValidationError(
            f"{FIX_REVIEW_ROUTE_PATH}: status diverge do Markdown em "
            + ", ".join(mismatched)
        )
    if review_route == "approved":
        failed = [
            finding_id for finding_id in finding_ids
            if indexed[finding_id]["status"] == "FAIL"
        ]
        if failed:
            raise FeatureValidationError(
                f"{FIX_REVIEW_ROUTE_PATH}: approved exige todos os F-* PASS; "
                "FAIL em " + ", ".join(failed)
            )

    contract_sha256 = baseline["contract_sha256"]
    assert isinstance(contract_sha256, dict)
    changed_contracts = [
        relative
        for relative, expected in contract_sha256.items()
        if not isinstance(relative, str)
        or _sha256(root / relative) != expected
    ]
    _, _, product_root = _load_baseline(root)
    changed_product = _product_paths_from_delta(root, base_commit, product_root)
    workset = [str(item) for item in baseline["impact_paths"]]
    impact_keys = {str(item) for item in baseline["impact_keys"]}
    escaped_workset = [
        path for path in changed_product
        if not _path_covered_by_workset(path, workset)
        and _semantic_key(path) not in impact_keys
    ]
    if review_route == "approved" and (changed_contracts or escaped_workset):
        details: list[str] = []
        if changed_contracts:
            details.append("contratos alterados: " + ", ".join(changed_contracts))
        if escaped_workset:
            details.append("fora do workset: " + ", ".join(escaped_workset))
        raise FeatureValidationError(
            f"{FIX_REVIEW_ROUTE_PATH}: correção expandida exige full_review/scope; "
            + "; ".join(details)
        )
    if changed_contracts and review_route not in {"scope", "full_review"}:
        raise FeatureValidationError(
            f"{FIX_REVIEW_ROUTE_PATH}: contrato alterado exige scope/full_review"
        )
    if escaped_workset and review_route not in {"full_review", "scope"}:
        raise FeatureValidationError(
            f"{FIX_REVIEW_ROUTE_PATH}: delta fora do workset exige full_review/scope"
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
    "prepare-receipt-baseline": prepare_receipt_baseline,
    "implementation": validate_implementation,
    "prepare-impact": prepare_impact,
    "pre-review": validate_pre_review,
    "evidence": validate_evidence,
    "prepare-review": prepare_review_context,
    "review": validate_review,
    "prepare-fix": prepare_fix,
    "fix-implementation": validate_fix_implementation,
    "fix-receipts": validate_fix_receipts,
    "fix-review": validate_fix_review,
    "proposal": validate_proposal,
    "apply-reconcile": apply_reconciliation,
    "reconcile": validate_reconcile,
}
READ_ONLY_VALIDATOR_MODES = tuple(
    mode
    for mode in VALIDATORS
    if mode
    not in {
        "reserve",
        "prepare-receipt-baseline",
        "prepare-impact",
        "prepare-review",
        "prepare-fix",
        "apply-reconcile",
    }
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
