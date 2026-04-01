"""Unit tests for ft.engine.stakeholder."""

import pytest
from pathlib import Path

from ft.engine.stakeholder import (
    scan_existing_docs,
    should_skip_node,
    hyper_mode_prompt,
    build_rejection_prompt,
    format_pending_summary,
    get_pending_items,
)


@pytest.fixture
def docs_dir(tmp_path):
    """Create a project/docs/ dir with some docs."""
    d = tmp_path / "project" / "docs"
    d.mkdir(parents=True)
    (d / "hipotese.md").write_text("\n".join(["line"] * 15))
    (d / "PRD.md").write_text("\n".join(["line"] * 40))
    return tmp_path


class TestScanExistingDocs:
    def test_returns_dict_of_docs(self, docs_dir):
        docs = scan_existing_docs(str(docs_dir))
        assert "hipotese.md" in docs
        assert "PRD.md" in docs

    def test_empty_when_no_docs_dir(self, tmp_path):
        docs = scan_existing_docs(str(tmp_path))
        assert docs == {}

    def test_reads_content(self, docs_dir):
        docs = scan_existing_docs(str(docs_dir))
        assert len(docs["hipotese.md"].splitlines()) == 15


class TestShouldSkipNode:
    def test_skip_hipotese_when_exists(self):
        docs = {"hipotese.md": "\n".join(["x"] * 15)}
        assert should_skip_node("ft.mdd.01.hipotese", docs) is True

    def test_no_skip_when_too_short(self):
        docs = {"hipotese.md": "short"}
        assert should_skip_node("ft.mdd.01.hipotese", docs) is False

    def test_no_skip_unrelated_node(self):
        docs = {"hipotese.md": "\n".join(["x"] * 15)}
        assert should_skip_node("ft.tdd.02.red", docs) is False


class TestHyperModePrompt:
    def test_enriches_with_context(self):
        docs = {"PRD.md": "# PRD\nconteudo"}
        result = hyper_mode_prompt(docs, "Tarefa original")
        assert "CONTEXTO EXISTENTE" in result
        assert "Tarefa original" in result
        assert "PRD.md" in result

    def test_returns_original_when_no_docs(self):
        result = hyper_mode_prompt({}, "Tarefa original")
        assert result == "Tarefa original"


class TestBuildRejectionPrompt:
    def test_includes_reason(self):
        result = build_rejection_prompt("tarefa", "precisa de mais detalhes")
        assert "precisa de mais detalhes" in result

    def test_includes_original_task(self):
        result = build_rejection_prompt("tarefa original", "motivo")
        assert "tarefa original" in result

    def test_includes_artifact_preview(self):
        result = build_rejection_prompt("tarefa", "motivo", "conteudo do artefato")
        assert "conteudo do artefato" in result


class TestFormatPendingSummary:
    def test_empty_pending(self):
        result = format_pending_summary([])
        assert "Nenhum" in result

    def test_shows_pending_items(self):
        items = [{"node_id": "ft.mdd.01", "type": "approval"}]
        result = format_pending_summary(items)
        assert "ft.mdd.01" in result
        assert "ft approve" in result


class TestGetPendingItems:
    def test_returns_pending_when_set(self):
        state = type("State", (), {"pending_approval": "node.01"})()
        items = get_pending_items(state)
        assert len(items) == 1
        assert items[0]["node_id"] == "node.01"

    def test_returns_empty_when_none(self):
        state = type("State", (), {"pending_approval": None})()
        items = get_pending_items(state)
        assert items == []
