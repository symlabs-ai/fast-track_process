"""Regressões de ownership do state e coordenação de process update."""

from __future__ import annotations

from argparse import Namespace
from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import sys

import pytest

from ft.cli import main as cli_main
from ft.engine import layout
from ft.engine import process_update as pu
from ft.engine.state import StateLockError, StateManager, lock_owner_is_alive


def test_state_claim_rejects_second_live_process(tmp_path: Path) -> None:
    """Somente um runner pode possuir o mesmo state por vez."""
    state_path = tmp_path / "project" / "state" / "engine_state.yml"
    manager = StateManager(state_path)
    manager.init_from_graph({"id": "feature"}, "feature.implement", 1)
    manager.release_lock()

    child_code = "\n".join(
        [
            "import sys",
            "from ft.engine.state import StateManager",
            "manager = StateManager(sys.argv[1])",
            "manager.claim()",
            "print('claimed', flush=True)",
            "sys.stdin.readline()",
            "manager.release_lock()",
        ]
    )
    env = os.environ.copy()
    repo_root = Path(__file__).resolve().parents[2]
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, [str(repo_root), env.get("PYTHONPATH", "")])
    )
    child = subprocess.Popen(
        [sys.executable, "-c", child_code, str(state_path)],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "claimed"

        with pytest.raises(StateLockError, match="ja esta rodando"):
            StateManager(state_path).claim()
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
            pytest.fail(f"processo auxiliar de claim não terminou: {stderr}")

    assert child.returncode == 0, stderr

    recovered = StateManager(state_path)
    recovered.claim()
    recovered.release_lock()


def test_pid_identity_mismatch_is_not_a_live_owner(tmp_path: Path) -> None:
    """Um PID reciclado não conserva ownership de uma execução antiga."""
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
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "ready"
        stale_lock = {
            "owner": "ft_engine",
            "pid": child.pid,
            "pid_start": "definitely-not-this-process",
        }

        assert not lock_owner_is_alive(stale_lock)

        state_path = tmp_path / "cycle-01" / "state" / "engine_state.yml"
        manager = StateManager(state_path)
        manager.init_from_graph({"id": "bug"}, "bug.fix", 1)
        manager.release_lock()
        raw = state_path.read_text(encoding="utf-8")
        state_path.write_text(
            raw.replace("_lock: null", "_lock:\n  owner: ft_engine\n"
                        f"  pid: {child.pid}\n"
                        "  pid_start: definitely-not-this-process"),
            encoding="utf-8",
        )

        claimed = StateManager(state_path)
        claimed.claim()
        claimed.release_lock()

        assert child.poll() is None, "PID reciclado não pode manter o state preso"
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


def test_diverged_confirmation_does_not_hold_manifest_write_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A revisão humana de process update não bloqueia runners paralelos."""
    root = tmp_path / "project"
    local_dir = root / ".ft" / "process" / "feature"
    local_dir.mkdir(parents=True)
    (root / ".ft" / "manifest.yml").write_text(
        "schema_version: 3\nprocesses: {}\n",
        encoding="utf-8",
    )
    state = pu.ProcessDriftState(
        name="feature",
        template_id="feature",
        entrypoint="run",
        local_dir=local_dir,
        local_process=local_dir / "process.yml",
        template_dir=tmp_path / "templates" / "feature",
        state=pu.STATE_DIVERGED,
        local_digest="sha256:local",
        global_digest="sha256:global",
        base_digest="sha256:base",
    )
    real_lock = layout._manifest_write_lock
    lock_depth = 0

    @contextmanager
    def tracked_lock(project_root: str | Path):
        nonlocal lock_depth
        with real_lock(project_root):
            lock_depth += 1
            try:
                yield
            finally:
                lock_depth -= 1

    def build_staging(_state, staging: Path) -> pu.MergeResult:
        staging.mkdir(parents=True)
        (staging / "process.yml").write_text(
            "id: feature\nnodes: []\n",
            encoding="utf-8",
        )
        return pu.MergeResult(staging_dir=staging, changed=["process.yml"])

    prompted = False

    def reject_at_prompt(_prompt: str) -> bool:
        nonlocal prompted
        prompted = True
        assert lock_depth == 0, "prompt humano manteve o lock do projeto"
        return False

    monkeypatch.setattr(cli_main, "find_project_root", lambda: root)
    monkeypatch.setattr(cli_main, "_drift_scan", lambda *_args: [state])
    monkeypatch.setattr(cli_main, "_process_update_runtime_guard", lambda *_args: [])
    monkeypatch.setattr(cli_main, "_validate_staged_process", lambda _path: (True, ""))
    monkeypatch.setattr(cli_main, "_print_staged_diff", lambda *_args: None)
    monkeypatch.setattr(cli_main, "_confirm", reject_at_prompt)
    monkeypatch.setattr(layout, "_manifest_write_lock", tracked_lock)
    monkeypatch.setattr(pu, "ensure_base_snapshot", lambda _state: None)
    monkeypatch.setattr(pu, "build_merge_staging", build_staging)

    with pytest.raises(SystemExit) as excinfo:
        cli_main.cmd_process_update(Namespace(name="feature"))

    assert excinfo.value.code == 1
    assert prompted
