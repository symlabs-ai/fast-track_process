"""O template diz o que um node escreve; o projeto diz onde isso mora.

`write_scope` listava palpites de diretório — `project`, `src`, `tests` — e um
template genérico não tem como saber a forma de cada projeto. O palpite errou
de quatro maneiras num único dia: deixou o produto da raiz fora do commit,
criou um `project/tests/` num projeto sem `project/` (onde o pytest nunca
coletava o teste escrito ali), tornou um achado incorrigível porque nenhum node
declarava `docs/PRD.md`, e deixou a reconciliação sem commit.

Papel (`@product`, `@proof`, `@contract`) move a pergunta "onde" para quem sabe
respondê-la: o projeto, em `.ft/project.yml: layout`.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from ft.engine.runner import StepRunner

_PROCESS_YAML = """id: roles_test
version: '1.0.0'
title: Roles test
execution_policy:
  entrypoint: run
  template: roles
nodes:
  - id: build
    type: build
    title: Build
    outputs: [out/]
    write_scope: ["@product", "@proof", "@contract", docs/extra.md]
    next: end
  - id: end
    type: end
    title: End
"""


def _runner(root: Path, layout: dict | None = None) -> StepRunner:
    root.mkdir(parents=True, exist_ok=True)
    process = root / "process.yml"
    process.write_text(_PROCESS_YAML, encoding="utf-8")
    if layout is not None:
        (root / ".ft").mkdir(exist_ok=True)
        (root / ".ft" / "project.yml").write_text(
            yaml.safe_dump(
                {"schema_version": 1, "project_id": "t", "layout": layout},
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
    return StepRunner(
        process_path=process,
        state_path=root / "state" / "engine_state.yml",
        project_root=str(root),
    )


def _escopo(runner: StepRunner) -> list[str]:
    return runner._resolve_allowed_paths(runner.graph.get_node("build"))


def test_o_projeto_declara_onde_o_produto_mora(tmp_path: Path) -> None:
    """O caso do SymProbe: produto na raiz, com src/ e tests/ existindo."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "src").mkdir()
    runner = _runner(
        root, {"product": ["."], "proof": ["checks"], "contract": ["docs/PRD.md"]}
    )

    escopo = _escopo(runner)

    assert escopo == [".", "checks", "docs/PRD.md", "docs/extra.md"]


def test_sem_declaracao_o_palpite_precisa_existir_no_disco(tmp_path: Path) -> None:
    """`src/` existe, então é ele. O palpite é conferido, não presumido."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "src").mkdir()
    (root / "checks").mkdir()

    escopo = _escopo(_runner(root))

    assert "src" in escopo
    assert "checks" in escopo
    assert "project" not in escopo


def test_palpite_que_nao_existe_nao_vira_diretorio_fantasma(tmp_path: Path) -> None:
    """O `project/tests/` que um ciclo criou e o pytest nunca coletou."""
    root = tmp_path / "repo"
    root.mkdir()

    escopo = _escopo(_runner(root))

    assert "project" not in escopo
    assert "src" not in escopo
    assert "." in escopo, "sem palpite válido, o produto é a raiz"


def test_papel_sem_lugar_some_do_escopo(tmp_path: Path) -> None:
    """Papel que o projeto não tem não vira caminho inexistente."""
    root = tmp_path / "repo"
    root.mkdir()
    runner = _runner(root, {"product": ["."], "proof": [], "contract": []})

    escopo = _escopo(runner)

    assert escopo == [".", "docs/extra.md"]


def test_caminho_literal_passa_intacto(tmp_path: Path) -> None:
    """Retrocompatibilidade: todo template existente segue valendo."""
    root = tmp_path / "repo"
    root.mkdir()

    assert "docs/extra.md" in _escopo(_runner(root))


def test_contrato_do_produto_e_alcancavel_por_papel(tmp_path: Path) -> None:
    """O achado que travou um ciclo em 97 voltas exigia escrever o PRD."""
    root = tmp_path / "repo"
    root.mkdir()
    runner = _runner(root, {"contract": ["docs/PRD.md"]})

    assert "docs/PRD.md" in _escopo(runner)
