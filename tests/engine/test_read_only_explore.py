"""Contrato do `ft explore` standalone read-only e streaming normalizado."""

from __future__ import annotations

from argparse import Namespace
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from ft.cli import main as cli_main
from ft.engine import read_only_explore as explore


def _args(**overrides) -> Namespace:
    values = {
        "process": None,
        "verbose": False,
        "request": "explique o grafo",
        "finish": False,
        "skip": False,
        "stream_json": False,
        "standalone": False,
        "agent": None,
        "model": None,
        "effort": None,
        "claude": None,
        "codex": None,
        "gemini": None,
        "opencode": None,
        "bypass_human_gates": False,
    }
    values.update(overrides)
    return Namespace(**values)


@pytest.mark.parametrize("agent", ["claude", "codex", "gemini", "opencode"])
def test_commands_standalone_sao_read_only(agent, tmp_path):
    command = explore.build_read_only_command(
        agent=agent,
        prompt="pergunta",
        project_root=tmp_path,
        model="modelo",
        effort="high",
    )

    joined = " ".join(command)
    assert "dangerously" not in joined
    assert "--yolo" not in command
    assert "--auto" not in command
    if agent == "claude":
        assert command[command.index("--permission-mode") + 1] == "plan"
        assert command[command.index("--allowedTools") + 1] == "Read,Glob,Grep"
    elif agent == "codex":
        assert command[command.index("--sandbox") + 1] == "read-only"
        assert "--ephemeral" in command
    elif agent == "gemini":
        assert command[command.index("--approval-mode") + 1] == "plan"
        assert command[command.index("--output-format") + 1] == "stream-json"
    else:
        assert command[command.index("--format") + 1] == "json"


def test_normaliza_deltas_claude_sem_duplicar_resultado():
    normalizer = explore.ExploreStreamNormalizer("claude")
    first = json.dumps({
        "type": "stream_event",
        "event": {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": "Olá "},
        },
    })
    second = json.dumps({
        "type": "stream_event",
        "event": {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": "mundo"},
        },
    })
    assistant = json.dumps({
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": "Olá mundo"}]},
    })
    result = json.dumps({"type": "result", "result": "Olá mundo"})

    assert normalizer.feed(first) == ["Olá "]
    assert normalizer.feed(second) == ["mundo"]
    assert normalizer.feed(assistant) == []
    assert normalizer.feed(result) == []
    assert normalizer.finish() == []
    assert normalizer.text == "Olá mundo"


@pytest.mark.parametrize(
    ("agent", "event", "expected"),
    [
        (
            "codex",
            {"type": "item.completed", "item": {"type": "agent_message", "text": "codex"}},
            "codex",
        ),
        (
            "gemini",
            {"type": "message", "role": "assistant", "content": "gemini", "delta": True},
            "gemini",
        ),
        (
            "opencode",
            {"type": "text", "part": {"type": "text", "text": "opencode"}},
            "opencode",
        ),
    ],
)
def test_normaliza_chunks_dos_demais_providers(agent, event, expected):
    normalizer = explore.ExploreStreamNormalizer(agent)
    assert normalizer.feed(json.dumps(event)) == [expected]
    assert normalizer.text == expected


def test_runner_entrega_chunks_progressivos_e_exit_code(tmp_path, monkeypatch):
    class FakeProcess:
        def __init__(self):
            self.stdout = io.StringIO(
                json.dumps({
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "resposta"},
                }) + "\n"
            )
            self.stderr = io.StringIO("")
            self.pid = 123

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(explore.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    chunks: list[str] = []

    result = explore.run_read_only_explore(
        request="pergunta",
        project_root=tmp_path,
        agent="codex",
        on_chunk=chunks.append,
    )

    assert result == explore.ExploreResult(0, "resposta")
    assert chunks == ["resposta"]
    assert list(tmp_path.iterdir()) == []


def test_runner_reporta_executor_ausente_com_exit_127(tmp_path, monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError(2, "No such file", "codex")

    monkeypatch.setattr(explore.subprocess, "Popen", missing)

    result = explore.run_read_only_explore(
        request="pergunta",
        project_root=tmp_path,
        agent="codex",
    )

    assert result.returncode == 127
    assert result.text == ""
    assert result.error == "executor não encontrado: codex"


def test_opencode_read_only_recusa_execucao_sem_bwrap(tmp_path, monkeypatch):
    monkeypatch.setattr(explore.shutil, "which", lambda executable: None)

    with pytest.raises(explore.ExploreConfigurationError, match="bubblewrap"):
        explore.run_read_only_explore(
            request="pergunta",
            project_root=tmp_path,
            agent="opencode",
        )


def test_parser_aceita_argv_claude_atual_da_f02(tmp_path, monkeypatch):
    project = tmp_path / "project"
    (project / ".ft").mkdir(parents=True)
    (project / ".ft" / "manifest.yml").write_text(
        "schema_version: 2\nprocesses: {}\ndefaults: {}\n",
        encoding="utf-8",
    )
    captured: dict[str, object] = {}
    monkeypatch.chdir(project)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "ft", "explore",
            "--claude", "opus",
            "--effort", "high",
            "--",
            "prompt da F02",
        ],
    )
    monkeypatch.setattr(cli_main, "cmd_explore", lambda args: captured.update(vars(args)))

    cli_main.main()

    assert captured["command"] == "explore"
    assert captured["claude"] == "opus"
    assert captured["effort"] == "high"
    assert captured["request"] == "prompt da F02"
    assert captured["standalone"] is False


def test_argv_f02_e_cancelamento_externo_recolhem_provider(tmp_path):
    project = tmp_path / "project"
    (project / ".ft").mkdir(parents=True)
    (project / ".ft" / "manifest.yml").write_text(
        "schema_version: 2\nprocesses: {}\ndefaults: {}\n",
        encoding="utf-8",
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    pids_path = tmp_path / "provider-pids"
    argv_path = tmp_path / "provider-argv"
    fake_codex = bin_dir / "codex"
    fake_codex.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$@\" > \"$FT_EXPLORE_TEST_ARGV\"\n"
        "sleep 30 &\n"
        "worker=$!\n"
        "printf '%s %s\\n' \"$$\" \"$worker\" > \"$FT_EXPLORE_TEST_PIDS\"\n"
        "wait \"$worker\"\n",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
    env["FT_HOME"] = str(tmp_path / "ft-home")
    env["FT_EXPLORE_TEST_PIDS"] = str(pids_path)
    env["FT_EXPLORE_TEST_ARGV"] = str(argv_path)
    process = subprocess.Popen(
        [
            sys.executable,
            "-m", "ft.cli.main",
            "explore",
            "--codex", "gpt-test",
            "--effort", "high",
            "--standalone",
            "--",
            "prompt da F02",
        ],
        cwd=project,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 5
        while not pids_path.is_file() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert pids_path.is_file(), process.stderr.read() if process.poll() is not None else ""
        provider_pid, worker_pid = [int(value) for value in pids_path.read_text().split()]

        os.killpg(process.pid, signal.SIGTERM)
        assert process.wait(timeout=5) == -signal.SIGTERM

        def alive(pid: int) -> bool:
            status = Path(f"/proc/{pid}/status")
            if not status.is_file():
                return False
            return "\nState:\tZ" not in "\n" + status.read_text(errors="replace")

        deadline = time.monotonic() + 3
        while (alive(provider_pid) or alive(worker_pid)) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not alive(provider_pid)
        assert not alive(worker_pid)

        provider_argv = argv_path.read_text(encoding="utf-8").splitlines()
        assert provider_argv[provider_argv.index("-m") + 1] == "gpt-test"
        assert "model_reasoning_effort=\"high\"" in provider_argv
        assert "--sandbox" in provider_argv
        assert provider_argv[provider_argv.index("--sandbox") + 1] == "read-only"
        assert any("prompt da F02" in argument for argument in provider_argv)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)


def test_cmd_standalone_stream_json_emite_protocolo(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli_main, "find_project_root", lambda: tmp_path)
    monkeypatch.setattr(cli_main, "canonical_project_root", lambda root: Path(root))
    monkeypatch.setattr(cli_main, "_active_exploration_runtime", lambda root: False)
    monkeypatch.setattr(
        cli_main,
        "manifest_llm_defaults",
        lambda root: ("codex", "gpt-saved", "medium"),
    )

    def fake_run(**kwargs):
        kwargs["on_chunk"]("um ")
        kwargs["on_chunk"]("dois")
        assert kwargs["agent"] == "codex"
        assert kwargs["model"] == "gpt-explicit"
        assert kwargs["effort"] == "max"
        return explore.ExploreResult(0, "um dois")

    monkeypatch.setattr(explore, "run_read_only_explore", fake_run)
    cli_main.cmd_explore(_args(
        stream_json=True,
        agent="codex",
        model="gpt-explicit",
        effort="max",
    ))

    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [event["type"] for event in events] == ["start", "chunk", "chunk", "result"]
    assert [event.get("seq") for event in events[1:3]] == [1, 2]
    assert events[0]["read_only"] is True
    assert events[-1] == {"type": "result", "ok": True, "text": "um dois", "exit_code": 0}


def test_cmd_standalone_preserva_exit_code_do_executor(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli_main, "find_project_root", lambda: tmp_path)
    monkeypatch.setattr(cli_main, "canonical_project_root", lambda root: Path(root))
    monkeypatch.setattr(cli_main, "_active_exploration_runtime", lambda root: False)
    monkeypatch.setattr(cli_main, "manifest_llm_defaults", lambda root: ("codex", None, None))
    monkeypatch.setattr(
        explore,
        "run_read_only_explore",
        lambda **kwargs: explore.ExploreResult(7, "parcial", "provider falhou"),
    )

    with pytest.raises(SystemExit, match="7"):
        cli_main.cmd_explore(_args(stream_json=True))

    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert events[-1]["type"] == "error"
    assert events[-1]["exit_code"] == 7
    assert events[-1]["text"] == "parcial"


def test_node_exploration_preserva_fluxo_legado_sem_override(tmp_path, monkeypatch):
    class State:
        exploration_log = []

    class StateManager:
        def load(self):
            return State()

    class Runner:
        state_mgr = StateManager()
        requests: list[str] = []

        def explore_request(self, request):
            self.requests.append(request)

    runner = Runner()
    monkeypatch.setattr(cli_main, "find_project_root", lambda: tmp_path)
    monkeypatch.setattr(cli_main, "canonical_project_root", lambda root: Path(root))
    monkeypatch.setattr(cli_main, "_active_exploration_runtime", lambda root: True)
    monkeypatch.setattr(cli_main, "get_runner", lambda *args, **kwargs: runner)
    monkeypatch.setattr(
        explore,
        "run_read_only_explore",
        lambda **kwargs: pytest.fail("standalone não deve executar no node legado"),
    )

    cli_main.cmd_explore(_args(request="pedido legado"))

    assert runner.requests == ["pedido legado"]


def test_standalone_prevalece_sobre_node_exploration_ativo(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_main, "find_project_root", lambda: tmp_path)
    monkeypatch.setattr(cli_main, "canonical_project_root", lambda root: Path(root))
    monkeypatch.setattr(cli_main, "_active_exploration_runtime", lambda root: True)
    monkeypatch.setattr(cli_main, "manifest_llm_defaults", lambda root: ("codex", None, None))
    monkeypatch.setattr(
        cli_main,
        "get_runner",
        lambda *args, **kwargs: pytest.fail("runner legado não deve ser selecionado"),
    )
    calls: list[dict] = []

    def fake_run(**kwargs):
        calls.append(kwargs)
        kwargs["on_chunk"]("independente")
        return explore.ExploreResult(0, "independente")

    monkeypatch.setattr(explore, "run_read_only_explore", fake_run)

    cli_main.cmd_explore(_args(request="pedido", standalone=True))

    assert len(calls) == 1
    assert calls[0]["project_root"] == tmp_path
