from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from ft.cli import main as cli_main
from ft.engine.layout import ensure_project_layout, update_manifest_llm_defaults


def _capabilities() -> dict[str, object]:
    return {
        "source": "real_provider_probe",
        "timestamp": "2026-07-12T12:00:00+00:00",
        "available": True,
        "agents": [
            {
                "id": "claude",
                "label": "Claude",
                "available": False,
                "models": [],
                "default_model": None,
                "reason": "claude CLI is not installed",
                "errors": [
                    {
                        "code": "not_installed",
                        "message": "claude CLI is not installed",
                    }
                ],
            },
            {
                "id": "codex",
                "label": "Codex",
                "available": True,
                "models": [
                    {
                        "id": "gpt-5.6-sol",
                        "label": "GPT-5.6-Sol",
                        "available": True,
                        "reason": None,
                        "efforts": ["low", "medium", "high", "max", "ultra"],
                        "default_effort": "medium",
                    }
                ],
                "default_model": "gpt-5.6-sol",
                "reason": None,
                "errors": [],
            },
            {
                "id": "opencode",
                "label": "OpenCode",
                "available": True,
                "models": [
                    {
                        "id": "pgx/zai-org_glm-4.7-flash",
                        "label": "GLM-4.7-Flash (PGX)",
                        "available": True,
                        "reason": None,
                        "efforts": None,
                        "default_effort": None,
                    }
                ],
                "default_model": "pgx/zai-org_glm-4.7-flash",
                "reason": None,
                "errors": [],
            },
        ],
        "defaults": {
            "agent": None,
            "models": {
                "claude": None,
                "codex": "gpt-5.6-sol",
                "opencode": "pgx/zai-org_glm-4.7-flash",
            },
            "efforts": {
                "claude": {},
                "codex": {"gpt-5.6-sol": "medium"},
                "opencode": {"pgx/zai-org_glm-4.7-flash": None},
            },
        },
        "errors": [
            {
                "agent": "claude",
                "code": "not_installed",
                "message": "claude CLI is not installed",
            }
        ],
    }


@pytest.fixture
def llm_project(tmp_path: Path) -> Path:
    ensure_project_layout(tmp_path)
    manifest_path = tmp_path / ".ft" / "manifest.yml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["custom"] = {"keep": [1, 2, 3]}
    manifest["defaults"] = {
        "llm_engine": "codex",
        "llm_model": "gpt-5.6-sol",
        "llm_effort": "high",
        "unrelated": "preserve-me",
    }
    manifest_path.write_text(
        yaml.safe_dump(manifest, sort_keys=False),
        encoding="utf-8",
    )
    return tmp_path


def test_atomic_layout_update_preserves_unrelated_keys_and_mode(
    llm_project: Path,
    monkeypatch,
):
    manifest_path = llm_project / ".ft" / "manifest.yml"
    manifest_path.chmod(0o640)
    real_replace = os.replace
    replacements: list[tuple[Path, Path]] = []

    def recording_replace(source, destination):
        replacements.append((Path(source), Path(destination)))
        real_replace(source, destination)

    monkeypatch.setattr("ft.engine.layout.os.replace", recording_replace)

    updated = update_manifest_llm_defaults(
        llm_project,
        llm_engine="opencode",
        llm_model="pgx/zai-org_glm-4.7-flash",
        llm_effort=None,
    )

    assert updated == manifest_path
    assert replacements and replacements[0][0].parent == manifest_path.parent
    assert replacements[0][1] == manifest_path
    assert not replacements[0][0].exists()
    assert manifest_path.stat().st_mode & 0o777 == 0o640
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    assert manifest["custom"] == {"keep": [1, 2, 3]}
    assert manifest["defaults"] == {
        "llm_engine": "opencode",
        "llm_model": "pgx/zai-org_glm-4.7-flash",
        "unrelated": "preserve-me",
    }


def test_atomic_layout_update_rejects_invalid_manifest_without_replacing(tmp_path: Path):
    manifest_path = tmp_path / ".ft" / "manifest.yml"
    manifest_path.parent.mkdir(parents=True)
    original = "defaults: [not, a, mapping]\nkeep: true\n"
    manifest_path.write_text(original, encoding="utf-8")

    with pytest.raises(ValueError, match="defaults deve ser mapping"):
        update_manifest_llm_defaults(
            tmp_path,
            llm_engine="codex",
            llm_model="gpt-5.6-sol",
            llm_effort="max",
        )

    assert manifest_path.read_text(encoding="utf-8") == original


def test_layout_never_persists_default_effort_sentinel(tmp_path: Path):
    ensure_project_layout(
        tmp_path,
        defaults={
            "llm_engine": "codex",
            "llm_model": "gpt-5.6-sol",
            "llm_effort": "max",
        },
    )
    ensure_project_layout(tmp_path, defaults={"llm_effort": "default"})

    manifest = yaml.safe_load(
        (tmp_path / ".ft" / "manifest.yml").read_text(encoding="utf-8")
    )
    assert manifest["defaults"] == {
        "llm_engine": "codex",
        "llm_model": "gpt-5.6-sol",
    }


def test_capabilities_overlay_saved_reported_and_effective_defaults(
    llm_project: Path,
    monkeypatch,
    capsys,
):
    probe = Mock(return_value=deepcopy(_capabilities()))
    monkeypatch.setattr(cli_main, "discover_llm_capabilities", probe)
    monkeypatch.setattr(cli_main, "find_project_root", lambda: llm_project)

    cli_main.cmd_llm_capabilities(SimpleNamespace(json=True))

    payload = json.loads(capsys.readouterr().out)
    assert probe.call_args.kwargs == {"cwd": llm_project}
    assert payload["source"] == "real_provider_probe"
    assert payload["timestamp"] == "2026-07-12T12:00:00+00:00"
    assert payload["defaults"]["saved"] == {
        "agent": "codex",
        "model": "gpt-5.6-sol",
        "effort": "high",
        "source": "project_manifest",
    }
    assert payload["defaults"]["reported"]["efforts"] == {
        "claude": {},
        "codex": {"gpt-5.6-sol": "medium"},
        "opencode": {"pgx/zai-org_glm-4.7-flash": None},
    }
    assert payload["defaults"]["effective"] == {
        "agent": "codex",
        "model": "gpt-5.6-sol",
        "effort": "high",
        "valid": True,
        "reason": None,
        "source": "project_manifest",
    }
    assert payload["errors"][0]["agent"] == "claude"


def test_defaults_command_validates_fresh_and_persists_selection(
    llm_project: Path,
    monkeypatch,
    capsys,
):
    probe = Mock(return_value=deepcopy(_capabilities()))
    monkeypatch.setattr(cli_main, "discover_llm_capabilities", probe)
    monkeypatch.setattr(cli_main, "find_project_root", lambda: llm_project)

    cli_main.cmd_llm_defaults(
        SimpleNamespace(
            agent="codex",
            model="gpt-5.6-sol",
            effort="max",
            json=True,
        )
    )

    payload = json.loads(capsys.readouterr().out)
    manifest = yaml.safe_load(
        (llm_project / ".ft" / "manifest.yml").read_text(encoding="utf-8")
    )
    assert probe.call_count == 1
    assert manifest["defaults"]["llm_engine"] == "codex"
    assert manifest["defaults"]["llm_model"] == "gpt-5.6-sol"
    assert manifest["defaults"]["llm_effort"] == "max"
    assert manifest["defaults"]["unrelated"] == "preserve-me"
    assert payload["updated"] is True
    assert payload["manifest"] == ".ft/manifest.yml"
    assert payload["defaults"]["saved"]["effort"] == "max"
    assert payload["defaults"]["effective"]["effort"] == "max"


def test_default_effort_removes_override_and_reports_provider_effective_value(
    llm_project: Path,
    monkeypatch,
    capsys,
):
    monkeypatch.setattr(
        cli_main,
        "discover_llm_capabilities",
        lambda **kwargs: deepcopy(_capabilities()),
    )
    monkeypatch.setattr(cli_main, "find_project_root", lambda: llm_project)

    cli_main.cmd_llm_defaults(
        SimpleNamespace(
            agent="codex",
            model="gpt-5.6-sol",
            effort="default",
            json=True,
        )
    )

    payload = json.loads(capsys.readouterr().out)
    manifest = yaml.safe_load(
        (llm_project / ".ft" / "manifest.yml").read_text(encoding="utf-8")
    )
    assert "llm_effort" not in manifest["defaults"]
    assert payload["defaults"]["saved"]["effort"] is None
    assert payload["defaults"]["effective"]["effort"] == "medium"


def test_model_without_effort_clears_override(
    llm_project: Path,
    monkeypatch,
    capsys,
):
    monkeypatch.setattr(
        cli_main,
        "discover_llm_capabilities",
        lambda **kwargs: deepcopy(_capabilities()),
    )
    monkeypatch.setattr(cli_main, "find_project_root", lambda: llm_project)

    cli_main.cmd_llm_defaults(
        SimpleNamespace(
            agent="opencode",
            model="pgx/zai-org_glm-4.7-flash",
            effort=None,
            json=True,
        )
    )

    payload = json.loads(capsys.readouterr().out)
    manifest = yaml.safe_load(
        (llm_project / ".ft" / "manifest.yml").read_text(encoding="utf-8")
    )
    assert "llm_effort" not in manifest["defaults"]
    assert payload["defaults"]["effective"]["effort"] is None


def test_invalid_effort_fails_closed_without_touching_manifest(
    llm_project: Path,
    monkeypatch,
    capsys,
):
    monkeypatch.setattr(
        cli_main,
        "discover_llm_capabilities",
        lambda **kwargs: deepcopy(_capabilities()),
    )
    monkeypatch.setattr(cli_main, "find_project_root", lambda: llm_project)
    manifest_path = llm_project / ".ft" / "manifest.yml"
    before = manifest_path.read_bytes()

    with pytest.raises(SystemExit) as exit_info:
        cli_main.cmd_llm_defaults(
            SimpleNamespace(
                agent="codex",
                model="gpt-5.6-sol",
                effort="impossible",
                json=True,
            )
        )

    payload = json.loads(capsys.readouterr().out)
    assert exit_info.value.code == 2
    assert manifest_path.read_bytes() == before
    assert payload["updated"] is False
    assert payload["errors"][-1]["code"] == "effort_unsupported"


@pytest.mark.parametrize(
    ("argv", "handler_name"),
    [
        (["ft", "llm-capabilities", "--json"], "cmd_llm_capabilities"),
        (
            [
                "ft",
                "llm-defaults",
                "--agent",
                "codex",
                "--model",
                "gpt-5.6-sol",
                "--effort",
                "max",
                "--json",
            ],
            "cmd_llm_defaults",
        ),
    ],
)
def test_main_parses_and_dispatches_new_llm_commands(
    argv,
    handler_name,
    monkeypatch,
):
    handler = Mock()
    monkeypatch.setattr(cli_main.sys, "argv", argv)
    monkeypatch.setattr(cli_main, "find_project_root", lambda: Path("/project"))
    monkeypatch.setattr(cli_main, "_guard_engine_repo", lambda root: None)
    monkeypatch.setattr(cli_main, handler_name, handler)

    cli_main.main()

    assert handler.call_count == 1
    assert handler.call_args.args[0].json is True


def test_resolve_llm_effort_preserves_explicit_default_override():
    assert cli_main.resolve_llm_effort(SimpleNamespace(effort=None)) is None
    assert cli_main.resolve_llm_effort(SimpleNamespace(effort="default")) == "default"
    assert cli_main.resolve_llm_effort(SimpleNamespace(effort=" max ")) == "max"
