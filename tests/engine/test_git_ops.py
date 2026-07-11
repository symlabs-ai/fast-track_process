import subprocess
from pathlib import Path

from ft.engine.git_ops import auto_commit


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def test_auto_commit_excludes_runtime_state(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")

    (repo / "docs").mkdir()
    (repo / "src").mkdir()
    (repo / "docs" / "report.md").write_text("ok\n", encoding="utf-8")
    (repo / ".serve_url").write_text("http://127.0.0.1:8787\n", encoding="utf-8")
    (repo / "src" / ".serve.log").write_text("runtime log\n", encoding="utf-8")
    (repo / "src" / ".serve.pid").write_text("123\n", encoding="utf-8")
    (repo / "cycle-01_log.md").write_text("cycle log\n", encoding="utf-8")
    (repo / ".ft" / "runtime").mkdir(parents=True)
    (repo / ".ft" / "runtime" / "engine_state.yml").write_text("runtime\n")
    (repo / ".ft" / "cycles" / "cycle-01").mkdir(parents=True)
    (repo / ".ft" / "cycles" / "cycle-01" / "cycle.yml").write_text("id: cycle-01\n")

    ok, detail = auto_commit("test commit", str(repo))

    assert ok, detail
    tracked = _git(repo, "ls-tree", "-r", "--name-only", "HEAD").stdout.splitlines()
    assert tracked == [".ft/cycles/cycle-01/cycle.yml", "docs/report.md"]
