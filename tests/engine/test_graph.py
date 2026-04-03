"""Unit tests for ft.engine.graph."""

import pytest
import yaml
from pathlib import Path
import tempfile

from ft.engine.graph import Node, ProcessGraph, load_graph


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_graph(nodes_data: list[dict]) -> ProcessGraph:
    """Helper: build a ProcessGraph from raw node dicts."""
    nodes = []
    for n in nodes_data:
        nodes.append(Node(
            id=n["id"],
            type=n.get("type", "build"),
            title=n.get("title", n["id"]),
            executor=n.get("executor", "python"),
            outputs=n.get("outputs", []),
            next=n.get("next"),
            sprint=n.get("sprint"),
            parallel_group=n.get("parallel_group"),
            branches=n.get("branches"),
            condition=n.get("condition"),
        ))
    return ProcessGraph(nodes, {"id": "test", "title": "Test"})


def simple_graph() -> ProcessGraph:
    return make_graph([
        {"id": "a", "type": "build", "next": "b"},
        {"id": "b", "type": "gate", "next": "c"},
        {"id": "c", "type": "end"},
    ])


# ---------------------------------------------------------------------------
# ProcessGraph — resolve_next
# ---------------------------------------------------------------------------

class TestResolveNext:
    def test_linear_chain(self):
        g = simple_graph()
        assert g.resolve_next("a") == "b"
        assert g.resolve_next("b") == "c"
        assert g.resolve_next("c") is None

    def test_end_node_returns_none(self):
        g = simple_graph()
        assert g.resolve_next("c") is None

    def test_decision_with_matching_branch(self):
        g = make_graph([
            {"id": "d", "type": "decision", "condition": "status",
             "branches": {"pass": "ok", "fail": "err"}, "next": "ok"},
            {"id": "ok", "type": "build", "next": "done"},
            {"id": "err", "type": "build", "next": "done"},
            {"id": "done", "type": "end"},
        ])
        assert g.resolve_next("d", {"status": "pass"}) == "ok"
        assert g.resolve_next("d", {"status": "fail"}) == "err"

    def test_decision_fallback_to_next(self):
        g = make_graph([
            {"id": "d", "type": "decision", "condition": "status",
             "branches": {"pass": "ok"}, "next": "fallback"},
            {"id": "ok", "type": "build", "next": "done"},
            {"id": "fallback", "type": "build", "next": "done"},
            {"id": "done", "type": "end"},
        ])
        assert g.resolve_next("d", {"status": "unknown"}) == "fallback"


# ---------------------------------------------------------------------------
# ProcessGraph — get_status
# ---------------------------------------------------------------------------

class TestGetStatus:
    def test_first_node_is_ready(self):
        g = simple_graph()
        status = g.get_status(set())
        assert status["a"] == "ready"
        assert status["b"] == "blocked"
        assert status["c"] == "blocked"

    def test_after_completing_a_b_is_ready(self):
        g = simple_graph()
        status = g.get_status({"a"})
        assert status["a"] == "done"
        assert status["b"] == "ready"
        assert status["c"] == "blocked"

    def test_all_done(self):
        g = simple_graph()
        status = g.get_status({"a", "b", "c"})
        assert all(s == "done" for s in status.values())


# ---------------------------------------------------------------------------
# ProcessGraph — sprint helpers
# ---------------------------------------------------------------------------

class TestSprintHelpers:
    def setup_method(self):
        self.g = make_graph([
            {"id": "a", "sprint": "s1", "next": "b"},
            {"id": "b", "sprint": "s1", "next": "c"},
            {"id": "c", "sprint": "s2", "next": "d"},
            {"id": "d", "type": "end"},
        ])

    def test_get_sprints(self):
        assert self.g.get_sprints() == ["s1", "s2"]

    def test_get_sprint_nodes(self):
        s1 = self.g.get_sprint_nodes("s1")
        assert [n.id for n in s1] == ["a", "b"]

    def test_sprint_of(self):
        assert self.g.sprint_of("a") == "s1"
        assert self.g.sprint_of("c") == "s2"
        assert self.g.sprint_of("d") is None


# ---------------------------------------------------------------------------
# ProcessGraph — validation
# ---------------------------------------------------------------------------

class TestGraphValidation:
    def test_missing_next_target_raises(self):
        with pytest.raises(ValueError, match="nao existe"):
            make_graph([
                {"id": "a", "next": "nonexistent"},
                {"id": "b", "type": "end"},
            ])

    def test_no_end_node_raises(self):
        with pytest.raises(ValueError, match="exatamente 1 node type=end"):
            make_graph([
                {"id": "a", "next": "b"},
                {"id": "b", "type": "build"},
            ])

    def test_multiple_end_nodes_raises(self):
        with pytest.raises(ValueError, match="exatamente 1 node type=end"):
            make_graph([
                {"id": "a", "next": "b"},
                {"id": "b", "type": "end"},
                {"id": "c", "type": "end"},
            ])


# ---------------------------------------------------------------------------
# load_graph — YAML file
# ---------------------------------------------------------------------------

class TestLoadGraph:
    def test_load_test_process(self):
        g = load_graph("process/test_process.yml")
        assert g.meta["id"] == "test_process"
        assert len(g.nodes) == 5

    def test_load_v2_process(self):
        g = load_graph("process/test_process_v2.yml")
        assert len(g.get_sprints()) == 2
        assert "sprint-01-discovery" in g.get_sprints()

    def test_load_fast_track_v2(self):
        g = load_graph("process/fast_track/FAST_TRACK_PROCESS_V2.yml")
        assert len(g.nodes) == 39
        assert len(g.get_sprints()) == 10

    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            load_graph("nonexistent.yml")
