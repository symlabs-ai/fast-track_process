"""Tests for validators that call subprocess (mocked)."""

import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from ft.engine.validators.artifacts import (
    tests_pass as val_tests_pass,
    tests_fail as val_tests_fail,
    coverage_min as val_coverage_min,
)
from ft.engine.validators.code import lint_clean, format_check
from ft.engine.validators.gates import gate_delivery, gate_smoke


# ---------------------------------------------------------------------------
# tests_pass / tests_fail
# ---------------------------------------------------------------------------

class TestTestsPass:
    def test_pass_when_returncode_zero(self):
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = "5 passed in 0.1s\n"
        with patch("subprocess.run", return_value=mock):
            passed, detail = val_tests_pass(".")
        assert passed
        assert "passed" in detail

    def test_fail_when_returncode_nonzero(self):
        mock = MagicMock()
        mock.returncode = 1
        mock.stdout = "FAILED test_foo.py::test_bar\n2 failed in 0.2s\n"
        with patch("subprocess.run", return_value=mock):
            passed, detail = val_tests_pass(".")
        assert not passed
        assert "FAIL" in detail


class TestTestsFail:
    def test_pass_when_val_tests_fail(self):
        mock = MagicMock()
        mock.returncode = 1
        with patch("subprocess.run", return_value=mock):
            passed, detail = val_tests_fail(".")
        assert passed
        assert "red phase" in detail

    def test_fail_when_val_tests_pass(self):
        mock = MagicMock()
        mock.returncode = 0
        with patch("subprocess.run", return_value=mock):
            passed, detail = val_tests_fail(".")
        assert not passed
        assert "FAIL" in detail


class TestCoverageMin:
    def test_coverage_above_min(self):
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = "TOTAL    100    10    90%\n"
        with patch("subprocess.run", return_value=mock):
            passed, detail = val_coverage_min(80, ".")
        assert passed
        assert "90%" in detail

    def test_coverage_below_min(self):
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = "TOTAL    100    50    50%\n"
        with patch("subprocess.run", return_value=mock):
            passed, detail = val_coverage_min(80, ".")
        assert not passed
        assert "FAIL" in detail

    def test_no_total_line(self):
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = "no coverage data\n"
        with patch("subprocess.run", return_value=mock):
            passed, detail = val_coverage_min(80, ".")
        assert not passed
        assert "FAIL" in detail


# ---------------------------------------------------------------------------
# lint_clean
# ---------------------------------------------------------------------------

class TestLintClean:
    def test_clean_when_returncode_zero(self):
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = "All checks passed.\n"
        with patch("subprocess.run", return_value=mock):
            passed, detail = lint_clean(project_root=".")
        assert passed
        assert "sem erros" in detail

    def test_fail_when_errors(self):
        mock = MagicMock()
        mock.returncode = 1
        mock.stdout = "src/foo.py:1:1: E001 error\nFound 1 error.\n"
        with patch("subprocess.run", return_value=mock):
            passed, detail = lint_clean(project_root=".")
        assert not passed
        assert "FAIL" in detail


class TestFormatCheck:
    def test_formatted_when_returncode_zero(self):
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = ""
        mock.stderr = ""
        with patch("subprocess.run", return_value=mock):
            passed, detail = format_check(project_root=".")
        assert passed

    def test_unformatted_when_returncode_nonzero(self):
        mock = MagicMock()
        mock.returncode = 1
        mock.stdout = ""
        mock.stderr = "Would reformat src/foo.py\n1 file would be reformatted\n"
        with patch("subprocess.run", return_value=mock):
            passed, detail = format_check(project_root=".")
        assert not passed
        assert "FAIL" in detail


# ---------------------------------------------------------------------------
# gate_delivery
# ---------------------------------------------------------------------------

class TestGateDelivery:
    def test_pass_when_files_exist_and_val_tests_pass(self, tmp_path):
        (tmp_path / "main.py").write_text("x")
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = "5 passed\n"
        with patch("subprocess.run", return_value=mock):
            passed, detail = gate_delivery(["main.py"], str(tmp_path))
        assert passed

    def test_fail_when_file_missing(self, tmp_path):
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = "5 passed\n"
        with patch("subprocess.run", return_value=mock):
            passed, detail = gate_delivery(["missing.py"], str(tmp_path))
        assert not passed
        assert "FAIL" in detail

    def test_fail_when_val_tests_fail(self, tmp_path):
        (tmp_path / "main.py").write_text("x")
        mock = MagicMock()
        mock.returncode = 1
        mock.stdout = "1 failed\n"
        with patch("subprocess.run", return_value=mock):
            passed, detail = gate_delivery(["main.py"], str(tmp_path))
        assert not passed
