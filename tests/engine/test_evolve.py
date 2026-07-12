"""Testes do ft evolve — evolução de processo paralela ao ciclo."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest
import yaml

from ft.cli import main as cli_main
from ft.engine import evolve, paths


REPO_ROOT = Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def ft_home(tmp_path, monkeypatch):
    home = tmp_path / "ft-home"
    monkeypatch.setenv("FT_HOME", str(home))
    return home


@pytest.fixture()
def project(tmp_path):
    """Projeto inicializado com fork local .ft/process/ e docs de ciclo."""
    root = tmp_path / "proj"
    process_dir = root / ".ft" / "process"
    process_dir.mkdir(parents=True)
    shutil.copy2(
        REPO_ROOT / "templates" / "base" / "process.yml",
        process_dir / "process.yml",
    )
    (root / ".ft" / "manifest.yml").write_text(
        yaml.safe_dump(
            {
                "process": ".ft/process/process.yml",
                "processes": {
                    "feature": {
                        "path": ".ft/process/feature/process.yml",
                        "template": "feature",
                        "entrypoint": "feature",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (root / "docs").mkdir()
    (root / "docs" / "handoff.md").write_text("# Handoff\ncontexto\n", encoding="utf-8")
    return root


@pytest.fixture()
def fake_engine(tmp_path):
    """Checkout fake do engine com templates evolve_process e feature."""
    engine = tmp_path / "engine"
    (engine / ".git").mkdir(parents=True)
    templates = engine / "templates"
    shutil.copytree(REPO_ROOT / "templates" / "evolve_process", templates / "evolve_process")
    shutil.copytree(REPO_ROOT / "templates" / "feature", templates / "feature")
    return engine


def _active_cycle(ft_home_dir: Path, project_root: Path, name: str = "cycle-03") -> Path:
    cycle = paths.worktrees_home(project_root) / name
    (cycle / "state").mkdir(parents=True)
    (cycle / "state" / "engine_state.yml").write_text(
        yaml.safe_dump({"current_node": "ft.build", "node_status": "blocked"}),
        encoding="utf-8",
    )
    (cycle / "docs").mkdir()
    (cycle / "docs" / "retro.md").write_text("# Retro\nnode X falhou 3x\n", encoding="utf-8")
    return cycle


# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------

def test_resolve_targets_exige_um_alvo(project):
    with pytest.raises(evolve.EvolveError, match="--project e/ou --global"):
        evolve.resolve_targets(project, include_project=False, include_global=False)


def test_resolve_targets_project_sem_processo_local(tmp_path):
    empty = tmp_path / "vazio"
    empty.mkdir()
    with pytest.raises(evolve.EvolveError, match=".ft/process"):
        evolve.resolve_targets(empty, include_project=True, include_global=False)


def test_resolve_targets_global_via_manifesto(project, fake_engine):
    targets = evolve.resolve_targets(
        project, include_project=True, include_global=True, engine_root=fake_engine
    )
    assert targets.project_dir == project / ".ft" / "process"
    assert set(targets.global_dirs) == {"feature"}
    assert targets.global_dirs["feature"] == fake_engine / "templates" / "feature"
    assert targets.labels == ["project", "global:feature"]


def test_resolve_targets_global_sem_manifesto(tmp_path, fake_engine):
    root = tmp_path / "solto"
    (root / ".ft" / "process").mkdir(parents=True)
    (root / ".ft" / "process" / "process.yml").write_text("id: x\n", encoding="utf-8")
    with pytest.raises(evolve.EvolveError, match="template global registrado"):
        evolve.resolve_targets(
            root, include_project=False, include_global=True, engine_root=fake_engine
        )


# ---------------------------------------------------------------------------
# Contexto do ciclo
# ---------------------------------------------------------------------------

def test_contexto_prefere_ciclo_ativo(ft_home, project):
    _active_cycle(ft_home, project)
    label, source, state = evolve.find_cycle_context(project)
    assert "cycle-03" in label and "ativo" in label
    assert source is not None and source.name == "cycle-03"
    assert state is not None and state.name == "engine_state.yml"


def test_contexto_ignora_ciclo_terminado(ft_home, project):
    cycle = _active_cycle(ft_home, project)
    state_file = cycle / "state" / "engine_state.yml"
    state_file.write_text(
        yaml.safe_dump({"current_node": "ft.end", "node_status": "done"}),
        encoding="utf-8",
    )
    archived = paths.project_cycles_dir(project) / "cycle-02"
    archived.mkdir(parents=True)
    (archived / "retro.md").write_text("# Retro arquivada\n", encoding="utf-8")

    label, source, state = evolve.find_cycle_context(project)
    assert "arquivado" in label
    assert source == archived
    assert state is None


def test_contexto_fallback_docs_da_raiz(ft_home, project):
    label, source, _state = evolve.find_cycle_context(project)
    assert label == "projeto (sem ciclo)"
    assert source == project


def test_contexto_ciclo_explicito_inexistente(ft_home, project):
    with pytest.raises(evolve.EvolveError, match="ciclo não encontrado"):
        evolve.find_cycle_context(project, cycle="cycle-99")


# ---------------------------------------------------------------------------
# Workspace
# ---------------------------------------------------------------------------

def _workspace(project, fake_engine, ft_home, **kwargs):
    targets = evolve.resolve_targets(
        project,
        include_project=kwargs.pop("include_project", True),
        include_global=kwargs.pop("include_global", True),
        engine_root=fake_engine,
    )
    return evolve.prepare_workspace(
        project,
        template_dir=fake_engine / "templates" / "evolve_process",
        targets=targets,
        **kwargs,
    )


def test_prepare_workspace_monta_staging_e_contexto(ft_home, project, fake_engine):
    _active_cycle(ft_home, project)
    ws = _workspace(project, fake_engine, ft_home, directive="focar em retries")

    # Workspace fora de worktrees/ — nunca aparece como ciclo.
    assert ws.root.is_relative_to(paths.evolve_home(project))
    assert not ws.root.is_relative_to(paths.worktrees_root())

    assert ws.process_file.is_file()
    assert (ws.staged_project_dir / "process.yml").is_file()
    assert (ws.staged_global_dir("feature") / "process.yml").is_file()
    assert (ws.context_dir / "cycle" / "docs" / "retro.md").is_file()
    assert (ws.context_dir / "cycle_state.yml").is_file()
    assert "focar em retries" in (ws.context_dir / "directive.md").read_text(encoding="utf-8")
    manifest = (ws.context_dir / "targets.md").read_text(encoding="utf-8")
    assert "targets/project/" in manifest
    assert "targets/global/feature/" in manifest

    meta = yaml.safe_load((ws.root / "workspace.yml").read_text(encoding="utf-8"))
    assert meta["targets"] == ["project", "global:feature"]
    assert meta["directive"] == "focar em retries"


def test_workspaces_numerados(ft_home, project, fake_engine):
    first = _workspace(project, fake_engine, ft_home, include_global=False)
    second = _workspace(project, fake_engine, ft_home, include_global=False)
    assert first.root.name == "evolve-01"
    assert second.root.name == "evolve-02"


def test_workspace_nao_e_detectado_como_ciclo_ativo(ft_home, project, fake_engine):
    ws = _workspace(project, fake_engine, ft_home, include_global=False)
    # Estado "em andamento" dentro do workspace evolve não pode contar como run.
    ws.state_file.write_text(
        yaml.safe_dump({"current_node": "evolve.analyze", "node_status": "running"}),
        encoding="utf-8",
    )
    assert cli_main._check_active_run(project) is None


# ---------------------------------------------------------------------------
# Validação, diff e apply
# ---------------------------------------------------------------------------

def test_validate_staged_detecta_yaml_quebrado(ft_home, project, fake_engine):
    ws = _workspace(project, fake_engine, ft_home, include_global=False)
    (ws.staged_project_dir / "process.yml").write_text("nodes: [", encoding="utf-8")
    errors = evolve.validate_staged(ws)
    assert errors and "project" in errors[0]


def test_validate_staged_exige_process_yml(ft_home, project, fake_engine):
    ws = _workspace(project, fake_engine, ft_home, include_global=False)
    (ws.staged_project_dir / "process.yml").unlink()
    errors = evolve.validate_staged(ws)
    assert any("nenhum process.yml" in error for error in errors)


def test_validate_staged_global_pristine(ft_home, project, fake_engine):
    ws = _workspace(project, fake_engine, ft_home)
    (ws.staged_global_dir("feature") / "engine_state.yml").write_text("x: 1\n", encoding="utf-8")
    errors = evolve.validate_staged(ws)
    assert any("global:feature" in error and "estado de execução" in error for error in errors)


def test_validate_staged_passa_sem_mudancas(ft_home, project, fake_engine):
    ws = _workspace(project, fake_engine, ft_home)
    assert evolve.validate_staged(ws) == []


def test_diff_e_apply_espelham_staging(ft_home, project, fake_engine):
    ws = _workspace(project, fake_engine, ft_home)

    staged_process = ws.staged_project_dir / "process.yml"
    staged_process.write_text(
        staged_process.read_text(encoding="utf-8") + "\n# melhoria EV-01\n",
        encoding="utf-8",
    )
    (ws.staged_project_dir / "scripts").mkdir(exist_ok=True)
    (ws.staged_project_dir / "scripts" / "novo.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    staged_readme = ws.staged_global_dir("feature") / "README.md"
    staged_readme.unlink()

    changes = evolve.diff_staged(ws)
    summary = {(change.target, change.status, change.relative) for change in changes}
    assert ("project", "modified", "process.yml") in summary
    assert ("project", "added", "scripts/novo.sh") in summary
    assert ("global:feature", "removed", "README.md") in summary
    assert len(changes) == 3

    evolve.apply_staged(ws, changes)
    real_process = project / ".ft" / "process" / "process.yml"
    assert "# melhoria EV-01" in real_process.read_text(encoding="utf-8")
    assert (project / ".ft" / "process" / "scripts" / "novo.sh").is_file()
    assert not (fake_engine / "templates" / "feature" / "README.md").exists()
    # Staging e alvos idênticos após o apply.
    assert evolve.diff_staged(ws) == []


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _evolve_args(**overrides) -> Namespace:
    values = {
        "command": "evolve",
        "process": None,
        "verbose": False,
        "directive": None,
        "template": "evolve_process",
        "project_target": True,
        "global_target": False,
        "cycle": None,
        "dry_run": False,
        "yes": True,
        "claude": True,
        "codex": None,
        "gemini": None,
        "opencode": None,
        "effort": None,
    }
    values.update(overrides)
    return Namespace(**values)


class _FakeRunner:
    """Simula o playbook: altera o staging e escreve os relatórios."""

    instances: list["_FakeRunner"] = []

    def __init__(self, *, process_path, state_path, project_root, **_kwargs):
        self.workspace = Path(project_root)
        self.process_path = Path(process_path)
        self.state_mgr = SimpleNamespace(
            load=lambda: SimpleNamespace(
                node_status="done", current_node=None, completed_nodes=["evolve.apply"]
            )
        )
        _FakeRunner.instances.append(self)

    def init_state(self):
        pass

    def run(self, mode):
        assert mode == "mvp"
        staged = self.workspace / "targets" / "project" / "process.yml"
        staged.write_text(
            staged.read_text(encoding="utf-8") + "\n# EV-01 aplicado\n",
            encoding="utf-8",
        )
        report_dir = self.workspace / "report"
        (report_dir / "findings.md").write_text("# Findings\n", encoding="utf-8")
        (report_dir / "evolution-report.md").write_text("# Evolution Report\n", encoding="utf-8")


@pytest.fixture()
def cli_engine(fake_engine, monkeypatch):
    monkeypatch.setattr(cli_main, "engine_root", lambda: fake_engine)
    monkeypatch.setattr(cli_main, "StepRunner", _FakeRunner)
    _FakeRunner.instances.clear()
    return fake_engine


def test_cmd_evolve_aplica_no_projeto(ft_home, project, fake_engine, cli_engine, monkeypatch, capsys):
    monkeypatch.chdir(project)
    cli_main.cmd_evolve(_evolve_args())
    out = capsys.readouterr().out
    assert "EV-01 aplicado" in (project / ".ft" / "process" / "process.yml").read_text(
        encoding="utf-8"
    )
    assert "aplicado(s)" in out
    # O runner rodou dentro do workspace evolve, nunca no projeto.
    assert _FakeRunner.instances[0].workspace.is_relative_to(paths.evolve_home(project))


def test_cmd_evolve_dry_run_nao_aplica(ft_home, project, fake_engine, cli_engine, monkeypatch, capsys):
    monkeypatch.chdir(project)
    before = (project / ".ft" / "process" / "process.yml").read_text(encoding="utf-8")
    cli_main.cmd_evolve(_evolve_args(dry_run=True))
    out = capsys.readouterr().out
    assert "--dry-run" in out
    assert (project / ".ft" / "process" / "process.yml").read_text(encoding="utf-8") == before


def test_cmd_evolve_exige_alvo(ft_home, project, fake_engine, cli_engine, monkeypatch, capsys):
    monkeypatch.chdir(project)
    with pytest.raises(SystemExit):
        cli_main.cmd_evolve(_evolve_args(project_target=False, global_target=False))
    assert "--project e/ou --global" in capsys.readouterr().out


def test_cmd_evolve_global_exige_git_no_engine(ft_home, project, fake_engine, cli_engine, monkeypatch, capsys):
    monkeypatch.chdir(project)
    shutil.rmtree(fake_engine / ".git")
    with pytest.raises(SystemExit):
        cli_main.cmd_evolve(_evolve_args(global_target=True))
    assert "checkout git do engine" in capsys.readouterr().out


def test_cmd_evolve_rejeita_process_flag(ft_home, project, fake_engine, cli_engine, monkeypatch):
    monkeypatch.chdir(project)
    with pytest.raises(ValueError, match="não aceita --process"):
        cli_main.cmd_evolve(_evolve_args(process="x.yml"))


def test_cmd_evolve_template_de_outro_entrypoint(ft_home, project, fake_engine, cli_engine, monkeypatch, capsys):
    monkeypatch.chdir(project)
    with pytest.raises(SystemExit):
        cli_main.cmd_evolve(_evolve_args(template="feature"))
    assert "entrypoint evolve" in capsys.readouterr().out
