from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

from ft.cli import main as cli_main
from ft.project import bootstrap_project


def _invoke(monkeypatch: pytest.MonkeyPatch, *arguments: object) -> None:
    monkeypatch.setattr(sys, "argv", ["ft", *(str(value) for value in arguments)])
    cli_main.main()


def test_validation_profiles_exposes_the_builtin_catalog(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _invoke(monkeypatch, "validation-profiles", "--json")

    payload = json.loads(capsys.readouterr().out)
    assert {profile["id"] for profile in payload["profiles"]} == {
        "android",
        "ios",
        "web",
        "desktop",
    }


def test_validation_matrix_materializes_the_current_project_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "product"
    bootstrap_project(root)

    _invoke(monkeypatch, "validation-matrix", root, "--json")

    payload = json.loads(capsys.readouterr().out)
    matrix = yaml.safe_load(
        (root / "docs" / "validation-matrix.yml").read_text(encoding="utf-8")
    )
    assert payload["status"] == "not_applicable"
    assert payload["written_to"] == "docs/validation-matrix.yml"
    assert matrix["status"] == "not_applicable"
    assert matrix["profiles"] == []
