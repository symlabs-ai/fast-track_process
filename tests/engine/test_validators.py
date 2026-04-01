"""Unit tests for ft.engine.validators.*"""

import pytest
from pathlib import Path

from ft.engine.validators.artifacts import (
    file_exists, min_lines, has_sections, min_user_stories,
)
from ft.engine.parallel import check_independence


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
        d = tmp_path / "project" / "docs"
        d.mkdir(parents=True)
        (d / "PRD.md").write_text("content")
        passed, _ = file_exists("project/docs/PRD.md", str(tmp_path))
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
