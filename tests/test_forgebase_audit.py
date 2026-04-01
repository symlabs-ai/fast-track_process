"""
TDD — ForgeBase Audit Report

Testa que project/docs/forgebase-audit.md existe e contém as seções
obrigatórias do template de auditoria ForgeBase.
"""

from pathlib import Path

import pytest

AUDIT_PATH = Path(__file__).parent.parent / "project" / "docs" / "forgebase-audit.md"

REQUIRED_SECTIONS = [
    "UseCaseRunner",
    "Value Tracks",
    "Observabilidade",
    "Logging",
    "Arquitetura",
    "Resultado Final",
]


class TestForgeBaseAuditExists:
    def test_audit_file_exists(self):
        assert AUDIT_PATH.exists(), f"Arquivo de auditoria não encontrado: {AUDIT_PATH}"

    def test_audit_file_not_empty(self):
        assert AUDIT_PATH.exists()
        content = AUDIT_PATH.read_text()
        assert len(content.strip()) > 0, "Arquivo de auditoria está vazio"

    def test_audit_file_min_lines(self):
        assert AUDIT_PATH.exists()
        lines = AUDIT_PATH.read_text().strip().splitlines()
        assert len(lines) >= 30, f"Auditoria muito curta: {len(lines)} linhas (mínimo 30)"


class TestForgeBaseAuditSections:
    @pytest.fixture(autouse=True)
    def content(self):
        assert AUDIT_PATH.exists(), f"Arquivo de auditoria não encontrado: {AUDIT_PATH}"
        self._content = AUDIT_PATH.read_text()

    def test_has_usecase_runner_section(self):
        assert "UseCaseRunner" in self._content

    def test_has_value_tracks_section(self):
        assert "Value Tracks" in self._content or "Support Track" in self._content

    def test_has_observabilidade_section(self):
        assert "Observabilidade" in self._content or "Pulse" in self._content

    def test_has_logging_section(self):
        assert "Logging" in self._content

    def test_has_arquitetura_section(self):
        assert "Arquitetura" in self._content

    def test_has_resultado_final_section(self):
        assert "Resultado Final" in self._content

    def test_has_approval_status(self):
        content_upper = self._content.upper()
        assert "APROVADO" in content_upper or "REPROVADO" in content_upper, \
            "Auditoria deve conter veredicto final (APROVADO ou REPROVADO)"

    def test_has_checklist_items(self):
        assert "- [" in self._content, "Auditoria deve conter checklist items"

    def test_has_summary_table(self):
        assert "|" in self._content, "Auditoria deve conter tabelas markdown"
