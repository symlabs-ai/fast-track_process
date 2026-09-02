"""As cópias de `engine_artifacts.py` nos templates são geradas, não mantidas.

Um bundle materializado em `.ft/process/<template>/` roda fora do pacote `ft`
e não consegue importar `ft.engine`. A única forma de os templates
compartilharem a declaração é copiá-la — e cópia mantida à mão foi exatamente
o que produziu o mesmo bug três vezes. Aqui a divergência vira falha de teste
com a instrução de como desfazê-la.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FONTE = ROOT / "ft" / "engine" / "engine_artifacts.py"
TEMPLATES = ("feature-fast", "bug-fast", "tweak")


def _copias() -> list[Path]:
    return [
        ROOT / "templates" / nome / "scripts" / "engine_artifacts.py"
        for nome in TEMPLATES
    ]


@pytest.mark.parametrize("copia", _copias(), ids=TEMPLATES)
def test_copia_do_template_e_identica_a_fonte(copia: Path) -> None:
    assert copia.is_file(), f"template sem a cópia: {copia}"
    assert copia.read_bytes() == FONTE.read_bytes(), (
        f"{copia.relative_to(ROOT)} divergiu da fonte. "
        f"Refaça com: cp {FONTE.relative_to(ROOT)} {copia.relative_to(ROOT)}"
    )


def _amostras_do_pathspec(pathspec: str) -> list[str]:
    """Caminhos concretos que um pathspec de git cobre.

    A tradução é mecânica de propósito: `GIT_RUNTIME_PATHSPECS` decide o que
    sai de um commit automático, e um erro de tradução ali seria invisível se
    o teste usasse uma lista escrita à mão ao lado da lista testada.
    """
    limpo = pathspec.removeprefix(":(glob)")
    if limpo.endswith("/**"):
        limpo = limpo[: -len("/**")] + "/amostra.txt"
    elif limpo.endswith("/"):
        limpo += "amostra.txt"
    profundo = limpo.replace("**/", "pasta/interna/")
    raso = limpo.replace("**/", "")
    return [caminho.replace("*", "x") for caminho in {profundo, raso}]


def test_pathspecs_do_git_sao_reconhecidos_pelo_predicado() -> None:
    """As duas formas da mesma declaração não podem discordar.

    A lista de pathspecs é literal porque pathspec de git tem sintaxe própria;
    isso só é seguro enquanto tudo que ela desindexa o predicado também
    reconhece como artefato da engine.
    """
    from ft.engine.engine_artifacts import GIT_RUNTIME_PATHSPECS, is_engine_artifact

    divergentes = [
        (pathspec, amostra)
        for pathspec in GIT_RUNTIME_PATHSPECS
        for amostra in _amostras_do_pathspec(pathspec)
        if not is_engine_artifact(amostra)
    ]
    assert divergentes == []


#: O que a engine escreve, e o que é do produto. Todo consumidor responde igual.
CORPUS_ENGINE = (
    "state/engine_state.yml",
    "state/trace/events.jsonl",
    "runs/03/state/engine_state.yml",
    ".ft/runtime/lock",
    ".ft/cache/bundle.json",
    ".ft/tmp/x",
    ".ft/logs/run.log",
    "projeto_log.md",
    "fast-track_log.md",
    "sub/projeto_log.md",
    ".serve.pid",
    ".serve_url",
    ".serve_backend.pid",
    "src/.serve.log",
    ".presented_artifact",
    ".presentation.log",
    "app/.node-runs-tmp/a",
    "test-results/spec/.tmp/trace.zip",
)

CORPUS_PRODUTO = (
    "src/relay.py",
    "src/test_relay.py",
    "Makefile",
    "checks/FEAT-007/AC-01.py",
    ".ft/process/feature-fast/process.yml",
    ".ft/cycles/cycle-10/feature.md",
    "app/.tmp/cache",
    "logs/servidor.md",
)


def _carrega(caminho: Path):
    spec = importlib.util.spec_from_file_location(
        f"mod_{caminho.parent.parent.name}", caminho
    )
    assert spec and spec.loader
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


@pytest.mark.parametrize("copia", [FONTE, *_copias()], ids=("fonte", *TEMPLATES))
def test_todas_as_copias_classificam_igual(copia: Path) -> None:
    modulo = _carrega(copia)
    assert [c for c in CORPUS_ENGINE if not modulo.is_engine_artifact(c)] == []
    assert [c for c in CORPUS_PRODUTO if modulo.is_engine_artifact(c)] == []


def test_caminho_que_escapa_da_raiz_nao_ganha_o_beneficio_da_duvida() -> None:
    from ft.engine.engine_artifacts import is_engine_artifact

    for caminho in ("/state/x", "../state/x", "", "a/../state/x"):
        assert is_engine_artifact(caminho) is False, caminho


@pytest.mark.parametrize("copia", [FONTE, *_copias()], ids=("fonte", *TEMPLATES))
def test_project_name_troca_o_sufixo_pelo_log_exato(copia: Path) -> None:
    """O parâmetro só aperta: o sufixo perdoaria um sósia plantado pelo LLM."""
    modulo = _carrega(copia)
    assert modulo.is_engine_artifact("projeto_log.md", project_name="projeto")
    assert not modulo.is_engine_artifact("atacante_log.md", project_name="projeto")
    assert not modulo.is_engine_artifact("sub/projeto_log.md", project_name="projeto")
    # Sem o nome do projeto, o mesmo sósia passa — é o preço da aproximação, e
    # a razão de todo consumidor que conhece a raiz precisar informá-la.
    assert modulo.is_engine_artifact("atacante_log.md")
    # O resto da regra não depende do nome do projeto.
    assert modulo.is_engine_artifact("state/engine_state.yml", project_name="projeto")
    assert modulo.is_engine_artifact(".serve.pid", project_name="projeto")


def test_todo_consumidor_que_conhece_a_raiz_informa_o_nome_do_projeto() -> None:
    """Chamar sem `project_name` onde a raiz existe é o afrouxamento silencioso.

    O `tweak` e o `bug-fast` já tinham a regra exata antes da fonte única. Se
    alguém reescrever uma chamada sem o parâmetro, a garantia deles evapora
    sem que nenhum outro teste perceba — este percebe.
    """
    consumidores = (
        ROOT / "ft" / "engine" / "delegate.py",
        ROOT / "ft" / "engine" / "validators" / "check_paths.py",
        ROOT / "templates" / "tweak" / "scripts" / "validate_tweak.py",
        ROOT / "templates" / "bug-fast" / "scripts" / "validate_bug.py",
        ROOT / "templates" / "feature-fast" / "scripts" / "validate_feature.py",
        ROOT / "templates" / "feature-fast" / "scripts" / "product_receipt.py",
    )
    frouxas: list[str] = []
    for consumidor in consumidores:
        for numero, linha in enumerate(
            consumidor.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if "is_engine_artifact(" not in linha or "def " in linha:
                continue
            if "project_name" not in linha:
                frouxas.append(f"{consumidor.name}:{numero}: {linha.strip()}")
    assert frouxas == [], (
        "estas chamadas conhecem a raiz e não passam project_name: "
        + "; ".join(frouxas)
    )


def test_o_log_que_a_engine_escreve_de_verdade_e_reconhecido(tmp_path: Path) -> None:
    """Os palpites antigos (`cycle-*`, `*.log`) nunca casaram com o nome real.

    Este teste não confia na memória: pergunta ao próprio código da engine
    qual é o nome do log e verifica que o predicado o reconhece.
    """
    from ft.engine.engine_artifacts import is_engine_artifact

    fonte = (ROOT / "ft" / "engine" / "runner.py").read_text(encoding="utf-8")
    assert "_log.md" in fonte, "a engine mudou o nome do log de atividade"
    assert is_engine_artifact("meu-projeto_log.md")
    assert not is_engine_artifact("cycle-01.log"), (
        "o palpite antigo não pode voltar a ser tratado como artefato da engine"
    )
