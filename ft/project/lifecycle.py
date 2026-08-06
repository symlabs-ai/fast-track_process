"""Versioned project lifecycle and deterministic project readiness.

Cycles are disposable execution units.  This module owns the durable objective
above them: a project remains ``building`` until its declared Definition of
Done evaluates without blockers and ``ft project-close`` records that result.
Only then may maintenance templates run.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
from typing import Any, Mapping

import yaml

from ft.engine import paths
from ft.engine.validation_profiles import (
    ValidationProfileError,
    default_validation_config,
    resolve_validation_matrix,
    safe_project_output,
    normalize_validation_config,
)
from ft.engine.validators.artifacts import _backlog_rows, _row_value
from ft.engine.validators.platforms import (
    platform_validation_report,
    validation_matrix_valid,
)


PROJECT_CONTRACT_VERSION = 1
PROJECT_READINESS_VERSION = 1
PROJECT_PHASES = frozenset({"building", "maintenance", "archived"})
PROJECT_ROLES = frozenset({"builder", "maintenance", "neutral"})
DEFAULT_MAINTENANCE_TEMPLATES = frozenset(
    {
        "feature",
        "feature-fast",
        "bug",
        "bug-fast",
        "tweak",
        "material_design_pwa",
    }
)
DEFAULT_BUILDER_TEMPLATES = frozenset(
    {
        "mvp-builder",
        "mvp-builder-fast",
        "fastfy",
    }
)
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_GIT_REVISION_RE = re.compile(r"^[0-9a-f]{40,64}$")
_PLACEHOLDERS = frozenset({"", "-", "—", "n/a", "na", "none", "null", "tbd"})


class ProjectContractError(ValueError):
    """The versioned project contract is missing, unsafe or malformed."""


class ProjectLifecycleError(RuntimeError):
    """A process is incompatible with the current project lifecycle."""


@dataclass(frozen=True)
class ReadinessCheck:
    id: str
    status: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"id": self.id, "status": self.status, "detail": self.detail}


@dataclass(frozen=True)
class ReadinessBlocker:
    code: str
    message: str
    references: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
        }
        if self.references:
            payload["references"] = list(self.references)
        return payload


@dataclass(frozen=True)
class ProjectReadiness:
    project_id: str
    phase: str
    target: str
    status: str
    ready: bool
    evaluated_revision: str | None
    definition_of_done_digest: str
    evidence_fingerprint: str
    checks: tuple[ReadinessCheck, ...]
    blockers: tuple[ReadinessBlocker, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": PROJECT_READINESS_VERSION,
            "project_id": self.project_id,
            "phase": self.phase,
            "target": self.target,
            "status": self.status,
            "ready": self.ready,
            "evaluated_revision": self.evaluated_revision,
            "definition_of_done_digest": self.definition_of_done_digest,
            "evidence_fingerprint": self.evidence_fingerprint,
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
            "checks": [check.as_dict() for check in self.checks],
            "blocking_count": len(self.blockers),
            "blockers": [blocker.as_dict() for blocker in self.blockers],
        }


def default_project_contract(project_root: str | Path) -> dict[str, Any]:
    """Return the conservative lifecycle contract created by ``ft init``."""
    root = Path(project_root).resolve()
    project_id = re.sub(r"[^A-Za-z0-9._-]+", "-", root.name).strip("-._")
    project_id = project_id[:128] or "project"
    return {
        "schema_version": PROJECT_CONTRACT_VERSION,
        "project_id": project_id,
        "objective": {
            "statement": "Entregar o objetivo definido em docs/PRD.md",
            "source": "docs/PRD.md",
            "target": "mvp",
        },
        "lifecycle": {
            "phase": "building",
            "owner_template": None,
            "delivered_revision": None,
        },
        "validation": default_validation_config(),
        "definition_of_done": {
            "backlog": {
                "path": "docs/PROJECT_BACKLOG.md",
                "priorities": ["P0", "P1"],
                "required_ids": [],
                "excluded_ids": [],
                "accepted_statuses": ["done", "accepted"],
                "require_evidence": True,
            },
            "required_gates": [],
            "require_clean_checkout": True,
            "require_no_active_cycles": True,
        },
    }


def _atomic_write_yaml(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    temporary: Path | None = None
    try:
        fd, raw = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        temporary = Path(raw)
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            yaml.safe_dump(
                dict(payload),
                handle,
                allow_unicode=True,
                sort_keys=False,
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _safe_project_file(root: Path, path: Path, *, label: str) -> Path:
    lexical = path if path.is_absolute() else root / path
    if lexical.is_symlink():
        raise ProjectContractError(f"{label} não pode ser link simbólico: {lexical}")
    try:
        resolved_parent = lexical.parent.resolve()
        resolved_parent.relative_to(root)
    except ValueError as exc:
        raise ProjectContractError(f"{label} escapa da raiz do projeto: {path}") from exc
    return lexical


def _relative_path(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProjectContractError(f"{field} deve ser path relativo não vazio")
    candidate = Path(value.strip())
    if candidate.is_absolute() or ".." in candidate.parts or "\\" in value:
        raise ProjectContractError(f"{field} deve permanecer dentro do projeto")
    return candidate.as_posix()


def _string_list(
    value: object,
    *,
    field: str,
    allow_empty: bool = True,
) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ProjectContractError(f"{field} deve ser lista de strings não vazias")
    normalized = [item.strip() for item in value]
    if not allow_empty and not normalized:
        raise ProjectContractError(f"{field} não pode ser vazio")
    if len(set(normalized)) != len(normalized):
        raise ProjectContractError(f"{field} contém valores duplicados")
    return normalized


def validate_project_contract(
    payload: Mapping[str, Any],
    *,
    path: str | Path = ".ft/project.yml",
) -> dict[str, Any]:
    """Validate and normalize the project lifecycle contract."""
    location = Path(path)
    if not isinstance(payload, Mapping):
        raise ProjectContractError(f"contrato inválido em {location}: raiz deve ser mapping")
    contract = dict(payload)
    if contract.get("schema_version") != PROJECT_CONTRACT_VERSION:
        raise ProjectContractError(
            f"schema_version inválido em {location}: "
            f"esperado {PROJECT_CONTRACT_VERSION}"
        )
    project_id = contract.get("project_id")
    if not isinstance(project_id, str) or not _SAFE_ID_RE.fullmatch(project_id):
        raise ProjectContractError("project_id inválido em .ft/project.yml")

    objective = contract.get("objective")
    if not isinstance(objective, Mapping):
        raise ProjectContractError("objective deve ser mapping")
    statement = objective.get("statement")
    target = objective.get("target")
    if not isinstance(statement, str) or not statement.strip():
        raise ProjectContractError("objective.statement deve ser string não vazia")
    if not isinstance(target, str) or not _SAFE_ID_RE.fullmatch(target):
        raise ProjectContractError("objective.target deve ser identificador não vazio")
    source = objective.get("source")
    if source is not None:
        _relative_path(source, field="objective.source")

    lifecycle = contract.get("lifecycle")
    if not isinstance(lifecycle, Mapping):
        raise ProjectContractError("lifecycle deve ser mapping")
    phase = lifecycle.get("phase")
    if phase not in PROJECT_PHASES:
        raise ProjectContractError(
            "lifecycle.phase deve ser building, maintenance ou archived"
        )
    owner = lifecycle.get("owner_template")
    if owner is not None and (
        not isinstance(owner, str) or not _SAFE_ID_RE.fullmatch(owner)
    ):
        raise ProjectContractError("lifecycle.owner_template inválido")
    delivered_revision = lifecycle.get("delivered_revision")
    if delivered_revision is not None and (
        not isinstance(delivered_revision, str)
        or not _GIT_REVISION_RE.fullmatch(delivered_revision)
    ):
        raise ProjectContractError("lifecycle.delivered_revision inválido")
    if phase == "maintenance" and delivered_revision is None:
        raise ProjectContractError(
            "projeto em maintenance exige lifecycle.delivered_revision"
        )

    if "validation" in contract:
        try:
            normalize_validation_config(contract["validation"])
        except ValidationProfileError as exc:
            raise ProjectContractError(str(exc)) from exc

    dod = contract.get("definition_of_done")
    if not isinstance(dod, Mapping):
        raise ProjectContractError("definition_of_done deve ser mapping")
    backlog = dod.get("backlog")
    if not isinstance(backlog, Mapping):
        raise ProjectContractError("definition_of_done.backlog deve ser mapping")
    _relative_path(
        backlog.get("path"),
        field="definition_of_done.backlog.path",
    )
    priorities = _string_list(
        backlog.get("priorities"),
        field="definition_of_done.backlog.priorities",
    )
    invalid_priorities = sorted(set(priorities) - {"P0", "P1", "P2"})
    if invalid_priorities:
        raise ProjectContractError(
            "prioridades inválidas no DoD: " + ", ".join(invalid_priorities)
        )
    for field in ("required_ids", "excluded_ids"):
        values = _string_list(
            backlog.get(field),
            field=f"definition_of_done.backlog.{field}",
        )
        invalid = [item for item in values if not re.fullmatch(r"PB-\d+[A-Z]?", item)]
        if invalid:
            raise ProjectContractError(
                f"definition_of_done.backlog.{field} contém IDs inválidos"
            )
    overlap = set(backlog["required_ids"]) & set(backlog["excluded_ids"])
    if overlap:
        raise ProjectContractError(
            "required_ids e excluded_ids não podem se sobrepor: "
            + ", ".join(sorted(overlap))
        )
    accepted = _string_list(
        backlog.get("accepted_statuses"),
        field="definition_of_done.backlog.accepted_statuses",
        allow_empty=False,
    )
    if not set(accepted).issubset({"done", "accepted"}):
        raise ProjectContractError(
            "accepted_statuses do projeto aceita somente done/accepted"
        )
    for field in (
        "require_evidence",
        "require_clean_checkout",
        "require_no_active_cycles",
    ):
        value = (
            backlog.get(field)
            if field == "require_evidence"
            else dod.get(field)
        )
        if not isinstance(value, bool):
            raise ProjectContractError(f"{field} deve ser booleano")

    gates = dod.get("required_gates")
    if not isinstance(gates, list):
        raise ProjectContractError("definition_of_done.required_gates deve ser lista")
    gate_ids: list[str] = []
    for index, gate in enumerate(gates):
        if not isinstance(gate, Mapping):
            raise ProjectContractError(f"required_gates[{index}] deve ser mapping")
        gate_id = gate.get("id")
        if not isinstance(gate_id, str) or not _SAFE_ID_RE.fullmatch(gate_id):
            raise ProjectContractError(f"required_gates[{index}].id inválido")
        gate_ids.append(gate_id)
        _relative_path(gate.get("path"), field=f"required_gates[{index}].path")
        field_name = gate.get("field")
        if (
            not isinstance(field_name, str)
            or not re.fullmatch(
                r"[A-Za-z0-9_-]+(?:\.(?:[A-Za-z0-9_-]+|\d+))*",
                field_name,
            )
        ):
            raise ProjectContractError(f"required_gates[{index}].field inválido")
        if "equals" not in gate:
            raise ProjectContractError(f"required_gates[{index}].equals é obrigatório")
    if len(set(gate_ids)) != len(gate_ids):
        raise ProjectContractError("required_gates contém IDs duplicados")
    return contract


def read_project_contract(
    project_root: str | Path,
    *,
    required: bool = True,
) -> dict[str, Any] | None:
    root = Path(project_root).resolve()
    path = _safe_project_file(
        root,
        paths.project_contract(root),
        label="contrato de projeto",
    )
    if not path.is_file():
        if required:
            raise ProjectContractError(
                f"contrato de projeto ausente em {path}; execute ft init --fix"
            )
        return None
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ProjectContractError(f"contrato inválido em {path}: {exc}") from exc
    return validate_project_contract(payload, path=path)


def ensure_project_contract(project_root: str | Path) -> Path:
    """Create the conservative building contract without overwriting a fork."""
    root = Path(project_root).resolve()
    path = _safe_project_file(
        root,
        paths.project_contract(root),
        label="contrato de projeto",
    )
    if path.exists():
        read_project_contract(root)
        return path
    _atomic_write_yaml(path, default_project_contract(root))
    return path


def write_project_contract(
    project_root: str | Path,
    payload: Mapping[str, Any],
) -> Path:
    root = Path(project_root).resolve()
    normalized = validate_project_contract(payload)
    path = _safe_project_file(
        root,
        paths.project_contract(root),
        label="contrato de projeto",
    )
    _atomic_write_yaml(path, normalized)
    return path


def write_project_readiness(
    project_root: str | Path,
    payload: Mapping[str, Any],
) -> Path:
    root = Path(project_root).resolve()
    path = _safe_project_file(
        root,
        paths.project_readiness(root),
        label="receipt de prontidão",
    )
    _atomic_write_yaml(path, payload)
    return path


def _canonical_json(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def definition_of_done_digest(contract: Mapping[str, Any]) -> str:
    relevant = {
        "project_id": contract.get("project_id"),
        "objective": contract.get("objective"),
        "definition_of_done": contract.get("definition_of_done"),
    }
    # Perfis selecionados são parte do aceite global: alterar um target depois
    # do fechamento deve invalidar o receipt READY. Contratos legados sem a
    # seção mantêm exatamente o digest histórico.
    if "validation" in contract:
        relevant["validation"] = contract.get("validation")
    return "sha256:" + hashlib.sha256(_canonical_json(relevant)).hexdigest()


def _git(
    root: Path,
    *args: str,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProjectLifecycleError(f"git indisponível: {exc}") from exc


def _head_revision(root: Path) -> str | None:
    result = _git(root, "rev-parse", "--verify", "HEAD")
    revision = result.stdout.strip() if result.returncode == 0 else ""
    return revision if _GIT_REVISION_RE.fullmatch(revision) else None


def _evidence_tree_digest(root: Path) -> str:
    """Hash the tracked tree while ignoring the readiness receipt itself."""
    result = _git(root, "ls-tree", "-r", "--full-tree", "HEAD")
    if result.returncode != 0:
        return "sha256:" + hashlib.sha256(b"<git-tree-unavailable>").hexdigest()
    retained = "\n".join(
        line
        for line in result.stdout.splitlines()
        if not line.endswith("\t.ft/project-readiness.yml")
    )
    return "sha256:" + hashlib.sha256(retained.encode("utf-8")).hexdigest()


def _git_dirty_paths(root: Path) -> tuple[str, ...]:
    result = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    if result.returncode != 0:
        return ("<git-status-failed>",)
    paths_seen: list[str] = []
    for line in result.stdout.splitlines():
        raw = line[3:].strip() if len(line) >= 4 else line.strip()
        if " -> " in raw:
            raw = raw.split(" -> ", 1)[1]
        if raw:
            paths_seen.append(raw)
    return tuple(paths_seen)


def _load_structured_file(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() == ".json":
            return json.loads(text)
        return yaml.safe_load(text)
    except (OSError, UnicodeError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise ProjectContractError(f"evidência estruturada inválida em {path}: {exc}") from exc


def _resolve_field(payload: Any, field: str) -> tuple[bool, Any]:
    current = payload
    for token in field.split("."):
        if isinstance(current, Mapping) and token in current:
            current = current[token]
            continue
        if isinstance(current, list) and token.isdigit():
            index = int(token)
            if 0 <= index < len(current):
                current = current[index]
                continue
        return False, None
    return True, current


def _archived_cycle_receipt(root: Path, cycle_id: str) -> bool:
    receipt = paths.project_cycle_dir(root, cycle_id) / "cycle.yml"
    if receipt.is_symlink() or not receipt.is_file():
        return False
    try:
        payload = yaml.safe_load(receipt.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeError, yaml.YAMLError):
        return False
    return (
        isinstance(payload, Mapping)
        and payload.get("id") == cycle_id
        and payload.get("status")
        in {"done", "completed", "failed", "aborted", "cancelled", "canceled"}
        and isinstance(payload.get("closed_at"), str)
        and bool(payload["closed_at"].strip())
    )


def _active_cycle_ids(root: Path) -> tuple[str, ...]:
    from ft.runs.registry import CycleRegistry

    return tuple(
        record.cycle_id
        for record in CycleRegistry(root).open_cycles(include_terminal=True)
        if not _archived_cycle_receipt(root, record.cycle_id)
    )


def _record_check(
    checks: list[ReadinessCheck],
    blockers: list[ReadinessBlocker],
    *,
    check_id: str,
    passed: bool,
    detail: str,
    blocker_code: str | None = None,
    references: tuple[str, ...] = (),
) -> None:
    checks.append(ReadinessCheck(check_id, "PASS" if passed else "FAIL", detail))
    if not passed:
        blockers.append(
            ReadinessBlocker(
                blocker_code or check_id,
                detail,
                references,
            )
        )


def _valid_maintenance_receipt(
    root: Path,
    contract: Mapping[str, Any],
) -> tuple[bool, str]:
    path = paths.project_readiness(root)
    if path.is_symlink() or not path.is_file():
        return False, "receipt READY do fechamento do projeto está ausente"
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeError, yaml.YAMLError):
        return False, "receipt de prontidão do projeto é inválido"
    lifecycle = contract["lifecycle"]
    expected_revision = lifecycle.get("delivered_revision")
    expected_dod = definition_of_done_digest(contract)
    valid = (
        isinstance(payload, Mapping)
        and payload.get("schema_version") == PROJECT_READINESS_VERSION
        and payload.get("project_id") == contract.get("project_id")
        and payload.get("phase") == "maintenance"
        and payload.get("target") == contract.get("objective", {}).get("target")
        and payload.get("status") == "READY"
        and payload.get("ready") is True
        and payload.get("evaluated_revision") == expected_revision
        and payload.get("definition_of_done_digest") == expected_dod
        and payload.get("blocking_count") == 0
        and payload.get("blockers") == []
    )
    if not valid:
        return False, "receipt READY não corresponde ao DoD e revisão entregues"
    ancestry = _git(root, "merge-base", "--is-ancestor", str(expected_revision), "HEAD")
    if ancestry.returncode != 0:
        return False, "revisão entregue pelo receipt não pertence ao HEAD atual"
    return True, "fechamento READY vigente e íntegro"


def evaluate_project_readiness(
    project_root: str | Path,
) -> ProjectReadiness:
    """Evaluate the current project against its versioned DoD."""
    root = Path(project_root).resolve()
    contract = read_project_contract(root)
    assert contract is not None
    project_id = str(contract["project_id"])
    objective = contract["objective"]
    lifecycle = contract["lifecycle"]
    dod = contract["definition_of_done"]
    phase = str(lifecycle["phase"])
    target = str(objective["target"])
    revision = _head_revision(root)
    dod_digest = definition_of_done_digest(contract)
    checks: list[ReadinessCheck] = []
    blockers: list[ReadinessBlocker] = []
    fingerprint = hashlib.sha256()
    fingerprint.update(b"ft-project-readiness-v1\0")
    fingerprint.update(_canonical_json(contract))
    fingerprint.update(b"\0")
    fingerprint.update(_evidence_tree_digest(root).encode("ascii"))

    if phase == "maintenance":
        valid, detail = _valid_maintenance_receipt(root, contract)
        _record_check(
            checks,
            blockers,
            check_id="closure-receipt",
            passed=valid,
            detail=detail,
            blocker_code="project.closure_receipt",
            references=(".ft/project-readiness.yml",),
        )
        status = "MAINTENANCE" if valid else "INVALID_MAINTENANCE"
        return ProjectReadiness(
            project_id=project_id,
            phase=phase,
            target=target,
            status=status,
            ready=valid,
            evaluated_revision=revision,
            definition_of_done_digest=dod_digest,
            evidence_fingerprint="sha256:" + fingerprint.hexdigest(),
            checks=tuple(checks),
            blockers=tuple(blockers),
        )
    if phase == "archived":
        return ProjectReadiness(
            project_id=project_id,
            phase=phase,
            target=target,
            status="ARCHIVED",
            ready=False,
            evaluated_revision=revision,
            definition_of_done_digest=dod_digest,
            evidence_fingerprint="sha256:" + fingerprint.hexdigest(),
            checks=(),
            blockers=(
                ReadinessBlocker(
                    "project.archived",
                    "projeto arquivado não aceita novos ciclos",
                ),
            ),
        )

    _record_check(
        checks,
        blockers,
        check_id="git-head",
        passed=revision is not None,
        detail=(
            f"revisão avaliada: {revision}"
            if revision
            else "repositório não possui HEAD Git válido"
        ),
        blocker_code="project.git_head",
    )

    source = objective.get("source")
    if source:
        source_path = _safe_project_file(
            root,
            Path(str(source)),
            label="fonte do objetivo",
        )
        source_ok = source_path.is_file()
        _record_check(
            checks,
            blockers,
            check_id="objective-source",
            passed=source_ok,
            detail=(
                f"fonte do objetivo encontrada: {source}"
                if source_ok
                else f"fonte do objetivo ausente: {source}"
            ),
            blocker_code="project.objective_source",
            references=(str(source),),
        )
        if source_ok:
            fingerprint.update(str(source).encode("utf-8"))
            fingerprint.update(b"\0")
            fingerprint.update(source_path.read_bytes())

    backlog_policy = dod["backlog"]
    backlog_relative = str(backlog_policy["path"])
    backlog_path = _safe_project_file(
        root,
        Path(backlog_relative),
        label="backlog do projeto",
    )
    if not backlog_path.is_file():
        _record_check(
            checks,
            blockers,
            check_id="backlog",
            passed=False,
            detail=f"backlog obrigatório ausente: {backlog_relative}",
            blocker_code="project.backlog_missing",
            references=(backlog_relative,),
        )
    else:
        backlog_content = backlog_path.read_text(encoding="utf-8", errors="ignore")
        fingerprint.update(backlog_relative.encode("utf-8"))
        fingerprint.update(b"\0")
        fingerprint.update(backlog_content.encode("utf-8"))
        rows = _backlog_rows(backlog_content)
        by_id = {str(row["_id"]): row for row in rows}
        priorities = set(backlog_policy["priorities"])
        required_ids = set(backlog_policy["required_ids"])
        excluded_ids = set(backlog_policy["excluded_ids"])
        missing_required = sorted(required_ids - set(by_id))
        selected_ids = {
            str(row["_id"])
            for row in rows
            if str(row.get("_priority")) in priorities
        } | required_ids
        selected_ids -= excluded_ids
        accepted_statuses = set(backlog_policy["accepted_statuses"])
        unfinished = sorted(
            item
            for item in selected_ids
            if item in by_id
            and str(by_id[item].get("_status")) not in accepted_statuses
        )
        missing_evidence: list[str] = []
        if backlog_policy["require_evidence"]:
            for item in sorted(selected_ids):
                row = by_id.get(item)
                if row is None:
                    continue
                evidence = _row_value(row, "evidência", "evidencia", "evidence")
                if evidence.strip().lower() in _PLACEHOLDERS:
                    missing_evidence.append(item)
        backlog_ok = bool(selected_ids) and not missing_required and not unfinished
        detail_parts = [f"{len(selected_ids)} item(ns) no DoD"]
        if not selected_ids:
            detail_parts.append("nenhum item selecionado")
        if missing_required:
            detail_parts.append("ausentes: " + ", ".join(missing_required))
        if unfinished:
            detail_parts.append("não concluídos: " + ", ".join(unfinished))
        _record_check(
            checks,
            blockers,
            check_id="backlog",
            passed=backlog_ok,
            detail="; ".join(detail_parts),
            blocker_code="project.backlog_unfinished",
            references=tuple(missing_required + unfinished),
        )
        evidence_ok = not missing_evidence
        _record_check(
            checks,
            blockers,
            check_id="backlog-evidence",
            passed=evidence_ok,
            detail=(
                "todos os itens concluídos possuem evidência"
                if evidence_ok
                else "itens sem evidência: " + ", ".join(missing_evidence)
            ),
            blocker_code="project.backlog_evidence",
            references=tuple(missing_evidence),
        )

    for gate in dod["required_gates"]:
        gate_id = str(gate["id"])
        relative = str(gate["path"])
        evidence_path = _safe_project_file(
            root,
            Path(relative),
            label=f"evidência do gate {gate_id}",
        )
        if not evidence_path.is_file():
            _record_check(
                checks,
                blockers,
                check_id=f"gate:{gate_id}",
                passed=False,
                detail=f"{gate_id}: evidência ausente em {relative}",
                blocker_code="project.gate_missing",
                references=(gate_id, relative),
            )
            continue
        fingerprint.update(relative.encode("utf-8"))
        fingerprint.update(b"\0")
        fingerprint.update(evidence_path.read_bytes())
        try:
            structured = _load_structured_file(evidence_path)
            found, actual = _resolve_field(structured, str(gate["field"]))
        except ProjectContractError as exc:
            found, actual = False, None
            parse_detail = str(exc)
        else:
            parse_detail = ""
        expected = gate["equals"]
        passed = found and actual == expected
        if not found:
            detail = parse_detail or (
                f"{gate_id}: campo {gate['field']} ausente em {relative}"
            )
        else:
            detail = (
                f"{gate_id}: {gate['field']}={actual!r}; esperado {expected!r}"
            )
        _record_check(
            checks,
            blockers,
            check_id=f"gate:{gate_id}",
            passed=passed,
            detail=detail,
            blocker_code="project.gate_blocked",
            references=(gate_id, relative),
        )

    validation_config = contract.get("validation")
    if validation_config is not None:
        try:
            matrix = resolve_validation_matrix(root, contract)
        except ValidationProfileError as exc:
            _record_check(
                checks,
                blockers,
                check_id="platform-validation",
                passed=False,
                detail=f"contrato de validação inválido: {exc}",
                blocker_code="project.platform_validation",
                references=(".ft/project.yml",),
            )
        else:
            if matrix["status"] == "active":
                matrix_path = str(matrix["matrix_path"])
                report_path = str(matrix["report_path"])
                evidence_root = str(matrix["evidence_root"])
                identity_path = str(matrix["test_identity"]["path"])
                matrix_ok, matrix_detail = validation_matrix_valid(
                    matrix_path,
                    project_root=str(root),
                )
                report_ok, report_detail = platform_validation_report(
                    matrix_path=matrix_path,
                    report_path=report_path,
                    evidence_root=evidence_root,
                    test_identity_path=identity_path,
                    require_approved=True,
                    project_root=str(root),
                )
                platform_ok = matrix_ok and report_ok
                detail = (
                    report_detail
                    if matrix_ok
                    else matrix_detail
                )
                _record_check(
                    checks,
                    blockers,
                    check_id="platform-validation",
                    passed=platform_ok,
                    detail=detail,
                    blocker_code="project.platform_validation",
                    references=(matrix_path, report_path),
                )
                for relative in (matrix_path, report_path):
                    try:
                        evidence_path = safe_project_output(root, relative)
                    except ValidationProfileError:
                        continue
                    if evidence_path.is_file():
                        fingerprint.update(relative.encode("utf-8"))
                        fingerprint.update(b"\0")
                        fingerprint.update(evidence_path.read_bytes())
            else:
                _record_check(
                    checks,
                    blockers,
                    check_id="platform-validation",
                    passed=True,
                    detail=f"matrix de plataforma: {matrix['status']}",
                    blocker_code="project.platform_validation",
                )

    if dod["require_no_active_cycles"]:
        active = _active_cycle_ids(root)
        _record_check(
            checks,
            blockers,
            check_id="active-cycles",
            passed=not active,
            detail=(
                "nenhum ciclo aberto"
                if not active
                else "ciclos ainda abertos: " + ", ".join(active)
            ),
            blocker_code="project.active_cycles",
            references=active,
        )

    if dod["require_clean_checkout"]:
        dirty = _git_dirty_paths(root)
        _record_check(
            checks,
            blockers,
            check_id="clean-checkout",
            passed=not dirty,
            detail=(
                "checkout limpo"
                if not dirty
                else "checkout possui mudanças: " + ", ".join(dirty[:12])
            ),
            blocker_code="project.dirty_checkout",
            references=dirty[:12],
        )

    ready = not blockers
    return ProjectReadiness(
        project_id=project_id,
        phase=phase,
        target=target,
        status="READY_TO_CLOSE" if ready else "BLOCKED",
        ready=ready,
        evaluated_revision=revision,
        definition_of_done_digest=dod_digest,
        evidence_fingerprint="sha256:" + fingerprint.hexdigest(),
        checks=tuple(checks),
        blockers=tuple(blockers),
    )


def close_project_contract(
    project_root: str | Path,
) -> ProjectReadiness:
    """Evaluate and persist a closure receipt; transition only on green DoD."""
    root = Path(project_root).resolve()
    readiness = evaluate_project_readiness(root)
    contract = read_project_contract(root)
    assert contract is not None
    if readiness.phase == "maintenance":
        return readiness

    receipt = readiness.as_dict()
    if readiness.ready:
        lifecycle = dict(contract["lifecycle"])
        lifecycle["phase"] = "maintenance"
        lifecycle["delivered_revision"] = readiness.evaluated_revision
        updated = dict(contract)
        updated["lifecycle"] = lifecycle
        write_project_contract(root, updated)
        receipt["phase"] = "maintenance"
        receipt["status"] = "READY"
        receipt["ready"] = True
        receipt["blocking_count"] = 0
        receipt["blockers"] = []
    else:
        receipt_path = paths.project_readiness(root)
        try:
            previous = (
                yaml.safe_load(receipt_path.read_text(encoding="utf-8")) or {}
                if receipt_path.is_file() and not receipt_path.is_symlink()
                else {}
            )
        except (OSError, UnicodeError, yaml.YAMLError):
            previous = {}
        stable_fields = (
            "schema_version",
            "project_id",
            "phase",
            "target",
            "status",
            "ready",
            "definition_of_done_digest",
            "evidence_fingerprint",
            "blocking_count",
            "blockers",
        )
        if isinstance(previous, Mapping) and all(
            previous.get(field) == receipt.get(field) for field in stable_fields
        ):
            return readiness
    write_project_readiness(root, receipt)
    return readiness


def reopen_project_contract(
    project_root: str | Path,
    *,
    reason: str,
    objective: str | None = None,
    target: str | None = None,
) -> dict[str, Any]:
    """Move a delivered project back to construction for a new project goal."""
    if not isinstance(reason, str) or not reason.strip():
        raise ProjectLifecycleError("reabertura exige --reason não vazio")
    root = Path(project_root).resolve()
    contract = read_project_contract(root)
    assert contract is not None
    if _active_cycle_ids(root):
        raise ProjectLifecycleError("não reabra o projeto enquanto houver ciclos abertos")
    lifecycle = dict(contract["lifecycle"])
    lifecycle["phase"] = "building"
    lifecycle["owner_template"] = None
    lifecycle["delivered_revision"] = None
    updated = dict(contract)
    updated["lifecycle"] = lifecycle
    if objective is not None or target is not None:
        objective_payload = dict(contract["objective"])
        if objective is not None:
            objective_payload["statement"] = objective.strip()
        if target is not None:
            objective_payload["target"] = target.strip()
        updated["objective"] = objective_payload
    write_project_contract(root, updated)
    superseded = {
        "schema_version": PROJECT_READINESS_VERSION,
        "project_id": updated["project_id"],
        "phase": "building",
        "target": updated["objective"]["target"],
        "status": "SUPERSEDED",
        "ready": False,
        "evaluated_revision": _head_revision(root),
        "definition_of_done_digest": definition_of_done_digest(updated),
        "evidence_fingerprint": "sha256:" + hashlib.sha256(
            reason.strip().encode("utf-8")
        ).hexdigest(),
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "checks": [],
        "blocking_count": 1,
        "blockers": [
            {
                "code": "project.reopened",
                "message": reason.strip(),
            }
        ],
    }
    write_project_readiness(root, superseded)
    return updated


def project_role_for_template(
    template_name: str,
    execution_policy: Mapping[str, Any] | None,
) -> tuple[str, tuple[str, ...]]:
    policy = execution_policy if isinstance(execution_policy, Mapping) else {}
    raw_role = policy.get("project_role")
    if raw_role is None:
        if template_name in DEFAULT_MAINTENANCE_TEMPLATES:
            role = "maintenance"
        elif template_name in DEFAULT_BUILDER_TEMPLATES:
            role = "builder"
        else:
            role = "neutral"
    else:
        role = str(raw_role)
    if role not in PROJECT_ROLES:
        raise ProjectLifecycleError(
            f"template {template_name} declara project_role inválido: {role}"
        )
    raw_allowed = policy.get("allowed_project_phases")
    if raw_allowed is None:
        allowed = (
            ("building",)
            if role == "builder"
            else ("maintenance",)
            if role == "maintenance"
            else ("building", "maintenance")
        )
    elif isinstance(raw_allowed, list) and all(
        isinstance(item, str) and item in PROJECT_PHASES for item in raw_allowed
    ):
        allowed = tuple(raw_allowed)
    else:
        raise ProjectLifecycleError(
            f"template {template_name} declara allowed_project_phases inválido"
        )
    expected = (
        {"building"}
        if role == "builder"
        else {"maintenance"}
        if role == "maintenance"
        else {"building", "maintenance"}
    )
    if set(allowed) != expected:
        raise ProjectLifecycleError(
            f"template {template_name} declara fases incompatíveis com "
            f"project_role={role}: {', '.join(allowed)}"
        )
    return role, allowed


def assert_template_allowed(
    project_root: str | Path,
    *,
    template_name: str,
    execution_policy: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Fail closed when a template does not belong to the project phase."""
    role, allowed = project_role_for_template(template_name, execution_policy)
    if role == "neutral":
        contract = read_project_contract(project_root, required=False)
        if (
            contract is not None
            and contract["lifecycle"]["phase"] not in allowed
        ):
            raise ProjectLifecycleError(
                f"template '{template_name}' recusado: projeto arquivado"
            )
        return contract
    contract = read_project_contract(project_root, required=False)
    if contract is None:
        if role == "builder":
            ensure_project_contract(project_root)
            contract = read_project_contract(project_root)
        else:
            raise ProjectLifecycleError(
                f"template de manutenção '{template_name}' recusado: "
                "o projeto não possui .ft/project.yml nem fechamento READY"
            )
    assert contract is not None
    phase = str(contract["lifecycle"]["phase"])
    if phase not in allowed:
        if role == "maintenance":
            raise ProjectLifecycleError(
                f"template de manutenção '{template_name}' recusado: projeto "
                f"está em {phase}, não em maintenance. Conclua o processo "
                "construtor e execute `ft project-close`."
            )
        raise ProjectLifecycleError(
            f"template construtor '{template_name}' recusado: projeto está em "
            f"{phase}. Use `ft project-reopen --reason \"...\"` para abrir um "
            "novo objetivo de projeto."
        )
    if role == "builder":
        owner = contract["lifecycle"].get("owner_template")
        if owner is not None and owner != template_name:
            raise ProjectLifecycleError(
                f"template construtor '{template_name}' recusado: o objetivo "
                f"atual pertence ao construtor '{owner}'"
            )
        if owner is None:
            lifecycle = dict(contract["lifecycle"])
            lifecycle["owner_template"] = template_name
            updated = dict(contract)
            updated["lifecycle"] = lifecycle
            write_project_contract(project_root, updated)
            contract = updated
    if role == "maintenance":
        valid, detail = _valid_maintenance_receipt(
            Path(project_root).resolve(),
            contract,
        )
        if not valid:
            raise ProjectLifecycleError(
                f"template de manutenção '{template_name}' recusado: {detail}"
            )
    return contract


def project_run_context(contract: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(contract, Mapping):
        return {}
    lifecycle = contract.get("lifecycle", {})
    objective = contract.get("objective", {})
    if not isinstance(lifecycle, Mapping) or not isinstance(objective, Mapping):
        return {}
    return {
        "project_id": contract.get("project_id"),
        "project_phase": lifecycle.get("phase"),
        "project_target": objective.get("target"),
        "project_definition_of_done_digest": definition_of_done_digest(contract),
        "project_delivered_revision": lifecycle.get("delivered_revision"),
    }
