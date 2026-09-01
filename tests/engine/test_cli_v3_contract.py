"""End-to-end CLI contract for the Fast Track V3 workspace model."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from ft.cli import main as cli_main
from ft.engine import paths
from ft.engine.runner import StepRunner
from ft.project.lifecycle import close_project_contract


def _invoke_cli(monkeypatch: pytest.MonkeyPatch, *arguments: object) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["ft", *(str(argument) for argument in arguments)],
    )
    cli_main.main()


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _tracked_workspace_snapshot(root: Path) -> dict[str, bytes]:
    return {
        relative: (root / relative).read_bytes()
        for relative in (
            ".ft/manifest.yml",
            ".ft/project.yml",
            ".ft/.gitignore",
            ".ft/process/.gitkeep",
            ".ft/cycles/.gitkeep",
            "AGENTS.md",
        )
    }


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
    monkeypatch.delenv("FT_DEBUG", raising=False)


def test_help_exposes_only_the_universal_v3_run_contract(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as top_level_exit:
        _invoke_cli(monkeypatch, "--help")
    assert top_level_exit.value.code == 0
    top_level_help = capsys.readouterr().out

    assert re.search(r"^\s+feature\s", top_level_help, re.MULTILINE) is None
    assert "--process" not in top_level_help

    with pytest.raises(SystemExit) as run_help_exit:
        _invoke_cli(monkeypatch, "run", "--help")
    assert run_help_exit.value.code == 0
    run_help = capsys.readouterr().out

    assert "--template TEMPLATE" in run_help
    assert "--process" not in run_help
    assert "--force" not in run_help

    with pytest.raises(SystemExit) as missing_template_exit:
        _invoke_cli(monkeypatch, "run", ".")
    assert missing_template_exit.value.code == 2
    parse_error = capsys.readouterr().err
    assert "required" in parse_error
    assert "--template" in parse_error


def test_init_creates_common_workspace_and_is_idempotent_and_checkable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)

    _invoke_cli(monkeypatch, "init", "product")
    first_output = capsys.readouterr().out
    project = tmp_path / "product"

    assert "Nenhum processo foi selecionado" in first_output
    assert (project / ".git").is_dir()
    assert _git(project, "rev-parse", "--verify", "HEAD")
    manifest = yaml.safe_load(
        (project / ".ft" / "manifest.yml").read_text(encoding="utf-8")
    )
    assert manifest == {"schema_version": 3, "processes": {}}
    assert "default_process" not in manifest
    assert not (project / "docs").exists()
    assert not (project / "src").exists()
    assert _git(project, "status", "--porcelain") == ""

    initial_head = _git(project, "rev-parse", "HEAD")
    initial_files = _tracked_workspace_snapshot(project)

    _invoke_cli(monkeypatch, "init", "product")
    second_output = capsys.readouterr().out
    assert "já estava inicializado e saudável" in second_output
    assert _git(project, "rev-parse", "HEAD") == initial_head
    assert _git(project, "status", "--porcelain") == ""
    assert _tracked_workspace_snapshot(project) == initial_files

    _invoke_cli(monkeypatch, "init", "product", "--check")
    check_output = capsys.readouterr().out
    assert "Status: healthy" in check_output
    assert _git(project, "rev-parse", "HEAD") == initial_head
    assert _git(project, "status", "--porcelain") == ""
    assert _tracked_workspace_snapshot(project) == initial_files


def test_init_adopt_bootstraps_legacy_directory_without_git(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "app.py").write_text("VALUE = 1\n", encoding="utf-8")

    with pytest.raises(SystemExit) as refused_exit:
        _invoke_cli(monkeypatch, "init", "legacy")
    assert refused_exit.value.code == 1
    refused = capsys.readouterr()
    assert "--adopt" in refused.err + refused.out
    assert not (legacy / ".git").exists()

    _invoke_cli(monkeypatch, "init", "legacy", "--adopt")
    output = capsys.readouterr().out

    assert (legacy / ".git").is_dir()
    assert (legacy / ".ft" / "manifest.yml").is_file()
    assert _git(legacy, "rev-parse", "--verify", "HEAD")
    # O legado não é commitado silenciosamente; o usuário é avisado.
    assert "app.py" in _git(legacy, "status", "--porcelain")
    assert "commite-os antes" in output


def test_project_lifecycle_commands_block_then_close_and_reopen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    _invoke_cli(monkeypatch, "init", "product")
    capsys.readouterr()
    project = tmp_path / "product"
    _git(project, "config", "user.name", "FT Contract Tests")
    _git(project, "config", "user.email", "ft-tests@example.invalid")

    with pytest.raises(SystemExit) as initial_status:
        _invoke_cli(monkeypatch, "project-status", project)
    assert initial_status.value.code == 2
    assert "BLOCKED" in capsys.readouterr().out

    with pytest.raises(SystemExit) as blocked_close:
        _invoke_cli(monkeypatch, "project-close", project)
    assert blocked_close.value.code == 2
    blocked_output = capsys.readouterr().out
    assert "permanece em building" in blocked_output
    assert (
        yaml.safe_load(
            (project / ".ft/project-readiness.yml").read_text(encoding="utf-8")
        )["status"]
        == "BLOCKED"
    )
    blocked_head = _git(project, "rev-parse", "HEAD")
    with pytest.raises(SystemExit) as repeated_close:
        _invoke_cli(monkeypatch, "project-close", project)
    assert repeated_close.value.code == 2
    capsys.readouterr()
    assert _git(project, "rev-parse", "HEAD") == blocked_head

    docs = project / "docs"
    docs.mkdir()
    (docs / "PRD.md").write_text("# PRD\n\nProduto completo.\n", encoding="utf-8")
    (docs / "PROJECT_BACKLOG.md").write_text(
        "| ID | Prioridade | Status | Evidência |\n"
        "| --- | --- | --- | --- |\n"
        "| PB-001 | P0 | done | tests/report.md |\n",
        encoding="utf-8",
    )
    _git(project, "add", "docs")
    _git(project, "commit", "-m", "deliver product")

    with pytest.raises(SystemExit) as ready_close:
        _invoke_cli(monkeypatch, "project-close", project)
    assert ready_close.value.code == 0
    assert "fase alterada para maintenance" in capsys.readouterr().out
    contract = yaml.safe_load((project / ".ft/project.yml").read_text(encoding="utf-8"))
    assert contract["lifecycle"]["phase"] == "maintenance"

    with pytest.raises(SystemExit) as reopened:
        _invoke_cli(
            monkeypatch,
            "project-reopen",
            project,
            "--reason",
            "Novo marco de produto",
            "--target",
            "v2",
        )
    assert reopened.value.code == 0
    assert "Projeto reaberto" in capsys.readouterr().out
    contract = yaml.safe_load((project / ".ft/project.yml").read_text(encoding="utf-8"))
    assert contract["objective"]["target"] == "v2"
    assert contract["lifecycle"]["phase"] == "building"


def test_project_status_from_worktree_uses_owner_checkout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    _invoke_cli(monkeypatch, "init", "product")
    capsys.readouterr()
    project = tmp_path / "product"
    linked = tmp_path / "linked-cycle"
    _git(project, "config", "user.name", "FT Contract Tests")
    _git(project, "config", "user.email", "ft-tests@example.invalid")
    _git(project, "worktree", "add", "-q", "-b", "cycle-inspection", str(linked))

    contract_path = project / ".ft/project.yml"
    contract = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    contract["objective"]["target"] = "owner-checkout"
    contract_path.write_text(
        yaml.safe_dump(contract, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    _git(project, "add", ".ft/project.yml")
    _git(project, "commit", "-m", "update owner objective")
    owner_revision = _git(project, "rev-parse", "HEAD")

    with pytest.raises(SystemExit) as status:
        _invoke_cli(monkeypatch, "project-status", linked, "--json")

    assert status.value.code == 2
    payload = yaml.safe_load(capsys.readouterr().out)
    assert payload["target"] == "owner-checkout"
    assert payload["evaluated_revision"] == owner_revision


def test_maintenance_template_is_rejected_before_materialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    _invoke_cli(monkeypatch, "init", "product")
    capsys.readouterr()
    project = tmp_path / "product"

    with pytest.raises(SystemExit) as refused:
        _invoke_cli(
            monkeypatch,
            "run",
            project,
            "--template",
            "feature-fast",
            "--request",
            "Completar algo que ficou pendente",
        )

    assert refused.value.code == 2
    output = capsys.readouterr().out
    assert "projeto está em building" in output
    assert not (project / ".ft/process/feature-fast").exists()
    assert not paths.worktrees_home(project).exists()


def test_run_rejects_repository_without_ft_initialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "ordinary-repository"
    project.mkdir()

    with pytest.raises(SystemExit) as run_exit:
        _invoke_cli(
            monkeypatch,
            "run",
            project,
            "--template",
            "feature",
            "--request",
            "Uma demanda",
        )

    assert run_exit.value.code == 1
    output = capsys.readouterr().out
    assert "não inicializado" in output
    assert not (project / ".ft").exists()


def test_migrate_layout_cli_accepts_v1_before_current_manifest_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "legacy-project"
    process = project / "process/process.yml"
    process.parent.mkdir(parents=True)
    process.write_text(
        """id: feature
version: '1.0'
title: Legacy feature
nodes:
  - id: end
    type: end
    title: End
""",
        encoding="utf-8",
    )
    manifest = project / ".ft/manifest.yml"
    manifest.parent.mkdir()
    manifest.write_text(
        "schema_version: 1\nprocess: process/process.yml\ntemplate: feature\n",
        encoding="utf-8",
    )

    _invoke_cli(monkeypatch, "migrate-layout", project)

    output = capsys.readouterr().out
    migrated = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    assert "Migrado" in output
    assert migrated["schema_version"] == 3
    assert "default_process" not in migrated
    assert migrated["processes"]["feature"]["v2_run_compatibility"] == {
        "version": 1,
        "legacy_entrypoint": "init",
    }
    assert (project / ".ft/process/feature/process.yml").is_file()


def test_two_template_runs_are_isolated_and_cycle_selection_is_strict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    _invoke_cli(monkeypatch, "init", "product")
    capsys.readouterr()
    project = tmp_path / "product"
    _git(project, "config", "user.name", "FT Contract Tests")
    _git(project, "config", "user.email", "ft-tests@example.invalid")
    docs = project / "docs"
    docs.mkdir()
    (docs / "PRD.md").write_text("# PRD\n\nProduto entregue.\n", encoding="utf-8")
    (docs / "PROJECT_BACKLOG.md").write_text(
        "| ID | Prioridade | Status | Evidência |\n"
        "| --- | --- | --- | --- |\n"
        "| PB-001 | P0 | done | tests/report.md |\n",
        encoding="utf-8",
    )
    _git(project, "add", "docs")
    _git(project, "commit", "-m", "deliver test project")
    close_project_contract(project)
    _git(project, "add", ".ft")
    _git(project, "commit", "-m", "close test project")

    started: list[tuple[Path, str]] = []

    def stop_before_graph_execution(self: StepRunner, mode: str = "step") -> None:
        started.append((Path(self.project_root).resolve(), mode))

    monkeypatch.setattr(StepRunner, "run", stop_before_graph_execution)
    monkeypatch.setattr(cli_main, "_api_health_check", lambda *_args, **_kwargs: None)

    _invoke_cli(
        monkeypatch,
        "run",
        project,
        "--template",
        "feature-fast",
        "--request",
        "Adicionar busca por telefone",
    )
    _invoke_cli(
        monkeypatch,
        "run",
        project,
        "--template",
        "tweak",
        "--request",
        "Reduzir o padding do cabeçalho",
    )
    capsys.readouterr()

    assert len(started) == 2
    assert [mode for _root, mode in started] == ["mvp", "mvp"]
    worktrees = {root.name: root for root, _mode in started}
    assert set(worktrees) == {"cycle-01-feature-fast", "cycle-02-tweak"}

    feature_worktree = worktrees["cycle-01-feature-fast"]
    tweak_worktree = worktrees["cycle-02-tweak"]
    feature_request = feature_worktree / "docs" / "feature-request.md"
    tweak_request = tweak_worktree / "docs" / "feature-request.md"
    assert feature_request.read_text(encoding="utf-8") == "Adicionar busca por telefone"
    assert tweak_request.read_text(encoding="utf-8") == "Reduzir o padding do cabeçalho"
    assert not (project / "docs" / "feature-request.md").exists()

    feature_state = yaml.safe_load(
        (feature_worktree / "state" / "engine_state.yml").read_text(encoding="utf-8")
    )
    tweak_state = yaml.safe_load(
        (tweak_worktree / "state" / "engine_state.yml").read_text(encoding="utf-8")
    )
    assert feature_state["template_id"] == "feature-fast"
    assert tweak_state["template_id"] == "tweak"
    assert feature_state["process_path"] == ".ft/process/feature-fast/process.yml"
    assert tweak_state["process_path"] == ".ft/process/tweak/process.yml"
    for state in (feature_state, tweak_state):
        assert state["project_id"] == "product"
        assert state["project_phase"] == "maintenance"
        assert state["project_target"] == "mvp"
        assert state["project_definition_of_done_digest"].startswith("sha256:")
        assert re.fullmatch(r"[0-9a-f]{40}", state["project_delivered_revision"])
    assert _git(project, "status", "--porcelain") == ""

    runtime_home = paths.worktrees_home(project)
    assert feature_worktree.parent == runtime_home
    assert tweak_worktree.parent == runtime_home

    monkeypatch.chdir(project)
    _invoke_cli(monkeypatch, "status")
    multi_status = capsys.readouterr().out
    assert "Ciclo: cycle-01-feature-fast" in multi_status
    assert "Ciclo: cycle-02-tweak" in multi_status
    assert "feature_fast v2.0.3" in multi_status
    assert "tweak v1.1.0" in multi_status

    selected = cli_main._select_cycle_for_command(project, "cycle-02-tweak")
    assert selected.name == "cycle-02-tweak"
    assert selected.worktree == tweak_worktree
