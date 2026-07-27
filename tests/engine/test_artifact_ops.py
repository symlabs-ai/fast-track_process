"""Containment contracts for process-declared artifact cleanup."""

from pathlib import Path

from ft.engine.artifact_ops import remove_declared_outputs


def test_remove_declared_outputs_removes_only_requested_files_and_directories(
    tmp_path: Path,
) -> None:
    root = tmp_path / "worktree"
    (root / "docs" / "generated").mkdir(parents=True)
    (root / "docs" / "generated" / "a.md").write_text("a", encoding="utf-8")
    (root / "keep.md").write_text("keep", encoding="utf-8")

    remove_declared_outputs(root, ["docs/generated/"])

    assert not (root / "docs" / "generated").exists()
    assert (root / "keep.md").read_text(encoding="utf-8") == "keep"


def test_remove_declared_outputs_refuses_parent_escape_and_external_symlink(
    tmp_path: Path,
) -> None:
    root = tmp_path / "worktree"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("preserve", encoding="utf-8")
    link = root / "outside-link"
    link.symlink_to(outside)

    remove_declared_outputs(root, ["../outside.txt", "outside-link"])

    assert outside.read_text(encoding="utf-8") == "preserve"
    assert link.is_symlink()
