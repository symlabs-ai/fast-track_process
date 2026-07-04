"""Tests do guard que impede usar o repo do engine/template como projeto."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

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


class TestGuardViaCLI:
    def test_ft_init_refuses_inside_engine_repo(self):
        env = {**os.environ, "PYTHONPATH": str(engine_root())}
        env.pop("FT_ALLOW_ENGINE_REPO", None)
        result = subprocess.run(
            [sys.executable, "-m", "ft.cli.main", "init"],
            capture_output=True, text=True, cwd=engine_root(), env=env,
        )
        assert result.returncode == 1
        assert "engine/template" in result.stdout + result.stderr
