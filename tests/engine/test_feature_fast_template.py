"""Contract tests for the opt-in feature-fast process."""

from __future__ import annotations

from pathlib import Path

import yaml

from ft.engine.graph import load_graph
from ft.engine.process_validator import validate_process
from ft.templates.catalog import TemplateCatalog


ROOT = Path(__file__).resolve().parents[2]
BASE_PROCESS = ROOT / "templates" / "feature" / "process.yml"
FAST_PROCESS = ROOT / "templates" / "feature-fast" / "process.yml"


def _payload(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _node_contract(node: dict) -> dict:
    fields = (
        "id",
        "type",
        "executor",
        "sprint",
        "next",
        "reject_next",
        "branches",
        "on_fail",
        "validators",
        "outputs",
        "review_route_path",
        "llm_episode",
    )
    return {
        field: _normalize_runtime_paths(node.get(field))
        for field in fields
    }


def _normalize_runtime_paths(value):
    if isinstance(value, str):
        return value.replace(
            ".ft/process/feature-fast/",
            ".ft/process/feature/",
        )
    if isinstance(value, list):
        return [_normalize_runtime_paths(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _normalize_runtime_paths(item)
            for key, item in value.items()
        }
    return value


def test_catalog_exposes_feature_fast() -> None:
    descriptor = TemplateCatalog(ROOT / "templates").get("feature-fast")

    assert descriptor.process_file == FAST_PROCESS
    assert descriptor.policy["template"] == "feature-fast"
    assert descriptor.policy["local_process_path"] == (
        ".ft/process/feature-fast/process.yml"
    )


def test_feature_fast_graph_and_session_policy_are_valid() -> None:
    graph = load_graph(FAST_PROCESS)
    report = validate_process(graph)

    assert report.passed, [issue.message for issue in report.errors]
    assert graph.meta["id"] == "feature_fast"
    assert graph.meta["session_policy"] == {
        "mode": "sprint",
        "providers": ["claude", "codex"],
        "initial_plan": "internal",
        "parallel_strategy": "fork",
        "recovery": "rehydrate",
    }


def test_feature_fast_preserves_feature_safety_contract() -> None:
    base = _payload(BASE_PROCESS)
    fast = _payload(FAST_PROCESS)

    assert fast["correction_policy"] == base["correction_policy"]
    assert fast["close_policy"] == base["close_policy"]
    assert fast["artifact_policy"] == base["artifact_policy"]
    assert [_node_contract(node) for node in fast["nodes"]] == [
        _node_contract(node) for node in base["nodes"]
    ]
    base_human_gates = sum(
        node["type"] == "human_gate" for node in base["nodes"]
    )
    assert base_human_gates == 3
    assert sum(
        node["type"] == "human_gate" for node in fast["nodes"]
    ) == base_human_gates


def test_feature_fast_session_boundaries_match_process_roles() -> None:
    graph = load_graph(FAST_PROCESS)

    assert graph.get_node("feature.discovery").sprint == "feature-01-scope"
    assert graph.get_node("feature.implement").sprint == "feature-02-build"
    assert graph.get_node("feature.evidence").sprint == "feature-02-build"
    review = graph.get_node("feature.review")
    assert review.sprint == "feature-02-build"
    assert review.type == "review"
    assert review.llm_timeout_seconds == 1200
    assert graph.get_node("feature.reconcile").sprint == "feature-03-acceptance"


def test_feature_fast_runtime_references_are_self_contained() -> None:
    process_text = FAST_PROCESS.read_text(encoding="utf-8")
    product_helper = (
        ROOT / "templates" / "feature-fast" / "scripts" / "product.sh"
    ).read_text(encoding="utf-8")
    receipt_helper = (
        ROOT / "templates" / "feature-fast" / "scripts" / "product_receipt.py"
    ).read_text(encoding="utf-8")
    serve_helper = (
        ROOT / "templates" / "feature-fast" / "scripts" / "serve.sh"
    ).read_text(encoding="utf-8")

    combined = "\n".join(
        (process_text, product_helper, receipt_helper, serve_helper)
    )
    assert ".ft/process/feature-fast/" in combined
    assert ".ft/process/feature/" not in combined
