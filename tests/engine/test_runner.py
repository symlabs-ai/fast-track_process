"""Unit tests for ft.engine.runner (LLM mocked)."""

import json

import pytest
from pathlib import Path
from unittest.mock import patch

from ft.engine import ui
from ft.engine.api_context import (
    enrich_api_contract_feedback,
    extract_api_endpoint_candidates,
)
from ft.engine.graph import load_graph
from ft.engine.runner import (
    StepRunner,
    run_validators,
    ValidationResult,
    build_task_prompt,
    _brief_cycle_objective,
    _llm_progress_snapshot,
    _parse_review_verdict,
)
from ft.engine.delegate import DelegateResult
from ft.engine.state import EngineState


_TEST_PROCESS_V2_YAML = """\
id: test_process_v2
version: "0.2.0"
title: "Processo de Teste v2 (Sprints)"
nodes:
  - id: step.01.hipotese
    type: discovery
    title: "Hipotese do produto"
    executor: llm_coach
    sprint: sprint-01-discovery
    outputs:
      - project/docs/hipotese.md
    requires_approval: true
    validators:
      - file_exists: project/docs/hipotese.md
      - min_lines: 5
    next: step.02.prd
  - id: step.02.prd
    type: document
    title: "PRD simplificado"
    executor: llm_coach
    sprint: sprint-01-discovery
    outputs:
      - project/docs/PRD.md
    validators:
      - file_exists: project/docs/PRD.md
      - has_sections:
          - Hipotese
          - Visao
          - User Stories
      - min_lines: 20
    next: gate.01.discovery
  - id: gate.01.discovery
    type: gate
    title: "Gate de discovery"
    executor: python
    sprint: sprint-01-discovery
    validators:
      - file_exists: project/docs/hipotese.md
      - file_exists: project/docs/PRD.md
    next: step.03.implementacao
  - id: step.03.implementacao
    type: build
    title: "Implementar funcionalidade basica"
    executor: llm_coder
    sprint: sprint-02-build
    outputs:
      - src/main.py
    validators:
      - file_exists: src/main.py
      - tests_pass: true
    next: gate.02.delivery
  - id: gate.02.delivery
    type: gate
    title: "Gate de delivery"
    executor: python
    sprint: sprint-02-build
    validators:
      - gate_delivery: true
    outputs:
      - src/main.py
    next: step.05.done
  - id: step.05.done
    type: end
    title: "Processo completo"
"""


@pytest.fixture
def runner_v2(tmp_path):
    """Runner with inline v2-style process (sprints + gates)."""
    process_path = tmp_path / "process.yml"
    process_path.write_text(_TEST_PROCESS_V2_YAML)
    return StepRunner(
        process_path=process_path,
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

    def test_init_persists_brief_objective_from_template_input(
        self, tmp_path, capsys
    ):
        root = tmp_path / "project"
        request = root / "docs" / "feature-request.md"
        request.parent.mkdir(parents=True)
        request.write_text(
            "# Feature\n\nAdicionar filtro por período ao relatório de vendas.\n",
            encoding="utf-8",
        )
        process_path = root / "process.yml"
        process_path.write_text(
            """
id: feature
version: "1.3.0"
title: Feature
input_policy:
  required: true
  destination: docs/feature-request.md
  prompt: Descreva a feature
nodes:
  - id: feature.discovery
    type: discovery
    title: Discovery
    next: feature.end
  - id: feature.end
    type: end
    title: End
""",
            encoding="utf-8",
        )
        runner = StepRunner(
            process_path=process_path,
            state_path=root / "state" / "engine_state.yml",
            project_root=root,
        )

        runner.init_state()

        state = runner.state_mgr.load()
        assert state.cycle_objective == (
            "Adicionar filtro por período ao relatório de vendas."
        )

        # Estados criados por versões anteriores não têm o novo campo; o
        # status ainda recupera a demanda pinada dentro da worktree.
        state.cycle_objective = None
        runner.state_mgr.save()
        capsys.readouterr()

        runner.status()

        assert (
            "Objetivo do Ciclo: Adicionar filtro por período ao relatório de "
            "vendas."
        ) in capsys.readouterr().out

    def test_cycle_objective_is_single_line_and_bounded(self):
        objective = _brief_cycle_objective(
            "## Demanda\n- Implementar uma alteração " + ("muito detalhada " * 30)
        )

        assert objective is not None
        assert "\n" not in objective
        assert len(objective) <= 160
        assert objective.endswith("…")

    def test_init_persists_selected_llm_engine(self, tmp_path):
        process_path = tmp_path / "process.yml"
        process_path.write_text(_TEST_PROCESS_V2_YAML)
        runner = StepRunner(
            process_path=process_path,
            state_path=tmp_path / "state.yml",
            project_root=".",
            llm_engine="codex",
        )
        runner.init_state()
        state = runner.state_mgr.load()
        assert state.llm_engine == "codex"

    def test_init_persists_selected_model_and_effort(self, tmp_path):
        process_path = tmp_path / "process.yml"
        process_path.write_text(_TEST_PROCESS_V2_YAML)
        runner = StepRunner(
            process_path=process_path,
            state_path=tmp_path / "state.yml",
            project_root=".",
            llm_engine="claude",
            llm_model="fable",
            llm_effort="max",
        )

        runner.init_state()

        state = runner.state_mgr.load()
        assert state.llm_model == "fable"
        assert state.llm_effort == "max"

    def test_node_effort_overrides_run_effort(self, tmp_path):
        process_path = tmp_path / "process.yml"
        process_path.write_text(_TEST_PROCESS_V2_YAML)
        runner = StepRunner(
            process_path=process_path,
            state_path=tmp_path / "state.yml",
            project_root=".",
            llm_effort="high",
        )
        node = runner.graph.first_node()
        node.llm_effort = "max"

        assert runner._resolve_llm_effort(EngineState(llm_effort="low"), node) == "max"

    def test_explicit_default_effort_clears_active_state_effort(self, tmp_path):
        process_path = tmp_path / "process.yml"
        process_path.write_text(_TEST_PROCESS_V2_YAML)
        runner = StepRunner(
            process_path=process_path,
            state_path=tmp_path / "state.yml",
            project_root=".",
            llm_effort="default",
        )
        state = EngineState(llm_effort="max")

        assert runner._resolve_llm_effort(state) is None

    def test_node_default_effort_clears_inherited_effort(self, tmp_path):
        process_path = tmp_path / "process.yml"
        process_path.write_text(_TEST_PROCESS_V2_YAML)
        runner = StepRunner(
            process_path=process_path,
            state_path=tmp_path / "state.yml",
            project_root=".",
            llm_effort="high",
        )
        node = runner.graph.first_node()
        node.llm_effort = "default"

        assert runner._resolve_llm_effort(EngineState(llm_effort="max"), node) is None

    def test_explicit_write_scope_overrides_output_derived_paths(self, runner_v2):
        from ft.engine.graph import Node

        node = Node(
            id="x",
            type="build",
            title="X",
            outputs=["docs/report.md"],
            write_scope=["main.py", "docs/"],
        )
        assert runner_v2._resolve_allowed_paths(node) == ["main.py", "docs/"]

    def test_init_cleans_validator_snapshots(self, tmp_path):
        project_root = tmp_path / "project_root"
        project_root.mkdir()
        state_dir = project_root / "runs" / "01" / "state"
        stale_snapshot = state_dir / "prd_rewrite_baseline.md"
        state_dir.mkdir(parents=True)
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
      - docs/PRD.md
    validators:
      - sections_unchanged:
          path: docs/PRD.md
          snapshot_path: prd_rewrite_baseline.md
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
            state_path=state_dir / "engine_state.yml",
            project_root=project_root,
        )

        runner.init_state()

        assert not stale_snapshot.exists()


class TestRecoverOrphanedDelegation:
    @staticmethod
    def _orphan_build(runner):
        runner.init_state()
        state = runner.state_mgr.load()
        state.current_node = "step.03.implementacao"
        state.node_status = "delegated"
        state._lock = {"pid": 999999}
        runner.state_mgr.save()
        return state

    def test_pass_finalizes_existing_artifacts_without_llm(self, runner_v2):
        self._orphan_build(runner_v2)

        with (
            patch.object(runner_v2.state_mgr, "_is_pid_alive", return_value=False),
            patch("ft.engine.runner.run_validators") as validators,
            patch.object(runner_v2, "_maybe_auto_commit") as auto_commit,
            patch.object(runner_v2, "_record_node_summary") as record_summary,
            patch("ft.engine.runner.delegate_to_llm") as delegate,
        ):
            validators.return_value = ValidationResult(True, False, None, [])

            recovered = runner_v2.recover_orphaned_delegation(mode="mvp")

        assert recovered is True
        delegate.assert_not_called()
        assert validators.call_args.kwargs["resume"] is True
        auto_commit.assert_called_once()
        record_summary.assert_called_once()
        state = runner_v2.state_mgr.load()
        assert state.current_node == "gate.02.delivery"
        assert state.node_status == "ready"
        assert "step.03.implementacao" in state.completed_nodes
        assert state.artifacts["main"] == "src/main.py"

    def test_claim_without_previous_lock_still_recovers_delegation(
        self,
        runner_v2,
    ):
        self._orphan_build(runner_v2)
        runner_v2.state_mgr.release_lock()
        claimed = runner_v2.state_mgr.claim()

        assert claimed.node_status == "delegated"
        assert runner_v2.state_mgr._claim_performed is True
        assert runner_v2.state_mgr._previous_claim_lock is None

        with (
            patch("ft.engine.runner.run_validators") as validators,
            patch.object(runner_v2, "_maybe_auto_commit") as auto_commit,
            patch.object(runner_v2, "_record_node_summary"),
            patch("ft.engine.runner.delegate_to_llm") as delegate,
        ):
            validators.return_value = ValidationResult(True, False, None, [])

            recovered = runner_v2.recover_orphaned_delegation(mode="mvp")

        assert recovered is True
        delegate.assert_not_called()
        auto_commit.assert_called_once()
        state = runner_v2.state_mgr.load()
        assert state.current_node == "gate.02.delivery"
        assert "step.03.implementacao" in state.completed_nodes

    def test_failed_validation_resets_ready_for_normal_delegation(self, runner_v2):
        self._orphan_build(runner_v2)

        with (
            patch.object(runner_v2.state_mgr, "_is_pid_alive", return_value=False),
            patch("ft.engine.runner.run_validators") as validators,
            patch.object(runner_v2, "_maybe_auto_commit") as auto_commit,
        ):
            validators.return_value = ValidationResult(False, True, "still red", [])

            recovered = runner_v2.recover_orphaned_delegation(mode="mvp")

        assert recovered is False
        assert validators.call_count == 1
        assert validators.call_args.kwargs["resume"] is True
        auto_commit.assert_not_called()
        state = runner_v2.state_mgr.load()
        assert state.current_node == "step.03.implementacao"
        assert state.node_status == "ready"
        assert state.active_llm_log is None

    def test_recovery_uses_resume_command_instead_of_expensive_command(
        self, runner_v2
    ):
        self._orphan_build(runner_v2)
        node = runner_v2.graph.get_node("step.03.implementacao")
        node.validators = [
            {
                "command_succeeds": {
                    "command": "python -c 'raise SystemExit(23)'",
                    "resume_command": "python -c 'print(\"receipt verified\")'",
                    "timeout": 1,
                }
            }
        ]

        with (
            patch.object(runner_v2.state_mgr, "_is_pid_alive", return_value=False),
            patch.object(runner_v2, "_maybe_auto_commit") as auto_commit,
        ):
            recovered = runner_v2.recover_orphaned_delegation(mode="mvp")

        assert recovered is True
        auto_commit.assert_called_once()
        state = runner_v2.state_mgr.load()
        assert state.current_node == "gate.02.delivery"
        assert "step.03.implementacao" in state.completed_nodes

    def test_missing_receipt_falls_back_to_full_once_without_llm(self, runner_v2):
        self._orphan_build(runner_v2)
        node = runner_v2.graph.get_node("step.03.implementacao")
        node.validators = [
            {
                "command_succeeds": {
                    "command": "python -c 'print(\"full recorded\")'",
                    "resume_command": (
                        "test -f docs/definitely-missing-feature-validation.json"
                    ),
                    "timeout": 1,
                }
            }
        ]

        with (
            patch.object(runner_v2.state_mgr, "_is_pid_alive", return_value=False),
            patch.object(runner_v2, "_maybe_auto_commit") as auto_commit,
            patch("ft.engine.runner.delegate_to_llm") as delegate,
            patch(
                "ft.engine.runner.run_validators", wraps=run_validators
            ) as validators,
        ):
            recovered = runner_v2.recover_orphaned_delegation(mode="mvp")

        assert recovered is True
        assert validators.call_count == 2
        assert validators.call_args_list[0].kwargs["resume"] is True
        assert "resume" not in validators.call_args_list[1].kwargs
        delegate.assert_not_called()
        auto_commit.assert_called_once()
        assert runner_v2.state_mgr.load().current_node == "gate.02.delivery"

    def test_resume_and_full_failure_reset_ready_without_llm(self, runner_v2):
        self._orphan_build(runner_v2)
        node = runner_v2.graph.get_node("step.03.implementacao")
        node.validators = [
            {
                "command_succeeds": {
                    "command": "python -c 'raise SystemExit(41)'",
                    "resume_command": "test -f docs/missing-receipt.json",
                    "timeout": 1,
                }
            }
        ]

        with (
            patch.object(runner_v2.state_mgr, "_is_pid_alive", return_value=False),
            patch.object(runner_v2, "_maybe_auto_commit") as auto_commit,
            patch("ft.engine.runner.delegate_to_llm") as delegate,
            patch(
                "ft.engine.runner.run_validators", wraps=run_validators
            ) as validators,
        ):
            recovered = runner_v2.recover_orphaned_delegation(mode="mvp")

        assert recovered is False
        assert validators.call_count == 2
        assert validators.call_args_list[0].kwargs["resume"] is True
        assert "resume" not in validators.call_args_list[1].kwargs
        delegate.assert_not_called()
        auto_commit.assert_not_called()
        state = runner_v2.state_mgr.load()
        assert state.current_node == "step.03.implementacao"
        assert state.node_status == "ready"

    def test_live_delegation_is_left_untouched(self, runner_v2):
        self._orphan_build(runner_v2)

        with (
            patch.object(runner_v2.state_mgr, "_is_pid_alive", return_value=True),
            patch("ft.engine.runner.run_validators") as validators,
        ):
            recovered = runner_v2.recover_orphaned_delegation(mode="mvp")

        assert recovered is False
        validators.assert_not_called()
        assert runner_v2.state_mgr.load().node_status == "delegated"


class TestRetryBlockedValidation:
    @staticmethod
    def _blocked_build(runner):
        runner.init_state()
        node = runner.graph.get_node("step.03.implementacao")
        node.validators = [
            {
                "command_succeeds": {
                    "command": "python -c 'print(\"full\")'",
                    "resume_command": "python -c 'print(\"verify\")'",
                }
            }
        ]
        state = runner.state_mgr.load()
        state.current_node = node.id
        state.node_status = "blocked"
        state.blocked_reason = (
            "Validacao falhou apos 0 tentativas: ambiente incompleto"
        )
        runner.state_mgr.save()
        return node

    def test_retry_uses_resume_then_full_without_redelegating(self, runner_v2):
        self._blocked_build(runner_v2)

        with (
            patch("ft.engine.runner.run_validators") as validators,
            patch.object(runner_v2, "_maybe_auto_commit") as auto_commit,
            patch.object(runner_v2, "_record_node_summary") as record_summary,
            patch("ft.engine.runner.delegate_to_llm") as delegate,
        ):
            validators.side_effect = [
                ValidationResult(False, True, "receipt ausente", []),
                ValidationResult(True, False, None, []),
            ]

            handled = runner_v2.retry_blocked_validation_without_llm(
                mode="step"
            )

        assert handled is True
        assert validators.call_count == 2
        assert validators.call_args_list[0].kwargs["resume"] is True
        assert "resume" not in validators.call_args_list[1].kwargs
        delegate.assert_not_called()
        auto_commit.assert_called_once()
        record_summary.assert_called_once()
        state = runner_v2.state_mgr.load()
        assert state.current_node == "gate.02.delivery"
        assert state.node_status == "ready"

    def test_retry_keeps_real_full_failure_blocked_for_ft_fix(self, runner_v2):
        self._blocked_build(runner_v2)

        with (
            patch("ft.engine.runner.run_validators") as validators,
            patch.object(runner_v2, "_maybe_auto_commit") as auto_commit,
            patch("ft.engine.runner.delegate_to_llm") as delegate,
        ):
            validators.side_effect = [
                ValidationResult(False, True, "receipt ausente", []),
                ValidationResult(False, True, "1 regression failed", []),
            ]

            handled = runner_v2.retry_blocked_validation_without_llm(
                mode="step"
            )

        assert handled is True
        delegate.assert_not_called()
        auto_commit.assert_not_called()
        state = runner_v2.state_mgr.load()
        assert state.current_node == "step.03.implementacao"
        assert state.node_status == "blocked"
        assert "1 regression failed" in str(state.blocked_reason)


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

    def test_approve_requires_message_when_gate_declares_it(
        self, runner_v2, capsys
    ):
        runner_v2.init_state()
        node = runner_v2.graph.get_node("step.01.hipotese")
        node.approval_message_required = True
        runner_v2.state_mgr.set_pending_approval(node.id)

        runner_v2.approve()

        blocked = runner_v2.state_mgr.load()
        assert blocked.pending_approval == node.id
        assert blocked.current_node == node.id
        assert node.id not in blocked.completed_nodes
        assert "exige uma mensagem" in capsys.readouterr().out

        runner_v2.approve("Direção visual objetiva")

        approved = runner_v2.state_mgr.load()
        assert approved.pending_approval is None
        assert approved.current_node == "step.02.prd"
        assert approved.last_approval_message == "Direção visual objetiva"

    def test_reject_no_retry_blocks(self, runner_v2):
        runner_v2.init_state()
        runner_v2.state_mgr.set_pending_approval("step.01.hipotese")
        runner_v2.reject("motivo de teste", retry=False)
        state = runner_v2.state_mgr.load()
        assert state.node_status == "blocked"
        assert "Rejeitado" in state.blocked_reason

    def test_reject_with_declared_fix_without_predecessor_review_follows_graph(
        self, tmp_path
    ):
        project_root = tmp_path / "project"
        state_dir = project_root / "state"
        state_dir.mkdir(parents=True)
        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: stakeholder_without_review
version: "1.0.0"
title: Stakeholder without review
nodes:
  - id: evidence
    type: build
    title: Produce evidence
    executor: codex
    next: deterministic.gate
  - id: deterministic.gate
    type: gate
    title: Deterministic gate
    executor: python
    next: stakeholder.gate
  - id: stakeholder.gate
    type: human_gate
    title: Stakeholder gate
    executor: python
    reject_next: stakeholder.fix
    next: end
  - id: stakeholder.fix
    type: build
    title: Stakeholder fix
    executor: codex
    next: stakeholder.gate
  - id: end
    type: end
    title: End
""",
            encoding="utf-8",
        )
        runner = StepRunner(
            process_path=process_path,
            state_path=state_dir / "engine_state.yml",
            project_root=project_root,
        )
        runner.init_state()
        state = runner.state_mgr.load()
        state.completed_nodes = ["evidence", "deterministic.gate"]
        state.current_node = "stakeholder.gate"
        state.node_status = "awaiting_approval"
        state.pending_approval = "stakeholder.gate"
        runner.state_mgr.save()

        assert runner.reject_with_origin_audit("XML real foi recusado")

        rejected = runner.state_mgr.load()
        assert rejected.current_node == "stakeholder.fix"
        assert rejected.node_status == "running"
        assert rejected.pending_approval is None
        assert rejected.active_fix_return is None
        assert rejected.completed_nodes == ["evidence", "deterministic.gate"]
        assert rejected.gate_log["stakeholder.gate"] == "REJECTED"
        assert "XML real foi recusado" in rejected.last_approval_message


class TestDelegationDisplay:
    def test_llm_episode_max_calls_hard_stops_before_second_call_and_persists(
        self, tmp_path
    ):
        project_root = tmp_path / "project"
        state_dir = project_root / "state"
        state_dir.mkdir(parents=True)
        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: budget_process
version: "1.0.0"
title: Budget
nodes:
  - id: implement
    type: build
    title: Implement
    executor: claude
    llm_timeout_seconds: 30
    llm_episode: implementation
    llm_episode_budget_seconds: 60
    llm_episode_max_calls: 1
    outputs: [docs/out.md]
    validators:
      - file_exists: docs/out.md
    next: end
  - id: end
    type: end
    title: End
""",
            encoding="utf-8",
        )
        runner = StepRunner(
            process_path=process_path,
            state_path=state_dir / "engine_state.yml",
            project_root=project_root,
        )
        runner.init_state()

        with patch(
            "ft.engine.runner.delegate_to_llm",
            return_value=DelegateResult(True, "DONE", [], []),
        ) as delegated:
            runner.run(mode="mvp")

        state = runner.state_mgr.load()
        assert delegated.call_count == 1
        assert state.node_status == "blocked"
        assert "Orçamento cumulativo" in (state.blocked_reason or "")
        assert state.llm_episodes["implementation"]["calls"] == 1
        assert state.llm_episodes["implementation"]["consumed_seconds"] >= 0
        checkpoint = state.llm_episodes["implementation"]["checkpoint"]
        assert checkpoint["node_id"] == "implement"
        assert isinstance(checkpoint["changed_paths"], list)
        assert checkpoint["created_at"].endswith("+00:00")

        resumed = StepRunner(
            process_path=process_path,
            state_path=state_dir / "engine_state.yml",
            project_root=project_root,
        )
        persisted = resumed.state_mgr.load()
        assert persisted.llm_episodes == state.llm_episodes

    def test_llm_episode_time_budget_is_telemetry_not_a_productivity_stop(
        self,
        tmp_path,
    ):
        project_root = tmp_path / "project"
        state_dir = project_root / "state"
        state_dir.mkdir(parents=True)
        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: soft_budget_process
version: "1.0.0"
title: Soft Budget
nodes:
  - id: implement
    type: build
    title: Implement
    executor: claude
    llm_timeout_seconds: 30
    llm_episode: implementation
    llm_episode_budget_seconds: 60
    llm_episode_max_calls: 3
    outputs: [docs/out.md]
    next: end
  - id: end
    type: end
    title: End
""",
            encoding="utf-8",
        )
        runner = StepRunner(
            process_path=process_path,
            state_path=state_dir / "engine_state.yml",
            project_root=project_root,
        )
        runner.init_state()
        state = runner.state_mgr.load()
        state.llm_episodes["implementation"] = {
            "ordinal": 1,
            "calls": 1,
            "consumed_seconds": 7200.0,
        }
        node = runner.graph.get_node("implement")

        record = runner._reserve_llm_episode_call(state, node)

        assert record is not None
        assert record["calls"] == 2
        assert record["soft_time_budget_exceeded"] is True
        assert record["soft_time_budget_seconds"] == 60
        assert record["soft_time_budget_consumed_seconds"] == 7200.0
        assert runner._effective_llm_timeout(node) == 30

    def test_node_llm_timeout_is_forwarded_to_delegate(self, tmp_path):
        project_root = tmp_path / "project"
        state_dir = project_root / "state"
        state_dir.mkdir(parents=True)
        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: timeout_process
version: "1.0.0"
title: Timeout
nodes:
  - id: implement
    type: document
    title: Implement
    executor: claude
    llm_timeout_seconds: 37
    outputs: [docs/out.md]
    validators:
      - file_exists: docs/out.md
    next: end
  - id: end
    type: end
    title: End
""",
            encoding="utf-8",
        )
        runner = StepRunner(
            process_path=process_path,
            state_path=state_dir / "engine_state.yml",
            project_root=project_root,
        )
        runner.init_state()

        def delegated(**kwargs):
            (project_root / "docs").mkdir()
            (project_root / "docs/out.md").write_text("done\n", encoding="utf-8")
            return DelegateResult(True, "DONE", ["docs/out.md"], [])

        with patch(
            "ft.engine.runner.delegate_to_llm", side_effect=delegated
        ) as delegate_mock:
            runner._run_llm_step(runner.graph.get_node("implement"))

        assert delegate_mock.call_args.kwargs["llm_timeout_seconds"] == 37

    def test_decision_can_start_new_semantic_episode(self, tmp_path):
        project_root = tmp_path / "project"
        state_dir = project_root / "state"
        state_dir.mkdir(parents=True)
        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: restart_process
title: Restart
nodes:
  - id: route
    type: decision
    title: Route
    condition: review_route
    branches:
      implementation: end
      approved: end
    episode_restart:
      implementation: implementation
  - id: end
    type: end
    title: End
""",
            encoding="utf-8",
        )
        runner = StepRunner(
            process_path=process_path,
            state_path=state_dir / "engine_state.yml",
            project_root=project_root,
        )
        runner.init_state()
        state = runner.state_mgr.load()
        state.artifacts["review_route"] = "implementation"
        state.llm_episodes["implementation"] = {
            "ordinal": 1,
            "calls": 2,
            "consumed_seconds": 42.5,
        }
        runner.state_mgr.save()

        runner._run_decision(runner.graph.get_node("route"))

        state = runner.state_mgr.load()
        assert state.current_node == "end"
        assert state.llm_episodes["implementation"] == {
            "ordinal": 2,
            "calls": 0,
            "consumed_seconds": 0.0,
            "last_reason": "decision:route:implementation",
        }

    def test_backward_decision_rewinds_completed_nodes_and_clears_route(self, tmp_path):
        project_root = tmp_path / "project"
        state_dir = project_root / "state"
        state_dir.mkdir(parents=True)
        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: semantic_route
title: Semantic route
nodes:
  - id: implement
    type: gate
    title: Implement
    next: review
  - id: review
    type: gate
    title: Review
    next: route
  - id: route
    type: decision
    title: Route
    condition: review_route
    branches:
      implementation: implement
      approved: end
    episode_restart:
      implementation: implementation
  - id: end
    type: end
    title: End
""",
            encoding="utf-8",
        )
        runner = StepRunner(
            process_path=process_path,
            state_path=state_dir / "engine_state.yml",
            project_root=project_root,
        )
        runner.init_state()
        state = runner.state_mgr.load()
        state.current_node = "route"
        state.completed_nodes = ["implement", "review"]
        state.gate_log = {"implement": "PASS", "review": "STRUCTURED"}
        state.artifacts["review_route"] = "implementation"
        state.llm_episodes["implementation"] = {
            "ordinal": 1,
            "calls": 2,
            "consumed_seconds": 42.5,
        }
        runner.state_mgr.save()

        runner._run_decision(runner.graph.get_node("route"))

        state = runner.state_mgr.load()
        assert state.current_node == "implement"
        assert state.completed_nodes == []
        assert state.gate_log == {}
        assert "review_route" not in state.artifacts
        assert state.llm_episodes["implementation"]["ordinal"] == 2

    def test_delegation_message_uses_effective_llm_engine(self, tmp_path, capsys):
        project_root = tmp_path / "project"
        docs = project_root / "docs"
        state_dir = project_root / "state"
        docs.mkdir(parents=True)
        state_dir.mkdir()
        (docs / "task_list.md").write_text(
            "\n".join(f"opencode compact line {i}" for i in range(35))
        )

        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: test_process
version: "0.1.0"
title: "Test"
nodes:
  - id: ft.plan.01.doc
    type: document
    title: Doc
    executor: claude
    outputs:
      - docs/out.md
    validators:
      - file_exists: docs/out.md
    next: ft.end
  - id: ft.end
    type: end
    title: End
"""
        )

        runner = StepRunner(
            process_path=process_path,
            state_path=state_dir / "engine_state.yml",
            project_root=project_root,
            llm_engine="opencode",
        )
        runner.init_state()
        node = runner.graph.get_node("ft.plan.01.doc")
        assert node.executor == "llm_claude"

        def delegate_side_effect(**kwargs):
            assert kwargs["llm_engine"] == "opencode"
            assert "opencode compact line 29" in kwargs["task"]
            assert "opencode compact line 30" not in kwargs["task"]
            assert "NAO releia este arquivo inteiro" in kwargs["task"]
            assert "opencode_deny_read_paths" not in kwargs
            assert "opencode_restrict_tools" not in kwargs
            assert kwargs["opencode_steps"] == 8
            assert kwargs["opencode_capture_output_path"] == "docs/out.md"
            (docs / "out.md").write_text("# Out\n")
            return DelegateResult(
                success=True,
                output="DONE",
                files_created=[],
                files_modified=[],
            )

        with patch(
            "ft.engine.runner.delegate_to_llm",
            side_effect=delegate_side_effect,
        ):
            runner._run_llm_step(node)

        out = capsys.readouterr().out
        assert "Delegando ao LLM (opencode)" in out
        assert "Delegando ao LLM (llm_claude)" not in out

    def test_document_prompt_includes_exact_required_headings(self, tmp_path):
        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: test_process
version: "0.1.0"
title: "Test"
nodes:
  - id: ft.plan.03.api_contract
    type: document
    title: API Contract
    executor: claude
    outputs:
      - docs/api_contract.md
    validators:
      - file_exists: docs/api_contract.md
      - has_sections:
          - Base URL
          - Endpoints
    next: ft.end
  - id: ft.end
    type: end
    title: End
"""
        )

        node = load_graph(process_path).get_node("ft.plan.03.api_contract")
        prompt = build_task_prompt(node, {})

        assert "Headings obrigatorios" in prompt
        assert "- ## Base URL" in prompt
        assert "- ## Endpoints" in prompt

    def test_api_contract_prompt_requires_relative_api_paths(self, tmp_path):
        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: test_process
version: "0.1.0"
title: "Test"
nodes:
  - id: ft.plan.03.api_contract
    type: document
    title: API Contract
    executor: claude
    outputs:
      - docs/api_contract.md
    validators:
      - file_exists: docs/api_contract.md
      - has_sections:
          - Base URL
          - Endpoints
    next: ft.end
  - id: ft.end
    type: end
    title: End
"""
        )

        node = load_graph(process_path).get_node("ft.plan.03.api_contract")
        prompt = build_task_prompt(node, {})

        assert "FORMATO RIGIDO OBRIGATORIO" in prompt
        assert "nunca URL completa" in prompt
        assert "| GET | /health |" in prompt
        assert "| GET | /api/recursos |" in prompt

    def test_api_contract_feedback_includes_endpoint_rows_from_docs(self, tmp_path):
        project_root = tmp_path / "project"
        state_dir = project_root / "state"
        docs = project_root / "docs"
        state_dir.mkdir(parents=True)
        docs.mkdir(parents=True)
        (docs / "PRD.md").write_text(
            "| POST | `/api/clientes` | Criar cliente |\n"
            "| GET | `/api/clientes` | Listar clientes |\n"
            "| GET | `/api/dashboard` | Resumo |\n",
            encoding="utf-8",
        )
        (docs / "api_contract.md").write_text(
            "## Base URL\n\n## Endpoints\n\nGET frases soltas sem path\n",
            encoding="utf-8",
        )
        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: test_process
version: "0.1.0"
title: "Test"
nodes:
  - id: ft.plan.03.api_contract
    type: document
    title: API Contract
    executor: claude
    outputs:
      - docs/api_contract.md
    next: ft.end
  - id: ft.end
    type: end
    title: End
"""
        )
        runner = StepRunner(
            process_path=process_path,
            state_path=state_dir / "engine_state.yml",
            project_root=project_root,
        )
        node = runner.graph.get_node("ft.plan.03.api_contract")

        feedback = enrich_api_contract_feedback(
            node.id,
            "api_contract_complete FAIL",
            project_root,
        )

        assert "DIAGNOSTICO ESPECIFICO DO CONTRATO DE API" in feedback
        assert "| GET | /health | Health check |" in feedback
        assert "| POST | /api/clientes | Criar cliente |" in feedback
        assert "GET frases soltas sem path" not in feedback
        assert "ARTEFATO INVALIDO ATUAL" not in feedback

    def test_api_contract_candidates_normalize_task_list_paths_without_api_prefix(self, tmp_path):
        project_root = tmp_path / "project"
        state_dir = project_root / "state"
        docs = project_root / "docs"
        state_dir.mkdir(parents=True)
        docs.mkdir(parents=True)
        (docs / "task_list.md").write_text(
            "1. Criar endpoint `POST /clientes` com validação.\n"
            "2. Implementar endpoints GET `/clientes`, PUT `/clientes/{id}` e DELETE `/clientes/{id}`.\n"
            "3. Criar endpoint POST `/agenda` recebendo data_hora.\n"
            "4. Criar endpoint GET `/cobrancas` retorna total_pendente.\n",
            encoding="utf-8",
        )
        candidates = extract_api_endpoint_candidates(project_root)

        assert ("POST", "/api/clientes", "com validação.") in candidates
        assert any(method == "GET" and path == "/api/clientes" for method, path, _ in candidates)
        assert any(method == "POST" and path == "/api/agenda" for method, path, _ in candidates)
        assert any(method == "GET" and path == "/api/cobrancas" for method, path, _ in candidates)

    def test_opencode_code_nodes_allow_native_edit_tools_by_default(self, tmp_path):
        project_root = tmp_path / "project"
        state_dir = project_root / "state"
        state_dir.mkdir(parents=True)

        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: test_process
version: "0.1.0"
title: "Test"
nodes:
  - id: ft.frontend.01.scaffold
    type: build
    title: Scaffold
    executor: claude
    outputs:
      - project/frontend/
      - .build_ok
    next: ft.plan.01.doc
  - id: ft.plan.01.doc
    type: document
    title: Doc
    executor: claude
    outputs:
      - docs/out.md
    next: ft.end
  - id: ft.end
    type: end
    title: End
"""
        )

        runner = StepRunner(
            process_path=process_path,
            state_path=state_dir / "engine_state.yml",
            project_root=project_root,
            llm_engine="opencode",
        )

        build_node = runner.graph.get_node("ft.frontend.01.scaffold")
        doc_node = runner.graph.get_node("ft.plan.01.doc")
        build_options = runner._opencode_options_for_node(build_node, "opencode")
        doc_options = runner._opencode_options_for_node(doc_node, "opencode")
        assert build_options.deny_edit_tools is True
        assert build_options.early_success_paths == []
        assert doc_options.deny_edit_tools is False
        assert doc_options.restrict_tools is False
        assert doc_options.early_success_paths == ["docs/out.md"]
        assert doc_options.capture_output_path == "docs/out.md"
        assert runner._resolve_allowed_paths(build_node) == ["project", ".build_ok"]
        assert runner._resolve_allowed_paths(doc_node) == ["docs/out.md"]

        with patch.dict("os.environ", {"FT_OPENCODE_CAPTURE_DOCS": "0"}):
            no_capture_options = runner._opencode_options_for_node(doc_node, "opencode")
        assert no_capture_options.capture_output_path is None

        with patch.dict("os.environ", {"FT_OPENCODE_DENY_EDIT_TOOLS": "0"}):
            native_build_options = runner._opencode_options_for_node(build_node, "opencode")
        assert native_build_options.deny_edit_tools is False

    def test_opencode_code_nodes_receive_hyper_mode_docs(self, tmp_path):
        project_root = tmp_path / "project"
        state_dir = project_root / "state"
        docs = project_root / "docs"
        state_dir.mkdir(parents=True)
        docs.mkdir(parents=True)
        (docs / "PRD.md").write_text("# PRD\n\n" + "\n".join(f"linha {i}" for i in range(45)))
        (docs / "ui_criteria.md").write_text("# UI\n\n" + "\n".join(f"criterio {i}" for i in range(45)))

        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: test_process
version: "0.1.0"
title: "Test"
nodes:
  - id: ft.frontend.02.implement
    type: build
    title: Implement
    executor: claude
    outputs:
      - project/frontend/src/
    next: ft.end
  - id: ft.end
    type: end
    title: End
"""
        )

        runner = StepRunner(
            process_path=process_path,
            state_path=state_dir / "engine_state.yml",
            project_root=project_root,
            llm_engine="opencode",
        )
        node = runner.graph.get_node("ft.frontend.02.implement")

        def delegate_side_effect(**kwargs):
            assert "CONTEXTO EXISTENTE" in kwargs["task"]
            assert "### PRD.md" in kwargs["task"]
            assert "### ui_criteria.md" in kwargs["task"]
            assert "leia apenas trechos relevantes" in kwargs["task"]
            assert "NAO releia este arquivo inteiro" not in kwargs["task"]
            (project_root / "project/frontend/src").mkdir(parents=True)
            return DelegateResult(
                success=True,
                output="DONE",
                files_created=[],
                files_modified=[],
            )

        with patch("ft.engine.runner.delegate_to_llm", side_effect=delegate_side_effect):
            runner._run_llm_step(node)

    def test_opencode_frontend_delegates_project_content_without_substitution(
        self, tmp_path
    ):
        project_root = tmp_path / "project"
        state_dir = project_root / "state"
        docs = project_root / "docs"
        state_dir.mkdir(parents=True)
        docs.mkdir(parents=True)
        (docs / "PRD.md").write_text(
            "# PRD\n\nLunar Atlas exibe órbitas, fases e missões científicas.\n",
            encoding="utf-8",
        )
        (docs / "ui_criteria.md").write_text(
            "# UI\n\n- C01: Mapa lunar.\n- C02: Painel de missão e formulário de observação.\n",
            encoding="utf-8",
        )

        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: test_process
version: "0.1.0"
title: "Test"
nodes:
  - id: ft.frontend.02.implement
    type: build
    title: Implement Frontend
    executor: claude
    outputs:
      - project/frontend/src/
    validators:
      - command_succeeds: cd project/frontend && npm run build --silent
      - command_succeeds: "python -c \\"from pathlib import Path; text=chr(10).join(p.read_text(encoding='utf-8', errors='ignore') for p in Path('project/frontend/src').rglob('*') if p.is_file()).lower(); assert '<form' in text and 'submit' in text\\""
    next: ft.end
  - id: ft.end
    type: end
    title: End
""",
            encoding="utf-8",
        )

        runner = StepRunner(
            process_path=process_path,
            state_path=state_dir / "engine_state.yml",
            project_root=project_root,
            llm_engine="opencode",
        )
        runner.init_state()
        node = runner.graph.get_node("ft.frontend.02.implement")

        def delegate_side_effect(**kwargs):
            frontend = project_root / "project/frontend"
            (frontend / "scripts").mkdir(parents=True)
            (frontend / "src").mkdir(parents=True)
            (frontend / "package.json").write_text(
                json.dumps({"type": "module", "scripts": {"build": "node scripts/build.mjs"}}),
                encoding="utf-8",
            )
            (frontend / "scripts" / "build.mjs").write_text("console.log('ok')\n", encoding="utf-8")
            (frontend / "src" / "main.js").write_text(
                "const title = 'Lunar Atlas';\n"
                "const html = '<form><button type=\"submit\">Registrar observação</button></form>';\n",
                encoding="utf-8",
            )
            return DelegateResult(success=True, output="DONE", files_created=[], files_modified=[])

        with patch("ft.engine.runner.delegate_to_llm", side_effect=delegate_side_effect) as delegated:
            runner._run_llm_step(node)

        assert delegated.called
        main_js = (project_root / "project/frontend/src/main.js").read_text(encoding="utf-8")
        assert "Lunar Atlas" in main_js
        assert "Registrar observação" in main_js
        assert runner.state_mgr.load().current_node == "ft.end"

    def test_opencode_scaffold_delegates_by_default(self, tmp_path):
        project_root = tmp_path / "project"
        state_dir = project_root / "state"
        state_dir.mkdir(parents=True)

        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: test_process
version: "0.1.0"
title: "Test"
nodes:
  - id: ft.frontend.01.scaffold
    type: build
    title: Scaffold
    executor: claude
    outputs:
      - project/frontend/
      - .build_ok
    validators:
      - file_exists: project/frontend/package.json
      - file_exists: .build_ok
    next: ft.end
  - id: ft.end
    type: end
    title: End
"""
        )

        runner = StepRunner(
            process_path=process_path,
            state_path=state_dir / "engine_state.yml",
            project_root=project_root,
            llm_engine="opencode",
        )
        runner.init_state()
        node = runner.graph.get_node("ft.frontend.01.scaffold")

        def delegate_side_effect(**kwargs):
            assert kwargs["llm_engine"] == "opencode"
            assert "OpenCode compact bundle" not in kwargs["task"]
            frontend = project_root / "project" / "frontend"
            frontend.mkdir(parents=True)
            (frontend / "package.json").write_text('{"scripts":{"build":"true"}}\n', encoding="utf-8")
            (project_root / ".build_ok").write_text("ok\n", encoding="utf-8")
            return DelegateResult(success=True, output="DONE", files_created=[], files_modified=[])

        with patch(
            "ft.engine.runner.delegate_to_llm",
            side_effect=delegate_side_effect,
        ) as delegate_mock:
            runner._run_llm_step(node)

        state = runner.state_mgr.load()
        assert delegate_mock.called
        assert state.metrics["llm_calls"] == 1
        assert state.current_node == "ft.end"

    def test_rewinds_to_tdd_red_when_completed_red_quality_is_invalid(self, tmp_path):
        project_root = tmp_path / "project"
        state_dir = project_root / "state"
        state_dir.mkdir(parents=True)

        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: test_process
version: "0.1.0"
title: "Test"
nodes:
  - id: ft.tdd.01.red
    type: test_red
    title: Red
    executor: claude
    outputs:
      - project/tests/
    validators:
      - file_exists: project/tests/
      - pytest_red_quality:
          tests_dir: project/tests
          min_tests: 3
          min_assertions: 3
    next: ft.tdd.02.green
  - id: ft.tdd.02.green
    type: test_green
    title: Green
    executor: claude
    outputs:
      - project/backend/
    validators:
      - command_succeeds: "cd project && python -m pytest tests/ -q"
    next: ft.end
  - id: ft.end
    type: end
    title: End
""",
            encoding="utf-8",
        )

        tests_dir = project_root / "project" / "tests"
        tests_dir.mkdir(parents=True)
        (tests_dir / "test_client_manager.py").write_text(
            "def test_stub():\n"
            "    pass\n",
            encoding="utf-8",
        )

        runner = StepRunner(
            process_path=process_path,
            state_path=state_dir / "engine_state.yml",
            project_root=project_root,
            llm_engine="opencode",
        )
        runner.init_state()
        state = runner.state_mgr.load()
        state.current_node = "ft.tdd.02.green"
        state.completed_nodes = ["ft.tdd.01.red"]
        state.gate_log = {"ft.tdd.01.red": "PASS"}
        state.artifacts = {"tests": "project/tests/"}
        state.metrics["steps_completed"] = 1
        runner.state_mgr.save()

        rewound = runner._rewind_invalid_tdd_red(runner.graph.get_node("ft.tdd.02.green"), state)

        assert rewound is True
        state = runner.state_mgr.load()
        assert state.current_node == "ft.tdd.01.red"
        assert state.completed_nodes == []
        assert "ft.tdd.01.red" not in state.gate_log
        assert "tests" not in state.artifacts

    def test_does_not_rewind_native_quality_sprint_that_reuses_legacy_ids(self, tmp_path):
        project_root = tmp_path / "project"
        state_dir = project_root / "state"
        state_dir.mkdir(parents=True)

        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: native_quality
version: "0.1.0"
title: "Native quality"
nodes:
  - id: ft.tdd.01.red
    type: build
    title: Android quality
    executor: claude
    outputs:
      - project/android/app/src/test/
    validators:
      - file_exists: project/android/app/src/test/
    next: gate.tdd
  - id: gate.tdd
    type: gate
    title: Android quality gate
    validators:
      - command_succeeds: "true"
    next: ft.end
  - id: ft.end
    type: end
    title: End
""",
            encoding="utf-8",
        )

        native_tests = project_root / "project" / "android" / "app" / "src" / "test"
        native_tests.mkdir(parents=True)
        marker = native_tests / "ConnectionMachineTest.kt"
        marker.write_text("class ConnectionMachineTest\n", encoding="utf-8")

        runner = StepRunner(
            process_path=process_path,
            state_path=state_dir / "engine_state.yml",
            project_root=project_root,
            llm_engine="opencode",
        )
        runner.init_state()
        state = runner.state_mgr.load()
        state.current_node = "gate.tdd"
        state.completed_nodes = ["ft.tdd.01.red"]
        state.gate_log = {"ft.tdd.01.red": "PASS"}
        state.metrics["steps_completed"] = 1
        runner.state_mgr.save()

        rewound = runner._rewind_invalid_tdd_red(
            runner.graph.get_node("gate.tdd"),
            state,
        )

        assert rewound is False
        assert marker.is_file()
        assert runner.state_mgr.load().current_node == "gate.tdd"

    def test_opencode_process_evolve_preserves_named_process_in_worktree(self, tmp_path):
        project_root = tmp_path / "project"
        work_dir = tmp_path / "worktrees" / "sample" / "cycle-01-opencode"
        state_dir = project_root / "state"
        (project_root / ".ft" / "process" / "mvp-builder").mkdir(parents=True)
        (work_dir / "docs").mkdir(parents=True)
        (work_dir / ".ft" / "process" / "mvp-builder").mkdir(parents=True)
        state_dir.mkdir(parents=True)

        process_path = (
            project_root / ".ft" / "process" / "mvp-builder" / "process.yml"
        )
        process_path.write_text(
            """
id: test_process
version: "0.1.0"
title: "Test"
nodes:
  - id: ft.handoff.05.process_evolve
    type: document
    title: Process Evolve
    executor: claude
    outputs:
      - docs/process-improvements.md
      - .ft/process/mvp-builder/process.yml
    validators:
      - file_exists: docs/process-improvements.md
      - file_exists: .ft/process/mvp-builder/process.yml
    next: ft.end
  - id: ft.end
    type: end
    title: End
""",
            encoding="utf-8",
        )
        worktree_process = (
            work_dir / ".ft" / "process" / "mvp-builder" / "process.yml"
        )
        worktree_process.write_text(
            process_path.read_text(encoding="utf-8"),
            encoding="utf-8",
        )

        runner = StepRunner(
            process_path=process_path,
            state_path=state_dir / "engine_state.yml",
            project_root=project_root,
            llm_engine="opencode",
        )
        runner._work_dir = str(work_dir)
        runner.init_state()

        def delegate_side_effect(**kwargs):
            (work_dir / "docs" / "process-improvements.md").write_text(
                "# Process Improvements\n\nNenhuma mudança proposta.\n",
                encoding="utf-8",
            )
            return DelegateResult(True, "DONE", [], ["docs/process-improvements.md"])

        with patch(
            "ft.engine.runner.delegate_to_llm",
            side_effect=delegate_side_effect,
        ) as delegated:
            runner._run_llm_step(runner.graph.get_node("ft.handoff.05.process_evolve"))

        assert delegated.call_count == 1
        restored = work_dir / ".ft" / "process" / "mvp-builder" / "process.yml"
        assert restored.exists()
        assert restored.stat().st_size > 0
        assert runner.state_mgr.load().current_node == "ft.end"

    def test_decision_skipped_branch_counts_as_progress(self, tmp_path):
        project_root = tmp_path / "project"
        state_dir = project_root / "state"
        (project_root / "docs").mkdir(parents=True)
        state_dir.mkdir(parents=True)
        (project_root / "docs" / "PRD.md").write_text("# PRD\n", encoding="utf-8")

        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: test_process
version: "0.1.0"
title: "Test"
nodes:
  - id: start
    type: decision
    title: Start
    condition: "file_exists:docs/PRD.md"
    branches:
      "true": after
      "false": skipped.one
  - id: skipped.one
    type: gate
    title: Skipped One
    executor: python
    next: skipped.two
  - id: skipped.two
    type: gate
    title: Skipped Two
    executor: python
    next: after
  - id: after
    type: gate
    title: After
    executor: python
    next: end
  - id: end
    type: end
    title: End
""",
            encoding="utf-8",
        )

        runner = StepRunner(
            process_path=process_path,
            state_path=state_dir / "engine_state.yml",
            project_root=project_root,
        )
        runner.init_state()
        runner.run(mode="mvp")

        state = runner.state_mgr.load()
        assert state.node_status == "done"
        # A branch não escolhida continua auditável como SKIPPED, mas não
        # infla o progresso da rota realmente executada.
        assert state.metrics["steps_completed"] == 2
        assert state.metrics["steps_total"] == 2
        assert state.gate_log["skipped.one"] == "SKIPPED"
        assert state.gate_log["skipped.two"] == "SKIPPED"

    def test_decision_false_branch_can_rejoin_main_path_without_skipping_it(self, tmp_path):
        project_root = tmp_path / "project"
        state_dir = project_root / "state"
        (project_root / "docs").mkdir(parents=True)
        state_dir.mkdir(parents=True)

        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: test_process
version: "0.1.0"
title: "Test"
nodes:
  - id: route.ui
    type: decision
    title: UI Criteria?
    condition: "file_exists:docs/ui_criteria.md"
    branches:
      "true": plan
      "false": create.ui
  - id: create.ui
    type: gate
    title: Create UI
    executor: python
    next: plan
  - id: plan
    type: gate
    title: Plan
    executor: python
    next: end
  - id: end
    type: end
    title: End
""",
            encoding="utf-8",
        )

        runner = StepRunner(
            process_path=process_path,
            state_path=state_dir / "engine_state.yml",
            project_root=project_root,
        )
        runner.init_state()
        runner._run_decision(runner.graph.get_node("route.ui"))

        state = runner.state_mgr.load()
        assert state.current_node == "create.ui"
        assert "plan" not in state.completed_nodes
        assert state.gate_log.get("plan") != "SKIPPED"

    def test_validation_profile_decision_uses_project_contract(self, tmp_path):
        project_root = tmp_path / "project"
        state_dir = project_root / "state"
        (project_root / ".ft").mkdir(parents=True)
        state_dir.mkdir(parents=True)
        (project_root / ".ft" / "project.yml").write_text(
            """
validation:
  schema_version: 1
  mode: explicit
  matrix_path: docs/validation-matrix.yml
  report_path: docs/platform-validation-report.yml
  evidence_root: docs/evidence/platform-validation
  test_identity:
    policy: optional
    path: docs/test-identity.json
  platforms:
    web:
      targets:
        desktop_browser:
          required: true
""",
            encoding="utf-8",
        )
        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: profile_route
version: "0.1.0"
title: Profile route
nodes:
  - id: route
    type: decision
    title: Profiles?
    condition: validation_profiles_active
    branches:
      "true": validate
      "false": end
  - id: validate
    type: gate
    title: Validate
    executor: python
    next: end
  - id: end
    type: end
    title: End
""",
            encoding="utf-8",
        )

        runner = StepRunner(
            process_path=process_path,
            state_path=state_dir / "engine_state.yml",
            project_root=project_root,
        )
        runner.init_state()
        runner._run_decision(runner.graph.get_node("route"))

        state = runner.state_mgr.load()
        assert state.current_node == "validate"
        assert state.route_choices["route"] == "validate"

    @pytest.mark.parametrize(
        ("mode", "platforms", "expected"),
        [
            ("disabled", "{}", "headless"),
            (
                "explicit",
                "{web: {targets: {desktop_browser: {required: true}}}}",
                "visual",
            ),
        ],
    )
    def test_project_validation_mode_routes_headless_without_guessing_from_files(
        self,
        tmp_path,
        mode,
        platforms,
        expected,
    ):
        project_root = tmp_path / "project"
        state_dir = project_root / "state"
        (project_root / ".ft").mkdir(parents=True)
        state_dir.mkdir(parents=True)
        reason = (
            "reason: Produto sem superfície visual.\n  "
            if mode == "disabled"
            else ""
        )
        (project_root / ".ft" / "project.yml").write_text(
            f"""
validation:
  schema_version: 1
  mode: {mode}
  {reason}matrix_path: docs/validation-matrix.yml
  report_path: docs/platform-validation-report.yml
  evidence_root: docs/evidence/platform-validation
  test_identity:
    policy: not_required
    path: docs/test-identity.json
  platforms: {platforms}
""",
            encoding="utf-8",
        )
        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: surface_route
version: "0.1.0"
title: Surface route
nodes:
  - id: route
    type: decision
    title: Surface?
    condition: project_validation_mode
    branches:
      disabled: headless
      _default: visual
  - id: headless
    type: gate
    title: Headless
    executor: python
    next: end
  - id: visual
    type: gate
    title: Visual
    executor: python
    next: end
  - id: end
    type: end
    title: End
""",
            encoding="utf-8",
        )

        runner = StepRunner(
            process_path=process_path,
            state_path=state_dir / "engine_state.yml",
            project_root=project_root,
        )
        runner.init_state()
        runner._run_decision(runner.graph.get_node("route"))

        state = runner.state_mgr.load()
        assert state.current_node == expected
        assert state.route_choices["route"] == expected

    def test_approved_human_gate_skips_reject_branch_progress(self, tmp_path):
        project_root = tmp_path / "project"
        state_dir = project_root / "state"
        state_dir.mkdir(parents=True)

        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: test_process
version: "0.1.0"
title: "Test"
nodes:
  - id: review
    type: human_gate
    title: Review
    executor: python
    reject_next: fix
    next: after
  - id: fix
    type: gate
    title: Fix
    executor: python
    next: review
  - id: after
    type: gate
    title: After
    executor: python
    next: end
  - id: end
    type: end
    title: End
""",
            encoding="utf-8",
        )

        runner = StepRunner(
            process_path=process_path,
            state_path=state_dir / "engine_state.yml",
            project_root=project_root,
        )
        runner.init_state()
        runner._bypass_human_gates = True
        runner.run(mode="mvp")

        state = runner.state_mgr.load()
        assert state.node_status == "done"
        # O fix permanece no denominador por ser um loop focal possível,
        # mas aprovação não o transforma artificialmente em trabalho feito.
        assert state.metrics["steps_completed"] == 2
        assert state.metrics["steps_total"] == 3
        assert state.gate_log["fix"] == "SKIPPED"

    def test_delegate_allowed_paths_keep_local_docs_in_external_workdir(self, tmp_path):
        project_root = tmp_path / "project"
        state_dir = project_root / "state"
        work_dir = tmp_path / "worktrees" / "sample" / "cycle-01-opencode"
        (work_dir / "docs").mkdir(parents=True)
        state_dir.mkdir(parents=True)

        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: test_process
version: "0.1.0"
title: "Test"
nodes:
  - id: ft.end
    type: end
    title: End
"""
        )
        runner = StepRunner(
            process_path=process_path,
            state_path=state_dir / "engine_state.yml",
            project_root=project_root,
            llm_engine="opencode",
        )
        runner._work_dir = str(work_dir)

        assert runner._delegate_allowed_paths(["docs/screenshots/", "docs/screenshot-review.md"]) == [
            "docs/screenshots/",
            "docs/screenshot-review.md",
        ]

    def test_opencode_document_retry_preserves_capture_mode(self, tmp_path):
        project_root = tmp_path / "project"
        docs = project_root / "docs"
        state_dir = project_root / "state"
        docs.mkdir(parents=True)
        state_dir.mkdir()

        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: test_process
version: "0.1.0"
title: "Test"
nodes:
  - id: ft.plan.01.doc
    type: document
    title: Doc
    executor: claude
    outputs:
      - docs/out.md
    validators:
      - file_exists: docs/out.md
      - has_sections:
          path: docs/out.md
          sections:
            - Required
    next: ft.end
  - id: ft.end
    type: end
    title: End
"""
        )

        runner = StepRunner(
            process_path=process_path,
            state_path=state_dir / "engine_state.yml",
            project_root=project_root,
            llm_engine="opencode",
        )
        runner.init_state()
        node = runner.graph.get_node("ft.plan.01.doc")

        def first_delegate(**kwargs):
            assert kwargs["opencode_capture_output_path"] == "docs/out.md"
            (docs / "out.md").write_text("# Missing\n")
            return DelegateResult(success=True, output="DONE", files_created=[], files_modified=[])

        def retry_delegate(**kwargs):
            assert kwargs["opencode_capture_output_path"] == "docs/out.md"
            assert kwargs["opencode_early_success_paths"] == ["docs/out.md"]
            (docs / "out.md").write_text("# Required\n")
            return DelegateResult(success=True, output="DONE", files_created=[], files_modified=[])

        with (
            patch("ft.engine.runner.delegate_to_llm", side_effect=first_delegate),
            patch("ft.engine.runner.delegate_with_feedback", side_effect=retry_delegate) as retry_mock,
        ):
            runner._run_llm_step(node)

        assert retry_mock.called
        assert runner.state_mgr.load().current_node == "ft.end"

    def test_opencode_document_auto_fix_uses_capture_prompt_by_default(self, tmp_path):
        project_root = tmp_path / "project"
        docs = project_root / "docs"
        state_dir = project_root / "state"
        docs.mkdir(parents=True)
        state_dir.mkdir()

        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: test_process
version: "0.1.0"
title: "Test"
nodes:
  - id: ft.plan.01.doc
    type: document
    title: Doc
    executor: claude
    outputs:
      - docs/out.md
    validators:
      - file_exists: docs/out.md
    next: ft.end
  - id: ft.end
    type: end
    title: End
"""
        )

        runner = StepRunner(
            process_path=process_path,
            state_path=state_dir / "engine_state.yml",
            project_root=project_root,
            llm_engine="opencode",
        )
        runner.init_state()
        node = runner.graph.get_node("ft.plan.01.doc")

        def auto_fix_delegate(**kwargs):
            task = kwargs["task"]
            assert "docs/out.md" in task
            assert "Nao responda DONE" in task
            assert "Quando terminar, diga DONE" not in task
            assert kwargs["opencode_capture_output_path"] == "docs/out.md"
            (docs / "out.md").write_text("# Fixed\n")
            return DelegateResult(success=True, output="DONE", files_created=[], files_modified=[])

        with patch("ft.engine.runner.delegate_to_llm", side_effect=auto_fix_delegate):
            assert runner._run_auto_fix(node, "file_exists FAIL: docs/out.md nao encontrado")

        assert runner.state_mgr.load().current_node == "ft.end"

    def test_structured_review_routes_rejection_without_static_on_fail(self, tmp_path):
        project_root = tmp_path / "project"
        docs = project_root / "docs"
        state_dir = project_root / "state"
        docs.mkdir(parents=True)
        state_dir.mkdir()
        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: structured_review
title: Structured Review
nodes:
  - id: review
    type: review
    title: Review
    executor: claude
    review_route_path: docs/review.yml
    outputs: [docs/review.md, docs/review.yml]
    validators:
      - file_exists: docs/review.md
      - file_exists: docs/review.yml
    next: route
  - id: route
    type: decision
    title: Route
    condition: review_route
    branches:
      implementation: end
      approved: end
  - id: end
    type: end
    title: End
""",
            encoding="utf-8",
        )
        runner = StepRunner(
            process_path=process_path,
            state_path=state_dir / "engine_state.yml",
            project_root=project_root,
        )
        runner.init_state()

        def review(**_kwargs):
            (docs / "review.md").write_text("Resultado: REJECTED\n")
            (docs / "review.yml").write_text(
                "review_route: implementation\nverdict: REJECTED\n"
            )
            return DelegateResult(True, "DONE", ["docs/review.md", "docs/review.yml"], [])

        with patch("ft.engine.runner.delegate_to_llm", side_effect=review):
            runner._run_review(runner.graph.get_node("review"))

        state = runner.state_mgr.load()
        assert state.current_node == "route"
        assert state.node_status != "blocked"
        assert state.gate_log["review"] == "STRUCTURED"

    @pytest.mark.parametrize(
        "line",
        [
            "**Veredito: REJECTED**",
            "Veredicto: REJEITADO",
            "Parecer — REPROVADO",
            "Verdict: FAILED",
            "| R-002 | REJECTED | evidência |",
        ],
    )
    def test_review_verdict_parser_recognizes_rejection_variants(self, line):
        assert _parse_review_verdict(line) == "REJECTED"

    def test_review_rejection_wins_over_approved_response_and_other_outputs(
        self,
        tmp_path,
        capsys,
    ):
        project_root = tmp_path / "project"
        docs = project_root / "docs"
        state_dir = project_root / "state"
        docs.mkdir(parents=True)
        state_dir.mkdir()
        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: fail_closed_batch_review
title: Fail-closed batch review
nodes:
  - id: batch.review
    type: review
    title: Combined review
    executor: claude
    no_pre_seed: true
    outputs: [docs/mvp-batch-review.md, docs/mvp-batch-review.yml]
    validators:
      - file_exists: docs/mvp-batch-review.md
      - file_exists: docs/mvp-batch-review.yml
    on_fail:
      human_gate: Corrigir somente os findings do batch.
      goto: batch.fix
    next: batch.verify
  - id: batch.fix
    type: build
    title: Fix
    next: batch.review
  - id: batch.verify
    type: gate
    title: Verify
    next: end
  - id: end
    type: end
    title: End
""",
            encoding="utf-8",
        )
        runner = StepRunner(
            process_path=process_path,
            state_path=state_dir / "engine_state.yml",
            project_root=project_root,
        )
        runner.init_state()

        def review(**_kwargs):
            (docs / "mvp-batch-review.md").write_text(
                "VERDICT: APPROVED\n\n"
                "**Veredito: REJECTED**\n\n"
                "| Requisito | Resultado |\n"
                "| --- | --- |\n"
                "| R-001 | PASS |\n"
                "| R-002 | REJECTED |\n",
                encoding="utf-8",
            )
            (docs / "mvp-batch-review.yml").write_text(
                "verdict: APPROVED\n"
                "results:\n"
                "  - ref: R-001\n"
                "    result: PASS\n"
                "  - ref: R-002\n"
                "    result: REJECTED\n",
                encoding="utf-8",
            )
            return DelegateResult(
                True,
                "VERDICT: APPROVED",
                ["docs/mvp-batch-review.md", "docs/mvp-batch-review.yml"],
                [],
            )

        with patch("ft.engine.runner.delegate_to_llm", side_effect=review):
            runner._run_review(runner.graph.get_node("batch.review"))

        state = runner.state_mgr.load()
        rendered = capsys.readouterr().out
        assert state.current_node == "batch.review"
        assert state.node_status == "pending_fix"
        assert state.pending_fix["goto"] == "batch.fix"
        assert "REVIEW_VERDICT_CONFLICT" in state.pending_fix["feedback"]
        assert "REVIEW REJECTED" in rendered
        assert "batch.verify" not in state.completed_nodes

    def test_existing_canonical_rejection_cannot_be_preseeded_as_approved(
        self,
        tmp_path,
    ):
        project_root = tmp_path / "project"
        docs = project_root / "docs"
        state_dir = project_root / "state"
        docs.mkdir(parents=True)
        state_dir.mkdir()
        (docs / "review.md").write_text(
            "**Veredito: REJECTED**\nFinding ainda aberto.\n",
            encoding="utf-8",
        )
        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: stale_rejected_review
title: Stale rejected review
nodes:
  - id: review
    type: review
    title: Review
    executor: claude
    outputs: [docs/review.md]
    validators:
      - file_exists: docs/review.md
    on_fail:
      human_gate: Corrigir finding aberto.
      goto: fix
    next: verify
  - id: fix
    type: build
    title: Fix
    next: review
  - id: verify
    type: gate
    title: Verify
    next: end
  - id: end
    type: end
    title: End
""",
            encoding="utf-8",
        )
        runner = StepRunner(
            process_path=process_path,
            state_path=state_dir / "engine_state.yml",
            project_root=project_root,
        )
        runner.init_state()

        with patch(
            "ft.engine.runner.delegate_to_llm",
            side_effect=AssertionError("review rejeitado não deve ser pulado"),
        ):
            runner._run_review(runner.graph.get_node("review"))

        state = runner.state_mgr.load()
        assert state.current_node == "review"
        assert state.node_status == "pending_fix"
        assert state.pending_fix["goto"] == "fix"
        assert "CANONICAL_REVIEW_REJECTED" in state.pending_fix["feedback"]

    def test_structured_rejection_overrides_approved_reviewer_response(
        self,
        tmp_path,
        capsys,
    ):
        project_root = tmp_path / "project"
        docs = project_root / "docs"
        state_dir = project_root / "state"
        docs.mkdir(parents=True)
        state_dir.mkdir()
        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: structured_conflict
title: Structured conflict
nodes:
  - id: review
    type: review
    title: Review
    executor: claude
    no_pre_seed: true
    review_route_path: docs/review.yml
    outputs: [docs/review.md, docs/review.yml]
    validators:
      - file_exists: docs/review.md
      - file_exists: docs/review.yml
    next: route
  - id: route
    type: decision
    title: Route
    condition: review_route
    branches:
      implementation: end
      approved: end
  - id: end
    type: end
    title: End
""",
            encoding="utf-8",
        )
        runner = StepRunner(
            process_path=process_path,
            state_path=state_dir / "engine_state.yml",
            project_root=project_root,
        )
        runner.init_state()

        def review(**_kwargs):
            (docs / "review.md").write_text(
                "**Veredito: REJECTED**\n",
                encoding="utf-8",
            )
            (docs / "review.yml").write_text(
                "review_route: implementation\nverdict: REJECTED\n",
                encoding="utf-8",
            )
            return DelegateResult(True, "VERDICT: APPROVED", [], [])

        with patch("ft.engine.runner.delegate_to_llm", side_effect=review):
            runner._run_review(runner.graph.get_node("review"))

        state = runner.state_mgr.load()
        rendered = capsys.readouterr().out
        assert state.current_node == "route"
        assert state.gate_log["review"] == "STRUCTURED"
        assert "REVIEW REJECTED — seguindo rota estruturada" in rendered

    def test_runtime_focal_review_persists_live_verdict_in_canonical_report(
        self,
        tmp_path,
    ):
        project_root = tmp_path / "project"
        docs = project_root / "docs"
        state_dir = project_root / "state"
        docs.mkdir(parents=True)
        state_dir.mkdir()
        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: focal_runtime_review
title: Focal runtime review
nodes:
  - id: fix
    type: build
    title: Fix
    next: broad.review
  - id: broad.review
    type: review
    title: Reexecutar todo o produto
    no_pre_seed: true
    preserve_outputs_on_reentry: true
    outputs: [docs/broad-review.md]
    validators:
      - file_exists: docs/broad-review.md
    next: acceptance
  - id: acceptance
    type: human_gate
    title: Acceptance
    reject_next: fix
    next: end
  - id: end
    type: end
    title: End
""",
            encoding="utf-8",
        )
        (docs / "broad-review.md").write_text(
            "VERDICT: REJECTED\nFinding antigo e amplo.\n",
            encoding="utf-8",
        )
        (docs / "logo-current.png").write_bytes(b"physical logo evidence")
        runner = StepRunner(
            process_path=process_path,
            state_path=state_dir / "engine_state.yml",
            project_root=project_root,
        )
        runner.init_state()
        state = runner.state_mgr.load()
        state.current_node = "broad.review"
        state.node_status = "ready"
        state.active_fix_return = {
            "fix_node": "fix",
            "audit_entry_node": "broad.review",
            "review_node": "broad.review",
            "evidence_origin": "broad.review",
            "review_mode": "origin_fallback",
            "gate_node": "acceptance",
            "review_context": "Audite apenas o logo 40% maior na S02.",
        }
        runner.state_mgr.save()

        def focal_review(**kwargs):
            assert "Audite apenas o logo 40% maior na S02." in kwargs["task"]
            assert "Reexecutar todo o produto" not in kwargs["task"]
            assert "baseline anterior à correção" in kwargs["task"]
            assert "artefato corrente produzido pelo fix" in kwargs["task"]
            assert "RECONCILIAÇÃO FOCAL DO RECIBO" in kwargs["task"]
            assert "docs/broad-review.md" in kwargs["task"]
            output = (
                "VERDICT: APPROVED\n"
                "Logo medido no APK atual e confirmado no dispositivo.\n\n"
                "```yaml\n"
                "focal_evidence:\n"
                "  coverage_complete: true\n"
                "  finding_kind: ui_visual\n"
                "  evidence_level: physical_e2e\n"
                "  data_origin: local_product\n"
                "  mock_only: false\n"
                "  journey: [instalar APK, abrir S02, medir logo]\n"
                "  visual_evidence: [docs/logo-current.png]\n"
                "  claims:\n"
                "    - requirement: logo 40% maior na S02\n"
                "      expected: logo ampliado\n"
                "      observed: logo medido no APK atual\n"
                "      status: PASS\n"
                "      evidence: [docs/logo-current.png]\n"
                "```"
            )
            (docs / "broad-review.md").write_text(output, encoding="utf-8")
            return DelegateResult(
                success=True,
                output=output,
                files_created=[],
                files_modified=["docs/broad-review.md"],
            )

        with patch(
            "ft.engine.runner.delegate_to_llm",
            side_effect=focal_review,
        ):
            runner._run_review(runner.graph.get_node("broad.review"))

        reviewed = runner.state_mgr.load()
        assert reviewed.current_node == "acceptance"
        assert reviewed.active_fix_return is None
        assert (docs / "broad-review.md").read_text(encoding="utf-8").startswith(
            "VERDICT: APPROVED"
        )

    def test_runtime_focal_review_refuses_stale_canonical_report(
        self,
        tmp_path,
    ):
        project_root = tmp_path / "project"
        docs = project_root / "docs"
        state_dir = project_root / "state"
        docs.mkdir(parents=True)
        state_dir.mkdir()
        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: focal_runtime_stale_receipt
title: Focal runtime stale receipt
nodes:
  - id: fix
    type: build
    title: Fix
    next: physical.review
  - id: physical.review
    type: review
    title: Physical review
    outputs: [docs/physical-review.md]
    validators:
      - file_exists: docs/physical-review.md
    on_fail:
      human_gate: Corrigir divergência focal.
      goto: fix
    next: acceptance
  - id: acceptance
    type: human_gate
    title: Acceptance
    reject_next: fix
    next: end
  - id: end
    type: end
    title: End
""",
            encoding="utf-8",
        )
        (docs / "physical-review.md").write_text(
            "VERDICT: REJECTED\nRecibo anterior.\n",
            encoding="utf-8",
        )
        (docs / "current.txt").write_text("prova corrente\n", encoding="utf-8")
        runner = StepRunner(
            process_path=process_path,
            state_path=state_dir / "engine_state.yml",
            project_root=project_root,
        )
        runner.init_state()
        state = runner.state_mgr.load()
        state.current_node = "physical.review"
        state.node_status = "ready"
        state.active_fix_return = {
            "fix_node": "fix",
            "audit_entry_node": "physical.review",
            "review_node": "physical.review",
            "evidence_origin": "physical.review",
            "review_mode": "origin_fallback",
            "review_context": "Audite apenas o finding focal.",
        }
        runner.state_mgr.save()

        output = (
            "VERDICT: APPROVED\n"
            "```yaml\n"
            "focal_evidence:\n"
            "  coverage_complete: true\n"
            "  finding_kind: functional\n"
            "  evidence_level: integration\n"
            "  data_origin: local_product\n"
            "  mock_only: false\n"
            "  journey: [executar capacidade focal]\n"
            "  claims:\n"
            "    - requirement: finding focal\n"
            "      expected: corrigido\n"
            "      observed: corrigido no produto corrente\n"
            "      status: PASS\n"
            "      evidence: [docs/current.txt]\n"
            "```"
        )
        with patch(
            "ft.engine.runner.delegate_to_llm",
            return_value=DelegateResult(True, output, [], []),
        ):
            runner._run_review(runner.graph.get_node("physical.review"))

        rejected = runner.state_mgr.load()
        assert rejected.current_node == "physical.review"
        assert rejected.node_status == "pending_fix"
        assert "EVIDENCE_RECEIPT_STALE" in rejected.pending_fix["feedback"]

    def test_review_output_snapshot_tracks_later_outputs(self, tmp_path):
        project_root = tmp_path / "project"
        docs = project_root / "docs"
        evidence = docs / "evidence"
        state_dir = project_root / "state"
        evidence.mkdir(parents=True)
        state_dir.mkdir()
        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: multi_output_review
title: Multi-output review
nodes:
  - id: review
    type: review
    title: Review
    outputs:
      - docs/immutable-matrix.yml
      - docs/canonical-report.yml
      - docs/evidence/
    next: end
  - id: end
    type: end
    title: End
""",
            encoding="utf-8",
        )
        (docs / "immutable-matrix.yml").write_text("status: active\n", encoding="utf-8")
        report = docs / "canonical-report.yml"
        report.write_text("verdict: REJECTED\n", encoding="utf-8")
        (evidence / "before.txt").write_text("before\n", encoding="utf-8")
        runner = StepRunner(
            process_path=process_path,
            state_path=state_dir / "engine_state.yml",
            project_root=project_root,
        )
        node = runner.graph.get_node("review")

        before = runner._review_outputs_snapshot(node)
        report.write_text("verdict: APPROVED\n", encoding="utf-8")
        after_report = runner._review_outputs_snapshot(node)
        (evidence / "after.txt").write_text("after\n", encoding="utf-8")
        after_evidence = runner._review_outputs_snapshot(node)

        assert before
        assert before != after_report
        assert after_report != after_evidence

    def test_runtime_focal_review_refuses_mock_only_ui_data_approval(self, tmp_path):
        project_root = tmp_path / "project"
        docs = project_root / "docs"
        state_dir = project_root / "state"
        docs.mkdir(parents=True)
        state_dir.mkdir()
        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: focal_ui_data_review
title: Focal UI data review
nodes:
  - id: fix
    type: build
    title: Fix
    next: physical.review
  - id: physical.review
    type: review
    title: Physical review
    no_pre_seed: true
    preserve_outputs_on_reentry: true
    outputs: [docs/physical-review.md]
    validators:
      - file_exists: docs/physical-review.md
    on_fail:
      human_gate: Evidência focal insuficiente.
      goto: fix
    next: acceptance
  - id: acceptance
    type: human_gate
    title: Acceptance
    reject_next: fix
    next: end
  - id: end
    type: end
    title: End
""",
            encoding="utf-8",
        )
        (docs / "physical-review.md").write_text(
            "VERDICT: REJECTED\nBaseline anterior.\n",
            encoding="utf-8",
        )
        (docs / "s44-mock.png").write_bytes(b"mocked screen")
        runner = StepRunner(
            process_path=process_path,
            state_path=state_dir / "engine_state.yml",
            project_root=project_root,
        )
        runner.init_state()
        state = runner.state_mgr.load()
        state.current_node = "physical.review"
        state.node_status = "ready"
        state.active_fix_return = {
            "fix_node": "fix",
            "audit_entry_node": "physical.review",
            "review_node": "physical.review",
            "evidence_origin": "physical.review",
            "review_mode": "origin_fallback",
            "gate_node": "acceptance",
            "review_context": (
                "Na tela S44, o telefone cadastrado no FastAPI não aparece."
            ),
        }
        runner.state_mgr.save()

        def false_approval(**kwargs):
            assert "CONTRATO GLOBAL DE FIDELIDADE" in kwargs["task"]
            return DelegateResult(
                success=True,
                output=(
                    "```yaml\n"
                    "focal_evidence:\n"
                    "  coverage_complete: true\n"
                    "  finding_kind: ui_data\n"
                    "  evidence_level: component\n"
                    "  data_origin: fixture\n"
                    "  mock_only: true\n"
                    "  journey: [render S44 mockada]\n"
                    "  visual_evidence: [docs/s44-mock.png]\n"
                    "  claims:\n"
                    "    - requirement: telefone aparece na S44\n"
                    "      expected: telefone visível\n"
                    "      observed: fixture visível\n"
                    "      status: PASS\n"
                    "      evidence: [docs/s44-mock.png]\n"
                    "```\n\n"
                    "VERDICT: APPROVED"
                ),
                files_created=[],
                files_modified=[],
            )

        with patch("ft.engine.runner.delegate_to_llm", side_effect=false_approval):
            runner._run_review(runner.graph.get_node("physical.review"))

        reviewed = runner.state_mgr.load()
        assert reviewed.current_node == "physical.review"
        assert reviewed.node_status == "ready"
        assert reviewed.pending_fix is None
        assert "EVIDENCE_FIDELITY_REJECTED" in (
            reviewed.active_fix_return["focal_evidence_feedback"]
        )
        assert "mock" in (
            reviewed.active_fix_return["focal_evidence_feedback"].casefold()
        )

    def test_declared_focal_review_uses_headless_evidence_contract(self, tmp_path):
        project_root = tmp_path / "project"
        docs = project_root / "docs"
        state_dir = project_root / "state"
        contract_dir = project_root / ".ft"
        docs.mkdir(parents=True)
        state_dir.mkdir()
        contract_dir.mkdir()
        (contract_dir / "project.yml").write_text(
            "validation:\n"
            "  schema_version: 1\n"
            "  mode: disabled\n"
            "  reason: Python SDK sem interface gráfica\n"
            "  test_identity:\n"
            "    policy: not_required\n"
            "    path: docs/test-identity.json\n"
            "  platforms: {}\n",
            encoding="utf-8",
        )
        (docs / "headless-regression.txt").write_text(
            "25/25 programmatic checks passed\n",
            encoding="utf-8",
        )
        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: focal_headless_review
title: Focal headless review
nodes:
  - id: fix
    type: build
    title: Fix
    fix_review: review
    next: review
  - id: review
    type: review
    title: Headless review
    no_pre_seed: true
    outputs: [docs/headless-review.md]
    validators:
      - file_exists: docs/headless-review.md
    on_fail:
      human_gate: Corrigir divergência focal.
      goto: fix
    next: acceptance
  - id: acceptance
    type: human_gate
    title: Acceptance
    reject_next: fix
    next: end
  - id: end
    type: end
    title: End
""",
            encoding="utf-8",
        )
        runner = StepRunner(
            process_path=process_path,
            state_path=state_dir / "engine_state.yml",
            project_root=project_root,
        )
        runner.init_state()
        state = runner.state_mgr.load()
        state.current_node = "review"
        state.node_status = "ready"
        state.active_fix_return = {
            "fix_node": "fix",
            "audit_entry_node": "review",
            "review_node": "review",
            "evidence_origin": "review",
            "review_mode": "declared",
            "review_context": (
                "EVIDENCE_FIDELITY_REJECTED: finding de dados visíveis deve "
                "declarar finding_kind: ui_data. Confirme evidência visual "
                "quando o finding for de UI."
            ),
        }
        runner.state_mgr.save()

        report = (
            "VERDICT: APPROVED\n\n"
            "```yaml\n"
            "focal_evidence:\n"
            "  coverage_complete: true\n"
            "  finding_kind: technical\n"
            "  evidence_level: integration\n"
            "  data_origin: local_product\n"
            "  mock_only: false\n"
            "  journey: [execute public SDK contract, inspect sanitized result]\n"
            "  visual_evidence: []\n"
            "  claims:\n"
            "    - requirement: headless SDK contract\n"
            "      expected: all programmatic checks pass\n"
            "      observed: 25/25 checks passed\n"
            "      status: PASS\n"
            "      evidence: [docs/headless-regression.txt]\n"
            "```\n"
        )
        wrong_kind_report = report.replace(
            "finding_kind: technical",
            "finding_kind: ui_data",
        )
        attempts = 0

        def headless_approval(**kwargs):
            nonlocal attempts
            assert "AUDITORIA FOCAL HEADLESS" in kwargs["task"]
            assert "não crie, solicite ou use tela" in kwargs["task"].casefold()
            selected_report = wrong_kind_report if attempts == 0 else report
            if attempts:
                assert "CORREÇÃO DO RECIBO FOCAL" in kwargs["task"]
                assert "não altere o produto" in kwargs["task"]
            attempts += 1
            (docs / "headless-review.md").write_text(
                selected_report,
                encoding="utf-8",
            )
            return DelegateResult(True, "DONE", [], ["docs/headless-review.md"])

        with patch("ft.engine.runner.delegate_to_llm", side_effect=headless_approval):
            runner._run_review(runner.graph.get_node("review"))
            retrying = runner.state_mgr.load()
            assert retrying.current_node == "review"
            assert retrying.node_status == "ready"
            assert retrying.pending_fix is None
            assert "headless" in (
                retrying.active_fix_return["focal_evidence_feedback"].casefold()
            )

            runner._run_review(runner.graph.get_node("review"))

        reviewed = runner.state_mgr.load()
        assert reviewed.current_node == "acceptance"
        assert reviewed.active_fix_return is None
        assert "focal_evidence_retries" not in reviewed.metrics

    def test_opencode_review_and_retry_use_bounded_restricted_options(self, tmp_path):
        project_root = tmp_path / "project"
        docs = project_root / "docs"
        state_dir = project_root / "state"
        docs.mkdir(parents=True)
        state_dir.mkdir()

        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: test_process
version: "0.1.0"
title: "Test"
nodes:
  - id: ft.review.screenshot
    type: review
    title: Screenshot Review
    description: Tirar screenshots e comparar com docs/ui_criteria.md.
    executor: claude
    max_turns: 60
    outputs:
      - docs/screenshots/
      - docs/screenshot-review.md
    validators:
      - file_exists: docs/screenshot-review.md
    next: ft.end
  - id: ft.end
    type: end
    title: End
"""
        )

        runner = StepRunner(
            process_path=process_path,
            state_path=state_dir / "engine_state.yml",
            project_root=project_root,
            llm_engine="opencode",
        )
        runner.init_state()
        node = runner.graph.get_node("ft.review.screenshot")

        first_result = DelegateResult(
            success=True,
            output="DONE",
            files_created=[],
            files_modified=[],
        )

        def retry_side_effect(**kwargs):
            assert kwargs["llm_engine"] == "opencode"
            assert kwargs["opencode_restrict_tools"] is True
            assert kwargs["opencode_steps"] == 10
            assert kwargs["max_turns"] == 60
            (docs / "screenshot-review.md").write_text("APPROVED\n")
            return DelegateResult(
                success=True,
                output="DONE",
                files_created=["docs/screenshot-review.md"],
                files_modified=[],
            )

        with (
            patch("ft.engine.runner.delegate_to_llm", return_value=first_result) as delegate_mock,
            patch("ft.engine.runner.delegate_with_feedback", side_effect=retry_side_effect) as retry_mock,
        ):
            runner._run_review(node)

        first_kwargs = delegate_mock.call_args.kwargs
        assert first_kwargs["llm_engine"] == "opencode"
        assert first_kwargs["opencode_restrict_tools"] is True
        assert first_kwargs["opencode_steps"] == 10
        assert first_kwargs["max_turns"] == 60
        assert "Descricao especifica do node" in first_kwargs["task"]
        assert "Arquivo: docs/screenshot-review.md" in first_kwargs["task"]
        assert "use APPROVED WITH NOTES, nao BLOCKED" in first_kwargs["task"]
        assert retry_mock.called

        state = runner.state_mgr.load()
        assert state.current_node == "ft.end"

    def test_review_report_with_blocked_status_does_not_approve(self, tmp_path):
        project_root = tmp_path / "project"
        docs = project_root / "docs"
        state_dir = project_root / "state"
        docs.mkdir(parents=True)
        state_dir.mkdir()

        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: test_process
version: "0.1.0"
title: "Test"
nodes:
  - id: ft.review.visual
    type: review
    title: Visual Review
    executor: claude
    outputs:
      - docs/visual-review.md
    validators:
      - file_exists: docs/visual-review.md
    next: ft.end
  - id: ft.end
    type: end
    title: End
"""
        )

        runner = StepRunner(
            process_path=process_path,
            state_path=state_dir / "engine_state.yml",
            project_root=project_root,
            llm_engine="opencode",
        )
        runner.init_state()
        node = runner.graph.get_node("ft.review.visual")

        def delegate_side_effect(**kwargs):
            (docs / "visual-review.md").write_text("**STATUS:** BLOCKED\nNao consegui revisar.\n")
            return DelegateResult(
                success=True,
                output="DONE",
                files_created=["docs/visual-review.md"],
                files_modified=[],
            )

        with patch("ft.engine.runner.delegate_to_llm", side_effect=delegate_side_effect):
            runner._run_review(node)

        state = runner.state_mgr.load()
        assert state.node_status == "blocked"
        assert state.current_node == "ft.review.visual"
        assert "BLOCKED" in state.blocked_reason

    def test_review_approved_with_notes_ignores_incidental_reject_words(self, tmp_path):
        project_root = tmp_path / "project"
        docs = project_root / "docs"
        state_dir = project_root / "state"
        docs.mkdir(parents=True)
        state_dir.mkdir()

        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: test_process
version: "0.1.0"
title: "Test"
nodes:
  - id: ft.review.visual
    type: review
    title: Visual Review
    executor: claude
    outputs:
      - docs/visual-review.md
    validators:
      - file_exists: docs/visual-review.md
    next: ft.end
  - id: ft.end
    type: end
    title: End
""",
            encoding="utf-8",
        )

        runner = StepRunner(
            process_path=process_path,
            state_path=state_dir / "engine_state.yml",
            project_root=project_root,
        )
        runner.init_state()
        node = runner.graph.get_node("ft.review.visual")

        def delegate_side_effect(**kwargs):
            (docs / "visual-review.md").write_text(
                "# Visual Review\n\n"
                "Resultado: APPROVED WITH NOTES\n\n"
                "| Critério | Status | Evidência |\n"
                "|---|---|---|\n"
                "| C07 | PASS | docs/screenshots/07-confirm-reject.png cobre confirmação. |\n"
                "| C15 | PASS | Comando `ft reject \"motivo\"` documentado. |\n\n"
                "Notas: nenhum estado BLOCKED observado durante a revisão.\n",
                encoding="utf-8",
            )
            return DelegateResult(
                success=True,
                output="DONE",
                files_created=["docs/visual-review.md"],
                files_modified=[],
            )

        with patch("ft.engine.runner.delegate_to_llm", side_effect=delegate_side_effect):
            runner._run_review(node)

        state = runner.state_mgr.load()
        assert state.current_node == "ft.end"
        assert state.node_status == "ready"
        assert state.gate_log["ft.review.visual"] == "APPROVED WITH NOTES"

    def test_review_max_turns_uses_recovery_feedback_before_blocking(self, tmp_path):
        project_root = tmp_path / "project"
        docs = project_root / "docs"
        state_dir = project_root / "state"
        docs.mkdir(parents=True)
        state_dir.mkdir()

        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: test_process
version: "0.1.0"
title: "Test"
nodes:
  - id: ft.review.visual
    type: review
    title: Visual Review
    executor: claude
    outputs:
      - docs/visual-review.md
    validators:
      - file_exists: docs/visual-review.md
    next: ft.end
  - id: ft.end
    type: end
    title: End
""",
            encoding="utf-8",
        )

        runner = StepRunner(
            process_path=process_path,
            state_path=state_dir / "engine_state.yml",
            project_root=project_root,
        )
        runner.init_state()
        node = runner.graph.get_node("ft.review.visual")

        def recovery_side_effect(**kwargs):
            assert "RECUPERACAO DE REVIEW APOS INTERRUPCAO/MAX_TURNS" in kwargs["feedback"]
            assert "ANALISE PARCIAL PRESERVADA" in kwargs["feedback"]
            assert "regressão material no contrato" in kwargs["feedback"]
            assert "nao converta um achado parcial" in kwargs["feedback"].lower()
            (docs / "visual-review.md").write_text("Resultado: APPROVED WITH NOTES\n", encoding="utf-8")
            return DelegateResult(
                success=True,
                output="DONE",
                files_created=["docs/visual-review.md"],
                files_modified=[],
            )

        with (
            patch(
                "ft.engine.runner.delegate_to_llm",
                return_value=DelegateResult(
                    success=False,
                    output=(
                        "Achado parcial: regressão material no contrato.\n"
                        "Reached maximum number of turns (60)"
                    ),
                    files_created=[],
                    files_modified=[],
                ),
            ),
            patch("ft.engine.runner.delegate_with_feedback", side_effect=recovery_side_effect) as recovery_mock,
            patch(
                "ft.engine.runner.run_validators", wraps=run_validators
            ) as validator_mock,
        ):
            runner._run_review(node)

        assert recovery_mock.called
        # early check + pre-check pós-falha + check pós-recovery. O último
        # resultado é reutilizado no fechamento, sem uma quarta suíte idêntica.
        assert validator_mock.call_count == 3
        state = runner.state_mgr.load()
        assert state.current_node == "ft.end"
        assert state.gate_log["ft.review.visual"] == "APPROVED WITH NOTES"

    def test_mandatory_review_regenerates_preexisting_valid_report(self, tmp_path):
        project_root = tmp_path / "project"
        docs = project_root / "docs"
        state_dir = project_root / "state"
        docs.mkdir(parents=True)
        state_dir.mkdir()
        report = docs / "feature-review.md"
        report.write_text("Resultado: APPROVED\nrelatório antigo\n", encoding="utf-8")

        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: test_process
version: "0.1.0"
correction_policy:
  mandatory_after_implementation:
    - feature.review
nodes:
  - id: feature.review
    type: review
    title: Feature Review
    executor: claude
    outputs:
      - docs/feature-review.md
    validators:
      - file_exists: docs/feature-review.md
    next: feature.end
  - id: feature.end
    type: end
    title: End
""",
            encoding="utf-8",
        )

        runner = StepRunner(
            process_path=process_path,
            state_path=state_dir / "engine_state.yml",
            project_root=project_root,
        )
        runner.init_state()
        node = runner.graph.get_node("feature.review")

        def delegate_side_effect(**kwargs):
            assert not report.exists()
            report.write_text("Resultado: APPROVED\nrelatório novo\n", encoding="utf-8")
            return DelegateResult(
                success=True,
                output="Resultado: APPROVED",
                files_created=["docs/feature-review.md"],
                files_modified=[],
            )

        with patch("ft.engine.runner.delegate_to_llm", side_effect=delegate_side_effect) as delegated:
            runner._run_review(node)

        assert delegated.called
        assert "relatório novo" in report.read_text(encoding="utf-8")
        assert runner.state_mgr.load().current_node == "feature.end"

    def test_stakeholder_message_survives_interrupted_delegation(self, tmp_path):
        project_root = tmp_path / "project"
        state_dir = project_root / "state"
        state_dir.mkdir(parents=True)
        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: test_process
version: "0.1.0"
nodes:
  - id: feature.implement
    type: build
    title: Implement
    executor: claude
    next: feature.end
  - id: feature.end
    type: end
    title: End
""",
            encoding="utf-8",
        )
        runner = StepRunner(
            process_path=process_path,
            state_path=state_dir / "engine_state.yml",
            project_root=project_root,
        )
        runner.init_state()
        state = runner.state_mgr.load()
        state.last_approval_message = "corrigir branches e current_step"
        runner.state_mgr.save()
        node = runner.graph.get_node("feature.implement")

        with (
            patch(
                "ft.engine.runner.delegate_to_llm",
                side_effect=KeyboardInterrupt,
            ),
            pytest.raises(KeyboardInterrupt),
        ):
            runner._run_llm_step(node)

        recovered = runner.state_mgr.load()
        assert recovered.last_approval_message == "corrigir branches e current_step"

    @pytest.mark.parametrize("delegate_success", [True, False])
    def test_review_rejected_verdict_routes_to_on_fail_before_validation_retry(
        self,
        tmp_path,
        delegate_success,
    ):
        project_root = tmp_path / "project"
        docs = project_root / "docs"
        state_dir = project_root / "state"
        docs.mkdir(parents=True)
        state_dir.mkdir()

        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: test_process
version: "0.1.0"
title: "Test"
nodes:
  - id: ft.review.visual
    type: review
    title: Visual Review
    executor: claude
    outputs:
      - docs/visual-review.md
    validators:
      - file_exists: docs/visual-review.md
      - command_succeeds: "python -c \\"from pathlib import Path; text=Path('docs/visual-review.md').read_text().lower(); assert 'rejected' not in text, 'review rejeitado'\\""
    on_fail:
      human_gate: Corrija a UI antes de continuar.
      goto: ft.frontend.fix
    next: ft.end
  - id: ft.frontend.fix
    type: build
    title: Fix Frontend
    executor: claude
    next: ft.review.visual
  - id: ft.end
    type: end
    title: End
""",
            encoding="utf-8",
        )

        runner = StepRunner(
            process_path=process_path,
            state_path=state_dir / "engine_state.yml",
            project_root=project_root,
        )
        runner.init_state()
        node = runner.graph.get_node("ft.review.visual")

        def delegate_side_effect(**kwargs):
            (docs / "visual-review.md").write_text(
                "Resultado: REJECTED\n\nFalha bloqueante de responsividade mobile.\n",
                encoding="utf-8",
            )
            return DelegateResult(
                success=delegate_success,
                output="DONE" if delegate_success else "BLOCKED",
                files_created=["docs/visual-review.md"],
                files_modified=[],
            )

        with (
            patch("ft.engine.runner.delegate_to_llm", side_effect=delegate_side_effect),
            patch("ft.engine.runner.delegate_with_feedback", side_effect=AssertionError("should not retry report")),
        ):
            runner._run_review(node)

        state = runner.state_mgr.load()
        assert state.node_status == "pending_fix"
        assert state.pending_fix["goto"] == "ft.frontend.fix"
        assert "REJECTED" in state.pending_fix["feedback"]

    @pytest.mark.parametrize("delegate_success", [True, False])
    def test_review_rejected_with_passing_validators_routes_to_claude_on_fail(
        self,
        tmp_path,
        delegate_success,
    ):
        project_root = tmp_path / "project"
        docs = project_root / "docs"
        state_dir = project_root / "state"
        docs.mkdir(parents=True)
        state_dir.mkdir()

        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: feature
version: "1.0.0"
title: Feature
nodes:
  - id: feature.review
    type: review
    title: Review
    executor: claude
    outputs:
      - docs/feature-review.md
    validators:
      - file_exists: docs/feature-review.md
    on_fail:
      human_gate: Corrija a implementação.
      goto: feature.implement
    next: feature.acceptance
  - id: feature.implement
    type: build
    title: Implement
    executor: claude
    next: feature.review
  - id: feature.acceptance
    type: human_gate
    title: Acceptance
    executor: python
    next: feature.end
  - id: feature.end
    type: end
    title: End
""",
            encoding="utf-8",
        )

        runner = StepRunner(
            process_path=process_path,
            state_path=state_dir / "engine_state.yml",
            project_root=project_root,
        )
        runner.init_state()
        node = runner.graph.get_node("feature.review")

        def delegate_side_effect(**kwargs):
            (docs / "feature-review.md").write_text(
                "Resultado: REJECTED\n\nRegressão de contrato público.\n",
                encoding="utf-8",
            )
            return DelegateResult(
                success=delegate_success,
                output="Resultado: REJECTED" if delegate_success else "max turns",
                files_created=["docs/feature-review.md"],
                files_modified=[],
            )

        with (
            patch("ft.engine.runner.delegate_to_llm", side_effect=delegate_side_effect) as delegated,
            patch(
                "ft.engine.runner.delegate_with_feedback",
                side_effect=AssertionError("review rejeitado não deve entrar em recovery"),
            ),
        ):
            runner._run_review(node)

        assert delegated.call_count == 1
        state = runner.state_mgr.load()
        assert state.node_status == "pending_fix"
        assert state.pending_fix["goto"] == "feature.implement"
        assert "Regressão de contrato público" in state.pending_fix["feedback"]

    def test_bypass_human_gates_applies_on_fail_automatically(self, tmp_path):
        project_root = tmp_path / "project"
        docs = project_root / "docs"
        state_dir = project_root / "state"
        docs.mkdir(parents=True)
        state_dir.mkdir()

        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: test_process
version: "0.1.0"
title: "Test"
nodes:
  - id: ft.review.visual
    type: review
    title: Visual Review
    executor: claude
    on_fail:
      human_gate: Corrija a UI antes de continuar.
      goto: ft.frontend.fix
    next: ft.end
  - id: ft.frontend.fix
    type: build
    title: Fix Frontend
    executor: claude
    next: ft.review.visual
  - id: ft.end
    type: end
    title: End
""",
            encoding="utf-8",
        )

        runner = StepRunner(
            process_path=process_path,
            state_path=state_dir / "engine_state.yml",
            project_root=project_root,
        )
        runner.init_state()
        runner._auto_approve = True
        runner._bypass_human_gates = True

        runner._handle_on_fail(runner.graph.get_node("ft.review.visual"), "Resultado: REJECTED")

        state = runner.state_mgr.load()
        assert state.current_node == "ft.frontend.fix"
        assert state.node_status == "running"
        assert state.pending_fix is None
        assert "Resultado: REJECTED" in state.last_approval_message

    def test_process_can_authorize_automatic_focal_on_fail(self, tmp_path):
        project_root = tmp_path / "project"
        state_dir = project_root / "state"
        state_dir.mkdir(parents=True)
        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: automatic_fix
version: "1.0.0"
title: Automatic fix
nodes:
  - id: review
    type: review
    title: Review
    executor: codex
    on_fail:
      human_gate: Corrigir finding focal.
      goto: fix
      automatic: true
    next: end
  - id: fix
    type: build
    title: Fix
    executor: codex
    next: end
  - id: end
    type: end
    title: End
""",
            encoding="utf-8",
        )
        runner = StepRunner(
            process_path=process_path,
            state_path=state_dir / "engine_state.yml",
            project_root=project_root,
        )
        runner.init_state()
        runner._auto_approve = True
        runner._bypass_human_gates = False

        runner._handle_on_fail(
            runner.graph.get_node("review"),
            "Finding reproduzível",
        )

        state = runner.state_mgr.load()
        assert state.current_node == "fix"
        assert state.node_status == "running"
        assert state.pending_fix is None
        assert "Finding reproduzível" in state.last_approval_message

    def test_directed_fix_can_return_only_to_rejecting_review(self, tmp_path):
        project_root = tmp_path / "project"
        state_dir = project_root / "state"
        state_dir.mkdir(parents=True)
        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: focal_validation
version: "1.0.0"
title: Focal validation
nodes:
  - id: foundation
    type: build
    title: Foundation
    executor: codex
    next: combined.review
  - id: combined.review
    type: review
    title: Combined review
    executor: codex
    next: fix
  - id: fix
    type: build
    title: Shared fix
    executor: codex
    next: combined.review
  - id: integrated.verify
    type: gate
    title: Integrated verify
    executor: python
    next: physical.review
  - id: physical.review
    type: review
    title: Physical review
    executor: codex
    on_fail:
      human_gate: Corrigir somente a divergência física.
      goto: fix
    next: end
  - id: end
    type: end
    title: End
""",
            encoding="utf-8",
        )
        runner = StepRunner(
            process_path=process_path,
            state_path=state_dir / "engine_state.yml",
            project_root=project_root,
        )
        runner.init_state()
        state = runner.state_mgr.load()
        state.completed_nodes = [
            "foundation",
            "combined.review",
            "fix",
            "integrated.verify",
        ]
        state.current_node = "physical.review"
        state.node_status = "ready"
        runner.state_mgr.save()

        runner._handle_on_fail(
            runner.graph.get_node("physical.review"),
            "VIS-001 reproduzível",
        )

        pending = runner.state_mgr.load()
        assert pending.pending_fix["origin"] == "physical.review"
        assert runner.apply_fix("Corrigir VIS-001")

        fixing = runner.state_mgr.load()
        assert fixing.current_node == "fix"
        assert fixing.active_fix_return["fix_node"] == "fix"
        assert fixing.active_fix_return["review_node"] == "physical.review"
        assert "Corrigir VIS-001" in fixing.active_fix_return["review_context"]
        assert "OWNERSHIP DA EVIDÊNCIA" in fixing.last_approval_message
        assert "não invente uma alteração de código" in fixing.last_approval_message
        assert fixing.completed_nodes == [
            "foundation",
            "combined.review",
            "integrated.verify",
        ]

        runner._advance_state("fix", "combined.review")

        resumed = runner.state_mgr.load()
        assert resumed.current_node == "physical.review"
        assert resumed.active_fix_return["review_mode"] == "origin_fallback"
        assert (
            resumed.active_fix_return["audit_entry_node"]
            == "physical.review"
        )
        assert "integrated.verify" in resumed.completed_nodes
        assert "fix" in resumed.completed_nodes

        physical = runner.graph.get_node("physical.review")
        prompt, _deny = runner._build_review_task_context(
            physical,
            runner._capture_delegation_llm_selection(
                resumed,
                node=physical,
            ),
        )
        assert "Corrigir VIS-001" in prompt
        assert "prompt amplo original deste review está suspenso" in prompt

        runner._advance_state("physical.review", "end")
        assert runner.state_mgr.load().active_fix_return is None

    def test_human_rejection_can_fix_and_return_to_evidence_review(self, tmp_path):
        project_root = tmp_path / "project"
        state_dir = project_root / "state"
        state_dir.mkdir(parents=True)
        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: terminal_validation
version: "1.0.0"
title: Terminal validation
nodes:
  - id: foundation
    type: build
    title: Foundation
    executor: codex
    next: fix
  - id: fix
    type: build
    title: Shared fix
    executor: codex
    outputs:
      - project/fix.md
    next: broad.review
  - id: broad.review
    type: review
    title: Broad review
    executor: codex
    next: integrated.verify
  - id: integrated.verify
    type: gate
    title: Integrated verify
    executor: python
    next: physical.review
  - id: physical.review
    type: review
    title: Physical review
    executor: codex
    outputs:
      - docs/physical-review.md
    on_fail:
      human_gate: Corrigir somente a divergência física.
      goto: fix
    next: visual.gate
  - id: visual.gate
    type: human_gate
    title: Visual gate
    executor: python
    reject_next: fix
    next: end
  - id: end
    type: end
    title: End
""",
            encoding="utf-8",
        )
        runner = StepRunner(
            process_path=process_path,
            state_path=state_dir / "engine_state.yml",
            project_root=project_root,
        )
        runner.init_state()
        state = runner.state_mgr.load()
        state.completed_nodes = [
            "foundation",
            "fix",
            "broad.review",
            "integrated.verify",
            "physical.review",
        ]
        state.current_node = "visual.gate"
        state.node_status = "awaiting_approval"
        state.pending_approval = "visual.gate"
        state.artifacts = {
            "fix": "project/fix.md",
            "physical-review": "docs/physical-review.md",
            "integrated": "docs/integrated-receipt.json",
        }
        runner.state_mgr.save()

        assert runner.reject_with_origin_audit(
            "S12 ainda diverge do mockup"
        )

        fixing = runner.state_mgr.load()
        assert fixing.current_node == "fix"
        assert fixing.pending_approval is None
        assert fixing.active_fix_return["fix_node"] == "fix"
        assert fixing.active_fix_return["review_node"] == "physical.review"
        assert fixing.active_fix_return["gate_node"] == "visual.gate"
        assert (
            "S12 ainda diverge do mockup"
            in fixing.active_fix_return["review_context"]
        )
        assert fixing.completed_nodes == [
            "foundation",
            "broad.review",
            "integrated.verify",
        ]
        assert fixing.artifacts == {
            "integrated": "docs/integrated-receipt.json",
        }
        assert "S12 ainda diverge" in fixing.last_approval_message

        runner._advance_state("fix", "broad.review")

        resumed = runner.state_mgr.load()
        assert resumed.current_node == "physical.review"
        assert resumed.active_fix_return["fix_node"] == "fix"
        assert resumed.active_fix_return["review_node"] == "physical.review"
        assert resumed.active_fix_return["gate_node"] == "visual.gate"
        assert "REVISÃO FOCAL OBRIGATÓRIA" in resumed.last_approval_message
        assert "S12 ainda diverge do mockup" in resumed.last_approval_message
        assert "integrated.verify" in resumed.completed_nodes

        runner._handle_on_fail(
            runner.graph.get_node("physical.review"),
            "A evidência do fix ainda diverge",
        )
        pending_again = runner.state_mgr.load()
        assert pending_again.pending_fix["return_gate"] == "visual.gate"
        assert runner.apply_fix("Corrigir somente a divergência remanescente")

        fixing_again = runner.state_mgr.load()
        assert fixing_again.current_node == "fix"
        assert fixing_again.active_fix_return["fix_node"] == "fix"
        assert fixing_again.active_fix_return["review_node"] == "physical.review"
        assert fixing_again.active_fix_return["gate_node"] == "visual.gate"
        assert (
            "Corrigir somente a divergência remanescente"
            in fixing_again.active_fix_return["review_context"]
        )

        runner._advance_state("fix", "broad.review")
        reviewed_again = runner.state_mgr.load()
        assert reviewed_again.current_node == "physical.review"
        assert (
            "Corrigir somente a divergência remanescente"
            in reviewed_again.last_approval_message
        )

        runner._advance_state("physical.review", "visual.gate")

        returned = runner.state_mgr.load()
        assert returned.current_node == "visual.gate"
        assert returned.active_fix_return is None
        assert returned.completed_nodes == [
            "foundation",
            "broad.review",
            "integrated.verify",
            "fix",
            "physical.review",
        ]

    def test_human_rejection_finds_review_through_completed_verify_gate(
        self,
        tmp_path,
    ):
        project_root = tmp_path / "project"
        state_dir = project_root / "state"
        state_dir.mkdir(parents=True)
        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: terminal_acceptance_after_verify
version: "1.0.0"
title: Terminal acceptance after verify
nodes:
  - id: foundation
    type: build
    title: Foundation
    next: combined.review
  - id: combined.review
    type: review
    title: Combined review
    next: integrated.verify
  - id: integrated.verify
    type: gate
    title: Integrated verify
    next: acceptance
  - id: acceptance
    type: human_gate
    title: Acceptance
    reject_next: fix
    next: end
  - id: fix
    type: build
    title: Focal fix
    fix_review: fix.review
    next: fix.review
  - id: fix.review
    type: review
    title: Review only the fix
    next: integrated.verify
  - id: end
    type: end
    title: End
""",
            encoding="utf-8",
        )
        runner = StepRunner(
            process_path=process_path,
            state_path=state_dir / "engine_state.yml",
            project_root=project_root,
        )
        runner.init_state()
        state = runner.state_mgr.load()
        state.completed_nodes = [
            "foundation",
            "combined.review",
            "integrated.verify",
        ]
        state.current_node = "acceptance"
        state.node_status = "awaiting_approval"
        state.pending_approval = "acceptance"
        runner.state_mgr.save()

        assert runner.reject_with_origin_audit("Fluxo público ainda incompleto")

        fixing = runner.state_mgr.load()
        assert fixing.current_node == "fix"
        assert fixing.pending_approval is None
        assert fixing.active_fix_return["evidence_origin"] == "combined.review"
        assert fixing.active_fix_return["review_node"] == "fix.review"
        assert fixing.active_fix_return["gate_node"] == "acceptance"
        assert fixing.completed_nodes == [
            "foundation",
            "combined.review",
            "integrated.verify",
        ]

    def test_human_rejection_uses_declared_fix_review_without_broad_rewind(
        self,
        tmp_path,
    ):
        project_root = tmp_path / "project"
        state_dir = project_root / "state"
        state_dir.mkdir(parents=True)
        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: declared_focal_review
version: "1.0.0"
title: Declared focal review
nodes:
  - id: foundation
    type: build
    title: Foundation
    next: fix
  - id: fix
    type: build
    title: Focal fix
    fix_review: fix.review
    next: fix.check
  - id: fix.check
    type: gate
    title: Focal check
    next: fix.review
  - id: fix.review
    type: review
    title: Review only the fix
    on_fail:
      human_gate: Fix ainda falha.
      goto: fix
    next: broad.review
  - id: broad.review
    type: review
    title: Broad review
    next: integrated.verify
  - id: integrated.verify
    type: gate
    title: Integrated verify
    next: evidence.review
  - id: evidence.review
    type: review
    title: Evidence review
    next: acceptance
  - id: acceptance
    type: human_gate
    title: Acceptance
    reject_next: fix
    next: end
  - id: end
    type: end
    title: End
""",
            encoding="utf-8",
        )
        runner = StepRunner(
            process_path=process_path,
            state_path=state_dir / "engine_state.yml",
            project_root=project_root,
        )
        runner.init_state()
        state = runner.state_mgr.load()
        state.completed_nodes = [
            "foundation",
            "fix",
            "fix.check",
            "fix.review",
            "broad.review",
            "integrated.verify",
            "evidence.review",
        ]
        state.current_node = "acceptance"
        state.node_status = "awaiting_approval"
        state.pending_approval = "acceptance"
        runner.state_mgr.save()

        assert runner.reject_with_origin_audit("O logo ainda está pequeno")

        fixing = runner.state_mgr.load()
        assert fixing.current_node == "fix"
        assert fixing.active_fix_return["audit_entry_node"] == "fix.check"
        assert fixing.active_fix_return["review_node"] == "fix.review"
        assert fixing.active_fix_return["evidence_origin"] == "evidence.review"
        assert fixing.active_fix_return["review_mode"] == "declared"
        assert fixing.completed_nodes == [
            "foundation",
            "broad.review",
            "integrated.verify",
            "evidence.review",
        ]

        runner._advance_state("fix", "fix.check")
        assert runner.state_mgr.load().current_node == "fix.check"
        runner._advance_state("fix.check", "fix.review")
        reviewing = runner.state_mgr.load()
        assert reviewing.current_node == "fix.review"
        assert "O logo ainda está pequeno" in reviewing.last_approval_message

        runner._advance_state("fix.review", "broad.review")
        returned = runner.state_mgr.load()
        assert returned.current_node == "acceptance"
        assert returned.active_fix_return is None
        assert "broad.review" in returned.completed_nodes
        assert "integrated.verify" in returned.completed_nodes
        assert "evidence.review" in returned.completed_nodes

    def test_llm_error_with_passing_validators_advances_node(self, tmp_path):
        project_root = tmp_path / "project"
        state_dir = project_root / "state"
        state_dir.mkdir(parents=True)

        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: test_process
version: "0.1.0"
title: "Test"
nodes:
  - id: ft.build.app
    type: build
    title: Build App
    executor: claude
    outputs:
      - src/app.py
    validators:
      - file_exists: src/app.py
    next: ft.end
  - id: ft.end
    type: end
    title: End
""",
            encoding="utf-8",
        )

        runner = StepRunner(
            process_path=process_path,
            state_path=state_dir / "engine_state.yml",
            project_root=project_root,
        )
        runner.init_state()
        node = runner.graph.get_node("ft.build.app")

        def delegate_side_effect(**kwargs):
            app = project_root / "src" / "app.py"
            app.parent.mkdir(parents=True)
            app.write_text("print('ok')\n", encoding="utf-8")
            return DelegateResult(
                success=False,
                output="Reached maximum number of turns",
                files_created=["src/app.py"],
                files_modified=[],
            )

        with patch("ft.engine.runner.delegate_to_llm", side_effect=delegate_side_effect):
            runner._run_llm_step(node)

        state = runner.state_mgr.load()
        assert state.current_node == "ft.end"
        assert state.gate_log["ft.build.app"] == "PASS"

    def test_review_recovery_blocking_screenshot_routes_to_auto_fix(self, tmp_path):
        project_root = tmp_path / "project"
        shots = project_root / "docs" / "screenshots"
        state_dir = project_root / "state"
        shots.mkdir(parents=True)
        state_dir.mkdir()
        (shots / "11-mobile-390x844-overflow-scroll-x175.png").write_bytes(b"not-empty")

        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: test_process
version: "0.1.0"
title: "Test"
nodes:
  - id: ft.review.visual
    type: review
    title: Visual Review
    executor: claude
    outputs:
      - docs/screenshots/
      - docs/visual-review.md
    validators:
      - file_exists: docs/visual-review.md
    on_fail:
      human_gate: Corrija a UI antes de continuar.
      goto: ft.frontend.fix
    next: ft.end
  - id: ft.frontend.fix
    type: build
    title: Fix Frontend
    executor: claude
    next: ft.review.visual
  - id: ft.end
    type: end
    title: End
""",
            encoding="utf-8",
        )

        runner = StepRunner(
            process_path=process_path,
            state_path=state_dir / "engine_state.yml",
            project_root=project_root,
        )
        runner.init_state()
        runner._auto_approve = True
        runner._bypass_human_gates = True
        node = runner.graph.get_node("ft.review.visual")

        with (
            patch(
                "ft.engine.runner.delegate_to_llm",
                return_value=DelegateResult(False, "Reached maximum number of turns", [], []),
            ),
            patch(
                "ft.engine.runner.delegate_with_feedback",
                return_value=DelegateResult(False, "Reached maximum number of turns", [], []),
            ),
        ):
            runner._run_review(node)

        report = project_root / "docs" / "visual-review.md"
        assert "Resultado: REJECTED" in report.read_text(encoding="utf-8")
        state = runner.state_mgr.load()
        assert state.current_node == "ft.frontend.fix"
        assert state.node_status == "running"
        assert state.pending_fix is None


class TestRewriteGuard:
    def test_no_pre_seed_only_removes_cycle_artifacts(self, tmp_path):
        project_root = tmp_path / "project"
        state_dir = project_root / "state"
        source_dir = project_root / "src" / "frontend"
        screenshots_dir = project_root / "docs" / "screenshots"
        source_dir.mkdir(parents=True)
        screenshots_dir.mkdir(parents=True)
        state_dir.mkdir()

        source = source_dir / "main.tsx"
        gitignore = project_root / ".gitignore"
        backlog = project_root / "docs" / "PROJECT_BACKLOG.md"
        task_list = project_root / "docs" / "task_list.md"
        screenshot = screenshots_dir / "desktop.png"
        marker = project_root / ".build_ok"
        source.write_text("export {};\n")
        gitignore.write_text("node_modules/\n")
        backlog.write_text("# Backlog\n")
        task_list.write_text("# Old task list\n")
        screenshot.write_bytes(b"old image")
        marker.write_text("ok\n")

        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: test_process
version: "0.1.0"
title: "Test"
artifact_policy:
  canonical:
    - docs/PROJECT_BACKLOG.md
  cycle:
    - docs/task_list.md
    - docs/screenshots/
    - .build_ok
nodes:
  - id: ft.frontend.scaffold
    no_pre_seed: true
    type: build
    title: Scaffold
    executor: llm_coder
    outputs:
      - src/frontend/
      - .gitignore
      - docs/PROJECT_BACKLOG.md
      - docs/task_list.md
      - docs/screenshots/
      - .build_ok
    next: ft.end
  - id: ft.end
    type: end
    title: End
"""
        )

        runner = StepRunner(
            process_path=process_path,
            state_path=state_dir / "engine_state.yml",
            project_root=project_root,
        )
        runner.init_state()
        runner._clear_no_pre_seed_outputs(runner.graph.get_node("ft.frontend.scaffold"))

        assert source.read_text() == "export {};\n"
        assert gitignore.read_text() == "node_modules/\n"
        assert backlog.read_text() == "# Backlog\n"
        assert not task_list.exists()
        assert not screenshots_dir.exists()
        assert not marker.exists()

    def test_no_pre_seed_can_preserve_cycle_outputs_for_reentry(self, tmp_path):
        project_root = tmp_path / "project"
        state_dir = project_root / "state"
        docs_dir = project_root / "docs"
        docs_dir.mkdir(parents=True)
        state_dir.mkdir()
        draft = docs_dir / "feature.md"
        draft.write_text("# Draft preservado\n")

        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: test_process
version: "0.1.0"
title: "Test"
artifact_policy:
  cycle:
    - docs/feature.md
nodes:
  - id: feature.discovery
    no_pre_seed: true
    preserve_outputs_on_reentry: true
    type: document
    title: Discovery
    outputs:
      - docs/feature.md
    next: end
  - id: end
    type: end
    title: End
""",
            encoding="utf-8",
        )

        runner = StepRunner(
            process_path=process_path,
            state_path=state_dir / "engine_state.yml",
            project_root=project_root,
        )
        runner.init_state()

        runner._clear_no_pre_seed_outputs(
            runner.graph.get_node("feature.discovery")
        )

        assert draft.read_text() == "# Draft preservado\n"

    def test_no_pre_seed_output_is_removed_before_document_delegation(self, tmp_path):
        project_root = tmp_path / "project"
        docs = project_root / "docs"
        state_dir = project_root / "state"
        docs.mkdir(parents=True)
        state_dir.mkdir()
        (docs / "PRD.md").write_text("# PRD\n\n## User Stories\nUS-01 base.\n")
        old_task_list = docs / "task_list.md"
        old_task_list.write_text("# OLD TASK LIST\nstale cycle content\n")

        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: test_process
version: "0.1.0"
title: "Test"
nodes:
  - id: ft.plan.01.task_list
    no_pre_seed: true
    type: document
    title: Task List
    executor: llm_coach
    outputs:
      - docs/task_list.md
    validators:
      - file_exists: docs/task_list.md
    next: ft.end
  - id: ft.end
    type: end
    title: End
"""
        )

        runner = StepRunner(
            process_path=process_path,
            state_path=state_dir / "engine_state.yml",
            project_root=project_root,
        )
        runner.init_state()
        node = runner.graph.get_node("ft.plan.01.task_list")

        def delegate_side_effect(**kwargs):
            assert not old_task_list.exists()
            assert "OLD TASK LIST" not in kwargs["task"]
            assert "PRD.md" in kwargs["task"]
            old_task_list.write_text("# New Task List\n")
            return DelegateResult(
                success=True,
                output="DONE",
                files_created=[],
                files_modified=[],
            )

        with patch(
            "ft.engine.runner.delegate_to_llm",
            side_effect=delegate_side_effect,
        ):
            runner._run_llm_step(node)

        state = runner.state_mgr.load()
        assert state.current_node == "ft.end"
        assert old_task_list.read_text() == "# New Task List\n"

    def test_document_retry_excludes_current_invalid_output_from_hyper_mode(self, tmp_path):
        project_root = tmp_path / "project"
        docs = project_root / "docs"
        state_dir = project_root / "state"
        docs.mkdir(parents=True)
        state_dir.mkdir()
        (docs / "PRD.md").write_text("# PRD\n\n## User Stories\nUS-01 criar clientes.\n")
        invalid_contract = docs / "api_contract.md"
        invalid_contract.write_text("# BAD API\nOpenSearch hallucination\n")

        process_path = tmp_path / "process.yml"
        process_path.write_text(
            """
id: test_process
version: "0.1.0"
title: "Test"
nodes:
  - id: ft.plan.03.api_contract
    type: document
    title: API Contract
    executor: llm_coach
    outputs:
      - docs/api_contract.md
    validators:
      - has_sections:
          path: docs/api_contract.md
          sections:
            - Base URL
    next: ft.end
  - id: ft.end
    type: end
    title: End
"""
        )

        runner = StepRunner(
            process_path=process_path,
            state_path=state_dir / "engine_state.yml",
            project_root=project_root,
        )
        runner.init_state()
        node = runner.graph.get_node("ft.plan.03.api_contract")

        def delegate_side_effect(**kwargs):
            assert "BAD API" not in kwargs["task"]
            assert "OpenSearch hallucination" not in kwargs["task"]
            assert "PRD.md" in kwargs["task"]
            invalid_contract.write_text("## Base URL\n\n## Endpoints\n")
            return DelegateResult(success=True, output="DONE", files_created=[], files_modified=[])

        with patch("ft.engine.runner.delegate_to_llm", side_effect=delegate_side_effect):
            runner._run_llm_step(node)

        state = runner.state_mgr.load()
        assert state.current_node == "ft.end"

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
        assert not (
            project_root / "project" / "state" / "prd_rewrite_baseline.md"
        ).exists()


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


class TestStatus:
    def test_progress_snapshot_reports_current_action_evolution_and_signal(
        self,
        tmp_path,
    ):
        log_path = tmp_path / "active.jsonl"
        events = [
            {
                "type": "item.updated",
                "item": {
                    "id": "todo-1",
                    "type": "todo_list",
                    "items": [
                        {"text": "inspecionar", "completed": True},
                        {"text": "auditar", "completed": False},
                    ],
                },
            },
            {
                "type": "item.completed",
                "item": {
                    "id": "change-1",
                    "type": "file_change",
                    "changes": [
                        {
                            "path": "/worktree/project/backend/app/api.py",
                            "kind": "update",
                        }
                    ],
                },
            },
            {
                "type": "item.completed",
                "item": {
                    "id": "cmd-1",
                    "type": "command_execution",
                    "command": "git diff --check",
                    "status": "completed",
                },
            },
            {
                "type": "item.started",
                "item": {
                    "id": "cmd-2",
                    "type": "command_execution",
                    "command": (
                        "ANDROID_SERIAL=sensitive-value sed -n '1,25p' "
                        "project/android/app/src/androidTest/java/com/wifire/go/"
                        "PhysicalPb024CommunityE2ETest.kt"
                    ),
                    "status": "in_progress",
                },
            },
        ]
        log_path.write_text(
            "\n".join(json.dumps(event) for event in events)
            + "\n[PRODUCTIVITY_RENEWED] source=process cpu_delta_ticks=5\n",
            encoding="utf-8",
        )

        snapshot = _llm_progress_snapshot(log_path)

        assert snapshot is not None
        assert snapshot.current == (
            "executando inspeção em `PhysicalPb024CommunityE2ETest.kt`"
        )
        assert snapshot.evolution == (
            "tarefas 1/2 · 1 comando concluído · 1 arquivo alterado"
        )
        assert snapshot.signal == "CPU/I/O do processo avançando"
        rendered = " ".join(
            value
            for value in (
                snapshot.current,
                snapshot.evolution,
                snapshot.signal,
            )
            if value
        )
        assert "sensitive-value" not in rendered

    def test_progress_snapshot_uses_exact_activity_sidecar_timestamp(
        self,
        tmp_path,
    ):
        from datetime import datetime, timezone

        from ft.engine.llm_activity import activity_log_path, activity_record

        log_path = tmp_path / "active.jsonl"
        event = json.dumps(
            {
                "type": "item.started",
                "item": {
                    "id": "cmd-live",
                    "type": "command_execution",
                    "command": "pytest tests/test_app.py -q",
                    "status": "in_progress",
                },
            }
        )
        log_path.write_text(event + "\n", encoding="utf-8")
        observed = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
        activity_log_path(log_path).write_text(
            activity_record(event, observed_at=observed) or "",
            encoding="utf-8",
        )

        snapshot = _llm_progress_snapshot(log_path)

        assert snapshot is not None
        assert snapshot.current == "executando testes focais em `test_app.py`"
        assert snapshot.current_started_at == observed

    def test_progress_snapshot_marks_completed_turn_as_terminal(self, tmp_path):
        log_path = tmp_path / "completed.jsonl"
        events = [
            {
                "type": "item.started",
                "item": {
                    "id": "cmd-finished",
                    "type": "command_execution",
                    "command": "pytest tests/test_lane.py -q",
                    "status": "in_progress",
                },
            },
            {
                "type": "item.completed",
                "item": {
                    "id": "summary",
                    "type": "agent_message",
                    "text": "NODE_SUMMARY:\n- verificado: testes verdes\nDONE",
                },
            },
            {
                "type": "turn.completed",
                "usage": {"input_tokens": 10, "output_tokens": 2},
            },
        ]
        log_path.write_text(
            "\n".join(json.dumps(event) for event in events) + "\n",
            encoding="utf-8",
        )

        snapshot = _llm_progress_snapshot(log_path)

        assert snapshot is not None
        assert snapshot.terminal is True
        assert snapshot.terminal_status == "completed"
        assert snapshot.current is None
        assert snapshot.signal == "chamada LLM concluída"

    def test_status_expands_active_banner_with_live_progress(
        self,
        runner_v2,
        capsys,
    ):
        runner_v2.init_state()
        log_path = runner_v2.state_mgr.path.parent / "llm_logs" / "current.jsonl"
        log_path.parent.mkdir(parents=True)
        log_path.write_text(
            json.dumps(
                {
                    "type": "item.started",
                    "item": {
                        "id": "cmd-live",
                        "type": "command_execution",
                        "command": (
                            "sed -n '1,30p' "
                            "project/backend/tests/test_community_business.py"
                        ),
                        "status": "in_progress",
                    },
                }
            )
            + "\n[PRODUCTIVITY_RENEWED] source=workspace files_delta=1\n",
            encoding="utf-8",
        )
        state = runner_v2.state_mgr.load()
        state.node_status = "delegated"
        state.active_llm_log = str(log_path)
        state.last_llm_log = str(log_path)
        runner_v2.state_mgr.save()

        runner_v2.status()

        out = capsys.readouterr().out
        assert "TRABALHO EM ANDAMENTO" in out
        assert "Agora: executando inspeção em `test_community_business.py`" in out
        assert "Sinais: worktree alterada" in out

    def test_status_preserves_engine_line_and_adds_model_effort(self, runner_v2, capsys):
        runner_v2.init_state()
        state = runner_v2.state_mgr.load()
        state.llm_engine = "codex"
        state.llm_model = "gpt-5.6-sol"
        state.llm_effort = "max"
        runner_v2.state_mgr.save()

        runner_v2.status()

        out = capsys.readouterr().out
        assert "LLM engine: codex" in out
        assert "LLM model: gpt-5.6-sol" in out
        assert "LLM effort: max" in out

    def test_status_places_updated_at_in_process_title(self, runner_v2, capsys):
        runner_v2.init_state()

        runner_v2.status(updated_at="14:35:09")

        out = capsys.readouterr().out
        assert "Process:" in out
        assert "·  Atualizado às 14:35:09" in out

    def test_status_shows_active_worktree_path(self, runner_v2, capsys):
        runner_v2.init_state()

        runner_v2.status()

        out = capsys.readouterr().out
        expected = Path(runner_v2.project_root).resolve()
        assert f"Worktree: {expected}" in out

    def test_status_ends_with_sanitized_log_tail(self, runner_v2, capsys):
        runner_v2.init_state()
        log_path = runner_v2.state_mgr.path.parent / "llm_logs" / "current.jsonl"
        log_path.parent.mkdir(parents=True)
        log_path.write_text(
            json.dumps(
                {
                    "type": "item.started",
                    "item": {
                        "id": "cmd-tail",
                        "type": "command_execution",
                        "command": "pytest tests/engine/test_runner.py",
                        "status": "in_progress",
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        state = runner_v2.state_mgr.load()
        state.active_llm_log = str(log_path)
        runner_v2.state_mgr.save()

        runner_v2.status()

        last_line = capsys.readouterr().out.rstrip().splitlines()[-1]
        assert "Log (tail): [" in last_line
        assert "testes focais" in last_line

    def test_status_always_has_log_tail_footer_without_log(self, runner_v2, capsys):
        runner_v2.init_state()

        runner_v2.status()

        last_line = capsys.readouterr().out.rstrip().splitlines()[-1]
        assert last_line.endswith("Log (tail): — nenhum log LLM disponível")

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

    def test_status_full_colors_pass_pending_skipped_gate_and_error(
        self,
        runner_v2,
        capsys,
        monkeypatch,
    ):
        runner_v2.init_state()
        state = runner_v2.state_mgr.load()
        state.completed_nodes = ["step.01.hipotese", "step.02.prd"]
        state.gate_log = {
            "step.01.hipotese": "PASS",
            "step.02.prd": "SKIPPED",
        }
        state.current_node = "gate.01.discovery"
        state.node_status = "awaiting_approval"
        state.pending_approval = "gate.01.discovery"
        runner_v2.state_mgr.save()

        palette = {
            "_COLOR": True,
            "BLUE": "[blue]",
            "WHITE": "[white]",
            "DIM": "[gray]",
            "RED": "[red]",
            "YELLOW": "[gate]",
            "BOLD_YELLOW": "[active]",
            "RESET": "[/]",
        }
        for name, value in palette.items():
            monkeypatch.setattr(ui, name, value)

        runner_v2.status(full=True)
        out = capsys.readouterr().out
        assert "[blue]    ✓ step.01.hipotese:" in out
        assert "[gray]    ✓ step.02.prd:" in out
        assert "[SKIPPED] ◀" not in out
        assert "[gate]    → gate.01.discovery:" in out
        assert "[white]    ○ step.03.implementacao:" in out

        state = runner_v2.state_mgr.load()
        state.node_status = "blocked"
        state.blocked_reason = "falha focal"
        state.pending_approval = None
        state.gate_log["gate.01.discovery"] = "FAIL"
        runner_v2.state_mgr.save()

        runner_v2.status(full=True)
        failed_out = capsys.readouterr().out
        assert "[red]    → gate.01.discovery:" in failed_out
        assert "[FAIL] ◀" in failed_out

    def test_status_shows_blocked_reason(self, runner_v2, capsys):
        runner_v2.init_state()
        runner_v2.state_mgr.block("test block reason")
        runner_v2.status()
        out = capsys.readouterr().out
        assert "test block reason" in out

    def test_status_does_not_call_recent_completed_delegation_active_when_blocked(
        self,
        runner_v2,
        capsys,
    ):
        runner_v2.init_state()
        log_path = runner_v2.state_mgr.path.parent / "llm_logs" / "finished.jsonl"
        log_path.parent.mkdir(parents=True)
        log_path.write_text(
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "id": "message-1",
                        "type": "agent_message",
                        "text": "revisão encerrada",
                    },
                }
            ),
            encoding="utf-8",
        )
        state = runner_v2.state_mgr.load()
        state.last_llm_log = str(log_path)
        state.active_llm_log = None
        runner_v2.state_mgr.save()
        runner_v2.state_mgr.block("receipt inconsistente")

        runner_v2.status()

        out = capsys.readouterr().out
        assert "BLOCKED: receipt inconsistente" in out
        assert "EM CONDUÇÃO" not in out
        assert "TRABALHO EM ANDAMENTO" not in out

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

    def test_status_clamps_current_position_and_reports_reexecutions(
        self,
        runner_v2,
        capsys,
    ):
        runner_v2.init_state()
        state = runner_v2.state_mgr.load()
        state.completed_nodes = [
            node_id
            for node_id, node in runner_v2.graph.nodes.items()
            if node.type != "end"
        ]
        state.metrics["steps_completed"] = len(state.completed_nodes)
        state.current_node = "step.03.implementacao"
        state.node_status = "ready"
        runner_v2.state_mgr.save()
        for status in ("rejected", "ok"):
            span = runner_v2.trace.begin_span(
                category="node",
                name="Implementação",
                node_id="step.03.implementacao",
                ordinal=runner_v2.trace.next_ordinal(
                    "node",
                    "step.03.implementacao",
                ),
            )
            span.finish(status=status)

        runner_v2.status()
        out = capsys.readouterr().out

        total = state.metrics["steps_total"]
        assert f"Progresso: {total}/{total} (passo atual)" in out
        assert f"{total + 1}/{total}" not in out
        assert "2 execuções (1 reexecução)" in out

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
    def test_gate_passes_when_files_exist(self, tmp_path, monkeypatch):
        """Gate PASS when required files exist."""
        # project_root="." → isolar CWD no tmp_path para não escrever no repo
        monkeypatch.chdir(tmp_path)
        (tmp_path / "project" / "docs").mkdir(parents=True)
        (Path(".") / "project/docs/hipotese.md").write_text("x" * 100)
        (Path(".") / "project/docs/PRD.md").write_text("x" * 100)

        process_path = tmp_path / "process.yml"
        process_path.write_text(_TEST_PROCESS_V2_YAML)
        runner = StepRunner(
            process_path=process_path,
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

    def test_gate_can_recover_from_blocked_state(self, tmp_path, monkeypatch):
        """Gate reexecutado com sucesso deve limpar o bloqueio e avançar."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "project" / "docs").mkdir(parents=True)
        (Path(".") / "project/docs/hipotese.md").write_text("x" * 100)
        (Path(".") / "project/docs/PRD.md").write_text("x" * 100)

        process_path = tmp_path / "process.yml"
        process_path.write_text(_TEST_PROCESS_V2_YAML)
        runner = StepRunner(
            process_path=process_path,
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

    def test_gate_autofix_defaults_to_project_scope(self, tmp_path):
        project_root = tmp_path / "project-root"
        (project_root / "project").mkdir(parents=True)
        process_path = tmp_path / "gate-process.yml"
        process_path.write_text(
            """
id: gate_scope
version: "1.0.0"
title: Gate scope
nodes:
  - id: verify
    type: gate
    title: Verify
    executor: python
    validators:
      - file_exists: project/fixed.txt
    next: end
  - id: end
    type: end
    title: End
""",
            encoding="utf-8",
        )
        runner = StepRunner(
            process_path=process_path,
            state_path=tmp_path / "gate-state.yml",
            project_root=project_root,
            llm_engine="codex",
        )
        runner.init_state()
        runner._auto_approve = True
        runner._max_gate_retries = 1

        def apply_fix(**kwargs):
            assert "project/" in kwargs["allowed_paths"]
            (project_root / "project" / "fixed.txt").write_text(
                "fixed\n",
                encoding="utf-8",
            )
            return DelegateResult(
                success=True,
                output="DONE",
                files_created=["project/fixed.txt"],
                files_modified=[],
            )

        with patch.object(
            runner,
            "_delegate_with_stream_retry",
            side_effect=apply_fix,
        ):
            runner._run_gate(runner.graph.get_node("verify"))

        state = runner.state_mgr.load()
        assert state.current_node == "end"
        assert state.gate_log["verify"] == "PASS"


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

    def test_fail_fast_stops_before_expensive_validator(self, tmp_path, monkeypatch):
        from ft.engine.graph import Node
        from ft.engine.runner import VALIDATOR_REGISTRY

        calls: list[str] = []

        def fail(*, project_root):
            calls.append("static")
            return False, f"static failed in {project_root}"

        def expensive(*, project_root):
            calls.append("expensive")
            return True, f"expensive passed in {project_root}"

        monkeypatch.setitem(VALIDATOR_REGISTRY, "static_check", fail)
        monkeypatch.setitem(VALIDATOR_REGISTRY, "expensive_check", expensive)
        node = Node(
            id="x",
            type="gate",
            title="X",
            validation_mode="fail_fast",
            validators=[{"static_check": True}, {"expensive_check": True}],
        )

        result = run_validators(node, str(tmp_path))

        assert not result.passed
        assert calls == ["static"]
        assert [item.name for item in result.items] == ["static_check"]
        assert result.items[0].duration_ms is not None

    def test_validator_stop_on_failure_is_local_opt_in(self, tmp_path, monkeypatch):
        from ft.engine.graph import Node
        from ft.engine.runner import VALIDATOR_REGISTRY

        calls: list[str] = []

        def fail(*, project_root):
            calls.append("first")
            return False, "first failed"

        def later(*, project_root):
            calls.append("later")
            return True, "later passed"

        monkeypatch.setitem(VALIDATOR_REGISTRY, "first_check", fail)
        monkeypatch.setitem(VALIDATOR_REGISTRY, "later_check", later)
        node = Node(
            id="x",
            type="gate",
            title="X",
            validators=[
                {"first_check": {"stop_on_failure": True}},
                {"later_check": True},
            ],
        )

        result = run_validators(node, str(tmp_path))

        assert not result.passed
        assert calls == ["first"]

    def test_validator_trace_has_parent_child_and_persistent_timing(
        self, tmp_path, monkeypatch
    ):
        from ft.engine.graph import Node
        from ft.engine.runner import VALIDATOR_REGISTRY
        from ft.engine.trace import TraceRecorder, read_trace_events

        monkeypatch.setitem(
            VALIDATOR_REGISTRY,
            "quick_check",
            lambda *, project_root: (True, f"ok: {project_root}"),
        )
        trace = TraceRecorder(tmp_path / "events.jsonl", "cycle-test")
        node = Node(
            id="x",
            type="gate",
            title="X",
            validators=[{"quick_check": True}],
        )

        result = run_validators(
            node,
            str(tmp_path),
            trace=trace,
            parent_span_id="node-parent",
            attempt_id="x:1",
        )

        assert result.passed
        starts = [
            event
            for event in read_trace_events(trace.path)
            if event["event"] == "span_start"
        ]
        validation = next(event for event in starts if event["category"] == "validation")
        validator = next(event for event in starts if event["category"] == "validator")
        assert validation["parent_span_id"] == "node-parent"
        assert validator["parent_span_id"] == validation["span_id"]
        assert validator["attempt_id"] == "x:1"

    def test_command_succeeds_accepts_per_validator_timeout(self, tmp_path):
        from ft.engine.graph import Node

        node = Node(
            id="x",
            type="gate",
            title="X",
            validators=[
                {
                    "command_succeeds": {
                        "command": "python -c 'import time; time.sleep(0.1)'",
                        "timeout": 0.01,
                    }
                }
            ],
        )

        result = run_validators(node, str(tmp_path))

        assert not result.passed
        assert "0.01s" in result.feedback

    def test_command_succeeds_uses_resume_command_only_on_explicit_resume(
        self, tmp_path
    ):
        from ft.engine.graph import Node

        node = Node(
            id="x",
            type="gate",
            title="X",
            validators=[
                {
                    "command_succeeds": {
                        "command": "python -c 'raise SystemExit(9)'",
                        "resume_command": "python -c 'print(\"receipt verified\")'",
                        "timeout": 1,
                    }
                }
            ],
        )

        normal = run_validators(node, str(tmp_path))
        resumed = run_validators(node, str(tmp_path), resume=True)

        assert not normal.passed
        assert resumed.passed
        assert "receipt verified" in resumed.items[0].detail

    def test_command_succeeds_resume_falls_back_to_normal_command(self, tmp_path):
        from ft.engine.graph import Node

        node = Node(
            id="x",
            type="gate",
            title="X",
            validators=[
                {
                    "command_succeeds": {
                        "command": "python -c 'print(\"normal validator\")'",
                        "timeout": 1,
                    }
                }
            ],
        )

        resumed = run_validators(node, str(tmp_path), resume=True)

        assert resumed.passed
        assert "normal validator" in resumed.items[0].detail

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
    def test_frontend_scaffold_prompt_includes_safe_bash_contract(self):
        from ft.engine.graph import Node

        node = Node(
            id="ft.frontend.01.scaffold",
            type="build",
            title="Scaffold Frontend",
            outputs=["project/frontend/", ".build_ok"],
            validators=[
                {"file_exists": "project/frontend/package.json"},
                {"command_succeeds": "cd project/frontend && npm install --silent && npm run build --silent"},
            ],
        )

        prompt = build_task_prompt(node, {})

        assert "mkdir -p project/frontend/scripts" in prompt
        assert "project/frontend/package.json" in prompt
        assert "retorne blocos para TODOS estes paths" in prompt
        assert "scripts.build" in prompt
        assert "node scripts/build.mjs" in prompt
        assert "npm run build --silent" in prompt
        assert "Nao escreva temporarios na raiz" in prompt

    def test_review_prompt_includes_description_outputs_and_validators(self):
        from ft.engine.graph import Node

        node = Node(
            id="ft.review.screenshot",
            type="review",
            title="Screenshot Review",
            description="Comparar telas com os critérios visuais.",
            outputs=["docs/screenshots/", "docs/screenshot-review.md"],
            validators=[{"file_exists": "docs/screenshot-review.md"}],
        )

        prompt = build_task_prompt(node, {})

        assert "Comparar telas com os critérios visuais" in prompt
        assert "Diretorio: docs/screenshots/" in prompt
        assert "Arquivo: docs/screenshot-review.md" in prompt
        assert "file_exists: docs/screenshot-review.md" in prompt
        assert "nao crie variacoes de nome" in prompt

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
