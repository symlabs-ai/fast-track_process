"""Persistent LLM session policy and runtime behavior."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ft.cli.fix_command import execute_fix
from ft.engine.delegate import DelegateResult
from ft.engine.graph import load_graph
from ft.engine.llm_defaults import LLMSelection
from ft.engine.process_validator import validate_process
from ft.engine.runner import StepRunner, ValidationResult
from ft.engine.state import EngineState, StateManager


PROCESS = """
id: session_test
version: "1"
title: Session Test
session_policy:
  mode: sprint
  providers: [claude, codex]
  initial_plan: internal
  parallel_strategy: fork
  recovery: rehydrate
nodes:
  - id: build
    type: build
    title: Build
    sprint: sprint-01
    executor: claude
    outputs: [project/]
    next: end
  - id: end
    type: end
    title: End
"""


def _runner(tmp_path: Path) -> StepRunner:
    process = tmp_path / "process.yml"
    process.write_text(PROCESS, encoding="utf-8")
    runner = StepRunner(
        process_path=process,
        state_path=tmp_path / "state" / "engine_state.yml",
        project_root=tmp_path,
    )
    runner.state_mgr._state = EngineState(
        process_id="session_test",
        current_node="build",
        current_sprint="sprint-01",
        metrics={"llm_calls": 0},
    )
    runner.state_mgr.save()
    return runner


def _selection(model: str = "sonnet") -> LLMSelection:
    return LLMSelection(
        engine="claude",
        model=model,
        effort="high",
    )


def _codex_selection() -> LLMSelection:
    return LLMSelection(engine="codex", model="gpt-5.6-sol", effort="max")


def test_session_policy_validates(tmp_path: Path) -> None:
    process = tmp_path / "process.yml"
    process.write_text(PROCESS, encoding="utf-8")

    assert validate_process(load_graph(process)).passed


def test_state_roundtrip_preserves_sessions_and_plan(tmp_path: Path) -> None:
    manager = StateManager(tmp_path / "engine_state.yml")
    manager._state = EngineState(
        llm_sessions={"sprint:s1": {"session_id": "thread-1", "turns": 2}},
        llm_execution_plan={"path": "llm_execution_plan.yml", "source": "llm"},
    )
    manager.save()

    reloaded = StateManager(manager.path).load()

    assert reloaded.llm_sessions["sprint:s1"]["session_id"] == "thread-1"
    assert reloaded.llm_execution_plan == {
        "path": "llm_execution_plan.yml",
        "source": "llm",
    }


def test_second_turn_resumes_same_sprint_session(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    node = runner.graph.get_node("build")
    first: dict = {}

    runner._attach_llm_session(first, node=node, selection=_selection())
    session_id = first["llm_session_id"]
    assert first["llm_session_resume"] is False
    context = first.pop("_ft_session_context")
    runner._record_llm_session_result(
        context,
        DelegateResult(True, "DONE", [], [], session_id=session_id),
    )

    second: dict = {}
    runner._attach_llm_session(second, node=node, selection=_selection())

    assert second["llm_session_id"] == session_id
    assert second["llm_session_resume"] is True
    assert runner.state_mgr.state.metrics["llm_sessions_created"] == 1


def test_review_and_parallel_lanes_are_isolated(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    node = runner.graph.get_node("build")

    base, review, parallel = {}, {}, {}
    runner._attach_llm_session(base, node=node, selection=_selection())
    runner._attach_llm_session(
        review,
        node=node,
        selection=_selection(),
        lane="review",
    )
    runner._attach_llm_session(
        parallel,
        node=node,
        selection=_selection(),
        lane="docs:api",
    )

    assert base["_ft_session_context"]["key"] == "sprint:sprint-01"
    assert review["_ft_session_context"]["key"] == "sprint:sprint-01:lane:review"
    assert parallel["_ft_session_context"]["key"] == (
        "sprint:sprint-01:lane:docs:api"
    )
    assert len({
        base["llm_session_id"],
        review["llm_session_id"],
        parallel["llm_session_id"],
    }) == 3


def test_model_change_supersedes_session(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    node = runner.graph.get_node("build")
    first: dict = {}
    runner._attach_llm_session(first, node=node, selection=_selection("sonnet"))
    old_id = first["llm_session_id"]

    changed: dict = {}
    runner._attach_llm_session(changed, node=node, selection=_selection("opus"))

    record = runner.state_mgr.state.llm_sessions["sprint:sprint-01"]
    assert changed["llm_session_id"] != old_id
    assert record["model"] == "opus"
    assert record["history"][-1]["session_id"] == old_id


def test_effort_change_resumes_same_session(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    node = runner.graph.get_node("build")
    first: dict = {}
    runner._attach_llm_session(first, node=node, selection=_selection())
    session_id = first["llm_session_id"]
    context = first.pop("_ft_session_context")
    runner._record_llm_session_result(
        context,
        DelegateResult(True, "DONE", [], [], session_id=session_id),
    )

    changed: dict = {}
    runner._attach_llm_session(
        changed,
        node=node,
        selection=LLMSelection(
            engine="claude",
            model="sonnet",
            effort="medium",
        ),
    )

    record = runner.state_mgr.state.llm_sessions["sprint:sprint-01"]
    assert changed["llm_session_id"] == session_id
    assert changed["llm_session_resume"] is True
    assert record["effort"] == "medium"
    assert record["history"] == []


def test_codex_auth_route_supersedes_gateway_session(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    node = runner.graph.get_node("build")
    node.executor = "llm_codex"
    selection = _codex_selection()

    gateway: dict = {}
    runner._attach_llm_session(gateway, node=node, selection=selection)
    gateway_context = gateway.pop("_ft_session_context")
    runner._record_llm_session_result(
        gateway_context,
        DelegateResult(True, "DONE", [], [], session_id="gateway-thread"),
    )

    node.codex_auth = "chatgpt"
    direct: dict = {}
    runner._attach_llm_session(direct, node=node, selection=selection)

    record = runner.state_mgr.state.llm_sessions["sprint:sprint-01"]
    assert direct["codex_auth"] == "chatgpt"
    assert "llm_session_id" not in direct
    assert record["codex_auth"] == "chatgpt"
    assert record["history"][-1]["session_id"] == "gateway-thread"
    assert record["history"][-1]["codex_auth"] is None


def test_resume_error_rehydrates_once(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    node = runner.graph.get_node("build")
    selection = _selection()
    initial: dict = {"task": "continue", "llm_engine": "claude"}
    runner._attach_llm_session(initial, node=node, selection=selection)
    first_id = initial["llm_session_id"]
    context = initial["_ft_session_context"]
    runner._record_llm_session_result(
        context,
        DelegateResult(True, "DONE", [], [], session_id=first_id),
    )

    resumed: dict = {"task": "continue", "llm_engine": "claude"}
    runner._attach_llm_session(resumed, node=node, selection=selection)
    replacement_id = "8575e55d-e2bd-414c-b76a-7aa622bc66e2"
    responses = [
        DelegateResult(
            False,
            "session not found; unable to resume",
            [],
            [],
            session_id=first_id,
            session_resumed=True,
            session_error=True,
        ),
        DelegateResult(True, "DONE", [], [], session_id=replacement_id),
    ]

    with patch("ft.engine.runner.delegate_to_llm", side_effect=responses) as delegated:
        result = runner._delegate_with_stream_retry(**resumed)

    assert result.success
    assert delegated.call_count == 2
    assert delegated.call_args_list[1].kwargs["llm_session_resume"] is False
    assert runner.state_mgr.state.metrics["llm_session_recoveries"] == 1
    assert runner.state_mgr.state.llm_sessions["sprint:sprint-01"]["history"]


def test_internal_plan_is_persisted_outside_worktree(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "demanda.md").write_text(
        "# Demanda\nConstruir busca rápida por telefone.\n",
        encoding="utf-8",
    )
    planned = DelegateResult(
        True,
        (
            "schema_version: 1\n"
            "process: session_test\n"
            "cycle: cycle-01\n"
            "objective: construir\n"
            "authority: advisory\n"
            "sprints:\n"
            "  - id: sprint-01\n"
            "    intent: construir\n"
            "    deliverables: [project/]\n"
            "    risks: []\n"
            "risks: []\n"
            "unknowns: []\n"
        ),
        [],
        [],
        session_id="9a95ca1c-69da-452c-935a-5624087f46db",
    )

    with (
        patch.object(
            runner,
            "_capture_delegation_llm_selection",
            return_value=_selection(),
        ),
        patch.object(
            runner,
            "_delegate_with_stream_retry",
            return_value=planned,
        ) as delegated,
    ):
        runner._ensure_internal_execution_plan(runner.state_mgr.state)

    plan_path = runner.state_mgr.path.parent / "llm_execution_plan.yml"
    assert plan_path.is_file()
    assert "sprint-01" in plan_path.read_text(encoding="utf-8")
    assert runner.state_mgr.state.llm_execution_plan["source"] == "llm"
    assert delegated.call_args.kwargs["raw_output"] is True
    assert delegated.call_args.kwargs["_ft_session_context"]["key"] == "plan"
    assert "Construir busca rápida por telefone." in delegated.call_args.kwargs["task"]


def test_internal_plan_falls_back_when_provider_raises(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    with (
        patch.object(
            runner,
            "_capture_delegation_llm_selection",
            return_value=_selection(),
        ),
        patch.object(
            runner,
            "_delegate_with_stream_retry",
            side_effect=OSError("provider unavailable"),
        ),
    ):
        runner._ensure_internal_execution_plan(runner.state_mgr.state)

    assert runner.state_mgr.state.llm_execution_plan["source"] == "deterministic"
    assert (runner.state_mgr.path.parent / "llm_execution_plan.yml").is_file()


def test_direct_fix_reuses_session_policy_and_injects_plan(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    plan_path = runner.state_mgr.path.parent / "llm_execution_plan.yml"
    plan_path.write_text(
        "schema_version: 1\nobjective: corrigir rápido\nsprints: []\n",
        encoding="utf-8",
    )
    state = runner.state_mgr.state
    state.node_status = "blocked"
    state.blocked_reason = "validator falhou"
    state.llm_execution_plan = {
        "path": "llm_execution_plan.yml",
        "source": "deterministic",
    }
    runner.state_mgr.save()

    with patch(
        "ft.engine.delegate.delegate_to_llm",
        return_value=DelegateResult(False, "BLOCKED", [], []),
    ) as delegated:
        execute_fix(
            SimpleNamespace(instruction="corrija", auto=False),
            runner,
        )

    kwargs = delegated.call_args.kwargs
    assert kwargs["llm_session_id"]
    assert kwargs["llm_session_resume"] is False
    assert "PLANO INTERNO DO CICLO" in kwargs["task"]
    assert "corrigir rápido" in kwargs["task"]


def test_directed_fix_preserves_and_resumes_builder_session(tmp_path: Path) -> None:
    process = tmp_path / "process.yml"
    process.write_text(
        """
id: directed_fix_session
version: "1"
title: Directed Fix Session
session_policy:
  mode: sprint
  providers: [codex]
  initial_plan: deterministic
  parallel_strategy: fork
  recovery: rehydrate
nodes:
  - id: review
    type: review
    title: Review
    sprint: sprint-01
    executor: codex
    outputs: [docs/review.yml]
    write_scope: [docs/review.yml]
    on_fail:
      goto: fix
    next: end
  - id: fix
    type: build
    title: Fix
    sprint: sprint-01
    executor: codex
    outputs: [project/]
    write_scope: [project]
    fix_review: review
    next: review
  - id: end
    type: end
    title: End
""",
        encoding="utf-8",
    )
    runner = StepRunner(
        process_path=process,
        state_path=tmp_path / "state" / "engine_state.yml",
        project_root=tmp_path,
    )
    runner.state_mgr._state = EngineState(
        process_id="directed_fix_session",
        current_node="review",
        current_sprint="sprint-01",
        node_status="pending_fix",
        pending_fix={
            "goto": "fix",
            "origin": "review",
            "feedback": "F-001 continua vermelho",
        },
        metrics={"llm_calls": 0},
    )
    runner.state_mgr.save()

    fix_node = runner.graph.get_node("fix")
    selection = _codex_selection()
    initial: dict = {}
    runner._attach_llm_session(initial, node=fix_node, selection=selection)
    context = initial.pop("_ft_session_context")
    runner._record_llm_session_result(
        context,
        DelegateResult(True, "DONE", [], [], session_id="thread-builder"),
    )

    assert runner.apply_fix("Corrigir somente F-001")

    resumed: dict = {}
    runner._attach_llm_session(resumed, node=fix_node, selection=selection)
    assert resumed["llm_session_id"] == "thread-builder"
    assert resumed["llm_session_resume"] is True
    record = runner.state_mgr.state.llm_sessions["sprint:sprint-01"]
    assert record["session_id"] == "thread-builder"
    assert record["established"] is True
    assert record.get("reset_reason") is None


def test_directed_fix_blocks_incompatible_session_change(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    node = runner.graph.get_node("build")
    initial: dict = {}
    runner._attach_llm_session(initial, node=node, selection=_selection())
    session_id = initial["llm_session_id"]
    context = initial.pop("_ft_session_context")
    runner._record_llm_session_result(
        context,
        DelegateResult(True, "DONE", [], [], session_id=session_id),
    )
    runner.state_mgr.state.active_fix_return = {
        "fix_node": "build",
        "review_node": "review",
    }
    runner.state_mgr.save()

    changed: dict = {}
    try:
        runner._attach_llm_session(
            changed,
            node=node,
            selection=_selection("opus"),
        )
    except RuntimeError as exc:
        assert "DIRECTED_FIX_SESSION_AFFINITY_CONFLICT" in str(exc)
    else:
        raise AssertionError("troca incompatível abriu um fix sem contexto")

    record = runner.state_mgr.state.llm_sessions["sprint:sprint-01"]
    assert record["session_id"] == session_id
    assert record["model"] == "sonnet"
    assert record["history"] == []
    assert runner.state_mgr.state.node_status == "blocked"
    assert "DIRECTED_FIX_SESSION_AFFINITY_CONFLICT" in str(
        runner.state_mgr.state.blocked_reason
    )


def test_direct_fix_on_ready_node_validates_and_advances_without_redelegation(
    tmp_path: Path,
) -> None:
    runner = _runner(tmp_path)
    state = runner.state_mgr.state
    state.node_status = "ready"
    runner.state_mgr.save()

    with (
        patch(
            "ft.engine.delegate.delegate_to_llm",
            return_value=DelegateResult(True, "DONE", [], []),
        ) as delegated,
        patch(
            "ft.engine.runner.run_validators",
            return_value=ValidationResult(True, False, None),
        ) as validated,
        patch.object(runner, "_maybe_auto_commit") as committed,
        patch.object(runner, "_record_node_summary"),
        patch.object(runner, "run") as rerun,
    ):
        execute_fix(
            SimpleNamespace(instruction="corrija", auto=False),
            runner,
        )

    assert delegated.call_count == 1
    assert validated.call_count == 1
    committed.assert_called_once()
    rerun.assert_not_called()
    final_state = runner.state_mgr.load()
    assert final_state.current_node == "end"
    assert "build" in final_state.completed_nodes


def test_direct_fix_validation_failure_stays_blocked_without_redelegation(
    tmp_path: Path,
) -> None:
    runner = _runner(tmp_path)
    state = runner.state_mgr.state
    state.node_status = "blocked"
    state.blocked_reason = "falha anterior"
    runner.state_mgr.save()

    with (
        patch(
            "ft.engine.delegate.delegate_to_llm",
            return_value=DelegateResult(True, "DONE", [], []),
        ),
        patch(
            "ft.engine.runner.run_validators",
            return_value=ValidationResult(False, False, "ainda vermelho"),
        ),
        patch.object(runner, "run") as rerun,
    ):
        execute_fix(
            SimpleNamespace(instruction="corrija", auto=False),
            runner,
        )

    rerun.assert_not_called()
    final_state = runner.state_mgr.load()
    assert final_state.node_status == "blocked"
    assert "ainda vermelho" in str(final_state.blocked_reason)


def test_session_policy_schema_rejects_unsupported_provider(tmp_path: Path) -> None:
    process = tmp_path / "process.yml"
    process.write_text(PROCESS.replace("[claude, codex]", "[gemini]"), encoding="utf-8")

    report = validate_process(load_graph(process))

    assert not report.passed
    assert any("session_policy.providers" in issue.message for issue in report.errors)
