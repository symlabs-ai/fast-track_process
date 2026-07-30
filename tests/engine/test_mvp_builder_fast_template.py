"""Contract tests for the opt-in mvp-builder-fast process."""

from pathlib import Path

from ft.engine.graph import ProcessGraph, load_graph
from ft.engine.process_validator import validate_process
from ft.templates.catalog import TemplateCatalog


ROOT = Path(__file__).resolve().parents[2]
FAST_PROCESS = ROOT / "templates" / "mvp-builder-fast" / "process.yml"
BASE_PROCESS = ROOT / "templates" / "mvp-builder" / "process.yml"


def _new_product_path(graph: ProcessGraph) -> list[str]:
    """Follow the false branch used when no canonical project docs exist."""
    seen: list[str] = []
    node = graph.first_node()
    while node.id not in seen:
        seen.append(node.id)
        if node.type == "decision":
            target = (node.branches or {}).get("false")
        else:
            target = node.next
        if not target:
            break
        node = graph.get_node(target)
    return seen


def _reachable(graph: ProcessGraph) -> set[str]:
    pending = [graph.first_node().id]
    found: set[str] = set()
    while pending:
        node_id = pending.pop()
        if node_id in found:
            continue
        found.add(node_id)
        node = graph.get_node(node_id)
        targets = [
            node.next,
            node.reject_next,
            (node.on_fail or {}).get("goto"),
            *(node.branches or {}).values(),
        ]
        pending.extend(target for target in targets if target)
    return found


def test_catalog_exposes_mvp_builder_fast() -> None:
    descriptor = TemplateCatalog(ROOT / "templates").get("mvp-builder-fast")

    assert descriptor.process_file == FAST_PROCESS
    assert descriptor.policy["template"] == "mvp-builder-fast"


def test_fast_graph_and_session_policy_are_valid() -> None:
    graph = load_graph(FAST_PROCESS)
    report = validate_process(graph)

    assert report.passed, [issue.message for issue in report.errors]
    assert graph.meta["id"] == "mvp_builder_fast"
    assert graph.meta["session_policy"] == {
        "mode": "sprint",
        "providers": ["claude", "codex"],
        "initial_plan": "internal",
        "parallel_strategy": "fork",
        "recovery": "rehydrate",
    }
    assert _reachable(graph) == set(graph.nodes)


def test_fast_path_reduces_llm_turns_and_preserves_human_gates() -> None:
    baseline = load_graph(BASE_PROCESS)
    fast = load_graph(FAST_PROCESS)
    baseline_path = _new_product_path(baseline)
    fast_path = _new_product_path(fast)
    baseline_turns = sum(
        baseline.get_node(node_id).executor.startswith("llm")
        for node_id in baseline_path
    )
    # Include the internal planning turn that precedes the graph.
    fast_turns = 1 + sum(
        fast.get_node(node_id).executor.startswith("llm")
        for node_id in fast_path
    )

    assert fast_turns <= 25
    assert fast_turns <= baseline_turns * 0.75
    # O gate de plano é determinístico; o oitavo human gate é o aceite final.
    assert sum(node.type == "human_gate" for node in fast.nodes.values()) == 8
    assert sum(node.type == "human_gate" for node in baseline.nodes.values()) == 7


def test_parallel_flag_routes_to_one_internal_builder_batch() -> None:
    graph = load_graph(FAST_PROCESS)
    route = graph.first_node()

    assert route.id == "ft.start.batch_mode"
    assert route.condition == "parallel_enabled"
    assert route.branches == {
        "true": "ft.batch.01.plan",
        "false": "ft.start.route",
    }
    assert graph.get_node("ft.batch.03.foundation").type == "build"
    assert graph.get_node("ft.batch.03.foundation").allow_pre_seed is True
    assert graph.get_node("ft.batch.04.execute").type == "batch"
    assert graph.get_node("ft.batch.04.execute").next == "ft.batch.05.review"
    assert graph.get_node("ft.batch.01.plan").no_pre_seed is False
    assert graph.get_node("ft.batch.01.plan").next == "ft.batch.03.foundation"
    assert graph.get_node("ft.batch.07.verify").next == "ft.batch.08.acceptance"
    assert graph.get_node("ft.batch.08.acceptance").reject_next == "ft.batch.06.fix"
    assert graph.get_node("ft.batch.09.reconcile").next == "ft.end"
    assert graph.meta["batch_policy"]["min_lanes"] == 2
    assert graph.meta["batch_policy"]["default_max_parallel"] == 2


def test_macro_nodes_keep_deterministic_checkpoints() -> None:
    graph = load_graph(FAST_PROCESS)

    foundation = graph.get_node("ft.plan.00.foundation_full")
    assert set(foundation.outputs) >= {
        "docs/PROJECT_BACKLOG.md",
        "docs/FEATURES.md",
        "docs/task_list.md",
        "docs/TECH_STACK.md",
    }
    assert foundation.next == "ft.plan.02b.tech_gate"

    frontend = graph.get_node("ft.frontend.01.build")
    assert frontend.next == "ft.frontend.03.prd_review"
    assert graph.get_node("ft.frontend.03.prd_review").type == "review"

    assert graph.get_node("ft.tdd.01.red").next == "ft.tdd.02.green"
    assert graph.get_node("ft.tdd.02.green").next == "ft.tdd.03.refactor"
    assert graph.get_node("ft.tdd.03.refactor").next == "gate.tdd"

    assert graph.get_node("ft.handoff.02.backlog_update").executor == "python"
    assert graph.get_node("ft.handoff.02b.features_update").executor == "python"
