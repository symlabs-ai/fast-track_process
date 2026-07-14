"""Focused coverage for process selection persisted in state and hooks."""

from __future__ import annotations

import stat

import pytest
import yaml

from ft.engine.hooks import load_environment, run_hooks
from ft.engine.layout import process_digest
from ft.engine.state import StateManager


def test_legacy_state_without_process_selection_fields_loads(tmp_path):
    state_path = tmp_path / "engine_state.yml"
    state_path.write_text("process_id: legacy\ncurrent_node: legacy.start\n")

    state = StateManager(state_path).load()

    assert state.process_path is None
    assert state.process_digest is None
    assert state.process_immutable is False
    assert state.template_id is None
    assert state.base_commit is None
    assert state.worktree_branch is None


def test_init_from_graph_persists_process_selection_fields(tmp_path):
    state_path = tmp_path / "engine_state.yml"
    manager = StateManager(state_path)
    manager.init_from_graph(
        {"id": "feature", "version": "1.0.0"},
        first_node_id="feature.preflight",
        total_steps=12,
        process_path=".ft/process/feature/process.yml",
        process_digest="sha256:abc123",
        process_immutable=True,
        template_id="feature",
        base_commit="deadbeef",
        worktree_branch="ft/feature-cycle-01",
    )

    state = StateManager(state_path).load()

    assert state.process_path == ".ft/process/feature/process.yml"
    assert state.process_digest == "sha256:abc123"
    assert state.process_immutable is True
    assert state.template_id == "feature"
    assert state.base_commit == "deadbeef"
    assert state.worktree_branch == "ft/feature-cycle-01"


def test_advance_does_not_duplicate_completed_node(tmp_path):
    manager = StateManager(tmp_path / "engine_state.yml")
    manager.init_from_graph({"id": "feature"}, "feature.start", 2)

    manager.advance("feature.start", "feature.end")
    manager.advance("feature.start", "feature.end")

    state = manager.load()
    assert state.completed_nodes == ["feature.start"]
    assert state.metrics["steps_completed"] == 1


def test_advance_guarded_does_not_duplicate_completed_node(tmp_path):
    manager = StateManager(tmp_path / "engine_state.yml")
    manager.init_from_graph({"id": "feature"}, "feature.start", 2)

    manager.advance_guarded("feature.start", "feature.end")
    manager.advance_guarded("feature.start", "feature.end")

    state = manager.load()
    assert state.completed_nodes == ["feature.start"]
    assert state.metrics["steps_completed"] == 1


def test_load_environment_uses_selected_process_path(tmp_path):
    legacy_dir = tmp_path / ".ft" / "process"
    feature_dir = legacy_dir / "feature"
    feature_dir.mkdir(parents=True)
    (legacy_dir / "environment.yml").write_text("name: legacy\n")
    (feature_dir / "environment.yml").write_text("name: feature\n")

    environment = load_environment(
        tmp_path,
        process_path=".ft/process/feature/process.yml",
    )

    assert environment == {"name": "feature"}


def test_load_environment_accepts_selected_process_dir(tmp_path):
    process_dir = tmp_path / ".ft" / "process" / "feature"
    process_dir.mkdir(parents=True)
    (process_dir / "environment.yml").write_text("name: feature\n")

    environment = load_environment(tmp_path, process_dir=process_dir)

    assert environment == {"name": "feature"}


def test_run_hooks_resolves_scripts_next_to_selected_process(tmp_path):
    legacy_dir = tmp_path / ".ft" / "process"
    feature_dir = legacy_dir / "feature"
    scripts_dir = feature_dir / "scripts"
    scripts_dir.mkdir(parents=True)

    environment = {"hooks": {"on_init": ["./scripts/selected.sh"]}}
    (feature_dir / "environment.yml").write_text(yaml.safe_dump(environment))
    script = scripts_dir / "selected.sh"
    script.write_text("#!/bin/sh\nprintf feature\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)

    results = run_hooks(
        "on_init",
        tmp_path,
        process_path=".ft/process/feature/process.yml",
    )

    assert results == [("./scripts/selected.sh", True, "feature")]


def test_run_hooks_requires_explicit_process_selection(tmp_path):
    scripts_dir = tmp_path / ".ft" / "process" / "scripts"
    scripts_dir.mkdir(parents=True)
    script = scripts_dir / "legacy.sh"
    script.write_text("#!/bin/sh\nprintf legacy\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)

    with pytest.raises(
        ValueError,
        match="process_path ou process_dir é obrigatório; não existe template principal",
    ):
        run_hooks(
            "on_init",
            tmp_path,
            environment={"hooks": {"on_init": ["./scripts/legacy.sh"]}},
        )


def test_process_digest_covers_environment_scripts_and_executable_mode(tmp_path):
    process_dir = tmp_path / ".ft" / "process" / "feature"
    scripts_dir = process_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    process = process_dir / "process.yml"
    environment = process_dir / "environment.yml"
    script = scripts_dir / "serve.sh"
    process.write_text("id: feature\nnodes: []\n")
    environment.write_text("run_mode: isolated\n")
    script.write_text("#!/bin/sh\nprintf ready\n")

    baseline = process_digest(process)
    environment.write_text("run_mode: continuous\n")
    assert process_digest(process) != baseline

    environment.write_text("run_mode: isolated\n")
    assert process_digest(process) == baseline
    script.write_text("#!/bin/sh\nprintf changed\n")
    assert process_digest(process) != baseline

    script.write_text("#!/bin/sh\nprintf ready\n")
    assert process_digest(process) == baseline
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    assert process_digest(process) != baseline


def test_run_hooks_rejects_relative_and_absolute_escape(tmp_path):
    process_dir = tmp_path / ".ft" / "process" / "feature"
    process_dir.mkdir(parents=True)
    marker = tmp_path / "escaped"
    outside = tmp_path / "outside.sh"
    outside.write_text(f"#!/bin/sh\ntouch {marker}\n")
    outside.chmod(outside.stat().st_mode | stat.S_IEXEC)
    relative_escape = "../../../outside.sh"

    results = run_hooks(
        "on_init",
        tmp_path,
        environment={
            "hooks": {"on_init": [relative_escape, str(outside.resolve())]}
        },
        process_path=".ft/process/feature/process.yml",
    )

    assert len(results) == 2
    assert all(success is False for _, success, _ in results)
    assert all("fora do processo local" in detail for _, _, detail in results)
    assert not marker.exists()


def test_process_digest_rejects_symlinked_runtime_file(tmp_path):
    process_dir = tmp_path / ".ft" / "process" / "feature"
    scripts_dir = process_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    process = process_dir / "process.yml"
    process.write_text("id: feature\nnodes: []\n")
    outside = tmp_path / "outside.sh"
    outside.write_text("#!/bin/sh\n")
    (scripts_dir / "outside.sh").symlink_to(outside)

    with pytest.raises(ValueError, match="link simbólico não permitido"):
        process_digest(process)
