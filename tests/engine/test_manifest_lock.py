"""Concorrência e atomicidade das mutações do manifest v2."""

from __future__ import annotations

import os
from pathlib import Path
import stat
import subprocess
import sys
import threading
import time

import pytest
import yaml

from ft.cli import main as cli_main
from ft.engine import layout
from ft.engine import paths
from ft.engine.state import process_start_identity


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    layout.ensure_project_layout(root)
    for name in ("feature", "bug"):
        process = root / ".ft" / "process" / name / "process.yml"
        process.parent.mkdir(parents=True)
        process.write_text(f"id: {name}\nnodes: []\n", encoding="utf-8")
        layout.register_project_process(
            root,
            process_name=name,
            process_path=process,
            template_id=name,
            entrypoint="feature",
            source_digest=f"sha256:{name}-initial",
            set_default=name == "feature",
        )
    return root


def _spawn_lock_probe(
    project: Path,
    ready: Path,
    acquired: Path,
) -> subprocess.Popen[str]:
    code = "\n".join(
        [
            "import fcntl",
            "import sys",
            "from pathlib import Path",
            "from ft.engine import paths",
            "project, ready, acquired = map(Path, sys.argv[1:])",
            "lock_path = paths.ft_home() / 'locks' / paths.project_runtime_key(project) / '.manifest.lock'",
            "lock_path.parent.mkdir(parents=True, exist_ok=True)",
            "with lock_path.open('a+', encoding='utf-8') as handle:",
            "    try:",
            "        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)",
            "    except BlockingIOError:",
            "        ready.write_text('blocked', encoding='utf-8')",
            "        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)",
            "    else:",
            "        ready.write_text('immediate', encoding='utf-8')",
            "    acquired.write_text('acquired', encoding='utf-8')",
        ]
    )
    env = os.environ.copy()
    repo_root = Path(__file__).resolve().parents[2]
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, [str(repo_root), env.get("PYTHONPATH", "")])
    )
    return subprocess.Popen(
        [sys.executable, "-c", code, str(project), str(ready), str(acquired)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _wait_for_path(path: Path, *, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.01)
    pytest.fail(f"timeout aguardando {path}")


def _wait_child(child: subprocess.Popen[str]) -> None:
    try:
        _stdout, stderr = child.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        child.kill()
        _stdout, stderr = child.communicate(timeout=5)
        pytest.fail(f"processo auxiliar não terminou: {stderr}")
    assert child.returncode == 0, stderr


def test_manifest_write_lock_is_reentrant_in_same_thread(tmp_path: Path) -> None:
    root = _project(tmp_path)

    with layout._manifest_write_lock(root):
        with layout._manifest_write_lock(root):
            layout.refresh_process_digests(
                root,
                "bug",
                source_digest="sha256:bug-nested",
            )

    assert (
        layout.read_manifest(root)["processes"]["bug"]["source_digest"]
        == "sha256:bug-nested"
    )


def test_suspended_project_lock_allows_other_process_then_reacquires(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
    root = tmp_path / "project"
    root.mkdir()
    first: subprocess.Popen[str] | None = None
    second: subprocess.Popen[str] | None = None

    try:
        with layout._manifest_write_lock(root):
            first_ready = tmp_path / "first.ready"
            first_acquired = tmp_path / "first.acquired"
            first = _spawn_lock_probe(root, first_ready, first_acquired)
            _wait_for_path(first_ready)
            assert first_ready.read_text(encoding="utf-8") == "blocked"
            assert not first_acquired.exists()

            with layout._suspend_manifest_write_lock(root):
                _wait_for_path(first_acquired)
                _wait_child(first)
                first = None

            second_ready = tmp_path / "second.ready"
            second_acquired = tmp_path / "second.acquired"
            second = _spawn_lock_probe(root, second_ready, second_acquired)
            _wait_for_path(second_ready)
            assert second_ready.read_text(encoding="utf-8") == "blocked"
            assert not second_acquired.exists(), (
                "o processo externo atravessou o lock que deveria ter sido "
                "readquirido"
            )

        assert second is not None
        _wait_child(second)
        second = None
        assert second_acquired.is_file()
    finally:
        for child in (first, second):
            if child is not None and child.poll() is None:
                child.kill()
                child.communicate(timeout=5)


def test_concurrent_manifest_updates_preserve_both_records(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = _project(tmp_path)
    first_write_entered = threading.Event()
    release_first_write = threading.Event()
    call_guard = threading.Lock()
    write_count = 0
    original_write = layout._atomic_write_manifest

    def delayed_write(path: Path, manifest: dict) -> None:
        nonlocal write_count
        with call_guard:
            current = write_count
            write_count += 1
        if current == 0:
            first_write_entered.set()
            assert release_first_write.wait(timeout=2)
        original_write(path, manifest)

    monkeypatch.setattr(layout, "_atomic_write_manifest", delayed_write)
    errors: list[BaseException] = []

    def update(name: str) -> None:
        try:
            layout.refresh_process_digests(
                root,
                name,
                source_digest=f"sha256:{name}-concurrent",
            )
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    first = threading.Thread(target=update, args=("feature",), daemon=True)
    second = threading.Thread(target=update, args=("bug",), daemon=True)
    first.start()
    assert first_write_entered.wait(timeout=2)
    second.start()
    time.sleep(0.05)
    assert second.is_alive(), "segunda mutação deveria aguardar o lock"
    release_first_write.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert not first.is_alive() and not second.is_alive()
    assert errors == []
    processes = layout.read_manifest(root)["processes"]
    assert processes["feature"]["source_digest"] == "sha256:feature-concurrent"
    assert processes["bug"]["source_digest"] == "sha256:bug-concurrent"


def test_directory_fsync_failure_does_not_report_false_rollback(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = _project(tmp_path)
    original_fsync = os.fsync

    def fsync_with_unsupported_directory(fd: int) -> None:
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("directory fsync unsupported")
        original_fsync(fd)

    monkeypatch.setattr(layout.os, "fsync", fsync_with_unsupported_directory)

    layout.refresh_process_digests(
        root,
        "bug",
        source_digest="sha256:bug-durable",
    )

    assert (
        layout.read_manifest(root)["processes"]["bug"]["source_digest"]
        == "sha256:bug-durable"
    )


def test_migrate_dry_run_holds_project_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
    root = tmp_path / "project"
    root.mkdir()
    ready = tmp_path / "migrate.ready"
    acquired = tmp_path / "migrate.acquired"
    child: subprocess.Popen[str] | None = None

    def migration_probe(
        project_root: str | Path,
        *,
        dry_run: bool,
        cycle_id: str,
    ) -> list[str]:
        nonlocal child
        assert Path(project_root) == root
        assert dry_run is True
        assert cycle_id == "legacy-unscoped"
        child = _spawn_lock_probe(root, ready, acquired)
        _wait_for_path(ready)
        assert ready.read_text(encoding="utf-8") == "blocked"
        assert not acquired.exists()
        return ["preview coordenado"]

    monkeypatch.setattr(layout, "_migrate_legacy_layout", migration_probe)
    try:
        assert layout.migrate_legacy_layout(root, dry_run=True) == [
            "preview coordenado"
        ]
        assert child is not None
        _wait_child(child)
        child = None
        assert acquired.is_file()
    finally:
        if child is not None and child.poll() is None:
            child.kill()
            child.communicate(timeout=5)


@pytest.mark.parametrize("sentinel_kind", ["continuous", "isolated"])
def test_live_startup_sentinel_blocks_migration_including_dry_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sentinel_kind: str,
) -> None:
    monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
    root = tmp_path / "project"
    process = root / "process" / "process.yml"
    process.parent.mkdir(parents=True)
    process.write_text(
        "id: test\n"
        "version: '1.0.0'\n"
        "nodes:\n"
        "  - id: end\n"
        "    type: end\n"
        "    title: End\n",
        encoding="utf-8",
    )
    reservation = cli_main._startup_reservation_payload(
        Path(".ft/process/feature/process.yml"),
        isolated=sentinel_kind == "isolated",
    )
    reservation["_lock"]["pid_start"] = process_start_identity(os.getpid())
    target = (
        paths.continuous_startup_path(root)
        if sentinel_kind == "continuous"
        else paths.startup_reservation_path(root)
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        yaml.safe_dump(reservation, sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="ciclo/runtime presente"):
        layout.migrate_legacy_layout(root, dry_run=True)

    assert process.is_file()
