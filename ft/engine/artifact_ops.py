"""Safe filesystem operations for process-declared artifacts."""

from __future__ import annotations

from pathlib import Path
import shutil
from typing import Iterable


def remove_declared_outputs(
    work_root: str | Path,
    outputs: Iterable[str],
) -> None:
    """Remove only outputs contained by the worktree, ignoring absent paths."""
    root = Path(work_root).resolve()
    for output in outputs:
        target = (root / output).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            continue
        try:
            if target.is_file() or target.is_symlink():
                target.unlink()
            elif target.is_dir():
                shutil.rmtree(target)
        except OSError:
            # Regeneration will fail through its validator with the precise
            # artifact path; cleanup must never escape the declared scope.
            continue
