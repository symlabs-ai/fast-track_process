"""Unit tests for ft.engine.validators.*"""

from pathlib import Path

import pytest

from ft.engine.parallel import check_independence
from ft.engine.validators.artifacts import (
    command_succeeds,
    demand_coverage,
    file_exists,
    has_sections,
    min_lines,
    min_user_stories,
    sections_unchanged,
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
