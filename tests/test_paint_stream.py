"""Realce markdown do `ft log --follow --markdown` (paint_stream_line).

Separa visualmente comandos bash, chamadas de ferramenta, resposta e
raciocínio (feedback do stakeholder vibeos, loop-01 2026-07-06).
"""

import pytest

from ft.engine import ui


def test_sem_cor_string_volta_intacta():
    """Propriedade de segurança: em pipe/NO_COLOR as constantes ANSI são
    vazias, então o realce nunca injeta lixo — a saída é idêntica à entrada."""
    # No ambiente de teste stdout não é tty, então ui._COLOR é False.
    for s in ["$ pytest -q", "✻ pensando", "→ resposta", "result: ok",
              "Edit app.py", "event system", "linha solta"]:
        assert ui.paint_stream_line(s) == s


def test_string_vazia():
    assert ui.paint_stream_line("") == ""


@pytest.fixture
def cores(monkeypatch):
    """Força constantes ANSI para sentinelas legíveis, simulando terminal."""
    marks = {
        "RESET": "[/]", "DIM": "[dim]", "ITALIC": "[it]", "GREEN": "[grn]",
        "BLUE": "[blu]", "BOLD_GREEN": "[BGRN]", "BOLD_WHITE": "[BWHT]",
        "BOLD_CYAN": "[BCYN]",
    }
    for name, val in marks.items():
        monkeypatch.setattr(ui, name, val)
    return marks


def test_bash_verde(cores):
    out = ui.paint_stream_line("$ python -m pytest")
    assert out.startswith("[BGRN]$[/]")
    assert "[grn]python -m pytest[/]" in out


def test_raciocinio_dim_italico(cores):
    out = ui.paint_stream_line("✻ analisando o runner")
    assert out == "[dim][it]✻ analisando o runner[/]"


def test_resposta_branco_negrito(cores):
    out = ui.paint_stream_line("→ Agora vou rodar a suíte")
    assert out == "[BWHT]→ Agora vou rodar a suíte[/]"


def test_result_ciano(cores):
    out = ui.paint_stream_line("result: 96 testes verdes")
    assert out == "[BCYN]result: 96 testes verdes[/]"


@pytest.mark.parametrize("linha", ["Read app.py", "Edit x.ts", "Write y.md",
                                    "Grep foo", "Glob **/*.py", "NotebookEdit"])
def test_ferramentas_azul(cores, linha):
    out = ui.paint_stream_line(linha)
    assert out == f"[blu]{linha}[/]"


def test_evento_generico_apagado(cores):
    assert ui.paint_stream_line("event compact_boundary") == "[dim]event compact_boundary[/]"
    assert ui.paint_stream_line("[CustomTool]") == "[dim][CustomTool][/]"


def test_linha_desconhecida_intacta(cores):
    assert ui.paint_stream_line("NODE_SUMMARY: - fiz: impl") == "NODE_SUMMARY: - fiz: impl"
