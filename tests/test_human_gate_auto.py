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
