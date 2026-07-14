"""Regressão: batch deve respawnar worker morto em plena delegação LLM.

Worker morto (crash, pkill externo) deixa o state do ciclo em ``delegated``
com lock de PID morto. Sem a detecção de órfã, o resume mantinha a feature
``running`` sem subprocesso e o batch esperava para sempre.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from ft.cli import feature_parallel as fp
from ft.engine import feature_batch as fb
from ft.engine import paths


def _write_cycle_state(
    root: Path,
    cycle_name: str,
    *,
    node_status: str,
    lock_pid: int | None,
) -> None:
    state_dir = paths.worktrees_home(root) / cycle_name / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "current_node": "feature.discovery",
        "node_status": node_status,
    }
    if lock_pid is not None:
        payload["_lock"] = {"owner": "ft_engine", "pid": lock_pid}
    (state_dir / "engine_state.yml").write_text(
        yaml.dump(payload), encoding="utf-8"
    )


def _dead_pid() -> int:
    pid = os.spawnlp(os.P_NOWAIT, "true", "true")
    os.waitpid(pid, 0)
    return pid


def _feature(cycle_name: str, status: str = "running") -> fb.BatchFeature:
    return fb.BatchFeature(
        feature_id="F-01",
        demand="d",
        cycle_name=cycle_name,
        status=status,
    )


def test_delegated_with_dead_lock_is_orphaned(tmp_path, monkeypatch):
    monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
    root = tmp_path / "proj"
    root.mkdir()
    _write_cycle_state(root, "cycle-01", node_status="delegated", lock_pid=_dead_pid())

    assert fp._cycle_delegation_is_orphaned(root, "cycle-01") is True


def test_delegated_with_live_lock_is_not_orphaned(tmp_path, monkeypatch):
    monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
    root = tmp_path / "proj"
    root.mkdir()
    _write_cycle_state(root, "cycle-01", node_status="delegated", lock_pid=os.getpid())

    assert fp._cycle_delegation_is_orphaned(root, "cycle-01") is False


def test_ready_state_is_not_orphaned(tmp_path, monkeypatch):
    monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
    root = tmp_path / "proj"
    root.mkdir()
    _write_cycle_state(root, "cycle-01", node_status="ready", lock_pid=None)

    assert fp._cycle_delegation_is_orphaned(root, "cycle-01") is False


def test_reconcile_respawns_orphaned_delegation(tmp_path, monkeypatch):
    monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
    root = tmp_path / "proj"
    root.mkdir()
    _write_cycle_state(root, "cycle-01", node_status="delegated", lock_pid=_dead_pid())
    feature = _feature("cycle-01")

    changed = fp._reconcile_external_idle_transition(root, feature)

    assert changed is True
    assert feature.status == "setup"


def test_reconcile_leaves_externally_driven_delegation_alone(tmp_path, monkeypatch):
    monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
    root = tmp_path / "proj"
    root.mkdir()
    _write_cycle_state(root, "cycle-01", node_status="delegated", lock_pid=os.getpid())
    feature = _feature("cycle-01")

    changed = fp._reconcile_external_idle_transition(root, feature)

    assert changed is False
    assert feature.status == "running"
