from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import stat

import pytest
import yaml

from ft.engine.layout import ensure_project_layout, read_manifest
from ft.templates import (
    TemplateCatalog,
    TemplateCatalogError,
    TemplateMaterializer,
    TemplateNotFoundError,
    resolve_template,
)
from ft.templates import materialize as materialize_module


def _project(root: Path) -> Path:
    root.mkdir()
    ensure_project_layout(root)
    return root


def _global_template(
    catalog: Path,
    name: str,
    *,
    entrypoint: str = "run",
) -> Path:
    template = catalog / name
    scripts = template / "scripts"
    scripts.mkdir(parents=True)
    (template / "process.yml").write_text(
        f"""id: {name}
version: '1.0'
execution_policy:
  entrypoint: {entrypoint}
  template: {name}
  materialization: copy_once
  runtime_source: local_only
input_policy:
  required: false
  destination: docs/demanda.md
  prompt: Descreva a demanda
nodes:
  - id: end
    type: end
    title: End
""",
        encoding="utf-8",
    )
    script = scripts / "run.sh"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    (template / "environment.yml").write_text("env: {}\n", encoding="utf-8")
    (template / "README.md").write_text("bundle docs\n", encoding="utf-8")
    (template / "docs").mkdir()
    (template / "docs" / "PRD.md").write_text("product seed\n", encoding="utf-8")
    (template / "src").mkdir()
    (template / "src" / "seed.py").write_text("seed = True\n", encoding="utf-8")
    return template


def test_catalog_lists_only_universal_run_templates(tmp_path: Path) -> None:
    catalog_root = tmp_path / "templates"
    _global_template(catalog_root, "feature")
    _global_template(catalog_root, "evolve", entrypoint="evolve")

    catalog = TemplateCatalog(catalog_root)

    assert catalog.names() == ("feature",)
    assert catalog.get("feature").policy["entrypoint"] == "run"
    with pytest.raises(TemplateCatalogError, match="entrypoint run"):
        catalog.require("evolve")
    with pytest.raises(TemplateNotFoundError, match="disponíveis: feature"):
        catalog.require("missing")


def test_materialization_is_copy_once_and_excludes_product_seeds(tmp_path: Path) -> None:
    root = _project(tmp_path / "project")
    catalog_root = tmp_path / "templates"
    source = _global_template(catalog_root, "feature")

    first = resolve_template(root, "feature", catalog_root=catalog_root)
    local = first.process_file
    script = local.parent / "scripts" / "run.sh"

    assert first.materialized is True
    assert local == (root / ".ft/process/feature/process.yml").resolve()
    assert (local.parent / ".base/process.yml").is_file()
    assert (local.parent / ".base/scripts/run.sh").is_file()
    assert not (local.parent / "docs").exists()
    assert not (local.parent / "src").exists()
    assert script.stat().st_mode & stat.S_IXUSR

    fork = local.read_text(encoding="utf-8") + "\n# fork local\n"
    local.write_text(fork, encoding="utf-8")
    (source / "process.yml").write_text(
        (source / "process.yml").read_text(encoding="utf-8")
        + "\n# catálogo novo\n",
        encoding="utf-8",
    )

    second = resolve_template(root, "feature", catalog_root=catalog_root)

    assert second.materialized is False
    assert second.source_drift is True
    assert local.read_text(encoding="utf-8") == fork
    manifest = read_manifest(root)
    record = manifest["processes"]["feature"]
    assert record["path"] == ".ft/process/feature/process.yml"
    assert record["template"] == "feature"
    assert record["entrypoint"] == "run"
    assert record["source_digest"] == first.source_digest
    assert "default_process" not in manifest


def test_unregistered_fork_is_preserved_and_registered(tmp_path: Path) -> None:
    root = _project(tmp_path / "project")
    catalog_root = tmp_path / "templates"
    source = _global_template(catalog_root, "tweak")
    local = root / ".ft/process/tweak"
    local.mkdir(parents=True)
    (local / "process.yml").write_text(
        (source / "process.yml").read_text(encoding="utf-8") + "\n# custom\n",
        encoding="utf-8",
    )

    result = resolve_template(root, "tweak", catalog_root=catalog_root)

    assert result.materialized is False
    assert result.process_file.read_text(encoding="utf-8").endswith("# custom\n")
    assert read_manifest(root)["processes"]["tweak"]["entrypoint"] == "run"


def test_registered_local_fork_resolves_when_global_template_disappears(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path / "project")
    catalog_root = tmp_path / "templates"
    source = _global_template(catalog_root, "feature")
    first = resolve_template(root, "feature", catalog_root=catalog_root)
    local_text = first.process_file.read_text(encoding="utf-8") + "\n# local only\n"
    first.process_file.write_text(local_text, encoding="utf-8")
    for candidate in sorted(source.rglob("*"), reverse=True):
        if candidate.is_file():
            candidate.unlink()
        elif candidate.is_dir():
            candidate.rmdir()
    source.rmdir()

    resolved = resolve_template(root, "feature", catalog_root=catalog_root)

    assert resolved.materialized is False
    assert resolved.current_source_digest is None
    assert resolved.source_drift is False
    assert resolved.process_file.read_text(encoding="utf-8") == local_text


def test_legacy_global_process_filename_materializes_to_canonical_path(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path / "project")
    catalog_root = tmp_path / "templates"
    source = _global_template(catalog_root, "legacy")
    (source / "process.yml").rename(source / "LEGACY_PROCESS.yml")

    resolved = resolve_template(root, "legacy", catalog_root=catalog_root)

    assert resolved.process_file.name == "process.yml"
    assert not (resolved.process_file.parent / "LEGACY_PROCESS.yml").exists()
    assert (resolved.process_file.parent / ".base/process.yml").is_file()


def test_registered_broken_fork_fails_without_recopying(tmp_path: Path) -> None:
    root = _project(tmp_path / "project")
    catalog_root = tmp_path / "templates"
    _global_template(catalog_root, "bug")
    resolve_template(root, "bug", catalog_root=catalog_root)
    local = root / ".ft/process/bug"
    for candidate in sorted(local.rglob("*"), reverse=True):
        if candidate.is_file():
            candidate.unlink()
        elif candidate.is_dir():
            candidate.rmdir()
    local.rmdir()

    with pytest.raises(TemplateCatalogError, match="ausente"):
        resolve_template(root, "bug", catalog_root=catalog_root)

    assert not local.exists()


def test_registration_failure_rolls_back_new_bundle(tmp_path: Path, monkeypatch) -> None:
    root = _project(tmp_path / "project")
    catalog_root = tmp_path / "templates"
    _global_template(catalog_root, "feature")

    def fail_registration(*_args, **_kwargs):
        raise RuntimeError("manifest write failed")

    monkeypatch.setattr(
        materialize_module,
        "register_project_process",
        fail_registration,
    )

    with pytest.raises(RuntimeError, match="manifest write failed"):
        resolve_template(root, "feature", catalog_root=catalog_root)

    assert not (root / ".ft/process/feature").exists()
    assert not list((root / ".ft/process").glob(".feature.*.staging"))
    assert "feature" not in read_manifest(root)["processes"]


def test_nested_symlink_in_global_or_local_bundle_is_rejected(tmp_path: Path) -> None:
    root = _project(tmp_path / "project")
    catalog_root = tmp_path / "templates"
    source = _global_template(catalog_root, "feature")
    outside = tmp_path / "outside.sh"
    outside.write_text("exit 0\n", encoding="utf-8")
    (source / "scripts" / "unsafe.sh").symlink_to(outside)

    with pytest.raises(TemplateCatalogError, match="link simbólico"):
        resolve_template(root, "feature", catalog_root=catalog_root)
    assert not (root / ".ft/process/feature").exists()

    (source / "scripts" / "unsafe.sh").unlink()
    resolve_template(root, "feature", catalog_root=catalog_root)
    local_link = root / ".ft/process/feature/scripts/local-link.sh"
    local_link.symlink_to(outside)
    with pytest.raises(TemplateCatalogError, match="link simbólico"):
        resolve_template(root, "feature", catalog_root=catalog_root)


def test_concurrent_materialization_publishes_one_complete_fork(tmp_path: Path) -> None:
    root = _project(tmp_path / "project")
    catalog_root = tmp_path / "templates"
    _global_template(catalog_root, "feature")
    materializer = TemplateMaterializer(root, catalog=TemplateCatalog(catalog_root))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: materializer.resolve("feature"), range(2)))

    assert {result.process_file for result in results} == {
        (root / ".ft/process/feature/process.yml").resolve()
    }
    assert sorted(result.materialized for result in results) == [False, True]
    assert not list((root / ".ft/process").glob(".feature.*.staging"))
    assert tuple(read_manifest(root)["processes"]) == ("feature",)


def test_materializer_requires_initialized_project(tmp_path: Path) -> None:
    root = tmp_path / "plain"
    root.mkdir()
    catalog_root = tmp_path / "templates"
    _global_template(catalog_root, "feature")

    with pytest.raises(TemplateCatalogError, match="não inicializado"):
        resolve_template(root, "feature", catalog_root=catalog_root)

    assert not (root / ".ft").exists()


def test_real_catalog_has_no_feature_entrypoint() -> None:
    catalog = TemplateCatalog()

    assert {"feature", "bug", "tweak"} <= set(catalog.names())
    for name in catalog.names():
        descriptor = catalog.get(name)
        payload = yaml.safe_load(descriptor.process_file.read_text(encoding="utf-8"))
        assert payload["execution_policy"]["entrypoint"] == "run"
        assert payload["execution_policy"]["template"] == name
        assert "input_policy" in payload
