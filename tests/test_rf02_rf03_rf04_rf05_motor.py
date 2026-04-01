"""
RED PHASE — RF-02 a RF-05: Motor de Estado.

RF-02: motor avança somente após gate PASS
RF-03: blocked_reason preenchido em gate BLOCK
RF-04: suporte a múltiplos ciclos (cycle-01, cycle-02, ...)
RF-05: métricas acumuladas (steps, cobertura, tokens)
"""

import pytest
from pathlib import Path

from ft.engine.state import StateManager
from ft.engine.cycle_manager import CycleManager
from ft.engine.metrics import MetricsTracker


# ---------------------------------------------------------------------------
# RF-02: Motor avança somente após gate PASS
# ---------------------------------------------------------------------------

class TestGatePassRequired:
    def test_advance_without_pass_raises(self, tmp_path):
        """advance() sem gate_result=PASS deve ser rejeitado."""
        mgr = StateManager(tmp_path / "state.yml")
        mgr.init_from_graph({"id": "proc-001"}, "node.01", 5)
        with pytest.raises(ValueError, match="PASS|gate_result"):
            mgr.advance_guarded("node.01", "node.02", gate_result="FAIL")

    def test_advance_with_pass_succeeds(self, tmp_path):
        """advance() com gate_result=PASS deve avançar normalmente."""
        mgr = StateManager(tmp_path / "state.yml")
        mgr.init_from_graph({"id": "proc-001"}, "node.01", 5)
        mgr.advance_guarded("node.01", "node.02", gate_result="PASS")
        state = mgr.load()
        assert state.current_node == "node.02"

    def test_advance_blocked_state_raises(self, tmp_path):
        """Não deve ser possível avançar a partir de estado blocked sem desbloquear."""
        mgr = StateManager(tmp_path / "state.yml")
        mgr.init_from_graph({"id": "proc-001"}, "node.01", 5)
        mgr.block("falha no gate")
        with pytest.raises(RuntimeError, match="blocked|bloqueado"):
            mgr.advance_guarded("node.01", "node.02", gate_result="PASS")

    def test_unblock_then_advance_succeeds(self, tmp_path):
        """Após unblock explícito, advance deve funcionar."""
        mgr = StateManager(tmp_path / "state.yml")
        mgr.init_from_graph({"id": "proc-001"}, "node.01", 5)
        mgr.block("falha temporária")
        mgr.unblock()
        mgr.advance_guarded("node.01", "node.02", gate_result="PASS")
        state = mgr.load()
        assert state.current_node == "node.02"


# ---------------------------------------------------------------------------
# RF-03: blocked_reason preenchido em gate BLOCK
# ---------------------------------------------------------------------------

class TestBlockedReason:
    def test_block_sets_reason(self, tmp_path):
        """block() deve persistir blocked_reason."""
        mgr = StateManager(tmp_path / "state.yml")
        mgr.init_from_graph({"id": "proc-001"}, "node.01", 5)
        mgr.block("cobertura insuficiente: 65%")
        state = mgr.load()
        assert state.node_status == "blocked"
        assert state.blocked_reason == "cobertura insuficiente: 65%"

    def test_block_empty_reason_raises(self, tmp_path):
        """block() com reason vazio deve ser rejeitado."""
        mgr = StateManager(tmp_path / "state.yml")
        mgr.init_from_graph({"id": "proc-001"}, "node.01", 5)
        with pytest.raises(ValueError, match="reason|motivo"):
            mgr.block("")

    def test_advance_clears_blocked_reason(self, tmp_path):
        """Após advance, blocked_reason deve ser None."""
        mgr = StateManager(tmp_path / "state.yml")
        mgr.init_from_graph({"id": "proc-001"}, "node.01", 5)
        mgr.block("motivo")
        mgr.unblock()
        mgr.advance_guarded("node.01", "node.02", gate_result="PASS")
        state = mgr.load()
        assert state.blocked_reason is None

    def test_gate_log_records_block_with_reason(self, tmp_path):
        """gate_log deve registrar BLOCK e o motivo."""
        mgr = StateManager(tmp_path / "state.yml")
        mgr.init_from_graph({"id": "proc-001"}, "node.01", 5)
        mgr.block_gate("node.01", "lint falhou")
        state = mgr.load()
        assert state.gate_log.get("node.01") == "BLOCK"
        assert state.blocked_reason is not None


# ---------------------------------------------------------------------------
# RF-04: Suporte a múltiplos ciclos
# ---------------------------------------------------------------------------

class TestMultipleCycles:
    def test_cycle_manager_initial_cycle(self, tmp_path):
        """CycleManager deve inicializar com cycle-01."""
        cm = CycleManager(tmp_path / "state.yml")
        assert cm.current_cycle() == "cycle-01"

    def test_advance_cycle_increments(self, tmp_path):
        """advance_cycle() deve ir de cycle-01 para cycle-02."""
        cm = CycleManager(tmp_path / "state.yml")
        cm.advance_cycle()
        assert cm.current_cycle() == "cycle-02"

    def test_advance_cycle_multiple_times(self, tmp_path):
        """advance_cycle() repetido deve incrementar sequencialmente."""
        cm = CycleManager(tmp_path / "state.yml")
        cm.advance_cycle()
        cm.advance_cycle()
        assert cm.current_cycle() == "cycle-03"

    def test_cycle_history_preserved(self, tmp_path):
        """Histórico de ciclos passados deve ser mantido."""
        cm = CycleManager(tmp_path / "state.yml")
        cm.advance_cycle()
        cm.advance_cycle()
        history = cm.cycle_history()
        assert "cycle-01" in history
        assert "cycle-02" in history

    def test_state_reset_on_new_cycle(self, tmp_path):
        """Novo ciclo deve resetar steps_completed mas preservar gate_log."""
        mgr = StateManager(tmp_path / "state.yml")
        mgr.init_from_graph({"id": "proc-001"}, "node.01", 5)
        mgr.advance("node.01", "node.02")

        cm = CycleManager(tmp_path / "state.yml")
        cm.advance_cycle(first_node="node.01")

        state = mgr.load()
        assert state.current_cycle == "cycle-02"
        assert state.metrics["steps_completed"] == 0

    def test_cycle_state_no_corruption(self, tmp_path):
        """Ciclos distintos não devem corromper o estado um do outro."""
        mgr = StateManager(tmp_path / "state.yml")
        mgr.init_from_graph({"id": "proc-001"}, "node.01", 5)
        mgr.record_artifact("prd", "project/docs/PRD.md")

        cm = CycleManager(tmp_path / "state.yml")
        cm.advance_cycle(first_node="node.01")

        state = mgr.load()
        # Artifacts do ciclo anterior devem ser preservados
        assert "prd" in state.artifacts


# ---------------------------------------------------------------------------
# RF-05: Métricas acumuladas (steps, cobertura, tokens)
# ---------------------------------------------------------------------------

class TestMetricsAccumulation:
    def test_steps_completed_increments_on_advance(self, tmp_path):
        """steps_completed deve incrementar a cada advance."""
        mgr = StateManager(tmp_path / "state.yml")
        mgr.init_from_graph({"id": "proc-001"}, "node.01", 5)
        mgr.advance("node.01", "node.02")
        mgr.advance("node.02", "node.03")
        state = mgr.load()
        assert state.metrics["steps_completed"] == 2

    def test_tokens_used_tracking(self, tmp_path):
        """MetricsTracker deve acumular tokens_used."""
        tracker = MetricsTracker(tmp_path / "state.yml")
        tracker.add_tokens(1500)
        tracker.add_tokens(2000)
        assert tracker.total_tokens() == 3500

    def test_coverage_update(self, tmp_path):
        """MetricsTracker deve atualizar coverage."""
        tracker = MetricsTracker(tmp_path / "state.yml")
        tracker.update_coverage(72.5)
        assert tracker.current_coverage() == 72.5

    def test_llm_calls_increment(self, tmp_path):
        """MetricsTracker deve contar LLM calls."""
        tracker = MetricsTracker(tmp_path / "state.yml")
        tracker.record_llm_call()
        tracker.record_llm_call()
        tracker.record_llm_call()
        assert tracker.total_llm_calls() == 3

    def test_metrics_persist_across_instances(self, tmp_path):
        """Métricas devem persistir entre instâncias."""
        path = tmp_path / "state.yml"
        t1 = MetricsTracker(path)
        t1.add_tokens(5000)
        t1.update_coverage(85.0)

        t2 = MetricsTracker(path)
        assert t2.total_tokens() == 5000
        assert t2.current_coverage() == 85.0

    def test_metrics_summary_returns_dict(self, tmp_path):
        """summary() deve retornar dict com todas as métricas."""
        tracker = MetricsTracker(tmp_path / "state.yml")
        tracker.add_tokens(1000)
        tracker.update_coverage(80.0)
        tracker.record_llm_call()

        summary = tracker.summary()
        assert "tokens_used" in summary
        assert "coverage" in summary
        assert "llm_calls" in summary
        assert summary["tokens_used"] == 1000
        assert summary["coverage"] == 80.0
        assert summary["llm_calls"] == 1
