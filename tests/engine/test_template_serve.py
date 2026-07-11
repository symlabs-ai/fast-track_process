import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _stop_owned_process(token: str) -> None:
    mode, raw_pid = token.strip().split(":", 1)
    pid = int(raw_pid)
    try:
        if mode == "group":
            os.killpg(pid, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass


def test_global_serve_script_isolates_occupied_port_and_preserves_listener(tmp_path):
    root = tmp_path / "sample"
    project = root / "project"
    scripts = root / ".ft" / "process" / "scripts"
    project.mkdir(parents=True)
    scripts.mkdir(parents=True)
    (project / "health").write_text("ok\n", encoding="utf-8")
    (project / "Makefile").write_text(
        "PORT ?= 8021\n"
        "run:\n"
        "\tpython -m http.server $(PORT) --bind 127.0.0.1\n"
        "url:\n"
        "\t@echo http://127.0.0.1:$(PORT)\n",
        encoding="utf-8",
    )

    template = (
        Path(__file__).resolve().parents[2]
        / "templates"
        / "fast-track-v3"
        / "scripts"
        / "serve.sh"
    )
    script = scripts / "serve.sh"
    shutil.copy2(template, script)

    occupied_port = _free_port()
    external_root = tmp_path / "external"
    external_root.mkdir()
    external = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "http.server",
            str(occupied_port),
            "--bind",
            "127.0.0.1",
        ],
        cwd=external_root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    for _ in range(50):
        try:
            with socket.create_connection(("127.0.0.1", occupied_port), timeout=0.1):
                break
        except OSError:
            time.sleep(0.02)
    else:
        external.terminate()
        external.wait(timeout=5)
        raise AssertionError("listener externo não iniciou")

    owned_token = ""
    try:
        result = subprocess.run(
            ["bash", str(script)],
            cwd=root,
            env={**os.environ, "PORT": str(occupied_port)},
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, result.stderr
        assert external.poll() is None

        selected_url = (root / ".serve_url").read_text(encoding="utf-8").strip()
        assert selected_url != f"http://127.0.0.1:{occupied_port}"
        owned_token = (root / ".serve.pid").read_text(encoding="utf-8").strip()
        assert owned_token.startswith(("group:", "pid:"))
    finally:
        if owned_token:
            _stop_owned_process(owned_token)
        external.terminate()
        try:
            external.wait(timeout=5)
        except subprocess.TimeoutExpired:
            external.kill()
            external.wait(timeout=5)
