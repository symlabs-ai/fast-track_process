"""ft close analisa o custo do ciclo por padrão (-y sem perguntar, -n pula)."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import pytest

from ft.cli import main as cli_main


class _Runner:
    def __init__(self, root: Path) -> None:
        self.project_root = str(root)


def _args(**kw) -> Namespace:
    base = {"analyse_yes": False, "analyse_no": False}
    base.update(kw)
    return Namespace(**base)


@pytest.fixture(autouse=True)
def _quiet_improvements():
    """Os achados têm testes próprios; aqui só interessa o fluxo do close."""
    with patch.object(cli_main, "_present_cycle_improvements"):
        yield


@pytest.fixture
def runner(tmp_path):
    return _Runner(tmp_path / "cycle-07-demo")


def test_dash_n_skips_without_asking(runner):
    with (
        patch.object(cli_main, "render_cycle_analysis") as render,
        patch.object(cli_main, "_confirm") as confirm,
    ):
        cli_main._maybe_analyse_cycle_before_close(_args(analyse_no=True), runner)
    render.assert_not_called()
    confirm.assert_not_called()


def test_dash_y_analyses_without_asking(runner):
    with (
        patch.object(
            cli_main, "render_cycle_analysis", return_value=object()
        ) as render,
        patch.object(cli_main, "_confirm") as confirm,
    ):
        cli_main._maybe_analyse_cycle_before_close(_args(analyse_yes=True), runner)
    render.assert_called_once()
    confirm.assert_not_called()


def test_default_asks_and_honours_yes(runner):
    with (
        patch.object(
            cli_main, "render_cycle_analysis", return_value=object()
        ) as render,
        patch.object(cli_main, "_confirm", return_value=True) as confirm,
        patch("sys.stdin") as stdin,
    ):
        stdin.isatty.return_value = True
        cli_main._maybe_analyse_cycle_before_close(_args(), runner)
    confirm.assert_called_once()
    render.assert_called_once()


def test_default_asks_and_honours_no(runner):
    with (
        patch.object(cli_main, "render_cycle_analysis") as render,
        patch.object(cli_main, "_confirm", return_value=False),
        patch("sys.stdin") as stdin,
    ):
        stdin.isatty.return_value = True
        cli_main._maybe_analyse_cycle_before_close(_args(), runner)
    render.assert_not_called()


def test_non_interactive_analyses_without_hanging(runner):
    """Sem TTY não há a quem perguntar: analisa em vez de travar num input()."""
    with (
        patch.object(
            cli_main, "render_cycle_analysis", return_value=object()
        ) as render,
        patch.object(cli_main, "_confirm") as confirm,
        patch("sys.stdin") as stdin,
    ):
        stdin.isatty.return_value = False
        cli_main._maybe_analyse_cycle_before_close(_args(), runner)
    confirm.assert_not_called()
    render.assert_called_once()


def test_analysis_failure_never_blocks_the_close(runner, capsys):
    with patch.object(
        cli_main, "render_cycle_analysis", side_effect=OSError("trace ilegível")
    ):
        cli_main._maybe_analyse_cycle_before_close(_args(analyse_yes=True), runner)
    assert "indisponível" in capsys.readouterr().out


def test_missing_telemetry_warns_but_proceeds(runner, capsys):
    with patch.object(cli_main, "render_cycle_analysis", return_value=None):
        cli_main._maybe_analyse_cycle_before_close(_args(analyse_yes=True), runner)
    assert "Sem telemetria" in capsys.readouterr().out
