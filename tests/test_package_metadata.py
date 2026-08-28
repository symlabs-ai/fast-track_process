from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

import pytest

from ft import __version__
from ft.cli.main import main


def test_package_version_has_single_runtime_source():
    root = Path(__file__).resolve().parents[1]
    # A versão vem de ft/__about__.py e de nenhum outro lugar — comparar com um
    # literal aqui só duplicaria a fonte que o teste existe para proteger.
    about = (root / "ft" / "__about__.py").read_text(encoding="utf-8")
    declared = re.search(r'__version__\s*=\s*"([^"]+)"', about)
    assert declared, "ft/__about__.py deve declarar __version__"
    assert re.fullmatch(r"\d+\.\d+\.\d+", declared.group(1))
    assert __version__ == declared.group(1)

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
