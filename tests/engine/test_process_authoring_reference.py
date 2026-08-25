"""Keep the generic process.yml reference aligned with the loader."""

import ast
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
GRAPH_SOURCE = ROOT / "ft" / "engine" / "graph.py"
AUTHORING_GUIDE = ROOT / "docs" / "ft_process_authoring.md"


def _yaml_node_keys() -> set[str]:
    """Return only keys read from YAML, excluding derived Node attributes."""
    module = ast.parse(GRAPH_SOURCE.read_text(encoding="utf-8"))
    keys: set[str] = {"id"}

    for call in ast.walk(module):
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
            continue
        if call.func.attr != "get" or not isinstance(call.func.value, ast.Name):
            continue
        if call.func.value.id != "node_raw" or not call.args:
            continue
        key = call.args[0]
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            keys.add(key.value)
    return keys


def _node_reference() -> str:
    guide = AUTHORING_GUIDE.read_text(encoding="utf-8")
    start = "<!-- process-yml-node-reference:start -->"
    end = "<!-- process-yml-node-reference:end -->"
    return guide.split(start, 1)[1].split(end, 1)[0]


def test_process_authoring_reference_covers_loader_tags() -> None:
    reference = _node_reference()

    missing = sorted(key for key in _yaml_node_keys() if f"`{key}`" not in reference)

    assert not missing, f"tags YAML do Node ausentes do guia: {missing}"


def test_process_authoring_reference_covers_template_header_tags() -> None:
    guide = AUTHORING_GUIDE.read_text(encoding="utf-8")
    root_keys: set[str] = set()

    for process in (ROOT / "templates").glob("*/process.yml"):
        payload = yaml.safe_load(process.read_text(encoding="utf-8"))
        assert isinstance(payload, dict)
        root_keys.update(payload)

    missing = sorted(key for key in root_keys if f"`{key}`" not in guide)

    assert not missing, f"tags de cabeçalho ausentes do guia: {missing}"
