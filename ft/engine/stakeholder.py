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
