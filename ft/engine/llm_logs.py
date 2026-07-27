"""Persistent log references shared by runners and out-of-band delegations."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Protocol


class StateLogStore(Protocol):
    """Small state-manager surface required by external delegations."""

    path: Path

    def load(self) -> Any: ...

    def save(self) -> None: ...


def display_log_path(project_root: str | Path, path: str | Path) -> str:
    """Return a project-relative path when the log lives below the project."""
    resolved = Path(path).resolve()
    try:
        return str(resolved.relative_to(Path(project_root).resolve()))
    except ValueError:
        return str(resolved)


def build_llm_log_path(
    state_path: str | Path,
    node_id: str,
    phase: str,
    *,
    engine: str,
    timestamp: datetime | None = None,
) -> Path:
    """Build the stable log path used by every delegated LLM invocation."""
    safe_node = node_id.replace("/", "-")
    safe_phase = phase.replace("/", "-")
    stamp = (timestamp or datetime.now()).strftime("%Y%m%d-%H%M%S")
    suffix = ".jsonl" if engine == "codex" else ".log"
    return Path(state_path).parent / "llm_logs" / (
        f"{stamp}__{safe_node}__{safe_phase}{suffix}"
    )


def activate_external_llm_log(
    state_store: StateLogStore,
    project_root: str | Path,
    node_id: str,
    phase: str,
    *,
    engine: str,
) -> Path:
    """Persist an active log reference for a delegation outside ``StepRunner``."""
    path = build_llm_log_path(
        state_store.path,
        node_id,
        phase,
        engine=engine,
    )
    relative = display_log_path(project_root, path)
    state = state_store.load()
    state.active_llm_log = relative
    state.last_llm_log = relative
    state_store.save()
    return path


def clear_external_llm_log(
    state_store: StateLogStore,
    *,
    expected_path: str | Path | None = None,
    project_root: str | Path | None = None,
) -> None:
    """Clear the active reference without touching the last completed log."""
    state = state_store.load()
    active = getattr(state, "active_llm_log", None)
    if not active:
        return
    if expected_path is not None:
        expected = (
            display_log_path(project_root, expected_path)
            if project_root is not None
            else str(expected_path)
        )
        if active != expected:
            return
    state.active_llm_log = None
    state_store.save()
