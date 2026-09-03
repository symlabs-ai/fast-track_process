"""O gate de implementação reconhece o check determinístico como verificação.

`validate_implementation` exige que a implementação traga, junto com o
produto, algo que prove o comportamento. A regra era uma substring: algum
caminho mudado precisava conter `test` ou `spec`. Ela é anterior ao desenho em
que toda obrigação embarca o seu `checks/<FEAT-NNN>/AC-NNN.py`.

O caso real: uma feature de empacotamento num projeto cujo produto é a raiz.
As mudanças foram `Makefile` e `packaging/`, e os seis AC vieram provados em
`checks/FEAT-007/`. O gate reprovou com "implementação não alterou nenhum
arquivo de teste" — a feature cumpria o contrato do template à risca, e o
caminho para desbloquear era escrever um teste decorativo.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "templates" / "feature-fast" / "scripts"

FEATURE_ID = "FEAT-007"
CHECKS = f"checks/{FEATURE_ID}"


def _load(nome: str):
    spec = importlib.util.spec_from_file_location(nome, SCRIPTS / f"{nome}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(nome, module)
    spec.loader.exec_module(module)
    return module


vf = _load("validate_feature")

FEATURE_MD = textwrap.dedent(
    """\
    ---
    type: evolution
    target_feature: FEAT-007
    backlog_item: PB-053
    priority: P1
    interface: internal
    ---

    ## Objetivo
    Empacotar o produto como `.deb`.

    ## Comportamento Esperado
    `make dist` produz o pacote a partir de uma árvore limpa.

    ## Critérios de Aceite
    - AC-01 — `make dist` emite o `.deb` em `dist/`.

    ## Fora do Escopo
    Assinatura do repositório APT.

    ## Restrições
    Sem dependência de Python no alvo.
    """
)


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


@pytest.fixture
def produto_na_raiz(tmp_path: Path) -> Path:
    """Um projeto cujo produto é a própria raiz, na baseline do ciclo."""
    root = tmp_path / "sym_probe"
    (root / "docs").mkdir(parents=True)

    (root / "docs/feature.md").write_text(FEATURE_MD, encoding="utf-8")
    (root / "docs/feature-request.md").write_text(
        "Feature PB-053: instalador nativo.\n", encoding="utf-8"
    )
    (root / "docs/FEATURES.md").write_text(
        "| ID | Nome | Status |\n| --- | --- | --- |\n"
        f"| {FEATURE_ID} | empacotamento | ativo |\n",
        encoding="utf-8",
    )
    (root / "docs/PROJECT_BACKLOG.md").write_text(
        "| ID | Item | Status |\n| --- | --- | --- |\n"
        "| PB-053 | instalador | doing |\n",
        encoding="utf-8",
    )
    (root / "Makefile").write_text("build:\n\techo base\n", encoding="utf-8")

    vf._atomic_write_yaml(
        root / "docs/feature-baseline.yml",
        {
            "version": 2,
            "product_root": ".",
            "project_backlog": [
                {"ID": "PB-053", "Item": "instalador", "Status": "doing"}
            ],
            "features": [
                {"ID": FEATURE_ID, "Nome": "empacotamento", "Status": "ativo"}
            ],
        },
    )

    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "baseline")
    return root


def _implementar_empacotamento(root: Path) -> None:
    """O delta real do ciclo: nenhum path com `test` ou `spec` no nome."""
    (root / "Makefile").write_text(
        "build:\n\techo base\ndist:\n\tpython packaging/build_deb.py\n",
        encoding="utf-8",
    )
    (root / "packaging").mkdir()
    (root / "packaging/build_deb.py").write_text("# assembla\n", encoding="utf-8")


def _provar_com_check(root: Path) -> None:
    (root / CHECKS).mkdir(parents=True)
    (root / CHECKS / "AC-01.py").write_text(
        "import sys\nsys.exit(0)\n", encoding="utf-8"
    )


def test_check_deterministico_conta_como_verificacao(produto_na_raiz: Path) -> None:
    """O caso do PB-053: produto na raiz, garantias provadas em `checks/`."""
    _implementar_empacotamento(produto_na_raiz)
    _provar_com_check(produto_na_raiz)

    vf.validate_implementation(produto_na_raiz)


def test_implementacao_sem_prova_alguma_continua_reprovando(
    produto_na_raiz: Path,
) -> None:
    """Aceitar `checks/` não pode virar aceitar produto cru."""
    _implementar_empacotamento(produto_na_raiz)

    with pytest.raises(vf.FeatureValidationError) as erro:
        vf.validate_implementation(produto_na_raiz)

    assert "verificação" in str(erro.value)


def test_reprovacao_nomeia_os_caminhos_que_o_gate_viu(produto_na_raiz: Path) -> None:
    """Sem a lista, diagnosticar o bloqueio custa uma sessão inteira."""
    _implementar_empacotamento(produto_na_raiz)

    with pytest.raises(vf.FeatureValidationError) as erro:
        vf.validate_implementation(produto_na_raiz)

    mensagem = str(erro.value)
    assert "Makefile" in mensagem
    assert "packaging/build_deb.py" in mensagem


def test_suite_do_projeto_continua_valendo_como_verificacao(
    produto_na_raiz: Path,
) -> None:
    """O caminho convencional não pode ter sido trocado pelo novo."""
    _implementar_empacotamento(produto_na_raiz)
    (produto_na_raiz / "tests").mkdir()
    (produto_na_raiz / "tests/test_build_deb.py").write_text(
        "def test_x():\n    assert True\n", encoding="utf-8"
    )

    vf.validate_implementation(produto_na_raiz)


def test_nada_mudou_reprova_nomeando_a_raiz(produto_na_raiz: Path) -> None:
    """Com o produto na raiz, a mensagem antiga dizia 'nenhum arquivo em ./'."""
    with pytest.raises(vf.FeatureValidationError) as erro:
        vf.validate_implementation(produto_na_raiz)

    assert "raiz do repositório" in str(erro.value)
