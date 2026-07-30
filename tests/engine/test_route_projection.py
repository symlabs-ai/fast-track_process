"""Regressões para contexto e progresso limitados à rota selecionada."""

from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest
import yaml

from ft.engine.runner import StepRunner
from ft.engine.state import StateManager


PROCESS = """\
id: route_projection
version: "1.0.0"
title: Route projection
session_policy:
  mode: sprint
  providers: [codex]
  initial_plan: internal
  parallel_strategy: fork
  recovery: rehydrate
batch_policy: {}
nodes:
  - id: route
    type: decision
    title: Selecionar rota
    executor: python
    condition: run_route
    branches:
      "validation": validation.plan
      "_default": discovery.restart
  - id: validation.plan
    type: document
    title: Planejar delta
    executor: codex
    outputs: [docs/plan.md]
    next: validation.review
  - id: validation.review
    type: review
    title: Auditar delta
    executor: codex
    outputs: [docs/review.md]
    on_fail:
      goto: validation.fix
    next: validation.verify
  - id: validation.fix
    type: build
    title: Corrigir delta
    executor: codex
    outputs: [project/]
    next: validation.review
  - id: validation.verify
    type: gate
    title: Regressão integrada
    executor: python
    next: validation.accept
  - id: validation.accept
    type: human_gate
    title: Aceite
    executor: python
    reject_next: validation.fix
    next: end
  - id: discovery.restart
    type: discovery
    title: Recomeçar discovery
    executor: codex
    outputs: [docs/discovery.md]
    next: delivery.full
  - id: delivery.full
    type: build
    title: Executar entrega completa
    executor: codex
    outputs: [project/]
    next: end
  - id: end
    type: end
    title: Fim
"""


def _runner(tmp_path: Path) -> StepRunner:
    root = tmp_path / "project"
    root.mkdir()
    process = tmp_path / "process.yml"
    process.write_text(PROCESS, encoding="utf-8")
    runner = StepRunner(
        process_path=process,
        state_path=tmp_path / "state" / "engine_state.yml",
        project_root=root,
        llm_engine="codex",
    )
    runner.init_state(run_route="validation", parallel_enabled=True)
    return runner


def test_init_banner_and_metrics_use_explicit_route(
    tmp_path: Path,
    capsys,
) -> None:
    root = tmp_path / "banner-project"
    root.mkdir()
    process = tmp_path / "banner-process.yml"
    process.write_text(PROCESS, encoding="utf-8")
    runner = StepRunner(
        process_path=process,
        state_path=tmp_path / "banner-state" / "engine_state.yml",
        project_root=root,
        llm_engine="codex",
    )

    runner.init_state(
        run_route="validation",
        parallel_enabled=True,
        parallel_max_slots=4,
    )

    state = runner.state_mgr.load()
    assert state.metrics["steps_total"] == 6
    assert state.run_route == "validation"
    assert state.parallel_enabled is True
    assert state.parallel_max_slots == 4
    assert "Total: 6 steps" in capsys.readouterr().out


def test_parallel_does_not_select_validation_route(tmp_path: Path) -> None:
    root = tmp_path / "default-project"
    root.mkdir()
    process = tmp_path / "default-process.yml"
    process.write_text(PROCESS, encoding="utf-8")
    runner = StepRunner(
        process_path=process,
        state_path=tmp_path / "default-state" / "engine_state.yml",
        project_root=root,
        llm_engine="codex",
    )

    runner.init_state(parallel_enabled=True, parallel_max_slots=4)

    state = runner.state_mgr.load()
    assert state.run_route == "default"
    assert state.parallel_enabled is True
    assert runner._selected_route_node_ids(state) == [
        "route",
        "discovery.restart",
        "delivery.full",
        "end",
    ]
    assert state.metrics["steps_total"] == 3


def test_unknown_explicit_route_is_rejected_before_state_init(tmp_path: Path) -> None:
    root = tmp_path / "invalid-project"
    root.mkdir()
    process = tmp_path / "invalid-process.yml"
    process.write_text(PROCESS, encoding="utf-8")
    state_path = tmp_path / "invalid-state" / "engine_state.yml"
    runner = StepRunner(
        process_path=process,
        state_path=state_path,
        project_root=root,
        llm_engine="codex",
    )

    with pytest.raises(ValueError, match="rotas disponíveis: validation"):
        runner.init_state(run_route="unknown")

    assert not state_path.exists()


def test_plan_and_progress_include_only_selected_route(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    state = runner.state_mgr.load()

    # init_state já projetou a rota e persistiu as métricas corretas.
    assert not runner._refresh_progress_metrics(state)
    plan = runner._deterministic_execution_plan(state)

    assert plan["selected_route"] == [
        "route",
        "validation.plan",
        "validation.review",
        "validation.fix",
        "validation.verify",
        "validation.accept",
        "end",
    ]
    assert state.metrics["steps_total"] == 6
    serialized = yaml.safe_dump(plan, allow_unicode=True, sort_keys=False)
    assert "discovery.restart" not in serialized
    assert "delivery.full" not in serialized


def test_skipped_branch_does_not_inflate_completed_progress(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    state = runner.state_mgr.load()
    state.completed_nodes = ["route", "discovery.restart", "validation.plan"]
    state.gate_log = {
        "route": "PASS",
        "discovery.restart": "SKIPPED",
        "validation.plan": "PASS",
    }

    runner._refresh_progress_metrics(state)

    assert state.metrics["steps_completed"] == 2
    assert state.metrics["steps_total"] == 6


def test_legacy_full_graph_plan_is_replaced_by_route_plan(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    plan_path = runner.state_mgr.path.parent / "llm_execution_plan.yml"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(
        "schema_version: 1\nsprints:\n  - id: all\n"
        "    nodes:\n      - id: discovery.restart\n",
        encoding="utf-8",
    )
    state = runner.state_mgr.load()
    state.llm_execution_plan = {
        "path": "llm_execution_plan.yml",
        "source": "legacy-full-graph",
    }
    runner.state_mgr.save()

    runner._ensure_internal_execution_plan(state)

    regenerated = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
    assert regenerated["selected_route"] == [
        "route",
        "validation.plan",
        "validation.review",
        "validation.fix",
        "validation.verify",
        "validation.accept",
        "end",
    ]
    assert "discovery.restart" not in plan_path.read_text(encoding="utf-8")
    assert state.llm_execution_plan["source"] == "deterministic-batch"


def test_decision_choice_survives_condition_change(tmp_path: Path) -> None:
    process = tmp_path / "file-route.yml"
    process.write_text(
        """\
id: stable_route
version: "1.0.0"
title: Stable route
nodes:
  - id: route
    type: decision
    title: Arquivo existe?
    executor: python
    condition: file_exists:marker.txt
    branches:
      "true": existing
      "false": create
  - id: create
    type: build
    title: Criar
    executor: codex
    outputs: [marker.txt]
    next: end
  - id: existing
    type: gate
    title: Existente
    executor: python
    next: end
  - id: end
    type: end
    title: Fim
""",
        encoding="utf-8",
    )
    root = tmp_path / "stable-project"
    root.mkdir()
    runner = StepRunner(
        process_path=process,
        state_path=tmp_path / "stable-state" / "engine_state.yml",
        project_root=root,
    )
    runner.init_state()

    runner._run_decision(runner.graph.get_node("route"))
    state = runner.state_mgr.load()
    assert state.route_choices == {"route": "create"}

    (root / "marker.txt").write_text("agora existe\n", encoding="utf-8")

    assert runner._selected_route_node_ids(state) == ["route", "create", "end"]


def test_route_choices_round_trip_in_state(tmp_path: Path) -> None:
    manager = StateManager(tmp_path / "engine_state.yml")
    state = manager.load()
    state.route_choices = {"route": "validation.plan"}
    manager.save()

    reloaded = StateManager(manager.path).load()

    assert reloaded.route_choices == {"route": "validation.plan"}


def test_build_checkpoint_requires_explicit_allow_pre_seed(tmp_path: Path) -> None:
    process = tmp_path / "checkpoint.yml"
    process.write_text(
        """\
id: checkpoint
version: "1.0.0"
title: Checkpoint
nodes:
  - id: foundation
    type: build
    title: Foundation
    executor: codex
    allow_pre_seed: true
    outputs: [foundation.ok]
    validators:
      - file_exists: foundation.ok
    next: end
  - id: end
    type: end
    title: Fim
""",
        encoding="utf-8",
    )
    root = tmp_path / "checkpoint-project"
    root.mkdir()
    (root / "foundation.ok").write_text("validado\n", encoding="utf-8")
    subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "checkpoint@example.test"],
        cwd=root,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Checkpoint Test"],
        cwd=root,
        check=True,
    )
    subprocess.run(["git", "add", "foundation.ok"], cwd=root, check=True)
    subprocess.run(
        ["git", "commit", "-m", "baseline"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    # Simula o input copiado pelo `ft run` depois que a worktree nasceu.
    (root / "request.md").write_text("delta autorizado\n", encoding="utf-8")
    runner = StepRunner(
        process_path=process,
        state_path=tmp_path / "checkpoint-state" / "engine_state.yml",
        project_root=root,
        llm_engine="codex",
    )
    runner.init_state()

    with (
        patch.object(
            runner,
            "_delegate_with_stream_retry",
            side_effect=AssertionError("checkpoint válido não deve chamar LLM"),
        ),
        patch(
            "ft.engine.runner.commit_knowledge",
            return_value=(True, "commit final desativado no teste"),
        ),
    ):
        runner.run(mode="mvp")

    state = runner.state_mgr.load()
    assert state.node_status == "done"
    assert state.metrics["llm_calls"] == 0
    assert state.gate_log["foundation"] == "PASS"
    assert subprocess.run(
        ["git", "status", "--porcelain", "--", "request.md", "foundation.ok"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout == ""
    assert "Foundation [foundation]" in subprocess.run(
        ["git", "log", "-1", "--pretty=%s"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
