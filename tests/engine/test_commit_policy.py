"""Process-scoped Git hook policy contracts."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml

from ft.cli import main as cli_main
from ft.engine.git_ops import (
    auto_commit,
    commit_knowledge,
    verify_hooks_from_process_meta,
)
from ft.engine.graph import Node, ProcessGraph
from ft.engine.layout import ensure_project_layout, register_project_process
from ft.engine.process_validator import validate_process
from ft.engine.runner import StepRunner


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repo(root: Path) -> None:
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "tests@example.invalid")
    _git(root, "config", "user.name", "Tests")
    (root / "README.md").write_text("base\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-qm", "base")


def _process_yaml(*, verify_hooks: bool | None, build: bool = False) -> str:
    policy = (
        ""
        if verify_hooks is None
        else "commit_policy:\n  verify_hooks: " + str(verify_hooks).lower() + "\n"
    )
    nodes = (
        "  - id: build\n"
        "    type: build\n"
        "    title: Build\n"
        "    outputs: [src/app.py]\n"
        "    next: end\n"
        "  - id: end\n"
        "    type: end\n"
        "    title: End\n"
        if build
        else
        "  - id: end\n"
        "    type: end\n"
        "    title: End\n"
    )
    return (
        "id: commit_policy_test\n"
        "version: '1.0.0'\n"
        "title: Commit policy test\n"
        f"{policy}"
        "nodes:\n"
        f"{nodes}"
    )


def _runner(
    tmp_path: Path,
    *,
    verify_hooks: bool | None,
    build: bool = False,
) -> StepRunner:
    process = tmp_path / "process.yml"
    process.write_text(
        _process_yaml(verify_hooks=verify_hooks, build=build),
        encoding="utf-8",
    )
    return StepRunner(
        process_path=process,
        state_path=tmp_path / "state" / "engine_state.yml",
        project_root=tmp_path,
    )


def test_policy_defaults_to_hooks_and_only_literal_false_disables() -> None:
    assert verify_hooks_from_process_meta(None) is True
    assert verify_hooks_from_process_meta({}) is True
    assert verify_hooks_from_process_meta({"commit_policy": {}}) is True
    assert verify_hooks_from_process_meta(
        {"commit_policy": {"verify_hooks": True}}
    ) is True
    assert verify_hooks_from_process_meta(
        {"commit_policy": {"verify_hooks": "false"}}
    ) is True
    assert verify_hooks_from_process_meta(
        {"commit_policy": {"verify_hooks": False}}
    ) is False


@pytest.mark.parametrize(
    "commit_policy",
    (
        "disabled",
        [],
        {"verify_hooks": None},
        {"verify_hooks": 0},
        {"verify_hooks": "false"},
    ),
)
def test_validator_rejects_non_boolean_commit_policy(commit_policy: object) -> None:
    graph = ProcessGraph(
        [Node(id="end", type="end", title="End")],
        {
            "id": "test",
            "version": "1.0.0",
            "commit_policy": commit_policy,
        },
    )

    report = validate_process(graph)

    assert not report.passed
    assert any("commit_policy" in issue.message for issue in report.errors)


@pytest.mark.parametrize("verify_hooks", (True, False))
def test_validator_accepts_boolean_commit_policy(verify_hooks: bool) -> None:
    graph = ProcessGraph(
        [Node(id="end", type="end", title="End")],
        {
            "id": "test",
            "version": "1.0.0",
            "commit_policy": {"verify_hooks": verify_hooks},
        },
    )

    assert validate_process(graph).passed


def test_git_commits_bypass_hooks_and_signing_only_when_disabled(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    hook_marker = repo / "hook-ran"
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\ntouch hook-ran\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)

    (repo / "src.py").write_text("VALUE = 1\n", encoding="utf-8")
    committed, _detail = auto_commit("default must run hook", str(repo))

    assert committed is False
    assert hook_marker.exists()

    hook_marker.unlink()
    prepare_marker = repo / "prepare-hook-ran"
    prepare_hook = repo / ".git" / "hooks" / "prepare-commit-msg"
    prepare_hook.write_text(
        "#!/bin/sh\ntouch prepare-hook-ran\nexit 1\n", encoding="utf-8"
    )
    prepare_hook.chmod(0o755)
    post_marker = repo / "post-hook-ran"
    post_hook = repo / ".git" / "hooks" / "post-commit"
    post_hook.write_text("#!/bin/sh\ntouch post-hook-ran\n", encoding="utf-8")
    post_hook.chmod(0o755)
    _git(repo, "config", "commit.gpgSign", "true")
    _git(repo, "config", "gpg.program", "/bin/false")
    committed, detail = auto_commit(
        "tweak bypass",
        str(repo),
        verify_hooks=False,
    )

    assert committed, detail
    assert not hook_marker.exists()
    assert not prepare_marker.exists()
    assert not post_marker.exists()
    assert _git(repo, "log", "-1", "--pretty=%s").stdout.strip() == "tweak bypass"

    docs = repo / "docs"
    docs.mkdir()
    (docs / "note.md").write_text("snapshot\n", encoding="utf-8")
    process = repo / ".ft" / "process" / "test"
    process.mkdir(parents=True)
    (process / "process.yml").write_text("id: test\n", encoding="utf-8")
    (repo / ".ft" / "manifest.yml").write_text("schema_version: 2\n", encoding="utf-8")
    (repo / ".ft" / ".gitignore").write_text("runtime/\n", encoding="utf-8")
    committed, detail = commit_knowledge(
        str(repo),
        label="tweak snapshot",
        verify_hooks=False,
    )

    assert committed, detail
    assert not hook_marker.exists()
    assert "tweak snapshot" in _git(repo, "log", "-1", "--pretty=%s").stdout


@pytest.mark.parametrize(
    ("policy", "expected"),
    ((None, True), (False, False)),
)
def test_runner_propagates_policy_to_node_and_post_run_commits(
    tmp_path: Path,
    policy: bool | None,
    expected: bool,
) -> None:
    build_root = tmp_path / "build"
    build_root.mkdir()
    build_runner = _runner(build_root, verify_hooks=policy, build=True)
    with patch(
        "ft.engine.runner.auto_commit",
        return_value=(True, "ok"),
    ) as auto:
        build_runner._maybe_auto_commit(build_runner.graph.get_node("build"))

    assert auto.call_args.kwargs["verify_hooks"] is expected

    end_root = tmp_path / "end"
    end_root.mkdir()
    end_runner = _runner(end_root, verify_hooks=policy)
    end_runner.init_state()
    with patch(
        "ft.engine.runner.commit_knowledge",
        return_value=(True, "ok"),
    ) as knowledge:
        end_runner.run(mode="mvp")

    assert knowledge.call_args.kwargs["verify_hooks"] is expected


@pytest.mark.parametrize(
    ("policy", "expected", "expected_flags"),
    (
        (None, True, set()),
        (False, False, {"--no-verify", "--no-gpg-sign"}),
    ),
)
def test_archive_commit_and_merge_receive_process_policy(
    tmp_path: Path,
    policy: bool | None,
    expected: bool,
    expected_flags: set[str],
) -> None:
    work = tmp_path / "work"
    original = tmp_path / "original"
    work.mkdir()
    original.mkdir()
    runner = _runner(work, verify_hooks=policy)
    runner.init_state()
    completed = subprocess.CompletedProcess(
        ["git", "merge"],
        0,
        stdout="merged",
        stderr="",
    )

    with (
        patch.object(
            runner,
            "_detect_worktree",
            return_value=(work, original, "cycle-tweak"),
        ),
        patch(
            "ft.engine.runner.archive_cycle_artifacts",
            return_value=SimpleNamespace(moved=[]),
        ),
        patch(
            "ft.engine.runner.auto_commit",
            return_value=(True, "archived"),
        ) as archive_commit,
        patch("ft.engine.runner.subprocess.run", return_value=completed) as git_run,
    ):
        assert runner.merge_on_close("full")

    assert archive_commit.call_args.kwargs["verify_hooks"] is expected
    merge_command = git_run.call_args.args[0]
    actual_flags = {flag for flag in merge_command if flag.startswith("--no-")}
    assert actual_flags == expected_flags | {"--no-edit"}
    if policy is False:
        assert merge_command[:4] == ["git", "-c", "core.hooksPath=/dev/null", "merge"]
    else:
        assert merge_command[:2] == ["git", "merge"]


class _FakeRunner:
    def __init__(self, *args, **kwargs):
        self._bypass_human_gates = False

    def init_state(self) -> None:
        return None

    def run(self, mode: str = "step") -> None:
        return None


def _run_args(project: Path) -> Namespace:
    return Namespace(
        project=str(project),
        process=None,
        from_project=None,
        hipotese=None,
        demand_input=None,
        bypass_human_gates=False,
        force=True,
        cycle_name=None,
        template=None,
        worktree=None,
        auto=True,
        claude=None,
        codex=None,
        gemini=None,
        opencode=None,
        effort=None,
        verbose=False,
    )


@pytest.mark.parametrize(
    ("policy", "expected"),
    ((None, True), (False, False)),
)
def test_pre_run_knowledge_commit_receives_selected_process_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    policy: bool | None,
    expected: bool,
) -> None:
    monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
    project = tmp_path / "project"
    ensure_project_layout(project)
    process = project / ".ft" / "process" / "policy" / "process.yml"
    process.parent.mkdir(parents=True)
    process.write_text(_process_yaml(verify_hooks=policy), encoding="utf-8")
    register_project_process(
        project,
        process_name="policy",
        process_path=process,
        template_id="policy",
        entrypoint="init",
        set_default=True,
    )

    with (
        patch("ft.cli.main.StepRunner", _FakeRunner),
        patch("ft.cli.main._api_health_check"),
        patch(
            "ft.engine.git_ops.commit_knowledge",
            return_value=(True, "snapshot"),
        ) as knowledge,
    ):
        cli_main.cmd_run(_run_args(project))

    assert knowledge.call_args.kwargs["verify_hooks"] is expected


def test_lightweight_templates_opt_out_after_their_own_deterministic_checks() -> None:
    root = Path(__file__).resolve().parents[2]
    lightweight = {"bug", "tweak"}
    for process_path in (root / "templates").glob("*/process.yml"):
        payload = yaml.safe_load(process_path.read_text())
        verify_hooks = payload.get("commit_policy", {}).get("verify_hooks", True)
        assert verify_hooks is (process_path.parent.name not in lightweight)
