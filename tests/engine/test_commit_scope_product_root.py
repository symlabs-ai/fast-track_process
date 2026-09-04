"""O commit de um node é o delta desse node, não a permissão do LLM.

`write_scope` diz onde o LLM pode escrever, e é orientação de prompt, não
barreira: um node cujo escopo era `['project', 'src', 'checks']` alterou o
`Makefile` da raiz. Usá-lo como pathspec de `git add` fazia o commit descrever
a permissão em vez do delta, e o que caía fora ficava de fora — em silêncio,
porque `git add` num pathspec que não casa só falha para quem lê o código de
saída.

O custo é destrutivo: `cmd_close` mergeia *commits* e depois roda
`git worktree remove --force`. Um único ciclo real perdeu duas coisas por
isso, ambas resgatadas à mão: o produto, que morava em `Makefile` e
`packaging/`, e a reconciliação que marca o item do backlog como entregue —
escrita por um node `document`, que sequer estava na lista de tipos que
commitam.

A primeira tentativa de correção errou duas vezes. Assumiu que "produto na
raiz" equivalia a "nenhum de `project`/`src`/`test`/`tests` existe", e um
projeto real tem `src/`, `tests/` **e** produto na raiz. Depois tentou varrer
a árvore inteira, o que atropela dois invariantes que a suíte já protegia: um
node que não mudou nada não pode gerar commit, e um arquivo que a engine
copiou para a worktree como entrada tem de permanecer untracked.

Comparar com a foto tirada antes resolve os dois casos sem varrer nada.
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
  - id: reconcile
    type: document
    title: Reconcile
    outputs: [docs/reconciliation.yml]
    write_scope: [docs/reconciliation.yml]
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


def _commit_build(root: Path, escrever) -> None:
    """Foto, depois a escrita do node, depois o commit — nessa ordem.

    A ordem é o teste: tirar a foto depois da escrita põe o produto na
    baseline e o delta sai vazio, que é o modo mais fácil de escrever um teste
    que passa sem provar nada.
    """
    runner = _runner(root)
    runner._refresh_commit_baseline()
    escrever()
    runner._maybe_auto_commit(runner.graph.get_node("build"))


def test_commit_alcanca_o_produto_na_raiz_quando_o_escopo_erra_o_lugar(tmp_path):
    """O caso do PB-053: Makefile e packaging/ sumiam do commit."""
    root = tmp_path / "repo"
    _init_repo(root)

    def escrever() -> None:
        # O escopo declarado aponta para project/ e src/, que este projeto não tem.
        (root / "checks").mkdir()
        (root / "checks" / "AC-01.py").write_text("# check\n", encoding="utf-8")
        (root / "Makefile").write_text("build:\n\techo base\npackage:\n\techo deb\n")
        (root / "packaging" / "deb").mkdir(parents=True)
        (root / "packaging" / "build_deb.py").write_text(
            "# assembla\n", encoding="utf-8"
        )
        (root / "packaging" / "deb" / "control.in").write_text("Package: x\n")

    _commit_build(root, escrever)

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

    def escrever() -> None:
        (root / "checks").mkdir()
        (root / "checks" / "AC-01.py").write_text("# check\n", encoding="utf-8")
        (root / "packaging").mkdir()
        (root / "packaging" / "build_deb.py").write_text(
            "# assembla\n", encoding="utf-8"
        )
        (root / "docs").mkdir()
        (root / CYCLE_ARTIFACT).write_text("# contrato do ciclo\n", encoding="utf-8")
        (root / "state").mkdir()
        (root / "state" / "engine_state.yml").write_text("runtime\n", encoding="utf-8")
        (root / f"{root.name}_log.md").write_text("log da engine\n", encoding="utf-8")

    _commit_build(root, escrever)

    tracked = _tracked(root)
    assert "packaging/build_deb.py" in tracked
    assert CYCLE_ARTIFACT not in tracked
    assert "state/engine_state.yml" not in tracked
    assert f"{root.name}_log.md" not in tracked


def test_produto_na_raiz_conta_mesmo_com_src_e_tests(tmp_path):
    """A forma real do projeto, que a primeira correção não pegava.

    `src/` e `tests/` existem **e** a raiz é produto. A heurística de palpites
    concluía, por `src/` existir, que o escopo declarado estava certo, e o
    produto da raiz seguia fora do commit.
    """
    root = tmp_path / "repo"
    _init_repo(root)

    def escrever() -> None:
        (root / "src").mkdir()
        (root / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
        (root / "tests").mkdir()
        (root / "tests" / "test_app.py").write_text(
            "def test_x(): pass\n", encoding="utf-8"
        )
        (root / "Makefile").write_text("build:\n\techo base\npackage:\n\techo deb\n")
        (root / "packaging").mkdir()
        (root / "packaging" / "build_deb.py").write_text(
            "# assembla\n", encoding="utf-8"
        )

    _commit_build(root, escrever)

    tracked = _tracked(root)
    assert "src/app.py" in tracked
    assert "tests/test_app.py" in tracked
    assert "packaging/build_deb.py" in tracked
    assert "package" in _git(root, "show", "HEAD:Makefile").stdout


def test_auto_commit_denuncia_pathspec_que_nao_casou_com_nada(tmp_path):
    """O `git add` que falha em silêncio foi o que escondeu o defeito."""
    root = tmp_path / "repo"
    _init_repo(root)
    (root / "novo.txt").write_text("conteudo\n", encoding="utf-8")

    ok, detail = auto_commit("commit", str(root), paths=["novo.txt", "project"])

    assert ok, detail
    assert "project" in detail
    assert "novo.txt" in _tracked(root)


def test_node_document_commita_a_reconciliacao_que_escreveu(tmp_path):
    """O segundo resgate manual: `document` não commitava nada.

    O node que marca o item do backlog como entregue declara apenas os seus
    dois artefatos descartáveis, e escreve os canônicos. Sem commit, o
    `ft close` mergeia e apaga: o projeto passa a registrar como não entregue
    uma feature aceita, sem conflito e sem aviso.
    """
    root = tmp_path / "repo"
    _init_repo(root)
    (root / "docs").mkdir()
    (root / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")
    (root / "docs" / "BACKLOG.md").write_text("| PB-01 | ready |\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "canonicos")

    runner = _runner(root)
    runner._refresh_commit_baseline()
    (root / "docs" / "BACKLOG.md").write_text(
        "| PB-01 | accepted |\n", encoding="utf-8"
    )
    (root / "CHANGELOG.md").write_text("# Changelog\n\n- entrega\n", encoding="utf-8")
    (root / CYCLE_ARTIFACT).write_text("# descartavel\n", encoding="utf-8")
    runner._maybe_auto_commit(runner.graph.get_node("reconcile"))

    commitado = _git(root, "show", "HEAD:docs/BACKLOG.md").stdout
    assert "accepted" in commitado
    assert "entrega" in _git(root, "show", "HEAD:CHANGELOG.md").stdout
    assert CYCLE_ARTIFACT not in _tracked(root)


def test_entrada_copiada_pela_engine_nao_entra_no_commit(tmp_path):
    """O invariante que a varredura quebrava.

    `ft run` copia a demanda para dentro da worktree depois que ela nasce. O
    arquivo está sujo desde antes do node rodar, e não é delta de node nenhum.
    """
    root = tmp_path / "repo"
    _init_repo(root)
    (root / "request.md").write_text("demanda\n", encoding="utf-8")

    runner = _runner(root)
    runner._refresh_commit_baseline()
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    runner._maybe_auto_commit(runner.graph.get_node("build"))

    tracked = _tracked(root)
    assert "src/app.py" in tracked
    assert "request.md" not in tracked


def test_node_que_nao_mudou_nada_nao_gera_commit(tmp_path):
    """Um node pulado por pre-seed não pode inventar um commit."""
    root = tmp_path / "repo"
    _init_repo(root)
    (root / "request.md").write_text("demanda\n", encoding="utf-8")

    runner = _runner(root)
    runner._refresh_commit_baseline()
    runner._maybe_auto_commit(runner.graph.get_node("build"))

    assert _git(root, "log", "-1", "--pretty=%s").stdout.strip() == "base"
    assert "request.md" not in _tracked(root)


def test_delta_pega_conteudo_novo_com_o_mesmo_status(tmp_path):
    """Arquivo já sujo antes do node, cujo conteúdo o node mudou.

    O status porcelain continua ` M` nos dois momentos; só o conteúdo denuncia.
    """
    root = tmp_path / "repo"
    _init_repo(root)
    (root / "Makefile").write_text("build:\n\techo sujo\n", encoding="utf-8")

    runner = _runner(root)
    runner._refresh_commit_baseline()
    (root / "Makefile").write_text("build:\n\techo sujo\npackage:\n\techo deb\n")
    runner._maybe_auto_commit(runner.graph.get_node("build"))

    assert "package" in _git(root, "show", "HEAD:Makefile").stdout


def test_guard_denuncia_o_que_o_node_escreveu_e_o_commit_nao_levou(tmp_path, capsys):
    """O sintoma dos dois resgates manuais, agora visível no ato.

    Com o escopo do commit sendo o delta, isto é vazio por construção. Quando
    não é, é o defeito — e é um defeito que se manifesta longe da causa: num
    ciclo real ele só apareceu no fim, depois de um `✓ COMMIT` na tela, e o
    `git worktree remove --force` do `ft close` apagaria a entrega.
    """
    root = tmp_path / "repo"
    _init_repo(root)
    runner = _runner(root)
    runner._refresh_commit_baseline()
    (root / "packaging").mkdir()
    (root / "packaging" / "build_deb.py").write_text("# assembla\n", encoding="utf-8")

    # Escopo do commit volta a ser só a permissão declarada, como era antes.
    runner._commit_pathspecs = lambda node: runner._resolve_allowed_paths(node)
    runner._maybe_auto_commit(runner.graph.get_node("build"))

    saida = capsys.readouterr().out
    assert "packaging/build_deb.py" in saida
    assert "FORA do commit" in saida
    assert "packaging/build_deb.py" not in _tracked(root)


def test_guard_cala_quando_o_commit_levou_tudo(tmp_path, capsys):
    """Um guard que grita no caminho normal é um guard que ninguém lê."""
    root = tmp_path / "repo"
    _init_repo(root)
    runner = _runner(root)
    runner._refresh_commit_baseline()
    (root / "packaging").mkdir()
    (root / "packaging" / "build_deb.py").write_text("# assembla\n", encoding="utf-8")
    (root / "docs").mkdir()
    (root / CYCLE_ARTIFACT).write_text("# descartavel\n", encoding="utf-8")

    runner._maybe_auto_commit(runner.graph.get_node("build"))

    saida = capsys.readouterr().out
    assert "packaging/build_deb.py" in _tracked(root)
    assert "FORA do commit" not in saida


def test_guard_nao_cobra_o_que_a_engine_copiou_como_entrada(tmp_path, capsys):
    """A demanda que o `ft run` põe na worktree não é delta de node nenhum."""
    root = tmp_path / "repo"
    _init_repo(root)
    (root / "request.md").write_text("demanda\n", encoding="utf-8")
    runner = _runner(root)
    runner._refresh_commit_baseline()
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")

    runner._maybe_auto_commit(runner.graph.get_node("build"))

    saida = capsys.readouterr().out
    assert "FORA do commit" not in saida
    assert "request.md" not in _tracked(root)
