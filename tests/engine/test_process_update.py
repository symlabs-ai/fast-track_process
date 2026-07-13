"""ft process update — drift e sincronização global→local dos processos.

Cobre a máquina de estados 3-way (base × local × global), o snapshot base
criado na materialização e reconstruído para forks legados, o fast-forward,
o merge via git merge-file e o comando CLI.
"""

from __future__ import annotations

import shutil
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from ft.cli import main as cli_main
from ft.engine import paths
from ft.engine import process_update as pu
from ft.engine.layout import process_digest, read_manifest

_REAL_ENGINE = cli_main.engine_root()


@pytest.fixture
def fake_engine(tmp_path, monkeypatch):
    """Cópia dos templates reais que os testes podem evoluir livremente."""
    engine = tmp_path / "engine"
    for template in ("base", "feature"):
        shutil.copytree(
            _REAL_ENGINE / "templates" / template,
            engine / "templates" / template,
        )
    monkeypatch.setattr(cli_main, "engine_root", lambda: engine)
    monkeypatch.setenv("FT_HOME", str(tmp_path / "ft_home"))
    return engine


@pytest.fixture
def project(tmp_path, fake_engine):
    root = tmp_path / "project"
    root.mkdir()
    cli_main.copy_template("base", root)
    (root / "docs").mkdir(exist_ok=True)
    (root / "src").mkdir(exist_ok=True)
    cli_main.materialize_process_template("feature", root, entrypoint="feature")
    return root


def _templates_root(fake_engine: Path) -> Path:
    return fake_engine / "templates"


def _scan(root: Path, fake_engine: Path, name: str | None = "feature"):
    states = pu.scan_processes(root, _templates_root(fake_engine), process_name=name)
    assert states, "processo esperado no manifest"
    return states[0]


def _evolve_global(fake_engine: Path, marker: str = "# evolved-global") -> Path:
    process = fake_engine / "templates" / "feature" / "process.yml"
    process.write_text(
        process.read_text(encoding="utf-8") + f"\n{marker}\n", encoding="utf-8"
    )
    return process


def _customize_local(root: Path, marker: str = "# fork-local") -> Path:
    script = root / ".ft" / "process" / "feature" / "scripts" / "product.sh"
    script.write_text(
        script.read_text(encoding="utf-8") + f"\n{marker}\n", encoding="utf-8"
    )
    return script


def _update_args(**overrides) -> Namespace:
    values = {
        "command": "process",
        "process_command": "update",
        "process": None,
        "verbose": False,
        "name": None,
        "check": False,
        "yes": False,
    }
    values.update(overrides)
    return Namespace(**values)


# ---------------------------------------------------------------- snapshot


def test_materialize_creates_base_snapshot(project):
    local = project / ".ft" / "process" / "feature"
    snapshot = local / pu.BASE_SNAPSHOT_DIR
    assert (snapshot / "process.yml").is_file()
    assert (snapshot / "scripts").is_dir()
    # O snapshot é o ancestral exato do fork recém-materializado.
    assert process_digest(snapshot / "process.yml") == process_digest(
        local / "process.yml"
    )
    # E não contém áreas internas aninhadas.
    assert not (snapshot / pu.BASE_SNAPSHOT_DIR).exists()


def test_scan_is_read_only(project, fake_engine):
    local = project / ".ft" / "process" / "feature"
    shutil.rmtree(local / pu.BASE_SNAPSHOT_DIR)
    _evolve_global(fake_engine)

    state = _scan(project, fake_engine)

    assert state.state == pu.STATE_FAST_FORWARD
    assert state.base_source == "local"
    # O scan classificou sem recriar o snapshot: preflights não sujam a árvore.
    assert not (local / pu.BASE_SNAPSHOT_DIR).exists()


# ------------------------------------------------------------ estados 3-way


def test_state_in_sync(project, fake_engine):
    state = _scan(project, fake_engine)
    assert state.state == pu.STATE_IN_SYNC


def test_state_fast_forward(project, fake_engine):
    _evolve_global(fake_engine)
    state = _scan(project, fake_engine)
    assert state.state == pu.STATE_FAST_FORWARD


def test_state_local_fork(project, fake_engine):
    _customize_local(project)
    state = _scan(project, fake_engine)
    assert state.state == pu.STATE_LOCAL_FORK


def test_state_diverged(project, fake_engine):
    _customize_local(project)
    _evolve_global(fake_engine)
    state = _scan(project, fake_engine)
    assert state.state == pu.STATE_DIVERGED


def test_state_diverged_without_ancestor(project, fake_engine):
    shutil.rmtree(project / ".ft" / "process" / "feature" / pu.BASE_SNAPSHOT_DIR)
    _customize_local(project)
    _evolve_global(fake_engine)
    state = _scan(project, fake_engine)
    assert state.state == pu.STATE_DIVERGED_NO_BASE
    assert state.base_source is None


def test_bootstrap_base_from_pristine_global(project, fake_engine):
    """Fork legado customizado + global intocado: o ancestral é o global."""
    shutil.rmtree(project / ".ft" / "process" / "feature" / pu.BASE_SNAPSHOT_DIR)
    _customize_local(project)

    state = _scan(project, fake_engine)
    assert state.state == pu.STATE_LOCAL_FORK
    assert state.base_source == "global"

    snapshot = pu.ensure_base_snapshot(state)
    assert snapshot is not None and (snapshot / "process.yml").is_file()
    assert process_digest(snapshot / "process.yml") == state.base_digest


# ------------------------------------------------------------- fast-forward


def test_fast_forward_updates_fork_and_ancestor(project, fake_engine):
    _evolve_global(fake_engine)
    state = _scan(project, fake_engine)

    staging, changed = pu.prepare_fast_forward(project, state)
    assert "atualizado: process.yml" in changed
    backup = pu.apply_update(project, state, staging)

    local = project / ".ft" / "process" / "feature"
    assert "# evolved-global" in (local / "process.yml").read_text(encoding="utf-8")
    assert backup.is_dir()
    assert not staging.exists()

    # Manifest re-registrado com o digest do novo global.
    record = read_manifest(project)["processes"]["feature"]
    assert record["source_digest"] == process_digest(
        fake_engine / "templates" / "feature" / "process.yml"
    )

    assert _scan(project, fake_engine).state == pu.STATE_IN_SYNC


# -------------------------------------------------------------------- merge


def test_merge_preserves_local_and_absorbs_global(project, fake_engine):
    _customize_local(project)
    _evolve_global(fake_engine)
    state = _scan(project, fake_engine)
    assert state.state == pu.STATE_DIVERGED

    staging = pu.staging_dir_for(project, "feature")
    result = pu.build_merge_staging(state, staging)

    assert result.clean
    merged_process = (staging / "process.yml").read_text(encoding="utf-8")
    merged_script = (staging / "scripts" / "product.sh").read_text(encoding="utf-8")
    assert "# evolved-global" in merged_process
    assert "# fork-local" in merged_script

    pu.apply_update(project, state, staging)

    # O novo ancestral é o global integrado, não o merge: a customização
    # local sobrevivente continua aparecendo como fork nos próximos scans.
    after = _scan(project, fake_engine)
    assert after.state == pu.STATE_LOCAL_FORK
    local = project / ".ft" / "process" / "feature"
    assert "# fork-local" in (
        local / "scripts" / "product.sh"
    ).read_text(encoding="utf-8")
    assert "# evolved-global" in (local / "process.yml").read_text(encoding="utf-8")
    base_script = local / pu.BASE_SNAPSHOT_DIR / "scripts" / "product.sh"
    assert "# fork-local" not in base_script.read_text(encoding="utf-8")


def test_merge_conflict_keeps_diff3_markers(project, fake_engine):
    local_process = project / ".ft" / "process" / "feature" / "process.yml"
    global_process = fake_engine / "templates" / "feature" / "process.yml"
    local_process.write_text(
        local_process.read_text(encoding="utf-8").replace(
            'version: "1.1.0"', 'version: "1.5.0-fork"'
        ),
        encoding="utf-8",
    )
    global_process.write_text(
        global_process.read_text(encoding="utf-8").replace(
            'version: "1.1.0"', 'version: "2.0.0"'
        ),
        encoding="utf-8",
    )

    state = _scan(project, fake_engine)
    assert state.state == pu.STATE_DIVERGED

    result = pu.build_merge_staging(state, pu.staging_dir_for(project, "feature"))

    assert result.conflicts == ["process.yml"]
    staged = (result.staging_dir / "process.yml").read_text(encoding="utf-8")
    assert "<<<<<<< local" in staged
    assert "||||||| base" in staged
    assert ">>>>>>> global" in staged
    # O fork local permaneceu intocado.
    assert 'version: "1.5.0-fork"' in local_process.read_text(encoding="utf-8")


def test_apply_update_rolls_back_on_failure(project, fake_engine):
    _evolve_global(fake_engine)
    state = _scan(project, fake_engine)
    staging, _ = pu.prepare_fast_forward(project, state)
    before = process_digest(state.local_process)

    with patch.object(pu, "refresh_process_digests", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError, match="boom"):
            pu.apply_update(project, state, staging)

    # Fork restaurado byte a byte; backup consumido pelo rollback.
    assert process_digest(state.local_process) == before
    assert not pu.backup_dir_for(project, "feature").exists()


# ------------------------------------------------------------ coordenadas


def test_global_side_is_rewritten_to_local_coordinates(tmp_path):
    template = tmp_path / "templates" / "legacy"
    (template / "scripts").mkdir(parents=True)
    (template / "process.yml").write_text(
        "id: legacy\nnodes:\n  - id: a\n    validators:\n"
        "      - command_succeeds: bash .ft/process/scripts/run.sh\n",
        encoding="utf-8",
    )
    (template / "scripts" / "run.sh").write_text(
        "cat .ft/process/process.yml\n", encoding="utf-8"
    )

    out = tmp_path / "out"
    pu.materialize_global_to(template, "legacy", out)

    assert ".ft/process/legacy/scripts/run.sh" in (out / "process.yml").read_text(
        encoding="utf-8"
    )
    assert ".ft/process/legacy/process.yml" in (
        out / "scripts" / "run.sh"
    ).read_text(encoding="utf-8")


# ---------------------------------------------------------------------- CLI


def test_cli_check_exits_zero_in_sync(project, monkeypatch, capsys):
    monkeypatch.chdir(project)
    with pytest.raises(SystemExit) as excinfo:
        cli_main.cmd_process_update(_update_args(check=True))
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "em sincronia" in out


def test_cli_check_exits_one_on_drift_without_writing(project, fake_engine, monkeypatch, capsys):
    _evolve_global(fake_engine)
    before = process_digest(project / ".ft" / "process" / "feature" / "process.yml")
    monkeypatch.chdir(project)

    with pytest.raises(SystemExit) as excinfo:
        cli_main.cmd_process_update(_update_args(check=True))

    assert excinfo.value.code == 1
    assert "fast-forward" in capsys.readouterr().out
    assert process_digest(
        project / ".ft" / "process" / "feature" / "process.yml"
    ) == before


def test_cli_yes_applies_fast_forward(project, fake_engine, monkeypatch, capsys):
    _evolve_global(fake_engine)
    monkeypatch.chdir(project)

    cli_main.cmd_process_update(_update_args(yes=True))

    out = capsys.readouterr().out
    assert "atualizado" in out
    assert _scan(project, fake_engine).state == pu.STATE_IN_SYNC


def test_cli_merge_requires_confirmation(project, fake_engine, monkeypatch, capsys):
    _customize_local(project)
    _evolve_global(fake_engine)
    monkeypatch.chdir(project)

    with patch("builtins.input", return_value="n"):
        with pytest.raises(SystemExit) as excinfo:
            cli_main.cmd_process_update(_update_args())
    assert excinfo.value.code == 1
    assert "mantido como está" in capsys.readouterr().out
    assert _scan(project, fake_engine).state == pu.STATE_DIVERGED

    with patch("builtins.input", return_value="s"):
        cli_main.cmd_process_update(_update_args())
    assert _scan(project, fake_engine).state == pu.STATE_LOCAL_FORK


def test_cli_blocks_with_active_cycle(project, fake_engine, monkeypatch):
    _evolve_global(fake_engine)
    monkeypatch.chdir(project)
    monkeypatch.setattr(cli_main, "_check_active_run", lambda root: "cycle-01 (build)")

    with pytest.raises(RuntimeError, match="ciclo ativo"):
        cli_main.cmd_process_update(_update_args(yes=True))


def test_cli_unknown_process_fails(project, monkeypatch, capsys):
    monkeypatch.chdir(project)
    with pytest.raises(SystemExit) as excinfo:
        cli_main.cmd_process_update(_update_args(name="inexistente"))
    assert excinfo.value.code == 1


def test_feature_preflight_warns_about_drift(project, fake_engine, capsys):
    # Sem drift, silêncio.
    cli_main._warn_process_drift(project, "feature")
    assert capsys.readouterr().out == ""

    _evolve_global(fake_engine)
    cli_main._warn_process_drift(project, "feature")
    assert "ft process update feature" in capsys.readouterr().out


def test_feature_preflight_warning_never_raises(project, monkeypatch, capsys):
    monkeypatch.setattr(
        cli_main, "_drift_scan", lambda *a, **k: (_ for _ in ()).throw(RuntimeError())
    )
    cli_main._warn_process_drift(project, "feature")  # não levanta
    assert capsys.readouterr().out == ""
