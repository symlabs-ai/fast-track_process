"""A rota da rejeição é escolhida pelo que foi achado, não por quem achou.

O gate de aceite mandava toda rejeição do stakeholder de volta ao node que
implementa, com a justificativa de que ela pode trazer requisito semântico
novo. Em dois ciclos reais a rejeição apontou defeito em check — a prova que
não provava — e a implementação voltou byte-idêntica depois de um restart
completo: caro, e arriscado, porque reescreve código correto sem necessidade.

Agora o gate declara os dois destinos e quem rejeita classifica. Escolher em
silêncio erraria dos dois lados: o caminho barato aplicado a um requisito novo
entrega a coisa errada, e o caro aplicado a um defeito de check reinicia a
implementação para trocar duas linhas.
"""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ft.cli import main as cli_main
from ft.engine.graph import Node, load_graph

ROOT = Path(__file__).resolve().parents[2]
FAST_PROCESS = ROOT / "templates" / "feature-fast" / "process.yml"


def _gate(**kwargs) -> Node:
    base = {
        "id": "acceptance",
        "type": "human_gate",
        "title": "Aceite",
        "reject_next": "fix_prepare",
    }
    base.update(kwargs)
    return Node(**base)


class _Runner:
    def __init__(self, gate: Node) -> None:
        self.graph = SimpleNamespace(get_node=lambda _id: gate)
        self.state_mgr = SimpleNamespace(
            load=lambda: SimpleNamespace(pending_approval="acceptance")
        )
        self.chamadas: list[bool] = []
        self.rodou = False

    def reject_with_origin_audit(self, reason: str, *, new_requirement: bool = False):
        self.chamadas.append(new_requirement)
        return True

    def run(self, mode: str) -> None:
        self.rodou = True


def _args(**kwargs) -> Namespace:
    base = {
        "reason": "onde; passos; esperado; observado",
        "no_retry": False,
        "audit_origin": False,
        "auto": False,
        "defect": False,
        "new_requirement": False,
        "cycle": None,
        "verbose": False,
    }
    base.update(kwargs)
    return Namespace(**base)


def _rejeitar(gate: Node, **kwargs):
    runner = _Runner(gate)
    with (
        patch("ft.cli.main.get_runner", return_value=runner),
        patch("ft.cli.main._ensure_runtime_selected", return_value=True),
        patch("ft.cli.main.resolve_llm_engine", return_value="claude"),
        patch("ft.cli.main.resolve_llm_model", return_value=None),
        patch("ft.cli.main.resolve_llm_effort", return_value=None),
    ):
        cli_main.cmd_reject(_args(**kwargs))
    return runner


def test_defeito_vai_para_a_correcao_focal(capsys):
    gate = _gate(reject_next_new_requirement="implement")

    runner = _rejeitar(gate, defect=True)

    assert runner.chamadas == [False]
    assert runner.rodou


def test_requisito_novo_volta_para_a_implementacao(capsys):
    gate = _gate(reject_next_new_requirement="implement")

    runner = _rejeitar(gate, new_requirement=True)

    assert runner.chamadas == [True]


def test_sem_classificacao_o_comando_recusa_e_explica(capsys):
    """Silêncio aqui erraria dos dois lados; a decisão tem que ser consciente."""
    gate = _gate(reject_next_new_requirement="implement")

    runner = _rejeitar(gate)

    assert runner.chamadas == []
    assert not runner.rodou
    saida = capsys.readouterr().out
    assert "--defect" in saida and "fix_prepare" in saida
    assert "--new-requirement" in saida and "implement" in saida


def test_gate_com_um_destino_so_nao_exige_classificacao(capsys):
    """Retrocompatível: gate que não declara o segundo destino segue como era."""
    runner = _rejeitar(_gate())

    assert runner.chamadas == [False]


def test_o_alvo_escolhido_pelo_runner_respeita_a_classificacao():
    from ft.engine.runner import StepRunner

    gate = _gate(reject_next_new_requirement="implement")
    escolher = StepRunner._reject_target

    assert escolher(None, gate, False) == "fix_prepare"
    assert escolher(None, gate, True) == "implement"
    # Sem o segundo destino declarado, requisito novo não inventa rota.
    assert escolher(None, _gate(), True) == "fix_prepare"


def test_o_template_declara_os_dois_destinos_no_aceite():
    graph = load_graph(str(FAST_PROCESS))
    gate = graph.get_node("feature.acceptance")

    assert gate.reject_next == "feature.fix_prepare"
    assert gate.reject_next_new_requirement == "feature.implement"
