"""Repository bootstrap, health checking, repair, and manifest migration."""

from ft.project.bootstrap import (
    BootstrapError,
    BootstrapResult,
    bootstrap_project,
)
from ft.project.lifecycle import (
    ProjectContractError,
    ProjectLifecycleError,
    ProjectReadiness,
    assert_template_allowed,
    close_project_contract,
    default_project_contract,
    ensure_project_contract,
    evaluate_project_readiness,
    project_run_context,
    read_project_contract,
    reopen_project_contract,
    validate_project_contract,
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
    "ProjectContractError",
    "ProjectIssue",
    "ProjectLifecycleError",
    "ProjectReadiness",
    "ProjectRepairResult",
    "assert_template_allowed",
    "bootstrap_project",
    "check_project",
    "close_project_contract",
    "default_project_contract",
    "ensure_project_contract",
    "evaluate_project_readiness",
    "migrate_v2_manifest",
    "project_run_context",
    "read_project_contract",
    "reopen_project_contract",
    "repair_project",
    "validate_project_contract",
)
