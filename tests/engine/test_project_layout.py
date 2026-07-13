from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest
import yaml

from ft.engine.layout import (
    ManifestError,
    archive_cycle_artifacts,
    ensure_project_layout,
    latest_cycle_artifact,
    manifest_llm_defaults,
    migrate_legacy_layout,
    process_digest,
    read_manifest,
    register_project_process,
    resolve_project_process,
    update_manifest_llm_defaults,
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
    ensure_project_layout(
        tmp_path,
        defaults={
            "llm_engine": "opencode",
            "llm_model": "example/model",
            "llm_effort": "high",
        },
    )
    process = tmp_path / ".ft" / "process" / "base" / "process.yml"
    process.parent.mkdir(parents=True)
    process.write_text(MINIMAL_PROCESS)
    register_project_process(
        tmp_path,
        process_name="base",
        process_path=process,
        template_id="base",
        entrypoint="init",
        set_default=True,
    )

    assert (tmp_path / ".ft" / "manifest.yml").is_file()
    assert (tmp_path / ".ft" / "cycles" / ".gitkeep").is_file()
    assert not (tmp_path / ".ft" / "runtime").exists()
    assert not (tmp_path / "state").exists()
    assert manifest_llm_defaults(tmp_path) == ("opencode", "example/model", "high")


@pytest.mark.parametrize(
    "name",
    [".", "..", "nested/name", r"nested\name", "/absolute", "-leading"],
)
def test_named_process_path_rejects_unsafe_names(tmp_path, name):
    from ft.engine import paths

    with pytest.raises(ValueError, match="nome de processo inválido"):
        paths.project_named_process_dir(tmp_path, name)


def test_manifest_v2_rejects_noncanonical_process_records_and_defaults(tmp_path):
    manifest = tmp_path / ".ft" / "manifest.yml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        "schema_version: 2\n"
        "processes:\n"
        "  feature:\n"
        "    path: .ft/process/process.yml\n"
        "defaults: []\n",
        encoding="utf-8",
    )

    with pytest.raises(ManifestError, match=r"processes\.feature\.path"):
        read_manifest(tmp_path)

    manifest.write_text(
        "schema_version: 2\nprocesses: {}\ndefaults: []\n",
        encoding="utf-8",
    )
    with pytest.raises(ManifestError, match="defaults deve ser mapping"):
        read_manifest(tmp_path)


def test_named_process_symlink_is_never_registered_or_resolved(tmp_path):
    ensure_project_layout(tmp_path)
    external = tmp_path / "external"
    external.mkdir()
    (external / "process.yml").write_text(MINIMAL_PROCESS)
    named = tmp_path / ".ft/process/feature"
    named.symlink_to(external, target_is_directory=True)

    with pytest.raises(ValueError, match="simbólico"):
        register_project_process(
            tmp_path,
            process_name="feature",
            process_path=named / "process.yml",
            template_id="feature",
            entrypoint="feature",
            set_default=True,
        )

    manifest = tmp_path / ".ft/manifest.yml"
    manifest.write_text(
        "schema_version: 2\n"
        "default_process: feature\n"
        "processes:\n"
        "  feature:\n"
        "    path: .ft/process/feature/process.yml\n"
        "    template: feature\n"
        "    entrypoint: feature\n",
        encoding="utf-8",
    )
    assert resolve_project_process(tmp_path) is None


def test_nested_gitignore_keeps_process_and_cycles_trackable(tmp_path):
    ensure_project_layout(tmp_path)
    process = tmp_path / ".ft" / "process" / "base" / "process.yml"
    process.parent.mkdir()
    process.write_text(MINIMAL_PROCESS)
    (tmp_path / ".ft" / "runtime").mkdir()
    (tmp_path / ".ft" / "runtime" / "state.yml").write_text("runtime")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)

    tracked = subprocess.run(
        ["git", "check-ignore", ".ft/process/base/process.yml"],
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
    selected_process = tmp_path / ".ft" / "process" / "feature" / "process.yml"
    selected_process.parent.mkdir(parents=True)
    selected_process.write_text(MINIMAL_PROCESS)
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
        process_id="test_process",
        process_path=".ft/process/feature/process.yml",
        process_digest="sha256:initial",
        template_id="feature",
        base_commit="deadbeef",
        worktree_branch="cycle-07",
        version="1.0.0",
        llm_engine="claude",
        llm_model="claude-fable-5",
        llm_effort="max",
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
    assert record["llm"]["effort"] == "max"
    assert record["process"]["path"] == ".ft/process/feature/process.yml"
    assert record["process"]["template"] == "feature"
    assert record["process"]["initial_digest"] == "sha256:initial"
    assert record["process"]["closed_digest"].startswith("sha256:")
    assert record["git"] == {
        "base_commit": "deadbeef",
        "worktree_branch": "cycle-07",
    }


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
    assert (project / ".ft" / "process" / "test" / "process.yml").exists()
    assert ".ft/process/test/scripts/serve.sh" in (
        project / ".ft" / "process" / "test" / "process.yml"
    ).read_text()
    migrated_script = (
        project / ".ft" / "process" / "test" / "scripts" / "serve.sh"
    ).read_text()
    assert "/../../../.." in migrated_script
    assert "mkdir -p .ft/process/test/scripts" in migrated_script
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
    assert ".ft/process/test/process.yml" in (docs / "PRD.md").read_text()
    assert ".ft/process/test/process.yml" in (source / "app.py").read_text()
    assert 'root / ".ft" / "process" / "test" / "process.yml"' in (source / "app.py").read_text()
    assert '(root / ".ft" / "process").mkdir(parents=True)' in (
        source / "app.py"
    ).read_text()
    assert "['docs', '.ft', 'src']" in (source / "app.py").read_text()
    assert "prefix === '.ft/process/'" in (source / "app.py").read_text()
    assert "../.ft/process/test/scripts/serve.sh" in (source / "Makefile").read_text()
    assert "process/process.yml" in (
        project / ".ft" / "cycles" / "cycle-08-claude" / "task_list.md"
    ).read_text()
    assert not (project / "state").exists()
    backups = list((ft_home / "migrations" / project.name).glob("*/state/engine_state.yml"))
    assert len(backups) == 1
    assert backups[0].read_text() == "must leave repo"

    rerun = migrate_legacy_layout(project, cycle_id="cycle-08-claude")
    assert rerun == ["layout v2 canônico já presente"]


def test_migration_rejects_unsafe_cycle_id_before_moving_files(tmp_path):
    process = tmp_path / "process"
    process.mkdir()
    (process / "process.yml").write_text(MINIMAL_PROCESS)

    with pytest.raises(ValueError, match="id de ciclo inválido"):
        migrate_legacy_layout(tmp_path, cycle_id="../outside")

    assert process.exists()


def test_migrate_flat_v1_to_named_v2_preserves_defaults_and_named_processes(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
    project = tmp_path / "project"
    flat = project / ".ft" / "process"
    scripts = flat / "scripts"
    scripts.mkdir(parents=True)
    (flat / "process.yml").write_text(
        MINIMAL_PROCESS.replace("id: test", "id: fast_track_v3")
        + "\n# .ft/process/scripts/serve.sh\n",
        encoding="utf-8",
    )
    (scripts / "serve.sh").write_text(
        'ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"\n',
        encoding="utf-8",
    )
    feature = flat / "feature" / "process.yml"
    feature.parent.mkdir()
    feature.write_text(MINIMAL_PROCESS.replace("id: test", "id: feature"))
    (project / ".ft" / "manifest.yml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "process": ".ft/process/process.yml",
                "template": {"id": "migrated-local"},
                "processes": {
                    "feature": {
                        "path": ".ft/process/feature/process.yml",
                        "template": "feature",
                        "entrypoint": "feature",
                    }
                },
                "defaults": {
                    "llm_engine": "codex",
                    "llm_model": "gpt-5.6-sol",
                    "llm_effort": "max",
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    migrate_legacy_layout(project)

    manifest = yaml.safe_load((project / ".ft" / "manifest.yml").read_text())
    assert manifest["schema_version"] == 2
    assert manifest["default_process"] == "mvp-builder"
    assert "process" not in manifest
    assert "template" not in manifest
    assert manifest["defaults"] == {
        "llm_engine": "codex",
        "llm_model": "gpt-5.6-sol",
        "llm_effort": "max",
    }
    assert manifest["processes"]["mvp-builder"]["path"] == (
        ".ft/process/mvp-builder/process.yml"
    )
    assert manifest["processes"]["mvp-builder"]["template"] == "mvp-builder"
    assert manifest["processes"]["feature"]["path"] == (
        ".ft/process/feature/process.yml"
    )
    assert not (flat / "process.yml").exists()
    assert not (flat / "scripts").exists()
    assert feature.exists()
    migrated_script = flat / "mvp-builder" / "scripts" / "serve.sh"
    assert "/../../../.." in migrated_script.read_text()
    assert "/../../../../.." not in migrated_script.read_text()
    assert migrate_legacy_layout(project) == ["layout v2 canônico já presente"]


def test_migration_refuses_while_external_cycle_state_exists(tmp_path, monkeypatch):
    ft_home = tmp_path / "ft-home"
    monkeypatch.setenv("FT_HOME", str(ft_home))
    project = tmp_path / "project"
    flat = project / ".ft" / "process" / "process.yml"
    flat.parent.mkdir(parents=True)
    flat.write_text(MINIMAL_PROCESS)
    state = ft_home / "worktrees" / project.name / "cycle-01" / "state" / "engine_state.yml"
    state.parent.mkdir(parents=True)
    state.write_text("node_status: running\n")

    with pytest.raises(RuntimeError, match="feche ou aborte"):
        migrate_legacy_layout(project)

    assert flat.exists()


def test_canonical_v2_migration_is_noop_even_with_external_state(tmp_path, monkeypatch):
    ft_home = tmp_path / "ft-home"
    monkeypatch.setenv("FT_HOME", str(ft_home))
    project = tmp_path / "project"
    process = project / ".ft/process/base/process.yml"
    process.parent.mkdir(parents=True)
    process.write_text(MINIMAL_PROCESS)
    ensure_project_layout(project)
    register_project_process(
        project,
        process_name="base",
        process_path=process,
        template_id="base",
        entrypoint="init",
        set_default=True,
    )
    external = ft_home / "worktrees" / project.name / "cycle-01/state/engine_state.yml"
    external.parent.mkdir(parents=True)
    external.write_text("node_status: running\n")

    assert migrate_legacy_layout(project) == ["layout v2 canônico já presente"]


def test_migration_blocks_active_local_state_but_archives_terminal_state(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
    active = tmp_path / "active"
    flat = active / ".ft/process/process.yml"
    flat.parent.mkdir(parents=True)
    flat.write_text(MINIMAL_PROCESS)
    state = active / "state/engine_state.yml"
    state.parent.mkdir(parents=True)
    state.write_text("current_node: build\nnode_status: running\n")

    with pytest.raises(RuntimeError, match="feche ou aborte"):
        migrate_legacy_layout(active)
    assert flat.exists()
    assert state.exists()

    done = tmp_path / "done"
    done_flat = done / ".ft/process/process.yml"
    done_flat.parent.mkdir(parents=True)
    done_flat.write_text(MINIMAL_PROCESS)
    done_state = done / "state/engine_state.yml"
    done_state.parent.mkdir(parents=True)
    done_state.write_text(
        "current_node: null\nnode_status: done\ncompleted_nodes: [end]\n"
    )

    migrate_legacy_layout(done)
    assert not done_state.exists()
    assert (done / ".ft/process/test/process.yml").exists()


def test_migration_preflights_process_collision_without_moving_sources(tmp_path):
    project = tmp_path / "project"
    catalog = project / ".ft/process"
    flat = catalog / "process.yml"
    scripts = catalog / "scripts"
    destination = catalog / "test/process.yml"
    scripts.mkdir(parents=True)
    destination.parent.mkdir(parents=True)
    flat.write_text(MINIMAL_PROCESS)
    (scripts / "serve.sh").write_text("source\n")
    destination.write_text(MINIMAL_PROCESS.replace("title: Test", "title: Durable"))

    with pytest.raises(FileExistsError, match="conflito durante migração"):
        migrate_legacy_layout(project)

    assert flat.read_text() == MINIMAL_PROCESS
    assert (scripts / "serve.sh").read_text() == "source\n"
    assert "Durable" in destination.read_text()


def test_migration_preflights_durable_cycle_history_without_overwrite(tmp_path):
    project = tmp_path / "project"
    legacy = project / "process/process.yml"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(MINIMAL_PROCESS)
    imported = project / "docs/archive/cycle-01/retro.md"
    imported.parent.mkdir(parents=True)
    imported.write_text("LEGACY")
    durable = project / ".ft/cycles/cycle-01/retro.md"
    durable.parent.mkdir(parents=True)
    durable.write_text("DURABLE")
    record = durable.parent / "cycle.yml"
    record.write_text("id: cycle-01\nstatus: done\n")

    with pytest.raises(FileExistsError, match="conflito durante migração"):
        migrate_legacy_layout(project)

    assert legacy.exists()
    assert imported.read_text() == "LEGACY"
    assert durable.read_text() == "DURABLE"
    assert record.read_text() == "id: cycle-01\nstatus: done\n"


def test_migration_reconciles_existing_cycle_inventory_without_losing_fields(tmp_path):
    project = tmp_path / "project"
    legacy = project / "process/process.yml"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(MINIMAL_PROCESS)
    imported = project / "docs/archive/cycle-01/new-report.md"
    imported.parent.mkdir(parents=True)
    imported.write_text("new")
    cycle = project / ".ft/cycles/cycle-01"
    cycle.mkdir(parents=True)
    (cycle / "durable.md").write_text("durable")
    record = cycle / "cycle.yml"
    record.write_text(
        "schema_version: 2\n"
        "id: cycle-01\n"
        "status: done\n"
        "custom: preserve-me\n"
        "artifacts: [durable.md]\n"
    )

    migrate_legacy_layout(project)

    reconciled = yaml.safe_load(record.read_text())
    assert reconciled["status"] == "done"
    assert reconciled["custom"] == "preserve-me"
    assert reconciled["artifacts"] == ["durable.md", "new-report.md"]


@pytest.mark.parametrize("record_kind", ["invalid", "symlink"])
def test_migration_validates_cycle_record_before_any_mutation(tmp_path, record_kind):
    project = tmp_path / "project"
    legacy = project / "process/process.yml"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(MINIMAL_PROCESS)
    imported = project / "docs/archive/cycle-01/new-report.md"
    imported.parent.mkdir(parents=True)
    imported.write_text("new")
    cycle = project / ".ft/cycles/cycle-01"
    cycle.mkdir(parents=True)
    record = cycle / "cycle.yml"
    if record_kind == "invalid":
        record.write_text("[not, a, mapping]\n")
        expected_error = "raiz deve ser mapping"
    else:
        external = tmp_path / "external-cycle.yml"
        external.write_text("id: external\nstatus: done\n")
        record.symlink_to(external)
        expected_error = "link simbólico ancestral"

    with pytest.raises((ManifestError, ValueError), match=expected_error):
        migrate_legacy_layout(project)

    assert legacy.exists()
    assert imported.exists()
    assert not (project / ".ft/process/test/process.yml").exists()


@pytest.mark.parametrize("linked_path", ["process", "cycles", "legacy-unscoped"])
def test_migration_rejects_symlinked_catalog_or_history_ancestors_atomically(
    tmp_path,
    linked_path,
):
    project = tmp_path / "project"
    external = tmp_path / f"external-{linked_path}"
    external.mkdir()
    ft_dir = project / ".ft"
    ft_dir.mkdir(parents=True)

    if linked_path == "process":
        (external / "process.yml").write_text(MINIMAL_PROCESS)
        (ft_dir / "process").symlink_to(external, target_is_directory=True)
        source = external / "process.yml"
    else:
        source = project / "process/process.yml"
        source.parent.mkdir(parents=True)
        source.write_text(MINIMAL_PROCESS)
        if linked_path == "cycles":
            (ft_dir / "cycles").symlink_to(external, target_is_directory=True)
        else:
            cycles = ft_dir / "cycles"
            cycles.mkdir()
            (cycles / "legacy-unscoped").symlink_to(
                external,
                target_is_directory=True,
            )

    before = source.read_text()
    with pytest.raises(ValueError, match="link simbólico ancestral"):
        migrate_legacy_layout(project)

    assert source.read_text() == before
    assert not any(external.rglob("mvp-builder"))
    assert not any(external.rglob("test"))


def test_migration_rejects_symlinked_default_destination_atomically(tmp_path):
    project = tmp_path / "project"
    catalog = project / ".ft/process"
    catalog.mkdir(parents=True)
    flat = catalog / "process.yml"
    flat.write_text(MINIMAL_PROCESS)
    external = tmp_path / "external-default"
    external.mkdir()
    (catalog / "test").symlink_to(external, target_is_directory=True)

    with pytest.raises(ValueError, match="link simbólico ancestral"):
        migrate_legacy_layout(project)

    assert flat.exists()
    assert list(external.iterdir()) == []


def test_migration_rejects_symlinked_registered_named_process_atomically(tmp_path):
    project = tmp_path / "project"
    catalog = project / ".ft/process"
    catalog.mkdir(parents=True)
    flat = catalog / "process.yml"
    flat.write_text(MINIMAL_PROCESS)
    external = tmp_path / "external-feature"
    external.mkdir()
    (external / "process.yml").write_text(
        MINIMAL_PROCESS.replace("id: test", "id: feature")
    )
    (catalog / "feature").symlink_to(external, target_is_directory=True)
    manifest = project / ".ft/manifest.yml"
    manifest.write_text(
        "schema_version: 1\n"
        "process: .ft/process/process.yml\n"
        "processes:\n"
        "  feature:\n"
        "    path: .ft/process/feature/process.yml\n"
        "    template: feature\n"
        "    entrypoint: feature\n"
    )

    with pytest.raises(ValueError, match="link simbólico ancestral"):
        migrate_legacy_layout(project)

    assert flat.exists()
    assert "schema_version: 1" in manifest.read_text()
    assert (external / "process.yml").exists()


def test_migration_validates_candidate_and_future_schema_before_mutation(tmp_path):
    invalid = tmp_path / "invalid"
    flat = invalid / ".ft/process/process.yml"
    flat.parent.mkdir(parents=True)
    flat.write_text(MINIMAL_PROCESS)
    manifest = invalid / ".ft/manifest.yml"
    manifest.write_text(
        "schema_version: 1\n"
        "process: .ft/process/process.yml\n"
        "defaults: [bad]\n"
    )
    with pytest.raises(ManifestError, match="defaults deve ser mapping"):
        migrate_legacy_layout(invalid)
    assert flat.exists()

    future = tmp_path / "future"
    future_flat = future / ".ft/process/process.yml"
    future_flat.parent.mkdir(parents=True)
    future_flat.write_text(MINIMAL_PROCESS)
    (future / ".ft/manifest.yml").write_text(
        "schema_version: 99\nprocess: .ft/process/process.yml\n"
    )
    with pytest.raises(ManifestError, match="migração automática recusada"):
        migrate_legacy_layout(future)
    assert future_flat.exists()


def test_migration_repairs_hybrid_v2_and_keeps_default_named_process_idempotent(
    tmp_path,
):
    project = tmp_path / "project"
    flat = project / ".ft/process/process.yml"
    flat.parent.mkdir(parents=True)
    flat.write_text(MINIMAL_PROCESS.replace("id: test", "id: process"))
    manifest = project / ".ft/manifest.yml"
    manifest.write_text(
        "schema_version: 2\n"
        "process: .ft/process/process.yml\n"
        "template: process\n"
        "processes: {}\n"
    )

    migrate_legacy_layout(project)

    migrated = yaml.safe_load(manifest.read_text())
    assert migrated["default_process"] == "process"
    assert migrated["processes"]["process"]["path"] == (
        ".ft/process/process/process.yml"
    )
    assert ".ft/process/.ft/process" not in manifest.read_text()
    assert migrate_legacy_layout(project) == ["layout v2 canônico já presente"]


def test_process_digest_ignores_generated_caches_but_tracks_runtime_bundle(tmp_path):
    process = tmp_path / ".ft/process/feature/process.yml"
    script = process.parent / "scripts" / "validate.py"
    script.parent.mkdir(parents=True)
    process.write_text(MINIMAL_PROCESS)
    script.write_text("print('v1')\n")
    initial = process_digest(process)

    cache = script.parent / "__pycache__"
    cache.mkdir()
    (cache / "validate.cpython-312.pyc").write_bytes(b"generated")
    (script.parent / ".pytest_cache").mkdir()
    (script.parent / ".pytest_cache" / "state").write_text("generated")
    assert process_digest(process) == initial

    script.write_text("print('v2')\n")
    assert process_digest(process) != initial


@pytest.mark.parametrize("dirname", ["runtime", "state", "logs", "runs", ".cache"])
def test_process_digest_tracks_semantic_files_in_runtime_like_directories(
    tmp_path,
    dirname,
):
    process = tmp_path / ".ft/process/feature/process.yml"
    semantic = process.parent / "scripts" / dirname / "policy.txt"
    semantic.parent.mkdir(parents=True)
    process.write_text(MINIMAL_PROCESS)
    semantic.write_text("v1\n")
    initial = process_digest(process)

    semantic.write_text("v2\n")

    assert process_digest(process) != initial


def test_process_digest_rejects_symlinked_scripts_directory(tmp_path):
    process = tmp_path / ".ft/process/feature/process.yml"
    process.parent.mkdir(parents=True)
    process.write_text(MINIMAL_PROCESS)
    external_scripts = tmp_path / "external-scripts"
    external_scripts.mkdir()
    (external_scripts / "serve.sh").write_text("#!/bin/sh\n")
    (process.parent / "scripts").symlink_to(
        external_scripts,
        target_is_directory=True,
    )

    with pytest.raises(ValueError, match="link simbólico"):
        process_digest(process)


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
    process = repo / ".ft" / "process" / "archive-test" / "process.yml"
    process.parent.mkdir()
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
    register_project_process(
        repo,
        process_name="archive-test",
        process_path=process,
        template_id="archive-test",
        entrypoint="init",
        set_default=True,
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
    runner = StepRunner(
        worktree / process.relative_to(repo),
        state_path,
        project_root=worktree,
    )
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


def test_docs_copy_merge_preserves_live_main_manifest(tmp_path, monkeypatch):
    ft_home = tmp_path / "ft-home"
    monkeypatch.setenv("FT_HOME", str(ft_home))
    repo = tmp_path / "project"
    repo.mkdir()
    ensure_project_layout(repo)
    process = repo / ".ft/process/archive-test/process.yml"
    process.parent.mkdir()
    process.write_text(MINIMAL_PROCESS)
    register_project_process(
        repo,
        process_name="archive-test",
        process_path=process,
        template_id="archive-test",
        entrypoint="init",
        set_default=True,
    )
    (repo / "docs").mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=repo, check=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)

    worktree = ft_home / "worktrees" / repo.name / "cycle-01"
    worktree.parent.mkdir(parents=True)
    subprocess.run(
        ["git", "worktree", "add", "-q", "-b", "cycle-docs", str(worktree)],
        cwd=repo,
        check=True,
    )
    (worktree / "docs").mkdir(exist_ok=True)
    (worktree / "docs/task_list.md").write_text("# Cycle")
    runner = StepRunner(
        worktree / process.relative_to(repo),
        worktree / "state/engine_state.yml",
        project_root=worktree,
    )
    runner.init_state()
    update_manifest_llm_defaults(
        repo,
        llm_engine="codex",
        llm_model="gpt-live",
        llm_effort="max",
    )

    assert runner.merge_on_close("docs")

    manifest = yaml.safe_load((repo / ".ft/manifest.yml").read_text())
    assert manifest["defaults"] == {
        "llm_engine": "codex",
        "llm_model": "gpt-live",
        "llm_effort": "max",
    }
    assert manifest["llm_defaults_revision"] == 1
    assert (repo / ".ft/cycles/cycle-01/task_list.md").exists()

    live_manifest = (repo / ".ft/manifest.yml").read_text()
    worktree_manifest = worktree / ".ft/manifest.yml"
    external_manifest = tmp_path / "external-cycle-manifest.yml"
    external_manifest.write_text(worktree_manifest.read_text())
    worktree_manifest.unlink()
    worktree_manifest.symlink_to(external_manifest)
    assert not runner.merge_on_close("docs")
    assert (repo / ".ft/manifest.yml").read_text() == live_manifest


def test_fallback_copy_and_selective_manifest_preserve_live_defaults(
    tmp_path,
    monkeypatch,
):
    ft_home = tmp_path / "ft-home"
    monkeypatch.setenv("FT_HOME", str(ft_home))
    repo = tmp_path / "project"
    repo.mkdir()
    ensure_project_layout(repo)
    process = repo / ".ft/process/base/process.yml"
    process.parent.mkdir()
    process.write_text(MINIMAL_PROCESS)
    register_project_process(
        repo,
        process_name="base",
        process_path=process,
        template_id="base",
        entrypoint="init",
        set_default=True,
    )
    (repo / "docs").mkdir()

    work = ft_home / "worktrees" / repo.name / "cycle-01"
    shutil.copytree(repo / ".ft", work / ".ft")
    (work / "docs").mkdir()
    (work / "docs/task_list.md").write_text("# Cycle")
    runner = StepRunner(
        work / ".ft/process/base/process.yml",
        work / "state/engine_state.yml",
        project_root=work,
    )
    runner.init_state()
    update_manifest_llm_defaults(
        repo,
        llm_engine="opencode",
        llm_model="provider/live",
        llm_effort=None,
    )
    monkeypatch.chdir(repo)

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr("ft.engine.runner.auto_commit", lambda *_args, **_kwargs: (True, "ok"))
        assert runner.merge_on_close("docs")

    live_manifest = (repo / ".ft/manifest.yml").read_text()
    assert "provider/live" in live_manifest
    assert (repo / ".ft/cycles/cycle-01/task_list.md").exists()

    external_manifest = tmp_path / "malicious-manifest.yml"
    external_manifest.write_text("schema_version: 2\nprocesses: {}\n")
    (work / ".ft/manifest.yml").unlink()
    (work / ".ft/manifest.yml").symlink_to(external_manifest)
    assert runner._merge_by_copy("selective", [".ft/manifest.yml"])
    assert (repo / ".ft/manifest.yml").read_text() == live_manifest

    external_destination = tmp_path / "external-destination"
    external_destination.mkdir()
    destination_scripts = repo / ".ft/process/base/scripts"
    destination_scripts.symlink_to(
        external_destination,
        target_is_directory=True,
    )
    work_scripts = work / ".ft/process/base/scripts"
    work_scripts.mkdir()
    (work_scripts / "hook.sh").write_text("#!/bin/sh\n")
    assert not runner._merge_by_copy("docs")
    assert list(external_destination.iterdir()) == []

    destination_scripts.unlink()
    external_source = tmp_path / "external-source"
    external_source.mkdir()
    (external_source / "payload.txt").write_text("outside")
    (work / ".ft/process/linked").symlink_to(
        external_source,
        target_is_directory=True,
    )
    assert not runner._merge_by_copy("docs")
    assert not (repo / ".ft/process/linked").exists()

    external_docs = tmp_path / "external-docs"
    external_docs.mkdir()
    (external_docs / "secret.txt").write_text("do not copy")
    (work / "docs/link").symlink_to(external_docs, target_is_directory=True)
    assert not runner._merge_by_copy(
        "selective",
        ["docs/link/secret.txt"],
    )
    assert not (repo / "docs/link/secret.txt").exists()
