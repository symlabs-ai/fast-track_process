"""A atestação determinística do feature-fast contra os validadores reais.

`attest_checks.py` substituiu as três revisões por LLM. O que este teste
protege não é o formato dos arquivos, e sim o contrato: os artefatos emitidos
precisam passar exatamente pelos mesmos `validate_feature.py` que cobravam o
LLM, o veredicto precisa vir da execução dos checks, e o ramo de correção
focal precisa continuar alcançável a partir de uma reprovação.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
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
attest = _load("attest_checks")

FEATURE_MD = textwrap.dedent(
    """\
    ---
    type: evolution
    target_feature: FEAT-001
    backlog_item: PB-050
    priority: P1
    interface: internal
    ---

    ## Objetivo
    Expor o relay como servidor executável.

    ## Comportamento Esperado
    O relay sobe por um entrypoint com host e porta configuráveis.

    ## Critérios de Aceite
    - AC-01 — `symprobe relay serve` sobe o servidor.
    - AC-02 — a URL padrão aponta para o relay de produção.

    ## Fora do Escopo
    Provisionamento de infraestrutura.

    ## Restrições
    Sem mudança de contrato do túnel.
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
def feature_root(tmp_path: Path) -> Path:
    """Um projeto no estado exato em que `feature.impact_prepare` termina."""
    root = tmp_path / "produto"
    (root / "docs").mkdir(parents=True)
    (root / "checks").mkdir()
    (root / "src").mkdir()

    (root / "docs/feature.md").write_text(FEATURE_MD, encoding="utf-8")
    (root / "docs/feature-plan.md").write_text(
        "## Plano\n- expor entrypoint\n", encoding="utf-8"
    )
    (root / "docs/feature-request.md").write_text(
        "Feature PB-050: entrypoint do relay.\n", encoding="utf-8"
    )
    (root / "docs/FEATURES.md").write_text(
        "| ID | Nome | Status |\n| --- | --- | --- |\n| FEAT-001 | relay | ativo |\n",
        encoding="utf-8",
    )
    (root / "docs/PROJECT_BACKLOG.md").write_text(
        "| ID | Item | Status |\n| --- | --- | --- |\n"
        "| PB-050 | entrypoint | doing |\n",
        encoding="utf-8",
    )
    (root / "src/relay.py").write_text(
        "def serve(host, port):\n    return (host, port)\n", encoding="utf-8"
    )
    (root / "src/test_relay.py").write_text(
        "from relay import serve\n", encoding="utf-8"
    )

    vf._atomic_write_yaml(
        root / "docs/feature-baseline.yml",
        {
            "version": 2,
            "product_root": "src",
            "project_backlog": [
                {"ID": "PB-050", "Item": "entrypoint", "Status": "doing"}
            ],
            "features": [{"ID": "FEAT-001", "Nome": "relay", "Status": "ativo"}],
        },
    )
    vf._atomic_write_yaml(
        root / "docs/feature-workset.yml",
        {"schema_version": 1, "paths": ["src/relay.py", "src/test_relay.py"]},
    )

    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "baseline")

    vf.prepare_receipt_baseline(root)

    # Delta de implementação: produto e teste mudam depois da baseline.
    (root / "src/relay.py").write_text(
        'DEFAULT_RELAY_URL = "wss://relay.symlabs.ai/sp"\n\n'
        "def serve(host, port):\n    return (host, port)\n",
        encoding="utf-8",
    )
    (root / "src/test_relay.py").write_text(
        "from relay import serve, DEFAULT_RELAY_URL\n", encoding="utf-8"
    )

    (root / "docs/feature-validation.json").write_text(
        json.dumps(
            {
                "fingerprint": "sha256:" + "a" * 64,
                "commands": ["make build", "make verify"],
            }
        ),
        encoding="utf-8",
    )
    vf._atomic_write_yaml(root / vf.IMPACT_PATH, vf._build_impact(root))
    for acceptance_id in ("AC-01", "AC-02"):
        (root / f"checks/{acceptance_id}.py").write_text(
            f"import sys; print('{acceptance_id} provado'); sys.exit(0)\n",
            encoding="utf-8",
        )
    return root


def _attest(root: Path, stage: str, expected: int = 0) -> str:
    done = subprocess.run(
        [sys.executable, str(SCRIPTS / "attest_checks.py"), stage],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert done.returncode == expected, f"{stage}: {done.stdout}{done.stderr}"
    return done.stdout + done.stderr


def _validate(root: Path, mode: str, expected: int = 0) -> str:
    done = subprocess.run(
        [sys.executable, str(SCRIPTS / "validate_feature.py"), mode],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert done.returncode == expected, f"{mode}: {done.stdout}{done.stderr}"
    return done.stdout + done.stderr


def test_attestacao_aprovada_passa_pelos_validadores_reais(feature_root: Path) -> None:
    # A ordem é a do grafo: checks -> evidence_gate -> review_prepare -> verify.
    _attest(feature_root, "pre")
    _validate(feature_root, "pre-review")
    _attest(feature_root, "evidence")
    _validate(feature_root, "evidence")
    _validate(feature_root, "prepare-review")
    _attest(feature_root, "attest")
    _validate(feature_root, "review")

    route = vf._read_yaml(feature_root, vf.REVIEW_ROUTE_PATH)
    assert route["verdict"] == "APPROVED"
    assert route["review_route"] == "approved"


def test_check_que_reprova_rejeita_e_abre_o_fix_focal(feature_root: Path) -> None:
    _attest(feature_root, "pre")
    _attest(feature_root, "evidence")
    _validate(feature_root, "prepare-review")
    (feature_root / "checks/AC-02.py").write_text(
        "import sys; print('URL ainda aponta para relay.symprobe.io'); sys.exit(1)\n",
        encoding="utf-8",
    )
    _attest(feature_root, "attest")
    _validate(feature_root, "review")

    route = vf._read_yaml(feature_root, vf.REVIEW_ROUTE_PATH)
    assert route["verdict"] == "REJECTED"
    assert route["review_route"] == "implementation"

    report = (feature_root / "docs/feature-review.md").read_text(encoding="utf-8")
    assert "F-01" in report
    assert "relay.symprobe.io" in report, "o achado precisa citar a saída do check"
    assert vf._review_ac_statuses(
        report, ["AC-01", "AC-02"], "docs/feature-review.md"
    ) == {"AC-01": "PASS", "AC-02": "FAIL"}

    # O ramo de correção focal continua alcançável a partir da rejeição.
    _validate(feature_root, "prepare-fix")


def test_reentrada_do_fix_reatesta_do_zero(feature_root: Path) -> None:
    _attest(feature_root, "pre")
    _attest(feature_root, "evidence")
    _validate(feature_root, "prepare-review")
    (feature_root / "checks/AC-02.py").write_text(
        "import sys; sys.exit(1)\n", encoding="utf-8"
    )
    _attest(feature_root, "attest")
    assert vf._read_yaml(feature_root, vf.REVIEW_ROUTE_PATH)["verdict"] == "REJECTED"

    # fix -> fix_validate -> fix_full_validate -> review_prepare -> verify
    (feature_root / "checks/AC-02.py").write_text(
        "import sys; print('URL corrigida'); sys.exit(0)\n", encoding="utf-8"
    )
    _validate(feature_root, "prepare-review")
    _attest(feature_root, "attest")
    _validate(feature_root, "review")
    assert vf._read_yaml(feature_root, vf.REVIEW_ROUTE_PATH)["verdict"] == "APPROVED"
    # A evidência derivada antes do fix continua ancorada no mesmo receipt.
    _validate(feature_root, "evidence")


def test_ac_sem_check_reprova_antes_da_suite(feature_root: Path) -> None:
    (feature_root / "checks/AC-02.py").unlink()
    saida = _attest(feature_root, "pre", expected=1)
    assert "cobertura incompleta" in saida and "AC-02" in saida
    assert _attest(feature_root, "attest", expected=1)


def test_check_orfao_reprova(feature_root: Path) -> None:
    (feature_root / "checks/AC-09.py").write_text(
        "import sys; sys.exit(0)\n", encoding="utf-8"
    )
    saida = _attest(feature_root, "pre", expected=1)
    assert "AC-09" in saida and "órf" in saida


def test_check_pendurado_e_falha_e_nao_trava_o_gate(
    feature_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (feature_root / "checks/AC-02.py").write_text(
        "import time; time.sleep(9999)\n", encoding="utf-8"
    )
    monkeypatch.setattr(attest, "CHECK_TIMEOUT", 2)
    passou, evidencia = attest._run_check(
        feature_root, feature_root / "checks/AC-02.py"
    )
    assert passou is False
    assert "timeout" in evidencia


def test_makefile_do_mvp_builder_entra_no_feature_fast(tmp_path, monkeypatch):
    """Um produto entregue pelo mvp-builder-fast chama a suíte de `verify`.

    Antes desta correção o feature-fast exigia literalmente um alvo `test` e
    recusava, no preflight, todo projeto vindo do outro template — os dois
    processos discordavam do nome do mesmo alvo.
    """
    receipt = _load("product_receipt")
    makefile = tmp_path / "Makefile"

    makefile.write_text("build:\n\t@true\nverify: build\n\t@true\n", encoding="utf-8")
    assert receipt.resolve_suite_target(tmp_path, ".") == "verify"
    assert receipt._commands(".", "verify")[1][-1] == "verify"

    # `test` continua vencendo quando os dois existem.
    makefile.write_text(
        "build:\n\t@true\ntest:\n\t@true\nverify:\n\t@true\n", encoding="utf-8"
    )
    assert receipt.resolve_suite_target(tmp_path, ".") == "test"

    # Sem nenhum dos dois, falha fechado em vez de gravar um receipt vazio.
    makefile.write_text("build:\n\t@true\n", encoding="utf-8")
    with pytest.raises(receipt.ReceiptError):
        receipt.resolve_suite_target(tmp_path, ".")


def test_log_do_engine_nao_invalida_o_impacto(feature_root: Path) -> None:
    """O engine escreve `<projeto>_log.md` entre um gate e o seguinte.

    Com `product_root: "."` — o layout que o mvp-builder-fast entrega — esse
    log caía dentro da lane do produto. O impacto passava a se autoinvalidar:
    `impact_prepare` gravava o fingerprint e o próprio ato de registrar a
    passagem do gate mudava o conteúdo hasheado, deixando `feature.checks`
    inalcançável para sempre. O resto do engine já trata `*_log.md` como
    artefato seu; o que este teste protege é que o validador concorde.
    """
    # O produto na raiz, como o mvp-builder-fast entrega.
    vf._atomic_write_yaml(
        feature_root / "docs/feature-baseline.yml",
        {
            "version": 2,
            "product_root": ".",
            "project_backlog": [
                {"ID": "PB-050", "Item": "entrypoint", "Status": "doing"}
            ],
            "features": [{"ID": "FEAT-001", "Nome": "relay", "Status": "ativo"}],
        },
    )
    # O log do engine é não-rastreado e vive na raiz, como no worktree real.
    log = feature_root / "produto_log.md"
    log.write_text("| no | resultado |\n| --- | --- |\n", encoding="utf-8")

    vf.prepare_receipt_baseline(feature_root)
    vf._atomic_write_yaml(feature_root / vf.IMPACT_PATH, vf._build_impact(feature_root))

    # O engine registra a transicao de no: o log cresce entre um gate e o outro.
    with log.open("a", encoding="utf-8") as handle:
        handle.write("| feature.impact_prepare | PASS |\n")

    # O impacto continua valido: o log e do engine, nao do produto.
    vf._current_impact(feature_root)
    _attest(feature_root, "pre")

    # E a garantia inversa: mexer no produto de verdade AINDA invalida.
    (feature_root / "src/relay.py").write_text(
        "def serve(host, port):\n    return None\n", encoding="utf-8"
    )
    with pytest.raises(vf.FeatureValidationError):
        vf._current_impact(feature_root)
