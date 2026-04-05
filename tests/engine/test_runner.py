"""Unit tests for ft.engine.runner (LLM mocked)."""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from ft.engine.graph import load_graph
from ft.engine.runner import StepRunner, run_validators, ValidationResult
from ft.engine.delegate import DelegateResult


@pytest.fixture
def runner_v2(tmp_path):
    """Runner using test_process_v2.yml with a temp state."""
    return StepRunner(
        process_path="process/test_process_v2.yml",
        state_path=tmp_path / "state.yml",
        project_root=".",
    )


@pytest.fixture
def runner_tdd(tmp_path):
    """Runner using test_process_v3_tdd.yml."""
    return StepRunner(
        process_path="process/test_process_v3_tdd.yml",
        state_path=tmp_path / "state.yml",
        project_root=".",
    )


# ---------------------------------------------------------------------------
# init_state
# ---------------------------------------------------------------------------

class TestInitState:
    def test_init_sets_first_node(self, runner_v2):
        runner_v2.init_state()
        state = runner_v2.state_mgr.load()
        assert state.current_node == "step.01.hipotese"
        assert state.node_status == "ready"

    def test_init_sets_total_steps(self, runner_v2):
        runner_v2.init_state()
        state = runner_v2.state_mgr.load()
        assert state.metrics["steps_total"] == 5

    def test_init_persists_selected_llm_engine(self, tmp_path):
        runner = StepRunner(
            process_path="process/test_process_v2.yml",
            state_path=tmp_path / "state.yml",
            project_root=".",
            llm_engine="codex",
        )
        runner.init_state()
        state = runner.state_mgr.load()
        assert state.llm_engine == "codex"


# ---------------------------------------------------------------------------
# approve / reject
# ---------------------------------------------------------------------------

class TestApproveReject:
    def test_approve_advances_node(self, runner_v2):
        runner_v2.init_state()
        runner_v2.state_mgr.set_pending_approval("step.01.hipotese")
        runner_v2.approve()
        state = runner_v2.state_mgr.load()
        assert state.current_node == "step.02.prd"
        assert "step.01.hipotese" in state.completed_nodes

    def test_approve_when_nothing_pending(self, runner_v2, capsys):
        runner_v2.init_state()
        runner_v2.approve()
        out = capsys.readouterr().out
        assert "pendente" in out.lower()

    def test_reject_no_retry_blocks(self, runner_v2):
        runner_v2.init_state()
        runner_v2.state_mgr.set_pending_approval("step.01.hipotese")
        runner_v2.reject("motivo de teste", retry=False)
        state = runner_v2.state_mgr.load()
        assert state.node_status == "blocked"
        assert "Rejeitado" in state.blocked_reason


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

class TestStatus:
    def test_status_shows_current_node(self, runner_v2, capsys):
        runner_v2.init_state()
        runner_v2.status()
        out = capsys.readouterr().out
        assert "step.01.hipotese" in out

    def test_status_full_shows_sprints(self, runner_v2, capsys):
        runner_v2.init_state()
        runner_v2.status(full=True)
        out = capsys.readouterr().out
        assert "sprint-01-discovery" in out
        assert "sprint-02-build" in out

    def test_status_shows_blocked_reason(self, runner_v2, capsys):
        runner_v2.init_state()
        runner_v2.state_mgr.block("test block reason")
        runner_v2.status()
        out = capsys.readouterr().out
        assert "test block reason" in out


# ---------------------------------------------------------------------------
# _run_gate
# ---------------------------------------------------------------------------

class TestRunGate:
    def test_gate_passes_when_files_exist(self, tmp_path):
        """Gate PASS when required files exist."""
        # Create required files
        (Path(".") / "project/docs/hipotese.md").write_text("x" * 100)
        (Path(".") / "project/docs/PRD.md").write_text("x" * 100)

        runner = StepRunner(
            process_path="process/test_process_v2.yml",
            state_path=tmp_path / "state.yml",
            project_root=".",
        )
        runner.init_state()
        # Manually advance to gate node
        runner.state_mgr.advance("step.01.hipotese", "step.02.prd")
        runner.state_mgr.advance("step.02.prd", "gate.01.discovery")

        node = runner.graph.get_node("gate.01.discovery")
        runner._run_gate(node)
        state = runner.state_mgr.load()
        assert state.node_status == "ready"
        assert "gate.01.discovery" in state.completed_nodes


# ---------------------------------------------------------------------------
# run_validators
# ---------------------------------------------------------------------------

class TestRunValidators:
    def test_no_validators_passes(self):
        from ft.engine.graph import Node
        node = Node(id="x", type="build", title="X")
        result = run_validators(node, ".")
        assert result.passed
        assert result.items == []

    def test_file_exists_validator(self, tmp_path):
        from ft.engine.graph import Node
        f = tmp_path / "test.txt"
        f.write_text("content")
        node = Node(
            id="x", type="build", title="X",
            validators=[{"file_exists": "test.txt"}],
        )
        result = run_validators(node, str(tmp_path))
        assert result.passed

    def test_failing_validator_not_passed(self, tmp_path):
        from ft.engine.graph import Node
        node = Node(
            id="x", type="build", title="X",
            validators=[{"file_exists": "missing.txt"}],
        )
        result = run_validators(node, str(tmp_path))
        assert not result.passed
        assert result.feedback is not None

    def test_multiple_validators_all_must_pass(self, tmp_path):
        from ft.engine.graph import Node
        f = tmp_path / "test.txt"
        f.write_text("line1\nline2")
        node = Node(
            id="x", type="build", title="X",
            outputs=["test.txt"],
            validators=[
                {"file_exists": "test.txt"},
                {"min_lines": 10},  # will fail
            ],
        )
        result = run_validators(node, str(tmp_path))
        assert not result.passed
        assert len(result.items) == 2
        assert result.items[0].passed
        assert not result.items[1].passed

    def test_retryable_when_llm_executor(self, tmp_path):
        from ft.engine.graph import Node
        node = Node(
            id="x", type="build", title="X",
            executor="llm_coder",
            validators=[{"file_exists": "missing.txt"}],
        )
        result = run_validators(node, str(tmp_path))
        assert result.retryable

    def test_not_retryable_when_python_executor(self, tmp_path):
        from ft.engine.graph import Node
        node = Node(
            id="x", type="gate", title="X",
            executor="python",
            validators=[{"file_exists": "missing.txt"}],
        )
        result = run_validators(node, str(tmp_path))
        assert not result.retryable
