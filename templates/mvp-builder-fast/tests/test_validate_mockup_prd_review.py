from __future__ import annotations

import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path
from typing import Any

import yaml

SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts" / "validate_mockup_prd_review.py"
)
SPEC = importlib.util.spec_from_file_location("validate_mockup_prd_review", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class MockupPrdReviewValidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.root = Path(self._tempdir.name)
        self.prd = self.root / "docs" / "PRD.md"
        self.ui_criteria = self.root / "docs" / "ui_criteria.md"
        self.mockups = self.root / "docs" / "mockups"
        self.screen_map = self.mockups / "screen-map.yml"
        self.markdown = self.mockups / "prd-coherence-review.md"
        self.review_yaml = self.mockups / "prd-coherence-review.yml"

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def _fixture(
        self,
        *,
        mapped: int = 2,
        auxiliary: bool = True,
        results: dict[str, str] | None = None,
        findings: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        results = results or {}
        total = mapped + (1 if auxiliary else 0)
        self.mockups.mkdir(parents=True, exist_ok=True)
        (self.mockups / "images").mkdir(exist_ok=True)

        prd_lines = ["# PRD"]
        for index in range(1, mapped + 1):
            prd_lines.append(f"REQ-{index:03d}: requisito real da tela T{index:02d}.")
        if auxiliary:
            prd_lines.append("REQ-AUX: superfície auxiliar necessária ao fluxo.")
        self.prd.parent.mkdir(parents=True, exist_ok=True)
        self.prd.write_text("\n".join(prd_lines) + "\n", encoding="utf-8")
        self.ui_criteria.write_text(
            "# UI Criteria\n"
            + "\n".join(
                f"- C{index:03d}: critério {index}" for index in range(1, total + 1)
            )
            + "\n",
            encoding="utf-8",
        )

        screens: list[dict[str, Any]] = []
        views: list[dict[str, Any]] = []
        for index in range(1, total + 1):
            screen_id = f"S{index:02d}"
            state_id = screen_id
            image = f"images/{state_id}-main.png"
            (self.mockups / image).write_bytes(b"\x89PNG\r\n\x1a\nfixture")
            screen: dict[str, Any] = {
                "id": screen_id,
                "title": f"Screen {index}",
                "criteria": [f"C{index:03d}"],
                "acceptance_criteria": "Aceitar texto com vírgulas, ponto e vírgula; sem serialização CSV.",
                "states": [
                    {
                        "id": state_id,
                        "label": "Main",
                        "target": "generic",
                        "image": image,
                        "width": 1440,
                        "height": 900,
                        "prompt_ref": f"prompt-{state_id}",
                    }
                ],
            }
            if index <= mapped:
                screen["prd_screen_id"] = f"T{index:02d}"
                requirement = f"REQ-{index:03d}"
                prd_screen_id = f"T{index:02d}"
            else:
                requirement = "REQ-AUX"
                prd_screen_id = "N/A"
            screens.append(screen)
            result = results.get(state_id, "COHERENT")
            views.append(
                {
                    "state_id": state_id,
                    "screen_id": screen_id,
                    "prd_screen_id": prd_screen_id,
                    "requirements": [requirement],
                    "criteria": [f"C{index:03d}"],
                    "result": result,
                    "blocking": result == "INCOHERENT",
                    "observation": f"Resultado verificável para {state_id}.",
                    "image": image,
                }
            )

        screen_map = {"schema_version": 1, "screens": screens}
        self.screen_map.write_text(
            yaml.safe_dump(screen_map, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        report_findings = findings or []
        report = {
            "schema_version": 1,
            "prd_sha256": _sha256(self.prd),
            "ui_criteria_sha256": _sha256(self.ui_criteria),
            "screen_map_sha256": _sha256(self.screen_map),
            "verdict": "REJECTED"
            if any(view["result"] == "INCOHERENT" for view in views)
            else "APPROVED",
            "summary": {
                "total_views": len(views),
                "coherent": sum(view["result"] == "COHERENT" for view in views),
                "coherent_with_reservation": sum(
                    view["result"] == "COHERENT_WITH_RESERVATION" for view in views
                ),
                "incoherent": sum(view["result"] == "INCOHERENT" for view in views),
            },
            "views": views,
            "findings": report_findings,
        }
        self._write_review(report)
        return report

    def _write_review(self, report: dict[str, Any]) -> None:
        self.review_yaml.write_text(
            yaml.safe_dump(report, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        summary = report["summary"]
        lines = [
            "# PRD–Mockup Coherence Review",
            "",
            f"VERDICT: {report['verdict']}",
            "",
            "## Resumo",
            "",
            f"- TOTAL_VIEWS: {summary['total_views']}",
            f"- COHERENT: {summary['coherent']}",
            f"- COHERENT_WITH_RESERVATION: {summary['coherent_with_reservation']}",
            f"- INCOHERENT: {summary['incoherent']}",
            "",
            "## Achados Transversais",
            "",
        ]
        images = {view["state_id"]: view["image"] for view in report["views"]}
        if report["findings"]:
            lines.extend(
                f"- FINDING: {finding['id']} | STATE: {finding['state_id']} | EVIDENCE: {images[finding['state_id']]}"
                for finding in report["findings"]
            )
        else:
            lines.append("- Nenhum finding transversal.")
        lines.extend(["", "## Revisão por State", ""])
        lines.extend(
            "- STATE: {state_id} | SCREEN: {screen_id} | PRD_SCREEN: {prd_screen_id} "
            "| RESULT: {result} | IMAGE: {image}".format(**view)
            for view in report["views"]
        )
        self.markdown.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _validate(self) -> None:
        VALIDATOR.validate(
            prd_path=self.prd,
            ui_criteria_path=self.ui_criteria,
            screen_map_path=self.screen_map,
            markdown_path=self.markdown,
            review_yaml_path=self.review_yaml,
        )

    def test_accepts_t01_to_t50_plus_auxiliary_and_punctuated_prose(self) -> None:
        self._fixture(
            mapped=50,
            auxiliary=True,
            results={"S51": "COHERENT_WITH_RESERVATION"},
        )
        self._validate()

    def test_accepts_rejected_with_actionable_png_finding(self) -> None:
        finding = {
            "id": "F-001",
            "state_id": "S01",
            "expected": "Mostrar a ação primária definida no PRD.",
            "observed": "A ação primária não aparece no PNG.",
            "evidence": ["images/S01-main.png"],
            "blocking": True,
        }
        self._fixture(
            mapped=1,
            auxiliary=False,
            results={"S01": "INCOHERENT"},
            findings=[finding],
        )
        self._validate()

    def test_rejects_stale_hash_and_incorrect_count(self) -> None:
        cases = ("stale_hash", "wrong_count")
        for case in cases:
            with self.subTest(case=case):
                report = self._fixture(mapped=1, auxiliary=False)
                if case == "stale_hash":
                    report["prd_sha256"] = "0" * 64
                    expected_error = "stale"
                else:
                    report["summary"]["coherent"] = 0
                    expected_error = "incorrect count"
                self._write_review(report)
                with self.assertRaisesRegex(VALIDATOR.ValidationError, expected_error):
                    self._validate()

    def test_rejects_state_order_and_prd_mapping_drift(self) -> None:
        cases = ("order", "mapped_tnn", "auxiliary_tnn")
        for case in cases:
            with self.subTest(case=case):
                report = self._fixture(mapped=2, auxiliary=True)
                if case == "order":
                    report["views"][0], report["views"][1] = (
                        report["views"][1],
                        report["views"][0],
                    )
                    expected_error = "differs from screen-map"
                elif case == "mapped_tnn":
                    report["views"][0]["prd_screen_id"] = "T02"
                    expected_error = "prd_screen_id differs"
                else:
                    report["views"][-1]["prd_screen_id"] = "T03"
                    expected_error = "prd_screen_id differs"
                self._write_review(report)
                with self.assertRaisesRegex(VALIDATOR.ValidationError, expected_error):
                    self._validate()

    def test_rejects_invalid_enum_and_blocking_reservation(self) -> None:
        cases = ("enum", "blocking_reservation")
        for case in cases:
            with self.subTest(case=case):
                report = self._fixture(mapped=1, auxiliary=False)
                if case == "enum":
                    report["views"][0]["result"] = "MAYBE"
                    expected_error = "result must be one of"
                else:
                    report["views"][0]["result"] = "COHERENT_WITH_RESERVATION"
                    report["views"][0]["blocking"] = True
                    report["summary"]["coherent"] = 0
                    report["summary"]["coherent_with_reservation"] = 1
                    expected_error = "blocking must be true only"
                self._write_review(report)
                with self.assertRaisesRegex(VALIDATOR.ValidationError, expected_error):
                    self._validate()

    def test_rejects_missing_or_non_actionable_findings(self) -> None:
        cases = ("missing", "missing_png", "same_text")
        for case in cases:
            with self.subTest(case=case):
                finding = {
                    "id": "F-001",
                    "state_id": "S01",
                    "expected": "Expected behavior",
                    "observed": "Observed defect",
                    "evidence": ["images/S01-main.png"],
                    "blocking": True,
                }
                findings = [] if case == "missing" else [finding]
                if case == "missing_png":
                    finding["evidence"] = ["inspection note"]
                elif case == "same_text":
                    finding["observed"] = finding["expected"]
                self._fixture(
                    mapped=1,
                    auxiliary=False,
                    results={"S01": "INCOHERENT"},
                    findings=findings,
                )
                expected_error = {
                    "missing": "must cover every",
                    "missing_png": "must include the exact state PNG",
                    "same_text": "expected and observed must differ",
                }[case]
                with self.assertRaisesRegex(VALIDATOR.ValidationError, expected_error):
                    self._validate()

    def test_rejects_unsafe_image_path(self) -> None:
        self._fixture(mapped=1, auxiliary=False)
        screen_map = yaml.safe_load(self.screen_map.read_text(encoding="utf-8"))
        screen_map["screens"][0]["states"][0]["image"] = "../escape.png"
        self.screen_map.write_text(
            yaml.safe_dump(screen_map, sort_keys=False), encoding="utf-8"
        )
        with self.assertRaisesRegex(
            VALIDATOR.ValidationError, "must stay under images"
        ):
            self._validate()

    def test_rejects_markdown_yaml_parity_drift(self) -> None:
        self._fixture(mapped=1, auxiliary=False)
        text = self.markdown.read_text(encoding="utf-8")
        self.markdown.write_text(
            text.replace("RESULT: COHERENT", "RESULT: INCOHERENT"),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            VALIDATOR.ValidationError, "Markdown state coverage"
        ):
            self._validate()


if __name__ == "__main__":
    unittest.main()
