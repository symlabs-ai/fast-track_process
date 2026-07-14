from __future__ import annotations

from pathlib import Path
import subprocess

from ft.engine.canonical_merge import (
    _merge_changelog,
    resolve_canonical_conflicts,
)


CHANGELOG = """# Changelog

## Unreleased

- #BUG PB-001 / FEAT-001: correção original.
"""

BACKLOG = """# PROJECT_BACKLOG

## Itens do Backlog

| ID | Tipo | Prioridade | Status | Origem | Título | Critérios de Aceite | Evidência | Decisão/Notas |
|---|---|---|---|---|---|---|---|---|
| PB-001 | Feature | P1 | accepted | produto | Terminal | Executar comandos | tests/base | Entregue |
| PB-101 | Bug | P1 | in_progress | suporte | Bug A | Não falhar A | — | Em correção |
| PB-102 | Bug | P1 | in_progress | suporte | Bug B | Não falhar B | — | Em correção |
"""

FEATURES = """# FEATURES

## Catálogo de Features

| ID | Status | Backlog | Título | Descrição | Entregue em | Evidência | Última evolução | Notas |
|---|---|---|---|---|---|---|---|---|
| FEAT-001 | active | PB-001 | Terminal | Executa comandos. | cycle-01 | tests/base | cycle-01 | Entrega inicial |
"""


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode != 0:
        raise AssertionError(result.stderr.decode("utf-8", errors="replace"))
    return result


def _write(root: Path, relative: str, content: str) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _init_repo(tmp_path: Path, *, features: str = FEATURES) -> tuple[Path, str]:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "user.email", "test@example.com")
    _write(root, "CHANGELOG.md", CHANGELOG)
    _write(root, "docs/PROJECT_BACKLOG.md", BACKLOG)
    _write(root, "docs/FEATURES.md", features)
    _write(root, "src/app.py", "VALUE = 'base'\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    branch = _git(root, "branch", "--show-current").stdout.decode().strip()
    return root, branch


def _commit(root: Path, message: str) -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", message)


def _begin_conflicting_merge(
    root: Path,
    main_branch: str,
    *,
    ours: callable,
    theirs: callable,
) -> None:
    _git(root, "switch", "-qc", "worker")
    theirs(root)
    _commit(root, "worker")
    _git(root, "switch", "-q", main_branch)
    ours(root)
    _commit(root, "ours")
    result = _git(root, "merge", "--no-commit", "worker", check=False)
    assert result.returncode == 1, result.stderr.decode()
    assert _git(root, "rev-parse", "-q", "--verify", "MERGE_HEAD").returncode == 0


def _bug_a_changes(root: Path) -> None:
    _write(
        root,
        "CHANGELOG.md",
        CHANGELOG + "- #BUG PB-101 / FEAT-001: corrige bug A.\n",
    )
    # Both physical rows are changed to force a textual conflict.  PB-102 only
    # changes Markdown spacing, so semantically this worker owns PB-101 alone.
    _write(
        root,
        "docs/PROJECT_BACKLOG.md",
        BACKLOG.replace(
            "| PB-101 | Bug | P1 | in_progress | suporte | Bug A | Não falhar A | — | Em correção |",
            "| PB-101 | Bug | P1 | accepted | suporte | Bug A | Não falhar A | tests/bug-a | Corrigido A |",
        ).replace(
            "| PB-102 | Bug | P1 | in_progress | suporte | Bug B | Não falhar B | — | Em correção |",
            "|PB-102|Bug|P1|in_progress|suporte|Bug B|Não falhar B|—|Em correção|",
        ),
    )
    _write(
        root,
        "docs/FEATURES.md",
        FEATURES.replace(
            "| FEAT-001 | active | PB-001 | Terminal | Executa comandos. | cycle-01 | tests/base | cycle-01 | Entrega inicial |",
            "| FEAT-001 | active | PB-001, PB-101 | Terminal | Executa comandos. | cycle-01 | tests/base; tests/bug-a | cycle-01; cycle-bug-a | Entrega inicial; Bug A corrigido |",
        ),
    )


def _bug_b_changes(root: Path) -> None:
    _write(
        root,
        "CHANGELOG.md",
        CHANGELOG + "- #BUG PB-102 / FEAT-001: corrige bug B.\n",
    )
    _write(
        root,
        "docs/PROJECT_BACKLOG.md",
        BACKLOG.replace(
            "| PB-101 | Bug | P1 | in_progress | suporte | Bug A | Não falhar A | — | Em correção |",
            "|PB-101|Bug|P1|in_progress|suporte|Bug A|Não falhar A|—|Em correção|",
        ).replace(
            "| PB-102 | Bug | P1 | in_progress | suporte | Bug B | Não falhar B | — | Em correção |",
            "| PB-102 | Bug | P1 | accepted | suporte | Bug B | Não falhar B | tests/bug-b | Corrigido B |",
        ),
    )
    _write(
        root,
        "docs/FEATURES.md",
        FEATURES.replace(
            "| FEAT-001 | active | PB-001 | Terminal | Executa comandos. | cycle-01 | tests/base | cycle-01 | Entrega inicial |",
            "| FEAT-001 | active | PB-001, PB-102 | Terminal | Executa comandos. | cycle-01 | tests/base; tests/bug-b | cycle-01; cycle-bug-b | Entrega inicial; Bug B corrigido |",
        ),
    )


def test_resolves_two_parallel_bugs_with_distinct_pbs_on_same_feature(tmp_path):
    root, main_branch = _init_repo(tmp_path)
    _begin_conflicting_merge(
        root,
        main_branch,
        ours=_bug_a_changes,
        theirs=_bug_b_changes,
    )
    assert set(
        _git(root, "diff", "--name-only", "--diff-filter=U").stdout.decode().splitlines()
    ) == {
        "CHANGELOG.md",
        "docs/FEATURES.md",
        "docs/PROJECT_BACKLOG.md",
    }

    result = resolve_canonical_conflicts(root)

    assert result.success, result.error
    assert result.resolved == (
        "CHANGELOG.md",
        "docs/FEATURES.md",
        "docs/PROJECT_BACKLOG.md",
    )
    assert not _git(root, "diff", "--name-only", "--diff-filter=U").stdout

    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    assert changelog.count("#BUG PB-101") == 1
    assert changelog.count("#BUG PB-102") == 1

    backlog = (root / "docs/PROJECT_BACKLOG.md").read_text(encoding="utf-8")
    assert "| PB-101 | Bug | P1 | accepted" in backlog
    assert "tests/bug-a | Corrigido A" in backlog
    assert "| PB-102 | Bug | P1 | accepted" in backlog
    assert "tests/bug-b | Corrigido B" in backlog

    features = (root / "docs/FEATURES.md").read_text(encoding="utf-8")
    assert features.count("| FEAT-001 ") == 1
    assert "PB-001, PB-101, PB-102" in features
    assert "tests/base; tests/bug-a; tests/bug-b" in features
    assert "cycle-01; cycle-bug-a; cycle-bug-b" in features
    assert "Entrega inicial; Bug A corrigido; Bug B corrigido" in features


def test_refuses_noncanonical_conflict_without_touching_index_or_worktree(tmp_path):
    root, main_branch = _init_repo(tmp_path)

    def ours(repo: Path) -> None:
        _write(repo, "CHANGELOG.md", CHANGELOG + "- ours\n")
        _write(repo, "src/app.py", "VALUE = 'ours'\n")

    def theirs(repo: Path) -> None:
        _write(repo, "CHANGELOG.md", CHANGELOG + "- theirs\n")
        _write(repo, "src/app.py", "VALUE = 'theirs'\n")

    _begin_conflicting_merge(root, main_branch, ours=ours, theirs=theirs)
    index_before = _git(root, "ls-files", "-u", "-z").stdout
    changelog_before = (root / "CHANGELOG.md").read_bytes()
    app_before = (root / "src/app.py").read_bytes()

    result = resolve_canonical_conflicts(root)

    assert not result.success
    assert "src/app.py" in (result.error or "")
    assert _git(root, "ls-files", "-u", "-z").stdout == index_before
    assert (root / "CHANGELOG.md").read_bytes() == changelog_before
    assert (root / "src/app.py").read_bytes() == app_before


def test_parse_failure_does_not_apply_an_earlier_mergeable_document(tmp_path):
    malformed = "# FEATURES\n\nFEAT-001 base\n"
    root, main_branch = _init_repo(tmp_path, features=malformed)

    def ours(repo: Path) -> None:
        _write(repo, "CHANGELOG.md", CHANGELOG + "- ours\n")
        _write(repo, "docs/FEATURES.md", "# FEATURES\n\nFEAT-001 ours\n")

    def theirs(repo: Path) -> None:
        _write(repo, "CHANGELOG.md", CHANGELOG + "- theirs\n")
        _write(repo, "docs/FEATURES.md", "# FEATURES\n\nFEAT-001 theirs\n")

    _begin_conflicting_merge(root, main_branch, ours=ours, theirs=theirs)
    index_before = _git(root, "ls-files", "-u", "-z").stdout
    files_before = {
        path: (root / path).read_bytes()
        for path in ("CHANGELOG.md", "docs/FEATURES.md")
    }

    result = resolve_canonical_conflicts(root)

    assert not result.success
    assert "exatamente um catálogo Markdown" in (result.error or "")
    assert result.resolved == ()
    assert _git(root, "ls-files", "-u", "-z").stdout == index_before
    assert {
        path: (root / path).read_bytes()
        for path in ("CHANGELOG.md", "docs/FEATURES.md")
    } == files_before


def test_requires_all_three_index_stages_for_modify_delete_conflict(tmp_path):
    root, main_branch = _init_repo(tmp_path)

    def ours(repo: Path) -> None:
        (repo / "CHANGELOG.md").unlink()

    def theirs(repo: Path) -> None:
        _write(repo, "CHANGELOG.md", CHANGELOG + "- worker\n")

    _begin_conflicting_merge(root, main_branch, ours=ours, theirs=theirs)
    index_before = _git(root, "ls-files", "-u", "-z").stdout

    result = resolve_canonical_conflicts(root)

    assert not result.success
    assert "stages :1/:2/:3" in (result.error or "")
    assert _git(root, "ls-files", "-u", "-z").stdout == index_before


def test_changelog_imports_only_unique_additive_lines():
    base = "# Changelog\n\n## Histórico\n\n- antiga\n"
    ours = "# Changelog\n\n## Histórico\n\n- #BUG PB-101\n- antiga\n"
    theirs = (
        "# Changelog\n\n## Histórico\n\n"
        "- #BUG PB-101\n- #BUG PB-102\n- antiga\n"
    )

    merged = _merge_changelog(base, ours, theirs)

    assert merged.count("#BUG PB-101") == 1
    assert merged.count("#BUG PB-102") == 1
    assert merged.endswith("- antiga\n")


def test_no_merge_in_progress_returns_failure(tmp_path):
    root, _main_branch = _init_repo(tmp_path)

    result = resolve_canonical_conflicts(root)

    assert not result.success
    assert "não há merge Git em andamento" in (result.error or "")
    assert result.resolved == ()
