"""Linux subreaper wrapper for bounded engine subprocesses.

The wrapper owns exactly one command tree.  It adopts descendants that detach
with ``setsid``/double-fork and guarantees that none survive the command's
return or a termination signal sent to the wrapper.
"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def _enable_subreaper() -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    prctl = getattr(libc, "prctl", None)
    if prctl is None:
        raise OSError("prctl indisponível")
    prctl.argtypes = [
        ctypes.c_int,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
    ]
    prctl.restype = ctypes.c_int
    if prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        error_number = ctypes.get_errno()
        raise OSError(error_number, "PR_SET_CHILD_SUBREAPER falhou")


def _reap() -> None:
    while True:
        try:
            pid, _status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid == 0:
            return


def _direct_children(parent_pid: int) -> list[int]:
    path = Path(f"/proc/{parent_pid}/task/{parent_pid}/children")
    try:
        return [int(value) for value in path.read_text().split()]
    except (FileNotFoundError, PermissionError, ProcessLookupError, ValueError):
        return []


def _descendants() -> set[int]:
    found: set[int] = set()
    pending = _direct_children(os.getpid())
    while pending:
        pid = pending.pop()
        if pid in found:
            continue
        found.add(pid)
        pending.extend(_direct_children(pid))
    return found


def _signal_descendants(process_signal: signal.Signals) -> set[int]:
    _reap()
    found = _descendants()
    for pid in sorted(found, reverse=True):
        try:
            os.kill(pid, process_signal)
        except ProcessLookupError:
            pass
    return found


def _cleanup() -> bool:
    deadline = time.monotonic() + 0.25
    while time.monotonic() < deadline:
        if not _signal_descendants(signal.SIGTERM):
            return True
        time.sleep(0.01)
    for _ in range(100):
        if not _signal_descendants(signal.SIGKILL):
            return True
        time.sleep(0.01)
    return not _descendants()


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments[:1] == ["--"]:
        arguments.pop(0)
    if not arguments:
        print("process_supervisor: comando ausente", file=sys.stderr)
        return 2
    if not sys.platform.startswith("linux"):
        print("process_supervisor: requer Linux", file=sys.stderr)
        return 125
    try:
        _enable_subreaper()
    except OSError as exc:
        print(f"process_supervisor: {exc}", file=sys.stderr)
        return 125

    child: subprocess.Popen[bytes] | None = None
    return_code = 126

    def stop(signum: int, _frame: object) -> None:
        signal.signal(signum, signal.SIG_IGN)
        _cleanup()
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        child = subprocess.Popen(arguments)
        return_code = child.wait()
    except OSError as exc:
        print(f"process_supervisor: não iniciou comando: {exc}", file=sys.stderr)
    finally:
        cleaned = _cleanup()
        if child is not None and child.poll() is None:
            try:
                child.kill()
            except ProcessLookupError:
                pass
        if not cleaned:
            print("process_supervisor: descendentes não encerrados", file=sys.stderr)

    if not cleaned:
        return 125
    if return_code < 0:
        return 128 + (-return_code)
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
