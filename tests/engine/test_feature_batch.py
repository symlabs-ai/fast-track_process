"""Testes do ft feature --parallel (feature_batch + orquestrador)."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path
import subprocess

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
    assert (worktree / "docs" / "feature-request.md").read_text(encoding="utf-8") == (
        "---\nreserved_backlog_item: PB-019\n---\n\nBusca por telefone\n"
    )
    assert features[0].demand == "Busca por telefone"

    # Segundo setup coexiste com o primeiro (force interno do batch).
    fp._setup_feature_cycle(batch, features[1], Namespace(verbose=False))
    assert features[1].cycle_name != features[0].cycle_name
    second_worktree = paths.worktrees_home(root) / features[1].cycle_name
    assert second_worktree.is_dir()
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
