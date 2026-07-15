"""Unit tests for ft.engine.state."""

import os
import pytest

import yaml

from ft.engine.state import StateManager


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

    def test_init_from_graph_persists_llm_selection(self, tmp_state):
        tmp_state.init_from_graph(
            {"id": "test_proc", "version": "1.0.0"},
            first_node_id="node.01",
            total_steps=5,
            llm_engine="claude",
            llm_model="fable",
            llm_effort="max",
        )

        state = tmp_state.load()
        assert state.llm_model == "fable"
        assert state.llm_effort == "max"

    def test_init_from_graph_persists_cycle_objective(self, tmp_state):
        tmp_state.init_from_graph(
            {"id": "feature", "version": "1.3.0"},
            first_node_id="feature.preflight",
            total_steps=17,
            current_cycle="cycle-13-feature",
            cycle_objective="Adicionar filtro por período ao relatório.",
        )

        state = tmp_state.load()
        assert state.current_cycle == "cycle-13-feature"
        assert state.cycle_objective == "Adicionar filtro por período ao relatório."

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

    def test_release_lock_preserves_state_and_unknown_fields(self, initialized_state):
        state = initialized_state.state
        state.last_approval_message = "preservar"
        initialized_state.save()
        raw = yaml.safe_load(initialized_state.path.read_text(encoding="utf-8"))
        raw["future_field"] = {"value": 42}
        initialized_state.path.write_text(
            yaml.safe_dump(raw, sort_keys=False), encoding="utf-8"
        )

        initialized_state.release_lock()

        persisted = yaml.safe_load(initialized_state.path.read_text(encoding="utf-8"))
        assert persisted["_lock"] is None
        assert persisted["last_approval_message"] == "preservar"
        assert persisted["future_field"] == {"value": 42}
        reloaded = StateManager(initialized_state.path).load(check_lock=True)
        assert reloaded.current_node == "node.01"
        assert reloaded.last_approval_message == "preservar"

    def test_save_after_release_reacquires_lock(self, initialized_state):
        initialized_state.release_lock()
        initialized_state.state.node_status = "delegated"

        initialized_state.save()

        persisted = yaml.safe_load(initialized_state.path.read_text(encoding="utf-8"))
        assert persisted["node_status"] == "delegated"
        assert persisted["_lock"]["owner"] == "ft_engine"
        assert persisted["_lock"]["pid"] == os.getpid()

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
