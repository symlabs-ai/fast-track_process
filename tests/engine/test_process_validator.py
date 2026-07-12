"""Tests for ft.engine.process_validator."""

import os
from pathlib import Path
import tempfile

import pytest
import yaml

from ft.engine.graph import ProcessGraph, Node, load_graph
from ft.engine.process_validator import (
    validate_process,
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
            reject_next=n.get("reject_next"),
            on_fail=n.get("on_fail"),
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

    def test_reject_next_is_reachable_edge(self):
        graph = _make_graph([
            {"id": "build", "type": "build", "title": "Build", "next": "gate", "outputs": ["src/app.py"]},
            {"id": "gate", "type": "human_gate", "title": "Gate", "next": "end", "reject_next": "fix"},
            {"id": "fix", "type": "build", "title": "Fix", "next": "gate", "outputs": ["src/app.py"]},
            {"id": "end", "type": "end", "title": "End"},
        ])
        report = validate_process(graph)
        assert not any(e.node_id == "fix" and "órfão" in e.message for e in report.errors)
        assert not any(e.node_id == "fix" and "inalcançável" in e.message for e in report.errors)

    def test_on_fail_goto_is_reachable_edge(self):
        graph = _make_graph([
            {
                "id": "build",
                "type": "build",
                "title": "Build",
                "outputs": ["src/app.py"],
                "next": "end",
                "on_fail": {"goto": "fix"},
            },
            {
                "id": "fix",
                "type": "build",
                "title": "Fix",
                "outputs": ["src/app.py"],
                "next": "end",
            },
            {"id": "end", "type": "end", "title": "End"},
        ])

        report = validate_process(graph)

        assert not any(
            error.node_id == "fix" and "órfão" in error.message
            for error in report.errors
        )
        assert not any(
            error.node_id == "fix" and "inalcançável" in error.message
            for error in report.errors
        )

    def test_dangling_on_fail_goto_is_rejected(self):
        with pytest.raises(ValueError, match="on_fail.goto"):
            _make_graph([
                {
                    "id": "build",
                    "type": "build",
                    "title": "Build",
                    "outputs": ["src/app.py"],
                    "next": "end",
                    "on_fail": {"goto": "missing"},
                },
                {"id": "end", "type": "end", "title": "End"},
            ])

    def test_duplicate_node_ids_are_rejected(self):
        with pytest.raises(ValueError, match="duplicados: build"):
            _make_graph([
                {
                    "id": "build",
                    "type": "build",
                    "title": "Build one",
                    "outputs": ["src/one.py"],
                    "next": "end",
                },
                {
                    "id": "build",
                    "type": "build",
                    "title": "Build two",
                    "outputs": ["src/two.py"],
                    "next": "end",
                },
                {"id": "end", "type": "end", "title": "End"},
            ])

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

    @pytest.mark.parametrize("template", [
        "templates/base/process.yml",
        "templates/feature/process.yml",
        "templates/mvp-builder/process.yml",
        "templates/ft-ui-prototype/process.yml",
    ])
    def test_templates_pass_process_validation(self, template):
        process_path = Path(__file__).parent.parent.parent / template
        graph = load_graph(process_path)
        report = validate_process(graph)
        assert report.passed, f"{template}: {[e.message for e in report.errors]}"


class TestV3RuntimeNames:
    """Regressão: validator rejeitava os nomes que o runtime de fato usa.

    O graph loader normaliza claude->llm_claude (V3) e o runner executa
    node.type == "exploration" — mas VALID_EXECUTORS/VALID_NODE_TYPES
    estavam presos nos nomes da V2, então `ft validate` reprovava processos
    que rodavam em produção (vibeos cycles 01-03)."""

    def test_llm_engine_executors_validos(self):
        for executor in ("llm_claude", "llm_codex", "llm_gemini", "llm_opencode"):
            assert executor in VALID_EXECUTORS

    def test_exploration_type_valido(self):
        assert "exploration" in VALID_NODE_TYPES

    def test_processo_v3_com_nomes_curtos_passa(self):
        """YAML V3 usa executor curto ('claude/opencode'); via load_graph deve validar."""
        raw = {
            "id": "mini_v3", "version": "1.0.0", "title": "mini",
            "nodes": [
                {"id": "a", "type": "build", "title": "A", "executor": "claude",
                 "outputs": ["docs/x.md"], "next": "b"},
                {"id": "b", "type": "build", "title": "B", "executor": "opencode",
                 "outputs": ["docs/y.md"], "next": "c"},
                {"id": "c", "type": "exploration", "title": "C", "next": "fim"},
                {"id": "fim", "type": "end", "title": "Fim"},
            ],
        }
        with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False) as f:
            yaml.safe_dump(raw, f)
            path = f.name
        try:
            graph = load_graph(Path(path))
            report = validate_process(graph)
            executor_errors = [e for e in report.errors if "executor" in e.message or "type" in e.message]
            assert executor_errors == []
        finally:
            os.unlink(path)
