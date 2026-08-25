"""Score estático de determinismo de processos (ft analyse-template)."""

from __future__ import annotations

from pathlib import Path

from ft.engine.determinism import (
    CATEGORY_SCORES,
    analyse_graph,
    classify_node,
)
from ft.engine.graph import Node, ProcessGraph, load_graph
from ft.engine.runner import VALIDATOR_REGISTRY

ROOT = Path(__file__).resolve().parents[2]
KNOWN = frozenset(VALIDATOR_REGISTRY)


def _node(**kwargs) -> Node:
    defaults = {"id": "n1", "type": "build", "title": "t", "executor": "llm_claude"}
    defaults.update(kwargs)
    return Node(**defaults)


def _graph(nodes: list[Node]) -> ProcessGraph:
    return ProcessGraph(nodes, meta={"id": "test_proc", "version": "1.0.0"})


# ---------------------------------------------------------------- classify


def test_python_executor_scores_full():
    scored = classify_node(_node(executor="python", type="gate"))
    assert scored.category == "python"
    assert scored.score == 1.0


def test_llm_with_strong_validator():
    scored = classify_node(
        _node(validators=[{"tests_pass": "pytest"}, {"file_exists": "a.md"}]),
        KNOWN,
    )
    assert scored.category == "llm_strong"
    assert scored.score == CATEGORY_SCORES["llm_strong"]


def test_llm_with_form_validator_only():
    scored = classify_node(
        _node(validators=[{"has_sections": ["A"]}, {"file_exists": "a.md"}]),
        KNOWN,
    )
    assert scored.category == "llm_medium"


def test_llm_with_existence_only():
    scored = classify_node(_node(validators=[{"file_exists": "a.md"}]), KNOWN)
    assert scored.category == "llm_weak"


def test_llm_without_validators():
    scored = classify_node(_node(validators=[]), KNOWN)
    assert scored.category == "llm_none"


def test_human_gate_and_structural_nodes_excluded():
    assert classify_node(_node(type="human_gate", executor="python")) is None
    assert classify_node(_node(type="end", executor="python")) is None


def test_unknown_validator_counts_as_medium():
    scored = classify_node(_node(validators=[{"custom_check_xyz": "arg"}]), KNOWN)
    assert scored.category == "llm_medium"


# ---------------------------------------------------------------- analyse


def test_score_is_mean_of_node_scores():
    graph = _graph(
        [
            _node(id="a", executor="python", type="gate", next="b"),
            _node(id="b", validators=[{"file_exists": "x.md"}], next="c"),
            _node(id="c", type="end", executor="python"),
        ]
    )
    report = analyse_graph(graph, KNOWN)
    assert report.process_id == "test_proc"
    assert len(report.scored) == 2
    assert report.score == (1.0 + CATEGORY_SCORES["llm_weak"]) / 2
    assert report.skipped_nodes == ["c"]


def test_requires_approval_counts_as_human_axis():
    graph = _graph(
        [
            _node(id="a", requires_approval=True, validators=[], next="z"),
            _node(id="z", type="end", executor="python"),
        ]
    )
    report = analyse_graph(graph, KNOWN)
    # continua pontuado como LLM, mas aparece no eixo humano
    assert len(report.scored) == 1
    assert report.human_nodes == ["a"]


def test_unknown_validators_reported_once():
    graph = _graph(
        [
            _node(id="a", validators=[{"weird_v": 1}], next="b"),
            _node(id="b", validators=[{"weird_v": 2}], next="z"),
            _node(id="z", type="end", executor="python"),
        ]
    )
    report = analyse_graph(graph, KNOWN)
    assert report.unknown_validators == ["weird_v"]


def test_weak_node_backed_by_gate_referencing_outputs():
    graph = _graph(
        [
            _node(
                id="fix",
                outputs=["src/"],
                validators=[{"file_exists": "src/"}],
                next="review",
            ),
            _node(id="review", type="review", validators=[], next="gate"),
            _node(
                id="gate",
                type="gate",
                executor="python",
                validators=[{"command_succeeds": "make -C src smoke"}],
                next="z",
            ),
            _node(id="z", type="end", executor="python"),
        ]
    )
    report = analyse_graph(graph, KNOWN)
    fix = next(n for n in report.weak_nodes if n.id == "fix")
    assert fix.backed_by_gate is True


def test_weak_node_not_backed_when_gate_ignores_outputs():
    graph = _graph(
        [
            _node(
                id="rewrite",
                outputs=["docs/PRD.next.md"],
                validators=[{"file_exists": "docs/PRD.next.md"}],
                next="gate",
            ),
            _node(
                id="gate",
                type="gate",
                executor="python",
                validators=[{"file_exists": "docs/other.md"}],
                next="z",
            ),
            _node(id="z", type="end", executor="python"),
        ]
    )
    report = analyse_graph(graph, KNOWN)
    rewrite = next(n for n in report.weak_nodes if n.id == "rewrite")
    assert rewrite.backed_by_gate is False


def test_backstop_follows_decision_branches():
    graph = _graph(
        [
            _node(
                id="fix",
                outputs=["src/"],
                validators=[{"file_exists": "src/"}],
                next="route",
            ),
            _node(
                id="route",
                type="decision",
                executor="python",
                branches={"a": "gate", "b": "z"},
            ),
            _node(
                id="gate",
                type="gate",
                executor="python",
                validators=[{"command_succeeds": "pytest src/tests"}],
                next="z",
            ),
            _node(id="z", type="end", executor="python"),
        ]
    )
    report = analyse_graph(graph, KNOWN)
    fix = next(n for n in report.weak_nodes if n.id == "fix")
    assert fix.backed_by_gate is True


# ------------------------------------------------------- template contracts


def test_mvp_builder_fast_score_band():
    graph = load_graph(ROOT / "templates" / "mvp-builder-fast" / "process.yml")
    report = analyse_graph(graph, KNOWN)
    # banda de regressão: mudanças no template que derrubem o determinismo
    # abaixo de 70% devem ser conscientes (ajuste este teste junto).
    assert report.score >= 0.70
    assert not report.unknown_validators
