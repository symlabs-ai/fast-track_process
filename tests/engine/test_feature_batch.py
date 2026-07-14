"""Testes do ft feature --parallel (feature_batch + orquestrador)."""

from __future__ import annotations

from argparse import Namespace
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
import yaml

from ft.cli import feature_parallel as fp
from ft.cli import main as cli_main
from ft.engine import feature_batch as fb
from ft.engine import paths

# ---------------------------------------------------------------------------
# EngineSpec
# ---------------------------------------------------------------------------


def test_parse_engine_spec_completo():
    spec = fb.parse_engine_spec("codex:gpt-5.3@high")
    assert (spec.engine, spec.model, spec.effort) == ("codex", "gpt-5.3", "high")


def test_parse_engine_spec_variantes():
    assert fb.parse_engine_spec("claude") == fb.EngineSpec("claude")
    assert fb.parse_engine_spec("gemini@max").effort == "max"
    assert fb.parse_engine_spec("opencode:pgx/x@low").model == "pgx/x"


def test_parse_engine_spec_invalido():
    with pytest.raises(fb.FeatureBatchError, match="engine desconhecido"):
        fb.parse_engine_spec("cursor")
    with pytest.raises(fb.FeatureBatchError, match="vazia"):
        fb.parse_engine_spec("  ")


def test_parse_engine_list():
    specs = fb.parse_engine_list("claude:opus, codex:gpt-5.3@high ,gemini")
    assert [spec.engine for spec in specs] == ["claude", "codex", "gemini"]


# ---------------------------------------------------------------------------
# Demandas
# ---------------------------------------------------------------------------


def test_split_input_demands_por_secoes():
    text = """## Busca por telefone
engine: codex:gpt-5.3
Adicionar busca por telefone na listagem.

## Dark mode
Tema escuro com toggle.
"""
    demands = fb.split_input_demands(text)
    assert len(demands) == 2
    assert demands[0][0].startswith("Busca por telefone")
    assert demands[0][1] == fb.EngineSpec("codex", "gpt-5.3")
    assert demands[1][1] is None


def test_split_input_demands_por_separador():
    text = "Feature A\n---\nengine: claude:opus\nFeature B\n"
    demands = fb.split_input_demands(text)
    assert len(demands) == 2
    assert demands[1] == ("Feature B", fb.EngineSpec("claude", "opus"))


def test_split_input_demands_vazio():
    with pytest.raises(fb.FeatureBatchError):
        fb.split_input_demands("   \n")


def test_build_features_round_robin_e_prioridade():
    demands = [
        ("A", None),
        ("B", fb.EngineSpec("gemini")),
        ("C", None),
    ]
    specs = [fb.EngineSpec("claude", "opus"), fb.EngineSpec("codex", "gpt-5.3")]
    features = fb.build_features(demands, specs)
    assert [feature.feature_id for feature in features] == ["F-01", "F-02", "F-03"]
    assert features[0].engine_spec == specs[0]  # round-robin
    assert features[1].engine_spec.engine == "gemini"  # spec própria vence
    assert features[2].engine_spec == specs[0]  # índice 2 % 2 == 0


def test_build_features_exige_duas_demandas():
    with pytest.raises(fb.FeatureBatchError, match="ao menos 2"):
        fb.build_features([("solo", None)])


def test_slugify():
    assert fb.slugify("Busca por Telefone! çã") == "busca-por-telefone-ca"
    assert fb.slugify("###") == "feature"


def test_backlog_items_normaliza_e_demanda_explicita_precisa_ser_inequivoca():
    assert fb.backlog_items("PB-2, pb-010 e PB-010A") == [
        "PB-002",
        "PB-010",
        "PB-010A",
    ]
    assert fb.explicit_backlog_item("Evoluir o PB-7") == "PB-007"
    assert fb.explicit_backlog_item("Feature sem item") is None
    with pytest.raises(fb.FeatureBatchError, match="mais de um PB"):
        fb.explicit_backlog_item("Unir PB-001 e PB-002")


# ---------------------------------------------------------------------------
# Plano
# ---------------------------------------------------------------------------


def _features(n: int = 3) -> list[fb.BatchFeature]:
    return fb.build_features([(f"demanda {i}", None) for i in range(1, n + 1)])


def _plan(entries: list[dict]) -> dict:
    return {"schema_version": fb.PLAN_SCHEMA_VERSION, "features": entries}


def test_validate_plan_ok():
    features = _features(2)
    plan = _plan(
        [
            {"id": "F-01", "areas": ["src/a/"], "depends_on": []},
            {"id": "F-02", "areas": ["src/b/"], "depends_on": ["F-01"]},
        ]
    )
    assert fb.validate_plan(plan, features) == []


def test_validate_plan_erros():
    features = _features(2)
    plan = _plan(
        [
            {"id": "F-01", "areas": [], "depends_on": ["F-09", "F-01"]},
        ]
    )
    errors = fb.validate_plan(plan, features)
    text = "; ".join(errors)
    assert "areas" in text
    assert "desconhecidos" in text
    assert "si mesma" in text
    assert "ausentes" in text and "F-02" in text


def test_validate_plan_area_absoluta():
    features = _features(2)
    plan = _plan(
        [
            {"id": "F-01", "areas": ["/etc"], "depends_on": []},
            {"id": "F-02", "areas": ["../fora"], "depends_on": []},
        ]
    )
    errors = fb.validate_plan(plan, features)
    assert any("F-01" in error for error in errors)
    assert any("F-02" in error for error in errors)


def test_tweak_planner_shares_one_short_budget_across_retry(tmp_path, monkeypatch):
    from ft.engine import delegate as delegate_module

    features = _features(2)
    batch_directory = tmp_path / "batch"
    (batch_directory / "logs").mkdir(parents=True)
    calls: list[int | None] = []

    def fake_delegate(**kwargs):
        calls.append(kwargs.get("llm_timeout_seconds"))
        if len(calls) == 1:
            return SimpleNamespace(success=False)
        (batch_directory / fb.PLAN_FILENAME).write_text(
            yaml.safe_dump(
                _plan(
                    [
                        {"id": "F-01", "areas": ["src/a/"], "depends_on": []},
                        {"id": "F-02", "areas": ["src/b/"], "depends_on": []},
                    ]
                )
            ),
            encoding="utf-8",
        )
        return SimpleNamespace(success=True)

    monkeypatch.setattr(delegate_module, "delegate_to_llm", fake_delegate)
    monotonic = iter([100.0, 100.0, 110.0])
    monkeypatch.setattr(fp.time, "monotonic", lambda: next(monotonic))

    plan = fp._run_planner(
        batch_directory,
        features,
        llm_engine="codex",
        llm_model="gpt-5.6-sol",
        llm_effort="max",
        llm_timeout_seconds=120,
    )

    assert plan["schema_version"] == fb.PLAN_SCHEMA_VERSION
    assert calls == [120, 110]


def test_tweak_planner_does_not_round_subsecond_budget_up(tmp_path, monkeypatch):
    from ft.engine import delegate as delegate_module

    features = _features(2)
    batch_directory = tmp_path / "batch"
    (batch_directory / "logs").mkdir(parents=True)
    calls: list[int | None] = []

    def fake_delegate(**kwargs):
        calls.append(kwargs.get("llm_timeout_seconds"))
        return SimpleNamespace(success=False)

    monkeypatch.setattr(delegate_module, "delegate_to_llm", fake_delegate)
    monotonic = iter([100.0, 100.0, 219.5])
    monkeypatch.setattr(fp.time, "monotonic", lambda: next(monotonic))

    with pytest.raises(fb.FeatureBatchError, match="budget/2 tentativas"):
        fp._run_planner(
            batch_directory,
            features,
            llm_engine="codex",
            llm_model="gpt-5.6-sol",
            llm_effort="max",
            llm_timeout_seconds=120,
        )

    assert calls == [120]


# ---------------------------------------------------------------------------
# Waves
# ---------------------------------------------------------------------------


def _feature(
    fid: str, areas: list[str], deps: list[str] | None = None
) -> fb.BatchFeature:
    return fb.BatchFeature(
        feature_id=fid, demand=fid, areas=areas, depends_on=deps or []
    )


def test_compute_waves_independentes_juntas():
    waves = fb.compute_waves(
        [
            _feature("F-01", ["src/a/"]),
            _feature("F-02", ["src/b/"]),
        ]
    )
    assert waves == [["F-01", "F-02"]]


def test_compute_waves_dependencia_sequencia():
    waves = fb.compute_waves(
        [
            _feature("F-01", ["src/a/"]),
            _feature("F-02", ["src/b/"], deps=["F-01"]),
            _feature("F-03", ["src/c/"], deps=["F-01"]),
        ]
    )
    assert waves == [["F-01"], ["F-02", "F-03"]]


def test_compute_waves_overlap_de_area_separa():
    waves = fb.compute_waves(
        [
            _feature("F-01", ["src/api/"]),
            _feature("F-02", ["src/api/users/"]),  # prefixo → conflito
            _feature("F-03", ["src/ui/"]),
        ]
    )
    assert waves == [["F-01", "F-03"], ["F-02"]]


def test_compute_waves_ciclo_detectado():
    with pytest.raises(fb.FeatureBatchError, match="cíclica"):
        fb.compute_waves(
            [
                _feature("F-01", ["src/a/"], deps=["F-02"]),
                _feature("F-02", ["src/b/"], deps=["F-01"]),
            ]
        )


def test_areas_overlap():
    assert fb._areas_overlap("src/api", "src/api/users/")
    assert fb._areas_overlap("src/api/", "src/api")
    assert not fb._areas_overlap("src/api/", "src/apiv2/")
    assert not fb._areas_overlap("src/a/", "src/b/")


# ---------------------------------------------------------------------------
# Estado do batch
# ---------------------------------------------------------------------------


def test_batch_roundtrip(tmp_path):
    features = _features(2)
    features[0].engine_spec = fb.EngineSpec("codex", "gpt-5.3", "high")
    features[0].cycle_name = "cycle-05-f-01-demanda-1"
    features[0].status = "merged"
    features[1].reserved_backlog_item = "PB-019"
    batch = fb.FeatureBatch(
        batch_id="batch-01",
        project_root=str(tmp_path / "proj"),
        template="feature",
        features=features,
        waves=[["F-01"], ["F-02"]],
        current_wave=1,
        max_parallel=3,
        planner_engine="codex",
        planner_model="gpt-5.6-sol",
        planner_effort="high",
    )
    fb.save_batch(batch)
    loaded = fb.load_batch(tmp_path / "proj", "batch-01")
    assert loaded.to_dict() == batch.to_dict()
    assert loaded.feature("F-01").engine_spec.label.startswith("codex/gpt-5.3")


def test_batch_roundtrip_legado_sem_reserva(tmp_path):
    batch = fb.FeatureBatch(
        batch_id="batch-01",
        project_root=str(tmp_path / "proj"),
        template="feature",
        features=_features(2),
        waves=[["F-01", "F-02"]],
    )
    legacy = batch.to_dict()

    loaded = fb.FeatureBatch.from_dict(legacy)

    assert [feature.reserved_backlog_item for feature in loaded.features] == [
        None,
        None,
    ]
    assert loaded.to_dict() == legacy


def test_new_batch_id_incrementa(tmp_path):
    root = tmp_path / "proj"
    assert fb.new_batch_id(root) == "batch-01"
    fb.batch_dir(root, "batch-03").mkdir(parents=True)
    assert fb.new_batch_id(root) == "batch-04"


def test_latest_batch_id(tmp_path):
    root = tmp_path / "proj"
    assert fb.latest_batch_id(root) is None
    for batch_id in ("batch-01", "batch-02"):
        directory = fb.batch_dir(root, batch_id)
        directory.mkdir(parents=True)
        (directory / fb.BATCH_FILENAME).write_text("x: 1\n", encoding="utf-8")
    assert fb.latest_batch_id(root) == "batch-02"


def test_latest_batch_id_uses_numeric_suffix(tmp_path):
    root = tmp_path / "proj"
    for batch_id in ("batch-99", "batch-100"):
        directory = fb.batch_dir(root, batch_id)
        directory.mkdir(parents=True)
        (directory / fb.BATCH_FILENAME).write_text("x: 1\n", encoding="utf-8")

    assert fb.latest_batch_id(root) == "batch-100"


def test_latest_active_batch_does_not_resurrect_older_paused_batch(tmp_path):
    root = tmp_path / "proj"
    older = fb.FeatureBatch(
        batch_id="batch-01",
        project_root=str(root),
        template="tweak",
        features=_features(2),
        waves=[["F-01", "F-02"]],
        status="paused",
    )
    newer = fb.FeatureBatch(
        batch_id="batch-02",
        project_root=str(root),
        template="tweak",
        features=_features(2),
        waves=[["F-01", "F-02"]],
        status="done",
    )
    fb.save_batch(older)
    fb.save_batch(newer)

    assert fb.latest_active_batch(root) is None


def test_latest_active_batch_ignores_malformed_latest_state(tmp_path):
    root = tmp_path / "proj"
    older = fb.FeatureBatch(
        batch_id="batch-01",
        project_root=str(root),
        template="tweak",
        features=_features(2),
        waves=[["F-01", "F-02"]],
        status="paused",
    )
    fb.save_batch(older)
    malformed = fb.batch_dir(root, "batch-02") / fb.BATCH_FILENAME
    malformed.parent.mkdir(parents=True)
    malformed.write_text(
        "batch_id: batch-02\n"
        f"project_root: {root}\n"
        "template: tweak\n"
        "status: planning\n"
        "current_wave: invalid\n"
        "waves: []\n"
        "features: []\n",
        encoding="utf-8",
    )

    assert fb.latest_active_batch(root) is None


def test_batch_fora_de_worktrees(tmp_path):
    root = tmp_path / "proj"
    home = fb.parallel_home(root)
    assert not str(home).startswith(str(paths.worktrees_root()))
    assert str(home).startswith(str(paths.runtime_home(root)))


# ---------------------------------------------------------------------------
# Orquestrador — unidades
# ---------------------------------------------------------------------------


def _batch(tmp_path, features, waves) -> fb.FeatureBatch:
    return fb.FeatureBatch(
        batch_id="batch-01",
        project_root=str(tmp_path / "proj"),
        template="feature",
        features=features,
        waves=waves,
    )


def test_tweak_parallel_disables_outer_rate_limit_respawns(tmp_path):
    tweak = _batch(tmp_path, [_feature("F-01", ["src/a/"])], [["F-01"]])
    tweak.template = "tweak"
    feature = _batch(tmp_path, [_feature("F-01", ["src/a/"])], [["F-01"]])

    assert fp._rate_limit_respawn_limit(tweak) == 0
    assert fp._rate_limit_respawn_limit(feature) == fp.MAX_RATE_LIMIT_RESPAWNS


def _repo_with_external_cycle_close(
    tmp_path: Path,
    monkeypatch,
    *,
    integrated: bool,
    worktree_exists: bool = False,
) -> tuple[Path, str]:
    """Repo real com archive commit integrado ou descartado por merge none."""
    monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
    root = tmp_path / ("integrated" if integrated else "not-integrated")
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "user.email", "test@example.com")
    (root / "README.md").write_text("base\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-qm", "base")
    base_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    cycle_name = "cycle-20-f-01-external"
    _git(root, "checkout", "-qb", cycle_name)
    (root / "product.txt").write_text("integrated feature\n", encoding="utf-8")
    _git(root, "add", "product.txt")
    _git(root, "commit", "-qm", "feat: external cycle")

    archive = root / ".ft" / "cycles" / cycle_name / "cycle.yml"
    archive.parent.mkdir(parents=True)
    archive.write_text(
        yaml.safe_dump(
            {
                "schema_version": 2,
                "id": cycle_name,
                "status": "done",
                "git": {
                    "base_commit": base_commit,
                    "worktree_branch": cycle_name,
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    archive_bytes = archive.read_bytes()
    _git(root, "add", archive.relative_to(root).as_posix())
    _git(root, "commit", "-qm", f"chore(ft): archive {cycle_name}")

    _git(root, "checkout", "-q", "main")
    if integrated:
        _git(root, "merge", "--no-ff", "--no-edit", cycle_name)
    else:
        # Simula o efeito observável de close --merge none: a branch fechada
        # não foi integrada. Mesmo que alguém copie o archive para main, ele
        # continua sem um archive commit alcançável por HEAD.
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_bytes(archive_bytes)
    _git(root, "branch", "-D", cycle_name)

    if worktree_exists:
        (paths.worktrees_home(root) / cycle_name).mkdir(parents=True)
    return root, cycle_name


def test_resume_reconcilia_ciclo_arquivado_e_integrado_e_avanca_wave(
    tmp_path, monkeypatch
):
    root, cycle_name = _repo_with_external_cycle_close(
        tmp_path, monkeypatch, integrated=True
    )
    feature = _feature("F-01", ["src/a/"])
    feature.status = "gate"
    feature.cycle_name = cycle_name
    batch = fb.FeatureBatch(
        batch_id="batch-01",
        project_root=str(root),
        template="feature",
        features=[feature],
        waves=[["F-01"]],
    )
    fb.save_batch(batch)

    fp._execute_batch(batch, Namespace(verbose=False))

    assert feature.status == "merged"
    assert batch.current_wave == 1
    assert batch.status == "done"
    assert fb.load_batch(root, batch.batch_id).feature("F-01").status == "merged"


def test_resume_nao_reconcilia_archive_sem_merge(tmp_path, monkeypatch):
    root, cycle_name = _repo_with_external_cycle_close(
        tmp_path, monkeypatch, integrated=False
    )
    feature = _feature("F-01", ["src/a/"])
    feature.status = "done"
    feature.cycle_name = cycle_name
    batch = fb.FeatureBatch(
        batch_id="batch-01",
        project_root=str(root),
        template="feature",
        features=[feature],
        waves=[["F-01"]],
    )

    assert fp._reconcile_externally_closed_cycles(batch, ["F-01"]) == []
    assert feature.status == "done"


def test_resume_nao_reconcilia_enquanto_worktree_existe(tmp_path, monkeypatch):
    root, cycle_name = _repo_with_external_cycle_close(
        tmp_path, monkeypatch, integrated=True, worktree_exists=True
    )
    feature = _feature("F-01", ["src/a/"])
    feature.status = "running"
    feature.cycle_name = cycle_name
    batch = fb.FeatureBatch(
        batch_id="batch-01",
        project_root=str(root),
        template="feature",
        features=[feature],
        waves=[["F-01"]],
    )

    assert fp._reconcile_externally_closed_cycles(batch, ["F-01"]) == []
    assert feature.status == "running"


def test_resume_preserva_estado_legado_sem_evidencia_de_close(tmp_path, monkeypatch):
    monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
    feature = _feature("F-01", ["src/a/"])
    feature.status = "gate"
    feature.cycle_name = "cycle-legacy"
    terminal = _feature("F-02", ["src/b/"])
    terminal.status = "failed"
    terminal.cycle_name = "cycle-failed"
    batch = _batch(tmp_path, [feature, terminal], [["F-01", "F-02"]])

    assert fp._reconcile_externally_closed_cycles(batch, ["F-01", "F-02"]) == []
    assert feature.status == "gate"
    assert terminal.status == "failed"


def test_collect_demands_exclusividade(tmp_path):
    args = Namespace(demand=["a"], feature_input="x.md")
    with pytest.raises(ValueError, match="não ambos"):
        fp._collect_demands(args)


def test_collect_demands_de_arquivo(tmp_path, monkeypatch):
    source = tmp_path / "demandas.md"
    source.write_text("## A\ncorpo A\n\n## B\ncorpo B\n", encoding="utf-8")
    args = Namespace(demand=[], feature_input=str(source))
    demands = fp._collect_demands(args)
    assert len(demands) == 2


def test_engine_cli_flags():
    assert fp._engine_cli_flags(None) == []
    assert fp._engine_cli_flags(fb.EngineSpec("claude")) == ["--claude"]
    assert fp._engine_cli_flags(fb.EngineSpec("codex", "gpt-5.3", "high")) == [
        "--codex",
        "gpt-5.3",
        "--effort",
        "high",
    ]


def test_worker_engine_spec_uses_batch_default_but_feature_override_wins(tmp_path):
    inherited = _feature("F-01", ["src/a/"])
    overridden = _feature("F-02", ["src/b/"])
    overridden.engine_spec = fb.EngineSpec("claude", "opus", "max")
    batch = _batch(tmp_path, [inherited, overridden], [["F-01", "F-02"]])
    batch.planner_engine = "codex"
    batch.planner_model = "gpt-5.6-sol"
    batch.planner_effort = "high"

    assert fp._worker_engine_spec(batch, inherited) == fb.EngineSpec(
        "codex", "gpt-5.6-sol", "high"
    )
    assert fp._worker_engine_spec(batch, overridden) == fb.EngineSpec(
        "claude", "opus", "max"
    )


def test_setup_feature_cycle_propagates_persisted_batch_default(tmp_path, monkeypatch):
    feature = _feature("F-01", ["src/ui/"])
    batch = _batch(tmp_path, [feature], [["F-01"]])
    batch.planner_engine = "codex"
    batch.planner_model = "gpt-5.6-sol"
    batch.planner_effort = "high"
    captured = {}
    monkeypatch.setattr(cli_main, "_next_cycle_num", lambda root: 7)
    monkeypatch.setattr(
        cli_main,
        "cmd_feature",
        lambda namespace: captured.setdefault("args", namespace),
    )

    fp._setup_feature_cycle(batch, feature, Namespace(verbose=False))

    namespace = captured["args"]
    assert namespace.codex == "gpt-5.6-sol"
    assert namespace.effort == "high"
    assert namespace.claude is None


def test_resume_worker_commands_propagate_persisted_batch_default(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
    feature = _feature("F-01", ["src/ui/"])
    feature.cycle_name = "cycle-07-f-01-ui"
    batch = _batch(tmp_path, [feature], [["F-01"]])
    batch.status = "paused"
    batch.planner_engine = "codex"
    batch.planner_model = "gpt-5.6-sol"
    batch.planner_effort = "high"
    fb.save_batch(batch)
    resumed = fb.load_batch(batch.project_root, batch.batch_id)
    resumed_feature = resumed.feature("F-01")
    spawned_commands: list[list[str]] = []

    monkeypatch.setattr(
        fp,
        "_spawn",
        lambda batch_arg, feature_arg, args, *, command: (
            spawned_commands.append(command) or _DoneProc()
        ),
    )
    monkeypatch.setattr(
        fp,
        "_cycle_state",
        lambda root, name: ("tweak.acceptance", "awaiting_approval"),
    )

    fp._spawn_continue(
        resumed,
        resumed_feature,
        Namespace(verbose=False, bypass_human_gates=False),
    )
    monkeypatch.setattr("builtins.input", lambda prompt="": "a")
    fp._handle_gate(
        resumed,
        resumed_feature,
        Namespace(verbose=False, bypass_human_gates=False),
    )
    resumed_feature.status = "blocked"
    monkeypatch.setattr("builtins.input", lambda prompt="": "r")
    fp._handle_blocked(
        resumed,
        resumed_feature,
        Namespace(verbose=False, bypass_human_gates=False),
    )

    flags = ["--codex", "gpt-5.6-sol", "--effort", "high"]
    assert spawned_commands == [
        ["continue", "--auto", "--cycle", feature.cycle_name, *flags],
        ["approve", "--auto", "--cycle", feature.cycle_name, *flags],
        ["retry", "--auto", "--cycle", feature.cycle_name, *flags],
    ]


def test_reserva_pbs_da_wave_a_partir_do_backlog_atual(tmp_path):
    features = [
        _feature("F-01", ["src/a/"]),
        _feature("F-02", ["src/b/"]),
        _feature("F-03", ["src/c/"]),
        _feature("F-04", ["src/d/"]),
    ]
    features[1].demand = "Implementar a demanda já registrada como PB-007"
    features[2].status = "setup"  # ciclo legado: não recebe reserva retroativa
    features[3].demand = "A wave futura já referencia PB-019"
    batch = _batch(tmp_path, features, [["F-01", "F-02", "F-03"], ["F-04"]])
    root = Path(batch.project_root)
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "PROJECT_BACKLOG.md").write_text(
        "# Backlog\n\n| ID | Título |\n|---|---|\n"
        "| PB-002 | antiga |\n| PB-018 | atual |\n",
        encoding="utf-8",
    )

    fp._reserve_wave_backlog_items(batch, batch.waves[0])

    # PB-019 foi explicitamente destinado à wave futura, então a alocação
    # nova desta wave o pula sem reservar prematuramente a própria F-04.
    assert features[0].reserved_backlog_item == "PB-020"
    assert features[1].reserved_backlog_item == "PB-007"
    assert features[2].reserved_backlog_item is None
    assert features[3].reserved_backlog_item is None


def test_reserva_da_wave_seguinte_rele_o_backlog_e_e_idempotente(tmp_path):
    features = [
        _feature("F-01", ["src/a/"]),
        _feature("F-02", ["src/b/"]),
        _feature("F-03", ["src/c/"]),
    ]
    batch = _batch(tmp_path, features, [["F-01", "F-02"], ["F-03"]])
    root = Path(batch.project_root)
    (root / "docs").mkdir(parents=True)
    backlog = root / "docs" / "PROJECT_BACKLOG.md"
    backlog.write_text("| PB-009 | existente |\n", encoding="utf-8")

    fp._reserve_wave_backlog_items(batch, batch.waves[0])
    assert [feature.reserved_backlog_item for feature in features] == [
        "PB-010",
        "PB-011",
        None,
    ]

    # Uma retomada da mesma wave preserva as reservas. Antes da wave seguinte,
    # um merge/push avançou o backlog e precisa definir o novo ponto de partida.
    fp._reserve_wave_backlog_items(batch, batch.waves[0])
    backlog.write_text("| PB-025 | vindo de merge/push |\n", encoding="utf-8")
    features[0].status = features[1].status = "merged"
    fp._reserve_wave_backlog_items(batch, batch.waves[1])
    assert features[2].reserved_backlog_item == "PB-026"


def test_execute_persiste_reserva_antes_do_primeiro_setup(tmp_path, monkeypatch):
    monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
    feature = _feature("F-01", ["src/a/"])
    batch = _batch(tmp_path, [feature], [["F-01"]])
    root = Path(batch.project_root)
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "PROJECT_BACKLOG.md").write_text(
        "| PB-004 | existente |\n", encoding="utf-8"
    )
    fb.save_batch(batch)

    def interrupted_setup(*args, **kwargs):
        raise RuntimeError("setup interrompido")

    monkeypatch.setattr(fp, "_setup_feature_cycle", interrupted_setup)

    with pytest.raises(RuntimeError, match="setup interrompido"):
        fp._execute_batch(batch, Namespace(verbose=False))

    persisted = fb.load_batch(root, batch.batch_id)
    assert persisted.feature("F-01").status == "planned"
    assert persisted.feature("F-01").reserved_backlog_item == "PB-005"


def test_execute_template_sem_backlog_nao_reserva_pb(tmp_path, monkeypatch):
    monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
    feature = _feature("F-01", ["src/a/"])
    batch = _batch(tmp_path, [feature], [["F-01"]])
    batch.template = "tweak"
    root = Path(batch.project_root)
    process = tmp_path / "engine" / "templates" / "tweak" / "process.yml"
    process.parent.mkdir(parents=True)
    process.write_text(
        "close_policy:\n  backlog:\n    mode: none\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cli_main, "engine_root", lambda: tmp_path / "engine")
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "PROJECT_BACKLOG.md").write_text(
        "| PB-004 | existente |\n", encoding="utf-8"
    )
    fb.save_batch(batch)

    def interrupted_setup(*args, **kwargs):
        raise RuntimeError("setup interrompido")

    monkeypatch.setattr(fp, "_setup_feature_cycle", interrupted_setup)

    with pytest.raises(RuntimeError, match="setup interrompido"):
        fp._execute_batch(batch, Namespace(verbose=False))

    persisted = fb.load_batch(root, batch.batch_id)
    assert persisted.template == "tweak"
    assert persisted.feature("F-01").reserved_backlog_item is None


def test_skip_orphans(tmp_path):
    features = [
        _feature("F-01", ["src/a/"]),
        _feature("F-02", ["src/b/"], deps=["F-01"]),
        _feature("F-03", ["src/c/"], deps=["F-02"]),
    ]
    batch = _batch(tmp_path, features, [["F-01"], ["F-02"], ["F-03"]])
    batch.feature("F-01").status = "failed"
    fb.save_batch(batch)
    fp._skip_orphans(batch, ["F-02"])
    assert batch.feature("F-02").status == "skipped"
    fp._skip_orphans(batch, ["F-03"])
    assert batch.feature("F-03").status == "skipped"


def test_close_wave_sucesso_e_falha(tmp_path, monkeypatch):
    features = [_feature("F-01", ["src/a/"]), _feature("F-02", ["src/b/"])]
    batch = _batch(tmp_path, features, [["F-01", "F-02"]])
    root = Path(batch.project_root)
    for feature, name in (
        (features[0], "cycle-02-f-01-a"),
        (features[1], "cycle-03-f-02-b"),
    ):
        feature.status = "done"
        feature.cycle_name = name
    fb.save_batch(batch)

    # F-01 fecha (worktree some); F-02 falha (worktree permanece).
    stubborn = paths.worktrees_home(root) / "cycle-03-f-02-b"
    stubborn.mkdir(parents=True)
    closed: list[str] = []

    def fake_close(namespace):
        closed.append(namespace.cycle)

    monkeypatch.setattr(cli_main, "cmd_close", fake_close)
    assert fp._close_wave(batch, Namespace(verbose=False)) is False
    assert closed == ["cycle-02-f-01-a", "cycle-03-f-02-b"]
    assert batch.feature("F-01").status == "merged"
    assert batch.feature("F-02").status == "done"
    assert "pendente" in batch.feature("F-02").detail

    # Resolvido o conflito (worktree removido), --resume fecha o restante.
    stubborn.rmdir()
    closed.clear()
    assert fp._close_wave(batch, Namespace(verbose=False)) is True
    assert closed == ["cycle-03-f-02-b"]  # merged não fecha de novo
    assert batch.feature("F-02").status == "merged"


class _DoneProc:
    """Popen fake que já terminou."""

    def __init__(self):
        self.terminated = False

    def poll(self):
        return 0

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0


def test_run_wave_conclui_quando_ciclos_terminam(tmp_path, monkeypatch):
    features = [_feature("F-01", ["src/a/"]), _feature("F-02", ["src/b/"])]
    batch = _batch(tmp_path, features, [["F-01", "F-02"]])
    for feature, name in ((features[0], "c-f01"), (features[1], "c-f02")):
        feature.status = "setup"
        feature.cycle_name = name
    fb.save_batch(batch)

    spawned: list[str] = []

    def fake_spawn_continue(batch_arg, feature, args):
        spawned.append(feature.feature_id)
        return _DoneProc()

    monkeypatch.setattr(fp, "_spawn_continue", fake_spawn_continue)
    monkeypatch.setattr(fp, "_cycle_state", lambda root, name: ("feature.end", "done"))
    monkeypatch.setattr(fp.time, "sleep", lambda seconds: None)

    fp._run_wave(batch, Namespace(verbose=False, bypass_human_gates=False))
    assert sorted(spawned) == ["F-01", "F-02"]
    assert {feature.status for feature in features} == {"done"}


def test_run_wave_gate_aprovado_inline(tmp_path, monkeypatch):
    features = [_feature("F-01", ["src/a/"])]
    features[0].status = "setup"
    features[0].cycle_name = "c-f01"
    batch = _batch(tmp_path, features, [["F-01"]])
    fb.save_batch(batch)

    states = iter(
        [
            ("feature.scope_gate", "awaiting_approval"),  # primeiro run para no gate
            ("feature.end", "done"),  # após approve, conclui
        ]
    )
    current = {"value": ("feature.scope_gate", "awaiting_approval")}

    def fake_cycle_state(root, name):
        return current["value"]

    spawned_commands: list[list[str]] = []

    def fake_spawn(batch_arg, feature, args, *, command):
        spawned_commands.append(command)
        current["value"] = next(states)
        return _DoneProc()

    monkeypatch.setattr(fp, "_spawn", fake_spawn)
    monkeypatch.setattr(fp, "_cycle_state", fake_cycle_state)
    monkeypatch.setattr(fp.time, "sleep", lambda seconds: None)
    monkeypatch.setattr("builtins.input", lambda prompt="": "a")

    fp._run_wave(batch, Namespace(verbose=False, bypass_human_gates=False))
    assert features[0].status == "done"
    # continue inicial + approve após o gate
    assert spawned_commands[0][:2] == ["continue", "--auto"]
    assert spawned_commands[1][:2] == ["approve", "--auto"]


def test_run_wave_reconcilia_gate_aprovado_externamente_durante_outro_prompt(
    tmp_path, monkeypatch
):
    features = [_feature("F-01", ["src/a/"]), _feature("F-02", ["src/b/"])]
    for feature, name in ((features[0], "c-f01"), (features[1], "c-f02")):
        feature.status = "gate"
        feature.cycle_name = name
    batch = _batch(tmp_path, features, [["F-01", "F-02"]])
    fb.save_batch(batch)

    states = {
        "c-f01": ("feature.scope_gate", "awaiting_approval"),
        "c-f02": ("feature.acceptance", "awaiting_approval"),
    }
    gate_calls: list[str] = []
    continued: list[str] = []

    def fake_handle_gate(batch_arg, feature, args):
        gate_calls.append(feature.feature_id)
        if feature.feature_id != "F-01":
            pytest.fail("F-02 não deve ser apresentada novamente como gate")

        # Enquanto o prompt de F-01 estava aberto, outro terminal executou:
        # ft approve --no-continue --cycle c-f02
        states["c-f02"] = ("feature.reconcile", "ready")
        feature.status = "running"
        states["c-f01"] = ("feature.end", "done")
        return _DoneProc()

    def fake_spawn_continue(batch_arg, feature, args):
        continued.append(feature.feature_id)
        states[str(feature.cycle_name)] = ("feature.end", "done")
        return _DoneProc()

    monkeypatch.setattr(fp, "_cycle_state", lambda root, name: states[name])
    monkeypatch.setattr(fp, "_handle_gate", fake_handle_gate)
    monkeypatch.setattr(fp, "_spawn_continue", fake_spawn_continue)
    monkeypatch.setattr(fp.time, "sleep", lambda seconds: None)

    fp._run_wave(batch, Namespace(verbose=False, bypass_human_gates=False))

    assert gate_calls == ["F-01"]
    assert continued == ["F-02"]
    assert {feature.status for feature in features} == {"done"}


def test_run_wave_resume_reconcilia_running_sem_child_com_gate_real(
    tmp_path, monkeypatch
):
    feature = _feature("F-01", ["src/a/"])
    feature.status = "running"  # persistido pelo batch antes da pausa
    feature.cycle_name = "c-f01"
    batch = _batch(tmp_path, [feature], [["F-01"]])
    fb.save_batch(batch)

    current = {"value": ("feature.scope_gate", "awaiting_approval")}
    gate_calls: list[str] = []
    sleep_calls = 0

    def fake_handle_gate(batch_arg, feature_arg, args):
        gate_calls.append(feature_arg.feature_id)
        feature_arg.status = "running"
        current["value"] = ("feature.end", "done")
        return _DoneProc()

    def bounded_sleep(seconds):
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls > 1:
            pytest.fail("resume entrou em polling sem subprocesso")

    monkeypatch.setattr(fp, "_cycle_state", lambda root, name: current["value"])
    monkeypatch.setattr(fp, "_handle_gate", fake_handle_gate)
    monkeypatch.setattr(fp.time, "sleep", bounded_sleep)

    fp._run_wave(batch, Namespace(verbose=False, bypass_human_gates=False))

    assert gate_calls == ["F-01"]
    assert feature.status == "done"


def test_run_wave_nao_reconcilia_running_enquanto_child_local_esta_ativo(
    tmp_path, monkeypatch
):
    feature = _feature("F-01", ["src/a/"])
    feature.status = "setup"
    feature.cycle_name = "c-f01"
    batch = _batch(tmp_path, [feature], [["F-01"]])
    fb.save_batch(batch)

    current = {"value": ("feature.start", "ready")}
    gate_calls: list[str] = []

    class _ActiveOnceProc(_DoneProc):
        def __init__(self):
            super().__init__()
            self.poll_calls = 0

        def poll(self):
            self.poll_calls += 1
            return None if self.poll_calls == 1 else 0

    active = _ActiveOnceProc()

    def fake_spawn_continue(batch_arg, feature_arg, args):
        current["value"] = ("feature.scope_gate", "awaiting_approval")
        return active

    def fake_handle_gate(batch_arg, feature_arg, args):
        assert active.poll_calls == 2  # somente após o child terminar
        gate_calls.append(feature_arg.feature_id)
        feature_arg.status = "running"
        current["value"] = ("feature.end", "done")
        return _DoneProc()

    monkeypatch.setattr(fp, "_cycle_state", lambda root, name: current["value"])
    monkeypatch.setattr(fp, "_spawn_continue", fake_spawn_continue)
    monkeypatch.setattr(fp, "_handle_gate", fake_handle_gate)
    monkeypatch.setattr(fp.time, "sleep", lambda seconds: None)

    fp._run_wave(batch, Namespace(verbose=False, bypass_human_gates=False))

    assert gate_calls == ["F-01"]
    assert feature.status == "done"


def test_questions_gate_exige_mensagem_e_encaminha_no_approve(tmp_path, monkeypatch):
    feature = _feature("F-01", ["src/a/"])
    feature.status = "gate"
    feature.cycle_name = "c-f01"
    batch = _batch(tmp_path, [feature], [["F-01"]])
    spawned_commands: list[list[str]] = []
    answers = iter(["a", "", "a", "  1. streaming; 2. histórico na sessão  "])

    monkeypatch.setattr(
        fp,
        "_cycle_state",
        lambda root, name: ("feature.questions", "awaiting_approval"),
    )
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    monkeypatch.setattr(
        fp,
        "_spawn",
        lambda batch_arg, feature_arg, args, *, command: (
            spawned_commands.append(command) or _DoneProc()
        ),
    )

    proc = fp._handle_gate(
        batch, feature, Namespace(verbose=False, bypass_human_gates=False)
    )

    assert isinstance(proc, _DoneProc)
    assert spawned_commands == [
        [
            "approve",
            "1. streaming; 2. histórico na sessão",
            "--auto",
            "--cycle",
            "c-f01",
        ]
    ]
    assert feature.status == "running"


def test_questions_gate_cancelado_nao_avanca(tmp_path, monkeypatch):
    feature = _feature("F-01", ["src/a/"])
    feature.status = "gate"
    feature.cycle_name = "c-f01"
    batch = _batch(tmp_path, [feature], [["F-01"]])
    calls = iter(["a", KeyboardInterrupt(), "d"])
    spawned_commands: list[list[str]] = []

    def fake_input(prompt=""):
        value = next(calls)
        if isinstance(value, BaseException):
            raise value
        return value

    monkeypatch.setattr(
        fp,
        "_cycle_state",
        lambda root, name: ("feature.questions", "awaiting_approval"),
    )
    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr(
        fp,
        "_spawn",
        lambda batch_arg, feature_arg, args, *, command: (
            spawned_commands.append(command) or _DoneProc()
        ),
    )

    assert (
        fp._handle_gate(
            batch, feature, Namespace(verbose=False, bypass_human_gates=False)
        )
        is None
    )
    assert spawned_commands == []
    assert feature.status == "gate"


def test_run_wave_pausa_termina_subprocessos(tmp_path, monkeypatch):
    features = [_feature("F-01", ["src/a/"])]
    features[0].status = "blocked"
    features[0].cycle_name = "c-f01"
    batch = _batch(tmp_path, features, [["F-01"]])
    fb.save_batch(batch)

    monkeypatch.setattr(fp.time, "sleep", lambda seconds: None)
    monkeypatch.setattr("builtins.input", lambda prompt="": "p")

    with pytest.raises(fp._PauseBatch):
        fp._run_wave(batch, Namespace(verbose=False, bypass_human_gates=False))


# ---------------------------------------------------------------------------
# Integração CLI
# ---------------------------------------------------------------------------


def test_cmd_feature_multiplas_demandas_sem_parallel():
    args = Namespace(
        command="feature",
        process=None,
        demand=["a", "b"],
        feature_input=None,
        parallel=False,
        resume=None,
        template="feature",
    )
    with pytest.raises(ValueError, match="--parallel"):
        cli_main.cmd_feature(args)


def test_cmd_feature_parallel_roteia_para_orquestrador(monkeypatch):
    called = {}

    def fake_run(args):
        called["args"] = args

    monkeypatch.setattr("ft.cli.feature_parallel.run_parallel_batch", fake_run)
    args = Namespace(command="feature", process=None, parallel=True, resume=None)
    cli_main.cmd_feature(args)
    assert called["args"] is args


def test_setup_parallel_propaga_template_tweak_sem_orquestrador_alternativo(
    tmp_path, monkeypatch
):
    feature = _feature("F-01", ["src/ui/"])
    batch = _batch(tmp_path, [feature], [["F-01"]])
    batch.template = "tweak"
    captured = {}
    monkeypatch.setattr(cli_main, "_next_cycle_num", lambda root: 7)
    monkeypatch.setattr(
        cli_main,
        "cmd_feature",
        lambda namespace: captured.setdefault("args", namespace),
    )

    fp._setup_feature_cycle(batch, feature, Namespace(verbose=False))

    namespace = captured["args"]
    assert namespace.template == "tweak"
    assert namespace.force is True
    assert namespace._setup_only is True
    assert feature.status == "setup"
    assert feature.cycle_name.startswith("cycle-07-f-01-")


def test_resume_preserva_template_do_batch_e_rejeita_troca(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    batch = fb.FeatureBatch(
        batch_id="batch-01",
        project_root=str(root),
        template="tweak",
        features=[_feature("F-01", ["src/a/"])],
        waves=[["F-01"]],
        status="paused",
    )
    fb.save_batch(batch)
    monkeypatch.setattr(cli_main, "find_project_root", lambda: root)
    monkeypatch.setattr(fp.sys.stdout, "reconfigure", lambda **kwargs: None)

    with pytest.raises(ValueError, match="usa o template 'tweak'"):
        fp.run_parallel_batch(Namespace(resume="batch-01", template="feature"))

    captured = {}
    monkeypatch.setattr(
        fp,
        "_execute_batch",
        lambda loaded, args: captured.setdefault("template", loaded.template),
    )
    fp.run_parallel_batch(Namespace(resume="batch-01", template=None))
    assert captured["template"] == "tweak"


@pytest.mark.parametrize("status", ["planning", "failed", "invalid"])
def test_resume_rejects_non_executable_batch_states(
    tmp_path, monkeypatch, status, capsys
):
    root = tmp_path / "proj"
    root.mkdir()
    batch = fb.FeatureBatch(
        batch_id="batch-01",
        project_root=str(root),
        template="tweak",
        features=[_feature("F-01", ["src/a/"])],
        waves=[],
        status=status,
    )
    fb.save_batch(batch)
    monkeypatch.setattr(cli_main, "find_project_root", lambda: root)
    monkeypatch.setattr(fp.sys.stdout, "reconfigure", lambda **kwargs: None)
    monkeypatch.setattr(
        fp,
        "_execute_batch",
        lambda *args, **kwargs: pytest.fail("batch inválido foi executado"),
    )

    with pytest.raises(SystemExit) as exit_info:
        fp.run_parallel_batch(Namespace(resume="batch-01", template=None))

    assert exit_info.value.code == 1
    assert "não pode ser retomado" in capsys.readouterr().out
    assert fb.load_batch(root, "batch-01").status == status


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", *arguments], cwd=root, check=True, capture_output=True, text=True
    )


def _git_project(tmp_path) -> Path:
    root = tmp_path / "proj"
    root.mkdir(parents=True)
    cli_main.copy_template("base", root)
    (root / "docs").mkdir(exist_ok=True)
    (root / "src").mkdir(exist_ok=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    return root


def test_setup_feature_cycle_cria_ciclo_sem_executar(tmp_path, monkeypatch):
    root = _git_project(tmp_path)
    monkeypatch.chdir(root)

    features = fb.build_features([("Busca por telefone", None), ("Dark mode", None)])
    features[0].engine_spec = fb.EngineSpec("codex", "gpt-5.3")
    features[0].reserved_backlog_item = "PB-019"
    batch = fb.FeatureBatch(
        batch_id="batch-01",
        project_root=str(root),
        template="feature",
        features=features,
        waves=[["F-01", "F-02"]],
    )

    fp._setup_feature_cycle(batch, features[0], Namespace(verbose=False))

    assert features[0].status == "setup"
    cycle_name = features[0].cycle_name
    assert cycle_name and "f-01" in cycle_name
    worktree = paths.worktrees_home(root) / cycle_name
    state_file = worktree / "state" / "engine_state.yml"
    assert state_file.is_file()
    state = yaml.safe_load(state_file.read_text(encoding="utf-8"))
    # Ciclo preparado no primeiro node, sem nenhum step executado.
    assert state["current_node"]
    assert not state.get("completed_nodes")
    assert state["_lock"] is None
    env = os.environ.copy()
    package_root = str(Path(cli_main.__file__).resolve().parents[2])
    env["PYTHONPATH"] = package_root + os.pathsep + env.get("PYTHONPATH", "")
    lock_probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from ft.engine.state import StateManager; "
            "StateManager(sys.argv[1]).load(check_lock=True)",
            str(state_file),
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
    )
    assert lock_probe.returncode == 0, lock_probe.stderr
    assert (worktree / "docs" / "feature-request.md").read_text(encoding="utf-8") == (
        "---\nreserved_backlog_item: PB-019\n---\n\nBusca por telefone\n"
    )
    assert features[0].demand == "Busca por telefone"

    # Segundo setup coexiste com o primeiro (force interno do batch).
    fp._setup_feature_cycle(batch, features[1], Namespace(verbose=False))
    assert features[1].cycle_name != features[0].cycle_name
    second_worktree = paths.worktrees_home(root) / features[1].cycle_name
    assert second_worktree.is_dir()
    second_state = yaml.safe_load(
        (second_worktree / "state" / "engine_state.yml").read_text(encoding="utf-8")
    )
    assert second_state["_lock"] is None
    assert (second_worktree / "docs" / "feature-request.md").read_text(
        encoding="utf-8"
    ) == "Dark mode\n"


def test_primeiro_batch_planeja_sem_sujar_checkout_e_materializa_no_setup(
    tmp_path,
    monkeypatch,
):
    ft_home = tmp_path / "ft-home"
    monkeypatch.setenv("FT_HOME", str(ft_home))
    root = _git_project(tmp_path)
    monkeypatch.chdir(root)

    plan = {
        "schema_version": fb.PLAN_SCHEMA_VERSION,
        "features": [
            {"id": "F-01", "areas": ["src/search/"], "depends_on": []},
            {"id": "F-02", "areas": ["src/theme/"], "depends_on": []},
        ],
    }
    monkeypatch.setattr(fp, "_run_planner", lambda *args, **kwargs: plan)
    args = Namespace(
        demand=["Busca por telefone", "Dark mode"],
        feature_input=None,
        engines=None,
        template="feature",
        max_parallel=2,
        yes=True,
        force=False,
        verbose=False,
        bypass_human_gates=False,
        claude=None,
        codex=None,
        gemini=None,
        opencode=None,
        effort=None,
    )

    batch = fp._plan_batch(args, root)

    assert batch is not None
    assert not paths.project_named_process_file(root, "feature").exists()
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    assert status.stdout == ""

    first = batch.feature("F-01")
    fp._setup_feature_cycle(batch, first, args)

    assert first.status == "setup"
    assert paths.project_named_process_file(root, "feature").is_file()
    assert first.cycle_name is not None
    worktree = paths.worktrees_home(root) / first.cycle_name
    assert paths.project_named_process_file(worktree, "feature").is_file()
    assert (
        subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        == ""
    )


def test_plan_batch_applies_short_planner_budget_only_to_tweak(tmp_path, monkeypatch):
    monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
    root = _git_project(tmp_path)
    monkeypatch.chdir(root)
    captured: dict[str, object] = {}
    plan = {
        "schema_version": fb.PLAN_SCHEMA_VERSION,
        "features": [
            {"id": "F-01", "areas": ["src/a/"], "depends_on": []},
            {"id": "F-02", "areas": ["src/b/"], "depends_on": []},
        ],
    }

    def fake_planner(*args, **kwargs):
        captured.update(kwargs)
        return plan

    monkeypatch.setattr(fp, "_run_planner", fake_planner)
    args = Namespace(
        demand=["Mude o botão A para azul", "Mude o botão B para verde"],
        feature_input=None,
        engines=None,
        template="tweak",
        max_parallel=2,
        yes=True,
        force=False,
        verbose=False,
        bypass_human_gates=False,
        claude=None,
        codex=None,
        gemini=None,
        opencode=None,
        effort=None,
    )

    batch = fp._plan_batch(args, root)

    assert batch is not None
    assert batch.template == "tweak"
    assert captured["llm_timeout_seconds"] == fp.TWEAK_PLANNER_TIMEOUT_SECONDS


def test_plan_batch_persists_planning_status_before_llm_returns(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
    root = _git_project(tmp_path)
    monkeypatch.chdir(root)
    observed: dict[str, object] = {}
    plan = {
        "schema_version": fb.PLAN_SCHEMA_VERSION,
        "features": [
            {"id": "F-01", "areas": ["src/a/"], "depends_on": []},
            {"id": "F-02", "areas": ["src/b/"], "depends_on": []},
        ],
    }

    def fake_planner(*args, **kwargs):
        batch_id = fb.latest_batch_id(root)
        assert batch_id is not None
        persisted = fb.load_batch(root, batch_id)
        observed.update(persisted.to_dict())
        return plan

    monkeypatch.setattr(fp, "_run_planner", fake_planner)
    args = Namespace(
        demand=["Mude o botão A para azul", "Mude o botão B para verde"],
        feature_input=None,
        engines=None,
        template="tweak",
        max_parallel=2,
        yes=True,
        force=False,
        verbose=False,
        bypass_human_gates=False,
        claude=None,
        codex="gpt-5.6-sol",
        gemini=None,
        opencode=None,
        effort="high",
    )

    batch = fp._plan_batch(args, root)

    assert batch is not None
    assert observed["status"] == "planning"
    assert observed["template"] == "tweak"
    assert observed["planner_engine"] == "codex"
    assert observed["planner_model"] == "gpt-5.6-sol"
    assert observed["planner_effort"] == "high"
    assert len(observed["features"]) == 2
    assert observed["waves"] == []
    assert batch.status == "planned"


def test_plan_batch_rejects_active_batch_but_force_allocates_new_id(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
    root = _git_project(tmp_path)
    monkeypatch.chdir(root)
    existing = fb.FeatureBatch(
        batch_id="batch-01",
        project_root=str(root),
        template="tweak",
        features=_features(2),
        waves=[],
        status="planning",
    )
    fb.save_batch(existing)
    plan = {
        "schema_version": fb.PLAN_SCHEMA_VERSION,
        "features": [
            {"id": "F-01", "areas": ["src/a/"], "depends_on": []},
            {"id": "F-02", "areas": ["src/b/"], "depends_on": []},
        ],
    }
    monkeypatch.setattr(fp, "_run_planner", lambda *args, **kwargs: plan)
    args = Namespace(
        demand=["Mude o botão A para azul", "Mude o botão B para verde"],
        feature_input=None,
        engines=None,
        template="tweak",
        max_parallel=2,
        yes=True,
        force=False,
        verbose=False,
        bypass_human_gates=False,
        claude=None,
        codex="gpt-5.6-sol",
        gemini=None,
        opencode=None,
        effort="high",
    )

    with pytest.raises(RuntimeError, match="batch paralelo ativo: batch-01"):
        fp._plan_batch(args, root)

    args.force = True
    created = fp._plan_batch(args, root)

    assert created is not None
    assert created.batch_id == "batch-02"
    assert fb.load_batch(root, "batch-01").status == "planning"


@pytest.mark.parametrize(
    ("explicit_engine", "expected_model", "expected_effort"),
    [("codex", None, None), ("claude", "opus", "max")],
)
def test_plan_batch_only_inherits_manifest_defaults_for_compatible_engine(
    tmp_path, monkeypatch, explicit_engine, expected_model, expected_effort
):
    monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
    root = _git_project(tmp_path)
    monkeypatch.chdir(root)
    monkeypatch.setattr(
        cli_main,
        "manifest_llm_defaults",
        lambda project_root: ("claude", "opus", "max"),
    )
    plan = {
        "schema_version": fb.PLAN_SCHEMA_VERSION,
        "features": [
            {"id": "F-01", "areas": ["src/a/"], "depends_on": []},
            {"id": "F-02", "areas": ["src/b/"], "depends_on": []},
        ],
    }
    monkeypatch.setattr(fp, "_run_planner", lambda *args, **kwargs: plan)
    engine_args = {"claude": None, "codex": None, "gemini": None, "opencode": None}
    engine_args[explicit_engine] = True
    args = Namespace(
        demand=["Mude o botão A para azul", "Mude o botão B para verde"],
        feature_input=None,
        engines=None,
        template="tweak",
        max_parallel=2,
        yes=True,
        force=False,
        verbose=False,
        bypass_human_gates=False,
        effort=None,
        **engine_args,
    )

    batch = fp._plan_batch(args, root)

    assert batch is not None
    assert batch.planner_engine == explicit_engine
    assert batch.planner_model == expected_model
    assert batch.planner_effort == expected_effort


@pytest.mark.parametrize("planner_error", [RuntimeError("boom"), KeyboardInterrupt()])
def test_plan_batch_marks_persisted_batch_failed_when_planner_stops(
    tmp_path, monkeypatch, planner_error
):
    monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
    root = _git_project(tmp_path)
    monkeypatch.chdir(root)

    def fail_planner(*args, **kwargs):
        raise planner_error

    monkeypatch.setattr(fp, "_run_planner", fail_planner)
    args = Namespace(
        demand=["Mude o botão A para azul", "Mude o botão B para verde"],
        feature_input=None,
        engines=None,
        template="tweak",
        max_parallel=2,
        yes=True,
        force=False,
        verbose=False,
        bypass_human_gates=False,
        claude=None,
        codex="gpt-5.6-sol",
        gemini=None,
        opencode=None,
        effort="high",
    )

    with pytest.raises(type(planner_error)):
        fp._plan_batch(args, root)

    batch_id = fb.latest_batch_id(root)
    assert batch_id is not None
    assert fb.load_batch(root, batch_id).status == "failed"
