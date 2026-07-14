"""Contracts for the minimal, single-delegation tweak context profile."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest

from ft.engine.context_profiles import (
    KNOWN_CONTEXT_PROFILES,
    TWEAK_PROFILE,
    TWEAK_PROFILES,
    compose_context_profile,
)
from ft.engine.llm_defaults import LLMSelection
from ft.engine.runner import StepRunner


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


def test_tweak_direct_is_bounded_and_strictly_allowlisted(tmp_path: Path) -> None:
    _write(tmp_path, "docs/feature-request.md", "Mude a cor do botão Salvar para azul.")
    _write(
        tmp_path,
        "docs/tweak-baseline.yml",
        "version: 1\nclassification: visual\nproduct_root: src\n",
    )
    _write(tmp_path, "docs/TECH_STACK.md", "React + TypeScript")
    _write(tmp_path, "docs/ui_criteria.md", "Use os tokens visuais existentes.")
    _write(tmp_path, "docs/api_contract.md", "API_SECRET_MUST_STAY_OUT")
    for relative in (
        "docs/PRD.md",
        "docs/PROJECT_BACKLOG.md",
        "docs/FEATURES.md",
        "docs/feature.md",
        "docs/tweak-report.md",
        ".ft/cycles/cycle-99/tweak-report.md",
        "state/engine_state.yml",
        "logs/provider.log",
        "docs/archive/old.md",
    ):
        _write(tmp_path, relative, f"FORBIDDEN:{relative}")

    with patch(
        "ft.engine.context_profiles._feature_target_ids",
        side_effect=AssertionError("tweak must not inspect feature/backlog targeting"),
    ):
        result = compose_context_profile(
            TWEAK_PROFILE,
            tmp_path,
            "IMPLEMENT NOW",
            last_approval_message="preserve o hover",
        )

    assert TWEAK_PROFILE in KNOWN_CONTEXT_PROFILES
    assert TWEAK_PROFILES[TWEAK_PROFILE].max_chars == 24_000
    assert len(result.context) <= 24_000
    assert "Mude a cor do botão Salvar para azul." in result.context
    assert "classification: visual" in result.context
    assert "React + TypeScript" in result.context
    assert "Use os tokens visuais existentes." in result.context
    assert "API_SECRET_MUST_STAY_OUT" not in result.context
    assert "FORBIDDEN:" not in result.context
    assert (
        'CURRENT_FEEDBACK={"state":"present","text":"preserve o hover"}'
        in result.context
    )
    assert result.prompt.endswith("IMPLEMENT NOW")
    assert "docs/**" in result.deny_read_paths
    assert ".ft/cycles/**" in result.deny_read_paths
    assert "state/**" in result.deny_read_paths
    # Optional docs are denied too when relevance filtering omits them.
    assert "docs/api_contract.md" in result.deny_read_paths


def test_tweak_direct_rejects_allowlisted_symlink_into_cycle_history(
    tmp_path: Path,
) -> None:
    _write(tmp_path, "docs/feature-request.md", "Mude a cor do botão para azul.")
    _write(tmp_path, "docs/tweak-baseline.yml", "classification: visual\n")
    _write(tmp_path, ".ft/cycles/cycle-99/secret.md", "SECRET_FROM_ARCHIVE")
    criteria = tmp_path / "docs" / "ui_criteria.md"
    criteria.symlink_to(tmp_path / ".ft" / "cycles" / "cycle-99" / "secret.md")

    result = compose_context_profile(TWEAK_PROFILE, tmp_path, "TASK")

    assert "SECRET_FROM_ARCHIVE" not in result.context
    assert "docs/ui_criteria.md" not in result.loaded_paths

    no_feedback = compose_context_profile(TWEAK_PROFILE, tmp_path, "TASK")
    assert 'CURRENT_FEEDBACK={"state":"none","text":null}' in no_feedback.context


@pytest.mark.parametrize(
    ("request_text", "classification", "expected", "unexpected"),
    (
        (
            "Ajuste o payload do endpoint GET /health.",
            "minor_behavior",
            "API CONTRACT INCLUDED",
            "UI CRITERIA INCLUDED",
        ),
        (
            "Troque o tooltip de ajuda para um texto mais curto.",
            "copy",
            "UI CRITERIA INCLUDED",
            "API CONTRACT INCLUDED",
        ),
        (
            "Ajuste o valor padrão da preferência local.",
            "minor_behavior",
            None,
            "UI CRITERIA INCLUDED",
        ),
    ),
)
def test_tweak_injects_ui_or_api_docs_only_when_relevant(
    tmp_path: Path,
    request_text: str,
    classification: str,
    expected: str | None,
    unexpected: str | None,
) -> None:
    _write(tmp_path, "docs/feature-request.md", request_text)
    _write(
        tmp_path,
        "docs/tweak-baseline.yml",
        f"version: 1\nclassification: {classification}\n",
    )
    _write(tmp_path, "docs/ui_criteria.md", "UI CRITERIA INCLUDED")
    _write(tmp_path, "docs/api_contract.md", "API CONTRACT INCLUDED")

    result = compose_context_profile(TWEAK_PROFILE, tmp_path, "TASK")

    if expected is not None:
        assert expected in result.context
    if unexpected is not None:
        assert unexpected not in result.context
    if expected is None:
        assert "UI CRITERIA INCLUDED" not in result.context
        assert "API CONTRACT INCLUDED" not in result.context


def test_tweak_delta_precedes_bounded_product_manifest_on_retry(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "tests@example.invalid")
    _git(tmp_path, "config", "user.name", "Tests")
    _write(tmp_path, "docs/feature-request.md", "Mude a cor do botão para azul.")
    _write(
        tmp_path,
        "docs/tweak-baseline.yml",
        "version: 1\nclassification: visual\nproduct_root: src\n",
    )
    _write(tmp_path, "src/theme.css", ".save { color: red; }\n")
    for index in range(150):
        _write(tmp_path, f"src/components/component_{index:03}.tsx", "export default null;\n")
    _git(tmp_path, "add", "docs", "src")
    _git(tmp_path, "commit", "-qm", "base")
    base = _git(tmp_path, "rev-parse", "HEAD")

    _write(tmp_path, "src/theme.css", ".save { color: blue; }\n")
    result = compose_context_profile(
        TWEAK_PROFILE,
        tmp_path,
        "TASK",
        base_commit=base,
    )

    assert len(result.context) <= 24_000
    assert "git:tweak.changed-files" in result.context
    assert "git:tweak.diff" in result.context
    assert "+.save { color: blue; }" in result.context
    assert "current:src/theme.css" in result.context
    assert "git:tweak.product-manifest" in result.context
    assert result.context.index("git:tweak.diff") < result.context.index(
        "git:tweak.product-manifest"
    )

    manifest_match = result.context.split("### git:tweak.product-manifest\n", 1)[1]
    manifest = json.loads(manifest_match.splitlines()[0])
    assert manifest["tracked_count"] == 151
    assert len(manifest["paths"]) == 120
    assert manifest["truncated"] is True


def test_opencode_runner_uses_tweak_profile_without_hypermode_kb_or_history(
    tmp_path: Path,
) -> None:
    root = tmp_path / "product"
    root.mkdir()
    _write(root, "docs/feature-request.md", "Mude o botão para azul.")
    _write(root, "docs/tweak-baseline.yml", "version: 1\nclassification: visual\n")
    _write(root, ".ft/cycles/cycle-88/secret.md", "HISTORY MUST NOT LEAK")
    process = tmp_path / "process.yml"
    process.write_text(
        "id: tweak_test\n"
        "version: '1.0.0'\n"
        "title: Tweak test\n"
        "nodes:\n"
        "  - id: tweak.implement\n"
        "    type: build\n"
        "    title: Implement directly\n"
        "    executor: opencode\n"
        "    context_profile: tweak.direct\n"
        "    outputs: [docs/tweak-report.md]\n"
        "    next: tweak.end\n"
        "  - {id: tweak.end, type: end, title: End}\n",
        encoding="utf-8",
    )
    runner = StepRunner(
        process_path=process,
        state_path=root / "state" / "engine_state.yml",
        project_root=root,
        llm_engine="opencode",
    )
    runner.init_state()
    node = runner.graph.get_node("tweak.implement")
    state = runner.state_mgr.load()

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
        prompt, compact, deny_paths = runner._build_llm_task_context(
            node,
            state,
            LLMSelection("opencode", None, None),
            allow_compact=False,
        )

    assert compact is None
    assert "CONTEXT_PROFILE=tweak.direct" in prompt
    assert "Mude o botão para azul." in prompt
    assert "HISTORY MUST NOT LEAK" not in prompt
    assert "docs/**" in deny_paths
    assert ".ft/cycles/**" in deny_paths
    assert "docs/feature-request.md" in deny_paths
