"""Serialized close primitive for concurrent run worktrees."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, TypeVar

from ft.runs.locking import close_merge_lock
from ft.runs.registry import CycleRecord, select_cycle


T = TypeVar("T")


@contextmanager
def serialized_close(
    project_root: str | Path,
    cycle_id: str,
    *,
    timeout: float | None = None,
) -> Iterator[CycleRecord]:
    """Hold the per-project merge lock and re-resolve the requested cycle.

    Re-resolution happens after acquiring the lock.  A close waiting behind a
    different cycle therefore cannot act on a worktree that the first close
    removed while it was queued.
    """

    with close_merge_lock(project_root, timeout=timeout):
        yield select_cycle(project_root, cycle_id, include_terminal=True)


class CloseCoordinator:
    """Execute one merge/close callback inside the serialized close section."""

    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root).resolve()

    def run(
        self,
        cycle_id: str,
        operation: Callable[[CycleRecord], T],
        *,
        timeout: float | None = None,
    ) -> T:
        with serialized_close(
            self.project_root,
            cycle_id,
            timeout=timeout,
        ) as cycle:
            return operation(cycle)
