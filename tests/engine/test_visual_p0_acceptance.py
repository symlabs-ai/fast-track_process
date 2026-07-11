from ft.engine.validators.artifacts import visual_p0_acceptance


def _write_report(tmp_path, text):
    report = tmp_path / "docs" / "visual-check-report.md"
    report.parent.mkdir(parents=True)
    report.write_text(text, encoding="utf-8")


def test_visual_acceptance_requires_single_explicit_pass(tmp_path):
    _write_report(
        tmp_path,
        "# Visual Check\n\n"
        "P0_ACCEPTANCE: PASS\n\n"
        "| Criterio | Resultado |\n"
        "|---|---|\n"
        "| C01 | PASS |\n"
        "| C02 | PASS COM NOTAS |\n",
    )

    passed, detail = visual_p0_acceptance(project_root=str(tmp_path))

    assert passed, detail


def test_visual_acceptance_rejects_audit_pass_without_product_verdict(tmp_path):
    _write_report(tmp_path, "# Visual Check\n\nResultado: PASS\n")

    passed, detail = visual_p0_acceptance(project_root=str(tmp_path))

    assert not passed
    assert "esperado exatamente" in detail


def test_visual_acceptance_rejects_failed_criterion(tmp_path):
    _write_report(
        tmp_path,
        "P0_ACCEPTANCE: PASS\n\n"
        "| Criterio | Resultado |\n"
        "|---|---|\n"
        "| C09 | FAIL - componente ausente |\n",
    )

    passed, detail = visual_p0_acceptance(project_root=str(tmp_path))

    assert not passed
    assert "C09" in detail


def test_visual_acceptance_rejects_duplicate_verdicts(tmp_path):
    _write_report(
        tmp_path,
        "P0_ACCEPTANCE: PASS\nP0_ACCEPTANCE: PASS\n",
    )

    passed, _ = visual_p0_acceptance(project_root=str(tmp_path))

    assert not passed
