"""Testes do paralelismo intra-processo (ft run/continue --parallel).

Cobre: persistência da flag no estado, gating do fan-out no runner,
fan-in determinístico com merge por branch e as regras de validação
de parallel_group no process_validator.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import yaml

from ft.cli.main import apply_parallel_flags
from ft.engine.graph import Node, load_graph
from ft.engine.layout import ensure_project_layout, register_project_process
from ft.engine.parallel import WorktreeResult
from ft.engine.process_validator import validate_process
from ft.engine.runner import StepRunner, ValidationResult
from ft.engine.state import StateManager
from ft.engine.trace import build_run_report


_PARALLEL_PROCESS = """\
id: parallel_proc
version: "1.0.0"
title: Parallel process
nodes:
  - id: par-a
    type: document
    title: Par A
    executor: llm_coder
    parallel_group: docs
    outputs:
      - docs/a.md
    next: par-b
  - id: par-b
    type: document
    title: Par B
    executor: llm_coder
    parallel_group: docs
    outputs:
      - docs/b.md
    next: end
  - id: end
    type: end
    title: End
"""


def _runner(tmp_path: Path) -> StepRunner:
    root = tmp_path / "proj"
    root.mkdir()
    ensure_project_layout(
        root,
        defaults={
            "llm_engine": "codex",
            "llm_model": "gpt-old",
            "llm_effort": "high",
        },
    )
    process = root / ".ft" / "process" / "test" / "process.yml"
    process.parent.mkdir(parents=True, exist_ok=True)
    process.write_text(_PARALLEL_PROCESS, encoding="utf-8")
    register_project_process(
        root,
        process_name="test",
        process_path=process,
        template_id="test",
        entrypoint="test",
        set_default=True,
    )
    runner = StepRunner(
        process_path=process,
        state_path=root / "state" / "engine_state.yml",
        project_root=root,
    )
    runner.init_state()
    return runner


# ---------------------------------------------------------------------------
# Estado
# ---------------------------------------------------------------------------


def test_state_parallel_fields_roundtrip(tmp_path: Path) -> None:
    mgr = StateManager(tmp_path / "engine_state.yml")
    state = mgr.load()
    assert state.parallel_enabled is False
    assert state.parallel_max_slots == 2

    state.parallel_enabled = True
    state.parallel_max_slots = 4
    mgr.save()

    reloaded = StateManager(tmp_path / "engine_state.yml").load()
    assert reloaded.parallel_enabled is True
    assert reloaded.parallel_max_slots == 4


def test_state_without_parallel_fields_defaults_to_disabled(tmp_path: Path) -> None:
    path = tmp_path / "engine_state.yml"
    path.write_text(
        yaml.dump({"process_id": "legacy", "current_node": "par-a"}),
        encoding="utf-8",
    )
    state = StateManager(path).load()
    assert state.parallel_enabled is False
    assert state.parallel_max_slots == 2


def test_apply_parallel_flags_persists_and_disables(tmp_path: Path) -> None:
    runner = _runner(tmp_path)

    apply_parallel_flags(
        runner, SimpleNamespace(parallel=True, no_parallel=False, max_parallel=3)
    )
    state = runner.state_mgr.load()
    assert state.parallel_enabled is True
    assert state.parallel_max_slots == 3

    apply_parallel_flags(
        runner, SimpleNamespace(parallel=False, no_parallel=True, max_parallel=None)
    )
    state = runner.state_mgr.load()
    assert state.parallel_enabled is False
    assert state.parallel_max_slots == 3


def test_apply_parallel_flags_noop_without_flags(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    before = runner.state_mgr.path.read_text(encoding="utf-8")
    apply_parallel_flags(
        runner, SimpleNamespace(parallel=False, no_parallel=False, max_parallel=None)
    )
    assert runner.state_mgr.path.read_text(encoding="utf-8") == before


# ---------------------------------------------------------------------------
# Runner — gating do fan-out
# ---------------------------------------------------------------------------


def _passing_validation() -> ValidationResult:
    return ValidationResult(passed=True, retryable=False, feedback=None)


def test_parallel_group_runs_sequentially_without_flag(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    delegated: list[str] = []

    def fake_llm_step(node):
        delegated.append(node.id)
        next_id = runner.graph.resolve_next(node.id)
        runner._advance_state(node.id, next_id)

    with (
        patch.object(runner, "_run_llm_step", side_effect=fake_llm_step),
        patch.object(runner, "_run_parallel_group") as fan_out,
    ):
        runner.run(mode="mvp")

    fan_out.assert_not_called()
    assert delegated == ["par-a", "par-b"]


def test_parallel_group_fans_out_with_flag(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    state = runner.state_mgr.load()
    state.parallel_enabled = True
    runner.state_mgr.save()

    def fake_group(nodes):
        for n in nodes:
            runner._advance_state(n.id, runner.graph.resolve_next(n.id))

    with (
        patch.object(runner, "_run_llm_step") as llm_step,
        patch.object(runner, "_run_parallel_group", side_effect=fake_group) as fan_out,
    ):
        runner.run(mode="mvp")

    llm_step.assert_not_called()
    fan_out.assert_called_once()
    assert [n.id for n in fan_out.call_args.args[0]] == ["par-a", "par-b"]


def test_fan_in_uses_group_order_even_if_threads_finish_out_of_order(
    tmp_path: Path,
) -> None:
    runner = _runner(tmp_path)
    state = runner.state_mgr.load()
    state.parallel_enabled = True
    state.parallel_max_slots = 4
    runner.state_mgr.save()

    nodes = [runner.graph.get_node("par-a"), runner.graph.get_node("par-b")]
    merged: list[str] = []

    class FakeParallelRunner:
        def __init__(self, project_root, max_slots=2):
            assert max_slots == 4

        def run_parallel(self, tasks, delegate_fn):
            # Ordem invertida simula par-b terminando antes de par-a.
            return [
                WorktreeResult(
                    node_id=task["node_id"],
                    branch=f"ft-parallel/{task['node_id']}",
                    worktree_path="",
                    success=True,
                    output="DONE",
                )
                for task in reversed(tasks)
            ]

        def merge_all(self, results):
            merged.extend(r.node_id for r in results)
            return [(r.node_id, True, "merge OK") for r in results]

    with (
        patch("ft.engine.runner.ParallelRunner", FakeParallelRunner),
        patch("ft.engine.runner.run_validators", return_value=_passing_validation()),
    ):
        runner._run_parallel_group(nodes)

    state = runner.state_mgr.load()
    # Fan-in em ordem do YAML: o último advance é o de par-b → end.
    assert merged == ["par-a", "par-b"]
    assert state.completed_nodes[-2:] == ["par-a", "par-b"]
    assert state.current_node == "end"
    assert state.node_status == "ready"
    report = build_run_report(runner.trace.path, run_id=runner.trace.run_id)
    queue_spans = [span for span in report["spans"] if span["category"] == "queue"]
    assert len(queue_spans) == 2
    assert {span["result"] for span in queue_spans} == {"not_dispatched"}


def test_fan_in_blocks_when_one_task_fails(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    nodes = [runner.graph.get_node("par-a"), runner.graph.get_node("par-b")]

    class FakeParallelRunner:
        def __init__(self, project_root, max_slots=2):
            pass

        def run_parallel(self, tasks, delegate_fn):
            return [
                WorktreeResult(
                    node_id=tasks[0]["node_id"],
                    branch="ft-parallel/a",
                    worktree_path="",
                    success=True,
                    output="DONE",
                ),
                WorktreeResult(
                    node_id=tasks[1]["node_id"],
                    branch="",
                    worktree_path="",
                    success=False,
                    output="boom",
                ),
            ]

        def merge_all(self, results):
            return [(r.node_id, True, "merge OK") for r in results]

    with patch("ft.engine.runner.ParallelRunner", FakeParallelRunner):
        runner._run_parallel_group(nodes)

    state = runner.state_mgr.load()
    assert state.node_status == "blocked"
    assert "par-b" in (state.blocked_reason or "")


# ---------------------------------------------------------------------------
# process_validator — regras de parallel_group
# ---------------------------------------------------------------------------


def _graph_with(nodes: list[Node]):
    graph = SimpleNamespace(nodes={n.id: n for n in nodes}, meta={})
    return graph


def test_validator_rejects_shared_outputs_in_group() -> None:
    from ft.engine.process_validator import _check_parallel_groups, ValidationReport

    report = ValidationReport()
    nodes = [
        Node(id="a", type="document", title="A", executor="llm_coder",
             outputs=["docs/x.md"], parallel_group="g", next="b"),
        Node(id="b", type="document", title="B", executor="llm_coder",
             outputs=["docs/x.md"], parallel_group="g", next="end"),
    ]
    _check_parallel_groups(_graph_with(nodes), report)
    assert any("outputs compartilhados" in i.message for i in report.errors)


def test_validator_rejects_control_and_python_nodes_in_group() -> None:
    from ft.engine.process_validator import _check_parallel_groups, ValidationReport

    report = ValidationReport()
    nodes = [
        Node(id="a", type="gate", title="A", executor="python",
             outputs=["docs/x.md"], parallel_group="g", next="b"),
        Node(id="b", type="document", title="B", executor="llm_coder",
             outputs=["docs/y.md"], parallel_group="g", next="end"),
    ]
    _check_parallel_groups(_graph_with(nodes), report)
    messages = [i.message for i in report.errors]
    assert any("node de controle" in m for m in messages)
    assert any("exige executor LLM" in m for m in messages)


def test_mvp_builder_template_parallel_groups_are_valid() -> None:
    root = Path(__file__).resolve().parents[2]
    graph = load_graph(root / "templates" / "mvp-builder" / "process.yml")
    report = validate_process(graph)
    assert report.passed, [i.message for i in report.errors]

    groups: dict[str, list[str]] = {}
    for node in graph.nodes.values():
        if node.parallel_group:
            groups.setdefault(node.parallel_group, []).append(node.id)
    assert groups == {
        "plan-docs": [
            "ft.plan.03.api_contract",
            "ft.plan.04.ui_criteria",
            "ft.plan.05.test_data",
        ],
        "handoff-analysis": [
            "ft.handoff.02.prd_rewrite",
            "ft.handoff.03.critical_analysis",
        ],
    }
