from __future__ import annotations

from pathlib import Path

import yaml

from ft.engine.runner import VALIDATOR_REGISTRY


def test_mvp_builder_fast_maintains_features_before_planning_and_after_delivery():
    root = Path(__file__).resolve().parents[2]
    data = yaml.safe_load(
        (root / "templates/mvp-builder-fast/process.yml").read_text(encoding="utf-8")
    )
    by_id = {node["id"]: node for node in data["nodes"]}

    assert "docs/FEATURES.md" in data["artifact_policy"]["canonical"]
    assert (
        by_id["ft.start.backlog.route"]["branches"]["true"] == "ft.start.features.route"
    )
    assert by_id["ft.start.features.route"]["branches"] == {
        "true": "ft.plan.00.foundation_existing",
        "false": "ft.plan.00.foundation_features",
    }

    create = by_id["ft.plan.00.foundation_features"]
    update = by_id["ft.handoff.02b.features_update"]
    assert by_id["ft.handoff.02.backlog_update"]["next"] == update["id"]
    assert update["next"] == "ft.handoff.02.prd_rewrite"
    for node in (create, update):
        assert "docs/FEATURES.md" in node["outputs"]
        validator_names = {name for spec in node["validators"] for name in spec}
        assert {
            "features_catalog_valid",
            "implemented_backlog_covered_by_features",
        } <= validator_names

    assert "features_catalog_valid" in VALIDATOR_REGISTRY
    assert "implemented_backlog_covered_by_features" in VALIDATOR_REGISTRY
