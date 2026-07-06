"""Heartbeat de `ft log --follow` descreve eventos system com detalhe.

Antes, todo evento `type=system` que não fosse `thinking_tokens` caía num
"evento system" opaco (vibeos, feedback do stakeholder 2026-07-06). Agora o
subtype aparece e o `init` expõe modelo / modo de permissão / nº de tools.
"""

import json

from ft.cli.main import _track_heartbeat


def _track(ev: dict) -> dict:
    ctx = {"desc": ""}
    _track_heartbeat(json.dumps(ev), ctx)
    return ctx


def test_system_init_mostra_modelo_e_tools():
    ctx = _track({
        "type": "system", "subtype": "init",
        "model": "claude-opus-4-8", "permissionMode": "acceptEdits",
        "tools": ["Read", "Edit", "Bash"],
    })
    assert "claude-opus-4-8" in ctx["desc"]
    assert "3 tools" in ctx["desc"]
    assert "acceptEdits" in ctx["desc"]


def test_system_init_sem_permission_mode_nao_quebra():
    ctx = _track({"type": "system", "subtype": "init", "model": "x", "tools": []})
    assert "0 tools" in ctx["desc"]
    assert ctx["desc"].endswith("0 tools)")


def test_system_subtype_desconhecido_mostra_subtype():
    ctx = _track({"type": "system", "subtype": "compact_boundary"})
    assert ctx["desc"] == "evento system/compact_boundary"


def test_system_thinking_tokens_preservado():
    ctx = _track({"type": "system", "subtype": "thinking_tokens", "estimated_tokens": 1234})
    assert "1234" in ctx["desc"]


def test_system_sem_subtype_cai_no_generico():
    ctx = _track({"type": "system"})
    assert ctx["desc"] == "evento system"


def test_thinking_delta_retorna_fragmento():
    ctx = {"desc": ""}
    frag = _track_heartbeat(json.dumps({
        "type": "stream_event",
        "event": {"type": "content_block_delta",
                  "delta": {"type": "thinking_delta", "thinking": "hmm"}},
    }), ctx)
    assert frag == "hmm"
    assert ctx["desc"] == "raciocinando"


def test_linha_nao_json_ignorada():
    ctx = {"desc": "prev"}
    assert _track_heartbeat("não é json", ctx) is None
    assert ctx["desc"] == "prev"
