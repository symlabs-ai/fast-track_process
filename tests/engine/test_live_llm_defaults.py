from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import yaml

from ft.cli import main as cli_main
from ft.engine.delegate import DelegateResult
from ft.engine.graph import Node
from ft.engine.layout import (
    canonical_project_root,
    ensure_project_layout,
    register_project_process,
    update_manifest_llm_defaults,
)
from ft.engine.parallel import WorktreeResult
from ft.engine.runner import LLMSelection, StepRunner
from ft.engine.state import EngineState


_TWO_CALL_PROCESS = """\
id: live_defaults
version: "1.0.0"
title: Live defaults
nodes:
  - id: first
    type: document
    title: First call
    executor: llm_coder
    next: second
  - id: second
    type: document
    title: Second call
    executor: llm_coder
    next: end
  - id: end
    type: end
    title: End
"""


def _write_registered_process(root: Path, name: str = "test") -> Path:
    process = root / ".ft" / "process" / name / "process.yml"
    process.parent.mkdir(parents=True, exist_ok=True)
    process.write_text(_TWO_CALL_PROCESS, encoding="utf-8")
    register_project_process(
        root,
        process_name=name,
        process_path=process,
        template_id=name,
        entrypoint="test",
        set_default=True,
    )
    return process


def _runner(
    tmp_path: Path,
    *,
    llm_engine: str | None = None,
    llm_model: str | None = None,
    llm_effort: str | None = None,
) -> tuple[StepRunner, Path, Path]:
    owner = tmp_path / "owner"
    cycle = tmp_path / "cycle"
    owner.mkdir()
    cycle.mkdir()
    ensure_project_layout(
        owner,
        defaults={
            "llm_engine": "codex",
            "llm_model": "gpt-old",
            "llm_effort": "high",
        },
    )
    ensure_project_layout(
        cycle,
        defaults={
            "llm_engine": "codex",
            "llm_model": "gpt-old",
            "llm_effort": "high",
        },
    )
    process = _write_registered_process(cycle)
    runner = StepRunner(
        process_path=process,
        state_path=cycle / "state" / "engine_state.yml",
        project_root=cycle,
        llm_engine=llm_engine,
        llm_model=llm_model,
        llm_effort=llm_effort,
        llm_defaults_root=owner,
    )
    runner.init_state()
    return runner, owner, cycle


def test_live_defaults_change_does_not_mutate_in_flight_call_and_reaches_next_call(
    tmp_path: Path,
) -> None:
    runner, owner, _cycle = _runner(tmp_path)
    first_started = threading.Event()
    release_first = threading.Event()
    calls: list[dict[str, object]] = []

    def fake_delegate(**kwargs):
        calls.append(dict(kwargs))
        if len(calls) == 1:
            first_started.set()
            assert release_first.wait(timeout=5)
        return DelegateResult(True, "DONE", [], [])

    with patch("ft.engine.runner.delegate_to_llm", side_effect=fake_delegate):
        thread = threading.Thread(target=runner.run, kwargs={"mode": "mvp"})
        thread.start()
        assert first_started.wait(timeout=5)

        update_manifest_llm_defaults(
            owner,
            llm_engine="opencode",
            llm_model="provider/new-model",
            llm_effort=None,
        )
        release_first.set()
        thread.join(timeout=10)

    assert not thread.is_alive()
    assert [
        (call["llm_engine"], call["llm_model"], call["llm_effort"])
        for call in calls
    ] == [
        ("codex", "gpt-old", "high"),
        ("opencode", "provider/new-model", None),
    ]
    state = runner.state_mgr.load()
    assert (state.llm_engine, state.llm_model, state.llm_effort) == (
        "opencode",
        "provider/new-model",
        None,
    )


def test_command_override_wins_without_consuming_a_new_manifest_revision(
    tmp_path: Path,
) -> None:
    runner, owner, cycle = _runner(
        tmp_path,
        llm_engine="codex",
        llm_model="command-model",
        llm_effort="max",
    )
    state = runner.state_mgr.load()
    original_digest = state.llm_defaults_digest
    update_manifest_llm_defaults(
        owner,
        llm_engine="claude",
        llm_model="claude-new",
        llm_effort="low",
    )

    command_selection = runner._capture_delegation_llm_selection(state)

    assert command_selection.engine == "codex"
    assert command_selection.model == "command-model"
    assert command_selection.effort == "max"
    assert state.llm_defaults_digest == original_digest

    resumed = StepRunner(
        process_path=cycle / ".ft/process/test/process.yml",
        state_path=cycle / "state" / "engine_state.yml",
        project_root=cycle,
        llm_defaults_root=owner,
    )
    resumed_state = resumed.state_mgr.load()
    next_selection = resumed._capture_delegation_llm_selection(resumed_state)

    assert next_selection.engine == "claude"
    assert next_selection.model == "claude-new"
    assert next_selection.effort == "low"
    assert resumed_state.llm_defaults_digest != original_digest


def test_reselecting_same_manifest_tuple_still_overrides_divergent_cycle_state(
    tmp_path: Path,
) -> None:
    runner, owner, _cycle = _runner(tmp_path)
    state = runner.state_mgr.load()
    original_digest = state.llm_defaults_digest
    state.llm_engine = "claude"
    state.llm_model = "cycle-override"
    state.llm_effort = "low"
    runner.state_mgr.save()

    update_manifest_llm_defaults(
        owner,
        llm_engine="codex",
        llm_model="gpt-old",
        llm_effort="high",
    )
    selection = runner._capture_delegation_llm_selection(state)

    assert (selection.engine, selection.model, selection.effort) == (
        "codex",
        "gpt-old",
        "high",
    )
    assert state.llm_defaults_digest != original_digest


def test_node_provider_change_does_not_inherit_incompatible_command_model(
    tmp_path: Path,
) -> None:
    runner, _owner, _cycle = _runner(
        tmp_path,
        llm_engine="codex",
        llm_model="gpt-command",
        llm_effort="max",
    )
    state = runner.state_mgr.load()
    node = runner.graph.get_node("first")
    node.llm_engine = "opencode"

    selection = runner._capture_delegation_llm_selection(state, node=node)

    assert selection.engine == "opencode"
    assert selection.model is None
    assert selection.effort is None

    node.llm_model = "provider/node-model"
    node.llm_effort = "default"
    selection = runner._capture_delegation_llm_selection(state, node=node)
    assert selection.model == "provider/node-model"
    assert selection.effort is None


def test_selection_reports_field_level_provenance_and_provider_resets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FT_LLM_ENGINE", "claude")
    monkeypatch.setenv("FT_LLM_MODEL", "env-model")
    monkeypatch.setenv("FT_LLM_EFFORT", "low")
    runner, _owner, _cycle = _runner(
        tmp_path,
        llm_engine="codex",
        llm_model="command-model",
        llm_effort="max",
    )
    state = runner.state_mgr.load()
    node = runner.graph.get_node("first")
    node.llm_engine = "opencode"
    node.llm_model = "provider/node-model"
    node.llm_effort = "high"

    selection = runner._capture_delegation_llm_selection(state, node=node)

    assert (selection.engine, selection.model, selection.effort) == (
        "opencode",
        "provider/node-model",
        "high",
    )
    assert selection.provenance == {
        "engine": "node",
        "model": "node",
        "effort": "node",
    }
    assert {entry["source"] for entry in selection.resolution} >= {
        "environment",
        "state",
        "command",
        "node",
    }


def test_selection_reports_manifest_live_and_explicit_effort_clear(tmp_path: Path) -> None:
    runner, owner, _cycle = _runner(tmp_path)
    state = runner.state_mgr.load()
    update_manifest_llm_defaults(
        owner,
        llm_engine="opencode",
        llm_model="provider/live",
        llm_effort=None,
    )

    selection = runner._capture_delegation_llm_selection(state)

    assert (selection.engine, selection.model, selection.effort) == (
        "opencode",
        "provider/live",
        None,
    )
    assert selection.provenance == {
        "engine": "manifest_live",
        "model": "manifest_live",
        "effort": "manifest_live",
    }


def test_provider_specific_context_is_rebuilt_and_compact_xml_is_initial_only(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, _owner, _cycle = _runner(tmp_path)
    node = runner.graph.get_node("first")
    node.id = "ft.tdd.02.green"
    node.type = "test_green"
    state = runner.state_mgr.load()
    monkeypatch.setenv("FT_OPENCODE_BUNDLE_MODE", "1")
    opencode = LLMSelection("opencode", "provider/model", "high")
    codex = LLMSelection("codex", "gpt/model", "max")

    initial_prompt, compact, _deny = runner._build_llm_task_context(
        node,
        state,
        opencode,
    )
    retry_prompt, retry_compact, _deny = runner._build_llm_task_context(
        node,
        state,
        opencode,
        allow_compact=False,
    )
    codex_prompt, codex_compact, _deny = runner._build_llm_task_context(
        node,
        state,
        codex,
        allow_compact=False,
    )

    assert compact is not None and "<ft_file" in initial_prompt
    assert retry_compact is None and "<ft_file" not in retry_prompt
    assert codex_compact is None and "<ft_file" not in codex_prompt

    review_node = Node(
        id="review",
        type="review",
        title="Review",
        outputs=["docs/missing-evidence/"],
    )
    opencode_review, deny_paths = runner._build_review_task_context(
        review_node,
        opencode,
    )
    codex_review, codex_deny_paths = runner._build_review_task_context(
        review_node,
        codex,
    )
    assert "INSTRUCAO OPENCODE REVIEW" in opencode_review
    assert "docs/missing-evidence" in deny_paths
    assert "INSTRUCAO OPENCODE REVIEW" not in codex_review
    assert codex_deny_paths == []


def test_node_hyper_mode_context_config_is_applied_by_runner(tmp_path: Path) -> None:
    runner, _owner, cycle = _runner(tmp_path)
    docs = cycle / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "preview.md").write_text("preview 0\npreview 1\npreview 2\n")
    (docs / "detail.md").write_text("detail 0\ndetail 1\ndetail 2\n")
    (docs / "omit.md").write_text("must not appear\n")
    node = runner.graph.get_node("first")
    node.hyper_mode_docs = ["docs/preview.md", "docs/detail.md"]
    node.hyper_mode_full_docs = ["docs/detail.md"]
    node.hyper_mode_preview_lines = 1
    node.hyper_mode_full_max_lines = 2

    prompt, _compact, _deny = runner._build_llm_task_context(
        node,
        runner.state_mgr.load(),
        LLMSelection("codex", "gpt/model", "max"),
    )

    assert "preview 0" in prompt and "preview 1" not in prompt
    assert "detail 0" in prompt and "detail 1" in prompt
    assert "detail 2" not in prompt
    assert "must not appear" not in prompt
    assert "### detail.md (INTEGRAL)" in prompt


def test_log_suffix_uses_the_attempt_snapshot_not_a_fresh_resolution(
    tmp_path: Path,
) -> None:
    runner, _owner, _cycle = _runner(tmp_path)

    assert runner._build_llm_log_path("node", "retry", engine="codex").suffix == ".jsonl"
    assert runner._build_llm_log_path("node", "retry", engine="opencode").suffix == ".log"


def test_parallel_task_captures_defaults_only_after_reaching_worker_slot(
    tmp_path: Path,
) -> None:
    runner, owner, _cycle = _runner(tmp_path)
    nodes = [
        Node(
            id=f"parallel-{index}",
            type="document",
            title=f"Parallel {index}",
            outputs=[f"docs/parallel-{index}.md"],
        )
        for index in range(1, 4)
    ]
    runner.graph.nodes.update({node.id: node for node in nodes})
    calls: list[tuple[object, object, object]] = []

    class FakeParallelRunner:
        def __init__(self, project_root, max_slots=2):
            assert max_slots == 2

        def run_parallel(self, tasks, delegate_fn):
            results = []
            for index, task in enumerate(tasks):
                if index == 2:
                    update_manifest_llm_defaults(
                        owner,
                        llm_engine="opencode",
                        llm_model="provider/queued",
                        llm_effort=None,
                    )
                result = delegate_fn(
                    task=task["task_prompt"],
                    project_root=str(tmp_path / f"worker-{index}"),
                    allowed_paths=task["allowed_paths"],
                    **task["delegate_kwargs"],
                )
                results.append(
                    WorktreeResult(
                        node_id=task["node_id"],
                        branch="",
                        worktree_path="",
                        success=result.success,
                        output=result.output,
                    )
                )
            return results

    def fake_delegate(**kwargs):
        calls.append(
            (kwargs["llm_engine"], kwargs["llm_model"], kwargs["llm_effort"])
        )
        return DelegateResult(True, "DONE", [], [])

    with (
        patch("ft.engine.runner.ParallelRunner", FakeParallelRunner),
        patch("ft.engine.runner.delegate_to_llm", side_effect=fake_delegate),
    ):
        runner._run_parallel_group(nodes)

    assert calls == [
        ("codex", "gpt-old", "high"),
        ("codex", "gpt-old", "high"),
        ("opencode", "provider/queued", None),
    ]

def test_invalid_live_manifest_fails_closed_instead_of_reusing_stale_state(
    tmp_path: Path,
) -> None:
    runner, owner, _cycle = _runner(tmp_path)
    state = runner.state_mgr.load()
    (owner / ".ft" / "manifest.yml").write_text(
        "defaults: [invalid, shape]\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="defaults deve ser mapping"):
        runner._capture_delegation_llm_selection(state)


def test_legacy_continuous_state_uses_persisted_tuple_as_manifest_baseline(
    tmp_path: Path,
) -> None:
    root = tmp_path / "continuous"
    root.mkdir()
    ensure_project_layout(
        root,
        defaults={
            "llm_engine": "opencode",
            "llm_model": "provider/new",
            "llm_effort": "high",
        },
    )
    process = _write_registered_process(root)
    runner = StepRunner(
        process_path=process,
        state_path=root / "state" / "engine_state.yml",
        project_root=root,
        llm_defaults_root=root,
    )
    runner.state_mgr._state = EngineState(
        llm_engine="claude",
        llm_model="legacy-model",
        llm_effort="low",
    )
    runner.state_mgr.save()
    state = runner.state_mgr.load()

    selection = runner._capture_delegation_llm_selection(state)

    assert selection == runner._resolve_llm_selection(state)
    assert (selection.engine, selection.model, selection.effort) == (
        "opencode",
        "provider/new",
        "high",
    )
    assert state.llm_defaults_digest is not None


def _capabilities() -> dict[str, object]:
    return {
        "source": "test",
        "available": True,
        "agents": [
            {
                "id": "codex",
                "available": True,
                "models": [
                    {
                        "id": "gpt-new",
                        "available": True,
                        "efforts": ["max"],
                        "default_effort": "max",
                    }
                ],
                "default_model": "gpt-new",
            }
        ],
        "defaults": {"agent": "codex", "models": {}, "efforts": {}},
        "errors": [],
    }


def test_llm_defaults_from_linked_worktree_updates_only_owner_checkout(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    owner = tmp_path / "owner"
    owner.mkdir()
    ensure_project_layout(
        owner,
        defaults={
            "llm_engine": "codex",
            "llm_model": "gpt-old",
            "llm_effort": "max",
        },
    )
    subprocess.run(["git", "init", "-q"], cwd=owner, check=True)
    subprocess.run(["git", "add", "-A"], cwd=owner, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=FT Test",
            "-c",
            "user.email=ft@example.test",
            "commit",
            "-qm",
            "initial",
        ],
        cwd=owner,
        check=True,
    )
    worktree = tmp_path / "cycle"
    subprocess.run(
        ["git", "worktree", "add", "-qb", "cycle-test", str(worktree)],
        cwd=owner,
        check=True,
    )
    worktree_before = (worktree / ".ft" / "manifest.yml").read_text(encoding="utf-8")
    probe = Mock(return_value=_capabilities())
    monkeypatch.setattr(cli_main, "find_project_root", lambda: worktree)
    monkeypatch.setattr(cli_main, "discover_llm_capabilities", probe)

    cli_main.cmd_llm_defaults(
        SimpleNamespace(
            agent="codex",
            model="gpt-new",
            effort="max",
            json=True,
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert canonical_project_root(worktree) == owner
    assert probe.call_args.kwargs == {"cwd": owner}
    assert payload["updated"] is True
    owner_manifest = yaml.safe_load(
        (owner / ".ft" / "manifest.yml").read_text(encoding="utf-8")
    )
    assert owner_manifest["defaults"] == {
        "llm_engine": "codex",
        "llm_model": "gpt-new",
        "llm_effort": "max",
    }
    assert (worktree / ".ft" / "manifest.yml").read_text(encoding="utf-8") == worktree_before
