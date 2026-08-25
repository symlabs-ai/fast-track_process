"""Intra-cycle parallel execution backed by isolated Git worktrees.

Every worktree is namespaced by cycle and node. A failed delegation, commit,
merge, or cleanup is intentionally left in place so no generated work is
silently discarded.
"""

from __future__ import annotations

import re
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any


@dataclass
class WorktreeResult:
    node_id: str
    branch: str
    worktree_path: str
    success: bool
    output: str
    files_created: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)


def _safe_ref_component(value: str) -> str:
    """Convert an engine identifier into one safe Git ref/path component."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-")
    return safe or "unnamed"


def _run_git(
    args: list[str],
    *,
    cwd: str | Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def create_worktree(
    node_id: str,
    project_root: str,
    base_branch: str = "main",
    *,
    cycle_id: str = "cycle-01",
    worktrees_root: str | Path | None = None,
) -> tuple[str, str]:
    """Create one atomically named branch/worktree for a parallel task."""
    root = Path(project_root).resolve()
    safe_cycle = _safe_ref_component(cycle_id)
    safe_node = _safe_ref_component(node_id)
    branch = f"ft-parallel/{safe_cycle}/{safe_node}"
    container = (
        Path(worktrees_root).resolve()
        if worktrees_root is not None
        else root.parent / ".ft-parallel-worktrees"
    )
    worktree = container / safe_cycle / safe_node

    branch_exists = _run_git(
        ["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=root,
    )
    if branch_exists.returncode == 0:
        raise RuntimeError(f"branch paralela já existe e foi preservada: {branch}")
    if branch_exists.returncode not in {0, 1}:
        raise RuntimeError(
            f"falha ao consultar branch paralela {branch}: "
            f"{branch_exists.stderr.strip()}"
        )
    if worktree.exists():
        raise RuntimeError(f"worktree paralelo já existe e foi preservado: {worktree}")

    worktree.parent.mkdir(parents=True, exist_ok=True)
    result = _run_git(
        ["worktree", "add", "-b", branch, str(worktree), base_branch],
        cwd=root,
    )
    if result.returncode != 0:
        # Git may have created a ref or directory before reporting an error.
        # Preserve either one for inspection instead of force-deleting it.
        raise RuntimeError(
            f"falha ao criar worktree {branch}: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return branch, str(worktree)


def remove_worktree(
    worktree_path: str,
    branch: str,
    project_root: str,
) -> tuple[bool, str]:
    """Remove a clean, merged worktree and branch without force."""
    root = Path(project_root).resolve()
    worktree = Path(worktree_path)
    if worktree.exists():
        removed = _run_git(["worktree", "remove", str(worktree)], cwd=root)
        if removed.returncode != 0:
            return (
                False,
                "worktree preservado porque a limpeza segura falhou: "
                f"{removed.stderr.strip() or removed.stdout.strip()}",
            )

    deleted = _run_git(["branch", "-d", branch], cwd=root)
    if deleted.returncode != 0:
        return (
            False,
            "branch preservada porque a exclusão segura falhou: "
            f"{deleted.stderr.strip() or deleted.stdout.strip()}",
        )

    for parent in (worktree.parent, worktree.parent.parent):
        try:
            parent.rmdir()
        except OSError:
            break
    return True, f"cleanup OK: {branch}"


def merge_branch(
    branch: str,
    project_root: str,
    squash: bool = False,
) -> tuple[bool, str]:
    """Merge one parallel branch, aborting conflicts but preserving its ref."""
    cmd = ["merge"]
    if squash:
        cmd.append("--squash")
    cmd.extend(["--no-ff", "-m", f"merge: ft-parallel branch {branch}", branch])
    result = _run_git(cmd, cwd=project_root)
    if result.returncode == 0:
        return True, f"merge OK: {branch}"

    aborted = _run_git(["merge", "--abort"], cwd=project_root)
    abort_note = ""
    if aborted.returncode != 0:
        abort_note = (
            "; merge --abort também falhou: "
            f"{aborted.stderr.strip() or aborted.stdout.strip()}"
        )
    detail = result.stderr.strip() or result.stdout.strip()
    return False, f"merge CONFLICT: {detail[:400]}{abort_note}"


def _normalise_output_path(raw: str) -> str:
    value = str(raw).replace("\\", "/").strip()
    while value.startswith("./"):
        value = value[2:]
    normalised = PurePosixPath(value).as_posix().rstrip("/")
    return normalised or "."


def outputs_overlap(
    node_a_outputs: list[str],
    node_b_outputs: list[str],
) -> list[tuple[str, str]]:
    """Return exact or ancestor/descendant output overlaps."""
    overlaps: list[tuple[str, str]] = []
    for raw_a in node_a_outputs:
        a = _normalise_output_path(raw_a)
        for raw_b in node_b_outputs:
            b = _normalise_output_path(raw_b)
            if (
                a == "."
                or b == "."
                or a == b
                or a.startswith(f"{b}/")
                or b.startswith(f"{a}/")
            ):
                overlaps.append((str(raw_a), str(raw_b)))
    return overlaps


def check_independence(
    node_a_outputs: list[str],
    node_b_outputs: list[str],
) -> bool:
    """Return whether two tasks have disjoint output path trees."""
    return not outputs_overlap(node_a_outputs, node_b_outputs)


class ParallelRunner:
    """Run independent tasks concurrently and merge them deterministically."""

    def __init__(
        self,
        project_root: str,
        max_slots: int = 2,
        *,
        cycle_id: str = "cycle-01",
        worktrees_root: str | Path | None = None,
    ):
        self.project_root = str(Path(project_root).resolve())
        self.max_slots = max(1, int(max_slots))
        self.cycle_id = cycle_id
        self.worktrees_root = worktrees_root
        self._semaphore = threading.Semaphore(self.max_slots)
        self._results: list[WorktreeResult] = []
        self._lock = threading.Lock()

    def run_parallel(
        self,
        tasks: list[dict[str, Any]],
        delegate_fn,
    ) -> list[WorktreeResult]:
        """Execute tasks concurrently, one isolated Git worktree per task."""
        for index, first in enumerate(tasks):
            for second in tasks[index + 1 :]:
                overlaps = outputs_overlap(
                    first.get("outputs", []),
                    second.get("outputs", []),
                )
                if overlaps:
                    raise ValueError(
                        "Tasks nao sao independentes: "
                        f"{first['node_id']} e {second['node_id']} possuem "
                        f"outputs sobrepostos: {overlaps}"
                    )

        base_result = _run_git(
            ["rev-parse", "--abbrev-ref", "HEAD"],
            cwd=self.project_root,
        )
        base = base_result.stdout.strip()
        if base_result.returncode != 0 or not base or base == "HEAD":
            raise RuntimeError(
                "fan-out paralelo exige uma branch Git ativa: "
                f"{base_result.stderr.strip() or base_result.stdout.strip()}"
            )

        with self._lock:
            self._results = []
        threads = [
            threading.Thread(
                target=self._run_one,
                args=(task, delegate_fn, base),
                name=f"ft-parallel-{_safe_ref_component(task['node_id'])}",
            )
            for task in tasks
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        with self._lock:
            return list(self._results)

    def _commit_successful_task(self, worktree_path: str, node_id: str) -> None:
        staged = _run_git(["add", "-A"], cwd=worktree_path)
        if staged.returncode != 0:
            raise RuntimeError(
                "falha ao preparar commit paralelo; worktree preservado: "
                f"{staged.stderr.strip() or staged.stdout.strip()}"
            )
        changed = _run_git(["diff", "--cached", "--quiet"], cwd=worktree_path)
        if changed.returncode == 0:
            return
        if changed.returncode != 1:
            raise RuntimeError(
                "falha ao inspecionar commit paralelo; worktree preservado: "
                f"{changed.stderr.strip() or changed.stdout.strip()}"
            )
        committed = _run_git(
            ["commit", "-m", f"ft-parallel: {node_id}"],
            cwd=worktree_path,
        )
        if committed.returncode != 0:
            raise RuntimeError(
                "falha no commit paralelo; worktree preservado: "
                f"{committed.stderr.strip() or committed.stdout.strip()}"
            )

    def _run_one(
        self,
        task: dict[str, Any],
        delegate_fn,
        base_branch: str,
    ) -> None:
        node_id = task["node_id"]
        branch: str | None = None
        worktree_path: str | None = None

        with self._semaphore:
            try:
                branch, worktree_path = create_worktree(
                    node_id,
                    self.project_root,
                    base_branch,
                    cycle_id=self.cycle_id,
                    worktrees_root=self.worktrees_root,
                )
                result = delegate_fn(
                    task=task["task_prompt"],
                    project_root=worktree_path,
                    allowed_paths=task.get("allowed_paths"),
                    **task.get("delegate_kwargs", {}),
                )
                if result.success:
                    self._commit_successful_task(worktree_path, node_id)
                wt_result = WorktreeResult(
                    node_id=node_id,
                    branch=branch,
                    worktree_path=worktree_path,
                    success=result.success,
                    output=result.output,
                    files_created=result.files_created,
                    files_modified=result.files_modified,
                )
            except Exception as exc:
                preserved = ""
                if worktree_path:
                    preserved = (
                        f" Worktree preservado em {worktree_path} (branch {branch})."
                    )
                wt_result = WorktreeResult(
                    node_id=node_id,
                    branch=branch or "",
                    worktree_path=worktree_path or "",
                    success=False,
                    output=f"{exc}{preserved}",
                )

        with self._lock:
            self._results.append(wt_result)

    def merge_all(
        self,
        results: list[WorktreeResult],
    ) -> list[tuple[str, bool, str]]:
        """Merge successful branches and clean only after a successful merge."""
        merge_results: list[tuple[str, bool, str]] = []
        for result in results:
            if not result.success or not result.branch:
                continue
            ok, detail = merge_branch(result.branch, self.project_root)
            if ok and result.worktree_path:
                cleaned, cleanup_detail = remove_worktree(
                    result.worktree_path,
                    result.branch,
                    self.project_root,
                )
                if not cleaned:
                    detail = f"{detail}; {cleanup_detail}"
            merge_results.append((result.node_id, ok, detail))
        return merge_results
