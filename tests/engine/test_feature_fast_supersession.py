"""Substituir a prova de uma feature exige declarar o que acontece com ela."""

from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "templates" / "feature-fast" / "scripts"


def _load(nome: str):
    spec = importlib.util.spec_from_file_location(nome, SCRIPTS / f"{nome}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(nome, module)
    spec.loader.exec_module(module)
    return module


vf = _load("validate_feature")

FEATURE_ID = "FEAT-007"
NAO_REGRESSAO = textwrap.dedent(
    """\

    ## Não-Regressão
    Os checks `checks/FEAT-007/AC-01.py` e `checks/FEAT-007/AC-02.py` do ciclo
    anterior são substituídos; suas garantias seguem reafirmadas no AC-01.
    """
)


def _feature_md(extra: str = "") -> str:
    return (
        textwrap.dedent(
            """\
            ---
            type: evolution
            target_feature: FEAT-007
            backlog_item: PB-053
            priority: P1
            interface: internal
            ---

            ## Objetivo
            Empacotar o produto.

            ## Comportamento Esperado
            `make dist` produz o pacote.

            ## Critérios de Aceite
            - AC-01 — o pacote é produzido.

            ## Fora do Escopo
            Assinatura.

            ## Restrições
            Sem Python no alvo.
            """
        )
        + extra
    )


@pytest.fixture
def projeto(tmp_path: Path) -> Path:
    root = tmp_path / "produto"
    (root / "docs").mkdir(parents=True)
    (root / "docs/feature.md").write_text(_feature_md(), encoding="utf-8")
    (root / "docs/feature-request.md").write_text("PB-053\n", encoding="utf-8")
    (root / "docs/FEATURES.md").write_text(
        "| ID | Nome | Status |\n| --- | --- | --- |\n"
        f"| {FEATURE_ID} | empacotamento | ativo |\n",
        encoding="utf-8",
    )
    (root / "docs/PROJECT_BACKLOG.md").write_text(
        "| ID | Item | Status |\n| --- | --- | --- |\n| PB-053 | pacote | doing |\n",
        encoding="utf-8",
    )
    return root


def _checks_anteriores(root: Path, quantos: int = 2) -> None:
    d = root / "checks" / FEATURE_ID
    d.mkdir(parents=True)
    for i in range(1, quantos + 1):
        (d / f"AC-{i:02d}.py").write_text("import sys\nsys.exit(0)\n", encoding="utf-8")


def test_substituir_check_sem_declarar_reprova(projeto: Path) -> None:
    """O caso FEAT-007: seis AC viram seis AC e as garantias somem."""
    _checks_anteriores(projeto)

    with pytest.raises(vf.FeatureValidationError) as erro:
        vf._assert_supersession_declared(projeto, FEATURE_ID)

    mensagem = str(erro.value)
    assert "Não-Regressão" in mensagem
    assert "checks/FEAT-007/AC-01.py" in mensagem
    assert "checks/FEAT-007/AC-02.py" in mensagem


def test_declaracao_completa_libera(projeto: Path) -> None:
    _checks_anteriores(projeto)
    (projeto / "docs/feature.md").write_text(
        _feature_md(NAO_REGRESSAO), encoding="utf-8"
    )

    vf._assert_supersession_declared(projeto, FEATURE_ID)


def test_declaracao_que_esquece_um_check_reprova(projeto: Path) -> None:
    """Citar 'os checks anteriores' em bloco não conta — cada um pelo caminho."""
    _checks_anteriores(projeto, quantos=3)
    (projeto / "docs/feature.md").write_text(
        _feature_md(NAO_REGRESSAO), encoding="utf-8"
    )

    with pytest.raises(vf.FeatureValidationError) as erro:
        vf._assert_supersession_declared(projeto, FEATURE_ID)

    assert "checks/FEAT-007/AC-03.py" in str(erro.value)
    assert "AC-01" not in str(erro.value).split("substituir:")[1]


def test_feature_sem_check_anterior_nao_exige_nada(projeto: Path) -> None:
    """Uma feature nova não deve carregar cerimônia de evolução."""
    vf._assert_supersession_declared(projeto, FEATURE_ID)


def _com_acs(projeto: Path, linhas: list[str]) -> None:
    """Monta o contrato inteiro que `validate_discovery` exige."""
    texto = _feature_md().replace("- AC-01 — o pacote é produzido.", "\n".join(linhas))
    (projeto / "docs/feature.md").write_text(texto, encoding="utf-8")
    ids = [linha.split("—")[0].strip("- ").strip() for linha in linhas]
    (projeto / "docs/feature-discovery.md").write_text(
        "clarification_status: clear\n\n## Discovery\nEmpacotamento.\n",
        encoding="utf-8",
    )
    (projeto / "docs/feature-questions.md").write_text("nenhuma\n", encoding="utf-8")
    (projeto / "docs/feature-plan.md").write_text(
        "## Plano\n- PB-053 FEAT-007 " + " ".join(ids) + "\n", encoding="utf-8"
    )
    (projeto / "docs/feature-workset.yml").write_text(
        "schema_version: 1\npaths:\n  - docs/feature.md\n", encoding="utf-8"
    )


def _erro_do_teto(projeto: Path) -> str | None:
    try:
        vf.validate_discovery(projeto)
    except vf.FeatureValidationError as exc:
        return str(exc)
    return None


def test_ac_de_nao_regressao_nao_conta_contra_o_teto(projeto: Path) -> None:
    """O teto fatia trabalho novo; dever herdado não é escopo a fatiar.

    Contá-lo empurrava para o pior desenho: com o teto cheio, a única saída
    era comprimir várias garantias herdadas num AC único — e um check por AC
    faz o conjunto inteiro passar ou reprovar junto, destruindo a atribuição
    de falha que é a razão de existir de um check determinístico.
    """
    linhas = [f"- AC-{i:02d} — fatia nova {i}." for i in range(1, 7)]
    linhas += [
        f"- AC-{i:02d} — garantia herdada de PB-052 (não-regressão)."
        for i in range(7, 10)
    ]
    _com_acs(projeto, linhas)

    erro = _erro_do_teto(projeto)

    assert erro is None or "limite de" not in erro


def test_teto_ainda_reprova_excesso_de_ac_novo(projeto: Path) -> None:
    """A isenção não pode virar porta dos fundos para escopo inteiro."""
    _com_acs(projeto, [f"- AC-{i:02d} — fatia nova {i}." for i in range(1, 9)])

    erro = _erro_do_teto(projeto)

    assert erro is not None
    assert "limite de" in erro and "8" in erro


def test_teto_conta_so_os_novos_na_mensagem(projeto: Path) -> None:
    """A mensagem precisa dizer quantos herdados saíram da conta."""
    linhas = [f"- AC-{i:02d} — fatia nova {i}." for i in range(1, 9)]
    linhas += ["- AC-09 — garantia herdada (não-regressão)."]
    _com_acs(projeto, linhas)

    erro = _erro_do_teto(projeto)

    assert erro is not None
    assert "1 de não-regressão não contam" in erro


def test_marcacao_exige_parenteses_como_no_controle_negativo(projeto: Path) -> None:
    """A regra é uma só: o que isenta do controle negativo é o que não conta.

    Duas cópias divergiriam, e um AC isento num lugar e contado no outro é a
    pior das combinações.
    """
    solto = "## Critérios de Aceite\n- AC-01 — fala de não-regressão sem marcar.\n"
    marcado = "## Critérios de Aceite\n- AC-01 — herdado (não-regressão).\n"

    assert vf._non_regression_acs(solto) == set()
    assert vf._non_regression_acs(marcado) == {"AC-01"}
