"""O commit de um node de build alcança o produto na raiz do repositório.

`write_scope` enumera palpites de onde o produto mora (`project`, `src`,
`test`, `tests`). Quando o produto é a própria raiz — como em qualquer
projeto cujo `Makefile`, `pyproject.toml` e `packaging/` ficam no topo — nenhum
palpite casa, e o `git add` restrito ao escopo deixava a implementação inteira
de fora do commit sem emitir sinal nenhum. O sintoma aparecia só depois, num
gate a jusante reclamando de outra coisa.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ft.engine.git_ops import auto_commit
from ft.engine.runner import StepRunner

CYCLE_ARTIFACT = "docs/feature.md"


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "tests@example.invalid")
    _git(root, "config", "user.name", "Tests")
    (root / "Makefile").write_text("build:\n\techo base\n", encoding="utf-8")
    _git(root, "add", "Makefile")
    _git(root, "commit", "-qm", "base")


_PROCESS_YAML = """id: commit_scope_test
version: '1.0.0'
title: Commit scope test
execution_policy:
  entrypoint: run
  template: scope
artifact_policy:
  canonical:
    - checks/
  cycle:
    - docs/feature.md
nodes:
  - id: build
    type: build
    title: Build
    outputs: [project/, src/, checks/]
    write_scope: [project, src, checks]
    next: end
  - id: end
    type: end
    title: End
"""


def _runner(root: Path) -> StepRunner:
    process = root / "process.yml"
    process.write_text(_PROCESS_YAML, encoding="utf-8")
    return StepRunner(
        process_path=process,
        state_path=root / "state" / "engine_state.yml",
        project_root=str(root),
    )


def _tracked(root: Path) -> list[str]:
    return _git(root, "ls-tree", "-r", "--name-only", "HEAD").stdout.split()


def _commit_build(root: Path) -> None:
    runner = _runner(root)
    runner._maybe_auto_commit(runner.graph.get_node("build"))


def test_commit_alcanca_o_produto_na_raiz_quando_o_escopo_erra_o_lugar(tmp_path):
    """O caso do PB-053: Makefile e packaging/ sumiam do commit."""
    root = tmp_path / "repo"
    _init_repo(root)
    # O escopo declarado aponta para project/ e src/, que este projeto não tem.
    (root / "checks").mkdir()
    (root / "checks" / "AC-01.py").write_text("# check\n", encoding="utf-8")
    (root / "Makefile").write_text("build:\n\techo base\npackage:\n\techo deb\n")
    (root / "packaging" / "deb").mkdir(parents=True)
    (root / "packaging" / "build_deb.py").write_text("# assembla\n", encoding="utf-8")
    (root / "packaging" / "deb" / "control.in").write_text("Package: x\n")

    _commit_build(root)

    tracked = _tracked(root)
    assert "Makefile" in tracked
    assert "packaging/build_deb.py" in tracked
    assert "packaging/deb/control.in" in tracked
    assert "checks/AC-01.py" in tracked
    assert "package:" in (root / "Makefile").read_text(encoding="utf-8")
    assert "package" in _git(root, "show", "HEAD:Makefile").stdout


def test_commit_na_raiz_nao_arrasta_artefato_de_ciclo(tmp_path):
    """Alcançar a raiz não pode significar varrer os descartáveis do ciclo."""
    root = tmp_path / "repo"
    _init_repo(root)
    (root / "checks").mkdir()
    (root / "checks" / "AC-01.py").write_text("# check\n", encoding="utf-8")
    (root / "packaging").mkdir()
    (root / "packaging" / "build_deb.py").write_text("# assembla\n", encoding="utf-8")
    (root / "docs").mkdir()
    (root / CYCLE_ARTIFACT).write_text("# contrato do ciclo\n", encoding="utf-8")
    (root / "state").mkdir()
    (root / "state" / "engine_state.yml").write_text("runtime\n", encoding="utf-8")
    (root / f"{root.name}_log.md").write_text("log da engine\n", encoding="utf-8")

    _commit_build(root)

    tracked = _tracked(root)
    assert "packaging/build_deb.py" in tracked
    assert CYCLE_ARTIFACT not in tracked
    assert "state/engine_state.yml" not in tracked
    assert f"{root.name}_log.md" not in tracked


def test_escopo_que_acerta_o_diretorio_do_produto_segue_restrito(tmp_path):
    """Se src/ existe, o escopo declarado está certo e nada mais entra."""
    root = tmp_path / "repo"
    _init_repo(root)
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    (root / "avulso.txt").write_text("fora do escopo\n", encoding="utf-8")

    _commit_build(root)

    tracked = _tracked(root)
    assert "src/app.py" in tracked
    assert "avulso.txt" not in tracked


def test_auto_commit_denuncia_pathspec_que_nao_casou_com_nada(tmp_path):
    """O `git add` que falha em silêncio foi o que escondeu o defeito."""
    root = tmp_path / "repo"
    _init_repo(root)
    (root / "novo.txt").write_text("conteudo\n", encoding="utf-8")

    ok, detail = auto_commit("commit", str(root), paths=["novo.txt", "project"])

    assert ok, detail
    assert "project" in detail
    assert "novo.txt" in _tracked(root)
