"""Tests for ft.engine.process_validator."""

import pytest
import yaml
from pathlib import Path

from ft.engine.graph import ProcessGraph, Node, load_graph
from ft.engine.process_validator import (
    validate_process,
    ValidationReport,
    VALID_NODE_TYPES,
    VALID_EXECUTORS,
)


def _make_graph(nodes_raw: list[dict], meta: dict | None = None) -> ProcessGraph:
    """Helper: cria ProcessGraph a partir de lista de dicts."""
    nodes = []
    for n in nodes_raw:
        nodes.append(Node(
            id=n["id"],
            type=n.get("type", "build"),
            title=n.get("title", n["id"]),
            executor=n.get("executor", "python"),
            outputs=n.get("outputs", []),
            validators=n.get("validators", []),
            next=n.get("next"),
            branches=n.get("branches"),
            condition=n.get("condition"),
            max_turns=n.get("max_turns"),
        ))
    return ProcessGraph(nodes, meta or {"id": "test", "version": "1.0.0"})


class TestStructure:
    def test_valid_types_pass(self):
        graph = _make_graph([
            {"id": "start", "type": "build", "title": "Start", "next": "end"},
            {"id": "end", "type": "end", "title": "End"},
        ])
        report = validate_process(graph)
        assert report.passed

    def test_invalid_type_error(self):
        graph = _make_graph([
            {"id": "start", "type": "magic", "title": "Start", "next": "end"},
            {"id": "end", "type": "end", "title": "End"},
        ])
        report = validate_process(graph)
        assert not report.passed
        assert any("type 'magic'" in e.message for e in report.errors)

    def test_invalid_executor_error(self):
        graph = _make_graph([
            {"id": "start", "type": "build", "title": "Start", "executor": "llm_designer", "next": "end"},
            {"id": "end", "type": "end", "title": "End"},
        ])
        report = validate_process(graph)
        assert any("executor 'llm_designer'" in e.message for e in report.errors)


class TestGraphIntegrity:
    def test_orphan_node_detected(self):
        graph = _make_graph([
            {"id": "start", "type": "build", "title": "Start", "next": "end"},
            {"id": "orphan", "type": "build", "title": "Orphan", "next": "end"},
            {"id": "end", "type": "end", "title": "End"},
        ])
        report = validate_process(graph)
        assert any("órfão" in e.message and e.node_id == "orphan" for e in report.errors)

    def test_linear_graph_passes(self):
        graph = _make_graph([
            {"id": "a", "type": "build", "title": "A", "next": "b"},
            {"id": "b", "type": "gate", "title": "B", "next": "c"},
            {"id": "c", "type": "build", "title": "C", "next": "end"},
            {"id": "end", "type": "end", "title": "End"},
        ])
        report = validate_process(graph)
        assert report.passed

    def test_decision_branches_reachable(self):
        graph = _make_graph([
            {"id": "start", "type": "decision", "title": "Decide",
             "condition": "mode", "branches": {"a": "path_a", "b": "path_b"}, "next": "path_a"},
            {"id": "path_a", "type": "build", "title": "Path A", "next": "end"},
            {"id": "path_b", "type": "build", "title": "Path B", "next": "end"},
            {"id": "end", "type": "end", "title": "End"},
        ])
        report = validate_process(graph)
        assert report.passed

    def test_non_terminal_without_next_error(self):
        graph = _make_graph([
            {"id": "start", "type": "build", "title": "Start", "next": "dangling"},
            {"id": "dangling", "type": "build", "title": "Dangling"},
            {"id": "end", "type": "end", "title": "End"},
        ])
        report = validate_process(graph)
        assert any("sem next" in e.message for e in report.errors)


class TestValidatorChecks:
    def test_unknown_validator_warning(self):
        graph = _make_graph([
            {"id": "start", "type": "gate", "title": "Gate",
             "validators": [{"nonexistent_check": True}], "next": "end"},
            {"id": "end", "type": "end", "title": "End"},
        ])
        registry = {"tests_pass": lambda: True}
        report = validate_process(graph, registry)
        assert any("nonexistent_check" in w.message for w in report.warnings)

    def test_known_validator_no_warning(self):
        graph = _make_graph([
            {"id": "start", "type": "gate", "title": "Gate",
             "validators": [{"tests_pass": True}], "next": "end"},
            {"id": "end", "type": "end", "title": "End"},
        ])
        registry = {"tests_pass": lambda: True}
        report = validate_process(graph, registry)
        assert not any("tests_pass" in w.message for w in report.warnings)


class TestSemantics:
    def test_gate_without_validators_warning(self):
        graph = _make_graph([
            {"id": "start", "type": "gate", "title": "Gate", "next": "end"},
            {"id": "end", "type": "end", "title": "End"},
        ])
        report = validate_process(graph)
        assert any("gate sem validators" in w.message for w in report.warnings)

    def test_build_without_outputs_warning(self):
        graph = _make_graph([
            {"id": "start", "type": "build", "title": "Build", "next": "end"},
            {"id": "end", "type": "end", "title": "End"},
        ])
        report = validate_process(graph)
        assert any("build sem outputs" in w.message for w in report.warnings)

    def test_llm_without_max_turns_warning(self):
        graph = _make_graph([
            {"id": "start", "type": "build", "title": "Build",
             "executor": "llm_coder", "outputs": ["src/"], "next": "end"},
            {"id": "end", "type": "end", "title": "End"},
        ])
        report = validate_process(graph)
        assert any("max_turns" in w.message for w in report.warnings)


class TestRealProcess:
    """Testa com o processo V2 real."""

    def test_fast_track_v2_passes(self):
        process_path = Path(__file__).parent.parent.parent / "process" / "fast_track" / "FAST_TRACK_PROCESS_V2.yml"
        if not process_path.exists():
            pytest.skip("FAST_TRACK_PROCESS_V2.yml not found")
        graph = load_graph(process_path)
        report = validate_process(graph)
        # Pode ter warnings, mas não deve ter erros
        assert report.passed, f"Erros: {[e.message for e in report.errors]}"
