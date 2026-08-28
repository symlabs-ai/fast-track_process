"""Triagem por severidade nos reviews (fix forward).

O contrato antigo era binário: qualquer finding forçava REJECTED e uma nova
rodada review→fix→review. Com severidade, só P0 bloqueia; P1/P2 aprovam com
findings e viram dívida registrada no backlog.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml

from ft.engine.validators.artifacts import (
    review_findings_tracked,
    review_outcome_valid,
)

SCOPE = """# Critérios

- C1 primeiro critério
- C2 segundo critério
"""


def _setup(tmp_path: Path, receipt: dict, markdown_verdict: str) -> Path:
    root = tmp_path
    docs = root / "docs"
    docs.mkdir(exist_ok=True)
    scope = docs / "ui_criteria.md"
    scope.write_text(SCOPE, encoding="utf-8")
    receipt = dict(receipt)
    receipt.setdefault("schema_version", 1)
    receipt["scope_sha256"] = hashlib.sha256(scope.read_bytes()).hexdigest()
    (docs / "review.yml").write_text(
        yaml.safe_dump(receipt, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    (docs / "review.md").write_text(
        f"# Review\n\nVERDICT: {markdown_verdict}\n", encoding="utf-8"
    )
    return root


def _validate(root: Path, **kwargs):
    return review_outcome_valid(
        path="docs/review.yml",
        scope_path="docs/ui_criteria.md",
        scope_pattern=r"\bC\d+\b",
        markdown_path="docs/review.md",
        project_root=str(root),
        **kwargs,
    )


def _results(c1: str = "PASS", c2: str = "PASS") -> list[dict]:
    return [
        {"ref": "C1", "result": c1, "evidence": ["shot-c1.png"]},
        {"ref": "C2", "result": c2, "evidence": ["shot-c2.png"]},
    ]


def _finding(severity: str | None, ref: str = "C2") -> dict:
    finding = {
        "id": "FND-001",
        "refs": [ref],
        "summary": "Espaçamento fora do especificado",
        "evidence": ["shot-c2.png"],
    }
    if severity is not None:
        finding["severity"] = severity
    return finding


# --------------------------------------------------------------- fix forward


@pytest.mark.parametrize("severity", ["P1", "P2"])
def test_non_blocking_findings_approve_with_findings(tmp_path, severity):
    root = _setup(
        tmp_path,
        {
            "verdict": "APPROVED_WITH_FINDINGS",
            "results": _results(c2="FAIL"),
            "findings": [_finding(severity)],
        },
        "APPROVED_WITH_FINDINGS",
    )
    ok, detail = _validate(root)
    assert ok, detail
    assert "APPROVED_WITH_FINDINGS" in detail


def test_gate_accepts_findings_only_when_opted_in(tmp_path):
    root = _setup(
        tmp_path,
        {
            "verdict": "APPROVED_WITH_FINDINGS",
            "results": _results(c2="FAIL"),
            "findings": [_finding("P1")],
        },
        "APPROVED_WITH_FINDINGS",
    )
    strict_ok, strict_detail = _validate(root, require_approved=True)
    assert not strict_ok
    assert "exige verdict" in strict_detail

    lenient_ok, _ = _validate(root, require_approved=True, allow_findings=True)
    assert lenient_ok


def test_p0_cannot_ride_along_as_approved(tmp_path):
    root = _setup(
        tmp_path,
        {
            "verdict": "APPROVED_WITH_FINDINGS",
            "results": _results(c2="FAIL"),
            "findings": [_finding("P0")],
        },
        "APPROVED_WITH_FINDINGS",
    )
    ok, detail = _validate(root)
    assert not ok
    assert "não admite findings P0" in detail


def test_pending_results_still_block_fix_forward(tmp_path):
    root = _setup(
        tmp_path,
        {
            "verdict": "APPROVED_WITH_FINDINGS",
            "results": _results(c2="PENDING"),
            "findings": [_finding("P1")],
        },
        "APPROVED_WITH_FINDINGS",
    )
    ok, detail = _validate(root)
    assert not ok
    assert "pendentes" in detail


# ------------------------------------------------------------------ rejected


def test_rejected_requires_a_blocking_finding(tmp_path):
    root = _setup(
        tmp_path,
        {
            "verdict": "REJECTED",
            "results": _results(c2="FAIL"),
            "findings": [_finding("P2")],
        },
        "REJECTED",
    )
    ok, detail = _validate(root)
    assert not ok
    assert "exige ao menos um finding P0" in detail


def test_missing_severity_stays_blocking_for_compatibility(tmp_path):
    """Receipts antigos, sem severidade, continuam válidos como REJECTED."""
    root = _setup(
        tmp_path,
        {
            "verdict": "REJECTED",
            "results": _results(c2="FAIL"),
            "findings": [_finding(None)],
        },
        "REJECTED",
    )
    ok, detail = _validate(root)
    assert ok, detail


def test_invalid_severity_is_rejected(tmp_path):
    root = _setup(
        tmp_path,
        {
            "verdict": "REJECTED",
            "results": _results(c2="FAIL"),
            "findings": [_finding("CRITICAL")],
        },
        "REJECTED",
    )
    ok, detail = _validate(root)
    assert not ok
    assert "severity" in detail


def test_markdown_verdict_must_match_receipt(tmp_path):
    root = _setup(
        tmp_path,
        {
            "verdict": "APPROVED_WITH_FINDINGS",
            "results": _results(c2="FAIL"),
            "findings": [_finding("P1")],
        },
        "APPROVED",
    )
    ok, detail = _validate(root)
    assert not ok
    assert "diverge" in detail


def test_clean_approved_still_forbids_findings(tmp_path):
    root = _setup(
        tmp_path,
        {
            "verdict": "APPROVED",
            "results": _results(),
            "findings": [_finding("P2")],
        },
        "APPROVED",
    )
    ok, detail = _validate(root)
    assert not ok
    assert "findings vazio" in detail


# ------------------------------------------------- dívida registrada


def _accepted_receipt(root: Path) -> None:
    docs = root / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "review.yml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "verdict": "APPROVED_WITH_FINDINGS",
                "findings": [{"id": "FND-001", "summary": "x"}],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def test_accepted_findings_must_reach_the_backlog(tmp_path):
    _accepted_receipt(tmp_path)
    (tmp_path / "docs" / "PROJECT_BACKLOG.md").write_text(
        "| ID | Título |\n| --- | --- |\n| PB-001 | outro item |\n", encoding="utf-8"
    )
    ok, detail = review_findings_tracked(
        review_paths=["docs/review.yml"], project_root=str(tmp_path)
    )
    assert not ok
    assert "FND-001" in detail


def test_tracked_findings_pass(tmp_path):
    _accepted_receipt(tmp_path)
    (tmp_path / "docs" / "PROJECT_BACKLOG.md").write_text(
        "| ID | Título |\n| --- | --- |\n| PB-002 | corrigir FND-001 |\n",
        encoding="utf-8",
    )
    ok, detail = review_findings_tracked(
        review_paths=["docs/review.yml"], project_root=str(tmp_path)
    )
    assert ok, detail


def test_rejected_receipts_are_not_tracked_debt(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "review.yml").write_text(
        yaml.safe_dump(
            {"schema_version": 1, "verdict": "REJECTED", "findings": [{"id": "F-9"}]},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    ok, detail = review_findings_tracked(
        review_paths=["docs/review.yml"], project_root=str(tmp_path)
    )
    assert ok
    assert "nenhum finding aceito" in detail


def test_missing_receipt_is_skipped(tmp_path):
    ok, _ = review_findings_tracked(
        review_paths=["docs/inexistente.yml"], project_root=str(tmp_path)
    )
    assert ok


# ------------------------------------------- parser do veredito em Markdown


@pytest.mark.parametrize(
    "line,expected",
    [
        ("VERDICT: APPROVED", "APPROVED"),
        ("Veredito: APPROVED", "APPROVED"),
        ("**VERDICT:** REJECTED", "REJECTED"),
        ("Resultado: **APPROVED_WITH_FINDINGS**", "APPROVED_WITH_FINDINGS"),
        ("`Parecer`: APPROVED", "APPROVED"),
    ],
)
def test_markdown_verdict_tolerates_emphasis_and_pt_label(tmp_path, line, expected):
    """O parecer é legível: negrito e rótulo em português não são ambiguidade."""
    from ft.engine.validators.artifacts import _review_markdown_verdict

    report = tmp_path / "review.md"
    report.write_text(f"# Parecer\n\n{line}\n", encoding="utf-8")
    assert _review_markdown_verdict(report) == expected


@pytest.mark.parametrize(
    "body",
    [
        "VERDICT: APPROVED\nVeredito: REJECTED\n",  # dois vereditos
        "# Parecer\n\nsem veredito nenhum\n",
        "Verdito: APPROVED\n",  # grafia incorreta não vira vocabulário aceito
    ],
)
def test_markdown_verdict_stays_strict(tmp_path, body):
    from ft.engine.validators.artifacts import _review_markdown_verdict

    report = tmp_path / "review.md"
    report.write_text(body, encoding="utf-8")
    with pytest.raises(ValueError, match="exatamente um veredito"):
        _review_markdown_verdict(report)
