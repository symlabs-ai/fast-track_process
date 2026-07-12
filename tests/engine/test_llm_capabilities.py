from __future__ import annotations

import json
import subprocess
from unittest.mock import Mock

from ft.engine.llm_capabilities import (
    MAX_DISCOVERY_TIMEOUT_SECONDS,
    discover_llm_capabilities,
)


CLAUDE_HELP = """
Options:
  --effort <level>  Effort level for the current session (low, medium, high, xhigh, max)
  --model <model>   Model alias (e.g. 'fable', 'opus', or 'sonnet') or full name
                    (e.g. 'claude-fable-5'). (default: fable)
  --print           Print and exit
"""

CODEX_CATALOG = json.dumps(
    {
        "models": [
            {
                "slug": "hidden-review",
                "display_name": "Hidden Review",
                "visibility": "hide",
                "priority": 0,
                "supported_reasoning_levels": [{"effort": "high"}],
                "default_reasoning_level": "high",
            },
            {
                "slug": "gpt-5.6-sol",
                "display_name": "GPT-5.6-Sol",
                "visibility": "list",
                "priority": 1,
                "is_default": True,
                "supported_reasoning_levels": [
                    {"effort": "low"},
                    {"effort": "medium"},
                    {"effort": "max"},
                    {"effort": "ultra"},
                ],
                "default_reasoning_level": "medium",
            },
            {
                "slug": "gpt-5.4-mini",
                "display_name": "GPT-5.4-Mini",
                "visibility": "list",
                "priority": 2,
                "supported_reasoning_levels": [{"effort": "low"}, {"effort": "high"}],
                "default_reasoning_level": "high",
            },
        ]
    }
)

OPENCODE_MODELS = """
provider/reasoner
{
  "id": "reasoner",
  "providerID": "provider",
  "name": "Reasoner",
  "status": "active",
  "variants": {
    "low": {"reasoningEffort": "low"},
    "max": {"reasoningEffort": "max"}
  },
  "defaultReasoningEffort": "max",
  "default": true
}
provider/plain
{
  "id": "plain",
  "providerID": "provider",
  "name": "Plain Model",
  "status": "active",
  "variants": {}
}
provider/retired
{
  "id": "retired",
  "providerID": "provider",
  "name": "Retired Model",
  "status": "deprecated",
  "variants": {"high": {"reasoningEffort": "high"}}
}
"""


def _completed(args: list[str], stdout: str, returncode: int = 0, stderr: str = ""):
    return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr=stderr)


def _successful_run(command, **kwargs):
    outputs = {
        ("claude", "--help"): CLAUDE_HELP,
        ("codex", "debug", "models"): CODEX_CATALOG,
        ("opencode", "models", "--verbose"): OPENCODE_MODELS,
    }
    return _completed(command, outputs[tuple(command)])


def _agent(result, agent_id: str):
    return next(agent for agent in result["agents"] if agent["id"] == agent_id)


def test_discovers_model_specific_capabilities_from_all_clis(monkeypatch):
    run = Mock(side_effect=_successful_run)
    monkeypatch.setattr(subprocess, "run", run)

    result = discover_llm_capabilities(timeout_seconds=0.25, cwd="/tmp/project")

    claude = _agent(result, "claude")
    assert claude["available"] is True
    assert claude["default_model"] == "claude-fable-5"
    assert [model["id"] for model in claude["models"]] == [
        "claude-fable-5",
        "opus",
        "sonnet",
    ]
    assert claude["models"][0]["label"] == "Fable 5"
    assert claude["models"][0]["efforts"] == ["low", "medium", "high", "xhigh", "max"]

    codex = _agent(result, "codex")
    assert codex["default_model"] == "gpt-5.6-sol"
    assert [model["id"] for model in codex["models"]] == ["gpt-5.6-sol", "gpt-5.4-mini"]
    assert codex["models"][0]["efforts"] == ["low", "medium", "max", "ultra"]
    assert codex["models"][0]["default_effort"] == "medium"

    opencode = _agent(result, "opencode")
    assert opencode["default_model"] == "provider/reasoner"
    assert [model["id"] for model in opencode["models"]] == [
        "provider/reasoner",
        "provider/plain",
    ]
    assert opencode["models"][0]["efforts"] == ["low", "max"]
    assert opencode["models"][0]["default_effort"] == "max"
    assert opencode["models"][1]["efforts"] is None

    assert result["source"] == "real_provider_probe"
    assert result["timestamp"].endswith("+00:00")
    assert result["available"] is True
    assert result["defaults"]["models"] == {
        "claude": "claude-fable-5",
        "codex": "gpt-5.6-sol",
        "opencode": "provider/reasoner",
    }
    assert result["errors"] == []
    assert run.call_count == 3
    for call in run.call_args_list:
        assert call.kwargs["timeout"] == 0.25
        assert call.kwargs["cwd"] == "/tmp/project"
        assert call.kwargs["check"] is False


def test_missing_cli_fails_closed_without_affecting_other_agents(monkeypatch):
    def fake_run(command, **kwargs):
        if command[0] == "claude":
            raise FileNotFoundError("claude")
        return _successful_run(command, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = discover_llm_capabilities()

    claude = _agent(result, "claude")
    assert claude == {
        "id": "claude",
        "label": "Claude",
        "available": False,
        "models": [],
        "default_model": None,
        "reason": "claude CLI is not installed",
        "errors": [{"code": "not_installed", "message": "claude CLI is not installed"}],
    }
    assert _agent(result, "codex")["available"] is True
    assert result["errors"] == [
        {
            "agent": "claude",
            "code": "not_installed",
            "message": "claude CLI is not installed",
        }
    ]


def test_timeout_and_nonzero_exit_are_structured_and_fail_closed(monkeypatch):
    def fake_run(command, **kwargs):
        if command[0] == "codex":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        if command[0] == "opencode":
            return _completed(command, "", returncode=7, stderr="provider unavailable\ntry later")
        return _successful_run(command, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = discover_llm_capabilities(timeout_seconds=0.1)

    codex = _agent(result, "codex")
    assert codex["available"] is False
    assert codex["errors"][0]["code"] == "timeout"
    opencode = _agent(result, "opencode")
    assert opencode["available"] is False
    assert opencode["errors"] == [
        {
            "code": "command_failed",
            "message": "opencode models --verbose exited with status 7: provider unavailable try later",
        }
    ]


def test_invalid_or_empty_capability_output_fails_closed(monkeypatch):
    def fake_run(command, **kwargs):
        if command[0] == "claude":
            return _completed(command, "Usage: claude but no model option")
        if command[0] == "codex":
            return _completed(command, "not-json")
        return _completed(command, "no metadata here")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = discover_llm_capabilities()

    assert [agent["available"] for agent in result["agents"]] == [False, False, False]
    assert [agent["errors"][0]["code"] for agent in result["agents"]] == [
        "invalid_output",
        "invalid_output",
        "no_models",
    ]


def test_discovery_is_not_cached_and_probes_every_invocation(monkeypatch):
    run = Mock(side_effect=_successful_run)
    monkeypatch.setattr(subprocess, "run", run)

    first = discover_llm_capabilities()
    second = discover_llm_capabilities()

    assert first["timestamp"] != second["timestamp"]
    assert {key: value for key, value in first.items() if key != "timestamp"} == {
        key: value for key, value in second.items() if key != "timestamp"
    }
    assert run.call_count == 6


def test_timeout_is_always_finite_and_capped(monkeypatch):
    run = Mock(side_effect=_successful_run)
    monkeypatch.setattr(subprocess, "run", run)

    discover_llm_capabilities(timeout_seconds=999)

    assert {call.kwargs["timeout"] for call in run.call_args_list} == {
        MAX_DISCOVERY_TIMEOUT_SECONDS
    }
