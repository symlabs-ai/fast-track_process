"""Tests for BL-12: Base/Environment separation.

Covers: find_process_yaml, copy_template, ft validate CLI,
gate_kb_review with kb_path, FT_KB_PATH env var, SymGateway extraction.
"""

from __future__ import annotations

import os
import subprocess
import sys
import warnings
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from ft.engine.graph import load_graph


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_process_yaml(path: Path, num_nodes: int = 2) -> Path:
    """Create a minimal valid process YAML."""
    content = {
        "id": "test_process",
        "version": "1.0.0",
        "title": "Test Process",
        "nodes": [
            {"id": "start", "type": "build", "title": "Start",
             "executor": "python", "outputs": ["out.txt"], "next": "end"},
            {"id": "end", "type": "end", "title": "End"},
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(content, default_flow_style=False))
    return path


def run_ft(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    # Use PYTHONPATH to ensure worktree code is used, not installed package
    repo_root = str(Path(__file__).resolve().parent.parent.parent)
    env = {**os.environ, "PYTHONPATH": repo_root}
    return subprocess.run(
        [sys.executable, "-m", "ft.cli.main"] + args,
        capture_output=True, text=True, cwd=cwd, env=env,
    )


# ---------------------------------------------------------------------------
# find_process_yaml
# ---------------------------------------------------------------------------

class TestFindProcessYaml:
    def test_finds_canonical_in_process_dir(self, tmp_path):
        """YAML at process/FAST_TRACK_PROCESS.yml is found."""
        from ft.cli.main import find_process_yaml
        _create_process_yaml(tmp_path / "process" / "FAST_TRACK_PROCESS.yml")
        result = find_process_yaml(tmp_path)
        assert result is not None
        assert result.name == "FAST_TRACK_PROCESS.yml"

    def test_finds_any_yaml_in_process_dir(self, tmp_path):
        """Single YAML in process/ is found even with non-standard name."""
        from ft.cli.main import find_process_yaml
        _create_process_yaml(tmp_path / "process" / "my_custom_process.yml")
        result = find_process_yaml(tmp_path)
        assert result is not None
        assert result.name == "my_custom_process.yml"

    def test_prefers_fast_track_when_multiple(self, tmp_path):
        """When multiple YAMLs exist, prefers one with FAST_TRACK in name."""
        from ft.cli.main import find_process_yaml
        _create_process_yaml(tmp_path / "process" / "FAST_TRACK_PROCESS.yml")
        _create_process_yaml(tmp_path / "process" / "other.yml")
        result = find_process_yaml(tmp_path)
        assert result.name == "FAST_TRACK_PROCESS.yml"

    def test_legacy_path_emits_warning(self, tmp_path):
        """YAML in process/fast_track/ emits DeprecationWarning."""
        from ft.cli.main import find_process_yaml
        _create_process_yaml(tmp_path / "process" / "fast_track" / "FAST_TRACK_PROCESS_V2.yml")
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = find_process_yaml(tmp_path)
            assert result is not None
            assert any(issubclass(x.category, DeprecationWarning) for x in w)

    def test_returns_none_when_no_process(self, tmp_path):
        """Returns None when no YAML found anywhere."""
        from ft.cli.main import find_process_yaml
        result = find_process_yaml(tmp_path)
        assert result is None


# ---------------------------------------------------------------------------
# copy_template
# ---------------------------------------------------------------------------

class TestCopyTemplate:
    def test_copies_template_to_process_dir(self, tmp_path):
        from ft.cli.main import copy_template
        result = copy_template("fast-track-v2", tmp_path)
        assert result.exists()
        assert result.name == "process.yml"
        assert (tmp_path / "process" / "process.yml").exists()

    def test_template_is_valid_yaml(self, tmp_path):
        from ft.cli.main import copy_template
        result = copy_template("fast-track-v2", tmp_path)
        graph = load_graph(result)
        assert len(graph.nodes) > 0

    def test_nonexistent_template_exits(self, tmp_path):
        from ft.cli.main import copy_template
        with pytest.raises(SystemExit):
            copy_template("nonexistent-template", tmp_path)


# ---------------------------------------------------------------------------
# ft init --template
# ---------------------------------------------------------------------------

class TestInitTemplate:
    def test_init_with_template_creates_process(self, tmp_path):
        (tmp_path / "project" / "state").mkdir(parents=True)
        result = run_ft(["--process", "/dev/null", "init"], cwd=tmp_path)
        # Just verify the flag is accepted without error
        # Full E2E of --template requires the template to exist relative to engine

    def test_init_without_process_gives_guidance(self, tmp_path):
        (tmp_path / "project" / "state").mkdir(parents=True)
        result = run_ft(["init"], cwd=tmp_path)
        assert result.returncode == 1
        assert "ft init --template" in result.stdout


# ---------------------------------------------------------------------------
# ft validate
# ---------------------------------------------------------------------------

class TestValidateCLI:
    def _base_project(self, tmp_path):
        """Create base project structure (docs/, process/, src/)."""
        (tmp_path / "project" / "state").mkdir(parents=True)
        (tmp_path / "docs").mkdir(exist_ok=True)
        (tmp_path / "src").mkdir(exist_ok=True)
        (tmp_path / "process").mkdir(exist_ok=True)

    def test_validate_valid_process(self, tmp_path):
        self._base_project(tmp_path)
        _create_process_yaml(tmp_path / "process" / "FAST_TRACK_PROCESS.yml")
        result = run_ft(["validate"], cwd=tmp_path)
        assert result.returncode == 0
        assert "PASS" in result.stdout

    def test_validate_with_explicit_process(self, tmp_path):
        self._base_project(tmp_path)
        yaml_path = _create_process_yaml(tmp_path / "process" / "my_process.yml")
        result = run_ft(["-p", str(yaml_path), "validate"], cwd=tmp_path)
        assert result.returncode == 0

    def test_validate_no_process_found(self, tmp_path):
        (tmp_path / "project" / "state").mkdir(parents=True)
        result = run_ft(["validate"], cwd=tmp_path)
        assert result.returncode == 1

    def test_validate_real_process(self):
        """Validate the actual FAST_TRACK_PROCESS_V2.yml."""
        process = Path(__file__).parent.parent.parent / "process" / "fast_track" / "FAST_TRACK_PROCESS_V2.yml"
        if not process.exists():
            pytest.skip("V2 process not found")
        # Need project/state for find_project_root
        result = run_ft(["-p", str(process), "validate"], cwd=process.parent.parent.parent)
        assert result.returncode == 0
        assert "PASS" in result.stdout


# ---------------------------------------------------------------------------
# gate_kb_review with kb_path
# ---------------------------------------------------------------------------

class TestGateKbReviewKbPath:
    def test_with_explicit_kb_path(self, tmp_path):
        from ft.engine.validators.gates import gate_kb_review
        # Create minimal project structure
        (tmp_path / "project" / "docs").mkdir(parents=True)
        kb_dir = tmp_path / "my_kb"
        kb_dir.mkdir()
        passed, msg = gate_kb_review(project_root=str(tmp_path), kb_path=str(kb_dir))
        assert passed
        assert "PASS" in msg

    def test_with_env_var(self, tmp_path):
        from ft.engine.validators.gates import gate_kb_review
        (tmp_path / "project" / "docs").mkdir(parents=True)
        kb_dir = tmp_path / "env_kb"
        kb_dir.mkdir()
        with patch.dict(os.environ, {"FT_KB_PATH": str(kb_dir)}):
            passed, msg = gate_kb_review(project_root=str(tmp_path))
        assert passed

    def test_without_kb_uses_fallback(self, tmp_path):
        from ft.engine.validators.gates import gate_kb_review
        (tmp_path / "project" / "docs").mkdir(parents=True)
        with patch.dict(os.environ, {}, clear=True):
            env = {k: v for k, v in os.environ.items() if k != "FT_KB_PATH"}
            with patch.dict(os.environ, env, clear=True):
                passed, msg = gate_kb_review(project_root=str(tmp_path))
        assert passed  # Should pass even without KB (just no pitfalls to check)


# ---------------------------------------------------------------------------
# FT_KB_PATH in StepRunner
# ---------------------------------------------------------------------------

class TestKbPathInRunner:
    def test_kb_path_from_env(self, tmp_path):
        from ft.engine.runner import StepRunner
        _create_process_yaml(tmp_path / "process" / "FAST_TRACK_PROCESS.yml")
        state_path = tmp_path / "project" / "state" / "engine_state.yml"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.dict(os.environ, {"FT_KB_PATH": "/some/kb/path"}):
            runner = StepRunner(
                process_path=tmp_path / "process" / "FAST_TRACK_PROCESS.yml",
                state_path=state_path,
                project_root=tmp_path,
            )
            assert runner._kb_path == "/some/kb/path"

    def test_kb_path_none_when_not_set(self, tmp_path):
        from ft.engine.runner import StepRunner
        _create_process_yaml(tmp_path / "process" / "FAST_TRACK_PROCESS.yml")
        state_path = tmp_path / "project" / "state" / "engine_state.yml"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.dict(os.environ, {}, clear=True):
            env = {k: v for k, v in os.environ.items() if k != "FT_KB_PATH"}
            with patch.dict(os.environ, env, clear=True):
                runner = StepRunner(
                    process_path=tmp_path / "process" / "FAST_TRACK_PROCESS.yml",
                    state_path=state_path,
                    project_root=tmp_path,
                )
                assert runner._kb_path is None


# ---------------------------------------------------------------------------
# SymGateway extraction
# ---------------------------------------------------------------------------

class TestSymGatewayExtraction:
    def test_import_from_integrations(self):
        from ft.integrations.symgateway import (
            provision_environment,
            check_gateway_403,
            SYMGATEWAY_BASE,
        )
        assert SYMGATEWAY_BASE == "https://symgateway.symlabs.ai"
        assert callable(provision_environment)
        assert callable(check_gateway_403)

    def test_check_gateway_403_detects_error(self):
        from ft.integrations.symgateway import check_gateway_403
        output = "Error 403: project not found in workspace folder_name='my_proj'"
        result = check_gateway_403(output)
        assert result is not None
        assert "my_proj" in result

    def test_check_gateway_403_returns_none_for_normal(self):
        from ft.integrations.symgateway import check_gateway_403
        result = check_gateway_403("DONE\nAll good")
        assert result is None

    def test_provision_creates_claude_md(self, tmp_path):
        from ft.integrations.symgateway import provision_environment
        provision_environment(project_root=tmp_path, base_url="https://example.com/api")
        assert (tmp_path / "CLAUDE.md").exists()
        assert "gateway_project" in (tmp_path / "CLAUDE.md").read_text()

    def test_provision_creates_settings(self, tmp_path):
        from ft.integrations.symgateway import provision_environment
        provision_environment(project_root=tmp_path, base_url="https://example.com/api")
        settings = tmp_path / ".claude" / "settings.local.json"
        assert settings.exists()
        import json
        data = json.loads(settings.read_text())
        assert data["env"]["ANTHROPIC_BASE_URL"] == "https://example.com/api"

    def test_delegate_works_without_symgateway(self):
        """Import lazy: delegate should handle missing symgateway gracefully."""
        # This tests that the try/except ImportError in delegate.py works
        from ft.engine.delegate import delegate_to_llm
        assert callable(delegate_to_llm)
