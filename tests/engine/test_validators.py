"""Unit tests for ft.engine.validators.*"""

import time
from unittest.mock import MagicMock, patch

import pytest

from ft.engine.parallel import check_independence
from ft.engine.validators.artifacts import (
    api_contract_complete,
    command_succeeds,
    demand_coverage,
    document_quality,
    file_exists,
    features_catalog_valid,
    has_sections,
    implemented_backlog_covered_by_features,
    min_lines,
    min_user_stories,
    backlog_pending_decisions,
    project_backlog_valid,
    pytest_red_quality,
    relative_dates_only,
    sections_unchanged,
    task_list_references_backlog,
    ui_criteria_ids,
    ui_criteria_coverage,
)
from ft.engine.validators.gates import gate_acceptance_cli, gate_kb_review

# ---------------------------------------------------------------------------
# artifacts
# ---------------------------------------------------------------------------

class TestFileExists:
    def test_existing_file(self, tmp_path):
        f = tmp_path / "foo.txt"
        f.write_text("hello")
        passed, detail = file_exists("foo.txt", str(tmp_path))
        assert passed
        assert "foo.txt" in detail

    def test_missing_file(self, tmp_path):
        passed, detail = file_exists("missing.txt", str(tmp_path))
        assert not passed
        assert "FAIL" in detail

    def test_nested_path(self, tmp_path):
        d = tmp_path / "docs"
        d.mkdir(parents=True)
        (d / "PRD.md").write_text("content")
        passed, _ = file_exists("docs/PRD.md", str(tmp_path))
        assert passed


class TestMinLines:
    def test_sufficient_lines(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("\n".join(["line"] * 10))
        passed, detail = min_lines("file.txt", 5, str(tmp_path))
        assert passed
        assert "10 linhas" in detail

    def test_insufficient_lines(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("line1\nline2")
        passed, detail = min_lines("file.txt", 10, str(tmp_path))
        assert not passed
        assert "FAIL" in detail

    def test_missing_file(self, tmp_path):
        passed, detail = min_lines("missing.txt", 5, str(tmp_path))
        assert not passed
        assert "nao existe" in detail

    def test_exact_min(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("\n".join(["x"] * 5))
        passed, _ = min_lines("file.txt", 5, str(tmp_path))
        assert passed


class TestHasSections:
    def test_all_sections_present(self, tmp_path):
        f = tmp_path / "doc.md"
        f.write_text("# Hipotese\ncontent\n# Visao\ncontent\n# User Stories\ncontent")
        passed, detail = has_sections("doc.md", ["Hipotese", "Visao", "User Stories"], str(tmp_path))
        assert passed

    def test_missing_section(self, tmp_path):
        f = tmp_path / "doc.md"
        f.write_text("# Hipotese\ncontent")
        passed, detail = has_sections("doc.md", ["Hipotese", "Visao"], str(tmp_path))
        assert not passed
        assert "Visao" in detail

    def test_case_insensitive(self, tmp_path):
        f = tmp_path / "doc.md"
        f.write_text("# HIPOTESE\ncontent")
        passed, _ = has_sections("doc.md", ["hipotese"], str(tmp_path))
        assert passed


class TestDocumentQuality:
    def test_fails_on_prompt_tool_echo(self, tmp_path):
        f = tmp_path / "docs" / "task_list.md"
        f.parent.mkdir()
        f.write_text(
            "I'll help you create a task list.\n"
            "<tool_call>Glob</tool_call>\n"
            "<arg_key>pattern</arg_key>\n"
            "<arg_value>docs/*.md</arg_value>\n"
            "line\nline\nline\nline\n",
            encoding="utf-8",
        )

        passed, detail = document_quality("docs/task_list.md", project_root=str(tmp_path), min_lines_count=5)

        assert not passed
        assert "ruido de execucao" in detail

    def test_passes_with_required_terms(self, tmp_path):
        f = tmp_path / "docs" / "task_list.md"
        f.parent.mkdir()
        f.write_text(
            "# Task List\n\n"
            "## US-01 Clientes\n"
            "- Task frontend: criar tela de clientes.\n"
            "- Task backend: criar API de clientes.\n"
            "- Task teste: cobrir criação.\n"
            "## US-02 Agenda\n"
            "- Task frontend: criar tela de agenda.\n"
            "- Task backend: criar API de agenda.\n",
            encoding="utf-8",
        )

        passed, detail = document_quality(
            "docs/task_list.md",
            project_root=str(tmp_path),
            min_lines_count=6,
            required_terms=["US-", "frontend", "backend", "teste"],
            min_required_terms=4,
        )

        assert passed
        assert "linhas uteis" in detail

    def test_fails_when_document_is_too_long(self, tmp_path):
        f = tmp_path / "docs" / "task_list.md"
        f.parent.mkdir()
        f.write_text("\n".join(f"- Task {i}" for i in range(15)), encoding="utf-8")

        passed, detail = document_quality(
            "docs/task_list.md",
            project_root=str(tmp_path),
            min_lines_count=5,
            max_lines_count=10,
        )

        assert not passed
        assert "max 10" in detail


class TestProjectBacklog:
    def test_project_backlog_valid_passes_for_canonical_table(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "PROJECT_BACKLOG.md").write_text(
            "# PROJECT_BACKLOG\n\n"
            "## Itens do Backlog\n\n"
            "| ID | Tipo | Prioridade | Status | Origem | Título | Critérios de Aceite | Evidência | Decisão/Notas |\n"
            "|---|---|---|---|---|---|---|---|---|\n"
            "| PB-001 | US | P0 | planned | PRD | Cadastro | Criar item pela UI | — | — |\n",
            encoding="utf-8",
        )

        passed, detail = project_backlog_valid(project_root=str(tmp_path))

        assert passed
        assert "1 item" in detail

    def test_project_backlog_valid_fails_invalid_priority(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "PROJECT_BACKLOG.md").write_text(
            "| ID | Tipo | Prioridade | Status | Origem | Título | Critérios de Aceite | Evidência | Decisão/Notas |\n"
            "|---|---|---|---|---|---|---|---|---|\n"
            "| PB-001 | US | P9 | planned | PRD | Cadastro | Criar item pela UI | — | — |\n",
            encoding="utf-8",
        )

        passed, detail = project_backlog_valid(project_root=str(tmp_path))

        assert not passed
        assert "prioridade invalida" in detail

    def test_task_list_must_reference_backlog_id(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "PROJECT_BACKLOG.md").write_text(
            "| ID | Tipo | Prioridade | Status | Origem | Título | Critérios de Aceite | Evidência | Decisão/Notas |\n"
            "|---|---|---|---|---|---|---|---|---|\n"
            "| PB-001 | US | P0 | planned | PRD | Cadastro | Criar item pela UI | — | — |\n",
            encoding="utf-8",
        )
        (docs / "task_list.md").write_text(
            "# Task List\n\n## PB-001\n- Task frontend: criar formulario.\n",
            encoding="utf-8",
        )

        passed, detail = task_list_references_backlog(project_root=str(tmp_path))

        assert passed
        assert "1 item" in detail

    def test_backlog_pending_decisions_blocks_planned_p0(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "PROJECT_BACKLOG.md").write_text(
            "| ID | Tipo | Prioridade | Status | Origem | Título | Critérios de Aceite | Evidência | Decisão/Notas |\n"
            "|---|---|---|---|---|---|---|---|---|\n"
            "| PB-001 | US | P0 | planned | PRD | Cadastro | Criar item pela UI | — | — |\n",
            encoding="utf-8",
        )

        passed, detail = backlog_pending_decisions(project_root=str(tmp_path))

        assert not passed
        assert "PB-001" in detail

    def test_backlog_pending_decisions_allows_deferred_with_note(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "PROJECT_BACKLOG.md").write_text(
            "| ID | Tipo | Prioridade | Status | Origem | Título | Critérios de Aceite | Evidência | Decisão/Notas |\n"
            "|---|---|---|---|---|---|---|---|---|\n"
            "| PB-001 | US | P1 | deferred | PRD | Relatorio | Exportar CSV | — | Adiado para ciclo 02 |\n",
            encoding="utf-8",
        )

        passed, detail = backlog_pending_decisions(project_root=str(tmp_path))

        assert passed
        assert "nenhum P0/P1" in detail


class TestFeaturesCatalog:
    HEADER = (
        "| ID | Status | Backlog | Título | Descrição | Entregue em | Evidência | Última evolução | Notas |\n"
        "|---|---|---|---|---|---|---|---|---|\n"
    )
    BACKLOG_HEADER = (
        "| ID | Tipo | Prioridade | Status | Origem | Título | Critérios de Aceite | Evidência | Decisão/Notas |\n"
        "|---|---|---|---|---|---|---|---|---|\n"
    )

    def _write_backlog(self, tmp_path, *rows):
        docs = tmp_path / "docs"
        docs.mkdir(exist_ok=True)
        (docs / "PROJECT_BACKLOG.md").write_text(
            self.BACKLOG_HEADER + "".join(f"{row}\n" for row in rows),
            encoding="utf-8",
        )

    def _write_features(self, tmp_path, *rows):
        docs = tmp_path / "docs"
        docs.mkdir(exist_ok=True)
        (docs / "FEATURES.md").write_text(
            "# FEATURES\n\n" + self.HEADER + "".join(f"{row}\n" for row in rows),
            encoding="utf-8",
        )

    def test_valid_catalog_passes(self, tmp_path):
        self._write_backlog(
            tmp_path,
            "| PB-001 | US | P0 | done | PRD | Cadastro | Criar pela UI | docs/e2e.md | Entregue |",
        )
        self._write_features(
            tmp_path,
            "| FEAT-001 | active | PB-001 | Cadastro | Cadastro de clientes | cycle-01 | docs/e2e.md | cycle-01 | — |",
        )

        passed, detail = features_catalog_valid(project_root=str(tmp_path))

        assert passed
        assert "1 feature" in detail

    @pytest.mark.parametrize(
        ("rows", "expected"),
        [
            (
                (
                    "| FEAT-001 | active | PB-001 | Cadastro | Cadastro de clientes | cycle-01 | docs/e2e.md | cycle-01 | — |",
                    "| FEAT-001 | deprecated | PB-001 | Cadastro antigo | Fluxo legado | cycle-01 | docs/e2e.md | cycle-02 | Substituído |",
                ),
                "duplicados",
            ),
            (
                (
                    "| FEAT-001 | planned | PB-001 | Cadastro | Cadastro de clientes | cycle-01 | docs/e2e.md | cycle-01 | — |",
                ),
                "status invalido",
            ),
        ],
    )
    def test_duplicate_id_or_invalid_status_fails(self, tmp_path, rows, expected):
        self._write_backlog(
            tmp_path,
            "| PB-001 | US | P0 | done | PRD | Cadastro | Criar pela UI | docs/e2e.md | Entregue |",
        )
        self._write_features(tmp_path, *rows)

        passed, detail = features_catalog_valid(project_root=str(tmp_path))

        assert not passed
        assert expected in detail

    @pytest.mark.parametrize(
        ("feature_backlog", "backlog_status", "expected"),
        [
            ("PB-001", "planned", "ainda nao implementados"),
            ("PB-999", "done", "desconhecidos"),
        ],
    )
    def test_open_or_unknown_backlog_reference_fails(
        self, tmp_path, feature_backlog, backlog_status, expected
    ):
        self._write_backlog(
            tmp_path,
            f"| PB-001 | US | P0 | {backlog_status} | PRD | Cadastro | Criar pela UI | docs/e2e.md | — |",
        )
        self._write_features(
            tmp_path,
            f"| FEAT-001 | active | {feature_backlog} | Cadastro | Cadastro de clientes | cycle-01 | docs/e2e.md | cycle-01 | — |",
        )

        passed, detail = features_catalog_valid(project_root=str(tmp_path))

        assert not passed
        assert expected in detail

    def test_coverage_requires_delivered_feature_backlog(self, tmp_path):
        self._write_backlog(
            tmp_path,
            "| PB-001 | US | P0 | done | PRD | Cadastro | Criar pela UI | docs/e2e.md | Entregue |",
            "| PB-002 | Feature | P1 | accepted | PRD | Busca | Buscar clientes | docs/e2e.md | Aceita |",
        )
        self._write_features(
            tmp_path,
            "| FEAT-001 | active | PB-001 | Cadastro | Cadastro de clientes | cycle-01 | docs/e2e.md | cycle-01 | — |",
        )

        passed, detail = implemented_backlog_covered_by_features(project_root=str(tmp_path))

        assert not passed
        assert "PB-002" in detail

    def test_coverage_excludes_delivered_bug_and_debt(self, tmp_path):
        self._write_backlog(
            tmp_path,
            "| PB-001 | Bug | P0 | done | QA | Corrigir login | Login corrigido | docs/e2e.md | Entregue |",
            "| PB-002 | Debt | P1 | accepted | Retro | Refatorar | Código refatorado | docs/tests.md | Aceita |",
        )
        self._write_features(tmp_path)

        passed, detail = implemented_backlog_covered_by_features(project_root=str(tmp_path))

        assert passed
        assert "0 PB" in detail

    def test_empty_catalog_is_valid_without_delivered_features(self, tmp_path):
        self._write_backlog(
            tmp_path,
            "| PB-001 | US | P1 | planned | PRD | Busca | Buscar clientes | — | — |",
        )
        self._write_features(tmp_path)

        catalog_passed, _ = features_catalog_valid(project_root=str(tmp_path))
        coverage_passed, _ = implemented_backlog_covered_by_features(project_root=str(tmp_path))

        assert catalog_passed
        assert coverage_passed


class TestApiContractComplete:
    def test_fails_when_product_endpoints_use_root_path(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "PRD.md").write_text("Como usuário quero criar clientes.\n", encoding="utf-8")
        (docs / "api_contract.md").write_text(
            "## Base URL\n\n"
            "## Endpoints\n\n"
            "**GET /**\n"
            "**POST /**\n",
            encoding="utf-8",
        )

        passed, detail = api_contract_complete(project_root=str(tmp_path))

        assert not passed
        assert "endpoint '/'" in detail
        assert "/api/<recurso>" in detail

    def test_fails_when_creation_product_has_no_post(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "PRD.md").write_text("Como usuário quero criar clientes.\n", encoding="utf-8")
        (docs / "api_contract.md").write_text(
            "## Base URL\n\n"
            "## Endpoints\n\n"
            "| Método | Path |\n"
            "|---|---|\n"
            "| GET | /clientes |\n"
            "| GET | /agenda |\n"
            "| GET | /cobrancas |\n"
            "| GET | /health |\n",
            encoding="utf-8",
        )

        passed, detail = api_contract_complete(project_root=str(tmp_path))

        assert not passed
        assert "nao tem POST" in detail

    def test_passes_complete_contract(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "PRD.md").write_text("Como usuário quero criar clientes.\n", encoding="utf-8")
        (docs / "api_contract.md").write_text(
            "## Base URL\n\n"
            "## Endpoints\n\n"
            "| Método | Path |\n"
            "|---|---|\n"
            "| GET | /api/clientes |\n"
            "| POST | /api/clientes |\n"
            "| GET | /api/agenda |\n"
            "| POST | /api/agenda |\n"
            "| GET | /health |\n",
            encoding="utf-8",
        )

        passed, detail = api_contract_complete(project_root=str(tmp_path))

        assert passed
        assert "endpoint" in detail

    def test_counts_bold_bullet_endpoints_but_still_requires_minimum(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "PRD.md").write_text("Como usuário quero criar clientes.\n", encoding="utf-8")
        (docs / "api_contract.md").write_text(
            "## Base URL\n\n"
            "## Endpoints\n\n"
            "- `GET /health`: health check.\n"
            "- **POST /clientes**: cria cliente.\n",
            encoding="utf-8",
        )

        passed, detail = api_contract_complete(project_root=str(tmp_path), min_endpoints=3)

        assert not passed
        assert "1 endpoint" in detail


class TestRelativeDatesOnly:
    def test_fails_on_absolute_iso_date(self, tmp_path):
        f = tmp_path / "docs" / "test_data.md"
        f.parent.mkdir()
        f.write_text("Agenda: HOJE (2026-07-08) 14:00\n", encoding="utf-8")

        passed, detail = relative_dates_only(project_root=str(tmp_path))

        assert not passed
        assert "data absoluta" in detail

    def test_passes_relative_dates(self, tmp_path):
        f = tmp_path / "docs" / "test_data.md"
        f.parent.mkdir()
        f.write_text("Agenda: HOJE 14:00; HOJE+1 09:00; HOJE-1 18:00\n", encoding="utf-8")

        passed, detail = relative_dates_only(project_root=str(tmp_path))

        assert passed
        assert "datas relativas" in detail


class TestMinUserStories:
    def test_sufficient_stories(self, tmp_path):
        f = tmp_path / "prd.md"
        content = "\n".join([f"### US-0{i} Story" for i in range(5)])
        f.write_text(content)
        passed, detail = min_user_stories("prd.md", 3, str(tmp_path))
        assert passed
        assert "5 user stories" in detail

    def test_insufficient_stories(self, tmp_path):
        f = tmp_path / "prd.md"
        f.write_text("### US-01 Story")
        passed, detail = min_user_stories("prd.md", 3, str(tmp_path))
        assert not passed
        assert "FAIL" in detail


class TestSectionsUnchanged:
    def test_passes_when_immutable_sections_are_identical(self, tmp_path):
        current = tmp_path / "docs" / "PRD.md"
        snapshot = tmp_path / "runs" / "01" / "state" / "prd_rewrite_baseline.md"
        current.parent.mkdir(parents=True)
        snapshot.parent.mkdir(parents=True)

        baseline = (
            "# PRD\n\n"
            "## Hipotese\nTexto base.\n\n"
            "## Visao\nVisao original.\n\n"
            "## User Stories\n### US-01 — Fluxo\nHistoria original.\n\n"
            "## 8.5 Contrato de Navegacao UI\nNovo contrato.\n"
        )
        current.write_text(baseline + "\n## 8.6 Contrato de Integracao HTTP\nHealth e proxy.\n")
        snapshot.write_text(baseline)

        passed, detail = sections_unchanged(
            "docs/PRD.md",
            "runs/01/state/prd_rewrite_baseline.md",
            ["Hipotese", "Visao", "User Stories"],
            str(tmp_path),
        )

        assert passed
        assert "secoes preservadas" in detail

    def test_fails_when_vision_changes(self, tmp_path):
        current = tmp_path / "docs" / "PRD.md"
        snapshot = tmp_path / "runs" / "01" / "state" / "prd_rewrite_baseline.md"
        current.parent.mkdir(parents=True)
        snapshot.parent.mkdir(parents=True)

        snapshot.write_text(
            "# PRD\n\n"
            "## Hipotese\nTexto base.\n\n"
            "## Visao\nVisao original.\n\n"
            "## User Stories\n### US-01 — Fluxo\nHistoria original.\n"
        )
        current.write_text(
            "# PRD\n\n"
            "## Hipotese\nTexto base.\n\n"
            "## Visao\nVisao reescrita com novo escopo.\n\n"
            "## User Stories\n### US-01 — Fluxo\nHistoria original.\n"
        )

        passed, detail = sections_unchanged(
            "docs/PRD.md",
            "runs/01/state/prd_rewrite_baseline.md",
            ["Hipotese", "Visao", "User Stories"],
            str(tmp_path),
        )

        assert not passed
        assert "Visao" in detail


class TestDemandCoverage:
    def test_passes_when_prd_mentions_requested_features(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "demanda.md").write_text(
            "- Quero criar tarefas com prioridade\n"
            "- Preciso filtrar tarefas por status\n"
        )
        (docs / "PRD.md").write_text(
            "## User Stories\n"
            "### US-01 - Criar tarefas\n"
            "Como usuário, quero criar tarefas com prioridade.\n\n"
            "### US-02 - Filtrar por status\n"
            "Como usuário, quero filtrar tarefas por status.\n"
        )

        passed, detail = demand_coverage(project_root=str(tmp_path))

        assert passed
        assert "PASS" in detail

    def test_fails_when_feature_is_missing(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "demanda.md").write_text(
            "- Quero criar tarefas com prioridade\n"
            "- Preciso exportar relatórios em CSV\n"
        )
        (docs / "PRD.md").write_text(
            "## User Stories\n"
            "### US-01 - Criar tarefas\n"
            "Como usuário, quero criar tarefas com prioridade.\n"
        )

        passed, detail = demand_coverage(project_root=str(tmp_path))

        assert not passed
        assert "exportar" in detail

    def test_fails_when_short_format_requirement_is_replaced(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "demanda.md").write_text("- Preciso exportar relatórios em CSV\n")
        (docs / "PRD.md").write_text(
            "## User Stories\n"
            "### US-01 - Exportar relatórios\n"
            "Como usuário, quero exportar relatórios em PDF.\n"
        )

        passed, detail = demand_coverage(project_root=str(tmp_path))

        assert not passed
        assert "csv" in detail.lower()


class TestUICriteriaCoverage:
    def test_ui_criteria_ids_passes_for_stable_ids(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir(parents=True)
        (docs / "ui_criteria.md").write_text(
            "- [ ] C01: Tela inicial mostra resumo.\n"
            "- [ ] C02: Navegação principal visível.\n",
            encoding="utf-8",
        )

        passed, detail = ui_criteria_ids(min_count=2, project_root=str(tmp_path))

        assert passed
        assert "2 criterios" in detail

    def test_ui_criteria_ids_fails_without_stable_ids(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir(parents=True)
        (docs / "ui_criteria.md").write_text("- Tela inicial mostra resumo.\n", encoding="utf-8")

        passed, detail = ui_criteria_ids(min_count=1, project_root=str(tmp_path))

        assert not passed
        assert "use IDs" in detail

    def test_passes_when_identified_criteria_are_reported_and_source_has_component(self, tmp_path):
        docs = tmp_path / "docs"
        src = tmp_path / "project" / "frontend" / "src"
        docs.mkdir(parents=True)
        src.mkdir(parents=True)
        (docs / "ui_criteria.md").write_text(
            "# UI Criteria\n\n"
            "- [ ] C01: Tela de filtros possui menu suspenso para status.\n"
            "- [ ] C02: Botões usam ícone SVG.\n",
            encoding="utf-8",
        )
        (docs / "screenshot-review.md").write_text(
            "# Review\n\n"
            "| Critério | Resultado |\n"
            "|---|---|\n"
            "| C01 | PASS |\n"
            "| C02 | PASS |\n",
            encoding="utf-8",
        )
        (src / "main.js").write_text(
            '<select data-testid="status-dropdown"><option>Aberto</option></select><svg></svg>',
            encoding="utf-8",
        )

        passed, detail = ui_criteria_coverage(source_dir="project/frontend/src", project_root=str(tmp_path))

        assert passed
        assert "2 criterios" in detail

    def test_report_pass_table_ignores_domain_words_in_evidence(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir(parents=True)
        (docs / "ui_criteria.md").write_text(
            "- [ ] C9: Dashboard mostra cobranças pendentes.\n",
            encoding="utf-8",
        )
        (docs / "screenshot-review.md").write_text(
            "| Critério | Resultado | Evidência |\n"
            "|---|---|---|\n"
            "| C9 | PASS | Dashboard exibe total de cobranças pendentes |\n",
            encoding="utf-8",
        )

        passed, detail = ui_criteria_coverage(project_root=str(tmp_path))

        assert passed, detail

    def test_report_matches_zero_padded_criterion_ids(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir(parents=True)
        (docs / "ui_criteria.md").write_text(
            "- [ ] C09: Dashboard mostra resumo.\n",
            encoding="utf-8",
        )
        (docs / "screenshot-review.md").write_text("| C9 | PASS |\n", encoding="utf-8")

        passed, detail = ui_criteria_coverage(project_root=str(tmp_path))

        assert passed, detail

    def test_passes_with_code_evidence_without_visual_report(self, tmp_path):
        docs = tmp_path / "docs"
        src = tmp_path / "project" / "frontend" / "src"
        docs.mkdir(parents=True)
        src.mkdir(parents=True)
        (docs / "ui_criteria.md").write_text(
            "- [ ] C01: Tela de filtros possui menu suspenso para status.\n"
            "- [ ] C02: Botões usam ícone SVG.\n",
            encoding="utf-8",
        )
        (src / "main.js").write_text(
            '<section data-ui-criteria="C01"><select><option>Aberto</option></select></section>'
            '<button data-ui-criteria="C02"><svg></svg></button>',
            encoding="utf-8",
        )

        passed, detail = ui_criteria_coverage(
            report_path=None,
            source_dir="project/frontend/src",
            evidence="code",
            project_root=str(tmp_path),
        )

        assert passed
        assert "codigo=2" in detail

    def test_any_evidence_accepts_code_when_report_is_missing(self, tmp_path):
        docs = tmp_path / "docs"
        src = tmp_path / "project" / "frontend" / "src"
        docs.mkdir(parents=True)
        src.mkdir(parents=True)
        (docs / "ui_criteria.md").write_text("- [ ] C01: Tela inicial mostra resumo.\n", encoding="utf-8")
        (src / "main.js").write_text('<main data-ui-criteria="C01"></main>', encoding="utf-8")

        passed, detail = ui_criteria_coverage(
            report_path="docs/screenshot-review.md",
            source_dir="project/frontend/src",
            project_root=str(tmp_path),
        )

        assert passed
        assert "evidence=any" in detail

    def test_fails_when_report_does_not_cover_every_criterion(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir(parents=True)
        (docs / "ui_criteria.md").write_text(
            "- [ ] C01: Tela inicial mostra resumo.\n"
            "- [ ] C02: Menu suspenso para status.\n",
            encoding="utf-8",
        )
        (docs / "screenshot-review.md").write_text("| C01 | PASS |\n", encoding="utf-8")

        passed, detail = ui_criteria_coverage(project_root=str(tmp_path))

        assert not passed
        assert "C02" in detail

    def test_fails_when_component_mentioned_has_no_source_evidence(self, tmp_path):
        docs = tmp_path / "docs"
        src = tmp_path / "project" / "frontend" / "src"
        docs.mkdir(parents=True)
        src.mkdir(parents=True)
        (docs / "ui_criteria.md").write_text(
            "- [ ] C01: Tela de filtros possui menu suspenso para status.\n",
            encoding="utf-8",
        )
        (src / "main.js").write_text('<button data-ui-criteria="C01">Status</button>', encoding="utf-8")

        passed, detail = ui_criteria_coverage(
            report_path=None,
            source_dir="project/frontend/src",
            evidence="code",
            project_root=str(tmp_path),
        )

        assert not passed
        assert "menu suspenso" in detail or "dropdown" in detail

    def test_fails_when_criteria_have_no_ids(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir(parents=True)
        (docs / "ui_criteria.md").write_text("- Menu suspenso para status.\n", encoding="utf-8")
        (docs / "screenshot-review.md").write_text("PASS\n", encoding="utf-8")

        passed, detail = ui_criteria_coverage(project_root=str(tmp_path))

        assert not passed
        assert "nenhum criterio identificado" in detail


class TestPytestRedQuality:
    def test_passes_for_meaningful_red_tests(self, tmp_path):
        tests = tmp_path / "project" / "tests"
        tests.mkdir(parents=True)
        (tests / "test_contract.py").write_text(
            "import pytest\n\n"
            "from backend import main\n\n"
            "def test_health_contract():\n"
            "    payload = main.health()\n"
            "    assert payload['status'] == 'ok'\n\n"
            "def test_create_cliente_validation():\n"
            "    with pytest.raises(ValueError):\n"
            "        main.create_cliente({'nome': ''})\n\n"
            "def test_total_pendente():\n"
            "    assert main.total_pendente() == 100\n",
            encoding="utf-8",
        )

        passed, detail = pytest_red_quality(project_root=str(tmp_path))

        assert passed, detail
        assert "3 teste" in detail

    def test_fails_for_pass_only_stub_tests(self, tmp_path):
        tests = tmp_path / "project" / "tests"
        tests.mkdir(parents=True)
        (tests / "test_client_manager.py").write_text(
            "import pytest\n\n"
            "@pytest.mark.asyncio\n"
            "async def test_post_cliente_success():\n"
            "    pass\n",
            encoding="utf-8",
        )
        (tests / "test_servico_manager.test").write_text(
            "def test_not_collected():\n"
            "    assert True\n",
            encoding="utf-8",
        )

        passed, detail = pytest_red_quality(project_root=str(tmp_path))

        assert not passed
        assert "min 3" in detail or "pass-only" in detail


class TestCommandSucceeds:
    def test_fails_when_pipeline_left_side_fails(self, tmp_path):
        passed, detail = command_succeeds("python -c 'raise SystemExit(7)' | tail -5", str(tmp_path))

        assert not passed
        assert "código 7" in detail

    def test_fails_when_pytest_runs_zero_tests(self, tmp_path):
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()

        passed, detail = command_succeeds("python -m pytest tests/ -q 2>&1 | tail -5", str(tmp_path))

        assert not passed
        assert "nenhum teste" in detail or "código 5" in detail

    def test_reruns_silent_command_for_diagnostics(self, tmp_path):
        silent = MagicMock(returncode=1, stdout="", stderr="")
        diagnostic = MagicMock(returncode=1, stdout="", stderr="Missing script: \"build\"\n")

        with patch(
            "ft.engine.validators.artifacts._run_shell_command",
            side_effect=[silent, diagnostic],
        ):
            passed, detail = command_succeeds("npm run build --silent", str(tmp_path))

        assert not passed
        assert "diagnostico sem --silent" in detail
        assert "Missing script" in detail

    def test_timeout_kills_child_before_it_can_write_marker(self, tmp_path):
        command = (
            "bash -c '(touch child-started; sleep 0.5; touch timeout-marker) & "
            "while [ ! -e child-started ]; do :; done; sleep 5'"
        )

        passed, detail = command_succeeds(command, str(tmp_path), timeout=0.2)

        assert not passed
        assert "excedeu" in detail
        assert (tmp_path / "child-started").exists()
        time.sleep(0.6)
        assert not (tmp_path / "timeout-marker").exists()

    def test_diagnostic_timeout_also_kills_child_process_group(self, tmp_path):
        script = tmp_path / "diagnostic.sh"
        script.write_text(
            "#!/usr/bin/env bash\n"
            "if [[ ${1:-} == --silent ]]; then exit 1; fi\n"
            "(touch diagnostic-child-started; sleep 0.5; "
            "touch diagnostic-timeout-marker) &\n"
            "while [[ ! -e diagnostic-child-started ]]; do :; done\n"
            "sleep 5\n",
            encoding="utf-8",
        )

        passed, detail = command_succeeds(
            "bash diagnostic.sh --silent",
            str(tmp_path),
            timeout=0.2,
        )

        assert not passed
        assert "código 1" in detail
        assert (tmp_path / "diagnostic-child-started").exists()
        time.sleep(0.6)
        assert not (tmp_path / "diagnostic-timeout-marker").exists()


# ---------------------------------------------------------------------------
# parallel — independence check
# ---------------------------------------------------------------------------

class TestCheckIndependence:
    def test_disjoint_outputs(self):
        assert check_independence(["src/a.py"], ["src/b.py"]) is True

    def test_overlapping_outputs(self):
        assert check_independence(["src/a.py"], ["src/a.py"]) is False

    def test_empty_outputs(self):
        assert check_independence([], ["src/a.py"]) is True

    def test_partial_overlap(self):
        assert check_independence(
            ["src/a.py", "src/shared.py"],
            ["src/b.py", "src/shared.py"]
        ) is False


# ---------------------------------------------------------------------------
# gates
# ---------------------------------------------------------------------------

class TestGateAcceptanceCli:
    def test_skip_for_ui_projects(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir(parents=True)
        (docs / "tech_stack.md").write_text("interface_type: ui\n")

        passed, detail = gate_acceptance_cli(str(tmp_path))

        assert passed
        assert "pulado" in detail

    def test_fail_without_report_for_api_projects(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir(parents=True)
        (docs / "tech_stack.md").write_text("interface_type: api\n")

        passed, detail = gate_acceptance_cli(str(tmp_path))

        assert not passed
        assert "acceptance-cli-report.md" in detail


class TestGateKbReview:
    def test_ui_with_auxiliary_backend_and_no_http_dependency_passes(self, tmp_path):
        docs = tmp_path / "docs"
        frontend_src = tmp_path / "frontend" / "src"
        src_dir = tmp_path / "src" / "pokemon"

        docs.mkdir(parents=True)
        frontend_src.mkdir(parents=True)
        src_dir.mkdir(parents=True)

        (docs / "tech_stack.md").write_text("interface_type: ui\n")
        (tmp_path / "frontend" / "package.json").write_text("{}\n")
        (tmp_path / "frontend" / "index.html").write_text("<!doctype html>\n")
        (tmp_path / "frontend" / "vite.config.js").write_text(
            "import { defineConfig } from 'vite'\n"
            "export default defineConfig({ server: { host: true } })\n"
        )
        (frontend_src / "App.jsx").write_text(
            "import { BrowserRouter, Route } from 'react-router-dom'\n"
            "export default function App() { return <BrowserRouter><Route path='/' element={null} /></BrowserRouter> }\n"
        )
        (tmp_path / "main.py").write_text(
            "from fastapi import FastAPI\napp = FastAPI()\n"
        )
        (src_dir / "api.py").write_text("def helper():\n    return 'ok'\n")

        passed, detail = gate_kb_review(str(tmp_path))

        assert passed
        assert "PASS" in detail

    def test_ui_with_frontend_http_dependency_and_backend_fails(self, tmp_path):
        docs = tmp_path / "docs"
        frontend_src = tmp_path / "frontend" / "src"
        src_dir = tmp_path / "src" / "pokemon"

        docs.mkdir(parents=True)
        frontend_src.mkdir(parents=True)
        src_dir.mkdir(parents=True)

        (docs / "tech_stack.md").write_text("interface_type: ui\n")
        (tmp_path / "frontend" / "package.json").write_text("{}\n")
        (tmp_path / "frontend" / "index.html").write_text("<!doctype html>\n")
        (tmp_path / "frontend" / "vite.config.js").write_text(
            "import { defineConfig } from 'vite'\n"
            "export default defineConfig({ server: { host: true } })\n"
        )
        (frontend_src / "App.jsx").write_text(
            "import { BrowserRouter, Route } from 'react-router-dom'\n"
            "export default function App() { fetch('/savegames'); return <BrowserRouter><Route path='/' element={null} /></BrowserRouter> }\n"
        )
        (tmp_path / "main.py").write_text(
            "from fastapi import FastAPI\napp = FastAPI()\n"
        )
        (src_dir / "api.py").write_text("def helper():\n    return 'ok'\n")

        passed, detail = gate_kb_review(str(tmp_path))

        assert not passed
        assert "provável interface_type=mixed" in detail
