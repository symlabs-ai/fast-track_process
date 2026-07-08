"""Unit tests for ft.engine.runner (LLM mocked)."""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from ft.engine.graph import load_graph
from ft.engine.runner import StepRunner, run_validators, ValidationResult, build_task_prompt
from ft.engine.delegate import DelegateResult


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


class TestDelegationDisplay:
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

    def test_opencode_code_nodes_allow_edit_tools(self, tmp_path):
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
        assert build_options.deny_edit_tools is False
        assert build_options.early_success_paths == []
        assert doc_options.deny_edit_tools is False
        assert doc_options.restrict_tools is False
        assert doc_options.early_success_paths == ["docs/out.md"]
        assert doc_options.capture_output_path == "docs/out.md"
        assert runner._resolve_allowed_paths(build_node) == ["project", ".build_ok"]
        assert runner._resolve_allowed_paths(doc_node) == ["docs/out.md"]

    def test_opencode_scaffold_uses_deterministic_fallback(self, tmp_path):
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

        with patch("ft.engine.runner.delegate_to_llm", side_effect=AssertionError("should not delegate")):
            runner._run_llm_step(node)

        assert (project_root / "project/frontend/package.json").exists()
        assert (project_root / ".build_ok").exists()
        assert runner.state_mgr.load().current_node == "ft.end"

    def test_opencode_frontend_implement_uses_deterministic_fallback(self, tmp_path):
        project_root = tmp_path / "project"
        state_dir = project_root / "state"
        frontend = project_root / "project" / "frontend"
        state_dir.mkdir(parents=True)
        frontend.mkdir(parents=True)
        (frontend / "package.json").write_text("{ broken json\n", encoding="utf-8")
        (frontend / "package.json.newbuildmjsjunk").write_text("junk\n", encoding="utf-8")

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
      - command_succeeds: "cd project/frontend && npm run build --silent"
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
        node = runner.graph.get_node("ft.frontend.02.implement")

        with patch("ft.engine.runner.delegate_to_llm", side_effect=AssertionError("should not delegate")):
            runner._run_llm_step(node)

        assert (frontend / "src/main.js").exists()
        assert not (frontend / "package.json.newbuildmjsjunk").exists()
        assert runner.state_mgr.load().current_node == "ft.end"

    def test_opencode_tdd_red_and_green_use_deterministic_fallbacks(self, tmp_path):
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
      - command_succeeds: "cd project && python -c \\"from pathlib import Path; import py_compile; files=list(Path('tests').rglob('test_*.py')); assert files; [py_compile.compile(str(p), doraise=True) for p in files]\\""
    next: ft.tdd.02.green
  - id: ft.tdd.02.green
    type: test_green
    title: Green
    executor: claude
    outputs:
      - project/backend/
    validators:
      - command_succeeds: "cd project && python -m pytest tests/ -q"
    next: ft.tdd.03.refactor
  - id: ft.tdd.03.refactor
    type: refactor
    title: Refactor
    executor: claude
    validators:
      - command_succeeds: "cd project && python -m pytest tests/ -q"
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

        with patch("ft.engine.runner.delegate_to_llm", side_effect=AssertionError("should not delegate")):
            runner._run_llm_step(runner.graph.get_node("ft.tdd.01.red"))
            assert (project_root / "project/tests/test_backend_contract.py").exists()
            assert runner.state_mgr.load().current_node == "ft.tdd.02.green"

            runner._run_llm_step(runner.graph.get_node("ft.tdd.02.green"))
            assert (project_root / "project/backend/main.py").exists()
            assert runner.state_mgr.load().current_node == "ft.tdd.03.refactor"

            (project_root / "project/backend/main.py").write_text("broken\n", encoding="utf-8")
            runner._run_llm_step(runner.graph.get_node("ft.tdd.03.refactor"))
            assert runner.state_mgr.load().current_node == "ft.end"

    def test_opencode_delivery_fallbacks_create_entrypoint_and_makefile(self, tmp_path):
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
  - id: ft.delivery.01.entrypoint
    type: build
    title: Entry
    executor: claude
    validators:
      - file_exists: project/backend/main.py
    next: ft.delivery.03.makefile
  - id: ft.delivery.03.makefile
    type: build
    title: Makefile
    executor: claude
    outputs:
      - project/Makefile
    validators:
      - file_exists: project/Makefile
      - command_succeeds: "make --dry-run dev 2>&1 | head -3"
      - command_succeeds: "cd project && make --dry-run run >/dev/null && test -n \\"$(make -s url)\\""
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

        with patch("ft.engine.runner.delegate_to_llm", side_effect=AssertionError("should not delegate")):
            runner._run_llm_step(runner.graph.get_node("ft.delivery.01.entrypoint"))
            assert (project_root / "project/backend/main.py").exists()
            assert runner.state_mgr.load().current_node == "ft.delivery.03.makefile"

            runner._run_llm_step(runner.graph.get_node("ft.delivery.03.makefile"))
            assert (project_root / "project/Makefile").exists()
            assert runner.state_mgr.load().current_node == "ft.end"

    def test_opencode_process_evolve_restores_process_yml_in_worktree(self, tmp_path):
        project_root = tmp_path / "project"
        work_dir = tmp_path / "worktrees" / "sample" / "cycle-01-opencode"
        state_dir = project_root / "state"
        (project_root / "process").mkdir(parents=True)
        (work_dir / "docs").mkdir(parents=True)
        (work_dir / "process").mkdir(parents=True)
        state_dir.mkdir(parents=True)

        process_path = project_root / "process" / "process.yml"
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
      - process/process.yml
    validators:
      - file_exists: docs/process-improvements.md
      - command_succeeds: "python3 -c \\"import yaml; yaml.safe_load(open('process/process.yml'))\\""
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
        runner._work_dir = str(work_dir)
        runner.init_state()

        with patch("ft.engine.runner.delegate_to_llm", side_effect=AssertionError("should not delegate")):
            runner._run_llm_step(runner.graph.get_node("ft.handoff.05.process_evolve"))

        restored = work_dir / "process" / "process.yml"
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
        assert state.metrics["steps_completed"] == 4
        assert state.metrics["steps_total"] == 4
        assert state.gate_log["skipped.one"] == "SKIPPED"
        assert state.gate_log["skipped.two"] == "SKIPPED"

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
        assert state.metrics["steps_completed"] == 3
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

    def test_opencode_document_auto_fix_uses_capture_prompt(self, tmp_path):
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

    def test_opencode_screenshot_review_uses_deterministic_fallback(self, tmp_path):
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
  - id: ft.frontend.04.screenshot_review
    type: review
    title: Screenshot Review
    description: Tirar screenshots e comparar com docs/ui_criteria.md.
    executor: claude
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
        node = runner.graph.get_node("ft.frontend.04.screenshot_review")

        with patch("ft.engine.runner.delegate_to_llm", side_effect=AssertionError("should not delegate")):
            runner._run_review(node)

        report = docs / "screenshot-review.md"
        assert report.exists()
        assert "APPROVED WITH NOTES" in report.read_text()
        assert (docs / "screenshots" / "README.md").exists()
        assert runner.state_mgr.load().current_node == "ft.end"

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


class TestRewriteGuard:
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
