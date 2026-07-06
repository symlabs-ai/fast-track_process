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
        "BOLD_CYAN": "[BCYN]", "BOLD": "[B]", "CYAN": "[cyn]",
    }
    for name, val in marks.items():
        monkeypatch.setattr(ui, name, val)
    monkeypatch.setattr(ui, "_COLOR", True)  # render_md exige cor ligada
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


# --- markdown leve na prosa (o prompt do nó) -------------------------------

def test_md_sem_cor_texto_cru():
    # Sem cor (pipe), a sintaxe markdown é preservada — não corrompe captura.
    assert ui.render_md("## Output") == "## Output"
    assert ui.render_md("- item") == "- item"
    assert ui.render_md("um **negrito** e `codigo`") == "um **negrito** e `codigo`"


def test_md_header(cores):
    assert ui.render_md("## Output") == "[BWHT]Output[/]"
    assert ui.render_md("### Sub titulo") == "[BWHT]Sub titulo[/]"


def test_md_bullet(cores):
    assert ui.render_md("- primeiro item") == "[cyn]•[/] primeiro item"
    assert ui.render_md("* outro") == "[cyn]•[/] outro"


def test_md_negrito_e_codigo_inline(cores):
    assert ui.render_md("use **isto**") == "use [B]isto[/]"
    assert ui.render_md("rode `pytest -q` agora") == "rode [cyn]pytest -q[/] agora"


def test_md_linha_de_prosa_no_paint(cores):
    # prosa cai no fallback do paint_stream_line → render_md
    assert ui.paint_stream_line("## Output") == "[BWHT]Output[/]"
    assert ui.paint_stream_line("- Escreva APENAS nos paths permitidos") == "[cyn]•[/] Escreva APENAS nos paths permitidos"


def test_md_nao_toca_comando_bash(cores):
    # linhas de stream (bash) NÃO viram markdown (um '-' no comando é literal)
    out = ui.paint_stream_line("$ grep -rn '**' arquivo")
    assert out.startswith("[BGRN]$[/]")
