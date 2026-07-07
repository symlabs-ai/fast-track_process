"""Unit tests for ft.engine.delegate command selection."""

import json

import pytest
from unittest.mock import patch

from ft.engine.delegate import (
    _build_executor_command,
    _executor_env,
    _extract_codex_output,
    DEFAULT_OPENCODE_MODEL,
    DelegateResult,
    delegate_with_feedback,
)


class TestBuildExecutorCommand:
    def test_builds_claude_command_with_bypass(self):
        cmd = _build_executor_command("claude", "faça algo", "/tmp/proj", 7)
        assert cmd[0] == "claude"
        assert "--dangerously-skip-permissions" in cmd
        assert ["--output-format", "stream-json"] == cmd[1:3]
        assert "--max-turns" in cmd
        assert "7" in cmd
        assert "-p" in cmd
        assert "faça algo" in cmd

    def test_builds_codex_command_with_bypass(self):
        cmd = _build_executor_command("codex", "faça algo", "/tmp/proj", 7)
        assert cmd[:2] == ["codex", "exec"]
        assert "--dangerously-bypass-approvals-and-sandbox" in cmd
        assert "--skip-git-repo-check" in cmd
        assert "--json" in cmd
        assert "-C" in cmd
        assert "/tmp/proj" in cmd
        assert "faça algo" == cmd[-1]

    def test_builds_opencode_command_with_default_model(self):
        cmd = _build_executor_command("opencode", "faça algo", "/tmp/proj", 7)
        assert cmd == [
            "opencode",
            "run",
            "--dir", "/tmp/proj",
            "-m", DEFAULT_OPENCODE_MODEL,
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
            "--dir", "/tmp/proj",
            "-m", "anthropic/claude-sonnet-4-5",
            "faça algo",
        ]

    def test_invalid_engine_raises(self):
        with pytest.raises(ValueError, match="Executor LLM desconhecido"):
            _build_executor_command("unknown_engine_xyz", "x", "/tmp/proj", 3)

    def test_opencode_env_enforces_runtime_config(self):
        env = _executor_env(
            "opencode",
            {
                "OPENCODE_CONFIG_CONTENT": json.dumps({
                    "permission": {"bash": "ask"},
                    "compaction": {"reserved": 2000},
                    "theme": "system",
                })
            },
        )

        config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
        assert config["permission"]["bash"] == "ask"
        assert config["permission"]["external_directory"] == "deny"
        assert config["compaction"] == {
            "auto": True,
            "prune": True,
            "reserved": 10000,
        }
        assert config["theme"] == "system"

    def test_non_opencode_env_is_unchanged(self):
        env = _executor_env("claude", {"OPENCODE_CONFIG_CONTENT": "{}"})
        assert env["OPENCODE_CONFIG_CONTENT"] == "{}"

    def test_extracts_final_codex_message_from_json_stream(self):
        raw = "\n".join([
            '{"type":"thread.started","thread_id":"t1"}',
            '{"type":"turn.started"}',
            '{"type":"item.completed","item":{"id":"i1","type":"agent_message","text":"DONE"}}',
            '{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":2}}',
        ])
        assert _extract_codex_output(raw) == "DONE"


class TestDelegateWithFeedback:
    def test_forwards_retry_options_to_delegate(self):
        expected = DelegateResult(
            success=True,
            output="DONE",
            files_created=[],
            files_modified=[],
        )

        with patch("ft.engine.delegate.delegate_to_llm", return_value=expected) as delegate_mock:
            result = delegate_with_feedback(
                original_task="escreva o PRD",
                feedback="faltaram linhas",
                project_root="/tmp/proj",
                allowed_paths=["project/docs/"],
                llm_engine="codex",
                max_turns=12,
                log_path="/tmp/proj/run.jsonl",
                stream_prefix="codex>",
            )

        assert result is expected
        delegate_mock.assert_called_once()
        kwargs = delegate_mock.call_args.kwargs
        assert "faltaram linhas" in kwargs["task"]
        assert kwargs["project_root"] == "/tmp/proj"
        assert kwargs["allowed_paths"] == ["project/docs/"]
        assert kwargs["llm_engine"] == "codex"
        assert kwargs["max_turns"] == 12
        assert kwargs["log_path"] == "/tmp/proj/run.jsonl"
        assert kwargs["stream_prefix"] == "codex>"
