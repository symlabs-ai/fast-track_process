"""Tests do guard que impede usar o repo do engine/template como projeto."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from ft.cli.main import _guard_engine_repo, engine_root


class TestGuardEngineRepo:
    def test_blocks_engine_repo(self, monkeypatch):
        monkeypatch.delenv("FT_ALLOW_ENGINE_REPO", raising=False)
        with pytest.raises(SystemExit) as exc:
            _guard_engine_repo(engine_root())
        assert exc.value.code == 1

    def test_allows_other_directories(self, tmp_path, monkeypatch):
        monkeypatch.delenv("FT_ALLOW_ENGINE_REPO", raising=False)
        _guard_engine_repo(tmp_path)  # não levanta

    def test_env_override_allows_engine_repo(self, monkeypatch):
        monkeypatch.setenv("FT_ALLOW_ENGINE_REPO", "1")
        _guard_engine_repo(engine_root())  # não levanta


def _run_ft_in_engine_repo(args: list[str]) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(engine_root())}
    env.pop("FT_ALLOW_ENGINE_REPO", None)
    return subprocess.run(
        [sys.executable, "-m", "ft.cli.main"] + args,
        capture_output=True, text=True, cwd=engine_root(), env=env,
    )


class TestGuardViaCLI:
    """Guard global: todo comando (exceto --help) recusa rodar no repo do template."""

    @pytest.mark.parametrize("args", [
        ["init"],
        ["status"],
        ["continue"],
        ["approve"],
        ["reject", "motivo"],
        ["graph"],
        ["validate", "--template", "base"],
        ["close"],
        ["abort"],
        ["run", ".", "--template", "base"],
        ["runs"],
    ])
    def test_command_refuses_inside_engine_repo(self, args):
        result = _run_ft_in_engine_repo(args)
        assert result.returncode == 1, f"ft {' '.join(args)} deveria bloquear no repo do template"
        assert "engine/template" in result.stdout + result.stderr

    def test_help_still_works_inside_engine_repo(self):
        result = _run_ft_in_engine_repo(["--help"])
        assert result.returncode == 0

    def test_run_allows_external_project_path_from_engine_repo(self, tmp_path):
        """ft run <path-externo> a partir do CWD do template deve passar do guard."""
        result = _run_ft_in_engine_repo(["runs", str(tmp_path)])
        assert "engine/template" not in result.stdout + result.stderr
