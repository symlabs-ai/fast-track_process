"""Concurrency contracts for independent Fast Track cycles."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest
import yaml

from ft.engine import paths
from ft.runs import (
    AmbiguousCycleError,
    CloseCoordinator,
    CycleAlreadyExists,
    CycleNotFoundError,
    LockKind,
    NoCycleError,
    ProjectLockReentryBlocked,
    RunCoordinator,
    allocate_cycle,
    close_merge_lock,
    inspect_project_lock,
    project_prep_lock,
    select_cycle,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _project(tmp_path: Path, *templates: str) -> Path:
    root = tmp_path / "product"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "tests@example.com")
    _git(root, "config", "user.name", "FT Tests")
    for template in templates:
        process = root / ".ft" / "process" / template / "process.yml"
        process.parent.mkdir(parents=True, exist_ok=True)
        process.write_text(
            yaml.safe_dump(
                {
                    "meta": {"id": template, "title": template},
                    "nodes": [{"id": "start", "type": "end"}],
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
    (root / ".ft" / "cycles").mkdir(parents=True)
    (root / ".ft" / "manifest.yml").write_text(
        yaml.safe_dump({"schema_version": 3, "processes": {}}),
        encoding="utf-8",
    )
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "bootstrap")
    return root


def _subprocess_env(ft_home: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["FT_HOME"] = str(ft_home)
    prior = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(REPO_ROOT) + (os.pathsep + prior if prior else "")
    return env


def _write_cycle(
    root: Path,
    cycle_id: str,
    *,
    status: str = "ready",
    mtime: float | None = None,
) -> Path:
    worktree = paths.worktrees_home(root) / cycle_id
    state = worktree / "state" / "engine_state.yml"
    state.parent.mkdir(parents=True)
    (worktree / ".git").write_text("gitdir: fake\n", encoding="utf-8")
    state.write_text(
        yaml.safe_dump(
            {
                "current_cycle": cycle_id,
                "node_status": status,
                "current_node": "start",
            }
        ),
        encoding="utf-8",
    )
    if mtime is not None:
        os.utime(worktree, (mtime, mtime))
    return worktree


def test_prepare_and_merge_locks_are_external_and_independent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    home = tmp_path / "runtime"
    monkeypatch.setenv("FT_HOME", str(home))

    with project_prep_lock(root) as preparation:
        assert preparation.path.is_relative_to(home)
        assert not preparation.path.is_relative_to(root)
        assert inspect_project_lock(root, LockKind.PREPARATION).state == "held"
        # Merge coordination has a separate lock and cannot block startup.
        with close_merge_lock(root, timeout=0) as merge:
            assert merge.path != preparation.path
            assert inspect_project_lock(root, LockKind.MERGE).state == "held"

    assert inspect_project_lock(root, LockKind.PREPARATION).state == "free"
    assert inspect_project_lock(root, LockKind.MERGE).state == "free"


def test_stale_metadata_is_detected_only_without_a_kernel_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.setenv("FT_HOME", str(tmp_path / "runtime"))

    with project_prep_lock(root) as lock:
        lock_path = lock.path
        owner = lock.owner
    assert owner is not None
    lock_path.write_text(json.dumps(owner.__dict__) + "\n", encoding="utf-8")

    inspection = inspect_project_lock(root, LockKind.PREPARATION)
    assert inspection.state == "stale"
    assert inspection.owner == owner
    with project_prep_lock(root) as recovered:
        assert recovered.recovered_stale_owner == owner

    lock_path.write_text("{broken-json\n", encoding="utf-8")
    malformed = inspect_project_lock(root, LockKind.PREPARATION)
    assert malformed.state == "stale"
    assert malformed.owner is None
    with project_prep_lock(root) as recovered:
        assert recovered.recovered_stale_metadata is True


def test_suspended_lease_makes_sibling_startup_wait(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "product"
    root.mkdir()
    home = tmp_path / "runtime"
    monkeypatch.setenv("FT_HOME", str(home))
    env = _subprocess_env(home)
    suspended = tmp_path / "suspended"
    operation_done = tmp_path / "operation-done"
    observed = tmp_path / "observed"
    owner_code = """
import pathlib, sys, time
from ft.runs import project_prep_lock
with project_prep_lock(sys.argv[1]) as lock:
    with lock.suspend():
        pathlib.Path(sys.argv[2]).touch()
        time.sleep(0.4)
        pathlib.Path(sys.argv[3]).touch()
"""
    sibling_code = """
import pathlib, sys
from ft.runs import project_prep_lock
with project_prep_lock(sys.argv[1], timeout=5):
    pathlib.Path(sys.argv[3]).write_text(str(pathlib.Path(sys.argv[2]).exists()))
"""
    owner = subprocess.Popen(
        [
            sys.executable,
            "-c",
            owner_code,
            str(root),
            str(suspended),
            str(operation_done),
        ],
        cwd=REPO_ROOT,
        env=env,
    )
    deadline = time.monotonic() + 5
    while not suspended.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert suspended.exists()
    inspection = inspect_project_lock(root, LockKind.PREPARATION)
    assert inspection.state == "suspended"

    sibling = subprocess.Popen(
        [
            sys.executable,
            "-c",
            sibling_code,
            str(root),
            str(operation_done),
            str(observed),
        ],
        cwd=REPO_ROOT,
        env=env,
    )
    assert owner.wait(timeout=10) == 0
    assert sibling.wait(timeout=10) == 0
    assert observed.read_text(encoding="utf-8") == "True"


def test_suspended_lease_rejects_owner_descendant_without_waiting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "product"
    root.mkdir()
    home = tmp_path / "runtime"
    monkeypatch.setenv("FT_HOME", str(home))
    env = _subprocess_env(home)
    marker = tmp_path / "hook-result"
    child_code = """
import pathlib, sys
from ft.runs import ProjectLockReentryBlocked, project_prep_lock
try:
    with project_prep_lock(sys.argv[1], timeout=3):
        result = 'acquired'
except ProjectLockReentryBlocked:
    result = 'blocked'
pathlib.Path(sys.argv[2]).write_text(result)
"""

    started = time.monotonic()
    with project_prep_lock(root) as lock:
        with lock.suspend():
            with pytest.raises(ProjectLockReentryBlocked):
                with project_prep_lock(root):
                    pass
            child = subprocess.run(
                [sys.executable, "-c", child_code, str(root), str(marker)],
                cwd=REPO_ROOT,
                env=env,
                timeout=5,
            )
    assert child.returncode == 0
    assert marker.read_text(encoding="utf-8") == "blocked"
    assert time.monotonic() - started < 2


def test_cycle_selection_never_uses_recency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "product"
    root.mkdir()
    monkeypatch.setenv("FT_HOME", str(tmp_path / "runtime"))

    with pytest.raises(NoCycleError):
        select_cycle(root)

    older = _write_cycle(root, "cycle-01-feature", mtime=10)
    assert select_cycle(root).worktree == older

    newer = _write_cycle(root, "cycle-02-tweak", mtime=20)
    with pytest.raises(AmbiguousCycleError) as error:
        select_cycle(root)
    assert error.value.cycle_ids == ("cycle-01-feature", "cycle-02-tweak")
    assert select_cycle(root, "cycle-01-feature").worktree == older
    assert select_cycle(root, "cycle-02-tweak").worktree == newer
    with pytest.raises(CycleNotFoundError):
        select_cycle(root, "cycle-99")


def test_terminal_filter_is_explicit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "product"
    root.mkdir()
    monkeypatch.setenv("FT_HOME", str(tmp_path / "runtime"))
    _write_cycle(root, "cycle-01-feature", status="completed")
    current = _write_cycle(root, "cycle-02-tweak", status="blocked")

    with pytest.raises(AmbiguousCycleError):
        select_cycle(root, include_terminal=True)
    assert select_cycle(root, include_terminal=False).worktree == current


def test_cycle_allocation_is_atomic_across_processes(tmp_path: Path) -> None:
    root = tmp_path / "product"
    root.mkdir()
    home = tmp_path / "runtime"
    env = _subprocess_env(home)
    code = (
        "from ft.runs import allocate_cycle; import sys; "
        "print(allocate_cycle(sys.argv[1], template_id='feature'))"
    )
    workers = [
        subprocess.Popen(
            [sys.executable, "-c", code, str(root)],
            cwd=REPO_ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(6)
    ]
    results = [worker.communicate(timeout=20) for worker in workers]
    assert all(worker.returncode == 0 for worker in workers), results
    cycle_ids = {stdout.strip() for stdout, _stderr in results}
    assert cycle_ids == {f"cycle-{number:02d}-feature" for number in range(1, 7)}


def test_allocation_never_reuses_legacy_numbers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "product"
    root.mkdir()
    monkeypatch.setenv("FT_HOME", str(tmp_path / "runtime"))
    ledger = paths.worktrees_home(root) / ".cycles"
    ledger.parent.mkdir(parents=True)
    ledger.write_text("01\n20\n", encoding="utf-8")

    assert allocate_cycle(root, template_id="feature") == "cycle-21-feature"
    with pytest.raises(CycleAlreadyExists):
        allocate_cycle(root, requested_name="cycle-20-reused")


def test_run_coordinator_creates_worktree_with_pinned_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _project(tmp_path, "feature")
    monkeypatch.setenv("FT_HOME", str(tmp_path / "runtime"))
    coordinator = RunCoordinator(root)
    with project_prep_lock(root):
        assert coordinator.preflight() == _git(root, "rev-parse", "HEAD")
        prepared = coordinator.prepare(
            template_id="feature",
            process_path=".ft/process/feature/process.yml",
            first_node="plan",
        )

    assert prepared.cycle_id == "cycle-01-feature"
    assert prepared.worktree.is_dir()
    assert (prepared.worktree / ".git").is_file()
    assert prepared.branch == prepared.cycle_id
    state = yaml.safe_load(prepared.state_path.read_text(encoding="utf-8"))
    assert state["template_id"] == "feature"
    assert state["process_path"] == ".ft/process/feature/process.yml"
    assert state["process_digest"] == prepared.pin.process_digest
    assert state["process_immutable"] is True
    assert state["base_commit"] == prepared.base_commit
    assert state["worktree_branch"] == prepared.branch
    assert state["current_node"] == "plan"
    assert select_cycle(root).state_path == prepared.state_path


def test_commit_hook_invoking_ft_fails_fast_under_suspended_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _project(tmp_path, "feature")
    monkeypatch.setenv("FT_HOME", str(tmp_path / "runtime"))
    marker = tmp_path / "pre-commit-result"
    hook = root / ".git" / "hooks" / "pre-commit"
    hook.write_text(
        "\n".join(
            [
                f"#!{sys.executable}",
                "import pathlib, sys",
                f"sys.path.insert(0, {str(REPO_ROOT)!r})",
                "from ft.runs import ProjectLockReentryBlocked, project_prep_lock",
                "try:",
                f"    with project_prep_lock({str(root)!r}, timeout=3):",
                "        result = 'acquired'",
                "except ProjectLockReentryBlocked:",
                "    result = 'blocked'",
                f"pathlib.Path({str(marker)!r}).write_text(result)",
                "sys.exit(0)",
                "",
            ]
        ),
        encoding="utf-8",
    )
    hook.chmod(0o755)
    process = root / ".ft" / "process" / "feature" / "process.yml"
    process.write_text(process.read_text(encoding="utf-8") + "# update\n")
    _git(root, "add", str(process.relative_to(root)))

    with project_prep_lock(root) as lock:
        with lock.suspend():
            _git(root, "commit", "-qm", "update process")

    assert marker.read_text(encoding="utf-8") == "blocked"
    assert _git(root, "status", "--porcelain") == ""


def test_worktree_hook_invoking_ft_fails_fast_without_deadlock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _project(tmp_path, "feature")
    monkeypatch.setenv("FT_HOME", str(tmp_path / "runtime"))
    marker = tmp_path / "post-checkout-result"
    hook = root / ".git" / "hooks" / "post-checkout"
    hook.write_text(
        "\n".join(
            [
                f"#!{sys.executable}",
                "import pathlib, sys",
                f"sys.path.insert(0, {str(REPO_ROOT)!r})",
                "from ft.runs import ProjectLockReentryBlocked, project_prep_lock",
                "try:",
                f"    with project_prep_lock({str(root)!r}, timeout=3):",
                "        result = 'acquired'",
                "except ProjectLockReentryBlocked:",
                "    result = 'blocked'",
                f"pathlib.Path({str(marker)!r}).write_text(result)",
                "sys.exit(0)",
                "",
            ]
        ),
        encoding="utf-8",
    )
    hook.chmod(0o755)

    prepared = RunCoordinator(root).prepare(
        template_id="feature",
        process_path=".ft/process/feature/process.yml",
        first_node="start",
    )

    assert prepared.worktree.is_dir()
    assert marker.read_text(encoding="utf-8") == "blocked"


def test_two_coordinators_prepare_different_templates_concurrently(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path, "feature", "tweak")
    home = tmp_path / "runtime"
    env = _subprocess_env(home)
    code = """
import json
import sys
from ft.runs import RunCoordinator
root, template = sys.argv[1:]
prepared = RunCoordinator(root).prepare(
    template_id=template,
    process_path=f'.ft/process/{template}/process.yml',
    first_node='start',
)
print(json.dumps({'cycle': prepared.cycle_id, 'worktree': str(prepared.worktree)}))
"""
    workers = [
        subprocess.Popen(
            [sys.executable, "-c", code, str(root), template],
            cwd=REPO_ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for template in ("feature", "tweak")
    ]
    outputs = [worker.communicate(timeout=30) for worker in workers]
    assert all(worker.returncode == 0 for worker in workers), outputs
    payloads = [json.loads(stdout) for stdout, _stderr in outputs]
    assert {
        int(payload["cycle"].split("-")[1]) for payload in payloads
    } == {1, 2}
    for payload, template in zip(payloads, ("feature", "tweak"), strict=True):
        assert payload["cycle"].endswith(f"-{template}")
        state = Path(payload["worktree"]) / "state" / "engine_state.yml"
        assert state.is_file()


def test_close_coordinator_serializes_operations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "product"
    root.mkdir()
    home = tmp_path / "runtime"
    monkeypatch.setenv("FT_HOME", str(home))
    _write_cycle(root, "cycle-01-feature", status="completed")

    marker = tmp_path / "inside"
    result = CloseCoordinator(root).run(
        "cycle-01-feature",
        lambda cycle: (marker.write_text(cycle.name, encoding="utf-8"), cycle.name)[1],
    )
    assert result == "cycle-01-feature"
    assert marker.read_text(encoding="utf-8") == result

    env = _subprocess_env(home)
    acquired = tmp_path / "first-acquired"
    released = tmp_path / "first-released"
    observed = tmp_path / "second-observed"
    first_code = """
import pathlib, sys, time
from ft.runs import close_merge_lock
with close_merge_lock(sys.argv[1]):
    pathlib.Path(sys.argv[2]).touch()
    time.sleep(0.4)
    pathlib.Path(sys.argv[3]).touch()
"""
    second_code = """
import pathlib, sys
from ft.runs import close_merge_lock
with close_merge_lock(sys.argv[1]):
    pathlib.Path(sys.argv[3]).write_text(str(pathlib.Path(sys.argv[2]).exists()))
"""
    first = subprocess.Popen(
        [sys.executable, "-c", first_code, str(root), str(acquired), str(released)],
        cwd=REPO_ROOT,
        env=env,
    )
    deadline = time.monotonic() + 5
    while not acquired.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert acquired.exists()
    second = subprocess.Popen(
        [sys.executable, "-c", second_code, str(root), str(released), str(observed)],
        cwd=REPO_ROOT,
        env=env,
    )
    assert first.wait(timeout=10) == 0
    assert second.wait(timeout=10) == 0
    assert observed.read_text(encoding="utf-8") == "True"
