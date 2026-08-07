from __future__ import annotations

from ft.engine.runner import StepRunner


PROCESS = """id: status_test
version: 1.0.0
title: Status Test
nodes:
  - id: end
    type: end
    title: Fim
"""


def test_status_places_repository_name_immediately_before_cycle(
    tmp_path,
    monkeypatch,
    capsys,
):
    ft_home = tmp_path / "runtime"
    worktree = ft_home / "worktrees" / "meu-repositorio" / "cycle-01"
    worktree.mkdir(parents=True)
    process = worktree / "process.yml"
    process.write_text(PROCESS, encoding="utf-8")
    monkeypatch.setenv("FT_HOME", str(ft_home))

    runner = StepRunner(
        process_path=process,
        state_path=worktree / "state" / "engine_state.yml",
        project_root=worktree,
    )
    runner.init_state()
    capsys.readouterr()

    runner.status()

    output = capsys.readouterr().out
    lines = output.splitlines()
    repository_line = next(
        index for index, line in enumerate(lines)
        if "Repositório: meu-repositorio" in line
    )
    cycle_line = next(
        index for index, line in enumerate(lines)
        if "Ciclo: cycle-01" in line
    )
    title_line = next(line for line in lines if "Process:" in line)

    assert repository_line + 1 == cycle_line
    assert "Repositório:" not in title_line
