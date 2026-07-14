"""Independent repository-local V3 layout contracts.

Bootstrap, template materialization and cycle allocation live in their focused
V3 suites.  These tests retain root discovery and tracked-ignore behavior.
"""

from __future__ import annotations


class TestFindProjectRoot:
    def test_detects_manifest_root(self, tmp_path, monkeypatch):
        from ft.cli.main import find_project_root
        from ft.engine.layout import ensure_project_layout

        ensure_project_layout(tmp_path)
        monkeypatch.chdir(tmp_path)

        assert find_project_root() == tmp_path

    def test_walks_up_to_manifest_root(self, tmp_path, monkeypatch):
        from ft.cli.main import find_project_root
        from ft.engine.layout import ensure_project_layout

        ensure_project_layout(tmp_path)
        nested = tmp_path / "sub" / "deep"
        nested.mkdir(parents=True)
        monkeypatch.chdir(nested)

        assert find_project_root() == tmp_path

    def test_returns_cwd_when_no_workspace_exists(self, tmp_path, monkeypatch):
        from ft.cli.main import find_project_root

        monkeypatch.chdir(tmp_path)

        assert find_project_root() == tmp_path

    def test_runtime_like_directory_does_not_identify_workspace(self, tmp_path, monkeypatch):
        from ft.cli.main import find_project_root

        nested = tmp_path / "product" / "state"
        nested.mkdir(parents=True)
        monkeypatch.chdir(nested)

        assert find_project_root() == nested


class TestProjectFtGitignore:
    def test_ignores_runtime_but_tracks_cycle_history(self, tmp_path):
        from ft.engine.layout import ensure_project_layout

        ensure_project_layout(tmp_path)
        content = (tmp_path / ".ft" / ".gitignore").read_text(encoding="utf-8")

        assert "/runtime/" in content
        assert "/cycles/" not in content

    def test_layout_creation_is_idempotent(self, tmp_path):
        from ft.engine.layout import ensure_project_layout

        ensure_project_layout(tmp_path)
        ensure_project_layout(tmp_path)
        content = (tmp_path / ".ft" / ".gitignore").read_text(encoding="utf-8")

        assert content.count("/runtime/") == 1
