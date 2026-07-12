"""Tests for BL-15: RunMode — isolated vs continuous.

Covers: _resolve_run_mode, cmd_run isolated (default), cmd_run continuous,
CycleManager advance on continuous re-run, _find_latest_state with continuous.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from ft.engine import paths
import yaml


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_process_yaml(path: Path) -> Path:
    content = {
        "id": "test_process",
        "version": "1.0.0",
        "title": "Test Process",
        "nodes": [
            {"id": "start", "type": "build", "title": "Start",
             "executor": "python", "outputs": ["docs/out.md"], "next": "end"},
            {"id": "end", "type": "end", "title": "End"},
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(content, default_flow_style=False))
    return path


def _create_environment_yml(project: Path, run_mode: str = "isolated") -> None:
    env_file = project / ".ft" / "process" / "environment.yml"
    env_file.parent.mkdir(parents=True, exist_ok=True)
    env_file.write_text(f"run_mode: {run_mode}\n")


def run_ft(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    repo_root = str(Path(__file__).resolve().parent.parent.parent)
    env = {**os.environ, "PYTHONPATH": repo_root, "FT_SKIP_HEALTH_CHECK": "1"}
    return subprocess.run(
        [sys.executable, "-m", "ft.cli.main"] + args,
        capture_output=True, text=True, cwd=cwd, env=env,
    )


# ---------------------------------------------------------------------------
# _resolve_run_mode
# ---------------------------------------------------------------------------

class TestResolveRunMode:
    def test_default_is_isolated(self, tmp_path):
        from ft.cli.main import _resolve_run_mode
        assert _resolve_run_mode(tmp_path) == "isolated"

    def test_reads_from_environment_yml(self, tmp_path):
        from ft.cli.main import _resolve_run_mode
        _create_environment_yml(tmp_path, "continuous")
        assert _resolve_run_mode(tmp_path) == "continuous"

    def test_isolated_explicit(self, tmp_path):
        from ft.cli.main import _resolve_run_mode
        _create_environment_yml(tmp_path, "isolated")
        assert _resolve_run_mode(tmp_path) == "isolated"


# ---------------------------------------------------------------------------
# _find_latest_state — continuous priority
# ---------------------------------------------------------------------------

class TestFindLatestStateContinuous:
    def test_continuous_state_takes_priority(self, tmp_path):
        from ft.cli.main import _find_latest_state
        # Create both continuous and isolated state
        cont = paths.continuous_state_path(tmp_path)
        cont.parent.mkdir(parents=True)
        cont.write_text(
            "current_node: continuous.start\n"
            "node_status: blocked\n"
            "blocked_reason: aguardando usuário\n"
        )
        iso = paths.worktrees_home(tmp_path) / "cycle-01" / "state" / "engine_state.yml"
        iso.parent.mkdir(parents=True)
        iso.write_text(
            "current_node: isolated.start\n"
            "node_status: blocked\n"
            "blocked_reason: aguardando usuário\n"
        )
        assert _find_latest_state(tmp_path) == cont

    def test_falls_back_to_isolated_when_no_continuous(self, tmp_path):
        from ft.cli.main import _find_latest_state
        iso = paths.worktrees_home(tmp_path) / "cycle-01" / "state" / "engine_state.yml"
        iso.parent.mkdir(parents=True)
        iso.write_text(
            "current_node: isolated.start\n"
            "node_status: blocked\n"
            "blocked_reason: aguardando usuário\n"
        )
        assert _find_latest_state(tmp_path) == iso

    def test_active_isolated_wins_over_inactive_continuous(self, tmp_path):
        from ft.cli.main import _find_latest_state

        cont = paths.continuous_state_path(tmp_path)
        cont.parent.mkdir(parents=True)
        cont.write_text("current_node: null\nnode_status: ready\n")
        iso = paths.worktrees_home(tmp_path) / "cycle-10" / "state" / "engine_state.yml"
        iso.parent.mkdir(parents=True)
        iso.write_text(
            "current_node: feature.discovery\n"
            "node_status: blocked\n"
            "completed_nodes: []\n"
        )

        assert _find_latest_state(tmp_path) == iso

    def test_completed_isolated_wins_over_pristine_continuous(
        self, tmp_path, monkeypatch
    ):
        """Um state vazio legado não pode ocultar o ciclo pronto para close."""
        from ft.cli.main import _find_latest_state

        monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
        project = tmp_path / "project"
        project.mkdir()
        continuous = paths.continuous_state_path(project)
        continuous.parent.mkdir(parents=True)
        continuous.write_text(
            "process_id: removed_process\n"
            "current_node: null\n"
            "node_status: ready\n"
            "completed_nodes: []\n"
            "metrics:\n"
            "  steps_completed: 0\n"
            "  steps_total: 54\n"
        )
        completed = (
            paths.worktrees_home(project)
            / "cycle-10"
            / "state"
            / "engine_state.yml"
        )
        completed.parent.mkdir(parents=True)
        completed.write_text(
            "process_id: feature\n"
            "current_node: null\n"
            "node_status: done\n"
            "completed_nodes: [feature.end]\n"
            "metrics:\n"
            "  steps_completed: 11\n"
            "  steps_total: 11\n"
        )

        assert _find_latest_state(project) == completed

    def test_completed_worktree_wins_over_terminal_continuous(
        self, tmp_path, monkeypatch
    ):
        """Close deve priorizar a worktree que ainda precisa de merge."""
        from ft.cli.main import _find_latest_state

        monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
        project = tmp_path / "project"
        project.mkdir()
        continuous = paths.continuous_state_path(project)
        continuous.parent.mkdir(parents=True)
        continuous.write_text(
            "process_id: old_continuous\n"
            "current_node: null\n"
            "node_status: done\n"
            "completed_nodes: [old.end]\n"
        )
        completed = (
            paths.worktrees_home(project)
            / "cycle-10"
            / "state"
            / "engine_state.yml"
        )
        completed.parent.mkdir(parents=True)
        completed.write_text(
            "process_id: feature\n"
            "current_node: null\n"
            "node_status: done\n"
            "completed_nodes: [feature.end]\n"
        )

        assert _find_latest_state(project) == completed


# ---------------------------------------------------------------------------
# ft run — isolated mode (default)
# ---------------------------------------------------------------------------

class TestRunIsolated:
    def test_creates_external_worktree(self, tmp_path):
        """BL-20: isolated mode creates cycle in ~/.ft/worktrees/, not runs/."""
        _create_process_yaml(paths.project_process_file(tmp_path))
        from ft.engine.layout import ensure_project_layout
        ensure_project_layout(tmp_path)
        run_ft(["run", str(tmp_path)], cwd=tmp_path)
        wt_home = paths.worktrees_home(tmp_path)
        assert wt_home.is_dir() and any(wt_home.iterdir())

    def test_output_shows_isolated(self, tmp_path):
        _create_process_yaml(paths.project_process_file(tmp_path))
        from ft.engine.layout import ensure_project_layout
        ensure_project_layout(tmp_path)
        result = run_ft(["run", str(tmp_path)], cwd=tmp_path)
        output = result.stdout + result.stderr
        assert "isolated" in output.lower()


# ---------------------------------------------------------------------------
# ft run — continuous mode
# ---------------------------------------------------------------------------

class TestRunContinuous:
    def test_creates_state_outside_project_root(self, tmp_path):
        _create_process_yaml(paths.project_process_file(tmp_path))
        from ft.engine.layout import ensure_project_layout
        ensure_project_layout(tmp_path)
        _create_environment_yml(tmp_path, "continuous")
        run_ft(["run", str(tmp_path)], cwd=tmp_path)
        assert paths.continuous_state_path(tmp_path).exists()
        assert not (tmp_path / "state").exists()

    def test_does_not_create_runs_dir_entries(self, tmp_path):
        _create_process_yaml(paths.project_process_file(tmp_path))
        from ft.engine.layout import ensure_project_layout
        ensure_project_layout(tmp_path)
        _create_environment_yml(tmp_path, "continuous")
        run_ft(["run", str(tmp_path)], cwd=tmp_path)
        runs_dir = tmp_path / "runs"
        if runs_dir.is_dir():
            run_dirs = [d for d in runs_dir.iterdir() if d.is_dir() and d.name.isdigit()]
            assert len(run_dirs) == 0

    def test_output_shows_continuous(self, tmp_path):
        _create_process_yaml(paths.project_process_file(tmp_path))
        from ft.engine.layout import ensure_project_layout
        ensure_project_layout(tmp_path)
        _create_environment_yml(tmp_path, "continuous")
        result = run_ft(["run", str(tmp_path)], cwd=tmp_path)
        output = result.stdout + result.stderr
        assert "continuous" in output.lower()


# ---------------------------------------------------------------------------
# CycleManager advance on continuous re-run
# ---------------------------------------------------------------------------

class TestCycleManagerAdvance:
    def test_second_run_advances_cycle(self, tmp_path):
        _create_process_yaml(paths.project_process_file(tmp_path))
        from ft.engine.layout import ensure_project_layout
        ensure_project_layout(tmp_path)
        _create_environment_yml(tmp_path, "continuous")
        # First run
        run_ft(["run", str(tmp_path)], cwd=tmp_path)
        state_path = paths.continuous_state_path(tmp_path)
        assert state_path.exists()
        with open(state_path) as f:
            state1 = yaml.safe_load(f)
        assert state1["current_cycle"] == "cycle-01"

        # Second run — should advance to cycle-02
        run_ft(["run", str(tmp_path)], cwd=tmp_path)
        with open(state_path) as f:
            state2 = yaml.safe_load(f)
        assert state2["current_cycle"] == "cycle-02"

    def test_cycle_manager_tracks_history(self, tmp_path):
        """CycleManager.advance_cycle writes cycle_history to the raw YAML."""
        from ft.engine.cycle_manager import CycleManager
        state_path = tmp_path / "state" / "engine_state.yml"
        state_path.parent.mkdir(parents=True)
        state_path.write_text("current_cycle: cycle-01\n")
        cm = CycleManager(state_path)
        cm.advance_cycle()
        assert cm.current_cycle() == "cycle-02"
        assert "cycle-01" in cm.cycle_history()
