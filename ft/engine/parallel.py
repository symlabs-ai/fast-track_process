"""
Execucao paralela via git worktrees.
Fan-out: cria worktree por task independente.
Fan-in: aguarda, merge, resolve conflitos.
"""

from __future__ import annotations

import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class WorktreeResult:
    node_id: str
    branch: str
    worktree_path: str
    success: bool
    output: str
    files_created: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)


def create_worktree(
    node_id: str,
    project_root: str,
    base_branch: str = "main",
) -> tuple[str, str]:
    """Cria um git worktree isolado para a task. Retorna (branch, worktree_path)."""
    branch = f"ft-parallel/{node_id.replace('.', '-')}"
    worktree_path = str(Path(project_root).parent / f".ft-worktree-{node_id.replace('.', '-')}")

    # Criar branch a partir da base
    subprocess.run(
        ["git", "branch", branch, base_branch],
        cwd=project_root, capture_output=True,
    )

    # Criar worktree
    result = subprocess.run(
        ["git", "worktree", "add", worktree_path, branch],
        cwd=project_root, capture_output=True, text=True,
    )

    if result.returncode != 0:
        # Limpar branch se worktree falhou
        subprocess.run(["git", "branch", "-D", branch], cwd=project_root, capture_output=True)
        raise RuntimeError(f"Falha ao criar worktree: {result.stderr}")

    return branch, worktree_path


def remove_worktree(
    worktree_path: str,
    branch: str,
    project_root: str,
):
    """Remove worktree e branch temporaria."""
    subprocess.run(
        ["git", "worktree", "remove", "--force", worktree_path],
        cwd=project_root, capture_output=True,
    )
    subprocess.run(
        ["git", "branch", "-D", branch],
        cwd=project_root, capture_output=True,
    )


def merge_branch(
    branch: str,
    project_root: str,
    squash: bool = False,
) -> tuple[bool, str]:
    """Merge de uma branch paralela na branch atual."""
    cmd = ["git", "merge"]
    if squash:
        cmd.append("--squash")
    cmd.extend(["--no-ff", "-m", f"merge: ft-parallel branch {branch}", branch])

    result = subprocess.run(
        cmd, cwd=project_root, capture_output=True, text=True,
    )

    if result.returncode == 0:
        return True, f"merge OK: {branch}"

    # Conflito — tentar resolver automaticamente
    conflict_result = subprocess.run(
        ["git", "merge", "--abort"],
        cwd=project_root, capture_output=True,
    )
    return False, f"merge CONFLICT: {result.stderr.strip()[:200]}"


def check_independence(
    node_a_outputs: list[str],
    node_b_outputs: list[str],
) -> bool:
    """Verifica se dois nodes podem paralelizar (outputs disjuntos)."""
    set_a = set(node_a_outputs)
    set_b = set(node_b_outputs)
    return len(set_a & set_b) == 0


class ParallelRunner:
    """
    Executa nodes independentes em paralelo via worktrees.

    Uso:
      runner = ParallelRunner(project_root, max_slots=2)
      results = runner.run_parallel([node_a, node_b], delegate_fn)
    """

    def __init__(self, project_root: str, max_slots: int = 2):
        self.project_root = project_root
        self.max_slots = max_slots
        self._semaphore = threading.Semaphore(max_slots)
        self._results: list[WorktreeResult] = []
        self._lock = threading.Lock()

    def run_parallel(
        self,
        tasks: list[dict[str, Any]],
        delegate_fn,
    ) -> list[WorktreeResult]:
        """
        Executa tasks em paralelo, cada uma em worktree isolado.

        tasks: lista de dicts com {node_id, task_prompt, allowed_paths}
        delegate_fn: funcao(task, project_root, allowed_paths) → DelegateResult
        """
        # Verificar independencia entre todas as tasks
        for i, t1 in enumerate(tasks):
            for t2 in tasks[i + 1:]:
                if not check_independence(
                    t1.get("outputs", []),
                    t2.get("outputs", []),
                ):
                    raise ValueError(
                        f"Tasks nao sao independentes: "
                        f"{t1['node_id']} e {t2['node_id']} "
                        f"compartilham outputs"
                    )

        # Obter branch atual
        base = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=self.project_root, capture_output=True, text=True,
        ).stdout.strip()

        threads = []
        for task in tasks:
            t = threading.Thread(
                target=self._run_one,
                args=(task, delegate_fn, base),
            )
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        return list(self._results)

    def _run_one(
        self,
        task: dict[str, Any],
        delegate_fn,
        base_branch: str,
    ):
        """Roda uma task em worktree isolado."""
        node_id = task["node_id"]
        branch, worktree_path = None, None

        with self._semaphore:
            try:
                branch, worktree_path = create_worktree(
                    node_id, self.project_root, base_branch
                )

                result = delegate_fn(
                    task=task["task_prompt"],
                    project_root=worktree_path,
                    allowed_paths=task.get("allowed_paths"),
                )

                # Commit no worktree
                subprocess.run(
                    ["git", "add", "-A"],
                    cwd=worktree_path, capture_output=True,
                )
                subprocess.run(
                    ["git", "commit", "-m", f"ft-parallel: {node_id}"],
                    cwd=worktree_path, capture_output=True,
                )

                wt_result = WorktreeResult(
                    node_id=node_id,
                    branch=branch,
                    worktree_path=worktree_path,
                    success=result.success,
                    output=result.output,
                    files_created=result.files_created,
                    files_modified=result.files_modified,
                )

            except Exception as e:
                wt_result = WorktreeResult(
                    node_id=node_id,
                    branch=branch or "",
                    worktree_path=worktree_path or "",
                    success=False,
                    output=str(e),
                )

        with self._lock:
            self._results.append(wt_result)

    def merge_all(self, results: list[WorktreeResult]) -> list[tuple[str, bool, str]]:
        """Merge todas as branches paralelas na branch atual."""
        merge_results = []
        for r in results:
            if r.success and r.branch:
                ok, detail = merge_branch(r.branch, self.project_root)
                merge_results.append((r.node_id, ok, detail))
                # Limpar worktree
                if r.worktree_path:
                    remove_worktree(r.worktree_path, r.branch, self.project_root)
        return merge_results
