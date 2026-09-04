"""Um ciclo que volta ao mesmo ponto sem mudar nada precisa parar sozinho.

Rotear para trás é o desenho funcionando: a revisão apurou algo e o ciclo
volta para corrigir. O que não é desenho é voltar de novo com a árvore
idêntica — nenhum commit novo, nenhum arquivo durável alterado.

O caso real: um AC exigia emendar `docs/PRD.md` e nenhum node do processo
declarava esse caminho no `write_scope`. Todos os gates passavam, a rota
estava correta, e o ciclo deu 97 voltas em cinco horas com o arquivo intocado.
Nada dizia "isto não converge", porque nada estava falhando — o teto de
`on_fail` só cobre node que falha.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ft.engine.runner import StepRunner

CYCLE_ARTIFACT = "docs/review.yml"

_PROCESS_YAML = """id: brake_test
version: '1.0.0'
title: Brake test
execution_policy:
  entrypoint: run
  template: brake
artifact_policy:
  canonical:
    - src/
  cycle:
    - docs/review.yml
nodes:
  - id: build
    type: build
    title: Build
    outputs: [src/]
    write_scope: [src]
    next: decide
  - id: decide
    type: decision
    title: Decide
    condition: verdict
    branches:
      rejected: build
      approved: end
    next: end
  - id: end
    type: end
    title: End
"""


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    )


def _runner(root: Path) -> StepRunner:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    (root / "src").mkdir(exist_ok=True)
    (root / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    process = root / "process.yml"
    process.write_text(_PROCESS_YAML, encoding="utf-8")
    runner = StepRunner(
        process_path=process,
        state_path=root / "state" / "engine_state.yml",
        project_root=str(root),
    )
    runner.init_state()
    runner._refresh_commit_baseline()
    return runner


def _voltar(runner: StepRunner) -> bool:
    node = runner.graph.get_node("decide")
    return runner._rewind_without_progress(node, "build")


def test_tres_voltas_sem_nada_mudar_bloqueiam(tmp_path: Path) -> None:
    """O caso do PB-058: 97 voltas com o arquivo alvo intocado."""
    runner = _runner(tmp_path / "repo")

    assert _voltar(runner) is False
    assert _voltar(runner) is False
    assert _voltar(runner) is True

    state = runner.state_mgr.load()
    assert state.node_status == "blocked"
    assert "não está convergindo" in (state.blocked_reason or "")
    assert "write_scope" in (state.blocked_reason or "")


def test_volta_que_mudou_o_produto_nao_bloqueia(tmp_path: Path) -> None:
    """Correção focal legítima leva uma ou duas rodadas e avança a cada uma."""
    root = tmp_path / "repo"
    runner = _runner(root)

    for i in range(6):
        assert _voltar(runner) is False
        (root / "src" / "app.py").write_text(f"x = {i}\n", encoding="utf-8")


def test_commit_novo_conta_como_progresso(tmp_path: Path) -> None:
    """Trabalho commitado some do `git status`; o HEAD é quem o denuncia."""
    root = tmp_path / "repo"
    runner = _runner(root)

    assert _voltar(runner) is False
    (root / "src" / "app.py").write_text("y = 2\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "correcao")

    assert _voltar(runner) is False
    assert _voltar(runner) is False


def test_regenerar_artefato_de_ciclo_nao_conta_como_progresso(tmp_path: Path) -> None:
    """O relatório da revisão é reescrito a cada volta — e não é correção.

    Sem ignorar os descartáveis, o freio nunca dispararia: toda volta produz um
    `docs/review.yml` diferente e pareceria progresso.
    """
    root = tmp_path / "repo"
    runner = _runner(root)
    (root / "docs").mkdir(exist_ok=True)

    for i in range(2):
        (root / CYCLE_ARTIFACT).write_text(f"verdict: {i}\n", encoding="utf-8")
        assert _voltar(runner) is False
    (root / CYCLE_ARTIFACT).write_text("verdict: 3\n", encoding="utf-8")

    assert _voltar(runner) is True


def test_assinatura_muda_quando_um_commit_aparece(tmp_path: Path) -> None:
    """Trabalho commitado deixa a árvore limpa; só o HEAD o denuncia.

    Sem a componente do commit, uma correção que foi commitada pareceria não
    ter mudado nada — e o freio pararia justamente o ciclo que estava
    convergindo.
    """
    root = tmp_path / "repo"
    runner = _runner(root)

    antes = runner._progress_signature()
    (root / "src" / "app.py").write_text("z = 9\n", encoding="utf-8")
    # Só o arquivo do produto: `add -A` arrastaria o `process.yml` untracked
    # para dentro do commit e mudaria o conjunto durável por acidente, fazendo
    # o teste passar sem depender do HEAD — que é justamente o que ele prova.
    _git(root, "add", "src/app.py")
    _git(root, "commit", "-qm", "correcao")

    depois = runner._progress_signature()
    sujos = {c for c in runner._worktree_snapshot()}
    assert "src/app.py" not in sujos, "árvore precisa estar limpa do produto"
    assert depois != antes


def test_decision_para_tras_passa_pelo_freio(tmp_path: Path) -> None:
    """O freio precisa estar ligado ao roteamento, não só existir.

    Este teste exercita `_run_decision`, que é quem detecta a volta para trás.
    Sem ele, remover a chamada ao freio não reprovaria nada.
    """
    root = tmp_path / "repo"
    runner = _runner(root)
    rewinds: list[str] = []
    runner._rewind_to_node = lambda target, motivo: rewinds.append(target)

    node = runner.graph.get_node("decide")
    for _ in range(3):
        state = runner.state_mgr.state
        state.artifacts["verdict"] = "rejected"
        runner.state_mgr.save()
        runner._run_decision(node)

    assert rewinds == ["build", "build"], rewinds
    assert runner.state_mgr.load().node_status == "blocked"
