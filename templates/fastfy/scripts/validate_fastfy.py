#!/usr/bin/env python3
"""Deterministic validators for the local ``fastfy`` adoption process.

The script validates artifacts only. Build/test execution remains owned by
``process.yml`` (via ``product.sh``) so command output stays visible in the
engine gate log.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import sys

import yaml


SURVEY_PATH = "docs/adoption-survey.md"
QUESTIONS_PATH = "docs/adoption-questions.md"
PLAN_PATH = "docs/adoption-plan.md"
REVIEW_PATH = "docs/adoption-review.md"

PRODUCT_ROOTS = {".", "project", "src"}
INTERFACES = {"ui", "api", "internal", "mixed"}
CLARIFICATION_RE = re.compile(
    r"^\s*clarification_status\s*:\s*(required|clear)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
PB_RE = re.compile(r"\bPB-\d+[A-Z]?\b", re.IGNORECASE)
FEAT_CHANGELOG_RE = re.compile(r"(?m)^[ \t]*(?:[-*+][ \t]+)?#FEAT(?=[ \t]|$)")
TEST_FILE_RE = re.compile(r"(^|[._/-])(test|tests|spec|specs)([._/-]|$)", re.IGNORECASE)
TEST_FILE_SUFFIXES = {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".go", ".rs", ".rb", ".sh", ".java", ".php", ".exs"}
SKIPPED_TREES = {".git", ".ft", "docs", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".claude"}


class FastfyValidationError(ValueError):
    """A user-facing adoption artifact violation."""


def _read(root: Path, relative: str) -> str:
    path = root / relative
    if not path.is_file():
        raise FastfyValidationError(f"arquivo obrigatório ausente: {relative}")
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        raise FastfyValidationError(f"arquivo obrigatório vazio: {relative}")
    return text


def _frontmatter(text: str, path: str) -> dict[str, object]:
    if not text.lstrip().startswith("---"):
        raise FastfyValidationError(f"{path}: frontmatter YAML ausente")
    parts = text.lstrip().split("---", 2)
    if len(parts) < 3:
        raise FastfyValidationError(f"{path}: frontmatter YAML não foi fechado")
    try:
        data = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError as exc:
        raise FastfyValidationError(f"{path}: frontmatter YAML inválido: {exc}") from exc
    if not isinstance(data, dict):
        raise FastfyValidationError(f"{path}: frontmatter deve ser um mapping")
    return data


def _survey_contract(root: Path) -> dict[str, object]:
    text = _read(root, SURVEY_PATH)
    metadata = _frontmatter(text, SURVEY_PATH)

    stack = str(metadata.get("stack") or "").strip()
    if not stack:
        raise FastfyValidationError(f"{SURVEY_PATH}: campo stack ausente ou vazio")

    product_root = str(metadata.get("product_root") or "").strip()
    if product_root not in PRODUCT_ROOTS:
        raise FastfyValidationError(
            f"{SURVEY_PATH}: product_root deve ser um de {sorted(PRODUCT_ROOTS)}; "
            f"recebido {product_root!r}"
        )

    interface = str(metadata.get("interface") or "").strip().lower()
    if interface not in INTERFACES:
        raise FastfyValidationError(
            f"{SURVEY_PATH}: interface deve ser um de {sorted(INTERFACES)}; "
            f"recebido {interface!r}"
        )

    has_tests = metadata.get("has_tests")
    if not isinstance(has_tests, bool):
        raise FastfyValidationError(f"{SURVEY_PATH}: has_tests deve ser true ou false")

    for field in ("build_command", "test_command"):
        if not str(metadata.get(field) or "").strip():
            raise FastfyValidationError(f"{SURVEY_PATH}: campo {field} ausente ou vazio")

    run_command = str(metadata.get("run_command") or "").strip()
    if interface != "internal" and not run_command:
        raise FastfyValidationError(
            f"{SURVEY_PATH}: run_command é obrigatório para interface {interface}"
        )

    match = CLARIFICATION_RE.search(text)
    if not match:
        raise FastfyValidationError(
            f"{SURVEY_PATH} sem clarification_status: required|clear"
        )

    return {
        "product_root": product_root,
        "interface": interface,
        "has_tests": has_tests,
        "clarification_status": match.group(1).lower(),
    }


def _makefile_targets(root: Path, product_root: str) -> tuple[str, str]:
    relative = "Makefile" if product_root == "." else f"{product_root}/Makefile"
    return relative, _read(root, relative)


def _has_target(makefile: str, target: str) -> bool:
    return re.search(rf"(?m)^{re.escape(target)}\s*:", makefile) is not None


def _repo_has_test_files(root: Path) -> bool:
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part in SKIPPED_TREES for part in relative.parts):
            continue
        if path.suffix.lower() not in TEST_FILE_SUFFIXES:
            continue
        if TEST_FILE_RE.search(relative.as_posix()):
            return True
    return False


def validate_preflight(root: Path) -> None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FastfyValidationError(f"não foi possível consultar o git: {exc}") from exc
    if result.returncode != 0:
        raise FastfyValidationError(
            "repositório sem HEAD utilizável; a adoção exige histórico Git "
            "(rode ft init e commite o legado antes)"
        )
    if (root / "docs/PRD.md").is_file() and (root / "docs/FEATURES.md").is_file():
        raise FastfyValidationError(
            "projeto já possui docs/PRD.md e docs/FEATURES.md; use "
            "`ft run . --template feature` para evoluir um projeto já adotado"
        )


def validate_survey(root: Path) -> None:
    contract = _survey_contract(root)
    questions = _read(root, QUESTIONS_PATH)
    _read(root, PLAN_PATH)
    if contract["clarification_status"] == "required":
        if "?" not in questions:
            raise FastfyValidationError(
                "clarification_status=required exige perguntas em "
                f"{QUESTIONS_PATH}"
            )
        return
    plan = _read(root, PLAN_PATH)
    if "Makefile" not in plan:
        raise FastfyValidationError(
            f"{PLAN_PATH} deve descrever o harness (Makefile com build/test/run)"
        )
    if contract["has_tests"] is False and not re.search(r"smoke", plan, re.IGNORECASE):
        raise FastfyValidationError(
            f"{PLAN_PATH}: repositório sem testes exige o smoke test mínimo no plano"
        )


def validate_docs(root: Path) -> None:
    contract = _survey_contract(root)
    prd = _read(root, "docs/PRD.md")
    if "##" not in prd:
        raise FastfyValidationError("docs/PRD.md sem seções (## ...)")
    _read(root, "docs/TECH_STACK.md")
    _read(root, "docs/PROJECT_BACKLOG.md")
    _read(root, "docs/FEATURES.md")

    changelog = _read(root, "CHANGELOG.md")
    if "##" not in changelog:
        raise FastfyValidationError(
            "CHANGELOG.md sem seções; reconstrua o histórico a partir do Git"
        )
    adoption_lines = [
        line
        for line in changelog.splitlines()
        if FEAT_CHANGELOG_RE.match(line)
    ]
    if not adoption_lines:
        raise FastfyValidationError(
            "CHANGELOG.md sem entrada de adoção iniciada por `#FEAT`"
        )
    if not any(PB_RE.search(line) for line in adoption_lines):
        raise FastfyValidationError(
            "CHANGELOG.md: a entrada `#FEAT` da adoção deve referenciar o PB-* de adoção"
        )

    interface = contract["interface"]
    if interface in {"ui", "mixed"}:
        _read(root, "docs/ui_criteria.md")
    if interface in {"api", "mixed"}:
        _read(root, "docs/api_contract.md")


def validate_harness(root: Path) -> None:
    contract = _survey_contract(root)
    product_root = str(contract["product_root"])
    interface = str(contract["interface"])

    relative, makefile = _makefile_targets(root, product_root)
    required = ["build", "test"]
    if interface != "internal":
        required += ["run", "url"]
    missing = [target for target in required if not _has_target(makefile, target)]
    if missing:
        raise FastfyValidationError(
            f"{relative} sem targets obrigatórios: " + ", ".join(missing)
        )

    if contract["has_tests"] is False and not _repo_has_test_files(root):
        raise FastfyValidationError(
            "repositório sem testes e nenhum smoke test foi criado; "
            "o harness exige ao menos um teste real"
        )


def validate_review(root: Path) -> None:
    report = _read(root, REVIEW_PATH)
    results = re.findall(r"(?m)^\s*Resultado\s*:\s*(APPROVED|REJECTED)\s*$", report)
    if len(results) != 1:
        raise FastfyValidationError(
            f"{REVIEW_PATH} exige exatamente uma linha "
            "`Resultado: APPROVED` ou `Resultado: REJECTED`"
        )


VALIDATORS = {
    "preflight": validate_preflight,
    "survey": validate_survey,
    "docs": validate_docs,
    "harness": validate_harness,
    "review": validate_review,
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="Raiz do projeto/worktree")
    parser.add_argument("mode", choices=[*VALIDATORS, "all"])
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = Path(args.root).resolve()
    modes = list(VALIDATORS) if args.mode == "all" else [args.mode]
    try:
        for mode in modes:
            VALIDATORS[mode](root)
    except FastfyValidationError as exc:
        print(f"fastfy validation FAIL [{args.mode}]: {exc}", file=sys.stderr)
        return 1
    print(f"fastfy validation PASS [{args.mode}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
