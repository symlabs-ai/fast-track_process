"""Deterministic planning primitives for parallel ``mvp-builder-fast`` work.

The LLM may propose the work packages, but it does not control scheduling or
runtime state.  This module validates the proposal, derives safe waves from
dependencies and path ownership, and provides the small persistence helpers
used by the runner.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tempfile
from typing import Any

import yaml


__all__ = [
    "BatchLane",
    "BatchPlan",
    "BatchPlanError",
    "changed_paths",
    "compute_waves",
    "load_batch_plan",
    "load_runtime",
    "paths_outside_ownership",
    "save_runtime",
    "validate_batch_plan",
]


class BatchPlanError(ValueError):
    """Raised when an LLM-authored batch plan is unsafe or inconsistent."""


@dataclass(frozen=True, slots=True)
class BatchLane:
    """One independently owned unit of work in a builder batch."""

    id: str
    title: str
    goal: str
    backlog_items: tuple[str, ...]
    requirements: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    areas: tuple[str, ...]
    depends_on: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BatchPlan:
    """Validated schema-v1 plan with engine-derived execution waves."""

    schema_version: int
    request_sha256: str
    requirements: tuple[str, ...]
    foundation: Mapping[str, Any]
    lanes: tuple[BatchLane, ...]
    waves: tuple[tuple[str, ...], ...]


_ROOT_FIELDS = frozenset(
    {
        "schema_version",
        "request_sha256",
        "requirements",
        "foundation",
        "lanes",
    }
)
_FOUNDATION_FIELDS = frozenset({"goal", "acceptance_criteria", "areas"})
_LANE_FIELDS = frozenset(
    {
        "id",
        "title",
        "goal",
        "backlog_items",
        "requirements",
        "acceptance_criteria",
        "areas",
        "depends_on",
    }
)
_CORE_PROTECTED_PATHS = (".git", ".ft", "state")
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_LANE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_WINDOWS_DRIVE_PATTERN = re.compile(r"^[A-Za-z]:")
_GLOB_CHARACTERS = frozenset("*?[")


def _fail(message: str) -> BatchPlanError:
    return BatchPlanError(f"plano batch inválido: {message}")


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _fail(f"{field} deve ser um mapping")
    return value


def _strict_fields(
    value: Mapping[str, Any],
    expected: frozenset[str],
    field: str,
) -> None:
    keys = set(value)
    missing = sorted(expected - keys)
    unknown = sorted(keys - expected)
    if missing:
        raise _fail(f"{field} não contém campos obrigatórios: {missing}")
    if unknown:
        raise _fail(f"{field} contém campos não suportados: {unknown}")


def _text(value: Any, field: str, *, maximum: int = 4096) -> str:
    if not isinstance(value, str):
        raise _fail(f"{field} deve ser texto")
    normalized = value.strip()
    if not normalized:
        raise _fail(f"{field} não pode ser vazio")
    if len(normalized) > maximum:
        raise _fail(f"{field} excede {maximum} caracteres")
    if "\x00" in normalized:
        raise _fail(f"{field} contém NUL")
    return normalized


def _identifier(
    value: Any,
    field: str,
    *,
    lane: bool = False,
) -> str:
    normalized = _text(value, field, maximum=128)
    pattern = _LANE_ID_PATTERN if lane else _ID_PATTERN
    if not pattern.fullmatch(normalized):
        raise _fail(f"{field} possui identificador inválido: {normalized!r}")
    return normalized


def _list(
    value: Any,
    field: str,
    *,
    allow_empty: bool,
    maximum: int,
) -> list[Any]:
    if not isinstance(value, list):
        raise _fail(f"{field} deve ser uma lista")
    if not allow_empty and not value:
        raise _fail(f"{field} não pode ser vazio")
    if len(value) > maximum:
        raise _fail(f"{field} excede o limite de {maximum} itens")
    return value


def _unique(values: Sequence[str], field: str) -> tuple[str, ...]:
    seen: set[str] = set()
    duplicate: str | None = None
    for value in values:
        if value in seen:
            duplicate = value
            break
        seen.add(value)
    if duplicate is not None:
        raise _fail(f"{field} repete {duplicate!r}")
    return tuple(values)


def _policy_int(
    policy: Mapping[str, Any],
    key: str,
    default: int,
    *,
    minimum: int = 1,
) -> int:
    value = policy.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise _fail(f"policy.{key} deve ser inteiro >= {minimum}")
    return value


def _normalize_path(value: Any, field: str) -> str:
    raw = _text(value, field, maximum=1024)
    if "\\" in raw:
        raise _fail(f"{field} deve usar separadores POSIX")
    if raw.startswith("/") or raw.startswith("~") or _WINDOWS_DRIVE_PATTERN.match(raw):
        raise _fail(f"{field} deve ser relativo ao repositório: {raw!r}")
    if any(character in raw for character in _GLOB_CHARACTERS):
        raise _fail(f"{field} não aceita globs: {raw!r}")

    # A trailing slash and an explicit leading ``./`` do not widen ownership,
    # so canonicalise them.  Interior dot segments are also harmless; parent
    # traversal is always rejected before PurePosixPath can collapse it.
    candidate = raw.rstrip("/")
    while candidate.startswith("./"):
        candidate = candidate[2:]
    raw_parts = candidate.split("/")
    if not candidate or any(part == ".." for part in raw_parts):
        raise _fail(f"{field} contém travessia ou path vazio: {raw!r}")

    normalized = PurePosixPath(candidate).as_posix()
    if normalized in {"", "."} or normalized.startswith("../"):
        raise _fail(f"{field} não pode representar a raiz do repositório")
    return normalized


def _paths_overlap(first: str, second: str) -> bool:
    first_folded = first.casefold()
    second_folded = second.casefold()
    return (
        first_folded == second_folded
        or first_folded.startswith(f"{second_folded}/")
        or second_folded.startswith(f"{first_folded}/")
    )


def _policy_paths(
    policy: Mapping[str, Any],
    key: str,
    *,
    include_core: bool = False,
) -> tuple[str, ...]:
    raw_values = policy.get(key, [])
    if raw_values is None:
        raw_values = []
    if not isinstance(raw_values, list):
        raise _fail(f"policy.{key} deve ser uma lista")

    values = list(_CORE_PROTECTED_PATHS) if include_core else []
    values.extend(
        _normalize_path(item, f"policy.{key}[{index}]")
        for index, item in enumerate(raw_values)
    )
    if include_core:
        return tuple(dict.fromkeys(values))
    return _unique(values, f"policy.{key}")


def _protected_paths(policy: Mapping[str, Any]) -> tuple[str, ...]:
    protected = list(_policy_paths(policy, "protected_paths", include_core=True))
    for key in ("plan_path", "request_path"):
        value = policy.get(key)
        if value not in (None, ""):
            protected.append(_normalize_path(value, f"policy.{key}"))
    # A repeated path is harmless when it comes from the mandatory core plus
    # an explicit policy entry, so de-duplicate here rather than rejecting it.
    return tuple(dict.fromkeys(protected))


def _validate_area(
    value: Any,
    field: str,
    *,
    protected: Sequence[str],
    allowed_roots: Sequence[str],
) -> str:
    area = _normalize_path(value, field)
    for protected_path in protected:
        if _paths_overlap(area, protected_path):
            raise _fail(
                f"{field} invade path protegido {protected_path!r}: {area!r}"
            )
    if allowed_roots and not any(
        area == root or area.startswith(f"{root}/") for root in allowed_roots
    ):
        raise _fail(f"{field} está fora das raízes permitidas: {area!r}")
    return area


def _text_items(
    value: Any,
    field: str,
    *,
    allow_empty: bool,
    maximum: int,
) -> tuple[str, ...]:
    raw = _list(
        value,
        field,
        allow_empty=allow_empty,
        maximum=maximum,
    )
    normalized = [
        _text(item, f"{field}[{index}]")
        for index, item in enumerate(raw)
    ]
    return _unique(normalized, field)


def _id_items(
    value: Any,
    field: str,
    *,
    allow_empty: bool,
    maximum: int,
    lane: bool = False,
) -> tuple[str, ...]:
    raw = _list(
        value,
        field,
        allow_empty=allow_empty,
        maximum=maximum,
    )
    normalized = [
        _identifier(item, f"{field}[{index}]", lane=lane)
        for index, item in enumerate(raw)
    ]
    return _unique(normalized, field)


def _requirement_items(
    value: Any,
    field: str,
    *,
    maximum: int,
) -> tuple[str, ...]:
    """Accept compact IDs or the v1 human-readable ``{id, text}`` form."""

    raw = _list(
        value,
        field,
        allow_empty=False,
        maximum=maximum,
    )
    normalized: list[str] = []
    representation: str | None = None
    for index, item in enumerate(raw):
        item_field = f"{field}[{index}]"
        if isinstance(item, str):
            current_representation = "id"
            requirement_id = _identifier(item, item_field)
        elif isinstance(item, Mapping):
            current_representation = "mapping"
            _strict_fields(item, frozenset({"id", "text"}), item_field)
            requirement_id = _identifier(item["id"], f"{item_field}.id")
            _text(item["text"], f"{item_field}.text")
        else:
            raise _fail(
                f"{item_field} deve ser um ID ou mapping com id/text"
            )
        if representation is not None and current_representation != representation:
            raise _fail(f"{field} mistura representações de requirements")
        representation = current_representation
        normalized.append(requirement_id)
    return _unique(normalized, field)


def _area_items(
    value: Any,
    field: str,
    *,
    allow_empty: bool,
    maximum: int,
    protected: Sequence[str],
    allowed_roots: Sequence[str],
) -> tuple[str, ...]:
    raw = _list(
        value,
        field,
        allow_empty=allow_empty,
        maximum=maximum,
    )
    normalized = [
        _validate_area(
            item,
            f"{field}[{index}]",
            protected=protected,
            allowed_roots=allowed_roots,
        )
        for index, item in enumerate(raw)
    ]
    return _unique(normalized, field)


def validate_batch_plan(
    data: Any,
    request_text: str,
    policy: Mapping[str, Any] | None,
) -> BatchPlan:
    """Validate and normalise one schema-v1 LLM batch plan.

    Unknown policy keys are intentionally ignored because the same policy also
    carries runner-only configuration.  The LLM-authored plan itself is strict:
    in particular, it cannot declare waves or executable commands.
    """

    if not isinstance(request_text, str):
        raise _fail("request_text deve ser texto")
    normalized_policy = _mapping(policy or {}, "policy")
    root = _mapping(data, "root")
    _strict_fields(root, _ROOT_FIELDS, "root")

    schema_version = root["schema_version"]
    if isinstance(schema_version, bool) or schema_version != 1:
        raise _fail("schema_version deve ser o inteiro 1")

    request_sha256 = _text(
        root["request_sha256"],
        "request_sha256",
        maximum=64,
    ).lower()
    if not re.fullmatch(r"[0-9a-f]{64}", request_sha256):
        raise _fail("request_sha256 deve ser um SHA-256 hexadecimal")
    expected_request_sha256 = hashlib.sha256(
        request_text.encode("utf-8")
    ).hexdigest()
    if request_sha256 != expected_request_sha256:
        raise _fail(
            "request_sha256 não corresponde ao input natural autorizado"
        )

    max_requirements = _policy_int(
        normalized_policy,
        "max_requirements",
        256,
    )
    requirements = _requirement_items(
        root["requirements"],
        "requirements",
        maximum=max_requirements,
    )

    min_lanes = _policy_int(normalized_policy, "min_lanes", 1)
    max_lanes = _policy_int(normalized_policy, "max_lanes", 16)
    if min_lanes > max_lanes:
        raise _fail("policy.min_lanes não pode exceder policy.max_lanes")
    max_criteria = _policy_int(
        normalized_policy,
        "max_acceptance_criteria_per_lane",
        32,
    )
    max_areas = _policy_int(
        normalized_policy,
        "max_areas_per_lane",
        64,
    )
    max_parallel = _policy_int(
        normalized_policy,
        "default_max_parallel",
        4,
    )

    protected = _protected_paths(normalized_policy)
    allowed_roots = _policy_paths(normalized_policy, "allowed_area_roots")
    evidence_root = normalized_policy.get("evidence_root")
    if evidence_root not in (None, ""):
        normalized_evidence_root = _normalize_path(
            evidence_root,
            "policy.evidence_root",
        )
        if any(
            _paths_overlap(normalized_evidence_root, item)
            for item in protected
        ):
            raise _fail("policy.evidence_root invade path protegido")

    foundation_raw = _mapping(root["foundation"], "foundation")
    _strict_fields(foundation_raw, _FOUNDATION_FIELDS, "foundation")
    foundation = {
        "goal": _text(foundation_raw["goal"], "foundation.goal"),
        "acceptance_criteria": _text_items(
            foundation_raw["acceptance_criteria"],
            "foundation.acceptance_criteria",
            allow_empty=False,
            maximum=max_criteria,
        ),
        "areas": _area_items(
            foundation_raw["areas"],
            "foundation.areas",
            allow_empty=True,
            maximum=max_areas,
            protected=protected,
            allowed_roots=allowed_roots,
        ),
    }

    lane_values = _list(
        root["lanes"],
        "lanes",
        allow_empty=False,
        maximum=max_lanes,
    )
    if len(lane_values) < min_lanes:
        raise _fail(
            f"lanes contém {len(lane_values)} item(ns), abaixo de min_lanes={min_lanes}"
        )

    lanes: list[BatchLane] = []
    for index, raw_lane in enumerate(lane_values):
        field = f"lanes[{index}]"
        lane_mapping = _mapping(raw_lane, field)
        _strict_fields(lane_mapping, _LANE_FIELDS, field)
        lanes.append(
            BatchLane(
                id=_identifier(lane_mapping["id"], f"{field}.id", lane=True),
                title=_text(lane_mapping["title"], f"{field}.title"),
                goal=_text(lane_mapping["goal"], f"{field}.goal"),
                backlog_items=_id_items(
                    lane_mapping["backlog_items"],
                    f"{field}.backlog_items",
                    allow_empty=True,
                    maximum=max_requirements,
                ),
                requirements=_id_items(
                    lane_mapping["requirements"],
                    f"{field}.requirements",
                    allow_empty=False,
                    maximum=max_requirements,
                ),
                acceptance_criteria=_text_items(
                    lane_mapping["acceptance_criteria"],
                    f"{field}.acceptance_criteria",
                    allow_empty=False,
                    maximum=max_criteria,
                ),
                areas=_area_items(
                    lane_mapping["areas"],
                    f"{field}.areas",
                    allow_empty=False,
                    maximum=max_areas,
                    protected=protected,
                    allowed_roots=allowed_roots,
                ),
                depends_on=_id_items(
                    lane_mapping["depends_on"],
                    f"{field}.depends_on",
                    allow_empty=True,
                    maximum=max_lanes,
                    lane=True,
                ),
            )
        )

    lane_ids = [lane.id for lane in lanes]
    _unique(lane_ids, "lanes.id")
    known_lanes = set(lane_ids)
    for lane in lanes:
        foundation_overlaps = [
            (foundation_area, lane_area)
            for foundation_area in foundation["areas"]
            for lane_area in lane.areas
            if _paths_overlap(foundation_area, lane_area)
        ]
        if foundation_overlaps:
            raise _fail(
                f"lane {lane.id!r} invade ownership exclusivo da foundation: "
                f"{foundation_overlaps}"
            )
        if lane.id in lane.depends_on:
            raise _fail(f"lane {lane.id!r} depende de si própria")
        unknown = sorted(set(lane.depends_on) - known_lanes)
        if unknown:
            raise _fail(f"lane {lane.id!r} possui dependências desconhecidas: {unknown}")

    owners: dict[str, str] = {}
    declared_requirements = set(requirements)
    for lane in lanes:
        for requirement in lane.requirements:
            if requirement not in declared_requirements:
                raise _fail(
                    f"lane {lane.id!r} referencia requirement desconhecido "
                    f"{requirement!r}"
                )
            previous = owners.get(requirement)
            if previous is not None:
                raise _fail(
                    f"requirement {requirement!r} foi atribuído às lanes "
                    f"{previous!r} e {lane.id!r}"
                )
            owners[requirement] = lane.id
    missing = [item for item in requirements if item not in owners]
    if missing:
        raise _fail(f"requirements sem lane responsável: {missing}")

    computed_waves = compute_waves(lanes, min(max_parallel, max_lanes))
    return BatchPlan(
        schema_version=1,
        request_sha256=request_sha256,
        requirements=requirements,
        foundation=foundation,
        lanes=tuple(lanes),
        waves=tuple(tuple(wave) for wave in computed_waves),
    )


def load_batch_plan(
    path: str | Path,
    request_path: str | Path,
    policy: Mapping[str, Any] | None,
) -> BatchPlan:
    """Load YAML and validate it against the exact staged natural-language input."""

    plan_path = Path(path)
    staged_request_path = Path(request_path)
    try:
        # Decode explicit bytes instead of using universal-newline text I/O:
        # request_sha256 binds the exact staged UTF-8 bytes, including CRLF.
        request_text = staged_request_path.read_bytes().decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise BatchPlanError(
            f"não foi possível ler o input batch {staged_request_path}: {exc}"
        ) from exc
    try:
        raw_plan = plan_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise BatchPlanError(
            f"não foi possível ler o plano batch {plan_path}: {exc}"
        ) from exc
    try:
        data = yaml.safe_load(raw_plan)
    except yaml.YAMLError as exc:
        raise BatchPlanError(f"YAML batch inválido em {plan_path}: {exc}") from exc
    return validate_batch_plan(data, request_text, policy)


def _lanes_overlap(first: BatchLane, second: BatchLane) -> bool:
    return any(
        _paths_overlap(first_area, second_area)
        for first_area in first.areas
        for second_area in second.areas
    )


def _largest_compatible_ready_set(
    ready: Sequence[BatchLane],
    maximum: int,
) -> list[BatchLane]:
    """Choose a maximum-width compatible set with stable input-order ties."""

    best: list[BatchLane] = []

    def visit(index: int, selected: list[BatchLane]) -> None:
        nonlocal best
        if len(selected) > len(best):
            best = list(selected)
            if len(best) == maximum:
                return
        if index >= len(ready) or len(selected) >= maximum:
            return
        if len(selected) + (len(ready) - index) <= len(best):
            return

        candidate = ready[index]
        if all(not _lanes_overlap(candidate, other) for other in selected):
            selected.append(candidate)
            visit(index + 1, selected)
            selected.pop()
            if len(best) == maximum:
                return
        visit(index + 1, selected)

    visit(0, [])
    return best


def compute_waves(
    lanes: Sequence[BatchLane],
    max_parallel: int,
) -> list[list[str]]:
    """Derive deterministic dependency- and ownership-safe execution waves."""

    if isinstance(max_parallel, bool) or not isinstance(max_parallel, int):
        raise _fail("max_parallel deve ser inteiro")
    if max_parallel < 1:
        raise _fail("max_parallel deve ser >= 1")
    if not lanes:
        return []

    ordered = sorted(lanes, key=lambda lane: lane.id)
    by_id: dict[str, BatchLane] = {}
    for lane in ordered:
        if not isinstance(lane, BatchLane):
            raise _fail("compute_waves aceita apenas BatchLane")
        if lane.id in by_id:
            raise _fail(f"lane duplicada em compute_waves: {lane.id!r}")
        by_id[lane.id] = lane
    known = set(by_id)
    for lane in ordered:
        unknown = set(lane.depends_on) - known
        if unknown:
            raise _fail(
                f"lane {lane.id!r} possui dependências desconhecidas: "
                f"{sorted(unknown)}"
            )
        if lane.id in lane.depends_on:
            raise _fail(f"lane {lane.id!r} depende de si própria")

    remaining = set(by_id)
    completed: set[str] = set()
    waves: list[list[str]] = []
    while remaining:
        ready = [
            lane
            for lane in ordered
            if lane.id in remaining and set(lane.depends_on) <= completed
        ]
        if not ready:
            blocked = {
                lane_id: sorted(set(by_id[lane_id].depends_on) - completed)
                for lane_id in sorted(remaining)
            }
            raise _fail(f"dependências cíclicas entre lanes: {blocked}")

        selected = _largest_compatible_ready_set(ready, max_parallel)
        if not selected:  # One ready lane is always compatible with itself.
            raise _fail("scheduler não encontrou lane segura para a próxima wave")
        wave = [lane.id for lane in selected]

        for first_index, first in enumerate(selected):
            for second in selected[first_index + 1 :]:
                if _lanes_overlap(first, second):
                    raise _fail(
                        f"overlap interno na wave: {first.id!r} e {second.id!r}"
                    )
        waves.append(wave)
        remaining.difference_update(wave)
        completed.update(wave)
    return waves


def _git(
    repo: Path,
    *arguments: str,
) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            ["git", *arguments],
            cwd=repo,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BatchPlanError(f"falha ao executar git em {repo}: {exc}") from exc


def _git_error(result: subprocess.CompletedProcess[bytes]) -> str:
    payload = result.stderr or result.stdout
    return payload.decode("utf-8", errors="replace").strip()


def _decode_git_path(value: bytes) -> str:
    return value.decode("utf-8", errors="surrogateescape")


def _parse_name_status_z(payload: bytes) -> set[str]:
    """Parse both Git's NUL-field and tab-field name-status encodings."""

    tokens = payload.split(b"\0")
    paths: set[str] = set()
    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if not token:
            continue

        first_path: bytes | None = None
        if b"\t" in token:
            status, first_path = token.split(b"\t", 1)
        else:
            status = token
            if index >= len(tokens):
                raise BatchPlanError("saída truncada de git diff --name-status")
            first_path = tokens[index]
            index += 1
        if not status:
            raise BatchPlanError("status vazio em git diff --name-status")
        if first_path:
            paths.add(_decode_git_path(first_path))

        if chr(status[0]) in {"R", "C"}:
            if index >= len(tokens):
                raise BatchPlanError("rename/copy truncado em git diff")
            second_path = tokens[index]
            index += 1
            if not second_path:
                raise BatchPlanError("destino vazio em rename/copy do git diff")
            paths.add(_decode_git_path(second_path))
    return paths


def changed_paths(repo: str | Path, base_ref: str) -> list[str]:
    """Return tracked and untracked paths changed since ``base_ref``.

    Rename/copy sources and destinations are both returned so a lane cannot
    evade ownership by moving a file across an area boundary.
    """

    root = Path(repo).resolve()
    if not root.is_dir():
        raise BatchPlanError(f"repositório batch inexistente: {root}")
    reference = _text(base_ref, "base_ref", maximum=512)
    resolved = _git(
        root,
        "rev-parse",
        "--verify",
        "--end-of-options",
        f"{reference}^{{commit}}",
    )
    if resolved.returncode != 0:
        raise BatchPlanError(
            f"base_ref Git inválida {reference!r}: {_git_error(resolved)}"
        )
    commit = resolved.stdout.decode("ascii", errors="strict").strip()

    diff = _git(
        root,
        "diff",
        "--name-status",
        "-z",
        "--find-renames",
        commit,
        "--",
    )
    if diff.returncode != 0:
        raise BatchPlanError(f"falha ao calcular diff batch: {_git_error(diff)}")
    paths = _parse_name_status_z(diff.stdout)

    untracked = _git(
        root,
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
        "--",
    )
    if untracked.returncode != 0:
        raise BatchPlanError(
            f"falha ao listar arquivos não rastreados: {_git_error(untracked)}"
        )
    paths.update(
        _decode_git_path(item)
        for item in untracked.stdout.split(b"\0")
        if item
    )
    return sorted(paths)


def _ownership_path(value: Any) -> str | None:
    try:
        return _normalize_path(value, "ownership path")
    except BatchPlanError:
        return None


def paths_outside_ownership(
    changed: Iterable[str],
    allowed: Iterable[str],
    evidence_root: str | None,
    lane_id: str,
) -> list[str]:
    """Return changed paths outside a lane's declared ownership.

    Evidence is an explicit narrow exception: a lane may write only below
    ``<evidence_root>/<lane_id>`` (or a root containing ``{lane_id}``).
    """

    normalized_allowed: list[str] = []
    for item in allowed:
        normalized = _ownership_path(item)
        if normalized is None:
            raise _fail(f"ownership permitido contém path inseguro: {item!r}")
        normalized_allowed.append(normalized)

    evidence_area: str | None = None
    if evidence_root not in (None, ""):
        safe_lane_id = _identifier(lane_id, "lane_id", lane=True)
        root_template = str(evidence_root)
        raw_evidence_area = (
            root_template.replace("{lane_id}", safe_lane_id)
            if "{lane_id}" in root_template
            else f"{root_template.rstrip('/')}/{safe_lane_id}"
        )
        evidence_area = _normalize_path(raw_evidence_area, "evidence ownership")

    outside: set[str] = set()
    for raw_path in changed:
        normalized = _ownership_path(raw_path)
        if normalized is None:
            outside.add(str(raw_path))
            continue
        owned = any(
            normalized == area or normalized.startswith(f"{area}/")
            for area in normalized_allowed
        )
        if not owned and evidence_area is not None:
            owned = normalized == evidence_area or normalized.startswith(
                f"{evidence_area}/"
            )
        if not owned:
            outside.add(normalized)
    return sorted(outside)


def load_runtime(path: str | Path) -> dict[str, Any]:
    """Load the authoritative batch runtime ledger, or ``{}`` if absent."""

    target = Path(path)
    if not target.exists():
        return {}
    try:
        raw = target.read_text(encoding="utf-8")
        payload = yaml.safe_load(raw)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise BatchPlanError(f"runtime batch inválido em {target}: {exc}") from exc
    if payload is None:
        raise BatchPlanError(f"runtime batch vazio em {target}")
    if not isinstance(payload, dict):
        raise BatchPlanError(f"runtime batch em {target} deve ser um mapping")
    return payload


def save_runtime(path: str | Path, data: Mapping[str, Any]) -> None:
    """Publish a complete runtime ledger with an atomic, fsynced replace."""

    if not isinstance(data, Mapping):
        raise BatchPlanError("runtime batch deve ser um mapping")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        payload = yaml.safe_dump(
            dict(data),
            allow_unicode=True,
            sort_keys=False,
        )
    except yaml.YAMLError as exc:
        raise BatchPlanError(f"runtime batch não serializável: {exc}") from exc

    mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o644
    temporary: Path | None = None
    try:
        descriptor, raw_temporary = tempfile.mkstemp(
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
        )
        temporary = Path(raw_temporary)
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        temporary = None

        # Persist the directory entry as well when the platform supports it.
        try:
            directory_fd = os.open(target.parent, os.O_RDONLY)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            except OSError:
                pass
            finally:
                os.close(directory_fd)
    except OSError as exc:
        raise BatchPlanError(
            f"falha ao salvar runtime batch em {target}: {exc}"
        ) from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
