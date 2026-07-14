"""Project-scoped coordination locks for independent Fast Track runs.

The lock files live below ``$FT_HOME``.  They are runtime coordination data,
never project metadata, and therefore cannot dirty the checkout that is about
to become a worktree base.

``flock`` is the authority for ownership.  The JSON payload is deliberately
diagnostic only: it lets callers identify a live owner and distinguish stale
metadata after a process crash without ever deleting a lock held by the
kernel.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from enum import Enum
import fcntl
import json
import os
from pathlib import Path
import subprocess
import threading
import time
from typing import Iterator, Literal, TextIO
from uuid import uuid4

from ft.engine import paths
from ft.engine.state import lock_owner_is_alive, process_start_identity


class LockKind(str, Enum):
    """Independent critical sections used by the run lifecycle."""

    PREPARATION = "prepare"
    MERGE = "merge"


LockState = Literal["free", "held", "suspended", "stale", "contended"]


@dataclass(frozen=True)
class LockOwner:
    pid: int
    pid_start: str
    token: str
    acquired_at: str
    project_root: str
    kind: str
    suspended: bool = False

    @classmethod
    def from_payload(cls, payload: object) -> LockOwner | None:
        if not isinstance(payload, dict):
            return None
        try:
            pid = int(payload["pid"])
            fields = {
                "pid": pid,
                "pid_start": str(payload["pid_start"]),
                "token": str(payload["token"]),
                "acquired_at": str(payload["acquired_at"]),
                "project_root": str(payload["project_root"]),
                "kind": str(payload["kind"]),
                "suspended": payload.get("suspended", False),
            }
        except (KeyError, TypeError, ValueError):
            return None
        if not isinstance(fields["suspended"], bool):
            return None
        if not all(
            fields[name]
            for name in fields
            if name not in {"pid", "suspended"}
        ):
            return None
        return cls(**fields)

    @property
    def is_alive(self) -> bool:
        return lock_owner_is_alive(
            {"pid": self.pid, "pid_start": self.pid_start},
            require_identity=True,
        )


@dataclass(frozen=True)
class LockInspection:
    path: Path
    state: LockState
    owner: LockOwner | None = None

    @property
    def is_stale(self) -> bool:
        return self.state == "stale"

    @property
    def is_locked(self) -> bool:
        return self.state in {"held", "suspended", "contended"}


class ProjectLockTimeout(TimeoutError):
    """Raised when a project lock cannot be acquired before its deadline."""

    def __init__(
        self,
        path: Path,
        timeout: float | None,
        owner: LockOwner | None,
    ) -> None:
        self.path = path
        self.timeout = timeout
        self.owner = owner
        owner_detail = f" pelo PID {owner.pid}" if owner is not None else ""
        super().__init__(
            f"lock {path.name} ocupado{owner_detail}"
            + (f" após {timeout:.3f}s" if timeout is not None else "")
        )


class ProjectLockReentryBlocked(RuntimeError):
    """A hook descended from a suspended owner tried to start another run."""

    def __init__(self, path: Path, owner: LockOwner | None) -> None:
        self.path = path
        self.owner = owner
        owner_detail = f" (owner PID {owner.pid})" if owner is not None else ""
        super().__init__(
            f"startup FT recusado dentro de hook Git durante {path.name}{owner_detail}"
        )


_THREAD_LOCKS: dict[Path, threading.RLock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()
_LOCAL_STATE = threading.local()


def _lock_path(project_root: str | Path, kind: LockKind) -> Path:
    root = Path(project_root).resolve()
    return (
        paths.ft_home()
        / "locks"
        / paths.project_runtime_key(root)
        / f".{kind.value}.lock"
    )


def _parent_pid(pid: int) -> int | None:
    """Return one PPID without trusting a recycled owner identity."""

    try:
        status = Path(f"/proc/{int(pid)}/status").read_text(encoding="utf-8")
        for line in status.splitlines():
            if line.startswith("PPid:"):
                return int(line.split(":", 1)[1].strip())
    except (OSError, UnicodeError, ValueError):
        pass
    try:
        result = subprocess.run(
            ["ps", "-o", "ppid=", "-p", str(int(pid))],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    try:
        return int(result.stdout.strip()) if result.returncode == 0 else None
    except ValueError:
        return None


def _is_descendant_of_current_process(ancestor_pid: int) -> bool:
    """True when this process was launched below ``ancestor_pid``.

    Git commonly inserts both its own process and a shell between the FT owner
    and a hook command, so checking only ``getppid()`` is insufficient.
    """

    current = os.getpid()
    visited: set[int] = set()
    for _ in range(64):
        parent = _parent_pid(current)
        if parent is None or parent <= 0 or parent == current or parent in visited:
            return False
        if parent == ancestor_pid:
            return True
        visited.add(parent)
        current = parent
    return False


def _read_owner_payload(handle: TextIO) -> tuple[LockOwner | None, bool]:
    try:
        handle.seek(0)
        raw = handle.read().strip()
        payload = json.loads(raw) if raw else None
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None, True
    return LockOwner.from_payload(payload), bool(raw)


def _read_owner(handle: TextIO) -> LockOwner | None:
    return _read_owner_payload(handle)[0]


def _write_owner(handle: TextIO, owner: LockOwner | None) -> None:
    handle.seek(0)
    handle.truncate()
    if owner is not None:
        json.dump(asdict(owner), handle, sort_keys=True)
        handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())


class ProjectLock:
    """Re-entrant, process-safe lock for one project lifecycle phase."""

    def __init__(
        self,
        project_root: str | Path,
        kind: LockKind,
        *,
        timeout: float | None = None,
        poll_interval: float = 0.05,
    ) -> None:
        if timeout is not None and timeout < 0:
            raise ValueError("timeout do lock não pode ser negativo")
        if poll_interval <= 0:
            raise ValueError("poll_interval deve ser positivo")
        self.project_root = Path(project_root).resolve()
        self.kind = kind
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.path = _lock_path(self.project_root, kind)
        self.owner: LockOwner | None = None
        self.recovered_stale_owner: LockOwner | None = None
        self.recovered_stale_metadata = False
        self._thread_lock: threading.RLock | None = None
        self._handle: TextIO | None = None
        self._nested = False
        self._acquired = False

    @property
    def held(self) -> bool:
        return self._acquired

    def __enter__(self) -> ProjectLock:
        return self.acquire()

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.release()

    def acquire(self) -> ProjectLock:
        if self._acquired:
            raise RuntimeError("instância de lock já adquirida")
        started = time.monotonic()
        deadline = None if self.timeout is None else started + self.timeout
        self.path.parent.mkdir(parents=True, exist_ok=True)

        with _THREAD_LOCKS_GUARD:
            thread_lock = _THREAD_LOCKS.setdefault(self.path, threading.RLock())
        remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
        if remaining is None:
            thread_lock.acquire()
            local_acquired = True
        else:
            local_acquired = thread_lock.acquire(timeout=remaining)
        if not local_acquired:
            raise ProjectLockTimeout(self.path, self.timeout, None)
        self._thread_lock = thread_lock

        held: dict[Path, dict[str, object]] = getattr(_LOCAL_STATE, "held", {})
        _LOCAL_STATE.held = held
        existing = held.get(self.path)
        if existing is not None:
            if existing.get("suspended") is True:
                owner = existing.get("owner")
                self._thread_lock.release()
                self._thread_lock = None
                raise ProjectLockReentryBlocked(
                    self.path,
                    owner if isinstance(owner, LockOwner) else None,
                )
            existing["depth"] = int(existing["depth"]) + 1
            self._nested = True
            self._acquired = True
            self.owner = existing["owner"]  # type: ignore[assignment]
            return self

        handle: TextIO | None = None
        try:
            handle = self.path.open("a+", encoding="utf-8")
            while True:
                try:
                    fcntl.flock(
                        handle.fileno(),
                        fcntl.LOCK_EX | fcntl.LOCK_NB,
                    )
                except BlockingIOError:
                    if deadline is not None and time.monotonic() >= deadline:
                        owner = _read_owner(handle)
                        raise ProjectLockTimeout(self.path, self.timeout, owner)
                    sleep_for = self.poll_interval
                    if deadline is not None:
                        sleep_for = min(
                            sleep_for,
                            max(0.0, deadline - time.monotonic()),
                        )
                    time.sleep(sleep_for)
                    continue

                previous_owner, _previous_payload = _read_owner_payload(handle)
                if (
                    previous_owner is not None
                    and previous_owner.suspended
                    and previous_owner.is_alive
                ):
                    # The kernel lock is intentionally available while the
                    # owner runs Git hooks.  Preserve the logical lease.
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    if _is_descendant_of_current_process(previous_owner.pid):
                        raise ProjectLockReentryBlocked(self.path, previous_owner)
                    if deadline is not None and time.monotonic() >= deadline:
                        raise ProjectLockTimeout(
                            self.path,
                            self.timeout,
                            previous_owner,
                        )
                    sleep_for = self.poll_interval
                    if deadline is not None:
                        sleep_for = min(
                            sleep_for,
                            max(0.0, deadline - time.monotonic()),
                        )
                    time.sleep(sleep_for)
                    continue
                break

            previous_owner, previous_payload = _read_owner_payload(handle)
            if previous_payload:
                # We own the kernel lock, so any payload left behind is stale
                # regardless of whether that PID still happens to exist.
                self.recovered_stale_metadata = True
                self.recovered_stale_owner = previous_owner
            pid_start = process_start_identity(os.getpid())
            if not pid_start:
                raise RuntimeError("não foi possível identificar o processo do lock")
            owner = LockOwner(
                pid=os.getpid(),
                pid_start=pid_start,
                token=uuid4().hex,
                acquired_at=datetime.now(timezone.utc).isoformat(),
                project_root=str(self.project_root),
                kind=self.kind.value,
            )
            _write_owner(handle, owner)
            held[self.path] = {
                "depth": 1,
                "handle": handle,
                "owner": owner,
                "suspended": False,
            }
            self._handle = handle
            self.owner = owner
            self._acquired = True
            return self
        except BaseException:
            if handle is not None:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
                handle.close()
            self._thread_lock.release()
            self._thread_lock = None
            raise

    @contextmanager
    def suspend(self) -> Iterator[ProjectLock]:
        """Temporarily release ``flock`` while retaining a logical lease.

        Independent startups wait for the owner to resume.  A process spawned
        below the owner (typically a Git hook invoking FT) fails immediately
        in :meth:`acquire`, preventing parent/child deadlock.
        """

        if not self._acquired:
            raise RuntimeError("lock precisa estar adquirido antes da suspensão")
        held: dict[Path, dict[str, object]] = getattr(_LOCAL_STATE, "held", {})
        entry = held.get(self.path)
        if entry is None or int(entry.get("depth", 0)) < 1:
            raise RuntimeError("lock não pode ser suspenso neste contexto")
        if entry.get("suspended") is True:
            raise RuntimeError("lock já está suspenso")
        handle = entry.get("handle")
        owner = entry.get("owner")
        if not hasattr(handle, "fileno") or not isinstance(owner, LockOwner):
            raise RuntimeError("estado inválido do lock")

        suspended_owner = replace(owner, suspended=True)
        _write_owner(handle, suspended_owner)  # type: ignore[arg-type]
        entry["owner"] = suspended_owner
        entry["suspended"] = True
        self.owner = suspended_owner
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)  # type: ignore[union-attr]
        try:
            yield self
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)  # type: ignore[union-attr]
            persisted, _has_payload = _read_owner_payload(handle)  # type: ignore[arg-type]
            if persisted is None or persisted.token != suspended_owner.token:
                raise RuntimeError("lease do project lock mudou durante a suspensão")
            resumed_owner = replace(suspended_owner, suspended=False)
            _write_owner(handle, resumed_owner)  # type: ignore[arg-type]
            entry["owner"] = resumed_owner
            entry["suspended"] = False
            self.owner = resumed_owner

    def release(self) -> None:
        if not self._acquired:
            return
        held: dict[Path, dict[str, object]] = getattr(_LOCAL_STATE, "held", {})
        entry = held.get(self.path)
        try:
            if entry is None:
                raise RuntimeError("estado local do lock foi perdido")
            depth = int(entry["depth"])
            if depth > 1:
                entry["depth"] = depth - 1
                return
            handle = entry["handle"]
            if not hasattr(handle, "fileno"):
                raise RuntimeError("handle inválido do lock")
            try:
                _write_owner(handle, None)  # type: ignore[arg-type]
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)  # type: ignore[union-attr]
                handle.close()  # type: ignore[union-attr]
                del held[self.path]
        finally:
            self._acquired = False
            self._handle = None
            if self._thread_lock is not None:
                self._thread_lock.release()
                self._thread_lock = None


def inspect_project_lock(
    project_root: str | Path,
    kind: LockKind,
) -> LockInspection:
    """Inspect a lock without mutating stale diagnostic metadata."""

    path = _lock_path(project_root, kind)
    if not path.exists():
        return LockInspection(path=path, state="free")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            owner = _read_owner(handle)
            state: LockState = (
                "held" if owner is not None and owner.is_alive else "contended"
            )
            return LockInspection(path=path, state=state, owner=owner)
        try:
            owner, has_payload = _read_owner_payload(handle)
            if owner is not None and owner.suspended and owner.is_alive:
                return LockInspection(
                    path=path,
                    state="suspended",
                    owner=owner,
                )
            return LockInspection(
                path=path,
                state="stale" if has_payload else "free",
                owner=owner,
            )
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def project_prep_lock(
    project_root: str | Path,
    *,
    timeout: float | None = None,
) -> Iterator[ProjectLock]:
    """Serialize only the short preparation phase of a new run."""

    with ProjectLock(project_root, LockKind.PREPARATION, timeout=timeout) as lock:
        yield lock


@contextmanager
def close_merge_lock(
    project_root: str | Path,
    *,
    timeout: float | None = None,
) -> Iterator[ProjectLock]:
    """Serialize merges into the owning checkout, independently of runs."""

    with ProjectLock(project_root, LockKind.MERGE, timeout=timeout) as lock:
        yield lock
