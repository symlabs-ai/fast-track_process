"""Structured process-improvement review and promotion governance.

Project cycles may improve their versioned process fork, but they must never
mutate the engine checkout directly.  This module validates the handoff between
those two ownership boundaries and keeps global candidates pending until a
maintainer records an explicit disposition.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re
from typing import Any

import yaml


SCHEMA_VERSION = 1
DEFAULT_REVIEW_PATH = "docs/process-improvements.yml"
DEFAULT_REPORT_PATH = "docs/process-improvements.md"

CLASSIFICATIONS = {"local", "global_candidate", "rejected"}
GLOBAL_TARGETS = {"process_template", "engine", "documentation"}
RESOLUTION_STATUSES = {"pending", "promoted", "deferred", "rejected"}
GLOBAL_CRITERIA = (
    "domain_independent",
    "no_product_identifiers",
    "configurable",
    "verified_in_cycle",
    "backward_compatible",
)

_IMPROVEMENT_ID_RE = re.compile(r"^PI-[0-9]{3,}$")


class ProcessImprovementError(ValueError):
    """Raised when the structured process-improvement contract is invalid."""


@dataclass(frozen=True)
class GlobalProcessCandidate:
    improvement_id: str
    title: str
    target: str
    status: str
    reason: str
    reference: str


@dataclass(frozen=True)
class ProcessImprovementReview:
    path: Path
    improvements: tuple[dict[str, Any], ...]
    global_candidates: tuple[GlobalProcessCandidate, ...]

    @property
    def pending_global_candidates(self) -> tuple[GlobalProcessCandidate, ...]:
        return tuple(
            item for item in self.global_candidates if item.status == "pending"
        )


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _safe_relative_path(value: Any) -> bool:
    if not _nonempty_string(value):
        return False
    path = PurePosixPath(str(value))
    return not path.is_absolute() and ".." not in path.parts


def _read_yaml_mapping(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ProcessImprovementError(f"nao foi possivel ler {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ProcessImprovementError(f"YAML invalido em {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ProcessImprovementError(f"{path} deve conter um mapping YAML")
    return data


def _validate_review_data(
    data: dict[str, Any],
    *,
    report_text: str,
) -> tuple[tuple[dict[str, Any], ...], tuple[GlobalProcessCandidate, ...]]:
    errors: list[str] = []
    if data.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version deve ser {SCHEMA_VERSION}")

    improvements = data.get("improvements")
    if not isinstance(improvements, list):
        errors.append("improvements deve ser uma lista")
        improvements = []

    no_findings_reason = data.get("no_findings_reason")
    if not improvements and not _nonempty_string(no_findings_reason):
        errors.append("lista vazia exige no_findings_reason")

    seen_ids: set[str] = set()
    global_candidates: list[GlobalProcessCandidate] = []

    for index, item in enumerate(improvements, start=1):
        prefix = f"improvements[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{prefix} deve ser um mapping")
            continue

        improvement_id = item.get("id")
        if not isinstance(improvement_id, str) or not _IMPROVEMENT_ID_RE.fullmatch(
            improvement_id
        ):
            errors.append(f"{prefix}.id deve usar PI-NNN")
            improvement_id = f"<item-{index}>"
        elif improvement_id in seen_ids:
            errors.append(f"id duplicado: {improvement_id}")
        else:
            seen_ids.add(improvement_id)

        if improvement_id not in report_text:
            errors.append(f"{improvement_id} nao aparece no relatorio Markdown")

        title = item.get("title")
        if not _nonempty_string(title):
            errors.append(f"{improvement_id}.title obrigatorio")
            title = ""
        rationale = item.get("rationale")
        if not _nonempty_string(rationale):
            errors.append(f"{improvement_id}.rationale obrigatorio")

        classification = item.get("classification")
        if classification not in CLASSIFICATIONS:
            errors.append(
                f"{improvement_id}.classification deve ser local, "
                "global_candidate ou rejected"
            )

        evidence = item.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            errors.append(f"{improvement_id}.evidence deve ter ao menos uma evidencia")
        else:
            for evidence_index, entry in enumerate(evidence, start=1):
                if (
                    not isinstance(entry, dict)
                    or not _nonempty_string(entry.get("source"))
                    or not _nonempty_string(entry.get("detail"))
                ):
                    errors.append(
                        f"{improvement_id}.evidence[{evidence_index}] "
                        "exige source e detail"
                    )

        criteria = item.get("criteria")
        criteria_values: dict[str, bool] = {}
        if not isinstance(criteria, dict):
            errors.append(f"{improvement_id}.criteria deve ser um mapping")
        else:
            for criterion in GLOBAL_CRITERIA:
                value = criteria.get(criterion)
                if not isinstance(value, bool):
                    errors.append(
                        f"{improvement_id}.criteria.{criterion} deve ser boolean"
                    )
                else:
                    criteria_values[criterion] = value

        change = item.get("change")
        applied_locally = False
        if not isinstance(change, dict):
            errors.append(f"{improvement_id}.change deve ser um mapping")
        else:
            applied_locally = change.get("applied_locally")
            if not isinstance(applied_locally, bool):
                errors.append(
                    f"{improvement_id}.change.applied_locally deve ser boolean"
                )
                applied_locally = False
            if not _nonempty_string(change.get("summary")):
                errors.append(f"{improvement_id}.change.summary obrigatorio")
            change_paths = change.get("paths", [])
            if not isinstance(change_paths, list) or any(
                not _safe_relative_path(path) for path in change_paths
            ):
                errors.append(
                    f"{improvement_id}.change.paths deve conter paths relativos seguros"
                )
            elif applied_locally and not change_paths:
                errors.append(
                    f"{improvement_id} aplicado localmente exige change.paths"
                )

        all_global_criteria = len(criteria_values) == len(GLOBAL_CRITERIA) and all(
            criteria_values.values()
        )

        if classification == "local" and all_global_criteria:
            errors.append(
                f"{improvement_id} satisfaz todos os criterios globais "
                "e nao pode ser classificado local"
            )
        if classification == "rejected" and applied_locally:
            errors.append(
                f"{improvement_id} rejected nao pode estar aplicado localmente"
            )

        if classification != "global_candidate":
            continue

        if not all_global_criteria:
            failed = [
                criterion
                for criterion in GLOBAL_CRITERIA
                if criteria_values.get(criterion) is not True
            ]
            errors.append(
                f"{improvement_id} global_candidate nao satisfaz: {', '.join(failed)}"
            )

        global_data = item.get("global")
        if not isinstance(global_data, dict):
            errors.append(f"{improvement_id}.global obrigatorio para global_candidate")
            continue

        target = global_data.get("target")
        if target not in GLOBAL_TARGETS:
            errors.append(
                f"{improvement_id}.global.target deve ser process_template, "
                "engine ou documentation"
            )
            target = ""
        if not _nonempty_string(global_data.get("summary")):
            errors.append(f"{improvement_id}.global.summary obrigatorio")
        test_plan = global_data.get("test_plan")
        if (
            not isinstance(test_plan, list)
            or not test_plan
            or any(not _nonempty_string(step) for step in test_plan)
        ):
            errors.append(f"{improvement_id}.global.test_plan exige ao menos um passo")

        resolution = global_data.get("resolution")
        if not isinstance(resolution, dict):
            errors.append(f"{improvement_id}.global.resolution obrigatorio")
            continue
        status = resolution.get("status")
        if status not in RESOLUTION_STATUSES:
            errors.append(
                f"{improvement_id}.global.resolution.status invalido: {status}"
            )
            status = ""
        reason = str(resolution.get("reason") or "").strip()
        reference = str(resolution.get("reference") or "").strip()
        if status != "pending" and not reason:
            errors.append(f"{improvement_id} resolvido exige resolution.reason")
        if status == "promoted" and not reference:
            errors.append(f"{improvement_id} promoted exige resolution.reference")

        global_candidates.append(
            GlobalProcessCandidate(
                improvement_id=improvement_id,
                title=str(title or "").strip(),
                target=str(target or ""),
                status=str(status or ""),
                reason=reason,
                reference=reference,
            )
        )

    if errors:
        raise ProcessImprovementError("; ".join(errors))

    return tuple(improvements), tuple(global_candidates)


def load_process_improvement_review(
    project_root: str | Path,
    *,
    path: str = DEFAULT_REVIEW_PATH,
    report_path: str = DEFAULT_REPORT_PATH,
) -> ProcessImprovementReview:
    root = Path(project_root)
    review_path = root / path
    report = root / report_path
    if not review_path.is_file():
        raise ProcessImprovementError(f"{path} nao encontrado")
    if not report.is_file():
        raise ProcessImprovementError(f"{report_path} nao encontrado")
    data = _read_yaml_mapping(review_path)
    improvements, candidates = _validate_review_data(
        data,
        report_text=report.read_text(encoding="utf-8", errors="ignore"),
    )
    return ProcessImprovementReview(
        path=review_path,
        improvements=improvements,
        global_candidates=candidates,
    )


def process_improvement_close_readiness(
    project_root: str | Path,
    *,
    path: str = DEFAULT_REVIEW_PATH,
    report_path: str = DEFAULT_REPORT_PATH,
) -> tuple[bool, str]:
    """Return whether process governance permits closing the current cycle."""
    root = Path(project_root)
    if not (root / path).exists():
        return True, "process improvements: artefato estruturado ausente (ciclo legado)"
    try:
        review = load_process_improvement_review(
            root, path=path, report_path=report_path
        )
    except ProcessImprovementError as exc:
        return False, f"process improvements invalido: {exc}"
    pending = review.pending_global_candidates
    if pending:
        ids = ", ".join(item.improvement_id for item in pending)
        return False, f"candidatos globais pendentes: {ids}"
    return True, (
        "process improvements: "
        f"{len(review.global_candidates)} candidato(s) global(is) "
        "com disposicao registrada"
    )


def resolve_global_process_candidate(
    project_root: str | Path,
    improvement_id: str,
    *,
    status: str,
    reason: str,
    reference: str = "",
    path: str = DEFAULT_REVIEW_PATH,
    report_path: str = DEFAULT_REPORT_PATH,
) -> ProcessImprovementReview:
    """Record a maintainer disposition without changing the global template."""
    if status not in RESOLUTION_STATUSES - {"pending"}:
        raise ProcessImprovementError("status deve ser promoted, deferred ou rejected")
    if not reason.strip():
        raise ProcessImprovementError("reason obrigatorio")
    if status == "promoted" and not reference.strip():
        raise ProcessImprovementError("reference obrigatoria para status promoted")

    root = Path(project_root)
    review_path = root / path
    report = root / report_path
    data = _read_yaml_mapping(review_path)
    improvements = data.get("improvements")
    if not isinstance(improvements, list):
        raise ProcessImprovementError("improvements deve ser uma lista")

    found = False
    for item in improvements:
        if not isinstance(item, dict) or item.get("id") != improvement_id:
            continue
        if item.get("classification") != "global_candidate":
            raise ProcessImprovementError(f"{improvement_id} nao e global_candidate")
        global_data = item.get("global")
        if not isinstance(global_data, dict):
            raise ProcessImprovementError(f"{improvement_id}.global ausente")
        global_data["resolution"] = {
            "status": status,
            "reason": reason.strip(),
            "reference": reference.strip(),
        }
        found = True
        break
    if not found:
        raise ProcessImprovementError(f"candidato nao encontrado: {improvement_id}")

    if not report.is_file():
        raise ProcessImprovementError(f"{report_path} nao encontrado")
    _validate_review_data(
        data,
        report_text=report.read_text(encoding="utf-8", errors="ignore"),
    )
    review_path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return load_process_improvement_review(root, path=path, report_path=report_path)
