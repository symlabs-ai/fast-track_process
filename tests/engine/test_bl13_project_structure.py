"""Tests for the repository-local .ft layout and external runtime.

Covers: root discovery, external state, versioned cycle numbering, zero-state
initialization, and isolated execution.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from ft.engine import paths
import yaml


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_process_yaml(path: Path, num_nodes: int = 2) -> Path:
    """Create a minimal valid process YAML with V3 paths."""
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


def run_ft(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    repo_root = str(Path(__file__).resolve().parent.parent.parent)
    env = {**os.environ, "PYTHONPATH": repo_root, "FT_SKIP_HEALTH_CHECK": "1"}
    return subprocess.run(
        [sys.executable, "-m", "ft.cli.main"] + args,
        capture_output=True, text=True, cwd=cwd, env=env,
    )


# ---------------------------------------------------------------------------
# find_project_root
# ---------------------------------------------------------------------------

class TestFindProjectRoot:
    def test_detects_by_hidden_process(self, tmp_path, monkeypatch):
        from ft.cli.main import find_project_root
        _create_process_yaml(tmp_path / ".ft" / "process" / "process.yml")
        monkeypatch.chdir(tmp_path)
        assert find_project_root() == tmp_path

    def test_walks_up_to_find_process(self, tmp_path, monkeypatch):
        from ft.cli.main import find_project_root
        _create_process_yaml(tmp_path / ".ft" / "process" / "process.yml")
        sub = tmp_path / "sub" / "deep"
        sub.mkdir(parents=True)
        monkeypatch.chdir(sub)
        assert find_project_root() == tmp_path

    def test_returns_cwd_when_no_process(self, tmp_path, monkeypatch):
        from ft.cli.main import find_project_root
        monkeypatch.chdir(tmp_path)
        assert find_project_root() == tmp_path

    def test_does_not_match_legacy_project_state(self, tmp_path, monkeypatch):
        """Runtime-like directories do not identify a project root."""
        from ft.cli.main import find_project_root
        (tmp_path / "project" / "state").mkdir(parents=True)
        monkeypatch.chdir(tmp_path)
        result = find_project_root()
        assert result == tmp_path


# ---------------------------------------------------------------------------
# _find_latest_state
# ---------------------------------------------------------------------------

class TestFindLatestState:
    def test_current_worktree_uses_own_project_runtime_and_state(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
        project = tmp_path / "ft_ui"
        worktree_home = paths.worktrees_home(project)
        worktree = worktree_home / "cycle-09-codex"
        _create_process_yaml(worktree / ".ft" / "process" / "process.yml")
        state = worktree / "state" / "engine_state.yml"
        state.parent.mkdir(parents=True)
        state.write_text("current_node: start\n")

        from ft.cli.main import _find_latest_state

        assert paths.project_runtime_key(worktree) == "ft_ui"
        assert paths.worktrees_home(worktree) == worktree_home
        assert paths.runtime_home(worktree) == paths.runtime_home(project)
        assert _find_latest_state(worktree) == state

    def test_finds_global_continuous_state(self, tmp_path):
        from ft.cli.main import _find_latest_state
        state = paths.continuous_state_path(tmp_path)
        state.parent.mkdir(parents=True)
        state.write_text("current_node: start\n")
        assert _find_latest_state(tmp_path) == state

    def test_ignores_repository_local_legacy_state(self, tmp_path):
        from ft.cli.main import _find_latest_state
        legacy = tmp_path / "project" / "state" / "engine_state.yml"
        legacy.parent.mkdir(parents=True)
        legacy.write_text("legacy")
        assert _find_latest_state(tmp_path) != legacy

    def test_defaults_to_continuous_runtime_without_existing_cycle(self, tmp_path):
        from ft.cli.main import _find_latest_state
        result = _find_latest_state(tmp_path)
        expected = paths.continuous_state_path(tmp_path)
        assert result == expected

    def test_state_only_directory_is_not_a_worktree(self, tmp_path, monkeypatch):
        from ft.cli.main import _worktree_root_from_state

        monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
        project = tmp_path / "project"
        candidate = paths.worktrees_home(project) / "cycle-01"
        state = candidate / "state" / "engine_state.yml"
        state.parent.mkdir(parents=True)
        state.write_text("node_status: ready\n", encoding="utf-8")

        assert _worktree_root_from_state(state) is None

        _create_process_yaml(paths.project_process_file(candidate))
        assert _worktree_root_from_state(state) == candidate

# ---------------------------------------------------------------------------
# _next_run_dir
# ---------------------------------------------------------------------------

class TestNextRunDir:
    def test_creates_first_run(self, tmp_path):
        from ft.cli.main import _next_run_dir
        run_dir = _next_run_dir(tmp_path)
        # BL-20: ciclos vivem em worktrees externos, não em runs/
        assert run_dir == paths.worktrees_home(tmp_path) / "cycle-01"
        assert run_dir.is_dir()

    def test_increments_from_existing(self, tmp_path):
        from ft.cli.main import _next_run_dir
        (tmp_path / ".ft" / "cycles" / "cycle-01").mkdir(parents=True)
        (tmp_path / ".ft" / "cycles" / "cycle-02").mkdir()
        run_dir = _next_run_dir(tmp_path)
        assert run_dir == paths.worktrees_home(tmp_path) / "cycle-03"
        assert run_dir.is_dir()

    def test_pads_with_zero(self, tmp_path):
        from ft.cli.main import _next_run_dir
        run_dir = _next_run_dir(tmp_path)
        assert run_dir.name == "cycle-01"


# ---------------------------------------------------------------------------
# project .ft ignore policy
# ---------------------------------------------------------------------------

class TestProjectFtGitignore:
    def test_ignores_only_runtime_subdirectories(self, tmp_path):
        from ft.engine.layout import ensure_project_layout
        ensure_project_layout(tmp_path)
        gitignore = tmp_path / ".ft" / ".gitignore"
        assert gitignore.exists()
        content = gitignore.read_text()
        assert "/runtime/" in content
        assert "/cycles/" not in content

    def test_idempotent(self, tmp_path):
        from ft.engine.layout import ensure_project_layout
        ensure_project_layout(tmp_path)
        ensure_project_layout(tmp_path)
        content = (tmp_path / ".ft" / ".gitignore").read_text()
        assert content.count("/runtime/") == 1


# ---------------------------------------------------------------------------
# ft init — creates V3 structure
# ---------------------------------------------------------------------------

class TestInitCreatesStructure:
    def test_creates_process_dir(self, tmp_path):
        run_ft(["init", "--template", "base"], cwd=tmp_path)
        assert (tmp_path / ".ft" / "process").is_dir()

    def test_creates_docs_dir(self, tmp_path):
        run_ft(["init", "--template", "base"], cwd=tmp_path)
        assert (tmp_path / "docs").is_dir()

    def test_does_not_create_runs_dir(self, tmp_path):
        """BL-20: ft init no longer creates runs/ inside the repo."""
        run_ft(["init", "--template", "base"], cwd=tmp_path)
        assert not (tmp_path / "runs").is_dir()

    def test_init_creates_no_execution_state(self, tmp_path):
        run_ft(["init", "--template", "base"], cwd=tmp_path)
        state = paths.worktrees_home(tmp_path) / "cycle-01" / "state" / "engine_state.yml"
        assert not state.exists()
        assert not (tmp_path / "state").exists()


# ---------------------------------------------------------------------------
# ft run — creates run subdir
# ---------------------------------------------------------------------------

class TestRunCreatesRunSubdir:
    def test_run_creates_external_worktree(self, tmp_path):
        """BL-20: ft run creates cycle in ~/.ft/worktrees/, not runs/."""
        from ft.engine.layout import ensure_project_layout
        _create_process_yaml(tmp_path / ".ft" / "process" / "process.yml")
        ensure_project_layout(tmp_path)
        (tmp_path / "docs").mkdir()
        (tmp_path / "src").mkdir()
        run_ft(["run", str(tmp_path)], cwd=tmp_path)
        wt_home = paths.worktrees_home(tmp_path)
        assert wt_home.is_dir()
        cycles = list(wt_home.iterdir())
        assert len(cycles) >= 1

    def test_run_creates_docs(self, tmp_path):
        from ft.engine.layout import ensure_project_layout
        _create_process_yaml(tmp_path / ".ft" / "process" / "process.yml")
        ensure_project_layout(tmp_path)
        run_ft(["run", str(tmp_path)], cwd=tmp_path)
        assert (tmp_path / "docs").is_dir()


# ---------------------------------------------------------------------------
# ft run increments run number
# ---------------------------------------------------------------------------

class TestRunIncrementsRunNumber:
    def test_second_run_creates_cycle_02(self, tmp_path):
        from ft.cli.main import _next_run_dir
        r1 = _next_run_dir(tmp_path)
        assert r1.name == "cycle-01"
        r2 = _next_run_dir(tmp_path)
        assert r2.name == "cycle-02"
        assert r2.is_dir()

    def test_second_ft_run_creates_second_cycle_e2e(self, tmp_path):
        """Two ft run calls should create cycle-01 and cycle-02 in worktrees."""
        from ft.engine.layout import ensure_project_layout
        _create_process_yaml(tmp_path / ".ft" / "process" / "process.yml")
        ensure_project_layout(tmp_path)
        run_ft(["run", str(tmp_path)], cwd=tmp_path)
        run_ft(["run", str(tmp_path)], cwd=tmp_path)
        wt_home = paths.worktrees_home(tmp_path)
        cycles = [d.name for d in wt_home.iterdir() if d.is_dir()]
        assert len(cycles) >= 2


# ---------------------------------------------------------------------------
# Legacy fallback
# ---------------------------------------------------------------------------

class TestNoLegacyFallback:
    def test_get_runner_does_not_read_legacy_state(self, tmp_path):
        from ft.cli.main import _find_latest_state
        legacy = tmp_path / "project" / "state" / "engine_state.yml"
        legacy.parent.mkdir(parents=True)
        legacy.write_text("process_id: test\n")
        result = _find_latest_state(tmp_path)
        assert result != legacy


# ---------------------------------------------------------------------------
# scan_existing_docs only reads docs/ not project/docs/
# ---------------------------------------------------------------------------

class TestScanExistingDocsV3:
    def test_ignores_legacy_project_docs(self, tmp_path):
        """scan_existing_docs should NOT read project/docs/ (legacy)."""
        from ft.engine.stakeholder import scan_existing_docs
        legacy = tmp_path / "project" / "docs"
        legacy.mkdir(parents=True)
        (legacy / "hipotese.md").write_text("legacy content")
        docs = scan_existing_docs(str(tmp_path))
        assert docs == {}

    def test_reads_from_docs(self, tmp_path):
        from ft.engine.stakeholder import scan_existing_docs
        d = tmp_path / "docs"
        d.mkdir()
        (d / "PRD.md").write_text("content")
        docs = scan_existing_docs(str(tmp_path))
        assert "PRD.md" in docs


# ---------------------------------------------------------------------------
# sections_unchanged with state_dir
# ---------------------------------------------------------------------------

class TestSectionsUnchangedWithStateDir:
    def test_snapshot_resolved_via_state_dir(self, tmp_path):
        """run_validators resolves snapshot_path relative to state_dir."""
        from ft.engine.runner import run_validators
        from ft.engine.graph import Node

        # Setup: docs/PRD.md and runs/01/state/prd_rewrite_baseline.md
        (tmp_path / "docs").mkdir()
        state_dir = tmp_path / "runs" / "01" / "state"
        state_dir.mkdir(parents=True)

        baseline = "## Hipotese\nTexto base.\n## Visao\nOriginal.\n"
        (tmp_path / "docs" / "PRD.md").write_text(baseline)
        (state_dir / "prd_rewrite_baseline.md").write_text(baseline)

        node = Node(
            id="test.rewrite",
            type="document",
            title="Rewrite",
            executor="llm_coach",
            outputs=["docs/PRD.md"],
            validators=[
                {"sections_unchanged": {
                    "path": "docs/PRD.md",
                    "snapshot_path": "prd_rewrite_baseline.md",
                    "sections": ["Hipotese", "Visao"],
                }}
            ],
        )

        result = run_validators(node, str(tmp_path), state_dir=str(state_dir))
        assert result.passed


# ---------------------------------------------------------------------------
# handoff nodes write to docs/
# ---------------------------------------------------------------------------

class TestHandoffDocsPath:
    def test_handoff_specs_outputs_docs(self):
        """ft.handoff.01.specs outputs should start with docs/."""
        from ft.engine.graph import load_graph
        process = Path(__file__).parent.parent.parent / "process" / "fast_track" / "FAST_TRACK_PROCESS_V2.yml"
        if not process.exists():
            pytest.skip("V2 process not found")
        g = load_graph(process)
        specs = g.get_node("ft.handoff.01.specs")
        for output in specs.outputs:
            assert output.startswith("docs/") or output == "CHANGELOG.md", (
                f"ft.handoff.01.specs output {output} should be in docs/"
            )

    def test_handoff_plano_voo_outputs_docs(self):
        """ft.handoff.02.plano_voo outputs should be docs/plano_de_voo.md."""
        from ft.engine.graph import load_graph
        process = Path(__file__).parent.parent.parent / "process" / "fast_track" / "FAST_TRACK_PROCESS_V2.yml"
        if not process.exists():
            pytest.skip("V2 process not found")
        g = load_graph(process)
        plano = g.get_node("ft.handoff.02.plano_voo")
        assert "docs/plano_de_voo.md" in plano.outputs

    def test_prd_rewrite_outputs_docs(self):
        """ft.prd.rewrite outputs should be docs/PRD.md."""
        from ft.engine.graph import load_graph
        process = Path(__file__).parent.parent.parent / "process" / "fast_track" / "FAST_TRACK_PROCESS_V2.yml"
        if not process.exists():
            pytest.skip("V2 process not found")
        g = load_graph(process)
        rewrite = g.get_node("ft.prd.rewrite")
        assert "docs/PRD.md" in rewrite.outputs


# ---------------------------------------------------------------------------
# docs/ path in outputs
# ---------------------------------------------------------------------------

class TestDocsPath:
    def test_outputs_use_docs_prefix(self):
        """V3 process YAML uses docs/ not project/docs/ in outputs."""
        from ft.engine.graph import load_graph
        process = Path(__file__).parent.parent.parent / "process" / "fast_track" / "FAST_TRACK_PROCESS_V2.yml"
        if not process.exists():
            pytest.skip("V2 process not found")
        g = load_graph(process)
        prd = g.get_node("ft.mdd.02.prd")
        assert prd.outputs[0] == "docs/PRD.md"

    def test_no_project_docs_references(self):
        """No node in V3 should reference project/docs/."""
        from ft.engine.graph import load_graph
        process = Path(__file__).parent.parent.parent / "process" / "fast_track" / "FAST_TRACK_PROCESS_V2.yml"
        if not process.exists():
            pytest.skip("V2 process not found")
        g = load_graph(process)
        for node in g.nodes.values():
            for output in node.outputs:
                assert not output.startswith("project/docs/"), (
                    f"Node {node.id} still uses project/docs/ prefix: {output}"
                )
