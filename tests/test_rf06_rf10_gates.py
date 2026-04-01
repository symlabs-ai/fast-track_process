"""
RED PHASE — RF-06 a RF-10: Gates de Qualidade.

RF-06: cada fase tem critérios de gate explícitos e verificáveis
RF-07: gate TDD exige red→green sequencial
RF-08: gate de cobertura bloqueia se < 80%
RF-09: gate E2E falha se qualquer cenário não passar
RF-10: gates bloqueados não podem ser contornados sem resolução explícita
"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from ft.engine.validators.gates import gate_tdd_sequence, gate_coverage_80, gate_e2e_all_pass
from ft.engine.state import StateManager


# ---------------------------------------------------------------------------
# RF-07: Gate TDD exige red→green sequencial
# ---------------------------------------------------------------------------

class TestGateTDDSequence:
    def test_passes_when_red_then_green(self, tmp_path):
        """Gate TDD deve passar quando red phase ocorreu antes de green."""
        # Simula: testes falhavam antes (red), agora passam (green)
        tdd_log = {"red_phase_completed": True, "tests_passing": True}
        passed, detail = gate_tdd_sequence(tdd_log, project_root=str(tmp_path))
        assert passed is True
        assert "OK" in detail or "PASS" in detail

    def test_fails_when_no_red_phase(self, tmp_path):
        """Gate TDD deve falhar se red phase não ocorreu."""
        tdd_log = {"red_phase_completed": False, "tests_passing": True}
        passed, detail = gate_tdd_sequence(tdd_log, project_root=str(tmp_path))
        assert passed is False
        assert "red" in detail.lower() or "FAIL" in detail

    def test_fails_when_tests_not_passing(self, tmp_path):
        """Gate TDD deve falhar se testes não estão passando."""
        tdd_log = {"red_phase_completed": True, "tests_passing": False}
        passed, detail = gate_tdd_sequence(tdd_log, project_root=str(tmp_path))
        assert passed is False

    def test_fails_when_skipped_red_phase(self, tmp_path):
        """Gate TDD deve falhar se red phase foi pulada."""
        tdd_log = {"red_phase_completed": False, "tests_passing": False}
        passed, detail = gate_tdd_sequence(tdd_log, project_root=str(tmp_path))
        assert passed is False

    def test_requires_both_phases_in_order(self, tmp_path):
        """Gate TDD exige red E green, nessa ordem."""
        # red=True, green=True: deve passar
        tdd_log = {"red_phase_completed": True, "tests_passing": True}
        passed, _ = gate_tdd_sequence(tdd_log, project_root=str(tmp_path))
        assert passed is True

    def test_detail_contains_phase_info(self, tmp_path):
        """Detail deve informar qual fase falhou."""
        tdd_log = {"red_phase_completed": False, "tests_passing": True}
        passed, detail = gate_tdd_sequence(tdd_log, project_root=str(tmp_path))
        assert not passed
        assert "red" in detail.lower()


# ---------------------------------------------------------------------------
# RF-08: Gate de cobertura bloqueia se < 80%
# ---------------------------------------------------------------------------

class TestGateCoverage80:
    def test_passes_at_exactly_80_percent(self, tmp_path):
        """Gate de cobertura deve passar com exatamente 80%."""
        with patch("ft.engine.validators.gates.coverage_min") as mock_cov:
            mock_cov.return_value = (True, "coverage_min: 80% (min 80%)")
            passed, detail = gate_coverage_80(project_root=str(tmp_path))
        assert passed is True

    def test_passes_above_80_percent(self, tmp_path):
        """Gate de cobertura deve passar com > 80%."""
        with patch("ft.engine.validators.gates.coverage_min") as mock_cov:
            mock_cov.return_value = (True, "coverage_min: 95% (min 80%)")
            passed, detail = gate_coverage_80(project_root=str(tmp_path))
        assert passed is True

    def test_fails_below_80_percent(self, tmp_path):
        """Gate de cobertura deve bloquear com < 80%."""
        with patch("ft.engine.validators.gates.coverage_min") as mock_cov:
            mock_cov.return_value = (False, "coverage_min FAIL: 65% < 80%")
            passed, detail = gate_coverage_80(project_root=str(tmp_path))
        assert passed is False
        assert "FAIL" in detail or "80" in detail

    def test_fails_at_79_percent(self, tmp_path):
        """Gate de cobertura deve bloquear com 79%."""
        with patch("ft.engine.validators.gates.coverage_min") as mock_cov:
            mock_cov.return_value = (False, "coverage_min FAIL: 79% < 80%")
            passed, detail = gate_coverage_80(project_root=str(tmp_path))
        assert passed is False

    def test_threshold_is_fixed_at_80(self, tmp_path):
        """Threshold padrão deve ser 80%, não configurável neste gate."""
        with patch("ft.engine.validators.gates.coverage_min") as mock_cov:
            mock_cov.return_value = (True, "ok")
            gate_coverage_80(project_root=str(tmp_path))
            # Deve ter chamado coverage_min com 80
            call_args = mock_cov.call_args
            assert 80 in call_args.args or call_args.kwargs.get("min_pct") == 80

    def test_detail_includes_coverage_percent(self, tmp_path):
        """Detail deve incluir o percentual de cobertura."""
        with patch("ft.engine.validators.gates.coverage_min") as mock_cov:
            mock_cov.return_value = (False, "coverage_min FAIL: 72% < 80%")
            passed, detail = gate_coverage_80(project_root=str(tmp_path))
        assert "72" in detail or "80" in detail


# ---------------------------------------------------------------------------
# RF-09: Gate E2E falha se qualquer cenário não passar
# ---------------------------------------------------------------------------

class TestGateE2EAllPass:
    def test_passes_when_all_scenarios_pass(self, tmp_path):
        """Gate E2E deve passar quando todos os cenários passam."""
        scenarios = [
            {"id": "e2e-01", "passed": True},
            {"id": "e2e-02", "passed": True},
            {"id": "e2e-03", "passed": True},
        ]
        passed, detail = gate_e2e_all_pass(scenarios, project_root=str(tmp_path))
        assert passed is True

    def test_fails_when_one_scenario_fails(self, tmp_path):
        """Gate E2E deve falhar se um único cenário falhar."""
        scenarios = [
            {"id": "e2e-01", "passed": True},
            {"id": "e2e-02", "passed": False},
            {"id": "e2e-03", "passed": True},
        ]
        passed, detail = gate_e2e_all_pass(scenarios, project_root=str(tmp_path))
        assert passed is False
        assert "e2e-02" in detail or "FAIL" in detail

    def test_fails_when_all_scenarios_fail(self, tmp_path):
        """Gate E2E deve falhar quando todos os cenários falham."""
        scenarios = [
            {"id": "e2e-01", "passed": False},
            {"id": "e2e-02", "passed": False},
        ]
        passed, detail = gate_e2e_all_pass(scenarios, project_root=str(tmp_path))
        assert passed is False

    def test_fails_when_no_scenarios(self, tmp_path):
        """Gate E2E deve falhar se lista de cenários está vazia."""
        passed, detail = gate_e2e_all_pass([], project_root=str(tmp_path))
        assert passed is False
        assert "cenário" in detail.lower() or "scenario" in detail.lower() or "vazio" in detail.lower()

    def test_detail_lists_failed_scenarios(self, tmp_path):
        """Detail deve listar IDs dos cenários que falharam."""
        scenarios = [
            {"id": "cenario-happy-path", "passed": False},
            {"id": "cenario-edge-case", "passed": True},
            {"id": "cenario-error", "passed": False},
        ]
        passed, detail = gate_e2e_all_pass(scenarios, project_root=str(tmp_path))
        assert not passed
        assert "cenario-happy-path" in detail or "cenario-error" in detail

    def test_five_scenarios_all_pass(self, tmp_path):
        """Todos os 5 cenários E2E obrigatórios devem passar (AC-05)."""
        scenarios = [
            {"id": f"e2e-0{i}", "passed": True} for i in range(1, 6)
        ]
        passed, detail = gate_e2e_all_pass(scenarios, project_root=str(tmp_path))
        assert passed is True


# ---------------------------------------------------------------------------
# RF-10: Gates bloqueados não podem ser contornados
# ---------------------------------------------------------------------------

class TestGateBlockedCannotBypass:
    def test_blocked_state_prevents_advance_guarded(self, tmp_path):
        """Estado blocked deve impedir advance_guarded."""
        mgr = StateManager(tmp_path / "state.yml")
        mgr.init_from_graph({"id": "proc-001"}, "node.01", 5)
        mgr.block("cobertura insuficiente")

        with pytest.raises(RuntimeError, match="blocked|bloqueado"):
            mgr.advance_guarded("node.01", "node.02", gate_result="PASS")

    def test_regular_advance_from_blocked_also_blocked(self, tmp_path):
        """advance() padrão de estado blocked deve lançar RuntimeError."""
        mgr = StateManager(tmp_path / "state.yml")
        mgr.init_from_graph({"id": "proc-001"}, "node.01", 5)
        mgr.block("falha de gate")

        with pytest.raises(RuntimeError, match="blocked|bloqueado"):
            mgr.advance("node.01", "node.02")

    def test_unblock_required_before_advance(self, tmp_path):
        """unblock() explícito é necessário antes de advance() a partir de blocked."""
        mgr = StateManager(tmp_path / "state.yml")
        mgr.init_from_graph({"id": "proc-001"}, "node.01", 5)
        mgr.block("gate falhou")

        # Sem unblock, deve falhar
        with pytest.raises(RuntimeError):
            mgr.advance("node.01", "node.02")

        # Após unblock, deve funcionar
        mgr.unblock()
        mgr.advance("node.01", "node.02")
        state = mgr.load()
        assert state.current_node == "node.02"

    def test_block_gate_log_entry_is_immutable(self, tmp_path):
        """Entrada BLOCK no gate_log não deve ser sobrescrita por PASS silencioso."""
        mgr = StateManager(tmp_path / "state.yml")
        mgr.init_from_graph({"id": "proc-001"}, "node.01", 5)
        mgr.block_gate("node.01", "lint errors")

        state = mgr.load()
        assert state.gate_log["node.01"] == "BLOCK"

    def test_explicit_resolution_recorded_in_gate_log(self, tmp_path):
        """Após resolução explícita e advance, gate_log deve registrar PASS."""
        mgr = StateManager(tmp_path / "state.yml")
        mgr.init_from_graph({"id": "proc-001"}, "node.01", 5)
        mgr.block("falha")
        mgr.unblock()
        mgr.advance_guarded("node.01", "node.02", gate_result="PASS")

        state = mgr.load()
        assert state.gate_log["node.01"] == "PASS"
