#!/usr/bin/env python3
"""Validate that a builder candidate is operational, not a visual/demo shell."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

import yaml


class ValidationError(AssertionError):
    pass


TEXT_SUFFIXES = {
    ".css",
    ".html",
    ".js",
    ".jsx",
    ".mjs",
    ".py",
    ".rs",
    ".swift",
    ".ts",
    ".tsx",
    ".vue",
    ".kt",
    ".kts",
    ".java",
}
EXCLUDED_PARTS = {
    ".git",
    ".ft",
    "node_modules",
    "target",
    "dist",
    "build",
    "coverage",
    "tests",
    "test",
    "fixtures",
    "__mocks__",
    "test-results",
}
RUNTIME_PROHIBITIONS = (
    ("default synthetic seed", re.compile(r"\b_default_seed\s*\(")),
    ("visual-state materializer", re.compile(r"\bmaterializeVisualState\b")),
    ("visual-state production module", re.compile(r"visual[-_]state[-_]data", re.I)),
    (
        "mock/fake/demo runtime data",
        re.compile(r"\b(?:mock|fake|demo)[_-]?(?:data|seed|provider|state)\b", re.I),
    ),
)
FONT_SIZE = re.compile(r"font-size\s*:\s*([0-9]+(?:\.[0-9]+)?)px", re.I)
FONT_SHORTHAND = re.compile(r"\bfont\s*:\s*[^;\n]*?\b([0-9]+(?:\.[0-9]+)?)px", re.I)
INLINE_FONT_SIZE = re.compile(
    r"\bfontSize\s*(?::|=)\s*(?:\{\s*)?[\"']?([0-9]+(?:\.[0-9]+)?)", re.I
)
PLACEHOLDERS = {"", "-", "—", "n/a", "na", "none", "null", "tbd", "todo"}


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(f"{field} must be a mapping")
    return value


def _list(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValidationError(f"{field} must be a list")
    return value


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or value.strip().casefold() in PLACEHOLDERS:
        raise ValidationError(f"{field} must be a non-placeholder string")
    return value.strip()


def _safe_path(root: Path, raw: Any, field: str, *, file: bool | None = None) -> Path:
    value = _text(raw, field)
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts or "\\" in value:
        raise ValidationError(f"{field} must remain inside the project")
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValidationError(f"{field} escapes the project") from exc
    if file is True and (not candidate.is_file() or candidate.is_symlink()):
        raise ValidationError(f"{field} evidence file does not exist: {value}")
    if file is False and (not candidate.exists() or candidate.is_symlink()):
        raise ValidationError(f"{field} production path does not exist: {value}")
    return candidate


def _backlog_scope(path: Path) -> set[str]:
    rows: set[str] = set()
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    header: dict[str, int] | None = None
    for line in lines:
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        normalized = [cell.casefold() for cell in cells]
        if "id" in normalized and "prioridade" in normalized and "status" in normalized:
            header = {
                name: normalized.index(name) for name in ("id", "prioridade", "status")
            }
            continue
        if header is None or not cells or set("".join(cells)) <= {"-", ":"}:
            continue
        if max(header.values()) >= len(cells):
            continue
        ref = cells[header["id"]]
        priority = cells[header["prioridade"]].upper()
        if re.fullmatch(r"PB-\d+[A-Z]?", ref) and priority in {"P0", "P1"}:
            rows.add(ref)
    if not rows:
        raise ValidationError("backlog contains no P0/P1 scope")
    return rows


def _source_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path] if path.suffix.casefold() in TEXT_SUFFIXES else []
    files: list[Path] = []
    for candidate in path.rglob("*"):
        if not candidate.is_file() or candidate.is_symlink():
            continue
        relative_parts = {part.casefold() for part in candidate.relative_to(path).parts}
        if relative_parts & EXCLUDED_PARTS:
            continue
        name = candidate.name.casefold()
        if (
            ".test." in name
            or ".spec." in name
            or candidate.suffix.casefold() not in TEXT_SUFFIXES
        ):
            continue
        files.append(candidate)
    return files


def _scan_runtime(root: Path, paths: list[Any]) -> list[str]:
    hits: list[str] = []
    for index, raw in enumerate(paths):
        production = _safe_path(
            root, raw, f"scan.production_paths[{index}]", file=False
        )
        for source in _source_files(production):
            try:
                text = source.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                raise ValidationError(
                    f"cannot read production source: {source}"
                ) from exc
            relative = source.relative_to(root).as_posix()
            for label, pattern in RUNTIME_PROHIBITIONS:
                if pattern.search(text) or pattern.search(source.name):
                    hits.append(f"{relative}: {label}")
            for line_number, line in enumerate(text.splitlines(), start=1):
                for match in INLINE_FONT_SIZE.finditer(line):
                    size = float(match.group(1))
                    if 0 < size < 12:
                        hits.append(
                            f"{relative}:{line_number}: readable font below 12px ({size:g}px)"
                        )
            if source.suffix.casefold() == ".css":
                for line_number, line in enumerate(text.splitlines(), start=1):
                    for match in (
                        *FONT_SIZE.finditer(line),
                        *FONT_SHORTHAND.finditer(line),
                    ):
                        size = float(match.group(1))
                        if 0 < size < 12:
                            hits.append(
                                f"{relative}:{line_number}: readable font below 12px ({size:g}px)"
                            )
    return sorted(set(hits))


def validate(report_path: Path, project_contract_path: Path, *, root: Path) -> None:
    report = _mapping(
        yaml.safe_load(report_path.read_text(encoding="utf-8")) or {}, "report"
    )
    if report.get("schema_version") != 1:
        raise ValidationError("schema_version must be 1")
    if report.get("evidence_grade") != "OPERATIONAL_REAL_DATA":
        raise ValidationError("evidence_grade must be OPERATIONAL_REAL_DATA")
    _text(report.get("candidate_ref"), "candidate_ref")
    _text(report.get("production_entrypoint"), "production_entrypoint")

    runtime = _mapping(report.get("runtime"), "runtime")
    expected_runtime = {
        "mode": "production",
        "clean_start": True,
        "restart_verified": True,
        "demo_seed_enabled": False,
        "synthetic_runtime_records": False,
        "mock_providers_enabled": False,
        "persistence": "durable",
    }
    for key, expected in expected_runtime.items():
        if runtime.get(key) != expected:
            raise ValidationError(f"runtime.{key} must be {expected!r}")

    ui = _mapping(report.get("ui", {}), "ui")
    if ui.get("applicable") is True:
        if (
            not isinstance(ui.get("minimum_observed_font_px"), (int, float))
            or ui["minimum_observed_font_px"] < 12
        ):
            raise ValidationError("ui.minimum_observed_font_px must be at least 12")
        if ui.get("zoom_percent") != 100:
            raise ValidationError("ui.zoom_percent must be 100")
        for index, evidence in enumerate(_list(ui.get("evidence"), "ui.evidence")):
            _safe_path(root, evidence, f"ui.evidence[{index}]", file=True)

    scan = _mapping(report.get("scan"), "scan")
    production_paths = _list(scan.get("production_paths"), "scan.production_paths")
    if not production_paths:
        raise ValidationError("scan.production_paths must not be empty")
    declared_hits = _list(scan.get("prohibited_hits"), "scan.prohibited_hits")
    actual_hits = _scan_runtime(root, production_paths)
    if declared_hits or actual_hits:
        raise ValidationError(
            "prohibited production runtime found: "
            + "; ".join(actual_hits or map(str, declared_hits))
        )

    backlog_path = _safe_path(root, report.get("scope_path"), "scope_path", file=True)
    scope = _backlog_scope(backlog_path)
    results = _list(report.get("results"), "results")
    indexed: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(results):
        result = _mapping(raw, f"results[{index}]")
        ref = _text(result.get("ref"), f"results[{index}].ref")
        if ref in indexed:
            raise ValidationError(f"duplicate result for {ref}")
        if result.get("result") != "PASS":
            raise ValidationError(f"{ref} is not PASS")
        evidence = _list(result.get("evidence"), f"results[{index}].evidence")
        if not evidence:
            raise ValidationError(f"{ref} has no operational evidence")
        for evidence_index, path in enumerate(evidence):
            _safe_path(
                root, path, f"results[{index}].evidence[{evidence_index}]", file=True
            )
        journey_ids = _list(result.get("journeys"), f"results[{index}].journeys")
        if not journey_ids:
            raise ValidationError(f"{ref} is not linked to an operational journey")
        indexed[ref] = result
    if set(indexed) != scope:
        raise ValidationError(
            f"P0/P1 coverage mismatch: missing={sorted(scope - set(indexed))}, extra={sorted(set(indexed) - scope)}"
        )

    journeys = _list(report.get("journeys"), "journeys")
    journey_index: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(journeys):
        journey = _mapping(raw, f"journeys[{index}]")
        journey_id = _text(journey.get("id"), f"journeys[{index}].id")
        if journey_id in journey_index:
            raise ValidationError(f"duplicate journey {journey_id}")
        if journey.get("result") != "PASS":
            raise ValidationError(f"journey {journey_id} is not PASS")
        if journey.get("navigation_mode") not in {
            "production_ui",
            "public_api",
            "public_cli",
            "native_ui",
        }:
            raise ValidationError(
                f"journey {journey_id} does not use a production public surface"
            )
        if journey.get("data_origin") != "created_via_public_interface":
            raise ValidationError(f"journey {journey_id} uses non-operational data")
        canary = _mapping(journey.get("canary"), f"journeys[{index}].canary")
        created = _text(
            canary.get("created_value"), f"journeys[{index}].canary.created_value"
        )
        persisted = _text(
            canary.get("persisted_value"), f"journeys[{index}].canary.persisted_value"
        )
        observed = _text(
            canary.get("observed_value"), f"journeys[{index}].canary.observed_value"
        )
        if not created == persisted == observed:
            raise ValidationError(
                f"journey {journey_id} canary differs across write/persistence/presentation"
            )
        if journey.get("persistence_restart_verified") is not True:
            raise ValidationError(
                f"journey {journey_id} did not survive a real restart"
            )
        evidence = _list(journey.get("evidence"), f"journeys[{index}].evidence")
        if not evidence:
            raise ValidationError(f"journey {journey_id} has no evidence")
        for evidence_index, path in enumerate(evidence):
            _safe_path(
                root, path, f"journeys[{index}].evidence[{evidence_index}]", file=True
            )
        journey_index[journey_id] = journey
    referenced_journeys = {
        str(item) for result in indexed.values() for item in result["journeys"]
    }
    if not referenced_journeys or not referenced_journeys.issubset(journey_index):
        raise ValidationError("results reference missing operational journeys")

    findings = _list(report.get("findings"), "findings")
    if findings:
        raise ValidationError("operational findings must be empty")
    if report.get("verdict") != "APPROVED":
        raise ValidationError("verdict must be APPROVED")

    contract = _mapping(
        yaml.safe_load(project_contract_path.read_text(encoding="utf-8")) or {},
        "project contract",
    )
    dod = _mapping(contract.get("definition_of_done"), "definition_of_done")
    gates = _list(dod.get("required_gates"), "definition_of_done.required_gates")
    expected_gate = {
        "id": "operational-real-data",
        "path": report_path.relative_to(root).as_posix(),
        "field": "verdict",
        "equals": "APPROVED",
    }
    if expected_gate not in gates:
        raise ValidationError(
            "project-close does not require the operational-real-data gate"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", nargs="?", default="docs/operational-readiness.yml")
    parser.add_argument("project_contract", nargs="?", default=".ft/project.yml")
    args = parser.parse_args()
    root = Path.cwd().resolve()
    try:
        validate(
            (root / args.report).resolve(),
            (root / args.project_contract).resolve(),
            root=root,
        )
    except (OSError, UnicodeError, yaml.YAMLError, ValidationError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
