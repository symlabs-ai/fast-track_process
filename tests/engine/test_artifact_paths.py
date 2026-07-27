from ft.engine.artifact_paths import TECH_STACK_PATH, resolve_tech_stack_path
from ft.templates.catalog import TemplateCatalog


def test_tech_stack_uses_uppercase_canonical_path_when_missing(tmp_path):
    assert resolve_tech_stack_path(tmp_path) == tmp_path / TECH_STACK_PATH


def test_tech_stack_reads_legacy_lowercase_path(tmp_path):
    legacy = tmp_path / "docs/tech_stack.md"
    legacy.parent.mkdir()
    legacy.write_text("interface_type: api\n", encoding="utf-8")

    assert resolve_tech_stack_path(tmp_path) == legacy


def test_tech_stack_prefers_canonical_path_when_both_exist(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    canonical = docs / "TECH_STACK.md"
    canonical.write_text("interface_type: ui\n", encoding="utf-8")
    (docs / "tech_stack.md").write_text("interface_type: api\n", encoding="utf-8")

    assert resolve_tech_stack_path(tmp_path) == canonical


def test_distributed_processes_only_declare_canonical_tech_stack_path():
    catalog = TemplateCatalog()

    for name in catalog.names():
        process_text = catalog.get(name).process_file.read_text(encoding="utf-8")
        assert "docs/tech_stack.md" not in process_text, name
