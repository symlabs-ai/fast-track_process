"""Unit tests for ft.engine.state."""

import pytest
import tempfile
from pathlib import Path

from ft.engine.state import StateManager, EngineState


@pytest.fixture
def tmp_state(tmp_path):
    return StateManager(tmp_path / "engine_state.yml")


@pytest.fixture
def initialized_state(tmp_state):
    tmp_state.init_from_graph(
        {"id": "test_proc", "version": "1.0.0"},
        first_node_id="node.01",
        total_steps=5,
    )
    return tmp_state


class TestStateManager:
    def test_load_nonexistent_returns_empty(self, tmp_state):
        state = tmp_state.load()
        assert state.current_node is None
        assert state.process_id == ""

    def test_init_from_graph(self, initialized_state):
        state = initialized_state.load()
        assert state.process_id == "test_proc"
        assert state.llm_engine == "claude"
        assert state.current_node == "node.01"
        assert state.node_status == "ready"
        assert state.metrics["steps_total"] == 5

    def test_init_from_graph_with_custom_llm_engine(self, tmp_state):
        tmp_state.init_from_graph(
            {"id": "test_proc", "version": "1.0.0"},
            first_node_id="node.01",
            total_steps=5,
            llm_engine="codex",
        )
        state = tmp_state.load()
        assert state.llm_engine == "codex"

    def test_persists_active_and_last_llm_logs(self, initialized_state):
        state = initialized_state.load()
        state.active_llm_log = "project/state/llm_logs/current.jsonl"
        state.last_llm_log = "project/state/llm_logs/last.jsonl"
        initialized_state.save()

        reloaded = initialized_state.load()
        assert reloaded.active_llm_log == "project/state/llm_logs/current.jsonl"
        assert reloaded.last_llm_log == "project/state/llm_logs/last.jsonl"

    def test_save_sets_lock(self, initialized_state):
        state = initialized_state.load()
        assert state._lock is not None
        assert state._lock["owner"] == "ft_engine"

    def test_advance_moves_current_node(self, initialized_state):
        initialized_state.advance("node.01", "node.02")
        state = initialized_state.load()
        assert state.current_node == "node.02"
        assert "node.01" in state.completed_nodes
        assert state.gate_log["node.01"] == "PASS"
        assert state.metrics["steps_completed"] == 1

    def test_advance_to_none_sets_done(self, initialized_state):
        initialized_state.advance("node.01", None)
        state = initialized_state.load()
        assert state.current_node is None
        assert state.node_status == "done"

    def test_block_sets_reason(self, initialized_state):
        initialized_state.block("test failure reason")
        state = initialized_state.load()
        assert state.node_status == "blocked"
        assert state.blocked_reason == "test failure reason"

    def test_set_pending_approval(self, initialized_state):
        initialized_state.set_pending_approval("node.01")
        state = initialized_state.load()
        assert state.node_status == "awaiting_approval"
        assert state.pending_approval == "node.01"

    def test_advance_clears_blocked_reason(self, initialized_state):
        initialized_state.block("reason")
        initialized_state.unblock()
        initialized_state.advance("node.01", "node.02")
        state = initialized_state.load()
        assert state.blocked_reason is None
        assert state.pending_approval is None

    def test_record_artifact(self, initialized_state):
        initialized_state.record_artifact("prd", "project/docs/PRD.md")
        state = initialized_state.load()
        assert state.artifacts["prd"] == "project/docs/PRD.md"

    def test_persistence_across_instances(self, tmp_path):
        """State persists when loading with a new instance."""
        path = tmp_path / "engine_state.yml"
        mgr1 = StateManager(path)
        mgr1.init_from_graph({"id": "p1"}, "n1", 3)
        mgr1.advance("n1", "n2")

        mgr2 = StateManager(path)
        state = mgr2.load()
        assert state.current_node == "n2"
        assert "n1" in state.completed_nodes

    def test_llm_calls_counter(self, initialized_state):
        state = initialized_state.state
        state.metrics["llm_calls"] = state.metrics.get("llm_calls", 0) + 1
        initialized_state.save()
        reloaded = initialized_state.load()
        assert reloaded.metrics["llm_calls"] == 1
