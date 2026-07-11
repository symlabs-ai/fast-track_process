from __future__ import annotations

import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
import yaml

from ft.engine.layout import (
    archive_cycle_artifacts,
    ensure_project_layout,
    latest_cycle_artifact,
    manifest_llm_defaults,
    migrate_legacy_layout,
    validate_template_is_pristine,
)
from ft.engine.state import EngineState
from ft.engine.runner import StepRunner
from ft.cli.main import cmd_runs


MINIMAL_PROCESS = """id: test
version: '1.0'
title: Test
nodes:
  - id: end
    type: end
    title: End
"""


def test_layout_contains_only_versionable_metadata(tmp_path):
    process = tmp_path / ".ft" / "process" / "process.yml"
    process.parent.mkdir(parents=True)
    process.write_text(MINIMAL_PROCESS)

    ensure_project_layout(
        tmp_path,
        template_id="base",
        defaults={"llm_engine": "opencode", "llm_model": "example/model"},
    )

    assert (tmp_path / ".ft" / "manifest.yml").is_file()
    assert (tmp_path / ".ft" / "cycles" / ".gitkeep").is_file()
    assert not (tmp_path / ".ft" / "runtime").exists()
    assert not (tmp_path / "state").exists()
    assert manifest_llm_defaults(tmp_path) == ("opencode", "example/model")


def test_nested_gitignore_keeps_process_and_cycles_trackable(tmp_path):
    ensure_project_layout(tmp_path)
    (tmp_path / ".ft" / "process" / "process.yml").write_text(MINIMAL_PROCESS)
    (tmp_path / ".ft" / "runtime").mkdir()
    (tmp_path / ".ft" / "runtime" / "state.yml").write_text("runtime")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)

    tracked = subprocess.run(
        ["git", "check-ignore", ".ft/process/process.yml"],
        cwd=tmp_path,
        capture_output=True,
    )
    ignored = subprocess.run(
        ["git", "check-ignore", ".ft/runtime/state.yml"],
        cwd=tmp_path,
        capture_output=True,
    )

    assert tracked.returncode == 1
    assert ignored.returncode == 0


def test_archive_moves_cycle_outputs_and_preserves_product_docs(tmp_path):
    ensure_project_layout(tmp_path)
    docs = tmp_path / "docs"
    screenshots = docs / "screenshots"
    screenshots.mkdir(parents=True)
    (docs / "PRD.md").write_text("# Product")
    (docs / "PROJECT_BACKLOG.md").write_text("# Backlog")
    (docs / "FEATURES.md").write_text("# Features")
    (docs / "task_list.md").write_text("# Tasks")
    (docs / "handoff.md").write_text("# Handoff")
    (screenshots / "home.png").write_bytes(b"png")
    (tmp_path / "cycle-07_log.md").write_text("| log |")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "engine_state.yml").write_text("runtime")

    state = EngineState(
        process_id="fast_track_v3",
        version="1.0.0",
        llm_engine="claude",
        llm_model="claude-fable-5",
        node_status="done",
        gate_log={"build": "PASS", "review": "PASS"},
        metrics={"steps_completed": 44, "steps_total": 44},
    )
    result = archive_cycle_artifacts(tmp_path, "cycle-07", state=state)

    assert (docs / "PRD.md").exists()
    assert (docs / "PROJECT_BACKLOG.md").exists()
    assert (docs / "FEATURES.md").exists()
    assert not (docs / "task_list.md").exists()
    assert not (docs / "screenshots").exists()
    assert (result.cycle_dir / "task_list.md").read_text() == "# Tasks"
    assert (result.cycle_dir / "screenshots" / "home.png").exists()
    assert (result.cycle_dir / "cycle-log.md").exists()
    assert (state_dir / "engine_state.yml").exists()
    assert not (result.cycle_dir / "engine_state.yml").exists()

    record = yaml.safe_load((result.cycle_dir / "cycle.yml").read_text())
    assert record["progress"] == {"completed": 44, "total": 44}
    assert record["gate_summary"] == {"PASS": 2}
    assert record["llm"]["model"] == "claude-fable-5"


def test_archive_uses_process_artifact_policy_and_is_idempotent(tmp_path):
    ensure_project_layout(tmp_path)
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "custom-report.md").write_text("custom")
    graph_meta = {
        "artifact_policy": {
            "canonical": ["docs/PRD.md"],
            "cycle": ["docs/custom-report.md"],
        }
    }

    first = archive_cycle_artifacts(tmp_path, "cycle-custom", graph_meta=graph_meta)
    second = archive_cycle_artifacts(tmp_path, "cycle-custom", graph_meta=graph_meta)

    assert first.moved == ("custom-report.md",)
    assert second.moved == ()
    assert (first.cycle_dir / "custom-report.md").read_text() == "custom"


def test_explicit_migration_moves_process_history_runtime_and_loose_cycle_artifacts(
    tmp_path,
    monkeypatch,
):
    ft_home = tmp_path / "ft-home"
    monkeypatch.setenv("FT_HOME", str(ft_home))
    project = tmp_path / "project"
    project.mkdir()
    process = project / "process"
    scripts = process / "scripts"
    scripts.mkdir(parents=True)
    (process / "process.yml").write_text(
        MINIMAL_PROCESS + "\n# process/scripts/serve.sh\n",
    )
    (scripts / "serve.sh").write_text(
        'ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"\n'
        'mkdir -p process/scripts\n',
    )
    docs = project / "docs"
    docs.mkdir()
    (docs / "PRD.md").write_text("# Product\n\nOpen process/process.yml")
    (docs / "task_list.md").write_text("# Old cycle\n\nUsed process/process.yml")
    (docs / "handoff-cycle-old.md").write_text("# Historical handoff")
    historical = docs / "archive" / "cycle-01"
    historical.mkdir(parents=True)
    (historical / "retro.md").write_text("# Historical retro")
    (project / ".build_ok").write_text("ready")
    source = project / "src"
    source.mkdir()
    (source / "app.py").write_text(
        'PROCESS = "process/process.yml"\n'
        'COMPOSED = root / "process" / "process.yml"\n'
        '(root / "process").mkdir()\n'
        "const PRIORITY = ['docs', 'process', 'src'];\n"
        "const open = depth < 1 && PRIORITY.includes(name);\n",
    )
    (source / "Makefile").write_text("check:\n\tbash ../process/scripts/serve.sh\n")
    (project / "state").mkdir()
    (project / "state" / "engine_state.yml").write_text("must leave repo")

    preview = migrate_legacy_layout(
        project,
        dry_run=True,
        cycle_id="cycle-08-claude",
    )
    assert preview
    assert process.exists()

    actions = migrate_legacy_layout(project, cycle_id="cycle-08-claude")
    assert actions
    assert not process.exists()
    assert (project / ".ft" / "process" / "process.yml").exists()
    assert ".ft/process/scripts/serve.sh" in (
        project / ".ft" / "process" / "process.yml"
    ).read_text()
    migrated_script = (
        project / ".ft" / "process" / "scripts" / "serve.sh"
    ).read_text()
    assert "/../../.." in migrated_script
    assert "mkdir -p .ft/process/scripts" in migrated_script
    assert (project / ".ft" / "cycles" / "cycle-08-claude" / "task_list.md").exists()
    assert (
        project / ".ft" / "cycles" / "cycle-08-claude" / "build-marker.txt"
    ).exists()
    assert (project / ".ft" / "cycles" / "cycle-01" / "retro.md").exists()
    assert (project / ".ft" / "cycles" / "cycle-01" / "cycle.yml").exists()
    assert (
        project / ".ft" / "cycles" / "legacy-unscoped" / "handoff-cycle-old.md"
    ).exists()
    assert (docs / "PRD.md").exists()
    assert ".ft/process/process.yml" in (docs / "PRD.md").read_text()
    assert ".ft/process/process.yml" in (source / "app.py").read_text()
    assert 'root / ".ft" / "process" / "process.yml"' in (source / "app.py").read_text()
    assert '(root / ".ft" / "process").mkdir(parents=True)' in (
        source / "app.py"
    ).read_text()
    assert "['docs', '.ft', 'src']" in (source / "app.py").read_text()
    assert "prefix === '.ft/process/'" in (source / "app.py").read_text()
    assert "../.ft/process/scripts/serve.sh" in (source / "Makefile").read_text()
    assert "process/process.yml" in (
        project / ".ft" / "cycles" / "cycle-08-claude" / "task_list.md"
    ).read_text()
    assert not (project / "state").exists()
    backups = list((ft_home / "migrations" / project.name).glob("*/state/engine_state.yml"))
    assert len(backups) == 1
    assert backups[0].read_text() == "must leave repo"

    rerun = migrate_legacy_layout(project, cycle_id="cycle-08-claude")
    assert rerun == ["layout canônico já presente"]


def test_migration_rejects_unsafe_cycle_id_before_moving_files(tmp_path):
    process = tmp_path / "process"
    process.mkdir()
    (process / "process.yml").write_text(MINIMAL_PROCESS)

    with pytest.raises(ValueError, match="id de ciclo inválido"):
        migrate_legacy_layout(tmp_path, cycle_id="../outside")

    assert process.exists()


def test_template_pristine_guard_rejects_execution_state(tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "engine_state.yml").write_text("bad")

    with pytest.raises(ValueError, match="estado de execução"):
        validate_template_is_pristine(tmp_path)


def test_all_shipped_templates_are_pristine():
    templates = Path(__file__).resolve().parents[2] / "templates"
    for template in templates.iterdir():
        if template.is_dir():
            validate_template_is_pristine(template)


def test_latest_cycle_artifact_uses_most_recent_file(tmp_path):
    first = tmp_path / ".ft" / "cycles" / "cycle-01"
    second = tmp_path / ".ft" / "cycles" / "cycle-02"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    old = first / "handoff.md"
    new = second / "handoff.md"
    old.write_text("old")
    new.write_text("new")
    os.utime(old, (1, 1))
    os.utime(new, (2, 2))

    assert latest_cycle_artifact(tmp_path, "handoff.md") == new


def test_runs_lists_imported_cycle_without_activity_log(tmp_path, monkeypatch, capsys):
    ft_home = tmp_path / "ft-home"
    monkeypatch.setenv("FT_HOME", str(ft_home))
    project = tmp_path / "project"
    cycle = project / ".ft" / "cycles" / "cycle-08"
    cycle.mkdir(parents=True)
    (cycle / "cycle.yml").write_text(
        """schema_version: 1
id: cycle-08
status: done
progress:
  completed: unknown
  total: unknown
""",
    )
    stale = ft_home / "worktrees" / project.name / "cycle-08" / "state"
    stale.mkdir(parents=True)
    stale.joinpath("engine_state.yml").write_text(
        """process_id: test
version: '1.0'
current_node: null
node_status: ready
completed_nodes: []
gate_log: {}
artifacts: {}
metrics:
  steps_completed: 0
  steps_total: 10
""",
    )

    cmd_runs(SimpleNamespace(project=str(project)))

    output = capsys.readouterr().out
    assert "cycle-08" in output
    assert "unknown/unknown" in output
    assert "archive" in output
    assert "runtime" not in output


def test_full_merge_archives_cycle_before_integrating_branch(tmp_path, monkeypatch):
    ft_home = tmp_path / "ft-home"
    monkeypatch.setenv("FT_HOME", str(ft_home))
    repo = tmp_path / "project"
    repo.mkdir()
    ensure_project_layout(repo)
    process = repo / ".ft" / "process" / "process.yml"
    process.write_text(
        """id: archive_test
version: '1.0'
title: Archive Test
artifact_policy:
  canonical: [docs/PRD.md]
  cycle: [docs/task_list.md]
nodes:
  - id: end
    type: end
    title: End
"""
    )
    (repo / "docs").mkdir()
    (repo / "docs" / "PRD.md").write_text("# Product")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)

    worktree = ft_home / "worktrees" / repo.name / "cycle-01"
    worktree.parent.mkdir(parents=True)
    subprocess.run(
        ["git", "worktree", "add", "-q", "-b", "cycle-01", str(worktree)],
        cwd=repo,
        check=True,
    )
    (worktree / "docs" / "task_list.md").write_text("# Cycle tasks")
    state_path = worktree / "state" / "engine_state.yml"
    runner = StepRunner(process, state_path, project_root=worktree)
    runner.init_state()
    state = runner.state_mgr.load()
    state.node_status = "done"
    state.current_node = None
    state.metrics["steps_completed"] = 1
    state.metrics["steps_total"] = 1
    runner.state_mgr.save()

    assert runner.merge_on_close("full")

    assert (repo / "docs" / "PRD.md").exists()
    assert not (repo / "docs" / "task_list.md").exists()
    assert (repo / ".ft" / "cycles" / "cycle-01" / "task_list.md").exists()
    assert (repo / ".ft" / "cycles" / "cycle-01" / "cycle.yml").exists()
    tracked = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "state/engine_state.yml" not in tracked
