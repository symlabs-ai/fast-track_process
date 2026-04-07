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


def diff_process(original_yaml: str, adapted_yaml: str) -> dict[str, Any]:
    """Compara dois YAMLs e retorna as diferenças.

    Retorna:
      {
        "renames": {"old_id": "new_id", ...},
        "added": ["new_node_id", ...],
        "removed": ["old_node_id", ...],
        "reordered": bool,
        "summary": ["descrição 1", "descrição 2", ...]
      }
    """
    import yaml as _yaml

    original = _yaml.safe_load(original_yaml)
    adapted = _yaml.safe_load(adapted_yaml)

    orig_nodes = {n["id"]: n for n in original.get("nodes", [])}
    adapt_nodes = {n["id"]: n for n in adapted.get("nodes", [])}

    orig_ids = set(orig_nodes.keys())
    adapt_ids = set(adapt_nodes.keys())

    removed = orig_ids - adapt_ids
    added = adapt_ids - orig_ids
    kept = orig_ids & adapt_ids

    # Inferir renomeações: nodes removidos que têm título similar a nodes adicionados
    renames: dict[str, str] = {}
    unmatched_removed = set(removed)
    unmatched_added = set(added)

    for old_id in list(unmatched_removed):
        old_title = orig_nodes[old_id].get("title", "").lower()
        old_type = orig_nodes[old_id].get("type", "")
        for new_id in list(unmatched_added):
            new_title = adapt_nodes[new_id].get("title", "").lower()
            new_type = adapt_nodes[new_id].get("type", "")
            # Mesmo tipo e título similar → provável renomeação
            if old_type == new_type and (
                old_title == new_title
                or old_id.split(".")[-1] == new_id.split(".")[-1]
            ):
                renames[old_id] = new_id
                unmatched_removed.discard(old_id)
                unmatched_added.discard(new_id)
                break

    # Detectar reordenação
    orig_order = [n["id"] for n in original.get("nodes", [])]
    adapt_order = [n["id"] for n in adapted.get("nodes", [])]
    kept_orig_order = [n for n in orig_order if n in kept]
    kept_adapt_order = [n for n in adapt_order if n in kept]
    reordered = kept_orig_order != kept_adapt_order

    # Gerar resumo legível
    summary: list[str] = []
    if renames:
        for old, new in renames.items():
            summary.append(f"Renomeado: {old} → {new}")
    if unmatched_added:
        for nid in unmatched_added:
            title = adapt_nodes[nid].get("title", nid)
            summary.append(f"Adicionado: {title} ({nid})")
    if unmatched_removed:
        for nid in unmatched_removed:
            title = orig_nodes[nid].get("title", nid)
            summary.append(f"Removido: {title} ({nid})")
    if reordered:
        summary.append("Ordem dos nodes foi alterada")

    orig_sprints = set(n.get("sprint", "") for n in original.get("nodes", []) if n.get("sprint"))
    adapt_sprints = set(n.get("sprint", "") for n in adapted.get("nodes", []) if n.get("sprint"))
    new_sprints = adapt_sprints - orig_sprints
    if new_sprints:
        summary.append(f"Sprints novos: {', '.join(sorted(new_sprints))}")

    return {
        "renames": renames,
        "added": sorted(unmatched_added),
        "removed": sorted(unmatched_removed),
        "reordered": reordered,
        "summary": summary,
    }


def apply_renames_to_state(state_path: str | Path, renames: dict[str, str]) -> None:
    """Aplica mapa de renomeação ao state existente."""
    import yaml as _yaml

    path = Path(state_path)
    if not path.exists() or not renames:
        return

    data = _yaml.safe_load(path.read_text()) or {}

    # Renomear current_node
    current = data.get("current_node")
    if current and current in renames:
        data["current_node"] = renames[current]

    # Renomear completed_nodes
    completed = data.get("completed_nodes", [])
    data["completed_nodes"] = [renames.get(n, n) for n in completed]

    # Renomear gate_log keys
    gate_log = data.get("gate_log", {})
    data["gate_log"] = {renames.get(k, k): v for k, v in gate_log.items()}

    # Renomear artifacts keys
    artifacts = data.get("artifacts", {})
    data["artifacts"] = {renames.get(k, k): v for k, v in artifacts.items()}

    path.write_text(_yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False))


def present_adaptation_proposal(
    diff: dict[str, Any],
    original_node_count: int,
    adapted_node_count: int,
) -> str:
    """Formata a proposta de adaptação para o stakeholder aprovar."""
    lines = [ui.header("Adaptação do Processo")]

    lines.append(f"\n  {ui.BOLD_WHITE}O que vai mudar:{ui.RESET}")
    for i, item in enumerate(diff["summary"], 1):
        lines.append(f"    {ui.YELLOW}{i}.{ui.RESET} {item}")

    if diff["renames"]:
        lines.append(f"\n  {ui.BOLD_WHITE}Nodes renomeados:{ui.RESET}")
        for old, new in diff["renames"].items():
            lines.append(f"    {ui.DIM}{old}{ui.RESET} → {ui.CYAN}{new}{ui.RESET}")

    if diff["added"]:
        lines.append(f"\n  {ui.BOLD_GREEN}Nodes adicionados:{ui.RESET}")
        for nid in diff["added"]:
            lines.append(f"    {ui.GREEN}+{ui.RESET} {nid}")

    if diff["removed"]:
        lines.append(f"\n  {ui.BOLD_RED}Nodes removidos:{ui.RESET}")
        for nid in diff["removed"]:
            lines.append(f"    {ui.RED}-{ui.RESET} {nid}")

    lines.append(f"\n  {ui.DIM}Processo: {original_node_count} → {adapted_node_count} nodes{ui.RESET}")
    lines.append(f"\n  {ui.BOLD_WHITE}Aprovar adaptação?{ui.RESET}")
    lines.append(f"    {ui.BOLD_CYAN}ft approve{ui.RESET} — aplicar adaptação e iniciar")
    lines.append(f"    {ui.BOLD_CYAN}ft reject{ui.RESET}  — usar processo padrão sem mudanças")
    lines.append("")

    return "\n".join(lines)


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
