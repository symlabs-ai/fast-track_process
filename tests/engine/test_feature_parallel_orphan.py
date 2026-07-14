"""Regressão: batch deve respawnar worker morto em plena delegação LLM.

Worker morto (crash, pkill externo) deixa o state do ciclo em ``delegated``
com lock de PID morto. Sem a detecção de órfã, o resume mantinha a feature
``running`` sem subprocesso e o batch esperava para sempre.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
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
    lock_pid_start: str | None = None,
) -> None:
    state_dir = paths.worktrees_home(root) / cycle_name / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "current_node": "feature.discovery",
        "node_status": node_status,
    }
    if lock_pid is not None:
        payload["_lock"] = {"owner": "ft_engine", "pid": lock_pid}
        if lock_pid_start is not None:
            payload["_lock"]["pid_start"] = lock_pid_start
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


def test_delegated_with_recycled_pid_identity_is_orphaned(tmp_path, monkeypatch):
    monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
    root = tmp_path / "proj"
    root.mkdir()
    _write_cycle_state(
        root,
        "cycle-01",
        node_status="delegated",
        lock_pid=os.getpid(),
        lock_pid_start="not-the-current-process",
    )

    assert fp._cycle_delegation_is_orphaned(root, "cycle-01") is True


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


def test_reaper_keeps_delegated_cycle_owned_by_external_driver_running(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
    root = tmp_path / "proj"
    root.mkdir()
    cycle_name = "cycle-01"
    _write_cycle_state(
        root,
        cycle_name,
        node_status="delegated",
        lock_pid=os.getpid(),
    )
    feature = _feature(cycle_name, status="setup")
    batch = fb.FeatureBatch(
        batch_id="batch-01",
        project_root=str(root),
        template="feature",
        features=[feature],
        waves=[[feature.feature_id]],
        status="running",
        max_parallel=1,
    )

    class FinishedWorker:
        def poll(self):
            return 0

    class StopWave(RuntimeError):
        pass

    blocked_calls: list[str] = []
    sleeps = 0

    def stop_after_reap(_seconds: float) -> None:
        nonlocal sleeps
        sleeps += 1
        if sleeps == 2:
            raise StopWave

    def handle_blocked(_batch, blocked_feature, _args):
        blocked_calls.append(blocked_feature.feature_id)
        return None

    monkeypatch.setattr(fp, "_spawn_continue", lambda *_args: FinishedWorker())
    monkeypatch.setattr(fp, "_handle_blocked", handle_blocked)
    monkeypatch.setattr(fp, "_print_board", lambda *_args: None)
    monkeypatch.setattr(fp.fb, "save_batch", lambda _batch: None)
    monkeypatch.setattr(fp.time, "sleep", stop_after_reap)

    with pytest.raises(StopWave):
        fp._run_wave(batch, object())

    assert feature.status == "running"
    assert blocked_calls == []
