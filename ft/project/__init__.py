"""Repository bootstrap, health checking, repair, and manifest migration."""

from ft.project.bootstrap import (
    BootstrapError,
    BootstrapResult,
    bootstrap_project,
)
from ft.project.migration import MigrationResult, migrate_v2_manifest
from ft.project.repair import (
    ProjectCheckResult,
    ProjectIssue,
    ProjectRepairResult,
    check_project,
    repair_project,
)

__all__ = (
    "BootstrapError",
    "BootstrapResult",
    "MigrationResult",
    "ProjectCheckResult",
    "ProjectIssue",
    "ProjectRepairResult",
    "bootstrap_project",
    "check_project",
    "migrate_v2_manifest",
    "repair_project",
)
