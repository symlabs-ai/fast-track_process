"""Unit tests for ft.engine.delegate command selection."""

import json
import os
import subprocess
import time

import pytest
from unittest.mock import patch

from ft.engine.delegate import (
    _build_executor_command,
    _clean_opencode_capture_text,
    _executor_env,
    _env_nonnegative_int,
    _append_opencode_runtime_diagnostics,
    _extract_codex_output,
    _extract_opencode_json_text,
    _is_opencode_internal_log_line,
    _opencode_capture_command,
    _prepare_opencode_sandbox_mounts,
    _stop_process_tree,
    _wait_for_process,
    _wrap_opencode_sandbox_command,
    DEFAULT_OPENCODE_CONTEXT_LIMIT,
    DEFAULT_OPENCODE_MODEL,
    DEFAULT_OPENCODE_OUTPUT_LIMIT,
    DelegateResult,
    ExecutorIdleTimeout,
    delegate_to_llm,
    delegate_with_feedback,
)


class TestBuildExecutorCommand:
    def test_env_nonnegative_int_accepts_zero(self, monkeypatch):
        monkeypatch.setenv("FT_OPENCODE_IDLE_RETRIES", "0")

        assert _env_nonnegative_int("FT_OPENCODE_IDLE_RETRIES") == 0

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
            "--dir", "/tmp/proj",
            "-m", "anthropic/claude-sonnet-4-5",
            "--auto",
            "--pure",
            "faça algo",
        ]

    def test_builds_opencode_command_with_variant_override(self, monkeypatch):
        monkeypatch.setenv("FT_OPENCODE_VARIANT", "low")

        cmd = _build_executor_command("opencode", "faça algo", "/tmp/proj", 7)

        assert ["--variant", "low"] == cmd[cmd.index("--variant"):cmd.index("--variant") + 2]

    def test_builds_opencode_command_allows_disabling_pure_and_variant(self, monkeypatch):
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
        assert ["--log-level", "DEBUG"] == cmd[cmd.index("--log-level"):cmd.index("--log-level") + 2]
        assert "--thinking" not in cmd
        assert cmd[-1] == "faça algo"

    def test_builds_opencode_command_with_custom_log_level(self, monkeypatch):
        monkeypatch.setenv("FT_OPENCODE_PRINT_LOGS", "1")
        monkeypatch.setenv("FT_OPENCODE_LOG_LEVEL", "INFO")

        cmd = _build_executor_command("opencode", "faça algo", "/tmp/proj", 7)

        assert "--print-logs" in cmd
        assert ["--log-level", "INFO"] == cmd[cmd.index("--log-level"):cmd.index("--log-level") + 2]
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
        internal_log.write_text("timestamp=now level=ERROR message=boom\n", encoding="utf-8")
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
            "opencode", "run", "--dir", "/tmp/project", "-m", DEFAULT_OPENCODE_MODEL,
            "--print-logs", "--log-level", "DEBUG", "prompt",
        ]

        captured = _opencode_capture_command(cmd)

        assert "--print-logs" not in captured
        assert "--log-level" not in captured
        assert captured[-3:] == ["--format", "json", "prompt"]

    def test_extracts_opencode_json_text_for_capture(self):
        raw = "\n".join([
            '{"type":"step_start","part":{"type":"step-start"}}',
            '{"type":"text","part":{"type":"text","text":"# Doc\\nbody\\n[tool_calls] (None)"}}',
        ])

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
        assert _clean_opencode_capture_text("BLOCKED: sem contexto") == "BLOCKED: sem contexto"

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

    def test_opencode_env_can_override_context_limit_for_custom_model(self, monkeypatch):
        monkeypatch.setenv("FT_OPENCODE_CONTEXT_LIMIT", "123456")
        monkeypatch.setenv("FT_OPENCODE_OUTPUT_LIMIT", "8192")

        env = _executor_env(
            "opencode",
            {
                "OPENCODE_CONFIG_CONTENT": json.dumps({
                    "provider": {
                        "pgx": {
                            "options": {"baseURL": "http://example.test/v1"},
                            "models": {
                                "openai/gpt-oss-20b": {"name": "GPT-OSS 20B"}
                            },
                        }
                    }
                })
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
                "OPENCODE_CONFIG_CONTENT": json.dumps({
                    "provider": {
                        "pgx": {
                            "options": {"baseURL": "http://example.test/v1"},
                            "models": {
                                "zai-org_glm-4.7-flash": {"name": "GLM 4.7 Flash"}
                            },
                        }
                    }
                })
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

    def test_opencode_sandbox_prepares_exact_file_and_dir_mounts(self, tmp_path):
        mounts = _prepare_opencode_sandbox_mounts(
            str(tmp_path),
            ["docs/api_contract.md", "project/frontend/"],
        )

        by_path = {mount.path.relative_to(tmp_path).as_posix(): mount for mount in mounts}
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
        monkeypatch.setattr("ft.engine.delegate.shutil.which", lambda name: "/usr/bin/bwrap")
        (tmp_path / "state").mkdir()

        cmd, mounts = _wrap_opencode_sandbox_command(
            ["opencode", "run", "prompt"],
            project_root=str(tmp_path),
            allowed_paths=["docs/out.md"],
            runtime_dir=str(tmp_path / "runtime"),
        )

        assert cmd[:7] == [
            "/usr/bin/bwrap",
            "--ro-bind", "/", "/",
            "--dev-bind", "/dev", "/dev",
        ]
        assert ["--bind", str(tmp_path / "docs/out.md"), str(tmp_path / "docs/out.md")] in [
            cmd[i:i + 3] for i in range(len(cmd) - 2)
        ]
        assert [
            "--ro-bind",
            str(tmp_path / "runtime" / "hidden-state"),
            str(tmp_path / "state"),
        ] in [cmd[i:i + 3] for i in range(len(cmd) - 2)]
        assert cmd[-3:] == ["opencode", "run", "prompt"]
        assert [mount.path for mount in mounts] == [tmp_path / "docs/out.md"]

    def test_delegate_opencode_code_node_materializes_generated_file_bundle(self, tmp_path, monkeypatch):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        generated = (
            "<ft_file path=\"project/frontend/package.json\">\n"
            "{\"scripts\":{\"build\":\"echo ok\"}}\n"
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

    def test_delegate_opencode_code_node_uses_tool_mode_by_default(self, tmp_path, monkeypatch):
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

    def test_delegate_opencode_native_write_prompt_uses_path_schema(self, tmp_path, monkeypatch):
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

    def test_delegate_opencode_file_bundle_tolerates_extra_text(self, tmp_path, monkeypatch):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        generated = (
            "I will create the scaffold now.\n"
            "<ft_file path=\"project/frontend/package.json\">\n"
            "{\"scripts\":{\"build\":\"echo ok\"}}\n"
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

    def test_delegate_opencode_file_bundle_prefixes_frontend_orphan_paths(self, tmp_path, monkeypatch):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        generated = (
            "<ft_file path=\"project/frontend/package.json\">\n"
            "{\"scripts\":{\"build\":\"node scripts/build.js\"}}\n"
            "</ft_file>\n"
            "<ft_file path=\"scripts/build.js\">\n"
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
        assert (tmp_path / "project/frontend/scripts/build.js").read_text(encoding="utf-8") == "process.exit(0)\n"
        assert "project/frontend/scripts/build.js" in result.output

    def test_delegate_opencode_file_bundle_normalizes_frontend_alias_paths(self, tmp_path, monkeypatch):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        generated = (
            "<ft_file path=\"project/frontend/package.json\">\n"
            "{\"scripts\":{\"build\":\"node scripts/build.mjs\"}}\n"
            "</ft_file>\n"
            "<ft_file path=\"package/frontend/scripts/build.mjs\">\n"
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
        assert (tmp_path / "project/frontend/scripts/build.mjs").read_text(encoding="utf-8") == "process.exit(0)\n"
        assert "project/frontend/scripts/build.mjs" in result.output

    def test_delegate_opencode_file_bundle_preserves_dotfile_paths(self, tmp_path, monkeypatch):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        generated = (
            "<ft_file path=\".build_ok\">\n"
            "ready\n"
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

    def test_forwards_opencode_read_denies_to_delegate(self):
        expected = DelegateResult(
            success=True,
            output="DONE",
            files_created=[],
            files_modified=[],
        )

        with patch("ft.engine.delegate.delegate_to_llm", return_value=expected) as delegate_mock:
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
