"""Contrato do alvo nomeado ou contextual de ``ft validate``."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

from ft.cli import main as cli_main


PROCESS = """\
id: sandbox
version: "1.0.0"
title: Sandbox validation
nodes:
  - id: sandbox.end
    type: end
    title: End
"""


def _invoke(monkeypatch: pytest.MonkeyPatch, *arguments: object) -> None:
    monkeypatch.setattr(sys, "argv", ["ft", *(str(value) for value in arguments)])
    cli_main.main()


def _workspace(root: Path) -> Path:
    process = root / ".ft/process/sandbox/process.yml"
    process.parent.mkdir(parents=True)
    process.write_text(PROCESS, encoding="utf-8")
    (root / ".ft/manifest.yml").write_text(
        "schema_version: 3\nprocesses: {}\n",
        encoding="utf-8",
    )
    return process


def test_validate_accepts_canonical_unregistered_local_process_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit) as finished:
        _invoke(
            monkeypatch,
            "validate",
            "--template",
            ".ft/process/sandbox/process.yml",
        )

    assert finished.value.code == 0
    output = capsys.readouterr().out
    assert "Validando .ft/process/sandbox/process.yml" in output
    assert "Resultado: PASS" in output


@pytest.mark.parametrize(
    "selector",
    [
        "../outside/process.yml",
        "/tmp/outside/process.yml",
        ".ft/process/sandbox/other.yml",
        ".ft/process/deep/nested/process.yml",
        ".ft\\process\\sandbox\\process.yml",
    ],
)
def test_validate_rejects_noncanonical_or_escaping_process_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    selector: str,
) -> None:
    _workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit) as refused:
        _invoke(monkeypatch, "validate", "--template", selector)

    assert refused.value.code == 1
    output = capsys.readouterr().out
    assert "ERRO: alvo de validação inválido" in output
    assert "Erro inesperado" not in output


def test_validate_rejects_process_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    process = _workspace(tmp_path)
    target = tmp_path / "real-process.yml"
    target.write_text(process.read_text(encoding="utf-8"), encoding="utf-8")
    process.unlink()
    process.symlink_to(target)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit) as refused:
        _invoke(
            monkeypatch,
            "validate",
            "--template",
            ".ft/process/sandbox/process.yml",
        )

    assert refused.value.code == 1
    output = capsys.readouterr().out
    assert "ERRO: alvo de validação inválido" in output
    assert "Erro inesperado" not in output
