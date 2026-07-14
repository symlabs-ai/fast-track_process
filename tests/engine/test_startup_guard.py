"""Regressões do guard de startup e da coordenação de process update."""

from __future__ import annotations

from argparse import Namespace
from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from ft.cli import main as cli_main
from ft.engine import layout
from ft.engine import paths
from ft.engine import process_update as pu
from ft.engine.state import (
    StateLockError,
    StateManager,
    lock_owner_is_alive,
    process_start_identity,
)


def _write_yaml(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def _registered_process(root: Path, *, run_mode: str = "isolated") -> Path:
    layout.ensure_project_layout(root)
    process = root / ".ft" / "process" / "feature" / "process.yml"
    process.parent.mkdir(parents=True)
    process.write_text(
        "id: feature\n"
        "version: '1.0.0'\n"
        "nodes:\n"
        "  - id: end\n"
        "    type: end\n"
        "    title: End\n",
        encoding="utf-8",
    )
    if run_mode != "isolated":
        (process.parent / "environment.yml").write_text(
            f"run_mode: {run_mode}\n",
            encoding="utf-8",
        )
    layout.register_project_process(
        root,
        process_name="feature",
        process_path=process,
        template_id="feature",
        entrypoint="feature",
        set_default=True,
    )
    return process


def _prepare_args(
    *,
    force: bool = True,
    worktree: str | None = None,
) -> Namespace:
    return Namespace(
        _require_git_worktree=False,
        force=force,
        worktree=worktree,
        parallel=False,
        codex=None,
        claude=None,
        gemini=None,
        opencode=None,
        effort=None,
    )


def _run_catalog_writer(root: Path, operation: str) -> subprocess.CompletedProcess[str]:
    code = "\n".join(
        [
            "import sys",
            "from pathlib import Path",
            "from ft.cli.main import materialize_process_template",
            "from ft.engine.layout import update_manifest_llm_defaults",
            "root = Path(sys.argv[1])",
            "operation = sys.argv[2]",
            "try:",
            "    if operation == 'materialize':",
            "        materialize_process_template('bug', root, entrypoint='feature')",
            "    elif operation == 'defaults':",
            "        update_manifest_llm_defaults(root, llm_engine='codex', llm_model='barrier-model', llm_effort='high')",
            "    else:",
            "        raise AssertionError(operation)",
            "except RuntimeError as exc:",
            "    print(str(exc))",
            "    raise SystemExit(23)",
        ]
    )
    env = os.environ.copy()
    repo_root = Path(__file__).resolve().parents[2]
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, [str(repo_root), env.get("PYTHONPATH", "")])
    )
    return subprocess.run(
        [sys.executable, "-c", code, str(root), operation],
        env=env,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )


def _tree_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_live_continuous_startup_reservation_blocks_process_update(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    reservation = cli_main._startup_reservation_payload(
        Path(".ft/process/feature/process.yml")
    )
    reservation["_lock"]["pid_start"] = process_start_identity(os.getpid())
    _write_yaml(paths.continuous_startup_path(root), reservation)

    with pytest.raises(RuntimeError, match="continuous|ciclo ativo"):
        cli_main._process_update_runtime_guard(root, {"bug"})


def test_dead_continuous_startup_reservation_is_ignored(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    reservation = cli_main._startup_reservation_payload(
        Path(".ft/process/feature/process.yml")
    )
    reservation["_lock"]["pid"] = 999_999_999
    reservation["_lock"]["pid_start"] = "dead-process"
    _write_yaml(paths.continuous_startup_path(root), reservation)

    assert cli_main._process_update_runtime_guard(root, {"bug"}) == []


@pytest.mark.parametrize(
    "contents",
    [
        "not: [valid-yaml\n",
        yaml.safe_dump({"node_status": "preparing", "_lock": {}}),
        yaml.safe_dump(["unexpected", "payload"]),
    ],
)
def test_invalid_continuous_startup_reservation_fails_closed(
    tmp_path: Path,
    contents: str,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    startup = paths.continuous_startup_path(root)
    startup.parent.mkdir(parents=True, exist_ok=True)
    startup.write_text(contents, encoding="utf-8")

    with pytest.raises(RuntimeError, match="reserva inválida|ciclo ativo"):
        cli_main._process_update_runtime_guard(root, {"bug"})


@pytest.mark.parametrize("active_kind", ["runtime", "startup"])
def test_force_never_crosses_continuous_runtime_or_startup_reservation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    active_kind: str,
) -> None:
    monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
    root = tmp_path / "project"
    root.mkdir()
    process = _registered_process(root)
    process_relative = Path(".ft/process/feature/process.yml")

    if active_kind == "runtime":
        _write_yaml(
            paths.continuous_state_path(root),
            {
                "process_path": process_relative.as_posix(),
                "current_node": "feature.implement",
                "node_status": "delegated",
                "completed_nodes": [],
                "metrics": {"steps_completed": 0, "steps_total": 1},
            },
        )
    else:
        reservation = cli_main._startup_reservation_payload(
            process_relative,
            isolated=False,
        )
        reservation["_lock"]["pid_start"] = process_start_identity(os.getpid())
        _write_yaml(paths.continuous_startup_path(root), reservation)

    with pytest.raises(RuntimeError, match=r"--force não é seguro"):
        cli_main._prepare_run_runtime(
            _prepare_args(force=True),
            source_project_root=root,
            process_path_at_root=process,
            process_relative=process_relative,
            explicit_cycle_name=None,
            inherited_engine=None,
        )


def test_second_startup_does_not_cleanup_with_live_generic_sentinel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
    root = tmp_path / "project"
    root.mkdir()
    process = _registered_process(root)
    process_relative = Path(".ft/process/feature/process.yml")
    orphan = paths.worktrees_home(root) / "cycle-01-orphan" / "state"
    orphan.mkdir(parents=True)
    reservation = cli_main._startup_reservation_payload(None, isolated=True)
    _write_yaml(paths.startup_reservation_path(root), reservation)

    with pytest.raises(RuntimeError, match=r"--force não é seguro"):
        cli_main._prepare_run_runtime(
            _prepare_args(force=True),
            source_project_root=root,
            process_path_at_root=process,
            process_relative=process_relative,
            explicit_cycle_name=None,
            inherited_engine=None,
        )

    assert orphan.is_dir(), "startup concorrente não pode apagar worktree alheia"


def test_pre_run_stages_under_lock_then_commits_with_guarded_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
    root = tmp_path / "project"
    root.mkdir()
    process = _registered_process(root)
    process_relative = Path(".ft/process/feature/process.yml")
    reservation = paths.startup_reservation_path(root)
    observations: list[str] = []

    def project_lock_entry() -> dict:
        held = getattr(layout._MANIFEST_LOCK_STATE, "held", {})
        assert len(held) == 1
        return next(iter(held.values()))

    def assert_process_scoped_reservation() -> None:
        with pytest.raises(RuntimeError, match="usa o processo 'feature'"):
            cli_main._process_update_runtime_guard(root, {"feature"})
        disjoint = cli_main._process_update_runtime_guard(root, {"bug"})
        assert len(disjoint) == 1
        assert "feature" in disjoint[0]

    def assert_ambiguous_reservation() -> None:
        payload = yaml.safe_load(reservation.read_text(encoding="utf-8"))
        assert payload["process_path"] is None
        for target in ("feature", "bug"):
            with pytest.raises(RuntimeError, match="sem process_path canônico"):
                cli_main._process_update_runtime_guard(root, {target})

    def stage_knowledge(project_root: str) -> tuple[bool, bool, str]:
        assert Path(project_root) == root
        entry = project_lock_entry()
        assert entry["suspended"] is False
        assert reservation.is_file()
        assert_process_scoped_reservation()
        observations.append("stage-locked")
        return True, True, "staged"

    def commit_staged_knowledge(
        project_root: str,
        *,
        label: str,
        verify_hooks: bool,
    ) -> tuple[bool, str]:
        assert Path(project_root) == root
        assert label == "pré-run snapshot"
        assert verify_hooks is True
        entry = project_lock_entry()
        assert entry["suspended"] is True
        assert reservation.is_file()
        assert_ambiguous_reservation()
        observations.append("commit-suspended")
        return True, "snapshot"

    monkeypatch.setattr(
        "ft.engine.git_ops.stage_knowledge",
        stage_knowledge,
    )
    monkeypatch.setattr(
        "ft.engine.git_ops.commit_staged_knowledge",
        commit_staged_knowledge,
    )

    prepared = cli_main._prepare_run_runtime(
        _prepare_args(force=True),
        source_project_root=root,
        process_path_at_root=process,
        process_relative=process_relative,
        explicit_cycle_name="cycle-01-feature",
        inherited_engine=None,
    )

    assert observations == ["stage-locked", "commit-suspended"]
    assert prepared.state_path.is_file()
    assert not reservation.exists()


def test_worktree_setup_suspension_uses_ambiguous_sentinel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
    root = tmp_path / "project"
    root.mkdir()
    process = _registered_process(root)
    process_relative = Path(".ft/process/feature/process.yml")
    reservation = paths.startup_reservation_path(root)
    run_dir = tmp_path / "prepared-worktree"
    setup_observed = False

    monkeypatch.setattr(
        "ft.engine.git_ops.stage_knowledge",
        lambda _root: (True, False, "clean"),
    )

    def setup_worktree(project_root: Path, name: str) -> Path:
        nonlocal setup_observed
        assert project_root == root
        assert name == "cycle-01-feature"
        held = getattr(layout._MANIFEST_LOCK_STATE, "held", {})
        assert len(held) == 1
        assert next(iter(held.values()))["suspended"] is True
        payload = yaml.safe_load(reservation.read_text(encoding="utf-8"))
        assert payload["process_path"] is None
        for target in ("feature", "bug"):
            with pytest.raises(RuntimeError, match="sem process_path canônico"):
                cli_main._process_update_runtime_guard(root, {target})
        run_dir.mkdir()
        setup_observed = True
        return run_dir

    monkeypatch.setattr(cli_main, "_setup_worktree_locked", setup_worktree)

    prepared = cli_main._prepare_run_runtime(
        _prepare_args(force=True, worktree="cycle-01-feature"),
        source_project_root=root,
        process_path_at_root=process,
        process_relative=process_relative,
        explicit_cycle_name=None,
        inherited_engine=None,
    )

    assert setup_observed
    assert prepared.project_root == run_dir
    assert prepared.state_path.is_file()
    assert not reservation.exists()


def test_exclusive_startup_barrier_blocks_materialization_then_restores(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
    root = tmp_path / "project"
    root.mkdir()
    _registered_process(root)
    process_relative = Path(".ft/process/feature/process.yml")
    reservation = paths.startup_reservation_path(root)
    manifest_path = paths.project_manifest(root)
    feature_dir = paths.project_named_process_dir(root, "feature")
    bug_dir = paths.project_named_process_dir(root, "bug")
    manifest_before = manifest_path.read_bytes()
    feature_before = _tree_snapshot(feature_dir)

    try:
        with layout._manifest_write_lock(root):
            cli_main._write_startup_reservation(
                reservation,
                process_relative,
                isolated=True,
            )
            with cli_main._suspend_startup_exclusively(
                root,
                reservation,
                process_relative,
                isolated=True,
            ):
                blocked = _run_catalog_writer(root, "materialize")
                assert blocked.returncode == 23, blocked.stderr
                assert "temporariamente reservado" in blocked.stdout
                assert not bug_dir.exists()
                assert manifest_path.read_bytes() == manifest_before
                assert _tree_snapshot(feature_dir) == feature_before

        restored = yaml.safe_load(reservation.read_text(encoding="utf-8"))
        assert restored["exclusive"] is False
        assert restored["process_path"] == process_relative.as_posix()

        allowed = _run_catalog_writer(root, "materialize")
        assert allowed.returncode == 0, allowed.stdout + allowed.stderr
        assert paths.project_named_process_file(root, "bug").is_file()
        assert "bug" in layout.read_manifest(root)["processes"]
        assert _tree_snapshot(feature_dir) == feature_before
    finally:
        cli_main._release_startup_reservation(root, reservation)


def test_exclusive_startup_barrier_blocks_defaults_then_restores(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
    root = tmp_path / "project"
    root.mkdir()
    _registered_process(root)
    process_relative = Path(".ft/process/feature/process.yml")
    reservation = paths.startup_reservation_path(root)
    manifest_path = paths.project_manifest(root)
    feature_dir = paths.project_named_process_dir(root, "feature")
    manifest_before = manifest_path.read_bytes()
    feature_before = _tree_snapshot(feature_dir)

    try:
        with layout._manifest_write_lock(root):
            cli_main._write_startup_reservation(
                reservation,
                process_relative,
                isolated=True,
            )
            with cli_main._suspend_startup_exclusively(
                root,
                reservation,
                process_relative,
                isolated=True,
            ):
                blocked = _run_catalog_writer(root, "defaults")
                assert blocked.returncode == 23, blocked.stderr
                assert "temporariamente reservado" in blocked.stdout
                assert manifest_path.read_bytes() == manifest_before
                assert _tree_snapshot(feature_dir) == feature_before

        restored = yaml.safe_load(reservation.read_text(encoding="utf-8"))
        assert restored["exclusive"] is False
        assert restored["process_path"] == process_relative.as_posix()

        allowed = _run_catalog_writer(root, "defaults")
        assert allowed.returncode == 0, allowed.stdout + allowed.stderr
        manifest = layout.read_manifest(root)
        assert manifest["defaults"] == {
            "llm_engine": "codex",
            "llm_model": "barrier-model",
            "llm_effort": "high",
        }
        assert _tree_snapshot(feature_dir) == feature_before
    finally:
        cli_main._release_startup_reservation(root, reservation)


def test_unfinished_merge_is_a_durable_catalog_and_startup_barrier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
    root = tmp_path / "project"
    root.mkdir()
    process = _registered_process(root)
    git_dir = root / ".git"
    git_dir.mkdir()
    merge_head = git_dir / "MERGE_HEAD"
    merge_head.write_text("deadbeef\n", encoding="utf-8")

    blocked = _run_catalog_writer(root, "defaults")
    assert blocked.returncode == 23, blocked.stdout + blocked.stderr
    assert "merge Git pendente" in blocked.stdout

    with pytest.raises(RuntimeError, match="merge Git pendente"):
        cli_main._prepare_run_runtime(
            _prepare_args(force=True),
            source_project_root=root,
            process_path_at_root=process,
            process_relative=Path(".ft/process/feature/process.yml"),
            explicit_cycle_name=None,
            inherited_engine=None,
        )
    assert not paths.startup_reservation_path(root).exists()

    merge_head.unlink()
    allowed = _run_catalog_writer(root, "defaults")
    assert allowed.returncode == 0, allowed.stdout + allowed.stderr


def test_isolated_preparing_state_reserves_only_its_canonical_process(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    state_path = (
        paths.worktrees_home(root)
        / "cycle-42-feature"
        / "state"
        / "engine_state.yml"
    )
    _write_yaml(
        state_path,
        {
            "process_path": ".ft/process/feature/process.yml",
            "current_node": "__preparing__",
            "node_status": "preparing",
            "completed_nodes": [],
            "metrics": {"steps_completed": 0, "steps_total": 0},
            "_lock": {
                "owner": "ft_startup",
                "pid": os.getpid(),
                "pid_start": process_start_identity(os.getpid()),
            },
        },
    )

    disjoint = cli_main._process_update_runtime_guard(root, {"bug"})
    assert len(disjoint) == 1
    assert "cycle-42-feature" in disjoint[0]
    assert "feature" in disjoint[0]

    with pytest.raises(RuntimeError, match="usa o processo 'feature'"):
        cli_main._process_update_runtime_guard(root, {"feature"})


def test_dead_preparing_state_does_not_reserve_process(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    state_path = (
        paths.worktrees_home(root)
        / "cycle-42-feature"
        / "state"
        / "engine_state.yml"
    )
    _write_yaml(
        state_path,
        {
            "process_path": ".ft/process/feature/process.yml",
            "current_node": "__preparing__",
            "node_status": "preparing",
            "completed_nodes": [],
            "metrics": {"steps_completed": 0, "steps_total": 0},
            "_lock": {
                "owner": "ft_startup",
                "pid": 999_999_999,
                "pid_start": "dead-process",
            },
        },
    )

    assert cli_main._process_update_runtime_guard(root, {"feature"}) == []


def test_state_claim_rejects_second_live_process(tmp_path: Path) -> None:
    state_path = tmp_path / "project" / "state" / "engine_state.yml"
    manager = StateManager(state_path)
    manager.init_from_graph({"id": "feature"}, "feature.implement", 1)
    manager.release_lock()

    child_code = "\n".join(
        [
            "import sys",
            "from ft.engine.state import StateManager",
            "manager = StateManager(sys.argv[1])",
            "manager.claim()",
            "print('claimed', flush=True)",
            "sys.stdin.readline()",
            "manager.release_lock()",
        ]
    )
    env = os.environ.copy()
    repo_root = Path(__file__).resolve().parents[2]
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, [str(repo_root), env.get("PYTHONPATH", "")])
    )
    child = subprocess.Popen(
        [sys.executable, "-c", child_code, str(state_path)],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "claimed"

        with pytest.raises(StateLockError, match="ja esta rodando"):
            StateManager(state_path).claim()
    finally:
        if child.poll() is None and child.stdin is not None:
            try:
                child.stdin.write("\n")
                child.stdin.flush()
            except BrokenPipeError:
                pass
        try:
            _stdout, stderr = child.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            _stdout, stderr = child.communicate(timeout=5)
            pytest.fail(f"processo auxiliar de claim não terminou: {stderr}")
    assert child.returncode == 0, stderr

    recovered = StateManager(state_path)
    recovered.claim()
    recovered.release_lock()


def test_pid_identity_mismatch_is_not_live_and_cancel_does_not_sigterm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
    root = tmp_path / "project"
    root.mkdir()
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys; print('ready', flush=True); sys.stdin.readline()",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "ready"
        stale_lock = {
            "owner": "ft_engine",
            "pid": child.pid,
            "pid_start": "definitely-not-this-process",
        }
        assert not lock_owner_is_alive(stale_lock)

        reservation = cli_main._startup_reservation_payload(
            Path(".ft/process/feature/process.yml"),
            isolated=False,
        )
        reservation["_lock"] = dict(stale_lock)
        _write_yaml(paths.continuous_startup_path(root), reservation)
        assert cli_main._process_update_runtime_guard(root, {"bug"}) == []

        state_path = root / "cycle-01" / "state" / "engine_state.yml"
        _write_yaml(
            state_path,
            {
                "current_node": "bug.fix",
                "node_status": "delegated",
                "completed_nodes": [],
                "metrics": {"steps_completed": 0, "steps_total": 1},
                "_lock": dict(stale_lock),
            },
        )
        monkeypatch.setattr(cli_main, "find_project_root", lambda: root)
        monkeypatch.setattr(cli_main, "_find_latest_state", lambda _root: state_path)
        monkeypatch.setattr(
            "ft.engine.delegate.delegate_to_llm",
            lambda **_kwargs: Namespace(success=False),
        )

        cli_main.cmd_cancel(Namespace(reason="identidade reciclada"))

        assert child.poll() is None, "PID reciclado não pode receber SIGTERM"
        persisted = yaml.safe_load(state_path.read_text(encoding="utf-8"))
        assert persisted["node_status"] == "cancelled"
        assert persisted["_lock"] is None
    finally:
        if child.poll() is None and child.stdin is not None:
            try:
                child.stdin.write("\n")
                child.stdin.flush()
            except BrokenPipeError:
                pass
        try:
            _stdout, stderr = child.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            _stdout, stderr = child.communicate(timeout=5)
            pytest.fail(f"processo auxiliar não terminou: {stderr}")
    assert child.returncode == 0, stderr


def test_diverged_confirmation_does_not_hold_manifest_write_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "project"
    local_dir = root / ".ft" / "process" / "feature"
    local_dir.mkdir(parents=True)
    (root / ".ft" / "manifest.yml").write_text(
        "schema_version: 2\n", encoding="utf-8"
    )
    state = pu.ProcessDriftState(
        name="feature",
        template_id="feature",
        entrypoint="feature",
        local_dir=local_dir,
        local_process=local_dir / "process.yml",
        template_dir=tmp_path / "templates" / "feature",
        state=pu.STATE_DIVERGED,
        local_digest="sha256:local",
        global_digest="sha256:global",
        base_digest="sha256:base",
    )
    real_lock = layout._manifest_write_lock
    lock_depth = 0

    @contextmanager
    def tracked_lock(project_root: str | Path):
        nonlocal lock_depth
        with real_lock(project_root):
            lock_depth += 1
            try:
                yield
            finally:
                lock_depth -= 1

    def build_staging(_state, staging: Path) -> pu.MergeResult:
        staging.mkdir(parents=True)
        (staging / "process.yml").write_text(
            "id: feature\nnodes: []\n", encoding="utf-8"
        )
        return pu.MergeResult(staging_dir=staging, changed=["process.yml"])

    prompted = False

    def reject_at_prompt(_prompt: str) -> bool:
        nonlocal prompted
        prompted = True
        assert lock_depth == 0, "prompt humano manteve o lock do projeto"
        return False

    monkeypatch.setattr(cli_main, "find_project_root", lambda: root)
    monkeypatch.setattr(cli_main, "_drift_scan", lambda *_args: [state])
    monkeypatch.setattr(cli_main, "_process_update_runtime_guard", lambda *_args: [])
    monkeypatch.setattr(cli_main, "_validate_staged_process", lambda _path: (True, ""))
    monkeypatch.setattr(cli_main, "_print_staged_diff", lambda *_args: None)
    monkeypatch.setattr(cli_main, "_confirm", reject_at_prompt)
    monkeypatch.setattr(layout, "_manifest_write_lock", tracked_lock)
    monkeypatch.setattr(pu, "ensure_base_snapshot", lambda _state: None)
    monkeypatch.setattr(pu, "build_merge_staging", build_staging)

    with pytest.raises(SystemExit) as excinfo:
        cli_main.cmd_process_update(cli_main.argparse.Namespace(name="feature"))

    assert excinfo.value.code == 1
    assert prompted
