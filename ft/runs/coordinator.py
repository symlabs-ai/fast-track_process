"""Transactional preparation of isolated, Git-backed Fast Track runs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any, Mapping

import yaml

from ft.engine import paths
from ft.engine.layout import process_digest as calculate_process_digest
from ft.runs.locking import project_prep_lock
from ft.runs.registry import CycleAllocator


class RunPreparationError(RuntimeError):
    pass


class GitWorkspaceRequired(RunPreparationError):
    pass


class DirtyWorkspaceError(RunPreparationError):
    pass


@dataclass(frozen=True)
class TemplatePin:
    """Immutable template identity captured by one run."""

    template_id: str
    process_path: str
    process_digest: str


@dataclass(frozen=True)
class PreparedRun:
    cycle_id: str
    project_root: Path
    worktree_path: Path
    branch: str
    state_path: Path
    base_commit: str
    pin: TemplatePin

    @property
    def name(self) -> str:
        return self.cycle_id

    @property
    def worktree(self) -> Path:
        return self.worktree_path


def _git(
    project_root: Path,
    *args: str,
    timeout: float = 30,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise GitWorkspaceRequired(f"git indisponível: {exc}") from exc


def _atomic_write_yaml(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = yaml.safe_dump(
        dict(data),
        allow_unicode=True,
        sort_keys=False,
    )
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
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _relative_process_path(project_root: Path, process_path: str | Path) -> str:
    raw = Path(process_path)
    candidate = raw if raw.is_absolute() else project_root / raw
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise RunPreparationError(f"processo local não encontrado: {candidate}") from exc
    try:
        relative = resolved.relative_to(project_root)
    except ValueError as exc:
        raise RunPreparationError(
            "processo do ciclo precisa pertencer ao repositório"
        ) from exc
    if not resolved.is_file() or relative.name != "process.yml":
        raise RunPreparationError(f"processo inválido: {relative}")
    if len(relative.parts) != 4 or relative.parts[:2] != (".ft", "process"):
        raise RunPreparationError(
            "processo precisa usar .ft/process/<template>/process.yml"
        )
    return relative.as_posix()


class RunCoordinator:
    """Create a branch, external worktree, and pinned state under one lock.

    The lock is released before this method returns.  Running the graph is a
    separate operation and therefore never serializes independent cycles.
    """

    _PINNED_FIELDS = frozenset(
        {
            "cycle_id",
            "current_cycle",
            "template_id",
            "process_path",
            "process_digest",
            "process_immutable",
            "base_commit",
            "worktree_branch",
        }
    )

    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root).resolve()

    def preflight(self) -> str:
        """Validate the owning checkout and return its current HEAD.

        Callers that materialize a template may invoke this while already
        holding :func:`project_prep_lock`, commit the resulting catalog change,
        and then call :meth:`prepare` in the same re-entrant critical section.
        ``prepare`` deliberately validates again after that commit.
        """

        root = self.project_root
        inside = _git(root, "rev-parse", "--is-inside-work-tree")
        top = _git(root, "rev-parse", "--show-toplevel")
        if inside.returncode != 0 or inside.stdout.strip() != "true":
            raise GitWorkspaceRequired(f"projeto não é um repositório Git: {root}")
        if top.returncode != 0 or Path(top.stdout.strip()).resolve() != root:
            raise GitWorkspaceRequired(
                "prepare a run a partir da raiz do checkout principal"
            )
        # The main checkout has a .git directory.  A linked worktree has a
        # .git file and must never become the owner of sibling run worktrees.
        if not (root / ".git").is_dir():
            raise GitWorkspaceRequired(
                "uma run não pode ser preparada de dentro de outra worktree"
            )
        head = _git(root, "rev-parse", "--verify", "HEAD")
        if head.returncode != 0 or not head.stdout.strip():
            raise GitWorkspaceRequired("repositório Git precisa ter um commit inicial")
        status = _git(root, "status", "--porcelain", "--untracked-files=normal")
        if status.returncode != 0:
            raise GitWorkspaceRequired(status.stderr.strip() or "git status falhou")
        if status.stdout.strip():
            raise DirtyWorkspaceError(
                "checkout precisa estar limpo antes de preparar uma run"
            )
        return head.stdout.strip()

    def prepare(
        self,
        *,
        template_id: str,
        process_path: str | Path,
        process_digest: str | None = None,
        requested_cycle: str | None = None,
        first_node: str | None = "__preparing__",
        initial_state: Mapping[str, Any] | None = None,
        timeout: float | None = None,
    ) -> PreparedRun:
        template = str(template_id).strip()
        if not template:
            raise ValueError("template_id é obrigatório")
        relative_process = _relative_process_path(self.project_root, process_path)
        source_process = self.project_root / relative_process
        digest = process_digest or calculate_process_digest(source_process)
        if not isinstance(digest, str) or not digest.strip():
            raise RunPreparationError("não foi possível calcular o digest do processo")
        extra_state = dict(initial_state or {})
        overlap = self._PINNED_FIELDS.intersection(extra_state)
        if overlap:
            fields = ", ".join(sorted(overlap))
            raise ValueError(f"initial_state não pode sobrescrever campos pinados: {fields}")

        worktree: Path | None = None
        branch: str | None = None
        with project_prep_lock(self.project_root, timeout=timeout) as lock:
            base_commit = self.preflight()

            tracked = _git(
                self.project_root,
                "ls-files",
                "--error-unmatch",
                "--",
                relative_process,
            )
            if tracked.returncode != 0:
                raise RunPreparationError(
                    f"processo precisa estar commitado antes da run: {relative_process}"
                )

            cycle_id = CycleAllocator(self.project_root).allocate_locked(
                lock,
                requested_name=requested_cycle,
                template_id=template,
            )
            branch = cycle_id
            worktree = paths.worktrees_home(self.project_root) / cycle_id
            worktree.parent.mkdir(parents=True, exist_ok=True)
            # ``git worktree add`` may execute post-checkout hooks.  Release
            # only the kernel flock while retaining a live logical lease:
            # sibling startups wait, hook descendants fail fast instead of
            # deadlocking their parent.
            with lock.suspend():
                add = _git(
                    self.project_root,
                    "worktree",
                    "add",
                    str(worktree),
                    "-b",
                    branch,
                    base_commit,
                    timeout=120,
                )
            if add.returncode != 0:
                self._rollback_worktree(worktree, branch)
                raise RunPreparationError(
                    "git worktree add falhou: "
                    + (add.stderr.strip() or add.stdout.strip())
                )

            try:
                pinned_process = worktree / relative_process
                if not pinned_process.is_file():
                    raise RunPreparationError(
                        f"snapshot do ciclo não contém {relative_process}"
                    )
                snapshot_digest = calculate_process_digest(pinned_process)
                if snapshot_digest != digest:
                    raise RunPreparationError(
                        "digest do processo mudou entre o checkout principal e a worktree"
                    )
                state_path = worktree / "state" / "engine_state.yml"
                state: dict[str, Any] = {
                    **extra_state,
                    "cycle_id": cycle_id,
                    "current_cycle": cycle_id,
                    "template_id": template,
                    "process_id": extra_state.get("process_id", template),
                    "process_path": relative_process,
                    "process_digest": digest,
                    "process_immutable": True,
                    "base_commit": base_commit,
                    "worktree_branch": branch,
                    "current_node": first_node,
                    "node_status": str(
                        extra_state.get("node_status")
                        or (
                            "preparing"
                            if first_node in (None, "__preparing__")
                            else "ready"
                        )
                    ),
                    "completed_nodes": list(extra_state.get("completed_nodes") or []),
                    "metrics": dict(
                        extra_state.get("metrics")
                        or {"steps_completed": 0, "steps_total": 0}
                    ),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "_lock": None,
                }
                _atomic_write_yaml(state_path, state)
            except BaseException:
                self._rollback_worktree(worktree, branch)
                worktree = None
                branch = None
                raise

        assert worktree is not None and branch is not None
        return PreparedRun(
            cycle_id=cycle_id,
            project_root=self.project_root,
            worktree_path=worktree,
            branch=branch,
            state_path=worktree / "state" / "engine_state.yml",
            base_commit=base_commit,
            pin=TemplatePin(
                template_id=template,
                process_path=relative_process,
                process_digest=digest,
            ),
        )

    def _rollback_worktree(self, worktree: Path, branch: str) -> None:
        _git(
            self.project_root,
            "worktree",
            "remove",
            "--force",
            str(worktree),
            timeout=60,
        )
        _git(self.project_root, "branch", "-D", branch)
