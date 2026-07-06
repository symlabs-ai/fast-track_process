"""Human gates param de verdade sob --auto (PV-9 vibeos, 2026-07-06).

Regressao dos 3 bypasses consecutivos do aceite final nos cycles 01-03:
`--auto` implicava `_bypass_human_gates=True`, entao o stakeholder nunca
era chamado. Bypass agora exige o flag explicito --bypass-human-gates.
"""

from argparse import Namespace

from ft.cli.main import resolve_bypass_human_gates


def test_auto_nao_bypassa_human_gate():
    args = Namespace(auto=True)
    assert resolve_bypass_human_gates(args) is False


def test_auto_com_flag_explicito_bypassa():
    args = Namespace(auto=True, bypass_human_gates=True)
    assert resolve_bypass_human_gates(args) is True


def test_flag_explicito_sozinho_bypassa():
    args = Namespace(bypass_human_gates=True)
    assert resolve_bypass_human_gates(args) is True


def test_default_nao_bypassa():
    assert resolve_bypass_human_gates(Namespace()) is False


# --- resolve_run_mode: approve --auto avança sozinho (fix do dança de 2 passos)

from ft.cli.main import resolve_run_mode


def test_run_mode_auto_vira_mvp():
    assert resolve_run_mode(Namespace(auto=True)) == "mvp"


def test_run_mode_sprint():
    assert resolve_run_mode(Namespace(auto=False, sprint=True)) == "sprint"


def test_run_mode_default_step():
    assert resolve_run_mode(Namespace()) == "step"


def test_run_mode_auto_vence_sprint():
    assert resolve_run_mode(Namespace(auto=True, sprint=True)) == "mvp"


# --- _cycle_complete: continue num ciclo done não reinicia (footgun) --------

from ft.cli.main import _cycle_complete


class _St:
    def __init__(self, node_status="ready", current_node=None, completed_nodes=None):
        self.node_status = node_status
        self.current_node = current_node
        self.completed_nodes = completed_nodes or []


def test_cycle_complete_por_node_status_done():
    assert _cycle_complete(_St(node_status="done", current_node=None,
                               completed_nodes=["a", "b"])) is True


def test_cycle_complete_current_none_com_nos_feitos():
    assert _cycle_complete(_St(node_status="ready", current_node=None,
                               completed_nodes=["a"])) is True


def test_estado_novo_nunca_rodou_nao_e_completo():
    # fresh: current_node None mas SEM nós completos → pode init/rodar
    assert _cycle_complete(_St(node_status="ready", current_node=None,
                               completed_nodes=[])) is False


def test_ciclo_em_andamento_nao_e_completo():
    assert _cycle_complete(_St(node_status="delegated", current_node="n1",
                               completed_nodes=["a"])) is False
