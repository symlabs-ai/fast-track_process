"""``kind: init`` templates: catálogo, execução única, marker e --fix."""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

import pytest

from ft.project import bootstrap_project
from ft.project.bootstrap import DEFAULT_INIT_TEMPLATE
from ft.project.init_scripts import (
    InitScriptError,
    init_marker_path,
    read_init_marker,
    record_init_template,
    run_init_template,
)
from ft.templates import TemplateCatalog, TemplateCatalogError, template_kind
from ft.templates.catalog import TemplateNotFoundError


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


def _write_init_template(catalog_root: Path, name: str, *, script_body: str) -> Path:
    template_dir = catalog_root / name
    scripts_dir = template_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    (template_dir / "template.yml").write_text(
        "kind: init\nscripts:\n  - scripts/setup.sh\n",
        encoding="utf-8",
    )
    script = scripts_dir / "setup.sh"
    script.write_text(
        f"#!/usr/bin/env bash\nset -euo pipefail\n{script_body}\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return template_dir


# ---------------------------------------------------------------------------
# Catálogo
# ---------------------------------------------------------------------------

def test_engine_ships_init_default_template():
    catalog = TemplateCatalog()
    descriptor = catalog.get_init(DEFAULT_INIT_TEMPLATE)
    assert descriptor.name == DEFAULT_INIT_TEMPLATE
    assert descriptor.scripts, "init-default deve declarar scripts"
    for script in descriptor.scripts:
        assert script.is_file()
        assert script.stat().st_mode & stat.S_IXUSR, f"não executável: {script}"
    assert descriptor.source_digest.startswith("sha256:")


def test_init_default_is_not_runnable_and_not_listed_for_run():
    catalog = TemplateCatalog()
    assert DEFAULT_INIT_TEMPLATE not in catalog.names()
    with pytest.raises(TemplateCatalogError, match="ft init --template"):
        catalog.get(DEFAULT_INIT_TEMPLATE)


def test_process_template_is_rejected_by_get_init():
    catalog = TemplateCatalog()
    with pytest.raises(TemplateCatalogError, match="ft run"):
        catalog.get_init("tweak")


def test_template_kind_defaults_to_process(tmp_path):
    template = tmp_path / "plain"
    template.mkdir()
    assert template_kind(template) == "process"


def test_init_manifest_rejects_script_outside_bundle(tmp_path):
    catalog_root = tmp_path / "catalog"
    template = _write_init_template(catalog_root, "escape", script_body="true")
    (template / "template.yml").write_text(
        "kind: init\nscripts:\n  - ../outside.sh\n",
        encoding="utf-8",
    )
    with pytest.raises(TemplateCatalogError, match="fora do bundle"):
        TemplateCatalog(catalog_root).get_init("escape")


def test_init_names_lists_only_init_templates(tmp_path):
    catalog_root = tmp_path / "catalog"
    _write_init_template(catalog_root, "my-env", script_body="true")
    process_dir = catalog_root / "proc"
    process_dir.mkdir()
    (process_dir / "process.yml").write_text("id: proc\n", encoding="utf-8")
    catalog = TemplateCatalog(catalog_root)
    assert catalog.init_names() == ("my-env",)
    with pytest.raises(TemplateNotFoundError, match="disponíveis: my-env"):
        catalog.require_init("missing")


# ---------------------------------------------------------------------------
# Execução e marker
# ---------------------------------------------------------------------------

def test_run_init_template_executes_scripts_with_env(tmp_path):
    catalog_root = tmp_path / "catalog"
    _write_init_template(
        catalog_root,
        "my-env",
        script_body=(
            'echo "mode=${FT_INIT_MODE} adopt=${FT_ADOPT}" > out.txt\n'
            'echo "provisionado .env"'
        ),
    )
    project = tmp_path / "project"
    project.mkdir()
    descriptor = TemplateCatalog(catalog_root).get_init("my-env")

    results = run_init_template(descriptor, project, mode="fix", adopt=True)

    assert (project / "out.txt").read_text() == "mode=fix adopt=1\n"
    assert results[0].output == "provisionado .env"


def test_run_init_template_blocks_on_failure(tmp_path):
    catalog_root = tmp_path / "catalog"
    _write_init_template(catalog_root, "broken", script_body="echo boom >&2\nexit 3")
    project = tmp_path / "project"
    project.mkdir()
    descriptor = TemplateCatalog(catalog_root).get_init("broken")

    with pytest.raises(InitScriptError, match="exit code 3: boom"):
        run_init_template(descriptor, project, mode="init")


def test_marker_records_and_reads_applied_templates(tmp_path):
    catalog_root = tmp_path / "catalog"
    _write_init_template(catalog_root, "my-env", script_body="true")
    project = tmp_path / "project"
    project.mkdir()
    descriptor = TemplateCatalog(catalog_root).get_init("my-env")

    assert read_init_marker(project) == {}
    record_init_template(project, descriptor)

    applied = read_init_marker(project)
    assert applied["my-env"]["digest"] == descriptor.source_digest
    assert "completed_at" in applied["my-env"]
    assert init_marker_path(project) == project / ".ft/runtime/init.yml"


# ---------------------------------------------------------------------------
# Bootstrap: roda uma única vez, marker gitignored
# ---------------------------------------------------------------------------

def test_bootstrap_records_marker_and_keeps_it_out_of_git(tmp_path):
    project = tmp_path / "new-project"
    bootstrap_project(project)

    applied = read_init_marker(project)
    assert DEFAULT_INIT_TEMPLATE in applied
    # Marker é local da máquina: gitignored, nunca entra na história.
    assert _git(project, "status", "--porcelain") == ""
    assert init_marker_path(project).is_file()


def test_bootstrap_creates_project_base_files(tmp_path):
    project = tmp_path / "new-project"
    bootstrap_project(project)

    gitignore = (project / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in gitignore
    assert (project / ".env.example").is_file()
    assert (project / "README.md").read_text(encoding="utf-8").startswith("# new-project")
    assert (project / "AGENTS.md").is_file()


def test_bootstrap_skips_init_scripts_after_marker(tmp_path):
    project = tmp_path / "new-project"
    bootstrap_project(project)
    head = _git(project, "rev-parse", "HEAD")

    # Arquivo criado pelo template é removido; sem --fix, o rerun não o recria.
    (project / ".env.example").unlink()
    _git(project, "add", "-u", ".")
    _git(
        project,
        "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
        "commit", "-q", "-m", "remove env example",
    )

    rerun = bootstrap_project(project)
    assert rerun.status == "unchanged"
    assert not (project / ".env.example").exists()
    assert _git(project, "rev-parse", "HEAD") != head  # só o commit manual acima


def test_bootstrap_never_overwrites_adopted_files(tmp_path):
    project = tmp_path / "legacy"
    project.mkdir()
    (project / ".gitignore").write_text("custom\n", encoding="utf-8")
    (project / "README.md").write_text("# meu produto\n", encoding="utf-8")

    bootstrap_project(project, adopt=True)

    assert (project / ".gitignore").read_text(encoding="utf-8") == "custom\n"
    assert (project / "README.md").read_text(encoding="utf-8") == "# meu produto\n"
