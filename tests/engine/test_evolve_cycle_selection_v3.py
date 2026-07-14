"""Contrato V3 de seleção de ciclo usado por ``ft evolve``."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ft.engine import evolve, paths


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
    return root


def _active_cycle(project: Path, name: str) -> Path:
    worktree = paths.worktrees_home(project) / name
    state = worktree / "state" / "engine_state.yml"
    state.parent.mkdir(parents=True)
    state.write_text(
        yaml.safe_dump({"current_node": "build", "node_status": "running"}),
        encoding="utf-8",
    )
    return worktree


def _archived_cycle(project: Path, name: str) -> Path:
    archived = paths.project_cycles_dir(project) / name
    archived.mkdir(parents=True)
    return archived


def test_sem_ciclo_disponivel_falha_em_vez_de_usar_docs(project: Path) -> None:
    (project / "docs").mkdir()

    with pytest.raises(evolve.EvolveError, match="nenhum ciclo disponível"):
        evolve.find_cycle_context(project)


@pytest.mark.parametrize("source_kind", ["active", "archived"])
def test_exatamente_um_ciclo_e_inferido(project: Path, source_kind: str) -> None:
    if source_kind == "active":
        expected = _active_cycle(project, "cycle-03-feature")
    else:
        expected = _archived_cycle(project, "cycle-03-feature")

    label, source, _state = evolve.find_cycle_context(project)

    assert "cycle-03-feature" in label
    assert source == expected


def test_multiplos_ciclos_exigem_cycle_e_listam_opcoes(project: Path) -> None:
    _active_cycle(project, "cycle-02-feature")
    _archived_cycle(project, "cycle-07-tweak")

    with pytest.raises(evolve.EvolveError) as captured:
        evolve.find_cycle_context(project)

    message = str(captured.value)
    assert "informe --cycle" in message
    assert "cycle-02-feature" in message
    assert "cycle-07-tweak" in message


def test_cycle_explicito_nao_depende_de_mtime(project: Path) -> None:
    _active_cycle(project, "cycle-02-feature")
    expected = _archived_cycle(project, "cycle-07-tweak")

    label, source, state = evolve.find_cycle_context(
        project, cycle="cycle-07-tweak"
    )

    assert "cycle-07-tweak" in label
    assert source == expected
    assert state is None


def test_cycle_explicito_rejeita_path_traversal(project: Path) -> None:
    with pytest.raises(evolve.EvolveError, match="nome de ciclo"):
        evolve.find_cycle_context(project, cycle="../cycle-01")
