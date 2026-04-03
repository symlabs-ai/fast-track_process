"""
Stakeholder Intelligence — interacao com o stakeholder humano.
Hyper-mode: absorve docs existentes e pula discovery.
Approval/rejection workflow com contexto.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Hyper-mode
# ---------------------------------------------------------------------------

def scan_existing_docs(project_root: str) -> dict[str, str]:
    """
    Scans project/docs/ para docs existentes.
    Retorna {filename: content} dos docs encontrados.
    """
    docs_dir = Path(project_root) / "project" / "docs"
    if not docs_dir.exists():
        return {}

    docs = {}
    for f in docs_dir.glob("*.md"):
        try:
            docs[f.name] = f.read_text()
        except OSError:
            pass
    return docs


def should_skip_node(node_id: str, existing_docs: dict[str, str]) -> bool:
    """
    Determina se um node de discovery pode ser pulado
    porque o artefato ja existe com conteudo suficiente.
    """
    doc_map = {
        "hipotese": ["hipotese.md", "hypothesis.md"],
        "prd": ["PRD.md", "prd.md"],
        "task_list": ["TASK_LIST.md", "task-list.md"],
        "handoff": ["HANDOFF.md", "handoff.md"],
    }

    for key, filenames in doc_map.items():
        if key in node_id.lower():
            for fname in filenames:
                if fname in existing_docs and len(existing_docs[fname].splitlines()) >= 10:
                    return True
    return False


def hyper_mode_prompt(existing_docs: dict[str, str], original_prompt: str) -> str:
    """
    Gera prompt enriquecido com contexto dos docs existentes.
    Usado quando o projeto ja tem docs parciais.
    """
    if not existing_docs:
        return original_prompt

    context_parts = ["CONTEXTO EXISTENTE (documentos ja produzidos):"]
    for fname, content in existing_docs.items():
        preview = "\n".join(content.splitlines()[:20])
        context_parts.append(f"\n### {fname}\n{preview}\n...")

    context = "\n".join(context_parts)
    return f"""{context}

---

{original_prompt}

IMPORTANTE: Use o contexto acima para evitar repetir informacoes ja estabelecidas.
Foque em complementar e refinar, nao em reescrever do zero.
"""


# ---------------------------------------------------------------------------
# KB Lessons
# ---------------------------------------------------------------------------

def scan_kb_lessons(ft_root: str, interface_type: str | None = None) -> str:
    """
    Lê avaliações de runs anteriores em <ft_root>/kb/avaliacao_e2e_*.md.
    Extrai seções de lições e pitfalls. Retorna string compacta para injeção no prompt.
    interface_type: se fornecido, destaca lições específicas para aquele tipo.
    """
    kb_dir = Path(ft_root) / "kb"
    if not kb_dir.exists():
        return ""

    evals = sorted(kb_dir.glob("avaliacao_e2e_*.md"))
    if not evals:
        return ""

    parts = ["LIÇÕES DE RUNS ANTERIORES (Process KB — padrões genéricos):"]

    for path in evals[-2:]:  # últimas 2 avaliações
        try:
            content = path.read_text()
        except OSError:
            continue

        lines = content.splitlines()
        title_line = next((l for l in lines if l.startswith("# ")), path.stem)
        nota_line = next((l for l in lines if "Nota:" in l or "nota" in l.lower()), "")

        # Extrair APENAS seções genéricas (lições de processo, não detalhes de projeto)
        # "O que falhou" e "Causa Raiz" são específicos de projeto — não injetar
        sections_to_extract = [
            "Lições para o Processo",
            "Lições para Próximos",
        ]

        extracted = [f"\n### Lições de {title_line.lstrip('# ')} {nota_line}"]
        in_section = False
        section_lines: list[str] = []

        for line in lines:
            if any(s.lower() in line.lower() for s in sections_to_extract) and line.startswith("##"):
                if section_lines:
                    extracted.extend(section_lines[:15])
                in_section = True
                section_lines = [line]
            elif in_section:
                if line.startswith("## "):
                    extracted.extend(section_lines[:15])
                    in_section = False
                    section_lines = []
                else:
                    section_lines.append(line)

        if section_lines:
            extracted.extend(section_lines[:15])

        # Só adiciona se extraiu algo além do header
        if len(extracted) > 1:
            parts.extend(extracted)

    if interface_type and interface_type not in ("cli_only", "api"):
        parts.append(
            "\n⚠️  ATENÇÃO (interface_type inclui UI/mixed): "
            "Verificar obrigatoriamente se existe entry point HTTP (main.py/app.py com FastAPI/Flask). "
            "Run SM5 falhou por backend ausente mesmo com testes unitários passando."
        )

    return "\n".join(parts)


def kb_lessons_prompt(lessons: str, original_prompt: str) -> str:
    """Injeta lições do KB no prompt, após o conteúdo principal."""
    if not lessons:
        return original_prompt
    return f"""{original_prompt}

---

{lessons}

Consulte as lições acima para evitar replicar erros de runs anteriores.
"""


# ---------------------------------------------------------------------------
# Approval context
# ---------------------------------------------------------------------------

def build_approval_context(
    node_id: str,
    node_title: str,
    artifacts: dict[str, str],
    project_root: str,
) -> str:
    """
    Constroi contexto para o stakeholder ao aprovar/rejeitar.
    Mostra conteudo dos artefatos produzidos.
    """
    lines = [
        f"Artefato para revisao: [{node_id}] {node_title}",
        "",
    ]

    for name, path in artifacts.items():
        if not path:
            continue
        full = Path(project_root) / path
        if full.exists():
            content = full.read_text()
            preview = "\n".join(content.splitlines()[:30])
            lines.append(f"--- {path} ---")
            lines.append(preview)
            if len(content.splitlines()) > 30:
                lines.append(f"... ({len(content.splitlines())} linhas total)")
            lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Rejection with feedback
# ---------------------------------------------------------------------------

def build_rejection_prompt(
    original_prompt: str,
    rejection_reason: str,
    artifact_content: str | None = None,
) -> str:
    """
    Constroi prompt de retry apos rejeicao pelo stakeholder.
    """
    parts = [
        "TAREFA ORIGINAL:",
        original_prompt,
        "",
        "REJEITADO PELO STAKEHOLDER.",
        f"Motivo: {rejection_reason}",
    ]

    if artifact_content:
        preview = "\n".join(artifact_content.splitlines()[:20])
        parts.extend([
            "",
            "ARTEFATO REJEITADO (primeiras linhas):",
            preview,
        ])

    parts.extend([
        "",
        "Corrija especificamente o que foi apontado no motivo da rejeicao.",
        "Nao modifique o que ja foi aprovado ou que nao foi mencionado.",
    ])

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Stakeholder state helpers
# ---------------------------------------------------------------------------

def get_pending_items(state: Any) -> list[dict[str, str]]:
    """Retorna lista de itens pendentes de aprovacao."""
    items = []
    if state.pending_approval:
        items.append({
            "node_id": state.pending_approval,
            "type": "approval",
        })
    return items


def format_pending_summary(pending: list[dict[str, str]]) -> str:
    """Formata resumo de itens pendentes para o stakeholder."""
    if not pending:
        return "Nenhum item pendente de aprovacao."

    lines = [f"{len(pending)} item(s) aguardando sua aprovacao:"]
    for item in pending:
        lines.append(f"  - {item['node_id']} ({item['type']})")
    lines.append("")
    lines.append("Use: ft approve  ou  ft reject <motivo>")
    return "\n".join(lines)
