"""Regressões de atomicidade dos estados de ciclo."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import threading

import pytest
import yaml

from ft.engine.cycle_manager import CycleManager
from ft.engine.layout import _manifest_write_lock
from ft.engine.state import (
    StateLockError,
    StateManager,
    process_start_identity,
)


def _join(thread: threading.Thread) -> None:
    thread.join(timeout=2)
    assert not thread.is_alive(), f"thread {thread.name} não terminou"


def test_release_lock_serializes_read_check_write(tmp_path, monkeypatch):
    """Uma mutação concorrente não pode entrar no meio do release."""
    state_path = tmp_path / "project" / "state" / "engine_state.yml"
    manager = StateManager(state_path)
    manager.init_from_graph({"id": "feature"}, "feature.implement", 3)

    release_read = threading.Event()
    allow_release = threading.Event()
    writer_done = threading.Event()
    original_check_lock = manager._check_lock

    def delayed_check(raw):
        release_read.set()
        assert allow_release.wait(timeout=2)
        original_check_lock(raw)

    monkeypatch.setattr(manager, "_check_lock", delayed_check)

    def release() -> None:
        manager.release_lock()

    def add_future_field() -> None:
        with _manifest_write_lock(state_path):
            raw = yaml.safe_load(state_path.read_text(encoding="utf-8"))
            raw["future_field"] = {"preserved": True}
            manager._write_raw_locked(raw)
        writer_done.set()

    release_thread = threading.Thread(target=release, name="release-lock")
    writer_thread = threading.Thread(target=add_future_field, name="state-writer")
    release_thread.start()
    assert release_read.wait(timeout=2)
    writer_thread.start()
    try:
        assert not writer_done.wait(timeout=0.1)
    finally:
        allow_release.set()

    _join(release_thread)
    _join(writer_thread)
    persisted = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    assert persisted["_lock"] is None
    assert persisted["future_field"] == {"preserved": True}


def test_advance_cycle_serializes_the_full_read_modify_write(tmp_path, monkeypatch):
    """Dois avanços concorrentes devem produzir cycle-03, sem lost update."""
    state_path = tmp_path / "project" / "state" / "engine_state.yml"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        yaml.safe_dump(
            {
                "current_cycle": "cycle-01",
                "cycle_history": [],
                "metrics": {"steps_completed": 4, "tokens_used": 99},
                "future_field": "preservar",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    first_manager = CycleManager(state_path)
    second_manager = CycleManager(state_path)
    first_read = threading.Event()
    allow_first = threading.Event()
    second_read = threading.Event()
    original_load = CycleManager._load_raw

    def controlled_load(self):
        raw = original_load(self)
        if threading.current_thread().name == "advance-one":
            first_read.set()
            assert allow_first.wait(timeout=2)
        elif threading.current_thread().name == "advance-two":
            second_read.set()
        return raw

    monkeypatch.setattr(CycleManager, "_load_raw", controlled_load)
    first_thread = threading.Thread(
        target=first_manager.advance_cycle,
        name="advance-one",
    )
    second_thread = threading.Thread(
        target=second_manager.advance_cycle,
        name="advance-two",
    )
    first_thread.start()
    assert first_read.wait(timeout=2)
    second_thread.start()
    try:
        assert not second_read.wait(timeout=0.1)
    finally:
        allow_first.set()

    _join(first_thread)
    _join(second_thread)
    persisted = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    assert persisted["current_cycle"] == "cycle-03"
    assert persisted["cycle_history"] == ["cycle-01", "cycle-02"]
    assert persisted["metrics"] == {"steps_completed": 0, "tokens_used": 99}
    assert persisted["future_field"] == "preservar"


def test_cycle_manager_refuses_to_advance_state_owned_by_live_process(
    tmp_path: Path,
) -> None:
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys; print('ready', flush=True); sys.stdin.readline()",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    state_path = tmp_path / "project" / "state" / "engine_state.yml"
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "ready"
        identity = process_start_identity(child.pid)
        assert identity is not None
        initial = {
            "current_cycle": "cycle-01",
            "current_node": "feature.implement",
            "node_status": "delegated",
            "cycle_history": [],
            "metrics": {"steps_completed": 2, "steps_total": 3},
            "_lock": {
                "owner": "ft_engine",
                "pid": child.pid,
                "pid_start": identity,
            },
        }
        state_path.parent.mkdir(parents=True)
        state_path.write_text(
            yaml.safe_dump(initial, sort_keys=False),
            encoding="utf-8",
        )

        with pytest.raises(StateLockError, match="ja esta rodando"):
            CycleManager(state_path).advance_cycle()

        assert yaml.safe_load(state_path.read_text(encoding="utf-8")) == initial
    finally:
        if child.poll() is None and child.stdin is not None:
            try:
                child.stdin.write("\n")
                child.stdin.flush()
            except BrokenPipeError:
                pass
        try:
            _stdout, stderr = child.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            _stdout, stderr = child.communicate(timeout=5)
            pytest.fail(f"processo auxiliar não terminou: {stderr}")
    assert child.returncode == 0, stderr
