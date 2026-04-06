"""Tests for BL-14: Environment Hooks.

Covers: load_environment, get_hooks, run_hooks, hooks_all_passed,
integration with runner (on_init, on_node_start, on_node_end,
on_gate_pass, on_gate_fail, on_deliver).
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import yaml

from ft.engine.hooks import (
    load_environment,
    get_hooks,
    run_hooks,
    hooks_all_passed,
)


# ---------------------------------------------------------------------------
# load_environment
# ---------------------------------------------------------------------------

class TestLoadEnvironment:
    def test_returns_empty_when_no_file(self, tmp_path):
        result = load_environment(str(tmp_path))
        assert result == {}

    def test_loads_yaml(self, tmp_path):
        env_file = tmp_path / "process" / "environment.yml"
        env_file.parent.mkdir(parents=True)
        env_file.write_text("hooks:\n  on_init:\n    - ./scripts/setup.sh\n")
        result = load_environment(str(tmp_path))
        assert "hooks" in result
        assert result["hooks"]["on_init"] == ["./scripts/setup.sh"]

    def test_returns_empty_for_invalid_yaml(self, tmp_path):
        env_file = tmp_path / "process" / "environment.yml"
        env_file.parent.mkdir(parents=True)
        env_file.write_text("just a string")
        result = load_environment(str(tmp_path))
        assert result == {}

    def test_returns_empty_for_null_yaml(self, tmp_path):
        env_file = tmp_path / "process" / "environment.yml"
        env_file.parent.mkdir(parents=True)
        env_file.write_text("")
        result = load_environment(str(tmp_path))
        assert result == {}


# ---------------------------------------------------------------------------
# get_hooks
# ---------------------------------------------------------------------------

class TestGetHooks:
    def test_extracts_hooks_from_environment(self):
        env = {"hooks": {"on_init": ["./a.sh", "./b.sh"], "on_deliver": ["./c.sh"]}}
        hooks = get_hooks(env)
        assert hooks["on_init"] == ["./a.sh", "./b.sh"]
        assert hooks["on_deliver"] == ["./c.sh"]

    def test_returns_empty_when_no_hooks(self):
        assert get_hooks({}) == {}
        assert get_hooks({"gateway": {"url": "x"}}) == {}

    def test_handles_single_string(self):
        env = {"hooks": {"on_init": "./single.sh"}}
        hooks = get_hooks(env)
        assert hooks["on_init"] == ["./single.sh"]

    def test_handles_invalid_hooks(self):
        assert get_hooks({"hooks": "not_a_dict"}) == {}


# ---------------------------------------------------------------------------
# run_hooks
# ---------------------------------------------------------------------------

class TestRunHooks:
    def test_returns_empty_when_no_hooks(self, tmp_path):
        results = run_hooks("on_init", str(tmp_path))
        assert results == []

    def test_runs_successful_script(self, tmp_path):
        # Create environment.yml with hook
        env_dir = tmp_path / "process"
        env_dir.mkdir()
        scripts_dir = env_dir / "scripts"
        scripts_dir.mkdir()

        script = scripts_dir / "ok.sh"
        script.write_text("#!/bin/bash\necho 'hello'\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        env = {"hooks": {"on_init": ["./scripts/ok.sh"]}}
        results = run_hooks("on_init", str(tmp_path), environment=env)

        assert len(results) == 1
        assert results[0][0] == "./scripts/ok.sh"
        assert results[0][1] is True  # success

    def test_detects_failed_script(self, tmp_path):
        env_dir = tmp_path / "process"
        env_dir.mkdir()
        scripts_dir = env_dir / "scripts"
        scripts_dir.mkdir()

        script = scripts_dir / "fail.sh"
        script.write_text("#!/bin/bash\nexit 1\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        env = {"hooks": {"on_init": ["./scripts/fail.sh"]}}
        results = run_hooks("on_init", str(tmp_path), environment=env)

        assert len(results) == 1
        assert results[0][1] is False  # failed

    def test_handles_missing_script(self, tmp_path):
        (tmp_path / "process").mkdir()
        env = {"hooks": {"on_init": ["./scripts/nonexistent.sh"]}}
        results = run_hooks("on_init", str(tmp_path), environment=env)

        assert len(results) == 1
        assert results[0][1] is False
        assert "não encontrado" in results[0][2]

    def test_runs_multiple_scripts(self, tmp_path):
        env_dir = tmp_path / "process" / "scripts"
        env_dir.mkdir(parents=True)

        for name in ("a.sh", "b.sh"):
            s = env_dir / name
            s.write_text("#!/bin/bash\necho ok\n")
            s.chmod(s.stat().st_mode | stat.S_IEXEC)

        env = {"hooks": {"on_init": ["./scripts/a.sh", "./scripts/b.sh"]}}
        results = run_hooks("on_init", str(tmp_path), environment=env)

        assert len(results) == 2
        assert all(r[1] for r in results)

    def test_no_event_returns_empty(self, tmp_path):
        env = {"hooks": {"on_deliver": ["./x.sh"]}}
        results = run_hooks("on_init", str(tmp_path), environment=env)
        assert results == []


# ---------------------------------------------------------------------------
# hooks_all_passed
# ---------------------------------------------------------------------------

class TestHooksAllPassed:
    def test_all_passed(self):
        assert hooks_all_passed([("a.sh", True, "ok"), ("b.sh", True, "ok")])

    def test_one_failed(self):
        assert not hooks_all_passed([("a.sh", True, "ok"), ("b.sh", False, "err")])

    def test_empty_list(self):
        assert hooks_all_passed([])


# ---------------------------------------------------------------------------
# Runner integration — hooks fire at correct moments
# ---------------------------------------------------------------------------

class TestRunnerHooksIntegration:
    def _make_project(self, tmp_path):
        """Create minimal project with environment.yml and a simple process."""
        (tmp_path / "process").mkdir()
        (tmp_path / "docs").mkdir()
        (tmp_path / "runs" / "01" / "state").mkdir(parents=True)

        process = {
            "id": "test_hooks",
            "version": "1.0.0",
            "title": "Hook Test Process",
            "nodes": [
                {"id": "gate1", "type": "gate", "title": "Gate 1",
                 "executor": "python", "validators": [{"file_exists": "docs/test.md"}],
                 "next": "end"},
                {"id": "end", "type": "end", "title": "End"},
            ],
        }
        (tmp_path / "process" / "FAST_TRACK_PROCESS.yml").write_text(
            yaml.dump(process, default_flow_style=False)
        )
        return tmp_path

    def test_runner_loads_environment(self, tmp_path):
        from ft.engine.runner import StepRunner
        project = self._make_project(tmp_path)

        env_file = project / "process" / "environment.yml"
        env_file.write_text("hooks:\n  on_init:\n    - ./scripts/test.sh\n")

        runner = StepRunner(
            process_path=project / "process" / "FAST_TRACK_PROCESS.yml",
            state_path=project / "runs" / "01" / "state" / "engine_state.yml",
            project_root=project,
        )
        assert "hooks" in runner._environment

    def test_fire_hooks_returns_true_when_no_hooks(self, tmp_path):
        from ft.engine.runner import StepRunner
        project = self._make_project(tmp_path)
        runner = StepRunner(
            process_path=project / "process" / "FAST_TRACK_PROCESS.yml",
            state_path=project / "runs" / "01" / "state" / "engine_state.yml",
            project_root=project,
        )
        assert runner._fire_hooks("on_init") is True

    def test_on_init_fires_during_init_state(self, tmp_path):
        from ft.engine.runner import StepRunner
        project = self._make_project(tmp_path)

        # Create a hook that writes a marker file
        scripts_dir = project / "process" / "scripts"
        scripts_dir.mkdir()
        script = scripts_dir / "marker.sh"
        script.write_text(f"#!/bin/bash\ntouch {tmp_path}/hook_fired\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        env_file = project / "process" / "environment.yml"
        env_file.write_text("hooks:\n  on_init:\n    - ./scripts/marker.sh\n")

        runner = StepRunner(
            process_path=project / "process" / "FAST_TRACK_PROCESS.yml",
            state_path=project / "runs" / "01" / "state" / "engine_state.yml",
            project_root=project,
        )
        runner.init_state()

        assert (tmp_path / "hook_fired").exists(), "on_init hook should have fired"

    def test_on_gate_pass_fires(self, tmp_path):
        from ft.engine.runner import StepRunner
        project = self._make_project(tmp_path)

        # Create the file that the gate expects
        (project / "docs" / "test.md").write_text("content")

        # Create hook
        scripts_dir = project / "process" / "scripts"
        scripts_dir.mkdir()
        script = scripts_dir / "gate_pass.sh"
        script.write_text(f"#!/bin/bash\ntouch {tmp_path}/gate_pass_fired\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        env_file = project / "process" / "environment.yml"
        env_file.write_text("hooks:\n  on_gate_pass:\n    - ./scripts/gate_pass.sh\n")

        runner = StepRunner(
            process_path=project / "process" / "FAST_TRACK_PROCESS.yml",
            state_path=project / "runs" / "01" / "state" / "engine_state.yml",
            project_root=project,
        )
        runner.init_state()
        runner.run(mode="step")

        assert (tmp_path / "gate_pass_fired").exists(), "on_gate_pass hook should have fired"

    def test_on_deliver_fires_at_process_end(self, tmp_path):
        from ft.engine.runner import StepRunner
        project = self._make_project(tmp_path)

        # Create the file that the gate expects
        (project / "docs" / "test.md").write_text("content")

        # Create hook
        scripts_dir = project / "process" / "scripts"
        scripts_dir.mkdir()
        script = scripts_dir / "deliver.sh"
        script.write_text(f"#!/bin/bash\ntouch {tmp_path}/deliver_fired\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        env_file = project / "process" / "environment.yml"
        env_file.write_text("hooks:\n  on_deliver:\n    - ./scripts/deliver.sh\n")

        runner = StepRunner(
            process_path=project / "process" / "FAST_TRACK_PROCESS.yml",
            state_path=project / "runs" / "01" / "state" / "engine_state.yml",
            project_root=project,
        )
        runner.init_state()
        runner.run(mode="mvp")

        assert (tmp_path / "deliver_fired").exists(), "on_deliver hook should have fired"
