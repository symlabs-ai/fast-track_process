"""
RED PHASE — RF-11 a RF-15: Agentes.

RF-11: cada agente opera apenas dentro do seu escopo
RF-12: ft_manager é o único agente que pode avançar nodes
RF-13: ft_gatekeeper retorna apenas PASS ou BLOCK
RF-14: forge_coder executa ciclos TDD (red → green → refactor)
RF-15: ft_acceptance gera matriz de cenários (happy, edge, error)
"""

import pytest
from pathlib import Path

from ft.engine.agent_policy import AgentPolicy, AgentRole
from ft.engine.gatekeeper import Gatekeeper, GatekeeperResult
from ft.engine.tdd_cycle import TDDCycleTracker, TDDPhase
from ft.engine.acceptance import AcceptanceMatrix


# ---------------------------------------------------------------------------
# RF-11: Cada agente opera apenas dentro do seu escopo
# ---------------------------------------------------------------------------

class TestAgentScope:
    def test_ft_manager_can_advance_nodes(self):
        """ft_manager deve ter permissão para avançar nodes."""
        policy = AgentPolicy()
        assert policy.can_advance_node(AgentRole.FT_MANAGER) is True

    def test_forge_coder_cannot_advance_nodes(self):
        """forge_coder NÃO deve ter permissão para avançar nodes."""
        policy = AgentPolicy()
        assert policy.can_advance_node(AgentRole.FORGE_CODER) is False

    def test_ft_coach_cannot_advance_nodes(self):
        """ft_coach NÃO deve ter permissão para avançar nodes."""
        policy = AgentPolicy()
        assert policy.can_advance_node(AgentRole.FT_COACH) is False

    def test_ft_gatekeeper_cannot_advance_nodes(self):
        """ft_gatekeeper NÃO deve ter permissão para avançar nodes."""
        policy = AgentPolicy()
        assert policy.can_advance_node(AgentRole.FT_GATEKEEPER) is False

    def test_ft_acceptance_cannot_advance_nodes(self):
        """ft_acceptance NÃO deve ter permissão para avançar nodes."""
        policy = AgentPolicy()
        assert policy.can_advance_node(AgentRole.FT_ACCEPTANCE) is False

    def test_forge_coder_allowed_paths(self):
        """forge_coder deve ter paths permitidos restritos a src/ e tests/."""
        policy = AgentPolicy()
        allowed = policy.allowed_paths(AgentRole.FORGE_CODER)
        assert any("src" in p or "tests" in p for p in allowed)

    def test_ft_coach_allowed_paths(self):
        """ft_coach deve ter paths permitidos restritos a project/docs/."""
        policy = AgentPolicy()
        allowed = policy.allowed_paths(AgentRole.FT_COACH)
        assert any("project/docs" in p or "docs" in p for p in allowed)

    def test_path_outside_scope_rejected(self):
        """Agente tentando escrever fora do escopo deve ser rejeitado."""
        policy = AgentPolicy()
        with pytest.raises(PermissionError, match="escopo|scope|permitido"):
            policy.assert_allowed(AgentRole.FORGE_CODER, "process/fast_track/FAST_TRACK_PROCESS_V2.yml")

    def test_path_within_scope_allowed(self):
        """Agente escrevendo dentro do escopo deve ser permitido."""
        policy = AgentPolicy()
        # forge_coder pode escrever em src/
        policy.assert_allowed(AgentRole.FORGE_CODER, "src/domain/entities/feature.py")

    def test_ft_manager_allowed_all_paths(self):
        """ft_manager como orquestrador pode aceder a mais paths."""
        policy = AgentPolicy()
        allowed = policy.allowed_paths(AgentRole.FT_MANAGER)
        assert len(allowed) > 0


# ---------------------------------------------------------------------------
# RF-12: ft_manager é o único agente que pode avançar nodes
# ---------------------------------------------------------------------------

class TestFtManagerExclusiveAdvance:
    def test_ft_manager_is_only_agent_who_can_advance(self):
        """Apenas ft_manager deve ter can_advance_node=True."""
        policy = AgentPolicy()
        agents_that_can_advance = [
            role for role in AgentRole
            if policy.can_advance_node(role)
        ]
        assert agents_that_can_advance == [AgentRole.FT_MANAGER]

    def test_advance_from_non_manager_raises(self, tmp_path):
        """advance_as() chamado por agente não-manager deve lançar exceção."""
        policy = AgentPolicy()
        from ft.engine.state import StateManager
        mgr = StateManager(tmp_path / "state.yml")
        mgr.init_from_graph({"id": "proc-001"}, "node.01", 5)

        with pytest.raises(PermissionError, match="ft_manager|manager"):
            policy.advance_as(AgentRole.FORGE_CODER, mgr, "node.01", "node.02")

    def test_advance_as_manager_succeeds(self, tmp_path):
        """advance_as() chamado por ft_manager deve funcionar."""
        policy = AgentPolicy()
        from ft.engine.state import StateManager
        mgr = StateManager(tmp_path / "state.yml")
        mgr.init_from_graph({"id": "proc-001"}, "node.01", 5)

        policy.advance_as(AgentRole.FT_MANAGER, mgr, "node.01", "node.02")
        state = mgr.load()
        assert state.current_node == "node.02"


# ---------------------------------------------------------------------------
# RF-13: ft_gatekeeper retorna apenas PASS ou BLOCK
# ---------------------------------------------------------------------------

class TestGatekeeperPassBlock:
    def test_gatekeeper_returns_pass(self):
        """Gatekeeper deve retornar PASS quando critérios são atendidos."""
        gk = Gatekeeper()
        criteria = {"file_exists": ["project/docs/PRD.md"]}
        # Mock: arquivo existe
        result = gk.evaluate(criteria, all_passed=True)
        assert result == GatekeeperResult.PASS

    def test_gatekeeper_returns_block(self):
        """Gatekeeper deve retornar BLOCK quando critérios falham."""
        gk = Gatekeeper()
        criteria = {"file_exists": ["missing_file.md"]}
        result = gk.evaluate(criteria, all_passed=False)
        assert result == GatekeeperResult.BLOCK

    def test_gatekeeper_result_is_only_pass_or_block(self):
        """GatekeeperResult só deve ter PASS e BLOCK como valores válidos."""
        valid_values = {r.value for r in GatekeeperResult}
        assert valid_values == {"PASS", "BLOCK"}

    def test_gatekeeper_no_intermediate_states(self):
        """Não deve existir estado intermediário no gatekeeper."""
        assert len(list(GatekeeperResult)) == 2

    def test_pass_result_is_string_pass(self):
        """GatekeeperResult.PASS deve ter valor 'PASS'."""
        assert GatekeeperResult.PASS.value == "PASS"

    def test_block_result_is_string_block(self):
        """GatekeeperResult.BLOCK deve ter valor 'BLOCK'."""
        assert GatekeeperResult.BLOCK.value == "BLOCK"

    def test_gatekeeper_evaluate_returns_enum(self):
        """evaluate() deve sempre retornar GatekeeperResult."""
        gk = Gatekeeper()
        result = gk.evaluate({}, all_passed=True)
        assert isinstance(result, GatekeeperResult)

    def test_gatekeeper_reason_on_block(self):
        """Gatekeeper deve fornecer reason ao retornar BLOCK."""
        gk = Gatekeeper()
        result, reason = gk.evaluate_with_reason({}, all_passed=False, failure_detail="cobertura 65%")
        assert result == GatekeeperResult.BLOCK
        assert reason is not None
        assert len(reason) > 0


# ---------------------------------------------------------------------------
# RF-14: forge_coder executa red → green → refactor
# ---------------------------------------------------------------------------

class TestTDDCycleTracker:
    def test_initial_phase_is_red(self, tmp_path):
        """Ciclo TDD deve iniciar na fase RED."""
        tracker = TDDCycleTracker(tmp_path / "tdd_state.yml")
        assert tracker.current_phase() == TDDPhase.RED

    def test_red_to_green_transition(self, tmp_path):
        """Deve ser possível avançar de RED para GREEN."""
        tracker = TDDCycleTracker(tmp_path / "tdd_state.yml")
        tracker.advance_phase()
        assert tracker.current_phase() == TDDPhase.GREEN

    def test_green_to_refactor_transition(self, tmp_path):
        """Deve ser possível avançar de GREEN para REFACTOR."""
        tracker = TDDCycleTracker(tmp_path / "tdd_state.yml")
        tracker.advance_phase()  # RED → GREEN
        tracker.advance_phase()  # GREEN → REFACTOR
        assert tracker.current_phase() == TDDPhase.REFACTOR

    def test_cannot_skip_red_phase(self, tmp_path):
        """Não deve ser possível pular direto para GREEN sem RED."""
        tracker = TDDCycleTracker(tmp_path / "tdd_state.yml")
        with pytest.raises(ValueError, match="RED|sequência|sequence"):
            tracker.set_phase(TDDPhase.GREEN)

    def test_red_phase_requires_failing_tests(self, tmp_path):
        """RED phase só deve ser marcada como completa se testes falharam."""
        tracker = TDDCycleTracker(tmp_path / "tdd_state.yml")
        with pytest.raises(ValueError, match="testes.*falhar|tests.*fail|red"):
            tracker.complete_red(tests_failed=False)

    def test_green_phase_requires_passing_tests(self, tmp_path):
        """GREEN phase só deve ser marcada como completa se testes passam."""
        tracker = TDDCycleTracker(tmp_path / "tdd_state.yml")
        tracker.complete_red(tests_failed=True)
        tracker.advance_phase()  # RED → GREEN
        with pytest.raises(ValueError, match="testes.*passar|tests.*pass|green"):
            tracker.complete_green(tests_passed=False)

    def test_full_cycle_red_green_refactor(self, tmp_path):
        """Ciclo completo RED→GREEN→REFACTOR deve funcionar."""
        tracker = TDDCycleTracker(tmp_path / "tdd_state.yml")
        tracker.complete_red(tests_failed=True)
        tracker.advance_phase()
        tracker.complete_green(tests_passed=True)
        tracker.advance_phase()
        assert tracker.current_phase() == TDDPhase.REFACTOR
        assert tracker.is_cycle_complete() is False  # Ainda no refactor

    def test_cycle_complete_after_refactor(self, tmp_path):
        """Ciclo deve ser marcado como completo após refactor."""
        tracker = TDDCycleTracker(tmp_path / "tdd_state.yml")
        tracker.complete_red(tests_failed=True)
        tracker.advance_phase()
        tracker.complete_green(tests_passed=True)
        tracker.advance_phase()
        tracker.complete_refactor()
        assert tracker.is_cycle_complete() is True

    def test_tdd_phase_enum_values(self):
        """TDDPhase deve ter exatamente RED, GREEN, REFACTOR."""
        phases = {p.value for p in TDDPhase}
        assert "RED" in phases
        assert "GREEN" in phases
        assert "REFACTOR" in phases


# ---------------------------------------------------------------------------
# RF-15: ft_acceptance gera matriz de cenários
# ---------------------------------------------------------------------------

class TestAcceptanceMatrix:
    def test_matrix_has_happy_path_scenarios(self):
        """Matriz deve conter cenários de happy path."""
        matrix = AcceptanceMatrix()
        matrix.add_scenario("happy", "US-01", "motor inicia com hipótese válida")
        scenarios = matrix.get_scenarios("happy")
        assert len(scenarios) == 1
        assert scenarios[0]["description"] == "motor inicia com hipótese válida"

    def test_matrix_has_edge_case_scenarios(self):
        """Matriz deve conter cenários de edge cases."""
        matrix = AcceptanceMatrix()
        matrix.add_scenario("edge", "US-01", "hipótese com process_id já existente")
        scenarios = matrix.get_scenarios("edge")
        assert len(scenarios) == 1

    def test_matrix_has_error_scenarios(self):
        """Matriz deve conter cenários de erro."""
        matrix = AcceptanceMatrix()
        matrix.add_scenario("error", "US-01", "state YAML corrompido")
        scenarios = matrix.get_scenarios("error")
        assert len(scenarios) == 1

    def test_matrix_categories_are_happy_edge_error(self):
        """Categorias válidas são apenas happy, edge e error."""
        matrix = AcceptanceMatrix()
        with pytest.raises(ValueError, match="categoria|category|inválido"):
            matrix.add_scenario("unknown_category", "US-01", "descrição")

    def test_matrix_all_scenarios_returns_all_types(self):
        """all_scenarios() deve retornar cenários de todas as categorias."""
        matrix = AcceptanceMatrix()
        matrix.add_scenario("happy", "US-01", "happy path básico")
        matrix.add_scenario("edge", "US-01", "edge case de ciclo")
        matrix.add_scenario("error", "US-01", "erro de estado")

        all_scenarios = matrix.all_scenarios()
        categories = {s["category"] for s in all_scenarios}
        assert "happy" in categories
        assert "edge" in categories
        assert "error" in categories

    def test_matrix_scenario_has_required_fields(self):
        """Cada cenário deve ter id, category, user_story, description."""
        matrix = AcceptanceMatrix()
        matrix.add_scenario("happy", "US-02", "TDD executa red→green")
        scenarios = matrix.get_scenarios("happy")
        s = scenarios[0]
        assert "id" in s
        assert "category" in s
        assert "user_story" in s
        assert "description" in s

    def test_matrix_scenario_ids_are_unique(self):
        """IDs dos cenários devem ser únicos."""
        matrix = AcceptanceMatrix()
        matrix.add_scenario("happy", "US-01", "cenário 1")
        matrix.add_scenario("happy", "US-01", "cenário 2")
        scenarios = matrix.get_scenarios("happy")
        ids = [s["id"] for s in scenarios]
        assert len(ids) == len(set(ids))

    def test_matrix_to_dict_is_serializable(self):
        """to_dict() deve retornar estrutura serializável."""
        matrix = AcceptanceMatrix()
        matrix.add_scenario("happy", "US-01", "test")
        d = matrix.to_dict()
        import json
        serialized = json.dumps(d)  # Não deve lançar exceção
        assert serialized is not None

    def test_empty_matrix_has_no_scenarios(self):
        """Matriz vazia não deve ter cenários."""
        matrix = AcceptanceMatrix()
        assert matrix.all_scenarios() == []
        assert matrix.total_count() == 0

    def test_matrix_count_per_category(self):
        """count_by_category() deve retornar contagem por tipo."""
        matrix = AcceptanceMatrix()
        matrix.add_scenario("happy", "US-01", "h1")
        matrix.add_scenario("happy", "US-01", "h2")
        matrix.add_scenario("edge", "US-01", "e1")
        matrix.add_scenario("error", "US-01", "err1")
        matrix.add_scenario("error", "US-01", "err2")

        counts = matrix.count_by_category()
        assert counts["happy"] == 2
        assert counts["edge"] == 1
        assert counts["error"] == 2
