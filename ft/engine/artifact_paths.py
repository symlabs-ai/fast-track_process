"""Canonical project artifact paths with read-only legacy compatibility."""

from __future__ import annotations

from pathlib import Path


TECH_STACK_PATH = Path("docs/TECH_STACK.md")
LEGACY_TECH_STACK_PATHS = (Path("docs/tech_stack.md"),)


def resolve_tech_stack_path(project_root: str | Path = ".") -> Path:
    """Return the canonical tech-stack path, or an existing legacy alias.

    New writes and process declarations must use :data:`TECH_STACK_PATH`.
    Lowercase aliases remain readable so existing project forks can migrate
    without losing context.
    """

    root = Path(project_root)
    canonical = root / TECH_STACK_PATH
    if canonical.exists():
        return canonical
    for legacy in LEGACY_TECH_STACK_PATHS:
        candidate = root / legacy
        if candidate.exists():
            return candidate
    return canonical
