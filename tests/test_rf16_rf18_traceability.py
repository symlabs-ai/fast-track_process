"""
RED PHASE — RF-16 a RF-18: Rastreabilidade.

RF-16: artifact registrado em `artifacts` map após produção
RF-17: gate_log preserva histórico acumulado
RF-18: sessões de agentes salvas em project/docs/sessions/
"""

import pytest
from pathlib import Path

from ft.engine.state import StateManager
from ft.engine.session_tracker import SessionTracker


# ---------------------------------------------------------------------------
# RF-16: Artifacts registrados no estado
# ---------------------------------------------------------------------------

class TestArtifactRegistry:
    def test_record_artifact_persists(self, tmp_path):
        """record_artifact() deve persistir o artifact no YAML."""
        mgr = StateManager(tmp_path / "state.yml")
        mgr.init_from_graph({"id": "proc-001"}, "node.01", 5)
        mgr.record_artifact("PRD", "project/docs/PRD.md")
        state = mgr.load()
        assert state.artifacts["PRD"] == "project/docs/PRD.md"

    def test_multiple_artifacts_stored(self, tmp_path):
        """Múltiplos artifacts devem ser armazenados."""
        mgr = StateManager(tmp_path / "state.yml")
        mgr.init_from_graph({"id": "proc-001"}, "node.01", 5)
        mgr.record_artifact("hipotese", "project/docs/hipotese.md")
        mgr.record_artifact("PRD", "project/docs/PRD.md")
        mgr.record_artifact("TASK_LIST", "project/docs/TASK_LIST.md")
        state = mgr.load()
        assert len(state.artifacts) == 3
        assert "hipotese" in state.artifacts
        assert "PRD" in state.artifacts
        assert "TASK_LIST" in state.artifacts

    def test_artifact_name_empty_raises(self, tmp_path):
        """record_artifact() com nome vazio deve ser rejeitado."""
        mgr = StateManager(tmp_path / "state.yml")
        mgr.init_from_graph({"id": "proc-001"}, "node.01", 5)
        with pytest.raises(ValueError, match="nome|name|artifact"):
            mgr.record_artifact("", "project/docs/PRD.md")

    def test_artifact_path_empty_raises(self, tmp_path):
        """record_artifact() com path vazio deve ser rejeitado."""
        mgr = StateManager(tmp_path / "state.yml")
        mgr.init_from_graph({"id": "proc-001"}, "node.01", 5)
        with pytest.raises(ValueError, match="path|caminho|artifact"):
            mgr.record_artifact("PRD", "")

    def test_artifact_overwrite_updates_path(self, tmp_path):
        """record_artifact() com mesmo nome deve atualizar o path."""
        mgr = StateManager(tmp_path / "state.yml")
        mgr.init_from_graph({"id": "proc-001"}, "node.01", 5)
        mgr.record_artifact("PRD", "project/docs/PRD.md")
        mgr.record_artifact("PRD", "project/docs/PRD_v2.md")
        state = mgr.load()
        assert state.artifacts["PRD"] == "project/docs/PRD_v2.md"

    def test_list_artifacts_returns_all(self, tmp_path):
        """list_artifacts() deve retornar dict com todos os artifacts."""
        mgr = StateManager(tmp_path / "state.yml")
        mgr.init_from_graph({"id": "proc-001"}, "node.01", 5)
        mgr.record_artifact("prd", "project/docs/PRD.md")
        mgr.record_artifact("task_list", "project/docs/TASK_LIST.md")
        artifacts = mgr.list_artifacts()
        assert isinstance(artifacts, dict)
        assert len(artifacts) == 2

    def test_artifacts_survive_advance(self, tmp_path):
        """Artifacts devem ser preservados após advance()."""
        mgr = StateManager(tmp_path / "state.yml")
        mgr.init_from_graph({"id": "proc-001"}, "node.01", 5)
        mgr.record_artifact("PRD", "project/docs/PRD.md")
        mgr.advance("node.01", "node.02")
        state = mgr.load()
        assert "PRD" in state.artifacts


# ---------------------------------------------------------------------------
# RF-17: gate_log preserva histórico acumulado
# ---------------------------------------------------------------------------

class TestGateLogHistory:
    def test_gate_log_records_pass(self, tmp_path):
        """gate_log deve registrar PASS para cada node que avançou."""
        mgr = StateManager(tmp_path / "state.yml")
        mgr.init_from_graph({"id": "proc-001"}, "node.01", 5)
        mgr.advance("node.01", "node.02")
        state = mgr.load()
        assert state.gate_log["node.01"] == "PASS"

    def test_gate_log_accumulates_all_entries(self, tmp_path):
        """gate_log deve acumular todas as entradas ao longo do processo."""
        mgr = StateManager(tmp_path / "state.yml")
        mgr.init_from_graph({"id": "proc-001"}, "node.01", 5)
        mgr.advance("node.01", "node.02")
        mgr.advance("node.02", "node.03")
        mgr.advance("node.03", None)
        state = mgr.load()
        assert "node.01" in state.gate_log
        assert "node.02" in state.gate_log
        assert "node.03" in state.gate_log

    def test_gate_log_block_entry_preserved(self, tmp_path):
        """gate_log deve preservar entradas BLOCK mesmo após resolução."""
        mgr = StateManager(tmp_path / "state.yml")
        mgr.init_from_graph({"id": "proc-001"}, "node.01", 5)
        mgr.block_gate("node.01", "lint falhou")
        state = mgr.load()
        assert state.gate_log["node.01"] == "BLOCK"

    def test_gate_log_does_not_lose_history_on_reload(self, tmp_path):
        """Histórico do gate_log não deve ser perdido após reload."""
        path = tmp_path / "state.yml"
        mgr1 = StateManager(path)
        mgr1.init_from_graph({"id": "proc-001"}, "node.01", 5)
        mgr1.advance("node.01", "node.02")

        mgr2 = StateManager(path)
        state = mgr2.load()
        assert "node.01" in state.gate_log
        assert state.gate_log["node.01"] == "PASS"

    def test_gate_log_order_preserved(self, tmp_path):
        """Ordem cronológica das entradas deve ser preservada."""
        mgr = StateManager(tmp_path / "state.yml")
        mgr.init_from_graph({"id": "proc-001"}, "node.01", 5)
        mgr.advance("node.01", "node.02")
        mgr.advance("node.02", "node.03")
        state = mgr.load()
        keys = list(state.gate_log.keys())
        assert keys.index("node.01") < keys.index("node.02")

    def test_gate_log_is_auditable(self, tmp_path):
        """gate_log deve ser auditável: cada entrada tem node e resultado."""
        mgr = StateManager(tmp_path / "state.yml")
        mgr.init_from_graph({"id": "proc-001"}, "node.01", 5)
        mgr.advance("node.01", "node.02")
        state = mgr.load()
        for node_id, result in state.gate_log.items():
            assert isinstance(node_id, str) and len(node_id) > 0
            assert result in ("PASS", "BLOCK")

    def test_gate_log_full_audit_trail(self, tmp_path):
        """gate_log deve conter auditoria completa: todos os nodes processados."""
        mgr = StateManager(tmp_path / "state.yml")
        mgr.init_from_graph({"id": "proc-001"}, "node.01", 5)
        nodes = ["node.01", "node.02", "node.03", "node.04"]
        for i, node in enumerate(nodes[:-1]):
            mgr.advance(node, nodes[i + 1])
        mgr.advance("node.04", None)

        state = mgr.load()
        assert len(state.gate_log) == 4
        assert all(v == "PASS" for v in state.gate_log.values())


# ---------------------------------------------------------------------------
# RF-18: Sessões de agentes em project/docs/sessions/
# ---------------------------------------------------------------------------

class TestSessionTracker:
    def test_session_saved_on_delegate(self, tmp_path):
        """Sessão deve ser salva quando agente é delegado."""
        sessions_dir = tmp_path / "project" / "docs" / "sessions"
        tracker = SessionTracker(sessions_dir)
        session_id = tracker.start_session(agent="forge_coder", node="ft.tdd.02.red")
        assert sessions_dir.exists()
        assert session_id is not None

    def test_session_file_created(self, tmp_path):
        """Arquivo de sessão deve ser criado em sessions/."""
        sessions_dir = tmp_path / "project" / "docs" / "sessions"
        tracker = SessionTracker(sessions_dir)
        session_id = tracker.start_session(agent="ft_coach", node="ft.mdd.01.hipotese")
        session_files = list(sessions_dir.glob("*.yml"))
        assert len(session_files) >= 1

    def test_session_contains_agent_and_node(self, tmp_path):
        """Arquivo de sessão deve conter agente e node."""
        sessions_dir = tmp_path / "project" / "docs" / "sessions"
        tracker = SessionTracker(sessions_dir)
        session_id = tracker.start_session(agent="forge_coder", node="ft.tdd.03.green")
        session_data = tracker.get_session(session_id)
        assert session_data["agent"] == "forge_coder"
        assert session_data["node"] == "ft.tdd.03.green"

    def test_session_has_timestamp(self, tmp_path):
        """Sessão deve ter timestamp de início."""
        sessions_dir = tmp_path / "project" / "docs" / "sessions"
        tracker = SessionTracker(sessions_dir)
        session_id = tracker.start_session(agent="ft_manager", node="node.01")
        session_data = tracker.get_session(session_id)
        assert "started_at" in session_data
        assert session_data["started_at"] is not None

    def test_session_end_records_completion(self, tmp_path):
        """end_session() deve registrar timestamp de conclusão."""
        sessions_dir = tmp_path / "project" / "docs" / "sessions"
        tracker = SessionTracker(sessions_dir)
        session_id = tracker.start_session(agent="forge_coder", node="ft.tdd.02.red")
        tracker.end_session(session_id, status="completed")
        session_data = tracker.get_session(session_id)
        assert session_data["status"] == "completed"
        assert "ended_at" in session_data

    def test_list_sessions_returns_all(self, tmp_path):
        """list_sessions() deve retornar todas as sessões registradas."""
        sessions_dir = tmp_path / "project" / "docs" / "sessions"
        tracker = SessionTracker(sessions_dir)
        tracker.start_session(agent="forge_coder", node="node.01")
        tracker.start_session(agent="ft_coach", node="node.02")
        sessions = tracker.list_sessions()
        assert len(sessions) == 2

    def test_sessions_dir_under_project_docs(self, tmp_path):
        """Sessions dir deve ser subdiretório de project/docs/sessions/."""
        base = tmp_path / "project" / "docs" / "sessions"
        tracker = SessionTracker(base)
        tracker.start_session(agent="ft_manager", node="node.01")
        # Verifica que os arquivos estão dentro do diretório correto
        assert base.exists()
        assert any(base.iterdir())

    def test_session_for_forge_coder_in_subdir(self, tmp_path):
        """Sessions de forge_coder devem estar no subdiretório correto."""
        base = tmp_path / "project" / "docs" / "sessions"
        tracker = SessionTracker(base)
        tracker.start_session(agent="forge_coder", node="ft.tdd.02.red")
        forge_dir = base / "forge_coder"
        assert forge_dir.exists()

    def test_session_persists_across_instances(self, tmp_path):
        """Sessão salva deve ser recuperável por nova instância do tracker."""
        sessions_dir = tmp_path / "project" / "docs" / "sessions"
        t1 = SessionTracker(sessions_dir)
        session_id = t1.start_session(agent="ft_coach", node="node.mdd")

        t2 = SessionTracker(sessions_dir)
        session_data = t2.get_session(session_id)
        assert session_data is not None
        assert session_data["agent"] == "ft_coach"

    def test_nonexistent_session_returns_none(self, tmp_path):
        """get_session() para ID inexistente deve retornar None."""
        sessions_dir = tmp_path / "project" / "docs" / "sessions"
        tracker = SessionTracker(sessions_dir)
        result = tracker.get_session("session-nonexistent-999")
        assert result is None
