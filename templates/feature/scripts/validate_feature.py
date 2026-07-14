#!/usr/bin/env python3
"""Deterministic validators for the local ``feature`` process.

The script deliberately validates artifacts only. Test/build execution remains
owned by ``process.yml`` so command output is visible in the engine gate log.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re
import subprocess
import sys
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
    _read(root, "docs/feature-request.md")
    _read(root, "docs/PRD.md")
    _read(root, "docs/PROJECT_BACKLOG.md")
    _read(root, "docs/FEATURES.md")
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
    paths: list[str] = []
    for line in result.stdout.splitlines():
        raw = line[3:].strip()
        if " -> " in raw:
            raw = raw.split(" -> ", 1)[1]
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
    _, _, acceptance_ids = _feature_contract(root)
    report = _read(root, "docs/implementation-report.md")
    _assert_ac_pass(report, acceptance_ids, "docs/implementation-report.md")
    _, _, product_root = _load_baseline(root)
    changed = _changed_product_paths(root, product_root)
    if not changed:
        raise FeatureValidationError(
            f"implementação não alterou nenhum arquivo em {product_root}/"
        )
    if not any("test" in path.lower() or "spec" in path.lower() for path in changed):
        raise FeatureValidationError("implementação não alterou nenhum arquivo de teste")


def validate_review(root: Path) -> None:
    _, _, acceptance_ids = _feature_contract(root)
    report = _read(root, "docs/feature-review.md")
    results = re.findall(r"(?m)^\s*Resultado\s*:\s*(APPROVED|REJECTED)\s*$", report)
    if len(results) != 1:
        raise FeatureValidationError(
            "docs/feature-review.md exige exatamente uma linha "
            "`Resultado: APPROVED` ou `Resultado: REJECTED`"
        )
    statuses = _review_ac_statuses(
        report,
        acceptance_ids,
        "docs/feature-review.md",
    )
    if results[0] == "APPROVED":
        failed = [acceptance_id for acceptance_id, status in statuses.items() if status == "FAIL"]
        if failed:
            raise FeatureValidationError(
                "docs/feature-review.md: Resultado APPROVED exige todos os AC como PASS; "
                "FAIL em " + ", ".join(failed)
            )


def validate_reconcile(root: Path) -> None:
    metadata, _, acceptance_ids = _feature_contract(root)
    backlog = metadata["backlog_item"]
    target = metadata["target_feature"]
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
        if len(new_feature_ids) != 1:
            raise FeatureValidationError(
                "FEATURES: feature new exige exatamente um novo ID FEAT-*; "
                f"encontrados {len(new_feature_ids)}"
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
        if not FEAT_RE.fullmatch(final_id):
            raise FeatureValidationError(f"ID final de feature inválido: {final_id}")
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
    "implementation": validate_implementation,
    "review": validate_review,
    "reconcile": validate_reconcile,
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="Raiz do projeto/worktree")
    parser.add_argument("mode", choices=[*VALIDATORS, "all"])
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = Path(args.root).resolve()
    modes = list(VALIDATORS) if args.mode == "all" else [args.mode]
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
