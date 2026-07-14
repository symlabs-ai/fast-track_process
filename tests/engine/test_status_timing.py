"""Regressão: ft status mostra tempo de ciclo e última atividade na linha de progresso."""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path

from ft.engine.layout import ensure_project_layout, register_project_process
from ft.engine.runner import StepRunner


_PROCESS = """\
id: timing_proc
version: "1.0.0"
title: Timing
nodes:
  - id: first
    type: document
    title: First
    executor: llm_coder
    next: end
  - id: end
    type: end
    title: End
"""


def _runner(tmp_path: Path) -> StepRunner:
    root = tmp_path / "proj"
    root.mkdir()
    ensure_project_layout(root, defaults={"llm_engine": "claude"})
    process = root / ".ft" / "process" / "test" / "process.yml"
    process.parent.mkdir(parents=True, exist_ok=True)
    process.write_text(_PROCESS, encoding="utf-8")
    register_project_process(
        root,
        process_name="test",
        process_path=process,
        template_id="test",
        entrypoint="test",
        set_default=True,
    )
    runner = StepRunner(
        process_path=process,
        state_path=root / "state" / "engine_state.yml",
        project_root=root,
    )
    runner.init_state()
    return runner


def test_status_progress_line_shows_runtime_and_last_activity(tmp_path, capsys):
    runner = _runner(tmp_path)
    root = Path(runner.project_root)

    started = datetime.now() - timedelta(minutes=90)
    log = root / runner._log_filename
    log.write_text(
        "# Run Log\n"
        "| timestamp | node_id |\n"
        "|-----------|---------|\n"
        f"| {started.strftime('%Y-%m-%d %H:%M:%S')} | `INIT` |\n",
        encoding="utf-8",
    )
    llm_dir = runner._llm_log_dir()
    llm_dir.mkdir(parents=True, exist_ok=True)
    (llm_dir / "delegation.log").write_text("...", encoding="utf-8")

    runner.status()
    out = capsys.readouterr().out
    assert "Ciclo: cycle-01" in out  # nome do ciclo sempre visível no status
    progress = next(line for line in out.splitlines() if "Progresso:" in line)
    assert "ciclo rodando há 1h30m" in progress
    assert "última atividade" in progress
    assert datetime.now().strftime("%H:") in progress.split("última atividade")[1]


def test_status_progress_line_degrades_without_run_log(tmp_path, capsys):
    runner = _runner(tmp_path)
    log = Path(runner.project_root) / runner._log_filename
    if log.exists():
        log.unlink()

    runner.status()
    out = capsys.readouterr().out
    progress = next(line for line in out.splitlines() if "Progresso:" in line)
    assert "rodando há" not in progress
    # engine_state.yml existe, então a última atividade ainda aparece
    assert "última atividade" in progress


def test_format_elapsed_units():
    assert StepRunner._format_elapsed(42) == "42s"
    assert StepRunner._format_elapsed(65) == "1m05s"
    assert StepRunner._format_elapsed(3725) == "1h02m"
