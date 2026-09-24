#!/usr/bin/env python3
"""Render the canonical valuation report as a styled, self-contained PDF."""

from __future__ import annotations

import html
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

import validate_valuation as validation

DOCS = Path("docs")
MARKDOWN = DOCS / "valuation-report.md"
HTML = DOCS / "valuation-report.html"
PDF = DOCS / "valuation-report.pdf"


def read_yaml(name: str) -> dict:
    path = DOCS / name
    if not path.is_file():
        return {}
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def inline(value: str) -> str:
    result = html.escape(value)
    def render_link(match: re.Match[str]) -> str:
        label, target = match.groups()
        if target.startswith(("https://", "http://")):
            return f'<a href="{html.escape(target, quote=True)}">{label}</a>'
        if not target.startswith(("/", "../")) and ":" not in target:
            return f'{label} <code>{target}</code>'
        return match.group(0)

    result = re.sub(r"\[([^\]]+)\]\(([^\s)]+)\)", render_link, result)
    result = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", result)
    result = re.sub(r"(?<!\w)\*([^*\n]+)\*(?!\w)", r"<em>\1</em>", result)
    result = re.sub(r"`([^`]+)`", r"<code>\1</code>", result)
    return result


def markdown_to_html(text: str) -> str:
    lines = text.splitlines()
    output: list[str] = []
    paragraph: list[str] = []
    index = 0

    def flush() -> None:
        if paragraph:
            output.append("<p>" + inline(" ".join(paragraph)) + "</p>")
            paragraph.clear()

    while index < len(lines):
        line = lines[index].strip()
        if not line:
            flush()
            index += 1
            continue
        if line.startswith("#") and re.match(r"^#{1,3} ", line):
            flush()
            level = min(len(line) - len(line.lstrip("#")), 3)
            output.append(f"<h{level}>{inline(line[level + 1:].strip())}</h{level}>")
            index += 1
            continue
        if line.startswith("|") and index + 1 < len(lines) and re.match(r"^\|?[\s:|\-]+\|?$", lines[index + 1].strip()):
            flush()
            headers = [cell.strip() for cell in line.strip("|").split("|")]
            index += 2
            rows: list[list[str]] = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                rows.append([cell.strip() for cell in lines[index].strip().strip("|").split("|")])
                index += 1
            output.append('<div class="table-wrap"><table><thead><tr>' +
                          "".join(f"<th>{inline(cell)}</th>" for cell in headers) +
                          "</tr></thead><tbody>" +
                          "".join("<tr>" + "".join(f"<td>{inline(row[i]) if i < len(row) else ''}</td>" for i in range(len(headers))) + "</tr>" for row in rows) +
                          "</tbody></table></div>")
            continue
        if line.startswith(("- ", "* ")):
            flush()
            items: list[str] = []
            while index < len(lines) and lines[index].strip().startswith(("- ", "* ")):
                items.append("<li>" + inline(lines[index].strip()[2:]) + "</li>")
                index += 1
            output.append("<ul>" + "".join(items) + "</ul>")
            continue
        paragraph.append(line)
        index += 1
    flush()
    return "\n".join(output)


def money(value: object, unit: str) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "N/D"
    formatted = f"{value:,.1f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return f"{formatted} {html.escape(unit_label(unit))}"


def unit_label(unit: str) -> str:
    return {"millions": "milhões", "thousands": "milhares", "units": "unidades"}.get(unit, unit)


def financial_chart(financial: dict) -> str:
    periods = [p for p in financial.get("periods", []) if isinstance(p, dict) and isinstance(p.get("revenue"), (int, float))]
    if not periods:
        return '<div class="chart-empty">Histórico financeiro insuficiente para gráfico.</div>'
    periods = sorted(periods, key=lambda p: p.get("year", 0))[-8:]
    maximum = max(float(p["revenue"]) for p in periods) or 1
    width = 660
    gap = width / len(periods)
    elements = []
    for i, p in enumerate(periods):
        x = 45 + i * gap + gap * .25
        bar_width = gap * .5
        height = max(2, 135 * float(p["revenue"]) / maximum)
        y = 165 - height
        elements.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" height="{height:.1f}" rx="5" fill="#247a78"/>')
        elements.append(f'<text x="{x + bar_width/2:.1f}" y="184" text-anchor="middle" class="tick">{html.escape(str(p.get("year", "")))}</text>')
        elements.append(f'<text x="{x + bar_width/2:.1f}" y="{y - 8:.1f}" text-anchor="middle" class="value">{html.escape(str(p["revenue"]).replace(".", ","))}</text>')
    return '<svg class="chart" viewBox="0 0 750 205" role="img" aria-label="Receita histórica da empresa-alvo">' + '<line x1="35" y1="165" x2="715" y2="165" stroke="#bcc9c8"/>' + "".join(elements) + '</svg>'


def valuation_chart(model: dict, case: dict, unit: str) -> str:
    low, high = model.get("equity_range_low"), model.get("equity_range_high")
    label = "Valor standalone do equity (100%)"
    if not isinstance(low, (int, float)) or not isinstance(high, (int, float)):
        low, high = model.get("enterprise_range_low"), model.get("enterprise_range_high")
        label = "Valor da operação (EV) preliminar; equity depende da dívida líquida"
        if not isinstance(low, (int, float)) or not isinstance(high, (int, float)):
            screening = case.get("screening") or {}
            low, high = screening.get("screening_ev_low"), screening.get("screening_ev_high")
            label = "EV hipotético para triagem; não é preço das quotas"
            if not isinstance(low, (int, float)) or not isinstance(high, (int, float)):
                return '<div class="chart-empty">Sem faixa numérica de triagem; consulte a decisão qualitativa.</div>'
    top = max(float(high), float(case.get("buyer_economic_ceiling_high") or 0), 1)
    scale = 600 / top
    start = 65 + max(0, float(low)) * scale
    end = 65 + max(0, float(high)) * scale
    values = [
        '<svg class="chart" viewBox="0 0 750 150" role="img" aria-label="Faixas de valor standalone e teto econômico">',
        '<line x1="65" y1="58" x2="665" y2="58" stroke="#bdcbc9" stroke-width="2"/>',
        f'<rect x="{start:.1f}" y="43" width="{max(end-start,3):.1f}" height="30" rx="9" fill="#247a78"/>',
        f'<text x="65" y="28" class="label">{label}: {money(low, unit)} – {money(high, unit)}</text>',
    ]
    ceiling = case.get("buyer_economic_ceiling_high") if label.startswith("Valor standalone") else None
    if isinstance(ceiling, (int, float)):
        position = 65 + max(0, float(ceiling)) * scale
        values.extend([
            f'<line x1="{position:.1f}" y1="82" x2="{position:.1f}" y2="113" stroke="#bd844d" stroke-width="3"/>',
            f'<text x="65" y="131" class="label">Teto econômico da compradora: {money(ceiling, unit)} (indicativo)</text>',
        ])
    values.append('</svg>')
    return "".join(values)


def build_html() -> str:
    report = MARKDOWN.read_text(encoding="utf-8")
    scope = read_yaml("valuation-scope.yml")
    financial = read_yaml("financial-inputs.yml")
    model = read_yaml("valuation-model.yml")
    case = read_yaml("buyer-case.yml")
    buyer = read_yaml("buyer-profile.yml")
    target = html.escape(str(scope.get("target_name") or "Empresa-alvo"))
    buyer_name = html.escape(str(buyer.get("buyer_name") or "Compradora"))
    date = html.escape(str(scope.get("valuation_date") or "Data-base não definida"))
    unit = str(scope.get("unit") or "units")
    display_unit = html.escape(unit_label(unit))
    currency = html.escape(str(scope.get("currency") or ""))
    summary = validation.section_body(report, "Sumário Executivo")
    remainder = re.sub(r"^## Sumário Executivo[^\S\n]*\n.*?(?=^## |\Z)", "", report, count=1, flags=re.MULTILINE | re.DOTALL)
    body = markdown_to_html(remainder)
    decision = markdown_to_html("## Sumário Executivo\n\n" + summary)
    return f'''<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<title>Valuation | {target}</title><style>
@page {{ size: A4; margin: 18mm 17mm 17mm; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; color: #203236; font: 10.5pt/1.48 Arial, sans-serif; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
h1, h2, h3 {{ font-family: Georgia, 'Times New Roman', serif; line-height: 1.15; color: #173d42; page-break-after: avoid; }}
h1 {{ font-size: 27pt; margin: 0 0 10mm; }}
h2 {{ font-size: 16pt; margin: 10mm 0 4mm; border-bottom: 1px solid #b6cbc9; padding-bottom: 2mm; }}
h3 {{ font-size: 12pt; margin: 7mm 0 2mm; }}
p {{ margin: 0 0 3.5mm; orphans: 3; widows: 3; }}
ul {{ margin: 0 0 4mm 0; padding-left: 6mm; }} li {{ margin-bottom: 1mm; }}
a {{ color: #146b69; text-decoration: none; overflow-wrap: anywhere; }}
code {{ color: #36585b; font: 9pt monospace; }}
.cover {{ page-break-after: always; min-height: 245mm; position: relative; padding-top: 23mm; }}
.eyebrow {{ color: #247a78; font: bold 9pt Arial, sans-serif; letter-spacing: .18em; text-transform: uppercase; }}
.cover h1 {{ font-size: 37pt; max-width: 155mm; margin: 11mm 0 6mm; }}
.cover .subtitle {{ font: 18pt Georgia, serif; color: #6b7b7d; margin-bottom: 18mm; }}
.cover .meta {{ border-top: 1px solid #b6cbc9; padding-top: 5mm; color: #53676b; width: 125mm; }}
.cover .meta strong {{ color: #173d42; }}
.cover-art {{ position: absolute; top: 8mm; right: -10mm; width: 91mm; opacity: .82; z-index: -1; }}
.cover-note {{ position: absolute; bottom: 0; color: #87989a; font-size: 8pt; }}
.chart-card {{ background: #f3f8f7; border: 1px solid #dce9e7; border-radius: 10px; padding: 5mm; margin: 6mm 0 8mm; break-inside: avoid; }}
.chart-title {{ font-weight: bold; color: #173d42; font-size: 9pt; letter-spacing: .04em; text-transform: uppercase; }}
.chart {{ width: 100%; max-height: 48mm; display: block; }}
.chart .tick {{ fill: #667a7b; font: 11px Arial; }} .chart .value {{ fill: #173d42; font: bold 11px Arial; }} .chart .label {{ fill: #36585b; font: 12px Arial; }}
.chart-empty {{ color: #6a7b7d; padding: 7mm; font-style: italic; }}
.table-wrap {{ margin: 3mm 0 4mm; break-inside: auto; }}
table {{ border-collapse: collapse; width: 100%; font-size: 8.3pt; line-height: 1.35; table-layout: fixed; }}
thead {{ display: table-header-group; }}
th {{ background: #173d42; color: white; text-align: left; padding: 2.3mm; overflow-wrap: anywhere; }}
td {{ border-bottom: 1px solid #dce9e7; padding: 2mm 2.3mm; vertical-align: top; overflow-wrap: anywhere; }}
tr:nth-child(even) td {{ background: #f5f8f8; }} tr {{ break-inside: avoid; }}
.contents > h1:first-child {{ display: none; }}
.decision-summary {{ page-break-after: always; font-size: 10pt; line-height: 1.35; }}
.decision-summary h2 {{ margin-top: 0; }}
.decision-summary p {{ margin-bottom: 2.5mm; }}
.decision-summary table {{ font-size: 8.3pt; }}
.footer {{ color: #718486; font-size: 8pt; border-top: 1px solid #dce9e7; margin-top: 10mm; padding-top: 3mm; }}
</style></head><body>
<section class="cover"><svg class="cover-art" viewBox="0 0 420 620" aria-hidden="true"><defs><linearGradient id="g" x1="0" x2="1" y1="0" y2="1"><stop stop-color="#dcece9"/><stop offset="1" stop-color="#f7e9dc"/></linearGradient></defs><circle cx="245" cy="235" r="158" fill="url(#g)"/><circle cx="245" cy="235" r="112" fill="none" stroke="#8db5ae" stroke-width="1"/><circle cx="245" cy="235" r="72" fill="none" stroke="#8db5ae" stroke-width="1"/><path d="M10 360 C95 250 170 300 250 150 S390 80 430 25" fill="none" stroke="#bd844d" stroke-width="2"/><path d="M0 420 C100 320 185 360 270 220 S400 150 440 100" fill="none" stroke="#9fc6be" stroke-width="1"/></svg>
<div class="eyebrow">Análise de aquisição · Relatório de valuation</div>
<h1>{target}</h1><div class="subtitle">Visão para {buyer_name}</div>
<div class="meta"><p><strong>Data-base</strong><br>{date}</p><p><strong>Moeda e escala</strong><br>{currency} · {display_unit}</p><p><strong>Escopo</strong><br>Valor independente, encaixe estratégico e preço para a compradora</p></div>
<div class="cover-note">Documento analítico para revisão. Não constitui aprovação de oferta.</div></section>
<section class="decision-summary">{decision}</section>
<div class="chart-card"><div class="chart-title">Evolução da receita do alvo · {currency} {display_unit}</div>{financial_chart(financial)}</div>
<div class="chart-card"><div class="chart-title">Faixa para decisão de triagem · {currency} {display_unit}</div>{valuation_chart(model, case, unit)}</div>
<main class="contents">{body}</main>
</body></html>'''


def render() -> int:
    if not MARKDOWN.is_file():
        print(f"arquivo ausente: {MARKDOWN}")
        return 1
    errors: list[str] = []
    validation.report(errors)
    if errors:
        print("BLOCK (pdf): " + "; ".join(errors))
        return 1
    chromium = shutil.which("chromium") or shutil.which("chromium-browser")
    if not chromium:
        print("chromium ausente; não é possível renderizar PDF")
        return 1
    HTML.write_text(build_html(), encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix=".valuation-pdf-", dir=DOCS) as profile:
        result = subprocess.run([
            chromium, "--headless", "--no-sandbox", "--disable-dev-shm-usage",
            "--disable-gpu", "--disable-extensions", "--no-pdf-header-footer",
            f"--user-data-dir={Path(profile).resolve()}", f"--print-to-pdf={PDF.resolve()}",
            HTML.resolve().as_uri(),
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=90, check=False)
    if result.returncode != 0 or not PDF.is_file():
        print("falha ao renderizar PDF:", result.stderr[-1000:])
        return 1
    return check()


def check() -> int:
    if not PDF.is_file() or PDF.stat().st_size < 5000 or PDF.open("rb").read(5) != b"%PDF-":
        print("PDF ausente, inválido ou pequeno demais")
        return 1
    pdfinfo = shutil.which("pdfinfo")
    if not pdfinfo:
        print("pdfinfo ausente; não é possível validar a paginação")
        return 1
    result = subprocess.run([pdfinfo, str(PDF)], capture_output=True, text=True, check=False)
    match = re.search(r"^Pages:\s+(\d+)", result.stdout, re.MULTILINE)
    if result.returncode != 0 or not match or int(match.group(1)) < 2:
        print("PDF deve ter capa e conteúdo legíveis")
        return 1
    pdftotext = shutil.which("pdftotext")
    if not pdftotext:
        print("pdftotext ausente; não é possível conferir o conteúdo")
        return 1
    result = subprocess.run([pdftotext, str(PDF), "-"], capture_output=True, text=True, check=False)
    for phrase in ("Mercado e Tamanho", "Preço para a Compradora", "Fontes e Rastreabilidade"):
        if result.returncode != 0 or phrase not in result.stdout:
            print(f"PDF não contém seção esperada: {phrase}")
            return 1
    pages = result.stdout.split("\f")
    errors: list[str] = []
    validation.report(errors)
    if errors:
        print("BLOCK (pdf): " + "; ".join(errors))
        return 1
    if len(pages) < 2:
        print("PDF não contém primeira página útil após a capa")
        return 1
    # PDF extraction may collapse spaces around slashes or wrap table headers.
    first_content = re.sub(r"\s+", "", pages[1])
    fragments = validation.summary_pdf_fragments(read_yaml("buyer-case.yml"), read_yaml("valuation-model.yml"))
    for fragment in fragments:
        if re.sub(r"\s+", "", fragment) not in first_content:
            print(f"PDF: síntese decisória ausente ou transbordou a primeira página útil: {fragment}")
            return 1
    print(f"PASS (pdf): {PDF}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in {"render", "check"}:
        print("uso: render_valuation_pdf.py <render|check>")
        sys.exit(2)
    sys.exit(render() if sys.argv[1] == "render" else check())
