"""
Process Triage — classifica demanda bruta do usuário.

Separa requisitos de PRODUTO (viram hipótese) de requisitos de PROCESSO
(adaptam o YAML). Roda antes de qualquer node do processo.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ft.engine import ui


TRIAGE_PROMPT = """Você é um analista de requisitos do Fast Track engine.

O usuário forneceu uma DEMANDA BRUTA — texto livre que pode conter:
1. Requisitos de PRODUTO (o que construir)
2. Requisitos de PROCESSO (como construir)
3. Ambos misturados
4. Algo vago que precisa de perguntas

DEMANDA DO USUÁRIO:
---
{demand}
---

PROCESSO ATUAL (resumo dos nodes):
---
{process_summary}
---

Sua tarefa: classificar a demanda e separar produto de processo.

Responda EXATAMENTE neste formato JSON (sem markdown, sem ```):
{{
  "product": {{
    "summary": "resumo do que o usuário quer construir (produto)",
    "problem": "o problema que o produto resolve",
    "opportunity": "a oportunidade de mercado/negócio"
  }},
  "process": {{
    "detected": true/false,
    "requirements": [
      "requisito de processo 1 (ex: prototipar UI antes de código)",
      "requisito de processo 2 (ex: sem backend, tudo mock)"
    ],
    "conflicts": [
      "conflito 1 com o processo atual (ex: processo vai direto pro TDD mas usuário quer protótipo primeiro)"
    ]
  }},
  "questions": [
    "pergunta 1 para esclarecer ambiguidade (se houver)",
    "pergunta 2"
  ]
}}

Se não detectou requisitos de processo, retorne "detected": false e listas vazias.
Se não tem perguntas, retorne lista vazia.
IMPORTANTE: retorne APENAS o JSON, sem texto antes ou depois."""


ADAPT_PROMPT = """Você é um engenheiro de processos do Fast Track engine.

O processo atual é um grafo YAML com nodes que executam em sequência.
O usuário tem requisitos de processo que o YAML atual NÃO atende.

REQUISITOS DE PROCESSO DO USUÁRIO:
{requirements}

CONFLITOS DETECTADOS:
{conflicts}

YAML ATUAL DO PROCESSO:
---
{yaml_content}
---

Sua tarefa: adaptar o YAML para atender os requisitos do usuário.

Regras:
- Mantenha o formato YAML válido (id, type, title, executor, outputs, validators, next)
- Pode reordenar nodes, adicionar novos, remover ou pular com decision nodes
- Mantenha os gates de qualidade (não remova gates, pode reposicionar)
- O primeiro node deve continuar sendo ft.mdd.01.hipotese
- O último node deve continuar sendo ft.end
- Use decision nodes para criar branches condicionais

Retorne APENAS o YAML adaptado, sem explicação antes ou depois.
Comece com "id:" (a primeira linha do YAML)."""


def summarize_process(yaml_content: str) -> str:
    """Gera resumo legível dos nodes do processo para o prompt de triage."""
    import yaml
    data = yaml.safe_load(yaml_content)
    nodes = data.get("nodes", [])
    lines = []
    current_sprint = None
    for n in nodes:
        sprint = n.get("sprint", "")
        if sprint != current_sprint:
            current_sprint = sprint
            if sprint:
                lines.append(f"\n  Sprint: {sprint}")
        ntype = n.get("type", "?")
        lines.append(f"    {n['id']} ({ntype}): {n.get('title', '?')}")
    return "\n".join(lines)


def classify_demand(
    demand: str,
    process_yaml_path: str | Path,
    project_root: str = ".",
    llm_engine: str = "claude",
) -> dict[str, Any]:
    """Classifica a demanda bruta do usuário.

    Retorna dict com 'product', 'process', 'questions'.
    """
    from ft.engine.delegate import delegate_to_llm

    yaml_content = Path(process_yaml_path).read_text()
    process_summary = summarize_process(yaml_content)

    prompt = TRIAGE_PROMPT.format(
        demand=demand,
        process_summary=process_summary,
    )

    result = delegate_to_llm(
        task=prompt,
        project_root=project_root,
        allowed_paths=[],
        max_turns=5,
        llm_engine=llm_engine,
    )

    # Parse JSON da resposta
    output = result.output.strip()
    # Tentar extrair JSON do output (pode ter texto extra)
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        # Procurar JSON no output
        start = output.find("{")
        end = output.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(output[start:end])
            except json.JSONDecodeError:
                pass

    # Fallback: tratar tudo como produto
    return {
        "product": {
            "summary": demand,
            "problem": "",
            "opportunity": "",
        },
        "process": {
            "detected": False,
            "requirements": [],
            "conflicts": [],
        },
        "questions": [],
    }


def generate_hypothesis(classification: dict[str, Any]) -> str:
    """Gera hipótese formatada a partir da classificação."""
    product = classification.get("product", {})
    problem = product.get("problem", "")
    opportunity = product.get("opportunity", "")
    summary = product.get("summary", "")

    lines = []
    lines.append("## Problema\n")
    lines.append(problem or summary or "(a ser definido)")
    lines.append("\n\n## Oportunidade\n")
    lines.append(opportunity or summary or "(a ser definido)")

    return "\n".join(lines)


def present_triage(classification: dict[str, Any]) -> str:
    """Formata o resultado do triage para apresentar ao stakeholder."""
    product = classification.get("product", {})
    process = classification.get("process", {})
    questions = classification.get("questions", [])

    lines = [ui.header("Análise da Demanda")]

    # Produto
    lines.append(f"\n  {ui.BOLD_WHITE}Produto detectado:{ui.RESET}")
    lines.append(f"    {product.get('summary', '(nenhum)')}")

    # Processo
    if process.get("detected"):
        lines.append(f"\n  {ui.BOLD_YELLOW}Requisitos de processo detectados:{ui.RESET}")
        for req in process.get("requirements", []):
            lines.append(f"    {ui.YELLOW}!{ui.RESET} {req}")
        if process.get("conflicts"):
            lines.append(f"\n  {ui.BOLD_RED}Conflitos com o processo atual:{ui.RESET}")
            for conflict in process["conflicts"]:
                lines.append(f"    {ui.RED}✗{ui.RESET} {conflict}")
        lines.append(f"\n  {ui.BOLD_WHITE}O processo precisa ser adaptado antes de iniciar.{ui.RESET}")
    else:
        lines.append(f"\n  {ui.BOLD_GREEN}Processo:{ui.RESET} compatível (sem adaptações necessárias)")

    # Perguntas
    if questions:
        lines.append(f"\n  {ui.BOLD_WHITE}Perguntas para esclarecer:{ui.RESET}")
        for i, q in enumerate(questions, 1):
            lines.append(f"    {ui.CYAN}{i}.{ui.RESET} {q}")

    return "\n".join(lines)


def adapt_process(
    process_yaml_path: str | Path,
    requirements: list[str],
    conflicts: list[str],
    project_root: str = ".",
    llm_engine: str = "claude",
) -> str | None:
    """Adapta o YAML do processo com base nos requisitos detectados.

    Retorna o YAML adaptado ou None se falhar.
    """
    from ft.engine.delegate import delegate_to_llm

    yaml_content = Path(process_yaml_path).read_text()

    prompt = ADAPT_PROMPT.format(
        requirements="\n".join(f"- {r}" for r in requirements),
        conflicts="\n".join(f"- {c}" for c in conflicts),
        yaml_content=yaml_content,
    )

    result = delegate_to_llm(
        task=prompt,
        project_root=project_root,
        allowed_paths=["process/"],
        max_turns=10,
        llm_engine=llm_engine,
    )

    if not result.success:
        return None

    # Extrair YAML da resposta
    output = result.output.strip()
    # Procurar início do YAML
    for marker in ("id:", "# Fast Track"):
        idx = output.find(marker)
        if idx >= 0:
            return output[idx:]

    return output if "nodes:" in output else None


def validate_adapted_yaml(yaml_content: str) -> tuple[bool, str]:
    """Valida o YAML adaptado usando o validador do engine."""
    import tempfile
    from ft.engine.graph import load_graph
    from ft.engine.process_validator import validate_process, format_report
    from ft.engine.runner import VALIDATOR_REGISTRY

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        f.write(yaml_content)
        f.flush()
        try:
            graph = load_graph(f.name)
            report = validate_process(graph, VALIDATOR_REGISTRY)
            total = len(graph.nodes)
            return report.passed, format_report(report, total)
        except Exception as e:
            return False, f"Erro ao validar YAML: {e}"
