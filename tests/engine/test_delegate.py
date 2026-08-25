"""Unit tests for ft.engine.delegate command selection."""

import json
import os
import subprocess
import sys
import time
from unittest.mock import patch

import pytest

from ft.engine.delegate import (
    DEFAULT_OPENCODE_CONTEXT_LIMIT,
    DEFAULT_OPENCODE_MODEL,
    DEFAULT_OPENCODE_OUTPUT_LIMIT,
    DelegateResult,
    ExecutorIdleTimeout,
    _append_opencode_runtime_diagnostics,
    _build_executor_command,
    _clean_opencode_capture_text,
    _env_nonnegative_int,
    _executor_env,
    _executor_idle_grace_seconds,
    _executor_idle_timeout_seconds,
    _executor_max_wall_timeout_seconds,
    _executor_timeout_seconds,
    _extract_codex_output,
    _extract_opencode_json_text,
    _extract_provider_session_id,
    _has_productive_liveness,
    _is_opencode_internal_log_line,
    _opencode_capture_command,
    _prepare_opencode_sandbox_mounts,
    _process_liveness_snapshot,
    _ProcessLiveness,
    _run_opencode_script,
    _stop_process_tree,
    _stream_process_output,
    _supervised_command,
    _symgateway_workflow_url,
    _wait_for_process,
    _workspace_progress_paths,
    _workspace_progress_snapshot,
    _wrap_opencode_sandbox_command,
    delegate_opencode_file_bundle_raw,
    delegate_to_llm,
    delegate_with_feedback,
)


class TestBuildExecutorCommand:
    @pytest.fixture(autouse=True)
    def _isolate_codex_profile(self, monkeypatch):
        monkeypatch.delenv("FT_CODEX_PROFILE", raising=False)

    def test_symgateway_workflow_url_inserts_or_replaces_label(self):
        assert (
            _symgateway_workflow_url(
                "https://symgateway.symlabs.ai/p/openai/v1", "innovation"
            )
            == "https://symgateway.symlabs.ai/p/openai/w/innovation/v1"
        )
        assert _symgateway_workflow_url(
            "https://symgateway.symlabs.ai/p/anthropic-max/s/ragent/w/old",
            "orchestration",
        ) == ("https://symgateway.symlabs.ai/p/anthropic-max/s/ragent/w/orchestration")
        assert (
            _symgateway_workflow_url("https://api.openai.com/v1", "innovation") is None
        )

    def test_env_nonnegative_int_accepts_zero(self, monkeypatch):
        monkeypatch.setenv("FT_OPENCODE_IDLE_RETRIES", "0")

        assert _env_nonnegative_int("FT_OPENCODE_IDLE_RETRIES") == 0

    def test_codex_ultra_uses_extended_executor_timeout(self, monkeypatch):
        monkeypatch.delenv("FT_CODEX_EXECUTOR_TIMEOUT", raising=False)
        monkeypatch.delenv("FT_LLM_EXECUTOR_TIMEOUT", raising=False)
        monkeypatch.setenv("FT_CODEX_REASONING_EFFORT", "ultra")

        assert _executor_timeout_seconds("codex") == 3600

    def test_provider_executor_timeout_overrides_ultra_default(self, monkeypatch):
        monkeypatch.setenv("FT_CODEX_REASONING_EFFORT", "ultra")
        monkeypatch.setenv("FT_LLM_EXECUTOR_TIMEOUT", "4200")
        monkeypatch.setenv("FT_CODEX_EXECUTOR_TIMEOUT", "5400")

        assert _executor_timeout_seconds("codex") == 5400

    def test_codex_idle_timeout_tracks_real_stream_activity(self, monkeypatch):
        monkeypatch.delenv("FT_CODEX_IDLE_TIMEOUT", raising=False)
        monkeypatch.delenv("FT_LLM_IDLE_TIMEOUT", raising=False)
        monkeypatch.delenv("FT_CODEX_EXECUTOR_TIMEOUT", raising=False)
        monkeypatch.delenv("FT_LLM_EXECUTOR_TIMEOUT", raising=False)

        assert _executor_idle_timeout_seconds("codex") == 480
        assert _executor_idle_timeout_seconds("claude") == 480
        assert _executor_idle_timeout_seconds("codex", 900) == 900

        monkeypatch.setenv("FT_LLM_IDLE_TIMEOUT", "720")
        assert _executor_idle_timeout_seconds("codex") == 720
        assert _executor_idle_timeout_seconds("claude") == 720

        monkeypatch.setenv("FT_CODEX_IDLE_TIMEOUT", "900")
        assert _executor_idle_timeout_seconds("codex") == 900

    def test_absolute_wall_timeout_is_opt_in(self, monkeypatch):
        monkeypatch.delenv("FT_CODEX_MAX_WALL_TIMEOUT", raising=False)
        monkeypatch.delenv("FT_LLM_MAX_WALL_TIMEOUT", raising=False)

        assert _executor_max_wall_timeout_seconds("codex") is None
        assert _executor_max_wall_timeout_seconds("claude") is None

        monkeypatch.setenv("FT_LLM_MAX_WALL_TIMEOUT", "7200")
        assert _executor_max_wall_timeout_seconds("codex") == 7200
        monkeypatch.setenv("FT_CODEX_MAX_WALL_TIMEOUT", "10800")
        assert _executor_max_wall_timeout_seconds("codex") == 10800

    def test_codex_idle_grace_is_bounded_and_overridable(self, monkeypatch):
        monkeypatch.delenv("FT_CODEX_IDLE_GRACE", raising=False)
        monkeypatch.delenv("FT_LLM_IDLE_GRACE", raising=False)

        assert _executor_idle_grace_seconds("codex") == 120
        assert _executor_idle_grace_seconds("opencode") == 0

        monkeypatch.setenv("FT_LLM_IDLE_GRACE", "45")
        assert _executor_idle_grace_seconds("codex") == 45
        monkeypatch.setenv("FT_CODEX_IDLE_GRACE", "0")
        assert _executor_idle_grace_seconds("codex") == 0

    @pytest.mark.parametrize("value", [0, -1, True, 1.5, "10"])
    def test_delegate_rejects_invalid_llm_timeout_before_spawn(self, value):
        with pytest.raises(ValueError, match="llm_timeout_seconds"):
            delegate_to_llm(task="x", llm_timeout_seconds=value)

    def test_builds_claude_command_with_bypass(self):
        cmd = _build_executor_command("claude", "faça algo", "/tmp/proj", 7)
        assert cmd[0] == "claude"
        assert "--dangerously-skip-permissions" in cmd
        assert ["--output-format", "stream-json"] == cmd[1:3]
        assert "--max-turns" in cmd
        assert "7" in cmd
        assert "-p" in cmd
        assert "faça algo" in cmd

    def test_builds_claude_command_with_effort(self):
        cmd = _build_executor_command(
            "claude",
            "faça algo",
            "/tmp/proj",
            7,
            model="fable",
            effort="max",
        )

        assert ["--model", "fable"] == cmd[
            cmd.index("--model") : cmd.index("--model") + 2
        ]
        assert ["--effort", "max"] == cmd[
            cmd.index("--effort") : cmd.index("--effort") + 2
        ]

    def test_builds_claude_new_and_resumed_session_commands(self):
        session_id = "4f5b71b2-f632-4f07-8ee7-4e8fb9946c39"

        fresh = _build_executor_command(
            "claude", "primeiro", "/tmp/proj", 7, session_id=session_id
        )
        resumed = _build_executor_command(
            "claude",
            "segundo",
            "/tmp/proj",
            7,
            session_id=session_id,
            resume_session=True,
        )

        assert fresh[fresh.index("--session-id") + 1] == session_id
        assert "--resume" not in fresh
        assert resumed[resumed.index("--resume") + 1] == session_id
        assert "--session-id" not in resumed

    @pytest.mark.parametrize("effort", [None, "", "default"])
    def test_claude_default_effort_omits_override(self, effort):
        cmd = _build_executor_command(
            "claude", "faça algo", "/tmp/proj", 7, effort=effort
        )

        assert "--effort" not in cmd

    def test_builds_codex_command_with_bypass(self):
        cmd = _build_executor_command("codex", "faça algo", "/tmp/proj", 7)
        assert cmd[:2] == ["codex", "exec"]
        assert not any("model_reasoning_effort" in item for item in cmd)
        assert "--dangerously-bypass-approvals-and-sandbox" in cmd
        assert "--skip-git-repo-check" in cmd
        assert "--json" in cmd
        assert "-C" in cmd
        assert "/tmp/proj" in cmd
        assert "faça algo" == cmd[-1]

    def test_builds_codex_command_with_profile(self, monkeypatch):
        monkeypatch.setenv("FT_CODEX_PROFILE", "symgateway-dev")

        fresh = _build_executor_command("codex", "faça algo", "/tmp/proj", 7)
        resumed = _build_executor_command(
            "codex",
            "continue",
            "/tmp/proj",
            7,
            session_id="019bf8f4-0f2c-7a73-b616-d4163299012b",
            resume_session=True,
        )

        assert fresh[:4] == ["codex", "--profile", "symgateway-dev", "exec"]
        assert resumed[:5] == [
            "codex",
            "--profile",
            "symgateway-dev",
            "exec",
            "resume",
        ]

    def test_chatgpt_auth_bypasses_profile_and_forces_builtin_provider(
        self, monkeypatch
    ):
        monkeypatch.setenv("FT_CODEX_PROFILE", "symgateway-dev")

        cmd = _build_executor_command(
            "codex",
            "gere a imagem",
            "/tmp/proj",
            7,
            codex_auth="chatgpt",
            workflow_id="mvp-builder-fast",
            ft_cycle="cycle-03-mvp-builder-fast",
        )

        assert cmd[:2] == ["codex", "exec"]
        assert "--profile" not in cmd
        assert 'model_provider="openai"' in cmd
        assert 'forced_login_method="chatgpt"' in cmd
        assert not any("symgateway.symlabs.ai" in item for item in cmd)

    @pytest.mark.parametrize("auth", ["api", "symgateway", "chatgpt --yolo"])
    def test_rejects_unknown_codex_auth(self, auth):
        with pytest.raises(ValueError, match="codex_auth"):
            _build_executor_command(
                "codex", "faça algo", "/tmp/proj", 7, codex_auth=auth
            )

    def test_rejects_codex_auth_for_other_executor(self):
        with pytest.raises(ValueError, match="executor Codex"):
            _build_executor_command(
                "claude", "faça algo", "/tmp/proj", 7, codex_auth="chatgpt"
            )

    def test_builds_codex_workflow_override_in_deepest_command_scope(
        self, tmp_path, monkeypatch
    ):
        config_home = tmp_path / "codex"
        config_home.mkdir()
        (config_home / "symgateway-dev.config.toml").write_text(
            'model_provider = "symgateway_openai_dev"\n'
            "[model_providers.symgateway_openai_dev]\n"
            'base_url = "https://symgateway.symlabs.ai/p/openai/v1"\n',
            encoding="utf-8",
        )
        monkeypatch.setenv("CODEX_HOME", str(config_home))
        monkeypatch.setenv("FT_CODEX_PROFILE", "symgateway-dev")

        fresh = _build_executor_command(
            "codex",
            "faça algo",
            "/tmp/proj",
            7,
            workflow_id="mdd",
            ft_cycle="cycle-02-mdd",
        )
        resumed = _build_executor_command(
            "codex",
            "continue",
            "/tmp/proj",
            7,
            session_id="thread-123",
            resume_session=True,
            workflow_id="mdd",
            ft_cycle="cycle-02-mdd",
        )

        override = (
            "model_providers.symgateway_openai_dev.base_url="
            '"https://symgateway.symlabs.ai/p/openai/w/mdd/t/mdd/c/cycle-02-mdd/v1"'
        )
        assert fresh[:6] == [
            "codex",
            "--profile",
            "symgateway-dev",
            "exec",
            "-c",
            override,
        ]
        assert resumed[:7] == [
            "codex",
            "--profile",
            "symgateway-dev",
            "exec",
            "resume",
            "-c",
            override,
        ]

    @pytest.mark.parametrize("workflow", ["bad/workflow", "x" * 65])
    def test_rejects_invalid_workflow(self, workflow):
        with pytest.raises(ValueError, match="workflow_id"):
            _build_executor_command(
                "codex", "faça algo", "/tmp/proj", 7, workflow_id=workflow
            )

    @pytest.mark.parametrize("cycle", ["bad/cycle", "x" * 129])
    def test_rejects_invalid_ft_cycle(self, cycle):
        with pytest.raises(ValueError, match="ft_cycle"):
            _build_executor_command(
                "codex",
                "faça algo",
                "/tmp/proj",
                7,
                workflow_id="mdd",
                ft_cycle=cycle,
            )

    def test_builds_codex_command_with_explicit_reasoning_effort(self, monkeypatch):
        monkeypatch.setenv("FT_CODEX_REASONING_EFFORT", "ultra")

        cmd = _build_executor_command(
            "codex",
            "faça algo",
            "/tmp/proj",
            7,
            model="gpt-5.6-sol",
        )

        assert ["-c", 'model_reasoning_effort="ultra"'] == cmd[2:4]
        assert ["-m", "gpt-5.6-sol"] == cmd[-3:-1]

    def test_builds_codex_command_with_project_effort(self, monkeypatch):
        monkeypatch.delenv("FT_CODEX_REASONING_EFFORT", raising=False)

        cmd = _build_executor_command(
            "codex", "faça algo", "/tmp/proj", 7, effort="max"
        )

        assert ["-c", 'model_reasoning_effort="max"'] == cmd[2:4]

    def test_builds_codex_resume_command_without_unsupported_cwd_flag(self):
        session_id = "019bf8f4-0f2c-7a73-b616-d4163299012b"

        cmd = _build_executor_command(
            "codex",
            "continue",
            "/tmp/proj",
            7,
            session_id=session_id,
            resume_session=True,
        )

        assert cmd[:3] == ["codex", "exec", "resume"]
        assert "-C" not in cmd
        assert cmd[-2:] == [session_id, "continue"]

    def test_delegate_captures_codex_session_and_timings(self, tmp_path, monkeypatch):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        fake = bin_dir / "codex"
        fake.write_text(
            "#!/usr/bin/env python3\n"
            'print(\'{"type":"thread.started","thread_id":"thread-123"}\')\n'
            'print(\'{"type":"item.completed","item":{"type":"agent_message",'
            '"text":"DONE"}}\')\n',
            encoding="utf-8",
        )
        fake.chmod(0o755)
        monkeypatch.setenv(
            "PATH",
            f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        )

        result = delegate_to_llm(
            task="faça",
            project_root=str(tmp_path),
            llm_engine="codex",
        )

        assert result.success
        assert result.session_id == "thread-123"
        assert result.timings["provider_wall_seconds"] >= 0
        assert result.timings["startup_to_first_event_seconds"] >= 0

    def test_codex_idle_monitor_renews_on_real_json_events(self, tmp_path, monkeypatch):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        fake = bin_dir / "codex"
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "import time\n"
            'print(\'{"type":"thread.started","thread_id":"thread-active"}\', flush=True)\n'
            "time.sleep(0.6)\n"
            'print(\'{"type":"item.started","item":{"type":"command_execution"}}\', flush=True)\n'
            "time.sleep(0.6)\n"
            'print(\'{"type":"item.completed","item":{"type":"agent_message",'
            '"text":"DONE"}}\', flush=True)\n',
            encoding="utf-8",
        )
        fake.chmod(0o755)
        monkeypatch.setenv(
            "PATH",
            f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        )
        monkeypatch.setenv("FT_CODEX_IDLE_TIMEOUT", "1")

        result = delegate_to_llm(
            task="faça",
            project_root=str(tmp_path),
            llm_engine="codex",
            llm_timeout_seconds=3,
        )

        assert result.success
        assert result.session_id == "thread-active"

    def test_codex_renews_inactivity_lease_when_source_file_grows(
        self,
        tmp_path,
        monkeypatch,
    ):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        source = tmp_path / "src" / "progress.py"
        fake = bin_dir / "codex"
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "import pathlib, time\n"
            f"path = pathlib.Path({str(source)!r})\n"
            "path.parent.mkdir(parents=True, exist_ok=True)\n"
            "for step in range(4):\n"
            "    path.write_text('value = ' + repr('x' * (step + 1)) + '\\n')\n"
            "    time.sleep(0.6)\n"
            'print(\'{"type":"thread.started","thread_id":"thread-worktree"}\', flush=True)\n'
            'print(\'{"type":"item.completed","item":{"type":"agent_message",'
            '"text":"DONE"}}\', flush=True)\n',
            encoding="utf-8",
        )
        fake.chmod(0o755)
        monkeypatch.setenv(
            "PATH",
            f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        )
        monkeypatch.setenv("FT_CODEX_IDLE_TIMEOUT", "1")
        monkeypatch.setenv("FT_CODEX_IDLE_GRACE", "0")
        monkeypatch.setenv("FT_WORKTREE_PROGRESS_INTERVAL", "1")
        static = _ProcessLiveness(alive=True, process_count=1)

        with patch(
            "ft.engine.delegate._process_liveness_snapshot",
            return_value=static,
        ):
            started = time.monotonic()
            result = delegate_to_llm(
                task="produza código em silêncio",
                project_root=str(tmp_path),
                allowed_paths=["src"],
                llm_engine="codex",
                llm_timeout_seconds=1,
            )
            elapsed = time.monotonic() - started

        assert result.success is True
        assert result.session_id == "thread-worktree"
        assert elapsed >= 2
        assert result.timings["workspace_renewals"] >= 2
        assert source.read_text(encoding="utf-8") == "value = 'xxxx'\n"

    def test_codex_renews_inactivity_lease_on_silent_process_progress(
        self,
        tmp_path,
        monkeypatch,
    ):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        fake = bin_dir / "codex"
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "import time\n"
            "time.sleep(2.4)\n"
            'print(\'{"type":"thread.started","thread_id":"thread-process"}\', flush=True)\n'
            'print(\'{"type":"item.completed","item":{"type":"agent_message",'
            '"text":"DONE"}}\', flush=True)\n',
            encoding="utf-8",
        )
        fake.chmod(0o755)
        monkeypatch.setenv(
            "PATH",
            f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        )
        monkeypatch.setenv("FT_CODEX_IDLE_TIMEOUT", "1")
        monkeypatch.setenv("FT_CODEX_IDLE_GRACE", "0")
        monkeypatch.setenv("FT_WORKTREE_PROGRESS_INTERVAL", "1")
        ticks = 0

        def progressing(_proc):
            nonlocal ticks
            ticks += 2
            return _ProcessLiveness(
                alive=True,
                process_count=1,
                cpu_ticks=ticks,
            )

        with patch(
            "ft.engine.delegate._process_liveness_snapshot",
            side_effect=progressing,
        ):
            result = delegate_to_llm(
                task="progrida silenciosamente no processo",
                project_root=str(tmp_path),
                llm_engine="codex",
                llm_timeout_seconds=1,
            )

        assert result.success is True
        assert result.session_id == "thread-process"
        assert result.timings["process_renewals"] >= 2

    def test_char_only_process_heartbeat_is_not_productive_liveness(self):
        baseline = _ProcessLiveness(
            alive=True,
            process_count=1,
            cpu_ticks=10,
            read_chars=1_000,
            write_chars=500,
        )
        heartbeat = _ProcessLiveness(
            alive=True,
            process_count=1,
            cpu_ticks=10,
            read_chars=2_984,
            write_chars=500,
        )
        actual_io = _ProcessLiveness(
            alive=True,
            process_count=1,
            cpu_ticks=10,
            read_chars=2_984,
            write_chars=500,
            read_bytes=4_096,
        )
        polling = _ProcessLiveness(
            alive=True,
            process_count=1,
            cpu_ticks=11,
            read_chars=2_984,
            write_chars=500,
        )
        active_cpu = _ProcessLiveness(
            alive=True,
            process_count=1,
            cpu_ticks=12,
            read_chars=2_984,
            write_chars=500,
        )

        assert _has_productive_liveness(baseline, heartbeat) is False
        assert _has_productive_liveness(baseline, polling) is False
        assert _has_productive_liveness(baseline, active_cpu) is True
        assert _has_productive_liveness(baseline, actual_io) is True

    def test_workspace_snapshot_detects_same_size_source_edit(self, tmp_path):
        source = tmp_path / "src" / "feature.py"
        source.parent.mkdir()
        source.write_text("value = 1\n", encoding="utf-8")
        paths = _workspace_progress_paths(str(tmp_path), ["src"])
        before = _workspace_progress_snapshot(paths, str(tmp_path))
        time.sleep(0.001)
        source.write_text("value = 2\n", encoding="utf-8")
        after = _workspace_progress_snapshot(paths, str(tmp_path))

        assert before.digest != after.digest
        assert before.source_file_count == after.source_file_count == 1
        assert before.source_bytes == after.source_bytes

    def test_workspace_progress_scope_covers_files_outside_node_write_allowlist(
        self,
        tmp_path,
    ):
        paths = _workspace_progress_paths(str(tmp_path), ["src"])
        before = _workspace_progress_snapshot(paths, str(tmp_path))
        evidence = tmp_path / "docs" / "progress.md"
        evidence.parent.mkdir()
        evidence.write_text("produção observável\n", encoding="utf-8")
        after = _workspace_progress_snapshot(paths, str(tmp_path))

        assert paths == [tmp_path.resolve()]
        assert before.digest != after.digest
        assert after.file_count == before.file_count + 1

    def test_workspace_snapshot_ignores_engine_logs_and_activity_sidecars(
        self, tmp_path
    ):
        paths = _workspace_progress_paths(str(tmp_path), ["docs"])
        before = _workspace_progress_snapshot(paths, str(tmp_path))
        log_dir = tmp_path / "state" / "llm_logs"
        log_dir.mkdir(parents=True)
        (log_dir / "review.jsonl").write_text("stream\n", encoding="utf-8")
        (log_dir / "review.jsonl.activity").write_text("heartbeat\n", encoding="utf-8")
        (tmp_path / "cycle-01_log.md").write_text("engine\n", encoding="utf-8")

        after = _workspace_progress_snapshot(paths, str(tmp_path))

        assert after == before

    def test_opt_in_max_wall_stops_even_a_productive_stream(
        self, tmp_path, monkeypatch
    ):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        fake = bin_dir / "codex"
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "import time\n"
            'print(\'{"type":"thread.started","thread_id":"thread-capped"}\', flush=True)\n'
            "while True:\n"
            '    print(\'{"type":"item.started","item":{"type":"command_execution"}}\', flush=True)\n'
            "    time.sleep(0.2)\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        monkeypatch.setenv(
            "PATH",
            f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        )
        monkeypatch.setenv("FT_CODEX_IDLE_TIMEOUT", "10")
        monkeypatch.setenv("FT_CODEX_MAX_WALL_TIMEOUT", "1")

        result = delegate_to_llm(
            task="execução produtiva porém limitada explicitamente",
            project_root=str(tmp_path),
            llm_engine="codex",
        )

        assert result.success is False
        assert "[MAX_WALL_TIMEOUT]" in result.output

    def test_delegate_keeps_explicit_claude_session_when_stream_omits_id(
        self,
        tmp_path,
        monkeypatch,
    ):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        fake = bin_dir / "claude"
        fake.write_text(
            '#!/usr/bin/env python3\nprint(\'{"type":"result","result":"DONE"}\')\n',
            encoding="utf-8",
        )
        fake.chmod(0o755)
        monkeypatch.setenv(
            "PATH",
            f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        )
        session_id = "4f5b71b2-f632-4f07-8ee7-4e8fb9946c39"

        result = delegate_to_llm(
            task="faça",
            project_root=str(tmp_path),
            llm_engine="claude",
            llm_session_id=session_id,
        )

        assert result.success
        assert result.session_id == session_id

    def test_codex_env_effort_overrides_project_effort(self, monkeypatch):
        monkeypatch.setenv("FT_CODEX_REASONING_EFFORT", "ultra")

        cmd = _build_executor_command(
            "codex", "faça algo", "/tmp/proj", 7, effort="max"
        )

        assert ["-c", 'model_reasoning_effort="ultra"'] == cmd[2:4]
        assert _executor_timeout_seconds("codex", "max") == 3600

    def test_rejects_invalid_project_effort(self):
        with pytest.raises(ValueError, match="llm_effort"):
            _build_executor_command(
                "claude", "faça algo", "/tmp/proj", 7, effort="high;rm"
            )

    def test_rejects_invalid_codex_reasoning_effort(self, monkeypatch):
        monkeypatch.setenv("FT_CODEX_REASONING_EFFORT", 'ultra" --sandbox read-only')

        with pytest.raises(ValueError, match="FT_CODEX_REASONING_EFFORT"):
            _build_executor_command("codex", "faça algo", "/tmp/proj", 7)

    @pytest.mark.parametrize("profile", ["symgateway-dev --yolo", "--yolo"])
    def test_rejects_invalid_codex_profile(self, monkeypatch, profile):
        monkeypatch.setenv("FT_CODEX_PROFILE", profile)

        with pytest.raises(ValueError, match="FT_CODEX_PROFILE"):
            _build_executor_command("codex", "faça algo", "/tmp/proj", 7)

    def test_builds_opencode_command_with_default_model(self):
        cmd = _build_executor_command("opencode", "faça algo", "/tmp/proj", 7)
        assert cmd == [
            "opencode",
            "run",
            "--dir",
            "/tmp/proj",
            "-m",
            DEFAULT_OPENCODE_MODEL,
            "--auto",
            "--pure",
            "faça algo",
        ]

    def test_builds_opencode_command_with_model_override(self):
        cmd = _build_executor_command(
            "opencode",
            "faça algo",
            "/tmp/proj",
            7,
            model="anthropic/claude-sonnet-4-5",
        )
        assert cmd == [
            "opencode",
            "run",
            "--dir",
            "/tmp/proj",
            "-m",
            "anthropic/claude-sonnet-4-5",
            "--auto",
            "--pure",
            "faça algo",
        ]

    def test_builds_opencode_command_with_variant_override(self, monkeypatch):
        monkeypatch.setenv("FT_OPENCODE_VARIANT", "low")

        cmd = _build_executor_command("opencode", "faça algo", "/tmp/proj", 7)

        assert ["--variant", "low"] == cmd[
            cmd.index("--variant") : cmd.index("--variant") + 2
        ]

    def test_builds_opencode_command_with_project_effort(self, monkeypatch):
        monkeypatch.delenv("FT_OPENCODE_VARIANT", raising=False)

        cmd = _build_executor_command(
            "opencode", "faça algo", "/tmp/proj", 7, effort="high"
        )

        assert ["--variant", "high"] == cmd[
            cmd.index("--variant") : cmd.index("--variant") + 2
        ]

    def test_opencode_allows_explicit_variant_named_none(self, monkeypatch):
        monkeypatch.delenv("FT_OPENCODE_VARIANT", raising=False)

        cmd = _build_executor_command(
            "opencode", "faça algo", "/tmp/proj", 7, effort="none"
        )

        assert ["--variant", "none"] == cmd[
            cmd.index("--variant") : cmd.index("--variant") + 2
        ]

    def test_builds_opencode_command_allows_disabling_pure_and_variant(
        self, monkeypatch
    ):
        monkeypatch.setenv("FT_OPENCODE_AUTO", "0")
        monkeypatch.setenv("FT_OPENCODE_PURE", "0")
        monkeypatch.setenv("FT_OPENCODE_VARIANT", "off")

        cmd = _build_executor_command("opencode", "faça algo", "/tmp/proj", 7)

        assert "--auto" not in cmd
        assert "--pure" not in cmd
        assert "--variant" not in cmd

    def test_builds_opencode_command_with_debug_flags(self, monkeypatch):
        monkeypatch.setenv("FT_OPENCODE_DEBUG", "1")

        cmd = _build_executor_command("opencode", "faça algo", "/tmp/proj", 7)

        assert "--print-logs" in cmd
        assert ["--log-level", "DEBUG"] == cmd[
            cmd.index("--log-level") : cmd.index("--log-level") + 2
        ]
        assert "--thinking" not in cmd
        assert cmd[-1] == "faça algo"

    def test_builds_opencode_command_with_custom_log_level(self, monkeypatch):
        monkeypatch.setenv("FT_OPENCODE_PRINT_LOGS", "1")
        monkeypatch.setenv("FT_OPENCODE_LOG_LEVEL", "INFO")

        cmd = _build_executor_command("opencode", "faça algo", "/tmp/proj", 7)

        assert "--print-logs" in cmd
        assert ["--log-level", "INFO"] == cmd[
            cmd.index("--log-level") : cmd.index("--log-level") + 2
        ]
        assert "--thinking" not in cmd
        assert cmd[-1] == "faça algo"

    def test_builds_opencode_command_with_thinking_flag(self, monkeypatch):
        monkeypatch.setenv("FT_OPENCODE_THINKING", "1")

        cmd = _build_executor_command("opencode", "faça algo", "/tmp/proj", 7)

        assert "--thinking" in cmd
        assert cmd[-1] == "faça algo"

    def test_invalid_engine_raises(self):
        with pytest.raises(ValueError, match="Executor LLM desconhecido"):
            _build_executor_command("unknown_engine_xyz", "x", "/tmp/proj", 3)

    def test_file_bundle_raw_inherits_global_productivity_supervision(self, tmp_path):
        supervised = DelegateResult(
            False,
            "[INACTIVITY_TIMEOUT] worktree e processo estagnados",
            [],
            [],
        )

        with patch(
            "ft.engine.delegate.delegate_to_llm",
            return_value=supervised,
        ) as delegated:
            result = delegate_opencode_file_bundle_raw(
                '<ft_file path="docs/out.md">hello</ft_file>',
                str(tmp_path),
                allowed_paths=["docs/out.md"],
            )

        assert result.success is False
        assert "[INACTIVITY_TIMEOUT]" in result.output
        assert result.files_created == []
        assert delegated.call_args.kwargs["llm_engine"] == "opencode"
        assert delegated.call_args.kwargs["raw_output"] is True
        assert delegated.call_args.kwargs["opencode_restrict_tools"] is True

    def test_opencode_env_enforces_runtime_config(self):
        env = _executor_env(
            "opencode",
            {
                "OPENCODE_CONFIG_CONTENT": json.dumps(
                    {
                        "permission": {"bash": "ask"},
                        "compaction": {"reserved": 2000},
                        "theme": "system",
                    }
                )
            },
        )

        config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
        assert config["permission"]["bash"] == "ask"
        assert config["permission"]["external_directory"] == "deny"
        assert env["CI"] == "1"
        assert env["COREPACK_ENABLE_DOWNLOAD_PROMPT"] == "0"
        assert env["npm_config_yes"] == "true"
        assert env["NPM_CONFIG_YES"] == "true"
        assert env["npm_config_audit"] == "false"
        assert env["npm_config_fund"] == "false"
        assert config["compaction"] == {
            "auto": True,
            "prune": True,
            "reserved": 10000,
        }
        assert config["theme"] == "system"

    def test_appends_opencode_runtime_diagnostics_to_step_log(self, tmp_path):
        runtime = tmp_path / "runtime"
        internal_log = runtime / "data" / "opencode" / "log" / "opencode.log"
        internal_log.parent.mkdir(parents=True)
        internal_log.write_text(
            "timestamp=now level=ERROR message=boom\n", encoding="utf-8"
        )
        step_log = tmp_path / "state" / "llm_logs" / "node.log"
        step_log.parent.mkdir(parents=True)
        step_log.write_text("Preamble\n", encoding="utf-8")

        _append_opencode_runtime_diagnostics(runtime, str(step_log))

        content = step_log.read_text(encoding="utf-8")
        assert "OPENCODE INTERNAL opencode.log" in content
        assert "message=boom" in content

    def test_opencode_env_can_deny_large_doc_reads(self):
        env = _executor_env(
            "opencode",
            {},
            opencode_deny_read_paths=["docs/PRD.md"],
            project_root="/tmp/project",
            opencode_restrict_tools=True,
            opencode_steps=8,
        )

        config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
        read_rules = config["permission"]["read"]
        assert read_rules["*"] == "allow"
        assert read_rules["*.env"] == "deny"
        assert read_rules["docs/PRD.md"] == "deny"
        assert read_rules["*/docs/PRD.md"] == "deny"
        assert read_rules["/tmp/project/docs/PRD.md"] == "deny"
        assert config["permission"]["bash"] == "deny"
        assert config["permission"]["glob"] == "deny"
        assert config["permission"]["grep"] == "deny"
        assert config["permission"]["list"] == "deny"
        assert config["agent"]["build"]["steps"] == 8
        assert config["agent"]["build"]["maxSteps"] == 8

    def test_opencode_env_text_only_denies_tools(self):
        env = _executor_env("opencode", {}, opencode_text_only=True)

        permission = json.loads(env["OPENCODE_CONFIG_CONTENT"])["permission"]
        assert permission["*"] == "deny"
        for tool in ("bash", "glob", "grep", "list", "read", "edit"):
            assert permission[tool] == "deny"

    def test_opencode_capture_command_uses_json_without_debug_logs(self):
        cmd = [
            "opencode",
            "run",
            "--dir",
            "/tmp/project",
            "-m",
            DEFAULT_OPENCODE_MODEL,
            "--print-logs",
            "--log-level",
            "DEBUG",
            "prompt",
        ]

        captured = _opencode_capture_command(cmd)

        assert "--print-logs" not in captured
        assert "--log-level" not in captured
        assert captured[-3:] == ["--format", "json", "prompt"]

    def test_extracts_opencode_json_text_for_capture(self):
        raw = "\n".join(
            [
                '{"type":"step_start","part":{"type":"step-start"}}',
                '{"type":"text","part":{"type":"text","text":"# Doc\\nbody\\n[tool_calls] (None)"}}',
            ]
        )

        extracted = _extract_opencode_json_text(raw)

        assert _clean_opencode_capture_text(extracted) == "# Doc\nbody"

    def test_identifies_opencode_internal_log_lines(self):
        assert _is_opencode_internal_log_line(
            'timestamp=2026-07-08T16:47:44.775Z level=INFO run=0b245190 message="llm runtime selected"'
        )
        assert not _is_opencode_internal_log_line("$ ls -la project/frontend")
        assert not _is_opencode_internal_log_line("→ Read docs/PRD.md")

    def test_opencode_capture_cleaner_removes_fence_and_trailing_blocked_note(self):
        text = (
            "```markdown\n"
            "# Doc\n"
            "\n"
            "body\n"
            "```\n"
            "\n"
            "BLOCKED: nao posso usar ferramenta de escrita"
        )

        assert _clean_opencode_capture_text(text) == "# Doc\n\nbody"

    def test_opencode_capture_cleaner_preserves_blocked_only_response(self):
        assert (
            _clean_opencode_capture_text("BLOCKED: sem contexto")
            == "BLOCKED: sem contexto"
        )

    def test_opencode_capture_cleaner_removes_operational_prelude_before_heading(self):
        text = "I need to create the task list first.\n\n# Task List\n\n- item\n"

        assert _clean_opencode_capture_text(text) == "# Task List\n\n- item"

    def test_opencode_env_can_deny_edit_tools_for_code_nodes(self):
        env = _executor_env(
            "opencode",
            {},
            opencode_deny_edit_tools=True,
        )

        config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
        assert config["permission"]["edit"] == "deny"
        assert "bash" not in config["permission"]

    def test_opencode_env_announces_default_model_context_limit(self):
        env = _executor_env("opencode", {}, opencode_model=DEFAULT_OPENCODE_MODEL)

        config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
        limit = config["provider"]["pgx"]["models"]["zai-org_glm-4.7-flash"]["limit"]
        assert limit == {
            "context": DEFAULT_OPENCODE_CONTEXT_LIMIT,
            "output": DEFAULT_OPENCODE_OUTPUT_LIMIT,
        }

    def test_opencode_env_can_override_context_limit_for_custom_model(
        self, monkeypatch
    ):
        monkeypatch.setenv("FT_OPENCODE_CONTEXT_LIMIT", "123456")
        monkeypatch.setenv("FT_OPENCODE_OUTPUT_LIMIT", "8192")

        env = _executor_env(
            "opencode",
            {
                "OPENCODE_CONFIG_CONTENT": json.dumps(
                    {
                        "provider": {
                            "pgx": {
                                "options": {"baseURL": "http://example.test/v1"},
                                "models": {
                                    "openai/gpt-oss-20b": {"name": "GPT-OSS 20B"}
                                },
                            }
                        }
                    }
                )
            },
            opencode_model="pgx/openai/gpt-oss-20b",
        )

        config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
        provider = config["provider"]["pgx"]
        model = provider["models"]["openai/gpt-oss-20b"]
        assert provider["options"]["baseURL"] == "http://example.test/v1"
        assert model["name"] == "GPT-OSS 20B"
        assert model["limit"] == {"context": 123456, "output": 8192}

    def test_opencode_env_can_set_provider_timeouts(self, monkeypatch):
        monkeypatch.setenv("FT_OPENCODE_PROVIDER_TIMEOUT", "900000")
        monkeypatch.setenv("FT_OPENCODE_CHUNK_TIMEOUT", "180000")
        monkeypatch.setenv("FT_OPENCODE_HEADER_TIMEOUT", "120000")

        env = _executor_env(
            "opencode",
            {
                "OPENCODE_CONFIG_CONTENT": json.dumps(
                    {
                        "provider": {
                            "pgx": {
                                "options": {"baseURL": "http://example.test/v1"},
                                "models": {
                                    "zai-org_glm-4.7-flash": {"name": "GLM 4.7 Flash"}
                                },
                            }
                        }
                    }
                )
            },
            opencode_model=DEFAULT_OPENCODE_MODEL,
        )

        config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
        provider = config["provider"]["pgx"]
        assert provider["options"] == {
            "baseURL": "http://example.test/v1",
            "timeout": 900000,
            "chunkTimeout": 180000,
            "headerTimeout": 120000,
        }
        assert provider["models"]["zai-org_glm-4.7-flash"]["name"] == "GLM 4.7 Flash"

    def test_non_opencode_env_is_unchanged(self):
        env = _executor_env("claude", {"OPENCODE_CONFIG_CONTENT": "{}"})
        assert env["OPENCODE_CONFIG_CONTENT"] == "{}"

    def test_claude_env_routes_gateway_and_loads_main_worktree_settings(self, tmp_path):
        main = tmp_path / "main"
        linked = tmp_path / "linked"
        main.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=main, check=True)
        subprocess.run(
            ["git", "config", "user.email", "tests@example.test"],
            cwd=main,
            check=True,
        )
        subprocess.run(["git", "config", "user.name", "Tests"], cwd=main, check=True)
        (main / "README.md").write_text("test\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=main, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=main, check=True)
        subprocess.run(
            ["git", "worktree", "add", "-q", "-b", "cycle", str(linked)],
            cwd=main,
            check=True,
        )
        settings_dir = main / ".claude"
        settings_dir.mkdir()
        (settings_dir / "settings.local.json").write_text(
            json.dumps(
                {
                    "env": {
                        "ANTHROPIC_BASE_URL": (
                            "https://symgateway.symlabs.ai/p/anthropic-max/s/test"
                        ),
                        "ANTHROPIC_API_KEY": "local-test-key",
                    }
                }
            ),
            encoding="utf-8",
        )

        env = _executor_env(
            "claude", {}, project_root=str(linked), workflow_id="innovation"
        )

        assert env["ANTHROPIC_BASE_URL"] == (
            "https://symgateway.symlabs.ai/p/anthropic-max/s/test/w/innovation"
        )
        assert env["ANTHROPIC_API_KEY"] == "local-test-key"

    def test_claude_env_does_not_reroute_another_provider(self):
        env = _executor_env(
            "claude",
            {"ANTHROPIC_BASE_URL": "https://api.anthropic.com"},
            workflow_id="innovation",
        )
        assert env["ANTHROPIC_BASE_URL"] == "https://api.anthropic.com"

    def test_opencode_sandbox_prepares_exact_file_and_dir_mounts(self, tmp_path):
        mounts = _prepare_opencode_sandbox_mounts(
            str(tmp_path),
            ["docs/api_contract.md", "project/frontend/"],
        )

        by_path = {
            mount.path.relative_to(tmp_path).as_posix(): mount for mount in mounts
        }
        assert set(by_path) == {"docs/api_contract.md", "project/frontend"}
        assert by_path["docs/api_contract.md"].is_file is True
        assert by_path["docs/api_contract.md"].placeholder is True
        assert by_path["project/frontend"].is_file is False
        assert (tmp_path / "docs/api_contract.md").exists()
        assert (tmp_path / "project/frontend").is_dir()

    def test_opencode_sandbox_ignores_paths_outside_project(self, tmp_path):
        outside = tmp_path.parent / "outside.md"
        mounts = _prepare_opencode_sandbox_mounts(str(tmp_path), [str(outside)])
        assert mounts == []
        assert not outside.exists()

    def test_opencode_sandbox_wraps_command_with_bwrap(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "ft.engine.delegate.shutil.which", lambda name: "/usr/bin/bwrap"
        )
        (tmp_path / "state").mkdir()

        cmd, mounts = _wrap_opencode_sandbox_command(
            ["opencode", "run", "prompt"],
            project_root=str(tmp_path),
            allowed_paths=["docs/out.md"],
            runtime_dir=str(tmp_path / "runtime"),
        )

        assert cmd[:7] == [
            "/usr/bin/bwrap",
            "--ro-bind",
            "/",
            "/",
            "--dev-bind",
            "/dev",
            "/dev",
        ]
        assert [
            "--bind",
            str(tmp_path / "docs/out.md"),
            str(tmp_path / "docs/out.md"),
        ] in [cmd[i : i + 3] for i in range(len(cmd) - 2)]
        assert [
            "--ro-bind",
            str(tmp_path / "runtime" / "hidden-state"),
            str(tmp_path / "state"),
        ] in [cmd[i : i + 3] for i in range(len(cmd) - 2)]
        assert cmd[-3:] == ["opencode", "run", "prompt"]
        assert [mount.path for mount in mounts] == [tmp_path / "docs/out.md"]

    def test_delegate_opencode_code_node_materializes_generated_file_bundle(
        self, tmp_path, monkeypatch
    ):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        generated = (
            '<ft_file path="project/frontend/package.json">\n'
            '{"scripts":{"build":"echo ok"}}\n'
            "</ft_file>\n"
        )
        fake = bin_dir / "opencode"
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "import json\n"
            f"print(json.dumps({{'type':'text','part':{{'type':'text','text':{generated!r}}}}}))\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
        monkeypatch.setenv("FT_OPENCODE_SANDBOX", "0")
        monkeypatch.setenv("FT_OPENCODE_BUNDLE_MODE", "1")

        result = delegate_to_llm(
            task="crie scaffold",
            project_root=str(tmp_path),
            allowed_paths=["project"],
            llm_engine="opencode",
            opencode_deny_edit_tools=True,
            log_path=str(tmp_path / "llm.log"),
        )

        assert result.success is True
        assert "File bundle gerado pelo OpenCode" in result.output
        assert (tmp_path / "project/frontend/package.json").exists()

    def test_delegate_opencode_code_node_uses_tool_mode_by_default(
        self, tmp_path, monkeypatch
    ):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        prompt_path = tmp_path / "prompt.txt"
        fake = bin_dir / "opencode"
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "import pathlib, sys\n"
            f"pathlib.Path({str(prompt_path)!r}).write_text(sys.argv[-1], encoding='utf-8')\n"
            "print('DONE')\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
        monkeypatch.setenv("FT_OPENCODE_SANDBOX", "0")

        result = delegate_to_llm(
            task="crie scaffold",
            project_root=str(tmp_path),
            allowed_paths=["project"],
            llm_engine="opencode",
            opencode_deny_edit_tools=True,
            log_path=str(tmp_path / "llm.log"),
        )

        prompt = prompt_path.read_text(encoding="utf-8")
        assert result.success is True
        assert "OBRIGATORIO: antes de dizer DONE, use Bash" in prompt
        assert "Responda SOMENTE com blocos XML" not in prompt
        assert (
            "NAO use `git checkout`, `git reset`, `git restore`, `git clean` ou `git revert`"
            in prompt
        )
        assert "NUNCA encerre, mate ou reinicie processos" in prompt

    def test_delegate_opencode_native_write_prompt_uses_path_schema(
        self, tmp_path, monkeypatch
    ):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        prompt_path = tmp_path / "prompt.txt"
        fake = bin_dir / "opencode"
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "import pathlib, sys\n"
            f"pathlib.Path({str(prompt_path)!r}).write_text(sys.argv[-1], encoding='utf-8')\n"
            "print('DONE')\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
        monkeypatch.setenv("FT_OPENCODE_SANDBOX", "0")

        result = delegate_to_llm(
            task="crie scaffold",
            project_root=str(tmp_path),
            allowed_paths=["project"],
            llm_engine="opencode",
            log_path=str(tmp_path / "llm.log"),
        )

        prompt = prompt_path.read_text(encoding="utf-8")
        assert result.success is True
        assert "campos `path` e `content`" in prompt
        assert "campos `path`, `oldString`, `newString`" in prompt
        assert "nunca use `filePath`" in prompt

    def test_llm_inactivity_window_stops_a_stagnant_executor(
        self, tmp_path, monkeypatch
    ):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        fake = bin_dir / "opencode"
        fake.write_text(
            "#!/bin/sh\nsleep 5\nprintf 'DONE\\n'\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
        monkeypatch.setenv("FT_OPENCODE_SANDBOX", "0")
        monkeypatch.setenv("FT_OPENCODE_IDLE_GRACE", "0")
        monkeypatch.setenv("FT_OPENCODE_IDLE_RETRIES", "0")

        started = time.monotonic()
        result = delegate_to_llm(
            task="demora",
            project_root=str(tmp_path),
            llm_engine="opencode",
            llm_timeout_seconds=1,
        )
        elapsed = time.monotonic() - started

        assert result.success is False
        assert "[INACTIVITY_TIMEOUT]" in result.output
        assert elapsed < 4

    @pytest.mark.skipif(
        not sys.platform.startswith("linux"), reason="requer subreaper Linux"
    )
    def test_llm_timeout_reaps_detached_executor_writer(self, tmp_path, monkeypatch):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        marker = tmp_path / "late-deadline-marker"
        fake = bin_dir / "opencode"
        fake.write_text(
            "#!/bin/sh\n"
            "setsid sh -c \"trap '' TERM HUP; sleep 5; "
            f'printf late > {str(marker)!r}" '
            "</dev/null >/dev/null 2>&1 &\n"
            "sleep 10\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
        monkeypatch.setenv("FT_OPENCODE_SANDBOX", "0")
        monkeypatch.setenv("FT_OPENCODE_IDLE_GRACE", "0")
        monkeypatch.setenv("FT_OPENCODE_IDLE_RETRIES", "0")

        started = time.monotonic()
        result = delegate_to_llm(
            task="demora com filho destacado",
            project_root=str(tmp_path),
            llm_engine="opencode",
            llm_timeout_seconds=1,
        )
        elapsed = time.monotonic() - started
        time.sleep(0.7)

        assert result.success is False
        assert "[INACTIVITY_TIMEOUT]" in result.output
        assert elapsed < 4
        assert not marker.exists()

    def test_opt_in_max_wall_includes_rate_limit_backoff_and_prevents_retry(
        self, tmp_path, monkeypatch
    ):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        count = tmp_path / "calls"
        fake = bin_dir / "opencode"
        fake.write_text(
            "#!/bin/sh\n"
            f"n=$(cat {str(count)!r} 2>/dev/null || printf 0)\n"
            f"printf '%s' $((n + 1)) > {str(count)!r}\n"
            "printf 'rate limit exceeded\\n'\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
        monkeypatch.setenv("FT_OPENCODE_SANDBOX", "0")
        monkeypatch.setenv("FT_RATE_LIMIT_BACKOFF", "60")
        monkeypatch.setenv("FT_OPENCODE_MAX_WALL_TIMEOUT", "1")

        started = time.monotonic()
        result = delegate_to_llm(
            task="rate limited",
            project_root=str(tmp_path),
            llm_engine="opencode",
            llm_timeout_seconds=1,
        )
        elapsed = time.monotonic() - started

        assert result.success is False
        assert result.rate_limited is False
        assert "[MAX_WALL_TIMEOUT]" in result.output
        assert count.read_text(encoding="utf-8") == "1"
        assert elapsed < 3

    def test_opencode_script_receives_only_remaining_opt_in_max_wall(
        self, tmp_path, monkeypatch
    ):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        fake = bin_dir / "opencode"
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "print('set -euo pipefail')\n"
            "print(\"printf 'ok\\\\n'\")\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
        monkeypatch.setenv("FT_OPENCODE_SANDBOX", "0")
        monkeypatch.setenv("FT_OPENCODE_SCRIPT_MODE", "1")
        monkeypatch.delenv("FT_OPENCODE_BUNDLE_MODE", raising=False)
        monkeypatch.setenv("FT_OPENCODE_MAX_WALL_TIMEOUT", "10")

        with (
            patch(
                "ft.engine.delegate.time.monotonic",
                side_effect=[0.0, 2.0, 3.0],
            ),
            patch(
                "ft.engine.delegate._wait_for_process",
                side_effect=lambda proc, **_kwargs: (proc.wait(), False),
            ),
            patch(
                "ft.engine.delegate._run_opencode_script",
                return_value=(True, "ok\n"),
            ) as script_runner,
        ):
            result = delegate_to_llm(
                task="gere script",
                project_root=str(tmp_path),
                llm_engine="opencode",
                opencode_deny_edit_tools=True,
                llm_timeout_seconds=10,
            )

        assert result.success is True
        assert script_runner.call_args.kwargs["timeout_seconds"] == 7.0

    def test_opencode_script_is_not_started_after_opt_in_max_wall_expires(
        self, tmp_path, monkeypatch
    ):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        fake = bin_dir / "opencode"
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "print('set -euo pipefail')\n"
            "print(\"printf 'ok\\\\n'\")\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
        monkeypatch.setenv("FT_OPENCODE_SANDBOX", "0")
        monkeypatch.setenv("FT_OPENCODE_SCRIPT_MODE", "1")
        monkeypatch.delenv("FT_OPENCODE_BUNDLE_MODE", raising=False)
        monkeypatch.setenv("FT_OPENCODE_MAX_WALL_TIMEOUT", "10")

        with (
            patch(
                "ft.engine.delegate.time.monotonic",
                side_effect=[0.0, 1.0, 11.0],
            ),
            patch(
                "ft.engine.delegate._wait_for_process",
                side_effect=lambda proc, **_kwargs: (proc.wait(), False),
            ),
            patch("ft.engine.delegate._run_opencode_script") as script_runner,
        ):
            result = delegate_to_llm(
                task="gere script",
                project_root=str(tmp_path),
                llm_engine="opencode",
                opencode_deny_edit_tools=True,
                llm_timeout_seconds=10,
            )

        assert result.success is False
        assert "[MAX_WALL_TIMEOUT]" in result.output
        script_runner.assert_not_called()

    def test_opencode_script_without_max_wall_keeps_script_timeout(self, tmp_path):
        with patch("ft.engine.delegate.subprocess.Popen") as popen:
            process = popen.return_value
            process.communicate.return_value = ("ok\n", "")
            process.returncode = 0
            ok, output = _run_opencode_script(
                "set -euo pipefail\nprintf 'ok\\n'\n",
                project_root=str(tmp_path),
                allowed_paths=None,
                env={},
                log_path=None,
                runtime_dir=None,
            )

        assert ok is True
        assert output == "ok\n"
        process.communicate.assert_called_once_with(timeout=1800.0)

    @pytest.mark.skipif(
        not sys.platform.startswith("linux"), reason="requer subreaper Linux"
    )
    def test_opencode_script_reaps_detached_writer_before_success(self, tmp_path):
        marker = tmp_path / "late-script-marker"
        script = (
            "setsid sh -c \"trap '' TERM; sleep 0.3; "
            f'printf late > {str(marker)!r}" '
            "</dev/null >/dev/null 2>&1 &\n"
            "printf 'ok\\n'\n"
        )

        ok, output = _run_opencode_script(
            script,
            project_root=str(tmp_path),
            allowed_paths=None,
            env=os.environ.copy(),
            log_path=None,
            runtime_dir=None,
            timeout_seconds=2,
        )
        time.sleep(0.5)

        assert ok is True
        assert output == "ok\n"
        assert not marker.exists()

    @pytest.mark.skipif(
        not sys.platform.startswith("linux"), reason="requer subreaper Linux"
    )
    def test_opencode_script_timeout_reaps_detached_writer_with_bounded_cleanup(
        self, tmp_path
    ):
        marker = tmp_path / "late-timeout-marker"
        script = (
            "setsid sh -c \"trap '' TERM; sleep 1; "
            f'printf late > {str(marker)!r}" '
            "</dev/null >/dev/null 2>&1 &\n"
            "sleep 10\n"
        )

        started = time.monotonic()
        ok, output = _run_opencode_script(
            script,
            project_root=str(tmp_path),
            allowed_paths=None,
            env=os.environ.copy(),
            log_path=None,
            runtime_dir=None,
            timeout_seconds=0.2,
        )
        elapsed = time.monotonic() - started
        time.sleep(1.1)

        assert ok is False
        assert "[TIMEOUT]" in output
        assert elapsed < 4
        assert not marker.exists()

    @pytest.mark.skipif(
        not sys.platform.startswith("linux"), reason="requer subreaper Linux"
    )
    def test_delegate_reaps_detached_executor_writer_before_success(
        self, tmp_path, monkeypatch
    ):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        marker = tmp_path / "late-executor-marker"
        fake = bin_dir / "opencode"
        fake.write_text(
            "#!/bin/sh\n"
            "setsid sh -c \"trap '' TERM; sleep 0.3; "
            f'printf late > {str(marker)!r}" '
            "</dev/null >/dev/null 2>&1 &\n"
            "printf 'DONE\\n'\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
        monkeypatch.setenv("FT_OPENCODE_SANDBOX", "0")

        result = delegate_to_llm(
            task="mudança focal",
            project_root=str(tmp_path),
            llm_engine="opencode",
            llm_timeout_seconds=2,
        )
        time.sleep(0.5)

        assert result.success is True
        assert not marker.exists()

    @pytest.mark.skipif(
        not sys.platform.startswith("linux"), reason="requer subreaper Linux"
    )
    def test_transcript_reconciliation_reaps_detached_executor_writer(self, tmp_path):
        marker = tmp_path / "late-reconcile-marker"
        fake = tmp_path / "fake-claude.py"
        fake.write_text(
            "import json, subprocess, time\n"
            f"print(json.dumps({{'type': 'system', 'subtype': 'init', "
            f"'session_id': 'sid', 'cwd': {str(tmp_path)!r}}}), flush=True)\n"
            "subprocess.Popen([\n"
            "    'setsid', 'sh', '-c',\n"
            f"    \"trap '' TERM; sleep 1.3; printf late > {str(marker)!r}\",\n"
            "], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, "
            "stderr=subprocess.DEVNULL)\n"
            "time.sleep(10)\n",
            encoding="utf-8",
        )
        proc = subprocess.Popen(
            _supervised_command([sys.executable, str(fake)]),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )

        with (
            patch("ft.engine.delegate._STALL_RECONCILE_SECS", 0),
            patch(
                "ft.engine.delegate._transcript_terminal_output", return_value="DONE"
            ),
        ):
            output = _stream_process_output(proc, "claude", stream_prefix="test")
        time.sleep(0.5)

        assert "DONE" in output
        assert not marker.exists()

    def test_delegate_opencode_file_bundle_tolerates_extra_text(
        self, tmp_path, monkeypatch
    ):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        generated = (
            "I will create the scaffold now.\n"
            '<ft_file path="project/frontend/package.json">\n'
            '{"scripts":{"build":"echo ok"}}\n'
            "</ft_file>\n"
            "The file is ready.\n"
        )
        fake = bin_dir / "opencode"
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "import json\n"
            f"print(json.dumps({{'type':'text','part':{{'type':'text','text':{generated!r}}}}}))\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
        monkeypatch.setenv("FT_OPENCODE_SANDBOX", "0")
        monkeypatch.setenv("FT_OPENCODE_BUNDLE_MODE", "1")

        result = delegate_to_llm(
            task="crie scaffold",
            project_root=str(tmp_path),
            allowed_paths=["project"],
            llm_engine="opencode",
            opencode_deny_edit_tools=True,
            log_path=str(tmp_path / "llm.log"),
        )

        assert result.success is True
        assert "File bundle gerado pelo OpenCode" in result.output
        assert (tmp_path / "project/frontend/package.json").exists()

    def test_delegate_opencode_file_bundle_prefixes_frontend_orphan_paths(
        self, tmp_path, monkeypatch
    ):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        generated = (
            '<ft_file path="project/frontend/package.json">\n'
            '{"scripts":{"build":"node scripts/build.js"}}\n'
            "</ft_file>\n"
            '<ft_file path="scripts/build.js">\n'
            "process.exit(0)\n"
            "</ft_file>\n"
        )
        fake = bin_dir / "opencode"
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "import json\n"
            f"print(json.dumps({{'type':'text','part':{{'type':'text','text':{generated!r}}}}}))\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
        monkeypatch.setenv("FT_OPENCODE_SANDBOX", "0")
        monkeypatch.setenv("FT_OPENCODE_BUNDLE_MODE", "1")

        result = delegate_to_llm(
            task="crie scaffold",
            project_root=str(tmp_path),
            allowed_paths=["project"],
            llm_engine="opencode",
            opencode_deny_edit_tools=True,
            log_path=str(tmp_path / "llm.log"),
        )

        assert result.success is True
        assert (tmp_path / "project/frontend/scripts/build.js").read_text(
            encoding="utf-8"
        ) == "process.exit(0)\n"
        assert "project/frontend/scripts/build.js" in result.output

    def test_delegate_opencode_file_bundle_normalizes_frontend_alias_paths(
        self, tmp_path, monkeypatch
    ):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        generated = (
            '<ft_file path="project/frontend/package.json">\n'
            '{"scripts":{"build":"node scripts/build.mjs"}}\n'
            "</ft_file>\n"
            '<ft_file path="package/frontend/scripts/build.mjs">\n'
            "process.exit(0)\n"
            "</ft_file>\n"
        )
        fake = bin_dir / "opencode"
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "import json\n"
            f"print(json.dumps({{'type':'text','part':{{'type':'text','text':{generated!r}}}}}))\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
        monkeypatch.setenv("FT_OPENCODE_SANDBOX", "0")
        monkeypatch.setenv("FT_OPENCODE_BUNDLE_MODE", "1")

        result = delegate_to_llm(
            task="crie scaffold",
            project_root=str(tmp_path),
            allowed_paths=["project"],
            llm_engine="opencode",
            opencode_deny_edit_tools=True,
            log_path=str(tmp_path / "llm.log"),
        )

        assert result.success is True
        assert (tmp_path / "project/frontend/scripts/build.mjs").read_text(
            encoding="utf-8"
        ) == "process.exit(0)\n"
        assert "project/frontend/scripts/build.mjs" in result.output

    def test_delegate_opencode_file_bundle_preserves_dotfile_paths(
        self, tmp_path, monkeypatch
    ):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        generated = '<ft_file path=".build_ok">\nready\n</ft_file>\n'
        fake = bin_dir / "opencode"
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "import json\n"
            f"print(json.dumps({{'type':'text','part':{{'type':'text','text':{generated!r}}}}}))\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
        monkeypatch.setenv("FT_OPENCODE_SANDBOX", "0")
        monkeypatch.setenv("FT_OPENCODE_BUNDLE_MODE", "1")

        result = delegate_to_llm(
            task="marque build",
            project_root=str(tmp_path),
            allowed_paths=[".build_ok"],
            llm_engine="opencode",
            opencode_deny_edit_tools=True,
            log_path=str(tmp_path / "llm.log"),
        )

        assert result.success is True
        assert (tmp_path / ".build_ok").read_text(encoding="utf-8") == "ready\n"
        assert "- .build_ok" in result.output

    def test_wait_for_process_returns_success_when_outputs_exist(self, tmp_path):
        output = tmp_path / "docs/out.md"
        output.parent.mkdir()
        output.write_text("# pronto\n")
        proc = subprocess.Popen(["sleep", "10"])
        try:
            returncode, early = _wait_for_process(
                proc,
                timeout=10,
                early_success_paths=[output],
                early_success_grace=1,
            )
        finally:
            if proc.poll() is None:
                proc.kill()

        assert returncode == 0
        assert early is True

    def test_wait_for_process_raises_when_executor_is_idle(self):
        proc = subprocess.Popen(["sleep", "10"])
        try:
            with pytest.raises(ExecutorIdleTimeout):
                _wait_for_process(
                    proc,
                    timeout=10,
                    activity={"last": time.time() - 2},
                    idle_timeout=1,
                )
        finally:
            if proc.poll() is None:
                proc.kill()

    def test_wait_for_process_grants_one_final_stagnation_confirmation(self):
        proc = subprocess.Popen(["sleep", "10"])
        diagnostics = []
        started = time.monotonic()
        live = _ProcessLiveness(
            alive=True,
            process_count=2,
            cpu_ticks=10,
            socket_count=1,
        )
        try:
            with (
                patch(
                    "ft.engine.delegate._process_liveness_snapshot",
                    return_value=live,
                ),
                pytest.raises(ExecutorIdleTimeout),
            ):
                _wait_for_process(
                    proc,
                    timeout=10,
                    activity={"last": time.time() - 2},
                    idle_timeout=1,
                    idle_grace=1,
                    on_idle_grace=diagnostics.append,
                )
        finally:
            if proc.poll() is None:
                proc.kill()

        assert 0.8 <= time.monotonic() - started < 3
        assert len(diagnostics) == 1
        assert diagnostics[0]["processes"] == 2
        assert diagnostics[0]["sockets"] == 1

    @pytest.mark.skipif(
        not sys.platform.startswith("linux"), reason="requer procfs Linux"
    )
    def test_process_liveness_snapshot_avoids_command_lines_and_counts_tree(self):
        proc = subprocess.Popen(
            ["bash", "-c", "sleep 10 & wait"],
            start_new_session=True,
        )
        try:
            time.sleep(0.1)
            snapshot = _process_liveness_snapshot(proc)
        finally:
            _stop_process_tree(proc)

        assert snapshot.alive is True
        assert snapshot.process_count >= 2
        assert snapshot.fd_count > 0

    def test_stop_process_tree_kills_child_process_group(self):
        proc = subprocess.Popen(
            ["bash", "-c", "sleep 30 & wait"],
            start_new_session=True,
        )
        pgid = os.getpgid(proc.pid)
        try:
            time.sleep(0.2)
            _stop_process_tree(proc)

            with pytest.raises(ProcessLookupError):
                os.killpg(pgid, 0)
        finally:
            if proc.poll() is None:
                os.killpg(pgid, 9)

    def test_extracts_final_codex_message_from_json_stream(self):
        raw = "\n".join(
            [
                '{"type":"thread.started","thread_id":"t1"}',
                '{"type":"turn.started"}',
                '{"type":"item.completed","item":{"id":"i1","type":"agent_message","text":"DONE"}}',
                '{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":2}}',
            ]
        )
        assert _extract_codex_output(raw) == "DONE"

    def test_extracts_provider_session_ids(self):
        assert (
            _extract_provider_session_id(
                "codex",
                '{"type":"thread.started","thread_id":"thread-123"}',
            )
            == "thread-123"
        )
        assert (
            _extract_provider_session_id(
                "claude",
                '{"type":"result","session_id":"4f5b71b2-f632-4f07-8ee7-4e8fb9946c39"}',
            )
            == "4f5b71b2-f632-4f07-8ee7-4e8fb9946c39"
        )


class TestDelegateWithFeedback:
    def test_forwards_retry_options_to_delegate(self):
        expected = DelegateResult(
            success=True,
            output="DONE",
            files_created=[],
            files_modified=[],
        )

        with patch(
            "ft.engine.delegate.delegate_to_llm", return_value=expected
        ) as delegate_mock:
            result = delegate_with_feedback(
                original_task="escreva o PRD",
                feedback="faltaram linhas",
                project_root="/tmp/proj",
                allowed_paths=["project/docs/"],
                llm_engine="codex",
                llm_effort="max",
                max_turns=12,
                log_path="/tmp/proj/run.jsonl",
                stream_prefix="codex>",
                llm_timeout_seconds=77,
                llm_session_id="thread-123",
                llm_session_resume=True,
                workflow_id="feature",
            )

        assert result is expected
        delegate_mock.assert_called_once()
        kwargs = delegate_mock.call_args.kwargs
        assert "faltaram linhas" in kwargs["task"]
        assert kwargs["project_root"] == "/tmp/proj"
        assert kwargs["allowed_paths"] == ["project/docs/"]
        assert kwargs["llm_engine"] == "codex"
        assert kwargs["llm_effort"] == "max"
        assert kwargs["max_turns"] == 12
        assert kwargs["log_path"] == "/tmp/proj/run.jsonl"
        assert kwargs["stream_prefix"] == "codex>"
        assert kwargs["llm_timeout_seconds"] == 77
        assert kwargs["llm_session_id"] == "thread-123"
        assert kwargs["llm_session_resume"] is True
        assert kwargs["workflow_id"] == "feature"

    def test_forwards_opencode_read_denies_to_delegate(self):
        expected = DelegateResult(
            success=True,
            output="DONE",
            files_created=[],
            files_modified=[],
        )

        with patch(
            "ft.engine.delegate.delegate_to_llm", return_value=expected
        ) as delegate_mock:
            delegate_with_feedback(
                original_task="escreva o contrato",
                feedback="faltou arquivo",
                project_root="/tmp/proj",
                llm_engine="opencode",
                opencode_deny_read_paths=["docs/PRD.md"],
                opencode_restrict_tools=True,
                opencode_steps=8,
                opencode_deny_edit_tools=True,
                opencode_early_success_paths=["docs/out.md"],
                opencode_capture_output_path="docs/out.md",
            )

        kwargs = delegate_mock.call_args.kwargs
        assert kwargs["opencode_deny_read_paths"] == ["docs/PRD.md"]
        assert kwargs["opencode_restrict_tools"] is True
        assert kwargs["opencode_steps"] == 8
        assert kwargs["opencode_deny_edit_tools"] is True
        assert kwargs["opencode_early_success_paths"] == ["docs/out.md"]
        assert kwargs["opencode_capture_output_path"] == "docs/out.md"
