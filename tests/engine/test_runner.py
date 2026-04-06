"""Unit tests for ft.engine.runner (LLM mocked)."""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from ft.engine.graph import load_graph
from ft.engine.runner import StepRunner, run_validators, ValidationResult, build_task_prompt
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

    def test_explicit_write_scope_overrides_output_derived_paths(self, runner_v2):
        from ft.engine.graph import Node

        node = Node(
            id="x",
            type="build",
            title="X",
            outputs=["project/docs/report.md"],
            write_scope=["main.py", "project/docs/"],
        )
        assert runner_v2._resolve_allowed_paths(node) == ["main.py", "project/docs/"]

    def test_init_cleans_validator_snapshots(self, tmp_path):
        project_root = tmp_path / "project_root"
        project_root.mkdir()
        stale_snapshot = project_root / "project" / "state" / "prd_rewrite_baseline.md"
        stale_snapshot.parent.mkdir(parents=True)
        stale_snapshot.write_text("stale")

        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: test_process
version: "0.1.0"
title: "Test"
nodes:
  - id: ft.prd.rewrite
    type: document
    title: Rewrite
    executor: llm_coach
    outputs:
      - project/docs/PRD.md
    validators:
      - sections_unchanged:
          path: project/docs/PRD.md
          snapshot_path: project/state/prd_rewrite_baseline.md
          sections:
            - Hipotese
    next: ft.end
  - id: ft.end
    type: end
    title: End
"""
        )

        runner = StepRunner(
            process_path=process_path,
            state_path=project_root / "project" / "state" / "engine_state.yml",
            project_root=project_root,
        )

        runner.init_state()

        assert not stale_snapshot.exists()


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


class TestRewriteGuard:
    def test_rewrite_node_with_immutable_sections_still_delegates(self, tmp_path):
        project_root = tmp_path / "project_root"
        docs = project_root / "project" / "docs"
        docs.mkdir(parents=True)
        (docs / "PRD.md").write_text(
            "# PRD\n\n## Hipotese\nBase.\n\n## Visao\nBase.\n\n## User Stories\n### US-01\nBase.\n"
        )

        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: test_process
version: "0.1.0"
title: "Test"
nodes:
  - id: ft.prd.rewrite
    type: document
    title: Rewrite
    executor: llm_coach
    outputs:
      - project/docs/PRD.md
    validators:
      - sections_unchanged:
          path: project/docs/PRD.md
          snapshot_path: project/state/prd_rewrite_baseline.md
          sections:
            - Hipotese
            - Visao
            - User Stories
    next: ft.end
  - id: ft.end
    type: end
    title: End
"""
        )

        runner = StepRunner(
            process_path=process_path,
            state_path=project_root / "project" / "state" / "engine_state.yml",
            project_root=project_root,
        )
        runner.init_state()
        node = runner.graph.get_node("ft.prd.rewrite")

        with patch(
            "ft.engine.runner.delegate_to_llm",
            return_value=DelegateResult(success=True, output="DONE", files_created=[], files_modified=[]),
        ) as delegate_mock:
            runner._run_llm_step(node)

        assert delegate_mock.called
        assert not (project_root / "project" / "state" / "prd_rewrite_baseline.md").exists()


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

    def test_status_shows_active_llm_log(self, runner_v2, capsys):
        runner_v2.init_state()
        state = runner_v2.state_mgr.load()
        state.node_status = "delegated"
        state.active_llm_log = "project/state/llm_logs/current.jsonl"
        state.last_llm_log = "project/state/llm_logs/last.jsonl"
        runner_v2.state_mgr.save()

        runner_v2.status()
        out = capsys.readouterr().out
        assert "LLM log ativo" in out
        assert "project/state/llm_logs/current.jsonl" in out

    def test_status_syncs_process_version_from_graph(self, runner_v2, capsys):
        runner_v2.init_state()
        state = runner_v2.state_mgr.load()
        state.version = "0.1.0"
        runner_v2.state_mgr.save()

        runner_v2.status()
        out = capsys.readouterr().out
        assert "v0.2.0" in out

        refreshed = runner_v2.state_mgr.load()
        assert refreshed.version == "0.2.0"

    def test_status_recomputes_progress_without_counting_end_node(self, runner_v2, capsys):
        runner_v2.init_state()
        runner_v2._advance_state("step.01.hipotese", "step.02.prd")
        runner_v2._advance_state("step.02.prd", "gate.01.discovery")
        runner_v2._advance_state("gate.01.discovery", "step.03.implementacao")
        runner_v2._advance_state("step.03.implementacao", "gate.02.delivery")
        runner_v2._advance_state("gate.02.delivery", "step.05.done")
        runner_v2._advance_state("step.05.done", None)

        runner_v2.status()
        out = capsys.readouterr().out

        assert "Progresso: 5/5" in out
        refreshed = runner_v2.state_mgr.load()
        assert refreshed.metrics["steps_completed"] == 5

    def test_status_backfills_inserted_decision_nodes_when_branch_already_traversed(self, tmp_path, capsys):
        project_root = tmp_path / "project_root"
        project_root.mkdir()

        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: test_process
version: "0.2.0"
title: "Decision Backfill"
nodes:
  - id: step.01
    type: build
    title: Step 01
    executor: llm_coder
    outputs:
      - src/one.py
    next: decision.01
  - id: decision.01
    type: decision
    title: Decide
    executor: python
    condition: interface_type
    branches:
      ui: step.02
      _default: step.02
    next: step.02
  - id: step.02
    type: gate
    title: Step 02
    executor: python
    next: ft.end
  - id: ft.end
    type: end
    title: End
"""
        )

        runner = StepRunner(
            process_path=process_path,
            state_path=project_root / "project" / "state" / "engine_state.yml",
            project_root=project_root,
        )
        runner.init_state()

        state = runner.state_mgr.load()
        state.version = "0.1.0"
        state.completed_nodes = ["step.01", "step.02", "ft.end"]
        state.gate_log = {"step.01": "PASS", "step.02": "PASS", "ft.end": "PASS"}
        state.artifacts["interface_type"] = "ui"
        state.current_node = None
        state.node_status = "done"
        state.metrics["steps_completed"] = 2
        state.metrics["steps_total"] = 2
        runner.state_mgr.save()

        runner.status()
        out = capsys.readouterr().out

        assert "Progresso: 3/3" in out
        refreshed = runner.state_mgr.load()
        assert refreshed.version == "0.2.0"
        assert "decision.01" in refreshed.completed_nodes
        assert refreshed.completed_nodes == ["step.01", "decision.01", "step.02", "ft.end"]
        assert refreshed.gate_log["decision.01"] == "PASS"
        assert refreshed.metrics["steps_completed"] == 3
        assert refreshed.metrics["steps_total"] == 3


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

    def test_gate_can_recover_from_blocked_state(self, tmp_path):
        """Gate reexecutado com sucesso deve limpar o bloqueio e avançar."""
        (Path(".") / "project/docs/hipotese.md").write_text("x" * 100)
        (Path(".") / "project/docs/PRD.md").write_text("x" * 100)

        runner = StepRunner(
            process_path="process/test_process_v2.yml",
            state_path=tmp_path / "state.yml",
            project_root=".",
        )
        runner.init_state()
        runner.state_mgr.advance("step.01.hipotese", "step.02.prd")
        runner.state_mgr.advance("step.02.prd", "gate.01.discovery")
        runner.state_mgr.block("falha antiga")

        node = runner.graph.get_node("gate.01.discovery")
        runner._run_gate(node)

        state = runner.state_mgr.load()
        assert state.node_status == "ready"
        assert state.blocked_reason is None
        assert state.current_node == "step.03.implementacao"
        assert state.gate_log["gate.01.discovery"] == "PASS"


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

    def test_sections_unchanged_validator_supports_dict_args(self, tmp_path):
        from ft.engine.graph import Node

        docs = tmp_path / "project" / "docs"
        state = tmp_path / "project" / "state"
        docs.mkdir(parents=True)
        state.mkdir(parents=True)
        (docs / "PRD.md").write_text(
            "# PRD\n\n## Hipotese\nBase.\n\n## Visao\nBase.\n\n## User Stories\n### US-01\nBase.\n"
        )
        (state / "prd_rewrite_baseline.md").write_text(
            "# PRD\n\n## Hipotese\nBase.\n\n## Visao\nBase.\n\n## User Stories\n### US-01\nBase.\n"
        )

        node = Node(
            id="ft.prd.rewrite",
            type="document",
            title="Rewrite",
            executor="llm_coach",
            outputs=["project/docs/PRD.md"],
            validators=[{
                "sections_unchanged": {
                    "path": "project/docs/PRD.md",
                    "snapshot_path": "project/state/prd_rewrite_baseline.md",
                    "sections": ["Hipotese", "Visao", "User Stories"],
                }
            }],
        )

        result = run_validators(node, str(tmp_path))

        assert result.passed


# ---------------------------------------------------------------------------
# build_task_prompt
# ---------------------------------------------------------------------------

class TestBuildTaskPrompt:
    def test_retro_prompt_reads_project_log_without_self(self, tmp_path):
        project_root = tmp_path / "pokemon"
        project_root.mkdir()
        (project_root / "pokemon_log.md").write_text("# Run Log\nretro input\n")

        from ft.engine.graph import Node

        node = Node(
            id="retro.01",
            type="retro",
            title="Retro",
            outputs=["project/docs/retro.md"],
        )

        prompt = build_task_prompt(node, {"_project_root": str(project_root)})

        assert "retro input" in prompt
        assert "project/docs/retro.md" in prompt
