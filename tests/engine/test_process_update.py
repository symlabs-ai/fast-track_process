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
from ft.engine import feature_batch as fb
from ft.engine import paths
from ft.engine import process_update as pu
from ft.engine.layout import process_digest, read_manifest

_REAL_ENGINE = cli_main.engine_root()


@pytest.fixture
def fake_engine(tmp_path, monkeypatch):
    """Cópia dos templates reais que os testes podem evoluir livremente."""
    engine = tmp_path / "engine"
    for template in ("base", "feature", "bug"):
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
    cli_main.materialize_process_template("bug", root, entrypoint="feature")
    return root


def _templates_root(fake_engine: Path) -> Path:
    return fake_engine / "templates"


def _scan(root: Path, fake_engine: Path, name: str | None = "feature"):
    states = pu.scan_processes(root, _templates_root(fake_engine), process_name=name)
    assert states, "processo esperado no manifest"
    return states[0]


def _evolve_global(
    fake_engine: Path,
    marker: str = "# evolved-global",
    *,
    name: str = "feature",
) -> Path:
    process = fake_engine / "templates" / name / "process.yml"
    process.write_text(
        process.read_text(encoding="utf-8") + f"\n{marker}\n", encoding="utf-8"
    )
    return process


_DEFAULT_PROCESS_PATH = object()


def _write_active_state(
    root: Path,
    cycle_name: str = "cycle-01",
    *,
    process_name: str = "feature",
    process_path: str | None | object = _DEFAULT_PROCESS_PATH,
    continuous: bool = False,
) -> Path:
    """Cria um runtime inequivocamente ativo para exercitar o guard da CLI."""
    state = (
        paths.continuous_state_path(root)
        if continuous
        else paths.worktrees_home(root)
        / cycle_name
        / "state"
        / "engine_state.yml"
    )
    state.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "process_id": process_name,
        "template_id": process_name,
        "current_node": f"{process_name}.implement",
        "node_status": "delegated",
    }
    if process_path is _DEFAULT_PROCESS_PATH:
        payload["process_path"] = f".ft/process/{process_name}/process.yml"
    elif process_path is not None:
        payload["process_path"] = process_path
    state.write_text(
        yaml.safe_dump(payload, sort_keys=False),
        encoding="utf-8",
    )
    return state


def _write_active_batch(
    root: Path,
    *,
    batch_id: str = "batch-01",
    template: str = "feature",
    status: str = "paused",
) -> Path:
    feature = fb.BatchFeature(feature_id="F-01", demand="demanda em andamento")
    batch = fb.FeatureBatch(
        batch_id=batch_id,
        project_root=str(root),
        template=template,
        features=[feature],
        waves=[[feature.feature_id]],
        status=status,
    )
    return fb.save_batch(batch)


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


def test_named_tweak_process_uses_same_update_pipeline(
    project, fake_engine, monkeypatch
):
    template = fake_engine / "templates" / "tweak"
    template.mkdir()
    process = template / "process.yml"
    process.write_text(
        """id: tweak
version: '1.0.0'
execution_policy:
  entrypoint: feature
  template: tweak
close_policy:
  backlog:
    mode: none
  merge: full
nodes:
  - id: tweak.apply
    type: build
    title: Aplicar
    executor: codex
    outputs: [src/]
    next: tweak.end
  - id: tweak.end
    type: end
    title: Fim
""",
        encoding="utf-8",
    )
    local = cli_main.materialize_process_template(
        "tweak", project, entrypoint="feature"
    )
    initial = _scan(project, fake_engine, name="tweak")
    assert initial.state == pu.STATE_IN_SYNC
    assert initial.entrypoint == "feature"

    process.write_text(
        process.read_text(encoding="utf-8") + "\n# tweak global evoluiu\n",
        encoding="utf-8",
    )
    assert _scan(project, fake_engine, name="tweak").state == pu.STATE_FAST_FORWARD

    monkeypatch.chdir(project)
    cli_main.cmd_process_update(_update_args(name="tweak", yes=True))

    assert "# tweak global evoluiu" in local.read_text(encoding="utf-8")
    assert _scan(project, fake_engine, name="tweak").state == pu.STATE_IN_SYNC


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
            'version: "1.2.0"', 'version: "1.5.0-fork"'
        ),
        encoding="utf-8",
    )
    global_process.write_text(
        global_process.read_text(encoding="utf-8").replace(
            'version: "1.2.0"', 'version: "2.0.0"'
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


def test_cli_allows_bug_update_while_feature_cycle_is_active(
    project, fake_engine, monkeypatch
):
    _evolve_global(fake_engine, name="bug")
    _write_active_state(project, process_name="feature")
    monkeypatch.chdir(project)

    cli_main.cmd_process_update(_update_args(name="bug", yes=True))

    assert _scan(project, fake_engine, name="bug").state == pu.STATE_IN_SYNC


def test_cli_blocks_bug_update_while_bug_cycle_is_active(
    project, fake_engine, monkeypatch
):
    _evolve_global(fake_engine, name="bug")
    _write_active_state(project, process_name="bug")
    monkeypatch.chdir(project)

    with pytest.raises(RuntimeError, match="ciclo ativo"):
        cli_main.cmd_process_update(_update_args(name="bug", yes=True))

    assert _scan(project, fake_engine, name="bug").state == pu.STATE_FAST_FORWARD


def test_cli_blocks_overlap_across_multiple_active_cycles(
    project, fake_engine, monkeypatch
):
    _evolve_global(fake_engine, name="bug")
    # O ciclo conflitante é deliberadamente o mais antigo: olhar apenas o
    # primeiro/mais recente runtime daria um falso negativo.
    _write_active_state(project, "cycle-01", process_name="bug")
    _write_active_state(project, "cycle-02", process_name="feature")
    monkeypatch.chdir(project)

    with pytest.raises(RuntimeError, match="ciclo ativo"):
        cli_main.cmd_process_update(_update_args(name="bug", yes=True))

    assert _scan(project, fake_engine, name="bug").state == pu.STATE_FAST_FORWARD


def test_cli_ignores_orphan_worktree_without_state_or_live_sentinel(
    project, fake_engine, monkeypatch
):
    _evolve_global(fake_engine, name="bug")
    preparing = paths.worktrees_home(project) / "cycle-01-preparing"
    preparing.mkdir(parents=True)
    # A janela legítima de startup agora é coberta por um sentinel. Sem state e
    # sem sentinel vivo, este diretório é resíduo órfão, não um runtime ativo.
    (preparing / ".git").write_text(
        "gitdir: /tmp/orphan-worktree\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(project)

    assert cli_main._process_update_runtime_guard(project, {"bug"}) == []
    cli_main.cmd_process_update(_update_args(name="bug", yes=True))

    assert _scan(project, fake_engine, name="bug").state == pu.STATE_IN_SYNC


@pytest.mark.parametrize(
    "ambiguous_path",
    [
        None,
        ".ft/process/process.yml",
        "../process/feature/process.yml",
    ],
)
def test_cli_fails_closed_for_active_state_with_ambiguous_process_metadata(
    project, fake_engine, monkeypatch, ambiguous_path
):
    _evolve_global(fake_engine, name="bug")
    _write_active_state(
        project,
        process_name="feature",
        process_path=ambiguous_path,
    )
    monkeypatch.chdir(project)

    with pytest.raises(RuntimeError, match="ciclo ativo"):
        cli_main.cmd_process_update(_update_args(name="bug", yes=True))

    assert _scan(project, fake_engine, name="bug").state == pu.STATE_FAST_FORWARD


def test_cli_blocks_disjoint_update_during_continuous_runtime(
    project, fake_engine, monkeypatch
):
    _evolve_global(fake_engine, name="bug")
    _write_active_state(project, process_name="feature", continuous=True)
    monkeypatch.chdir(project)

    with pytest.raises(RuntimeError, match="ciclo ativo"):
        cli_main.cmd_process_update(_update_args(name="bug", yes=True))

    assert _scan(project, fake_engine, name="bug").state == pu.STATE_FAST_FORWARD


def test_cli_unnamed_update_allows_only_actionable_disjoint_process(
    project, fake_engine, monkeypatch
):
    _evolve_global(fake_engine, name="bug")
    _write_active_state(project, process_name="feature")
    monkeypatch.chdir(project)

    cli_main.cmd_process_update(_update_args(yes=True))

    assert _scan(project, fake_engine, name="bug").state == pu.STATE_IN_SYNC
    assert _scan(project, fake_engine, name="feature").state == pu.STATE_IN_SYNC


def test_cli_unnamed_update_blocks_atomically_on_any_active_process(
    project, fake_engine, monkeypatch
):
    _evolve_global(fake_engine, marker="# feature-evolved", name="feature")
    _evolve_global(fake_engine, marker="# bug-evolved", name="bug")
    _write_active_state(project, process_name="feature")
    bug_before = process_digest(
        project / ".ft" / "process" / "bug" / "process.yml"
    )
    feature_before = process_digest(
        project / ".ft" / "process" / "feature" / "process.yml"
    )
    monkeypatch.chdir(project)

    with pytest.raises(RuntimeError, match="ciclo ativo"):
        cli_main.cmd_process_update(_update_args(yes=True))

    assert process_digest(
        project / ".ft" / "process" / "bug" / "process.yml"
    ) == bug_before
    assert process_digest(
        project / ".ft" / "process" / "feature" / "process.yml"
    ) == feature_before
    assert _scan(project, fake_engine, name="bug").state == pu.STATE_FAST_FORWARD
    assert _scan(project, fake_engine, name="feature").state == pu.STATE_FAST_FORWARD


def test_cli_check_remains_read_only_with_same_process_active(
    project, fake_engine, monkeypatch
):
    _evolve_global(fake_engine, name="bug")
    _write_active_state(project, process_name="bug")
    monkeypatch.chdir(project)

    with pytest.raises(SystemExit) as excinfo:
        cli_main.cmd_process_update(_update_args(name="bug", check=True))

    assert excinfo.value.code == 1
    assert _scan(project, fake_engine, name="bug").state == pu.STATE_FAST_FORWARD


def test_cli_cas_aborts_if_local_bundle_changes_before_apply(
    project, fake_engine, monkeypatch
):
    _evolve_global(fake_engine, marker="# global-bug-update", name="bug")
    local_process = project / ".ft" / "process" / "bug" / "process.yml"
    original_validate = cli_main._validate_staged_process

    def validate_then_change_local(staging):
        result = original_validate(staging)
        local_process.write_text(
            local_process.read_text(encoding="utf-8")
            + "\n# concurrent-local-change\n",
            encoding="utf-8",
        )
        return result

    monkeypatch.setattr(
        cli_main,
        "_validate_staged_process",
        validate_then_change_local,
    )
    monkeypatch.chdir(project)

    with pytest.raises(RuntimeError, match="mudou durante o update"):
        cli_main.cmd_process_update(_update_args(name="bug", yes=True))

    content = local_process.read_text(encoding="utf-8")
    assert "# concurrent-local-change" in content
    assert "# global-bug-update" not in content
    assert not pu.backup_dir_for(project, "bug").exists()


def test_cli_allows_bug_update_while_feature_batch_is_paused(
    project, fake_engine, monkeypatch
):
    _evolve_global(fake_engine, name="bug")
    _write_active_batch(project, template="feature", status="paused")
    monkeypatch.chdir(project)

    cli_main.cmd_process_update(_update_args(name="bug", yes=True))

    assert _scan(project, fake_engine, name="bug").state == pu.STATE_IN_SYNC


def test_cli_blocks_feature_update_while_feature_batch_is_paused(
    project, fake_engine, monkeypatch
):
    _evolve_global(fake_engine, name="feature")
    _write_active_batch(project, template="feature", status="paused")
    monkeypatch.chdir(project)

    with pytest.raises(RuntimeError, match="batch|ciclo ativo"):
        cli_main.cmd_process_update(_update_args(name="feature", yes=True))

    assert _scan(project, fake_engine, name="feature").state == pu.STATE_FAST_FORWARD


def test_cli_running_feature_batch_allows_disjoint_bug_update(
    project, fake_engine, monkeypatch
):
    _evolve_global(fake_engine, name="bug")
    _write_active_batch(project, template="feature", status="running")
    monkeypatch.chdir(project)

    cli_main.cmd_process_update(_update_args(name="bug", yes=True))

    assert _scan(project, fake_engine, name="bug").state == pu.STATE_IN_SYNC


def test_cli_running_feature_batch_blocks_feature_update(
    project, fake_engine, monkeypatch
):
    _evolve_global(fake_engine, name="feature")
    _write_active_batch(project, template="feature", status="running")
    monkeypatch.chdir(project)

    with pytest.raises(RuntimeError, match="batch|ciclo ativo"):
        cli_main.cmd_process_update(_update_args(name="feature", yes=True))

    assert _scan(project, fake_engine, name="feature").state == pu.STATE_FAST_FORWARD


def test_cli_older_running_batch_is_not_hidden_by_newer_done_batch(
    project, fake_engine, monkeypatch
):
    _evolve_global(fake_engine, name="bug")
    _write_active_batch(
        project,
        batch_id="batch-01",
        template="bug",
        status="running",
    )
    _write_active_batch(
        project,
        batch_id="batch-02",
        template="feature",
        status="done",
    )
    monkeypatch.chdir(project)

    with pytest.raises(RuntimeError, match="batch|ciclo ativo"):
        cli_main.cmd_process_update(_update_args(name="bug", yes=True))

    assert _scan(project, fake_engine, name="bug").state == pu.STATE_FAST_FORWARD


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


def test_generated_caches_are_invisible_to_update(project, fake_engine):
    """Caches (__pycache__, .pyc) no template ou no fork nunca entram em
    cópia, merge ou classificação — mesmo conjunto ignorado pelo digest."""
    pycache = fake_engine / "templates" / "feature" / "scripts" / "__pycache__"
    pycache.mkdir(exist_ok=True)
    (pycache / "x.cpython-312.pyc").write_bytes(b"\x00cache")
    (fake_engine / "templates" / "feature" / "scripts" / "y.pyc").write_bytes(b"\x00")

    # Cache não é drift: continua em sincronia.
    assert _scan(project, fake_engine).state == pu.STATE_IN_SYNC

    # E não vaza para o fork num update real.
    _customize_local(project)
    _evolve_global(fake_engine)
    state = _scan(project, fake_engine)
    result = pu.build_merge_staging(state, pu.staging_dir_for(project, "feature"))
    assert result.clean
    staged = [str(p) for p in result.staging_dir.rglob("*")]
    assert not any("__pycache__" in p or p.endswith(".pyc") for p in staged)
    pu.apply_update(project, state, result.staging_dir)
    local = project / ".ft" / "process" / "feature"
    assert not list(local.rglob("*.pyc"))
