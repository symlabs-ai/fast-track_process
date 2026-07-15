"""Focused contracts for deterministic feature-delta context composition."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
from unittest.mock import patch

from ft.engine.context_profiles import (
    FEATURE_DELTA_PROFILES,
    _feature_target_ids,
    _targeted_id_lines,
    compose_context_profile,
)
from ft.engine.delegate import DelegateResult
from ft.engine.graph import Node
from ft.engine.llm_defaults import LLMSelection
from ft.engine.runner import StepRunner, ValidationResult


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _runner(tmp_path: Path, *, node_type: str = "discovery") -> tuple[StepRunner, Node]:
    root = tmp_path / "product"
    root.mkdir()
    process = tmp_path / "process.yml"
    process.write_text(
        "id: profile_test\n"
        "version: '1.0.0'\n"
        "title: Profile test\n"
        "nodes:\n"
        "  - id: feature.work\n"
        f"    type: {node_type}\n"
        "    title: Work\n"
        "    executor: codex\n"
        f"    context_profile: feature_delta.{('review' if node_type == 'review' else 'discovery')}\n"
        "    outputs: [docs/output.md]\n"
        "    next: feature.end\n"
        "  - {id: feature.end, type: end, title: End}\n",
        encoding="utf-8",
    )
    runner = StepRunner(
        process_path=process,
        state_path=root / "state" / "engine_state.yml",
        project_root=root,
        llm_engine="codex",
    )
    runner.init_state()
    return runner, runner.graph.get_node("feature.work")


def test_profile_caps_are_explicit() -> None:
    assert {
        name: profile.max_chars
        for name, profile in FEATURE_DELTA_PROFILES.items()
    } == {
        "feature_delta.discovery": 64_000,
        "feature_delta.implement": 48_000,
        "feature_delta.evidence": 40_000,
        "feature_delta.review": 56_000,
        "feature_delta.reconcile": 72_000,
    }


def test_feedback_sentinel_uses_only_explicit_last_approval(tmp_path: Path) -> None:
    none = compose_context_profile(
        "feature_delta.discovery", tmp_path, "TASK"
    )
    present = compose_context_profile(
        "feature_delta.discovery",
        tmp_path,
        "TASK",
        last_approval_message="confirma streaming",
    )

    assert 'CURRENT_FEEDBACK={"state":"none","text":null}' in none.context
    assert (
        'CURRENT_FEEDBACK={"state":"present","text":"confirma streaming"}'
        in present.context
    )


def test_allowlist_never_loads_cycle_state_logs_or_archives(tmp_path: Path) -> None:
    _write(tmp_path, "docs/feature.md", "ALLOWED")
    for relative in (
        ".ft/cycles/cycle-99/feature.md",
        "state/feature.md",
        "logs/feature.md",
        "docs/archive/feature.md",
    ):
        _write(tmp_path, relative, f"FORBIDDEN:{relative}")

    result = compose_context_profile(
        "feature_delta.review", tmp_path, "TASK"
    )

    assert "ALLOWED" in result.context
    assert "FORBIDDEN:" not in result.context
    assert ".ft/cycles/**" in result.deny_read_paths
    assert "state/**" in result.deny_read_paths


def test_discovery_reentry_keeps_all_drafts_as_context(tmp_path: Path) -> None:
    drafts = {
        "docs/feature-discovery.md": "DRAFT DISCOVERY",
        "docs/feature-questions.md": "DRAFT QUESTIONS",
        "docs/feature.md": "DRAFT FEATURE",
        "docs/feature-plan.md": "DRAFT PLAN",
    }
    for relative, content in drafts.items():
        _write(tmp_path, relative, content)

    result = compose_context_profile(
        "feature_delta.discovery", tmp_path, "TASK"
    )

    for relative, content in drafts.items():
        assert relative in result.loaded_paths
        assert content in result.context


def test_review_never_injects_its_existing_report(tmp_path: Path) -> None:
    _write(tmp_path, "docs/feature.md", "APPROVED SCOPE")
    _write(tmp_path, "docs/feature-review.md", "STALE REVIEW MUST NOT RETURN")

    result = compose_context_profile("feature_delta.review", tmp_path, "TASK")

    assert "APPROVED SCOPE" in result.context
    assert "STALE REVIEW MUST NOT RETURN" not in result.context
    assert "docs/feature-review.md" not in result.loaded_paths


def test_tech_stack_alias_is_deduplicated_and_conflict_is_manifested(
    tmp_path: Path,
) -> None:
    _write(tmp_path, "docs/TECH_STACK.md", "same stack")
    _write(tmp_path, "docs/tech_stack.md", "same stack")

    identical = compose_context_profile(
        "feature_delta.discovery", tmp_path, "TASK"
    )

    assert "### docs/TECH_STACK.md" in identical.context
    assert "### docs/tech_stack.md" not in identical.context
    assert '"state":"identical"' in identical.context

    _write(tmp_path, "docs/tech_stack.md", "conflicting legacy stack")
    conflict = compose_context_profile(
        "feature_delta.discovery", tmp_path, "TASK"
    )

    assert "### docs/TECH_STACK.md" in conflict.context
    assert "### docs/tech_stack.md" in conflict.context
    assert '"state":"conflict"' in conflict.context
    assert '"preferred":"docs/TECH_STACK.md"' in conflict.context


def test_receipt_is_compact_and_preserves_schema_v2_file_count(tmp_path: Path) -> None:
    _write(tmp_path, "docs/feature.md", "scope")
    receipt = {
        "schema_version": 2,
        "kind": "feature-product-validation",
        "result": "pass",
        "fingerprint": "sha256:aggregate",
        "recorded_at": "2026-07-13T12:00:00Z",
        "product_root": "src",
        "commands": [["make", "-C", "src", "test"]],
        "file_count": 37,
        "manifest_path": "docs/feature-validation.manifest.json",
        "files": None,
        "secret_per_file_hash": "must-not-leak",
    }
    _write(
        tmp_path,
        "docs/feature-validation.json",
        json.dumps(receipt),
    )

    result = compose_context_profile(
        "feature_delta.review", tmp_path, "TASK"
    )

    assert '"file_count":37' in result.context
    assert "sha256:aggregate" in result.context
    assert "must-not-leak" not in result.context
    assert '"files"' not in result.context
    assert "manifest_path" not in result.context


def test_critical_docs_survive_huge_prd_and_context_stays_bounded(tmp_path: Path) -> None:
    _write(tmp_path, "docs/feature.md", "CRITICAL FEATURE")
    _write(tmp_path, "docs/feature-plan.md", "CRITICAL PLAN")
    _write(tmp_path, "docs/feature-review.md", "CRITICAL REVIEW")
    _write(tmp_path, "docs/PROJECT_BACKLOG.md", "CRITICAL BACKLOG")
    _write(tmp_path, "docs/FEATURES.md", "CRITICAL CATALOG")
    _write(tmp_path, "docs/PRD.md", "P" * 200_000)
    _write(
        tmp_path,
        "docs/feature-validation.json",
        json.dumps({"schema_version": 2, "result": "pass", "file_count": 4}),
    )

    result = compose_context_profile(
        "feature_delta.reconcile", tmp_path, "TASK"
    )

    assert len(result.context) <= 72_000
    for marker in (
        "CRITICAL FEATURE",
        "CRITICAL PLAN",
        "CRITICAL REVIEW",
        "CRITICAL BACKLOG",
        "CRITICAL CATALOG",
        '"file_count":4',
    ):
        assert marker in result.context


def test_opencode_denies_allowlisted_docs_even_when_cap_omits_them(
    tmp_path: Path,
) -> None:
    for relative in FEATURE_DELTA_PROFILES["feature_delta.implement"].paths:
        if relative == "docs/feature-validation.json":
            _write(
                tmp_path,
                relative,
                json.dumps({"schema_version": 2, "result": "pass", "file_count": 1}),
            )
        else:
            _write(tmp_path, relative, relative + "\n" + ("X" * 20_000))

    result = compose_context_profile(
        "feature_delta.implement", tmp_path, "TASK"
    )

    assert len(result.context) <= 48_000
    assert "docs/api_contract.md" not in result.loaded_paths
    assert "docs/api_contract.md" in result.deny_read_paths
    assert "docs/**" in result.deny_read_paths


def test_large_canonical_docs_keep_head_tail_and_targeted_pb_feat_rows(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "docs/feature.md",
        "---\nbacklog_item: PB-777\ntarget_feature: FEAT-123\n---\n",
    )
    _write(
        tmp_path,
        "docs/PROJECT_BACKLOG.md",
        "BACKLOG HEAD\n"
        + ("A" * 12_000)
        + "\n| PB-777 | accepted | TARGET BACKLOG ROW |\n"
        + ("B" * 12_000)
        + "\nBACKLOG TAIL\n",
    )
    _write(
        tmp_path,
        "docs/FEATURES.md",
        "FEATURES HEAD\n"
        + ("C" * 12_000)
        + "\n| FEAT-123 | TARGET FEATURE ROW | PB-777 |\n"
        + ("D" * 12_000)
        + "\nFEATURES TAIL\n",
    )

    result = compose_context_profile(
        "feature_delta.reconcile", tmp_path, "TASK"
    )

    assert len(result.context) <= 72_000
    assert "BACKLOG HEAD" in result.context
    assert "BACKLOG TAIL" in result.context
    assert "FEATURES HEAD" in result.context
    assert "FEATURES TAIL" in result.context
    assert "TARGET BACKLOG ROW" in result.context
    assert "TARGET FEATURE ROW" in result.context
    assert "TARGETED_ID_EXCERPTS" in result.context
    assert "docs/**" in result.deny_read_paths


def test_target_ids_come_only_from_anchored_feature_fields(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "docs/feature.md",
        "---\nbacklog_item: PB-007\ntarget_feature: 'FEAT-123'\n---\n"
        "Body mentions PB-999 and FEAT-999 incidentally.\n",
    )

    assert _feature_target_ids(tmp_path) == ("PB-007", "FEAT-123")


def test_targeted_rows_match_complete_ids_not_numeric_prefixes(tmp_path: Path) -> None:
    path = tmp_path / "docs" / "PROJECT_BACKLOG.md"
    _write(
        tmp_path,
        "docs/PROJECT_BACKLOG.md",
        "| PB-0010 | must not match |\n"
        "| PB-001 | exact match |\n"
        "| prefix-PB-001-suffix | must not match |\n",
    )

    selected = _targeted_id_lines(path, ("PB-001",))

    assert "exact match" in selected
    assert "PB-0010" not in selected
    assert "prefix-PB-001-suffix" not in selected


def test_git_manifest_diff_and_changed_file_excerpts_are_focal(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "tests@example.invalid")
    _git(tmp_path, "config", "user.name", "Tests")
    _write(tmp_path, "src/app.py", "VALUE = 1\n")
    _write(tmp_path, "tests/test_app.py", "def test_old():\n    assert True\n")
    _write(tmp_path, "docs/unrelated.md", "DO NOT DISCOVER")
    _write(tmp_path, "docs/PROJECT_BACKLOG.md", "PB-001 planned\n")
    _git(tmp_path, "add", "src", "tests", "docs")
    _git(tmp_path, "commit", "-qm", "base")
    base = _git(tmp_path, "rev-parse", "HEAD")

    _write(tmp_path, "src/app.py", "VALUE = 2\n")
    _write(tmp_path, "tests/test_new.py", "def test_new():\n    assert True\n")
    _write(tmp_path, "docs/PROJECT_BACKLOG.md", "PB-001 accepted\n")

    discovery = compose_context_profile(
        "feature_delta.discovery", tmp_path, "TASK", base_commit=base
    )
    implementation = compose_context_profile(
        "feature_delta.implement", tmp_path, "TASK", base_commit=base
    )
    reconcile = compose_context_profile(
        "feature_delta.reconcile", tmp_path, "TASK", base_commit=base
    )

    assert "git:feature-delta.product-manifest" in discovery.context
    assert "src/app.py" in discovery.context
    assert "tests/test_app.py" in discovery.context
    assert "git:feature-delta.changed-files" in implementation.context
    assert "git:feature-delta.diff" in implementation.context
    assert "current:src/app.py" in implementation.context
    assert "current:tests/test_new.py" in implementation.context
    assert "VALUE = 2" in implementation.context
    assert "DO NOT DISCOVER" not in implementation.context
    assert "docs/PROJECT_BACKLOG.md" in reconcile.context
    assert "+PB-001 accepted" in reconcile.context


def test_normal_runner_profile_skips_hyper_kb_and_cycle_memory(tmp_path: Path) -> None:
    runner, node = _runner(tmp_path)
    _write(Path(runner.project_root), "docs/feature.md", "draft survives")
    state = runner.state_mgr.load()
    state.last_approval_message = "keep scope"
    selection = LLMSelection("codex", "gpt-test", "max")

    with (
        patch.object(
            runner,
            "_scan_hyper_mode_docs",
            side_effect=AssertionError("HyperMode must be skipped"),
        ),
        patch(
            "ft.engine.runner.scan_kb_lessons",
            side_effect=AssertionError("KB must be skipped"),
        ),
        patch.object(
            runner,
            "_inject_cycle_memory",
            side_effect=AssertionError("cycle memory must be skipped"),
        ),
    ):
        prompt, compact, deny = runner._build_llm_task_context(
            node, state, selection
        )

    assert compact is None
    assert deny == []
    assert "draft survives" in prompt
    assert 'CURRENT_FEEDBACK={"state":"present","text":"keep scope"}' in prompt


def test_review_profile_works_for_codex_without_opencode_instructions(
    tmp_path: Path,
) -> None:
    runner, node = _runner(tmp_path, node_type="review")
    node.outputs = ["docs/feature-review.md", "docs/screenshots/feature/"]
    _write(Path(runner.project_root), "docs/feature.md", "review scope")

    codex_prompt, codex_deny = runner._build_review_task_context(
        node, LLMSelection("codex", None, None)
    )
    opencode_prompt, opencode_deny = runner._build_review_task_context(
        node, LLMSelection("opencode", None, None)
    )

    assert "review scope" in codex_prompt
    assert "INSTRUCAO OPENCODE REVIEW" not in codex_prompt
    assert codex_deny == []
    assert "INSTRUCAO OPENCODE REVIEW" in opencode_prompt
    assert "docs/feature.md" in opencode_deny
    assert "docs/screenshots/feature/" in opencode_deny


def test_auto_fix_profile_skips_hypermode_and_composes_delegated_prompt(
    tmp_path: Path,
) -> None:
    runner, node = _runner(tmp_path)
    _write(Path(runner.project_root), "docs/feature.md", "AUTO FIX SCOPE")
    result = DelegateResult(True, "DONE", [], ["docs/output.md"])

    with (
        patch.object(
            runner,
            "_scan_hyper_mode_docs",
            side_effect=AssertionError("HyperMode must be skipped"),
        ),
        patch(
            "ft.engine.runner.scan_kb_lessons",
            side_effect=AssertionError("KB must be skipped"),
        ),
        patch.object(
            runner,
            "_inject_cycle_memory",
            side_effect=AssertionError("cycle memory must be skipped"),
        ),
        patch("ft.engine.runner.delegate_to_llm", return_value=result) as delegated,
        patch(
            "ft.engine.runner.run_validators",
            return_value=ValidationResult(True, False, None, []),
        ),
    ):
        assert runner._run_auto_fix(node, "validator failed") is True

    task = delegated.call_args.kwargs["task"]
    assert "CONTEXT_PROFILE=feature_delta.discovery" in task
    assert "AUTO FIX SCOPE" in task


def test_stakeholder_retry_uses_same_profile_compositor(tmp_path: Path) -> None:
    runner, retry_node = _runner(tmp_path)
    root = Path(runner.project_root)
    _write(root, "docs/feature.md", "RETRY SCOPE")
    gate = Node(
        id="feature.gate",
        type="human_gate",
        title="Gate",
        executor="python",
        next="feature.end",
    )
    runner.graph.nodes[gate.id] = gate
    retry_node.next = gate.id
    state = runner.state_mgr.load()
    state.completed_nodes = [retry_node.id]
    state.current_node = gate.id
    state.node_status = "awaiting_approval"
    state.pending_approval = gate.id
    runner.state_mgr.save()
    delegated_result = DelegateResult(True, "DONE", [], ["docs/output.md"])

    with (
        patch.object(
            runner,
            "_scan_hyper_mode_docs",
            side_effect=AssertionError("HyperMode must be skipped"),
        ),
        patch(
            "ft.engine.runner.delegate_with_feedback",
            return_value=delegated_result,
        ) as delegated,
        patch(
            "ft.engine.runner.run_validators",
            return_value=ValidationResult(True, False, None, []),
        ),
    ):
        runner.reject("corrija o contrato", retry=True)

    original_task = delegated.call_args.kwargs["original_task"]
    assert "CONTEXT_PROFILE=feature_delta.discovery" in original_task
    assert "RETRY SCOPE" in original_task
    assert "REJEITADO PELO STAKEHOLDER: corrija o contrato" in (
        delegated.call_args.kwargs["feedback"]
    )
