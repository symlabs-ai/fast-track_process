from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import pytest

from ft import __version__
from ft.cli.main import main


def test_package_version_has_single_runtime_source():
    assert __version__ == "0.20.2"

    root = Path(__file__).resolve().parents[1]
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert "version" not in pyproject["project"]
    assert "version" in pyproject["project"]["dynamic"]
    assert pyproject["tool"]["hatch"]["version"]["path"] == "ft/__about__.py"


def test_cli_reports_runtime_package_version(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["ft", "--version"])

    with pytest.raises(SystemExit) as exc:
        main()

    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"ft {__version__}"
