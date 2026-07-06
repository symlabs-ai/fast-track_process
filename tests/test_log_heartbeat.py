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
    assert ctx["desc"] == "pensando (~1234 tokens)"  # sem trecho quando não há raciocínio


def test_thinking_tokens_anexa_trecho_do_raciocinio():
    ctx = {"desc": ""}
    # primeiro chega o raciocínio (thinking_delta), depois a contagem de tokens
    _track_heartbeat(json.dumps({
        "type": "stream_event",
        "event": {"type": "content_block_delta",
                  "delta": {"type": "thinking_delta",
                            "thinking": "analisando o call site do runner"}},
    }), ctx)
    _track_heartbeat(json.dumps({
        "type": "system", "subtype": "thinking_tokens", "estimated_tokens": 5600,
    }), ctx)
    assert ctx["desc"].startswith("pensando (~5600 tokens): …")
    assert "call site do runner" in ctx["desc"]


def test_system_sem_subtype_cai_no_generico():
    ctx = _track({"type": "system"})
    assert ctx["desc"] == "evento system"


def test_result_sucesso_mostra_turnos_tempo_custo():
    ctx = _track({
        "type": "result", "subtype": "success", "is_error": False,
        "num_turns": 12, "duration_ms": 82763, "total_cost_usd": 0.8166435,
    })
    assert ctx["desc"] == "resultado ok — 12 turnos · 82.8s · US$ 0.82"


def test_result_erro_mostra_subtype_e_marca_erro():
    ctx = _track({
        "type": "result", "subtype": "error_max_turns", "is_error": True,
        "num_turns": 40, "duration_ms": 120000, "total_cost_usd": 2.5,
    })
    assert ctx["desc"].startswith("resultado com erro")
    assert "error_max_turns" in ctx["desc"]
    assert "US$ 2.50" in ctx["desc"]


def test_result_sem_campos_opcionais_nao_quebra():
    ctx = _track({"type": "result", "is_error": False})
    assert ctx["desc"] == "resultado ok"


def _assistant(blocks):
    return _track({"type": "assistant", "message": {"role": "assistant", "content": blocks}})


def test_assistant_tool_use_arquivo_mostra_basename():
    ctx = _assistant([{"type": "tool_use", "name": "Edit",
                       "input": {"file_path": "/home/x/project/app/api/projects.py"}}])
    assert ctx["desc"] == "Edit: projects.py"


def test_assistant_tool_use_bash_mostra_comando():
    ctx = _assistant([{"type": "tool_use", "name": "Bash",
                       "input": {"command": "python -m pytest -q"}}])
    assert ctx["desc"] == "Bash: python -m pytest -q"


def test_assistant_tool_use_comando_longo_truncado():
    cmd = "echo " + "a" * 200
    ctx = _assistant([{"type": "tool_use", "name": "Bash", "input": {"command": cmd}}])
    assert ctx["desc"].startswith("Bash: ")
    assert len(ctx["desc"]) <= len("Bash: ") + 60


def test_assistant_texto_mostra_trecho():
    ctx = _assistant([{"type": "text", "text": "Now let me run the suite\nto confirm"}])
    assert ctx["desc"] == "escrevendo: Now let me run the suite to confirm"


def test_assistant_so_thinking_mostra_trecho():
    ctx = _assistant([{"type": "thinking", "thinking": "analisando o runner"}])
    assert ctx["desc"] == "raciocinando: …analisando o runner"


def test_assistant_thinking_vazio_cai_no_generico():
    ctx = _assistant([{"type": "thinking", "thinking": ""}])
    assert ctx["desc"] == "raciocinando"


def test_assistant_vazio_cai_no_generico():
    ctx = _assistant([])
    assert ctx["desc"] == "gerando resposta"


def test_thinking_delta_retorna_fragmento():
    ctx = {"desc": ""}
    frag = _track_heartbeat(json.dumps({
        "type": "stream_event",
        "event": {"type": "content_block_delta",
                  "delta": {"type": "thinking_delta", "thinking": "hmm"}},
    }), ctx)
    assert frag == "hmm"
    assert ctx["desc"] == "raciocinando: …hmm"


def test_linha_nao_json_ignorada():
    ctx = {"desc": "prev"}
    assert _track_heartbeat("não é json", ctx) is None
    assert ctx["desc"] == "prev"


# --- heartbeat de silêncio: tempo + node -----------------------------------

from ft.cli.main import _fmt_elapsed, _node_from_log_name


def test_fmt_elapsed_segundos():
    assert _fmt_elapsed(0) == "há 0s"
    assert _fmt_elapsed(45) == "há 45s"
    assert _fmt_elapsed(59.9) == "há 59s"


def test_fmt_elapsed_minutos():
    assert _fmt_elapsed(60) == "há 1 min 00s"
    assert _fmt_elapsed(135) == "há 2 min 15s"


def test_fmt_elapsed_nunca_negativo():
    assert _fmt_elapsed(-5) == "há 0s"


def test_node_from_log_name_extrai_node():
    assert _node_from_log_name("20260706-143226__loop.s04.mission_check__review-retry.log") == "loop.s04.mission_check"
    assert _node_from_log_name("20260706-122637__loop.s06.red__run.log") == "loop.s06.red"


def test_node_from_log_name_sem_padrao():
    assert _node_from_log_name("arquivo_solto.log") is None
    assert _node_from_log_name("semseparador") is None


# --- espaçamento de bloco bash (ft log -m): branco só nas bordas -----------

from ft.cli.main import _needs_block_blank


def test_bloco_bash_branco_so_nas_bordas():
    # Sequência: texto, 3 bashes, texto  →  branco ao ENTRAR e ao SAIR do bloco,
    # nunca entre bashes consecutivos.
    flags = [False, True, True, True, False]  # is_bash de cada linha de conteúdo
    prev = False
    layout = []
    for is_bash in flags:
        if _needs_block_blank(prev, is_bash):
            layout.append("·")  # linha em branco
        layout.append("$" if is_bash else "T")
        prev = is_bash
    assert layout == ["T", "·", "$", "$", "$", "·", "T"]


def test_transicoes_do_bloco():
    assert _needs_block_blank(False, True) is True   # abre
    assert _needs_block_blank(True, False) is True   # fecha
    assert _needs_block_blank(True, True) is False   # dentro do bloco
    assert _needs_block_blank(False, False) is False  # fora do bloco


# --- motivo real da espera: gate humano / bloqueio / LLM -------------------

from ft.cli.main import _wait_reason


def test_wait_reason_gate_por_pending_approval():
    kind, text = _wait_reason("awaiting_approval", "gate.s04", None, "gate.s04")
    assert kind == "gate"
    assert "gate.s04" in text
    assert "ft approve" in text and "ft reject" in text


def test_wait_reason_gate_por_status():
    kind, text = _wait_reason("awaiting_approval", None, None, "gate.value_core")
    assert kind == "gate"
    assert "gate.value_core" in text


def test_wait_reason_blocked():
    kind, text = _wait_reason("blocked", None, "git_diff_not_empty falhou", "loop.s04.green")
    assert kind == "blocked"
    assert "loop.s04.green" in text
    assert "git_diff_not_empty falhou" in text


def test_wait_reason_blocked_sem_motivo():
    kind, text = _wait_reason("blocked", None, None, "n1")
    assert kind == "blocked"
    assert "sem motivo" in text


def test_wait_reason_rodando_e_none():
    assert _wait_reason("delegated", None, None, "loop.s04.green") == (None, None)
    assert _wait_reason("ready", None, None, "n1") == (None, None)


# --- âncora do contador de silêncio no mtime do log ------------------------

from ft.cli.main import _log_mtime


def test_log_mtime_le_mtime_do_arquivo(tmp_path):
    import os
    p = tmp_path / "x.log"
    p.write_text("hi")
    os.utime(p, (1000.0, 1000.0))
    assert _log_mtime(p) == 1000.0


def test_log_mtime_arquivo_inexistente_cai_em_now(tmp_path):
    import time
    t = _log_mtime(tmp_path / "nao-existe.log")
    assert abs(t - time.time()) < 5


# --- ciclo parado: ready/delegated sem orquestrador vivo -------------------

def test_wait_reason_stalled_sem_orquestrador():
    kind, text = _wait_reason("ready", None, None, "loop.s03.mission_check",
                              orchestrator_alive=False)
    assert kind == "stalled"
    assert "PARADO" in text and "loop.s03.mission_check" in text
    assert "ft continue" in text


def test_wait_reason_ready_com_orquestrador_e_normal():
    # orquestrador vivo → não é stall, cai no comportamento normal (LLM)
    assert _wait_reason("ready", None, None, "n1", orchestrator_alive=True) == (None, None)


def test_wait_reason_gate_vence_orquestrador_morto():
    # um human gate é pausa legítima, não "stalled", mesmo sem orquestrador
    kind, _ = _wait_reason("awaiting_approval", "gate.final", None, "gate.final",
                           orchestrator_alive=False)
    assert kind == "gate"


# --- sanitização de texto do estado (vazamento de cor no bloqueio) ---------

from ft.cli.main import _oneline


def test_oneline_colapsa_multilinha():
    assert _oneline("Review falhou:\n\nNow let's check\nthe tests") == "Review falhou: Now let's check the tests"


def test_oneline_tira_ansi():
    assert _oneline("\x1b[31mvermelho\x1b[0m aqui") == "vermelho aqui"


def test_oneline_trunca_com_reticencias():
    out = _oneline("x" * 200, limit=50)
    assert out == "x" * 50 + "…"


def test_oneline_vazio():
    assert _oneline(None) == "" and _oneline("") == ""


def test_wait_reason_blocked_sanitiza_multilinha():
    # o motivo real que vazou a cor: 315 chars com \n
    reason = "Review falhou: Now let's check the test file\n\nand run the frontend suite\n" * 5
    kind, text = _wait_reason("blocked", None, reason, "loop.s03.mission_check")
    assert kind == "blocked"
    assert "\n" not in text  # NUNCA multilinha — senão a cor vaza


# --- truncar linha à largura do terminal (empilhamento do heartbeat longo) --

from ft.cli.main import _truncate_visible


def test_truncate_visible_curto_intacto():
    assert _truncate_visible("abc", 10) == "abc"


def test_truncate_visible_trunca_com_reticencias():
    assert _truncate_visible("abcdefghij", 5) == "abcd…"


def test_truncate_visible_ignora_ansi_na_largura():
    # 5 chars visíveis cabem em width=5, apesar dos códigos ANSI
    s = "\x1b[31mabcde\x1b[0m"
    assert _truncate_visible(s, 5) == s


def test_truncate_visible_garante_reset():
    out = _truncate_visible("\x1b[31mabcdefgh", 4, reset="\x1b[0m")
    assert out.endswith("\x1b[0m")
    assert out.startswith("\x1b[31m")
    assert "…" in out
