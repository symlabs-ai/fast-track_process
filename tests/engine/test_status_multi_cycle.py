"""Regressão: ft status sem --cycle com vários ciclos abertos faz fan-out rotulado."""

from __future__ import annotations

import io
from types import SimpleNamespace

import ft.runs as runs_mod
from ft.cli import main as cli
from ft.runs.registry import AmbiguousCycleError


class _StubRunner:
    def __init__(self, name):
        self.name = name

    def status(self, full=False, updated_at=None):
        if updated_at:
            print(f"<updated {updated_at}>")
        print(f"<status {self.name} full={full}>")

    def status_report(self, updated_at=None):
        if updated_at:
            print(f"<updated {updated_at}>")
        print(f"<report {self.name}>")


class _TTYBuffer(io.StringIO):
    def isatty(self):
        return True


def _patch_common(monkeypatch, tmp_path, cycles):
    monkeypatch.setattr(cli, "find_project_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "canonical_project_root", lambda p: p)

    def fake_select(owner, selected, include_terminal=True):
        raise AmbiguousCycleError(cycles)

    monkeypatch.setattr(runs_mod, "select_cycle", fake_select)
    monkeypatch.setattr(cli, "get_runner", lambda **kw: _StubRunner(kw.get("cycle")))


def test_status_fans_out_labeled_blocks_for_each_open_cycle(
    monkeypatch, tmp_path, capsys
):
    _patch_common(monkeypatch, tmp_path, ["cycle-a", "cycle-b"])

    cli.cmd_status(SimpleNamespace())

    out = capsys.readouterr().out
    assert "Ciclo: cycle-a" in out
    assert "Ciclo: cycle-b" in out
    assert out.index("cycle-a") < out.index("cycle-b")
    assert out.count("<status ") == 2


def test_status_with_explicit_cycle_does_not_fan_out(monkeypatch, tmp_path, capsys):
    _patch_common(monkeypatch, tmp_path, ["cycle-a", "cycle-b"])

    cli.cmd_status(SimpleNamespace(cycle="cycle-b"))

    out = capsys.readouterr().out
    assert out.count("<status ") == 1
    assert "cycle-b" in out


def test_status_report_mode_fans_out_too(monkeypatch, tmp_path, capsys):
    _patch_common(monkeypatch, tmp_path, ["cycle-a", "cycle-b"])

    cli.cmd_status(SimpleNamespace(report=True))

    out = capsys.readouterr().out
    assert out.count("<report ") == 2


def test_status_watch_redraws_in_normal_scrollable_screen(monkeypatch, tmp_path):
    _patch_common(monkeypatch, tmp_path, ["cycle-a", "cycle-b"])
    output = _TTYBuffer()
    intervals = []

    def fake_sleep(seconds):
        intervals.append(seconds)
        if len(intervals) == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(cli.sys, "stdout", output)
    monkeypatch.setattr(cli.time, "sleep", fake_sleep)

    cli.cmd_status(SimpleNamespace(watch=7))

    rendered = output.getvalue()
    assert intervals == [7, 7]
    assert "\x1b[?1049h" not in rendered
    assert "\x1b[?1049l" not in rendered
    assert rendered.count("\x1b[H\x1b[J") == 2
    assert rendered.count("<updated ") == 4
    assert rendered.count("<status cycle-a full=False>") == 2
    assert rendered.count("<status cycle-b full=False>") == 2


def test_status_watch_without_value_defaults_to_sixty_seconds(monkeypatch, tmp_path):
    received = {}
    monkeypatch.setattr(cli.sys, "argv", ["ft", "status", "--watch"])
    monkeypatch.setattr(cli, "find_project_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_guard_engine_repo", lambda _root: None)
    monkeypatch.setattr(cli, "cmd_status", lambda args: received.update(vars(args)))

    cli.main()

    assert received["watch"] == 60
