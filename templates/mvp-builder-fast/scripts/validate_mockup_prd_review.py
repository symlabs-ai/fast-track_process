#!/usr/bin/env python3
"""Validate the deterministic PRD-to-mockup coherence review contract."""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any

import yaml


RESULTS = {"COHERENT", "COHERENT_WITH_RESERVATION", "INCOHERENT"}
VERDICTS = {"APPROVED", "REJECTED"}


class ValidationError(ValueError):
    """Raised when a review artifact violates its contract."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def _read_text(path: Path, label: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValidationError(f"cannot read {label} at {path}: {exc}") from exc


def _load_yaml(path: Path, label: str) -> dict[str, Any]:
    try:
        value = yaml.safe_load(_read_text(path, label))
    except yaml.YAMLError as exc:
        raise ValidationError(f"invalid YAML in {label} at {path}: {exc}") from exc
    _require(isinstance(value, dict), f"{label} must be a YAML mapping")
    return value


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ValidationError(f"cannot hash {path}: {exc}") from exc


def _string_list(value: Any, label: str, *, allow_empty: bool = False) -> list[str]:
    _require(isinstance(value, list), f"{label} must be a list")
    result: list[str] = []
    for index, item in enumerate(value):
        _require(
            isinstance(item, str) and bool(item.strip()),
            f"{label}[{index}] must be a non-empty string",
        )
        result.append(item.strip())
    _require(allow_empty or bool(result), f"{label} must not be empty")
    _require(len(result) == len(set(result)), f"{label} contains duplicates")
    return result


def _safe_image_ref(value: Any, mockups_root: Path, label: str) -> str:
    _require(isinstance(value, str) and bool(value.strip()), f"{label} is required")
    ref = value.strip()
    _require("\\" not in ref and "\x00" not in ref, f"{label} is not a safe POSIX path")
    pure = PurePosixPath(ref)
    _require(not pure.is_absolute(), f"{label} must be relative")
    _require(
        len(pure.parts) >= 2
        and pure.parts[0] == "images"
        and all(part not in {"", ".", ".."} for part in pure.parts),
        f"{label} must stay under images/",
    )
    _require(pure.suffix.lower() == ".png", f"{label} must reference a PNG")

    image_root = (mockups_root / "images").resolve()
    candidate = (mockups_root / Path(*pure.parts)).resolve()
    try:
        candidate.relative_to(image_root)
    except ValueError as exc:
        raise ValidationError(f"{label} escapes the images directory") from exc
    _require(candidate.is_file(), f"{label} does not exist: {ref}")
    return ref


def _inventory(
    screen_map: dict[str, Any],
    screen_map_path: Path,
    prd_text: str,
    ui_criteria_text: str,
) -> list[dict[str, Any]]:
    _require(screen_map.get("schema_version") == 1, "screen-map schema_version must be 1")
    screens = screen_map.get("screens")
    _require(isinstance(screens, list) and bool(screens), "screen-map screens must be non-empty")

    known_criteria = set(re.findall(r"\bC\d{2,}\b", ui_criteria_text))
    seen_screens: set[str] = set()
    seen_prd_screens: set[str] = set()
    seen_states: set[str] = set()
    expected: list[dict[str, Any]] = []
    mockups_root = screen_map_path.parent

    for screen_index, screen in enumerate(screens):
        label = f"screens[{screen_index}]"
        _require(isinstance(screen, dict), f"{label} must be a mapping")
        screen_id = screen.get("id")
        _require(
            isinstance(screen_id, str) and re.fullmatch(r"S\d{2,}", screen_id) is not None,
            f"{label}.id must match SNN",
        )
        _require(screen_id not in seen_screens, f"duplicate screen id {screen_id}")
        seen_screens.add(screen_id)

        raw_prd_screen_id = screen.get("prd_screen_id")
        if raw_prd_screen_id is None or raw_prd_screen_id == "":
            prd_screen_id = "N/A"
        else:
            _require(
                isinstance(raw_prd_screen_id, str)
                and re.fullmatch(r"T\d{2,}", raw_prd_screen_id) is not None,
                f"{label}.prd_screen_id must match TNN when present",
            )
            prd_screen_id = raw_prd_screen_id
            _require(
                prd_screen_id not in seen_prd_screens,
                f"prd_screen_id {prd_screen_id} maps to more than one screen",
            )
            seen_prd_screens.add(prd_screen_id)

        acceptance_criteria = screen.get("acceptance_criteria")
        _require(
            isinstance(acceptance_criteria, str) and bool(acceptance_criteria.strip()),
            f"{label}.acceptance_criteria must be non-empty prose",
        )

        criteria = _string_list(screen.get("criteria"), f"{label}.criteria")
        _require(
            all(re.fullmatch(r"C\d{2,}", cid) is not None for cid in criteria),
            f"{label}.criteria must contain only CIDs",
        )
        missing_criteria = [cid for cid in criteria if cid not in known_criteria]
        _require(not missing_criteria, f"{label}.criteria not found in ui_criteria: {missing_criteria}")

        states = screen.get("states")
        _require(isinstance(states, list) and bool(states), f"{label}.states must be non-empty")
        for state_index, state in enumerate(states):
            state_label = f"{label}.states[{state_index}]"
            _require(isinstance(state, dict), f"{state_label} must be a mapping")
            state_id = state.get("id")
            _require(
                isinstance(state_id, str)
                and (state_id == screen_id or re.fullmatch(re.escape(screen_id) + r"\.\d+", state_id)),
                f"{state_label}.id must belong to {screen_id}",
            )
            _require(state_id not in seen_states, f"duplicate state id {state_id}")
            seen_states.add(state_id)
            image = _safe_image_ref(state.get("image"), mockups_root, f"{state_label}.image")
            expected.append(
                {
                    "state_id": state_id,
                    "screen_id": screen_id,
                    "prd_screen_id": prd_screen_id,
                    "criteria": criteria,
                    "image": image,
                }
            )

    _require(expected, "screen-map must declare at least one state")
    return expected


def _validate_markdown(
    markdown: str,
    verdict: str,
    summary: dict[str, int],
    views: list[dict[str, Any]],
    findings: list[dict[str, Any]],
) -> None:
    _require(markdown.count("VERDICT:") == 1, "Markdown must contain exactly one VERDICT")
    verdict_lines = re.findall(r"(?m)^VERDICT: (APPROVED|REJECTED)$", markdown)
    _require(verdict_lines == [verdict], "Markdown and YAML verdicts differ")

    headings = ["## Resumo", "## Achados Transversais", "## Revisão por State"]
    positions: list[int] = []
    for heading in headings:
        _require(markdown.count(heading) == 1, f"Markdown must contain one {heading} section")
        positions.append(markdown.index(heading))
    _require(positions == sorted(positions), "Markdown sections are out of order")

    lines = markdown.splitlines()
    summary_lines = [
        f"- TOTAL_VIEWS: {summary['total_views']}",
        f"- COHERENT: {summary['coherent']}",
        f"- COHERENT_WITH_RESERVATION: {summary['coherent_with_reservation']}",
        f"- INCOHERENT: {summary['incoherent']}",
    ]
    for line in summary_lines:
        _require(lines.count(line) == 1, f"Markdown summary mismatch: {line}")

    expected_state_lines = [
        "- STATE: {state_id} | SCREEN: {screen_id} | PRD_SCREEN: {prd_screen_id} "
        "| RESULT: {result} | IMAGE: {image}".format(**view)
        for view in views
    ]
    observed_state_lines = [line for line in lines if line.startswith("- STATE: ")]
    _require(
        observed_state_lines == expected_state_lines,
        "Markdown state coverage, order, result, or image differs from YAML",
    )

    image_by_state = {view["state_id"]: view["image"] for view in views}
    expected_finding_lines = [
        f"- FINDING: {finding['id']} | STATE: {finding['state_id']} "
        f"| EVIDENCE: {image_by_state[finding['state_id']]}"
        for finding in findings
    ]
    observed_finding_lines = [line for line in lines if line.startswith("- FINDING: ")]
    _require(observed_finding_lines == expected_finding_lines, "Markdown findings differ from YAML")
    empty_marker = "- Nenhum finding transversal."
    if findings:
        _require(empty_marker not in lines, "Markdown declares no findings but YAML has findings")
    else:
        _require(lines.count(empty_marker) == 1, "Markdown must declare the empty findings section")


def validate(
    *,
    prd_path: Path,
    ui_criteria_path: Path,
    screen_map_path: Path,
    markdown_path: Path,
    review_yaml_path: Path,
) -> None:
    prd_text = _read_text(prd_path, "PRD")
    ui_criteria_text = _read_text(ui_criteria_path, "ui_criteria")
    screen_map = _load_yaml(screen_map_path, "screen-map")
    expected_views = _inventory(screen_map, screen_map_path, prd_text, ui_criteria_text)
    report = _load_yaml(review_yaml_path, "coherence review")
    markdown = _read_text(markdown_path, "coherence review Markdown")

    _require(report.get("schema_version") == 1, "review schema_version must be 1")
    for field, source_path in (
        ("prd_sha256", prd_path),
        ("ui_criteria_sha256", ui_criteria_path),
        ("screen_map_sha256", screen_map_path),
    ):
        value = report.get(field)
        _require(
            isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None,
            f"{field} must be a lowercase SHA-256",
        )
        _require(value == _sha256(source_path), f"{field} is stale")

    verdict = report.get("verdict")
    _require(verdict in VERDICTS, f"verdict must be one of {sorted(VERDICTS)}")
    views = report.get("views")
    _require(isinstance(views, list), "views must be a list")
    _require(
        len(views) == len(expected_views),
        "views must cover every screen-map state exactly once",
    )

    normalized_views: list[dict[str, Any]] = []
    result_counts: Counter[str] = Counter()
    for index, (view, expected) in enumerate(zip(views, expected_views)):
        label = f"views[{index}]"
        _require(isinstance(view, dict), f"{label} must be a mapping")
        for field in ("state_id", "screen_id", "prd_screen_id", "image"):
            _require(view.get(field) == expected[field], f"{label}.{field} differs from screen-map")

        requirements = _string_list(view.get("requirements"), f"{label}.requirements")
        missing_requirements = [ref for ref in requirements if ref not in prd_text]
        _require(
            not missing_requirements,
            f"{label}.requirements not found literally in PRD: {missing_requirements}",
        )
        criteria = _string_list(view.get("criteria"), f"{label}.criteria")
        _require(criteria == expected["criteria"], f"{label}.criteria differs from parent screen")

        result = view.get("result")
        _require(result in RESULTS, f"{label}.result must be one of {sorted(RESULTS)}")
        blocking = view.get("blocking")
        _require(type(blocking) is bool, f"{label}.blocking must be boolean")
        _require(
            blocking is (result == "INCOHERENT"),
            f"{label}.blocking must be true only for INCOHERENT",
        )
        observation = view.get("observation")
        _require(
            isinstance(observation, str) and bool(observation.strip()),
            f"{label}.observation must be non-empty",
        )
        result_counts[result] += 1
        normalized_views.append(
            {
                "state_id": expected["state_id"],
                "screen_id": expected["screen_id"],
                "prd_screen_id": expected["prd_screen_id"],
                "result": result,
                "image": expected["image"],
            }
        )

    summary = report.get("summary")
    _require(isinstance(summary, dict), "summary must be a mapping")
    expected_summary = {
        "total_views": len(views),
        "coherent": result_counts["COHERENT"],
        "coherent_with_reservation": result_counts["COHERENT_WITH_RESERVATION"],
        "incoherent": result_counts["INCOHERENT"],
    }
    for field, expected_count in expected_summary.items():
        value = summary.get(field)
        _require(type(value) is int and value >= 0, f"summary.{field} must be a non-negative integer")
        _require(value == expected_count, f"summary.{field} has an incorrect count")

    findings = report.get("findings")
    _require(isinstance(findings, list), "findings must be a list")
    seen_finding_ids: set[str] = set()
    incoherent_states = {
        view["state_id"] for view in normalized_views if view["result"] == "INCOHERENT"
    }
    image_by_state = {view["state_id"]: view["image"] for view in normalized_views}
    covered_states: set[str] = set()
    normalized_findings: list[dict[str, Any]] = []
    for index, finding in enumerate(findings):
        label = f"findings[{index}]"
        _require(isinstance(finding, dict), f"{label} must be a mapping")
        finding_id = finding.get("id")
        _require(
            isinstance(finding_id, str) and re.fullmatch(r"F-\d{3,}", finding_id) is not None,
            f"{label}.id must match F-NNN",
        )
        _require(finding_id not in seen_finding_ids, f"duplicate finding id {finding_id}")
        seen_finding_ids.add(finding_id)
        state_id = finding.get("state_id")
        _require(state_id in incoherent_states, f"{label}.state_id must reference an INCOHERENT view")
        expected_text = finding.get("expected")
        observed_text = finding.get("observed")
        _require(
            isinstance(expected_text, str) and bool(expected_text.strip()),
            f"{label}.expected must be actionable text",
        )
        _require(
            isinstance(observed_text, str) and bool(observed_text.strip()),
            f"{label}.observed must be actionable text",
        )
        _require(expected_text.strip() != observed_text.strip(), f"{label} expected and observed must differ")
        evidence = _string_list(finding.get("evidence"), f"{label}.evidence")
        expected_image = image_by_state[state_id]
        _require(expected_image in evidence, f"{label}.evidence must include the exact state PNG")
        for evidence_index, item in enumerate(evidence):
            if item.lower().endswith(".png"):
                _safe_image_ref(item, screen_map_path.parent, f"{label}.evidence[{evidence_index}]")
        _require(finding.get("blocking") is True, f"{label}.blocking must be true")
        covered_states.add(state_id)
        normalized_findings.append({"id": finding_id, "state_id": state_id})

    if verdict == "APPROVED":
        _require(not incoherent_states, "APPROVED requires zero INCOHERENT views")
        _require(not findings, "APPROVED requires findings to be empty")
    else:
        _require(bool(incoherent_states), "REJECTED requires at least one INCOHERENT view")
        _require(
            covered_states == incoherent_states,
            "REJECTED findings must cover every and only INCOHERENT state",
        )

    _validate_markdown(
        markdown,
        verdict,
        expected_summary,
        normalized_views,
        normalized_findings,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prd", type=Path, default=Path("docs/PRD.md"))
    parser.add_argument("--ui-criteria", type=Path, default=Path("docs/ui_criteria.md"))
    parser.add_argument("--screen-map", type=Path, default=Path("docs/mockups/screen-map.yml"))
    parser.add_argument(
        "--markdown",
        type=Path,
        default=Path("docs/mockups/prd-coherence-review.md"),
    )
    parser.add_argument(
        "--review-yaml",
        type=Path,
        default=Path("docs/mockups/prd-coherence-review.yml"),
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        validate(
            prd_path=args.prd,
            ui_criteria_path=args.ui_criteria,
            screen_map_path=args.screen_map,
            markdown_path=args.markdown,
            review_yaml_path=args.review_yaml,
        )
    except ValidationError as exc:
        print(f"mockup PRD review validation: FAIL: {exc}", file=sys.stderr)
        return 1
    print("mockup PRD review validation: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
