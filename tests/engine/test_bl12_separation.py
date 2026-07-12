"""Tests for BL-12: Base/Environment separation.

Covers: find_process_yaml, copy_template, ft validate CLI,
gate_kb_review with kb_path, FT_KB_PATH env var, external integration separation.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
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
    def test_finds_canonical_project_process(self, tmp_path):
        from ft.cli.main import find_process_yaml
        expected = _create_process_yaml(tmp_path / ".ft" / "process" / "process.yml")
        result = find_process_yaml(tmp_path)
        assert result == expected

    def test_does_not_scan_noncanonical_yaml(self, tmp_path):
        from ft.cli.main import find_process_yaml
        _create_process_yaml(tmp_path / ".ft" / "process" / "custom.yml")
        assert find_process_yaml(tmp_path) is None

    def test_does_not_fallback_to_old_process_directory(self, tmp_path):
        from ft.cli.main import find_process_yaml
        _create_process_yaml(tmp_path / "process" / "FAST_TRACK_PROCESS.yml")
        assert find_process_yaml(tmp_path) is None

    def test_returns_none_when_no_process(self, tmp_path):
        """Returns None when no YAML found anywhere."""
        from ft.cli.main import find_process_yaml
        result = find_process_yaml(tmp_path)
        assert result is None


# ---------------------------------------------------------------------------
# copy_template
# ---------------------------------------------------------------------------

class TestCopyTemplate:
    def test_available_templates_are_dynamic_and_sorted(self):
        from ft.cli.main import available_templates

        templates = available_templates()
        all_templates = available_templates(entrypoint=None)

        assert templates == sorted(templates)
        assert "mvp-builder" in templates
        assert "feature" not in templates
        assert "fast-track-v3" not in templates
        assert "feature" in all_templates

    def test_copies_template_to_hidden_process_dir(self, tmp_path):
        from ft.cli.main import copy_template
        result = copy_template("fast-track-v2", tmp_path)
        assert result.exists()
        assert result.name == "process.yml"
        assert (tmp_path / ".ft" / "process" / "process.yml").exists()
        assert (tmp_path / ".ft" / "manifest.yml").exists()

    def test_template_is_valid_yaml(self, tmp_path):
        from ft.cli.main import copy_template
        result = copy_template("fast-track-v2", tmp_path)
        graph = load_graph(result)
        assert len(graph.nodes) > 0

    def test_base_template_copies_generic_ui_criteria(self, tmp_path):
        from ft.cli.main import copy_template
        copy_template("base", tmp_path)
        ui_criteria = tmp_path / "docs" / "ui_criteria.md"
        assert ui_criteria.exists()
        content = ui_criteria.read_text(encoding="utf-8")
        assert "C01:" in content
        assert "data-ui-criteria" in content
        assert "ServiceMate" not in content

    def test_mvp_builder_template_does_not_preseed_ui_criteria(self, tmp_path):
        from ft.cli.main import copy_template
        copy_template("mvp-builder", tmp_path)
        assert not (tmp_path / "docs" / "ui_criteria.md").exists()
        manifest = yaml.safe_load((tmp_path / ".ft" / "manifest.yml").read_text())
        assert manifest["template"]["id"] == "mvp-builder"

    def test_template_process_yml_takes_precedence_over_environment_yml(self, tmp_path):
        from ft.cli.main import copy_template

        process = copy_template("symgateway", tmp_path)
        data = yaml.safe_load(process.read_text(encoding="utf-8"))

        assert data["id"] == "base_process"
        assert "nodes" in data

    def test_nonexistent_template_exits(self, tmp_path):
        from ft.cli.main import copy_template
        with pytest.raises(SystemExit):
            copy_template("nonexistent-template", tmp_path)

    def test_feature_entrypoint_is_not_materialized_by_init_copy(self, tmp_path):
        from ft.cli.main import copy_template

        with pytest.raises(SystemExit):
            copy_template("feature", tmp_path)

        assert not (tmp_path / ".ft").exists()


# ---------------------------------------------------------------------------
# ft init --template
# ---------------------------------------------------------------------------

class TestInitTemplate:
    def test_init_with_template_creates_process(self, tmp_path):
        result = run_ft(["init", "--template", "base"], cwd=tmp_path)
        assert result.returncode == 0
        assert (tmp_path / ".ft" / "process" / "process.yml").exists()
        assert not (tmp_path / "state").exists()
        assert not (tmp_path / ".ft" / "runtime").exists()

    def test_init_without_template_fails_and_lists_available_templates(self, tmp_path):
        result = run_ft(["init"], cwd=tmp_path)
        assert result.returncode == 2
        output = result.stdout + result.stderr
        assert "--template" in output
        assert "base" in output
        assert "mvp-builder" in output
        assert not (tmp_path / ".ft").exists()

    def test_cmd_init_raises_before_side_effects_without_template(self, tmp_path, monkeypatch):
        from ft.cli.main import cmd_init

        monkeypatch.chdir(tmp_path)
        with pytest.raises(ValueError, match="Templates disponíveis:.*mvp-builder"):
            cmd_init(SimpleNamespace(template=None))

        assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# ft validate
# ---------------------------------------------------------------------------

class TestValidateCLI:
    def _base_project(self, tmp_path):
        """Create base project structure (docs/, .ft/process/, src/)."""
        from ft.engine.layout import ensure_project_layout
        (tmp_path / "docs").mkdir(exist_ok=True)
        (tmp_path / "src").mkdir(exist_ok=True)
        ensure_project_layout(tmp_path)

    def test_validate_valid_process(self, tmp_path):
        self._base_project(tmp_path)
        _create_process_yaml(tmp_path / ".ft" / "process" / "process.yml")
        result = run_ft(["validate"], cwd=tmp_path)
        assert result.returncode == 0
        assert "PASS" in result.stdout

    def test_validate_with_explicit_process(self, tmp_path):
        self._base_project(tmp_path)
        yaml_path = _create_process_yaml(tmp_path / ".ft" / "process" / "process.yml")
        result = run_ft(["-p", str(yaml_path), "validate"], cwd=tmp_path)
        assert result.returncode == 0

    def test_validate_no_process_found(self, tmp_path):
        (tmp_path / "project" / "state").mkdir(parents=True)
        result = run_ft(["validate"], cwd=tmp_path)
        assert result.returncode == 1

    def test_validate_real_process(self, monkeypatch):
        """Validate the actual MVP Builder template with an explicit path."""
        process = Path(__file__).parent.parent.parent / "templates" / "mvp-builder" / "process.yml"
        # Roda dentro do repo do template (dev do engine) — precisa do override do guard
        monkeypatch.setenv("FT_ALLOW_ENGINE_REPO", "1")
        result = run_ft(["-p", str(process), "validate"], cwd=process.parent.parent.parent)
        # O YAML passa, mas o engine repo não é um projeto no layout .ft.
        assert result.returncode == 1
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
# External environment integrations
# ---------------------------------------------------------------------------

class TestExternalIntegrationSeparation:
    def test_symgateway_not_packaged_as_engine_module(self):
        import importlib.util

        assert importlib.util.find_spec("ft.integrations.symgateway") is None

    def test_symgateway_template_contains_register_script(self):
        root = Path(__file__).parent.parent.parent
        script = root / "templates" / "symgateway" / "scripts" / "register_gateway.sh"

        assert script.exists()
        assert "SYM_GATEWAY_PROJECT_KEY" in script.read_text()

    def test_symgateway_template_wires_script_as_hook(self):
        root = Path(__file__).parent.parent.parent
        env_yml = root / "templates" / "symgateway" / "environment.yml"
        data = yaml.safe_load(env_yml.read_text())

        assert "scripts/register_gateway.sh" in data["hooks"]["on_init"]

    def test_delegate_has_no_external_integration_dependency(self):
        """Delegate remains importable without provider-specific modules."""
        from ft.engine.delegate import delegate_to_llm
        assert callable(delegate_to_llm)
