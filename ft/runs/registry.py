"""Durable cycle allocation, inventory, and strict cycle selection."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any, Iterable, Mapping

import yaml

from ft.engine import paths
from ft.runs.locking import LockKind, ProjectLock, project_prep_lock


_SAFE_CYCLE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_NUMBERED_CYCLE_RE = re.compile(r"^cycle-(\d+)(?:-|$)")
TERMINAL_STATUSES = frozenset(
    {"done", "completed", "failed", "aborted", "cancelled", "canceled"}
)


class CycleRegistryError(RuntimeError):
    """Base class for cycle allocation and selection failures."""


class InvalidCycleName(CycleRegistryError, ValueError):
    pass


class CycleAlreadyExists(CycleRegistryError):
    pass


class NoCycleError(CycleRegistryError):
    pass


class CycleNotFoundError(CycleRegistryError):
    pass


class CycleNotReadyError(CycleRegistryError):
    pass


class AmbiguousCycleError(CycleRegistryError):
    def __init__(self, cycle_ids: Iterable[str]) -> None:
        self.cycle_ids = tuple(cycle_ids)
        rendered = ", ".join(self.cycle_ids)
        super().__init__(
            "mais de um ciclo está aberto; informe --cycle. "
            f"Opções: {rendered}"
        )


def validate_cycle_name(name: str) -> str:
    normalized = str(name).strip()
    if not normalized:
        raise InvalidCycleName("nome de ciclo vazio")
    if normalized in {".", ".."} or not _SAFE_CYCLE_RE.fullmatch(normalized):
        raise InvalidCycleName(
            "nome de ciclo deve ter até 80 caracteres e usar apenas letras, "
            "números, '.', '_' ou '-'"
        )
    return normalized


def _cycle_number(name: str) -> int | None:
    match = _NUMBERED_CYCLE_RE.match(name)
    return int(match.group(1)) if match else None


def _cycle_sort_key(record_or_name: CycleRecord | str) -> tuple[int, int, str]:
    name = (
        record_or_name.cycle_id
        if isinstance(record_or_name, CycleRecord)
        else record_or_name
    )
    number = _cycle_number(name)
    return (number is None, number if number is not None else 0, name)


def _template_slug(template_id: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", str(template_id).strip())
    value = value.strip("-._")[:40]
    if not value:
        raise ValueError("template_id não produz um identificador de ciclo válido")
    return value


def _allocations_path(project_root: Path) -> Path:
    return paths.runtime_home(project_root) / "cycles" / "allocations.yml"


def _legacy_ledger_path(project_root: Path) -> Path:
    return paths.worktrees_home(project_root) / ".cycles"


def _atomic_write(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        fd, raw = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        temporary = Path(raw)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _read_allocation_ledger(project_root: Path) -> tuple[set[str], set[int]]:
    ledger = _allocations_path(project_root)
    if not ledger.is_file():
        return set(), set()
    try:
        data = yaml.safe_load(ledger.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise CycleRegistryError(f"ledger de ciclos inválido em {ledger}: {exc}") from exc
    raw_allocated = data.get("allocated", []) if isinstance(data, dict) else None
    if not isinstance(raw_allocated, list) or not all(
        isinstance(item, str) for item in raw_allocated
    ):
        raise CycleRegistryError(f"ledger de ciclos inválido em {ledger}")
    raw_numbers = data.get("reserved_numbers", [])
    if not isinstance(raw_numbers, list) or not all(
        isinstance(item, int) and not isinstance(item, bool) and item > 0
        for item in raw_numbers
    ):
        raise CycleRegistryError(f"ledger de ciclos inválido em {ledger}")
    return (
        {validate_cycle_name(item) for item in raw_allocated},
        set(raw_numbers),
    )


def _read_allocations(project_root: Path) -> set[str]:
    return _read_allocation_ledger(project_root)[0]


def _write_allocations(
    project_root: Path,
    allocated: set[str],
    reserved_numbers: set[int],
) -> None:
    payload = yaml.safe_dump(
        {
            "schema_version": 1,
            "allocated": sorted(allocated, key=_cycle_sort_key),
            "reserved_numbers": sorted(reserved_numbers),
        },
        allow_unicode=True,
        sort_keys=False,
    )
    _atomic_write(_allocations_path(project_root), payload)

    # Keep the old numeric ledger coherent while the CLI migrates to this API.
    numbers = sorted(reserved_numbers)
    if numbers:
        _atomic_write(
            _legacy_ledger_path(project_root),
            "".join(f"{number:02d}\n" for number in numbers),
        )


def _directory_cycle_ids(container: Path) -> set[str]:
    if not container.is_dir():
        return set()
    return {
        entry.name
        for entry in container.iterdir()
        if entry.is_dir() and _SAFE_CYCLE_RE.fullmatch(entry.name)
    }


def _git_branch_ids(project_root: Path) -> set[str]:
    try:
        result = subprocess.run(
            [
                "git",
                "for-each-ref",
                "--format=%(refname:short)",
                "refs/heads",
            ],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    if result.returncode != 0:
        return set()
    return {
        line.strip()
        for line in result.stdout.splitlines()
        if _SAFE_CYCLE_RE.fullmatch(line.strip())
    }


def _legacy_allocated_numbers(project_root: Path) -> set[int]:
    ledger = _legacy_ledger_path(project_root)
    if not ledger.is_file():
        return set()
    try:
        tokens = ledger.read_text(encoding="utf-8").split()
    except OSError:
        return set()
    return {int(token) for token in tokens if token.isdigit()}


class CycleAllocator:
    """Allocate never-reused cycle IDs under the project preparation lock."""

    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root).resolve()

    def _known_ids(self) -> set[str]:
        return (
            _read_allocations(self.project_root)
            | _directory_cycle_ids(paths.worktrees_home(self.project_root))
            | _directory_cycle_ids(paths.project_cycles_dir(self.project_root))
            | _git_branch_ids(self.project_root)
        )

    def allocate_locked(
        self,
        lock: ProjectLock,
        *,
        requested_name: str | None = None,
        template_id: str | None = None,
    ) -> str:
        if (
            not lock.held
            or lock.kind is not LockKind.PREPARATION
            or lock.project_root != self.project_root
        ):
            raise RuntimeError("alocação exige o preparation lock deste projeto")

        known = self._known_ids()
        allocated, stored_numbers = _read_allocation_ledger(self.project_root)
        legacy_numbers = _legacy_allocated_numbers(self.project_root)
        reserved_numbers = stored_numbers | legacy_numbers

        if requested_name is not None:
            cycle_id = validate_cycle_name(requested_name)
            number = _cycle_number(cycle_id)
            if cycle_id in known or (
                number is not None and number in reserved_numbers
            ):
                raise CycleAlreadyExists(f"ciclo já reservado: {cycle_id}")
        else:
            known_numbers = {
                number
                for cycle_id in known
                if (number := _cycle_number(cycle_id)) is not None
            } | reserved_numbers
            number = max(known_numbers, default=0) + 1
            base = f"cycle-{number:02d}"
            cycle_id = (
                f"{base}-{_template_slug(template_id)}" if template_id else base
            )
            while cycle_id in known:
                number += 1
                base = f"cycle-{number:02d}"
                cycle_id = (
                    f"{base}-{_template_slug(template_id)}"
                    if template_id
                    else base
                )

        allocated.add(cycle_id)
        allocated_number = _cycle_number(cycle_id)
        if allocated_number is not None:
            reserved_numbers.add(allocated_number)
        _write_allocations(self.project_root, allocated, reserved_numbers)
        return cycle_id

    def allocate(
        self,
        *,
        requested_name: str | None = None,
        template_id: str | None = None,
        timeout: float | None = None,
    ) -> str:
        with project_prep_lock(self.project_root, timeout=timeout) as lock:
            return self.allocate_locked(
                lock,
                requested_name=requested_name,
                template_id=template_id,
            )


@dataclass(frozen=True)
class CycleRecord:
    cycle_id: str
    worktree_path: Path
    state_path: Path
    state: Mapping[str, Any]
    status: str
    state_error: str | None = None

    @property
    def name(self) -> str:
        return self.cycle_id

    @property
    def worktree(self) -> Path:
        return self.worktree_path

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def ready(self) -> bool:
        return (
            self.state_error is None
            and self.state_path.is_file()
            and self.status not in {"preparing", "invalid"}
        )


def _read_cycle_record(worktree: Path) -> CycleRecord:
    state_path = worktree / "state" / "engine_state.yml"
    if not state_path.is_file():
        return CycleRecord(
            cycle_id=worktree.name,
            worktree_path=worktree,
            state_path=state_path,
            state={},
            status="preparing",
            state_error="state ainda não foi criado",
        )
    try:
        payload = yaml.safe_load(state_path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        return CycleRecord(
            cycle_id=worktree.name,
            worktree_path=worktree,
            state_path=state_path,
            state={},
            status="invalid",
            state_error=f"state inválido: {exc}",
        )
    if not isinstance(payload, dict):
        return CycleRecord(
            cycle_id=worktree.name,
            worktree_path=worktree,
            state_path=state_path,
            state={},
            status="invalid",
            state_error="state inválido: raiz deve ser mapping",
        )
    return CycleRecord(
        cycle_id=worktree.name,
        worktree_path=worktree,
        state_path=state_path,
        state=payload,
        status=str(payload.get("node_status") or "ready"),
    )


class CycleRegistry:
    """Inventory open worktrees and select cycles without recency fallbacks."""

    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root).resolve()

    def open_cycles(self, *, include_terminal: bool = True) -> tuple[CycleRecord, ...]:
        home = paths.worktrees_home(self.project_root)
        if not home.is_dir():
            return ()
        records = []
        for entry in home.iterdir():
            if not entry.is_dir() or not _SAFE_CYCLE_RE.fullmatch(entry.name):
                continue
            # A state file or Git worktree marker is concrete runtime evidence.
            if not (entry / ".git").is_file() and not (
                entry / "state" / "engine_state.yml"
            ).is_file():
                continue
            record = _read_cycle_record(entry)
            if include_terminal or not record.terminal:
                records.append(record)
        return tuple(sorted(records, key=_cycle_sort_key))

    def select(
        self,
        requested: str | None = None,
        *,
        include_terminal: bool = True,
    ) -> CycleRecord:
        records = self.open_cycles(include_terminal=include_terminal)
        if requested is not None:
            name = validate_cycle_name(requested)
            match = next((record for record in records if record.cycle_id == name), None)
            if match is None:
                available = ", ".join(record.cycle_id for record in records)
                suffix = f" Ciclos abertos: {available}" if available else ""
                raise CycleNotFoundError(f"ciclo não encontrado: {name}.{suffix}")
            if not match.ready:
                reason = match.state_error or f"status {match.status}"
                raise CycleNotReadyError(
                    f"ciclo {name} não está pronto: {reason}"
                )
            return match

        if not records:
            qualifier = " não terminal" if not include_terminal else ""
            raise NoCycleError(f"nenhum ciclo{qualifier} aberto")
        if len(records) > 1:
            raise AmbiguousCycleError(record.cycle_id for record in records)
        record = records[0]
        if not record.ready:
            reason = record.state_error or f"status {record.status}"
            raise CycleNotReadyError(
                f"ciclo {record.cycle_id} não está pronto: {reason}"
            )
        return record


def allocate_cycle(
    project_root: str | Path,
    requested_name: str | None = None,
    *,
    template_id: str | None = None,
    timeout: float | None = None,
) -> str:
    """Public adapter used by the CLI while preparing a run."""

    return CycleAllocator(project_root).allocate(
        requested_name=requested_name,
        template_id=template_id,
        timeout=timeout,
    )


def select_cycle(
    project_root: str | Path,
    requested: str | None = None,
    *,
    include_terminal: bool = True,
) -> CycleRecord:
    """Select exactly one open cycle, or fail with actionable ambiguity."""

    return CycleRegistry(project_root).select(
        requested,
        include_terminal=include_terminal,
    )
