"""Unit tests for ft.engine.delegate command selection."""

import pytest

from ft.engine.delegate import _build_executor_command, _extract_codex_output


class TestBuildExecutorCommand:
    def test_builds_claude_command_with_bypass(self):
        cmd = _build_executor_command("claude", "faça algo", "/tmp/proj", 7)
        assert cmd[:3] == ["claude", "--print", "--dangerously-skip-permissions"]
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

    def test_invalid_engine_raises(self):
        with pytest.raises(ValueError, match="Executor LLM desconhecido"):
            _build_executor_command("gemini", "x", "/tmp/proj", 3)

    def test_extracts_final_codex_message_from_json_stream(self):
        raw = "\n".join([
            '{"type":"thread.started","thread_id":"t1"}',
            '{"type":"turn.started"}',
            '{"type":"item.completed","item":{"id":"i1","type":"agent_message","text":"DONE"}}',
            '{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":2}}',
        ])
        assert _extract_codex_output(raw) == "DONE"
