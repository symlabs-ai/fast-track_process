from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "templates"
    / "mvp-builder-fast"
    / "scripts"
    / "validate_operational_readiness.py"
)
SPEC = importlib.util.spec_from_file_location("validate_operational_readiness", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


def _fixture(
    root: Path,
    *,
    css: str = "body { font-size: 12px; }\n",
    source: str = "print('production')\n",
) -> tuple[Path, Path]:
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text(source, encoding="utf-8")
    (root / "src" / "app.css").write_text(css, encoding="utf-8")
    (root / "docs" / "evidence" / "operational").mkdir(parents=True)
    evidence = root / "docs" / "evidence" / "operational" / "OP-001.txt"
    evidence.write_text("created=persisted=observed=canary-123\n", encoding="utf-8")
    backlog = root / "docs" / "PROJECT_BACKLOG.md"
    backlog.write_text(
        "| ID | Prioridade | Status | Título |\n"
        "|---|---|---|---|\n"
        "| PB-001 | P0 | done | Jornada operacional |\n",
        encoding="utf-8",
    )
    report = {
        "schema_version": 1,
        "verdict": "APPROVED",
        "evidence_grade": "OPERATIONAL_REAL_DATA",
        "candidate_ref": "sha256:candidate",
        "production_entrypoint": "make run",
        "scope_path": "docs/PROJECT_BACKLOG.md",
        "runtime": {
            "mode": "production",
            "clean_start": True,
            "restart_verified": True,
            "demo_seed_enabled": False,
            "synthetic_runtime_records": False,
            "mock_providers_enabled": False,
            "persistence": "durable",
        },
        "ui": {
            "applicable": True,
            "minimum_observed_font_px": 12,
            "zoom_percent": 100,
            "evidence": ["docs/evidence/operational/OP-001.txt"],
        },
        "scan": {"production_paths": ["src"], "prohibited_hits": []},
        "results": [
            {
                "ref": "PB-001",
                "result": "PASS",
                "journeys": ["OP-001"],
                "evidence": ["docs/evidence/operational/OP-001.txt"],
            }
        ],
        "journeys": [
            {
                "id": "OP-001",
                "refs": ["PB-001"],
                "result": "PASS",
                "navigation_mode": "production_ui",
                "data_origin": "created_via_public_interface",
                "canary": {
                    "created_value": "canary-123",
                    "persisted_value": "canary-123",
                    "observed_value": "canary-123",
                },
                "persistence_restart_verified": True,
                "evidence": ["docs/evidence/operational/OP-001.txt"],
            }
        ],
        "findings": [],
    }
    report_path = root / "docs" / "operational-readiness.yml"
    report_path.write_text(yaml.safe_dump(report, sort_keys=False), encoding="utf-8")
    contract = {
        "definition_of_done": {
            "required_gates": [
                {
                    "id": "operational-real-data",
                    "path": "docs/operational-readiness.yml",
                    "field": "verdict",
                    "equals": "APPROVED",
                }
            ]
        },
    }
    contract_path = root / ".ft" / "project.yml"
    contract_path.parent.mkdir()
    contract_path.write_text(
        yaml.safe_dump(contract, sort_keys=False), encoding="utf-8"
    )
    return report_path, contract_path


def test_accepts_complete_operational_real_data_journey(tmp_path: Path) -> None:
    report, contract = _fixture(tmp_path)
    VALIDATOR.validate(report, contract, root=tmp_path)


@pytest.mark.parametrize(
    ("css", "source", "message"),
    [
        (
            "body { font-size: 12px; }\n",
            "def _default_seed(): return {'demo': True}\n",
            "default synthetic seed",
        ),
        (
            "body { font-size: 9px; }\n",
            "print('production')\n",
            "readable font below 12px",
        ),
        (
            "code { font: 700 9px/1.4 monospace; }\n",
            "print('production')\n",
            "readable font below 12px",
        ),
        (
            "body { font-size: 12px; }\n",
            "const style = { fontSize: 9 };\n",
            "readable font below 12px",
        ),
    ],
)
def test_rejects_demo_runtime_and_illegible_production_text(
    tmp_path: Path,
    css: str,
    source: str,
    message: str,
) -> None:
    report, contract = _fixture(tmp_path, css=css, source=source)
    with pytest.raises(VALIDATOR.ValidationError, match=message):
        VALIDATOR.validate(report, contract, root=tmp_path)
