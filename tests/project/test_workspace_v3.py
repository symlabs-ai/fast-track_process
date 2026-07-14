from __future__ import annotations

from pathlib import Path
import subprocess

import pytest
import yaml

from ft.engine.layout import (
    ManifestError,
    ensure_project_layout,
    get_project_process_record,
    iter_project_process_records,
    list_project_processes,
    migrate_legacy_layout,
    read_manifest,
    register_project_process,
    resolve_project_process,
)
from ft.project import (
    BootstrapError,
    bootstrap_project,
    check_project,
    migrate_v2_manifest,
    repair_project,
)
from ft.templates import TemplateCatalogError, resolve_template


MINIMAL_PROCESS = """id: test
version: '1.0'
title: Test
nodes:
  - id: end
    type: end
    title: End
"""


@pytest.fixture(autouse=True)
def isolated_ft_home(tmp_path, monkeypatch):
    monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _initialized_git(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    (root / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(root, "add", "seed.txt")
    _git(
        root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-q",
        "-m",
        "seed",
    )


def test_v3_manifest_is_empty_catalog_without_default(tmp_path):
    project = tmp_path / "project"
    ensure_project_layout(project)

    manifest = read_manifest(project)
    assert manifest["schema_version"] == 3
    assert manifest["processes"] == {}
    assert "default_process" not in manifest
    assert list_project_processes(project) == ()
    assert iter_project_process_records(project) == ()


def test_v3_registration_and_resolution_are_explicit(tmp_path):
    project = tmp_path / "project"
    ensure_project_layout(project)
    for name in ("tweak", "feature"):
        process = project / ".ft/process" / name / "process.yml"
        process.parent.mkdir(parents=True)
        process.write_text(MINIMAL_PROCESS.replace("id: test", f"id: {name}"))
        register_project_process(
            project,
            process_name=name,
            process_path=process,
            template_id=name,
            entrypoint="run",
            set_default=True,
        )

    assert list_project_processes(project) == ("feature", "tweak")
    assert [name for name, _record in iter_project_process_records(project)] == [
        "feature",
        "tweak",
    ]
    assert get_project_process_record(project, "feature")["template"] == "feature"
    assert resolve_project_process(project) is None
    assert resolve_project_process(project, "tweak") == (
        project / ".ft/process/tweak/process.yml"
    ).resolve()
    assert "default_process" not in read_manifest(project)


def test_v2_is_readable_but_next_write_normalizes_to_v3(tmp_path):
    project = tmp_path / "project"
    process = project / ".ft/process/feature/process.yml"
    process.parent.mkdir(parents=True)
    process.write_text(MINIMAL_PROCESS)
    manifest_path = project / ".ft/manifest.yml"
    manifest_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 2,
                "default_process": "feature",
                "processes": {
                    "feature": {
                        "path": ".ft/process/feature/process.yml",
                        "template": "feature",
                        "entrypoint": "feature",
                    }
                },
            },
            sort_keys=False,
        )
    )

    assert resolve_project_process(project) == process.resolve()
    ensure_project_layout(project)
    migrated = read_manifest(project)
    assert migrated["schema_version"] == 3
    assert "default_process" not in migrated
    assert migrated["processes"]["feature"]["path"] == (
        ".ft/process/feature/process.yml"
    )
    assert migrated["processes"]["feature"]["v2_run_compatibility"] == {
        "version": 1,
        "legacy_entrypoint": "feature",
    }
    assert resolve_template(
        project,
        "feature",
        catalog_root=tmp_path / "catalog-does-not-exist",
    ).process_file == process.resolve()


def test_v1_migration_keeps_local_process_runnable_as_explicit_template(tmp_path):
    project = tmp_path / "project"
    process = project / "process/process.yml"
    process.parent.mkdir(parents=True)
    process.write_text(MINIMAL_PROCESS.replace("id: test", "id: feature"))
    manifest_path = project / ".ft/manifest.yml"
    manifest_path.parent.mkdir()
    manifest_path.write_text(
        "schema_version: 1\n"
        "process: process/process.yml\n"
        "template: feature\n",
        encoding="utf-8",
    )

    migrate_legacy_layout(project)

    migrated_process = project / ".ft/process/feature/process.yml"
    record = read_manifest(project)["processes"]["feature"]
    assert record["entrypoint"] == "init"
    assert record["v2_run_compatibility"] == {
        "version": 1,
        "legacy_entrypoint": "init",
    }
    assert resolve_template(
        project,
        "feature",
        catalog_root=tmp_path / "catalog-does-not-exist",
    ).process_file == migrated_process.resolve()


def test_v3_rejects_default_process(tmp_path):
    manifest = tmp_path / ".ft/manifest.yml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        "schema_version: 3\ndefault_process: feature\nprocesses: {}\n",
        encoding="utf-8",
    )
    with pytest.raises(ManifestError, match="não aceita default_process"):
        read_manifest(tmp_path)


def test_bootstrap_creates_git_head_common_scaffold_and_is_idempotent(tmp_path):
    project = tmp_path / "new-project"
    result = bootstrap_project(project)

    assert result.status == "created"
    assert result.created_repository is True
    assert result.commit == _git(project, "rev-parse", "HEAD")
    assert _git(project, "status", "--porcelain") == ""
    assert read_manifest(project) == {"schema_version": 3, "processes": {}}
    assert (project / ".ft/process/.gitkeep").is_file()
    assert (project / ".ft/cycles/.gitkeep").is_file()
    assert not (project / "docs").exists()
    assert not (project / "src").exists()
    assert (project / "AGENTS.md").is_file()
    assert list((project / ".ft/process").glob("*/process.yml")) == []

    head = result.commit
    rerun = bootstrap_project(project)
    assert rerun.status == "unchanged"
    assert rerun.commit is None
    assert _git(project, "rev-parse", "HEAD") == head


def test_bootstrap_refuses_nonempty_directory_without_git(tmp_path):
    project = tmp_path / "existing"
    project.mkdir()
    (project / "product.txt").write_text("do not adopt")

    with pytest.raises(BootstrapError, match="não vazio"):
        bootstrap_project(project)
    assert not (project / ".git").exists()
    assert not (project / ".ft").exists()


def test_bootstrap_adds_scaffold_to_clean_existing_repository(tmp_path):
    project = tmp_path / "existing-git"
    _initialized_git(project)
    product_before = (project / "seed.txt").read_bytes()
    old_head = _git(project, "rev-parse", "HEAD")

    result = bootstrap_project(project)

    assert result.status == "updated"
    assert result.created_repository is False
    assert result.commit and result.commit != old_head
    assert (project / "seed.txt").read_bytes() == product_before
    assert _git(project, "status", "--porcelain") == ""


def test_bootstrap_refuses_dirty_existing_repository_without_writes(tmp_path):
    project = tmp_path / "dirty-git"
    _initialized_git(project)
    (project / "uncommitted.txt").write_text("mine", encoding="utf-8")

    with pytest.raises(BootstrapError, match="deve estar limpo"):
        bootstrap_project(project)

    assert not (project / ".ft").exists()
    assert not (project / "docs").exists()


def test_bootstrap_adopts_nonempty_directory_without_git(tmp_path):
    project = tmp_path / "legacy"
    project.mkdir()
    (project / "app.py").write_text("VALUE = 1\n", encoding="utf-8")

    result = bootstrap_project(project, adopt=True)

    assert result.status == "created"
    assert result.created_repository is True
    assert result.commit == _git(project, "rev-parse", "HEAD")
    assert read_manifest(project) == {"schema_version": 3, "processes": {}}
    # A adoção nunca commita o legado silenciosamente: app.py continua fora do Git.
    assert "app.py" in _git(project, "status", "--porcelain")
    assert (project / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"


def test_bootstrap_adopts_dirty_existing_repository(tmp_path):
    project = tmp_path / "dirty-adopt"
    _initialized_git(project)
    (project / "uncommitted.txt").write_text("mine", encoding="utf-8")

    result = bootstrap_project(project, adopt=True)

    assert result.status == "updated"
    assert result.created_repository is False
    assert result.commit == _git(project, "rev-parse", "HEAD")
    assert (project / ".ft/manifest.yml").is_file()
    assert "uncommitted.txt" in _git(project, "status", "--porcelain")
    assert (project / "uncommitted.txt").read_text(encoding="utf-8") == "mine"


def test_v2_migration_dry_run_and_apply_preserve_bundles_and_cycles(tmp_path):
    project = tmp_path / "project"
    process = project / ".ft/process/feature/process.yml"
    process.parent.mkdir(parents=True)
    process.write_text(MINIMAL_PROCESS, encoding="utf-8")
    cycle = project / ".ft/cycles/cycle-01/cycle.yml"
    cycle.parent.mkdir(parents=True)
    cycle.write_text("id: cycle-01\nstatus: done\n", encoding="utf-8")
    manifest_path = project / ".ft/manifest.yml"
    manifest_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 2,
                "default_process": "feature",
                "processes": {
                    "feature": {
                        "path": ".ft/process/feature/process.yml",
                        "template": "feature",
                        "entrypoint": "feature",
                    }
                },
                "defaults": {"llm_engine": "codex"},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    manifest_before = manifest_path.read_bytes()
    process_before = process.read_bytes()
    cycle_before = cycle.read_bytes()

    preview = migrate_v2_manifest(project, dry_run=True)
    assert preview.status == "would_migrate"
    assert manifest_path.read_bytes() == manifest_before

    result = migrate_v2_manifest(project)
    assert result.status == "migrated"
    assert result.backup_path is not None and result.backup_path.read_bytes() == manifest_before
    manifest = read_manifest(project)
    assert manifest["schema_version"] == 3
    assert "default_process" not in manifest
    assert manifest["defaults"] == {"llm_engine": "codex"}
    assert manifest["processes"]["feature"]["entrypoint"] == "feature"
    assert manifest["processes"]["feature"]["v2_run_compatibility"] == {
        "version": 1,
        "legacy_entrypoint": "feature",
    }
    assert process.read_bytes() == process_before
    assert cycle.read_bytes() == cycle_before
    resolved = resolve_template(
        project,
        "feature",
        catalog_root=tmp_path / "catalog-does-not-exist",
    )
    assert resolved.process_file == process.resolve()
    assert resolved.current_source_digest is None
    assert migrate_v2_manifest(project).status == "unchanged"


def test_v2_migration_bridges_init_and_feature_without_rewriting_forks(tmp_path):
    project = tmp_path / "project"
    feature = project / ".ft/process/feature/process.yml"
    feature.parent.mkdir(parents=True)
    feature.write_text(
        """id: feature
execution_policy:
  entrypoint: feature
  template: feature
nodes: []
""",
        encoding="utf-8",
    )
    mvp = project / ".ft/process/mvp-builder/process.yml"
    mvp.parent.mkdir(parents=True)
    mvp.write_text(MINIMAL_PROCESS.replace("id: test", "id: mvp"), encoding="utf-8")
    cycle = project / ".ft/cycles/cycle-09/cycle.yml"
    cycle.parent.mkdir(parents=True)
    cycle.write_text("id: cycle-09\ncustom: preserve\n", encoding="utf-8")
    manifest_path = project / ".ft/manifest.yml"
    manifest_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 2,
                "default_process": "mvp-builder",
                "processes": {
                    "feature": {
                        "path": ".ft/process/feature/process.yml",
                        "template": "feature",
                        "entrypoint": "feature",
                        "custom": {"keep": True},
                    },
                    "mvp-builder": {
                        "path": ".ft/process/mvp-builder/process.yml",
                        "template": "mvp-builder",
                        "entrypoint": "init",
                    },
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    feature_before = feature.read_bytes()
    mvp_before = mvp.read_bytes()
    cycle_before = cycle.read_bytes()

    migrate_v2_manifest(project)

    manifest = read_manifest(project)
    assert "default_process" not in manifest
    assert manifest["processes"]["feature"]["custom"] == {"keep": True}
    assert manifest["processes"]["feature"]["entrypoint"] == "feature"
    assert manifest["processes"]["mvp-builder"]["entrypoint"] == "init"
    assert feature.read_bytes() == feature_before
    assert mvp.read_bytes() == mvp_before
    assert cycle.read_bytes() == cycle_before

    absent_catalog = tmp_path / "absent-catalog"
    assert resolve_template(
        project,
        "feature",
        catalog_root=absent_catalog,
    ).process_file == feature.resolve()
    assert resolve_template(
        project,
        "mvp-builder",
        catalog_root=absent_catalog,
    ).process_file == mvp.resolve()


def test_v2_migration_rejects_policy_mismatch_without_touching_manifest(tmp_path):
    project = tmp_path / "project"
    process = project / ".ft/process/feature/process.yml"
    process.parent.mkdir(parents=True)
    process.write_text(
        """id: feature
execution_policy:
  entrypoint: evolve
  template: feature
nodes: []
""",
        encoding="utf-8",
    )
    manifest_path = project / ".ft/manifest.yml"
    manifest_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 2,
                "default_process": "feature",
                "processes": {
                    "feature": {
                        "path": ".ft/process/feature/process.yml",
                        "template": "feature",
                        "entrypoint": "feature",
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    before = manifest_path.read_bytes()

    with pytest.raises(ManifestError, match="não pode receber a ponte"):
        migrate_v2_manifest(project)

    assert manifest_path.read_bytes() == before


def test_v2_run_bridge_cannot_be_forged_after_migration(tmp_path):
    project = tmp_path / "project"
    process = project / ".ft/process/feature/process.yml"
    process.parent.mkdir(parents=True)
    process.write_text(MINIMAL_PROCESS, encoding="utf-8")
    manifest_path = project / ".ft/manifest.yml"
    manifest_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 3,
                "processes": {
                    "feature": {
                        "path": ".ft/process/feature/process.yml",
                        "template": "feature",
                        "entrypoint": "feature",
                        "v2_run_compatibility": {
                            "version": 1,
                            "legacy_entrypoint": "init",
                        },
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(TemplateCatalogError, match="incompatível"):
        resolve_template(
            project,
            "feature",
            catalog_root=tmp_path / "absent-catalog",
        )


def test_check_is_read_only_and_repair_reconstructs_from_orphan_bundle(
    tmp_path,
    monkeypatch,
):
    project = tmp_path / "project"
    _initialized_git(project)
    process = project / ".ft/process/custom/process.yml"
    process.parent.mkdir(parents=True)
    process.write_text(MINIMAL_PROCESS, encoding="utf-8")
    ft_home = tmp_path / "isolated-home"
    monkeypatch.setenv("FT_HOME", str(ft_home))
    before = {
        path.relative_to(project).as_posix(): path.read_bytes()
        for path in project.rglob("*")
        if path.is_file()
    }

    check = check_project(project)
    after_check = {
        path.relative_to(project).as_posix(): path.read_bytes()
        for path in project.rglob("*")
        if path.is_file()
    }
    assert check.status == "broken"
    assert before == after_check
    assert not ft_home.exists()

    repaired = repair_project(project)
    assert repaired.status == "repaired"
    assert repaired.backup_dir is not None
    assert (repaired.backup_dir / "repair.yml").is_file()
    manifest = read_manifest(project)
    assert manifest["schema_version"] == 3
    assert manifest["processes"]["custom"]["path"] == (
        ".ft/process/custom/process.yml"
    )
    assert "template" not in manifest["processes"]["custom"]
    assert not check_project(project).errors


def test_repair_backs_up_corrupt_manifest_and_blocks_future_schema(tmp_path):
    project = tmp_path / "project"
    _initialized_git(project)
    manifest = project / ".ft/manifest.yml"
    manifest.parent.mkdir(parents=True)
    corrupt = b"schema_version: [\n"
    manifest.write_bytes(corrupt)

    repaired = repair_project(project)
    assert repaired.status == "repaired"
    assert repaired.backup_dir is not None
    assert (repaired.backup_dir / ".ft/manifest.yml").read_bytes() == corrupt
    assert read_manifest(project)["schema_version"] == 3

    future = yaml.safe_dump({"schema_version": 99, "processes": {}}).encode()
    manifest.write_bytes(future)
    blocked = repair_project(project)
    assert blocked.status == "blocked"
    assert manifest.read_bytes() == future


def test_repair_drops_only_v3_default_and_preserves_catalog_and_cycles(tmp_path):
    project = tmp_path / "project"
    _initialized_git(project)
    process = project / ".ft/process/feature/process.yml"
    process.parent.mkdir(parents=True)
    process.write_text(MINIMAL_PROCESS, encoding="utf-8")
    cycle = project / ".ft/cycles/cycle-07/cycle.yml"
    cycle.parent.mkdir(parents=True)
    cycle.write_text("id: cycle-07\nstatus: running\n", encoding="utf-8")
    manifest = project / ".ft/manifest.yml"
    record = {
        "path": ".ft/process/feature/process.yml",
        "template": "feature",
        "entrypoint": "run",
        "custom": {"preserve": True},
    }
    manifest.write_text(
        yaml.safe_dump(
            {
                "schema_version": 3,
                "default_process": "feature",
                "processes": {"feature": record},
                "custom_top_level": "preserve",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    process_before = process.read_bytes()
    cycle_before = cycle.read_bytes()

    result = repair_project(project)

    assert result.status == "repaired"
    repaired = read_manifest(project)
    assert "default_process" not in repaired
    assert repaired["processes"]["feature"] == record
    assert repaired["custom_top_level"] == "preserve"
    assert process.read_bytes() == process_before
    assert cycle.read_bytes() == cycle_before
