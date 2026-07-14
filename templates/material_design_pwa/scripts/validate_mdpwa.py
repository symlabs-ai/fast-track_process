#!/usr/bin/env python3
"""Deterministic validators for the local ``material_design_pwa`` process.

The script validates artifacts only. Build/test execution remains owned by
``process.yml`` (via ``product.sh``) so command output stays visible in the
engine gate log.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import unicodedata

import yaml


AUDIT_PATH = "docs/mdpwa-audit.md"
QUESTIONS_PATH = "docs/mdpwa-questions.md"
PLAN_PATH = "docs/mdpwa-plan.md"
REVIEW_PATH = "docs/mdpwa-review.md"
RESULT_PATH = "docs/mdpwa-result.md"

PB_RE = re.compile(r"\bPB-\d+[A-Z]?\b", re.IGNORECASE)
CLARIFICATION_RE = re.compile(
    r"^\s*clarification_status\s*:\s*(required|clear)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
FEAT_CHANGELOG_RE = re.compile(r"(?m)^[ \t]*(?:[-*+][ \t]+)?#FEAT(?=[ \t]|$)[^\r\n]*")

# Conteúdo mínimo que o contrato de tokens M3 da Fase 1 precisa carregar.
THEME_REQUIRED_MARKERS = (
    "--md-sys-color-primary",
    "--md-sys-color-surface",
    "--md-sys-color-on-surface",
    "--md-sys-color-outline",
    "--md-sys-typescale-",
    "--md-sys-shape-corner-",
)
THEME_REQUIRED_PATTERNS = (
    (":focus-visible", re.compile(r":focus-visible")),
    ("prefers-color-scheme: dark", re.compile(r"prefers-color-scheme\s*:\s*dark")),
)

MANIFEST_REQUIRED_KEYS = ("name", "short_name", "start_url", "display", "theme_color")
MANIFEST_REQUIRED_ICON_SIZES = ("192x192", "512x512")

# Itens do checklist de QA das guidelines que a revisão precisa cobrir.
REVIEW_CHECKLIST = (
    "tokens",
    "tipografia",
    "responsividade",
    "navegacao",
    "acessibilidade",
    "instalacao",
    "offline",
    "atualizacao",
    "payload",
)


class MdpwaValidationError(ValueError):
    """A user-facing artifact violation for the Material PWA process."""


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    return "".join(char for char in value if not unicodedata.combining(char)).lower()


def _read(root: Path, relative: str) -> str:
    path = root / relative
    if not path.is_file():
        raise MdpwaValidationError(f"arquivo obrigatório ausente: {relative}")
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        raise MdpwaValidationError(f"arquivo obrigatório vazio: {relative}")
    return text


def _frontmatter(text: str, path: str) -> dict[str, object]:
    if not text.lstrip().startswith("---"):
        raise MdpwaValidationError(f"{path}: frontmatter YAML ausente")
    parts = text.lstrip().split("---", 2)
    if len(parts) < 3:
        raise MdpwaValidationError(f"{path}: frontmatter YAML não foi fechado")
    try:
        data = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError as exc:
        raise MdpwaValidationError(f"{path}: frontmatter YAML inválido: {exc}") from exc
    if not isinstance(data, dict):
        raise MdpwaValidationError(f"{path}: frontmatter deve ser um mapping")
    return data


def _safe_relative_path(raw: object, field: str, path: str) -> str:
    value = str(raw or "").strip()
    if not value:
        raise MdpwaValidationError(f"{path}: campo {field} ausente ou vazio")
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise MdpwaValidationError(
            f"{path}: {field} deve ser um path relativo dentro do projeto"
        )
    return value


def _detect_product_root(root: Path) -> str:
    candidates = [
        relative
        for relative in ("project", "src")
        if (root / relative / "Makefile").is_file()
    ]
    if len(candidates) > 1:
        raise MdpwaValidationError(
            "mais de um diretório de produto possui Makefile: " + ", ".join(candidates)
        )
    if candidates:
        return candidates[0]
    if (root / "Makefile").is_file():
        return "."
    raise MdpwaValidationError(
        "Makefile do produto ausente; esperado em project/Makefile, src/Makefile "
        "ou Makefile na raiz — rode o template fastfy para criar o harness"
    )


def _audit_contract(root: Path) -> dict[str, object]:
    text = _read(root, AUDIT_PATH)
    metadata = _frontmatter(text, AUDIT_PATH)

    for field in ("framework", "ui_root"):
        if not str(metadata.get(field) or "").strip():
            raise MdpwaValidationError(f"{AUDIT_PATH}: campo {field} ausente ou vazio")
    for field in ("has_manifest", "has_service_worker"):
        if not isinstance(metadata.get(field), bool):
            raise MdpwaValidationError(
                f"{AUDIT_PATH}: campo {field} deve ser true ou false"
            )

    match = CLARIFICATION_RE.search(text)
    if not match:
        raise MdpwaValidationError(
            f"{AUDIT_PATH} sem clarification_status: required|clear"
        )
    return {"clarification_status": match.group(1).lower()}


def _plan_contract(root: Path) -> dict[str, str]:
    text = _read(root, PLAN_PATH)
    metadata = _frontmatter(text, PLAN_PATH)

    backlog = str(metadata.get("backlog_item") or "").strip().upper()
    if not PB_RE.fullmatch(backlog):
        raise MdpwaValidationError(f"{PLAN_PATH}: backlog_item inválido: {backlog!r}")

    contract = {"backlog_item": backlog}
    for field in ("theme_file", "manifest_path", "sw_source"):
        contract[field] = _safe_relative_path(metadata.get(field), field, PLAN_PATH)

    offline = str(metadata.get("offline_fallback") or "").strip()
    if not offline:
        raise MdpwaValidationError(f"{PLAN_PATH}: campo offline_fallback ausente")
    if offline != "generated":
        offline = _safe_relative_path(offline, "offline_fallback", PLAN_PATH)
    contract["offline_fallback"] = offline
    return contract


def validate_preflight(root: Path) -> None:
    product_root = _detect_product_root(root)
    makefile_path = "Makefile" if product_root == "." else f"{product_root}/Makefile"
    makefile = _read(root, makefile_path)
    missing = [
        target
        for target in ("build", "test", "run", "url")
        if not re.search(rf"(?m)^{re.escape(target)}\s*:", makefile)
    ]
    if missing:
        raise MdpwaValidationError(
            f"{makefile_path} sem targets obrigatórios para um ciclo de UI: "
            + ", ".join(missing)
        )


def validate_audit(root: Path) -> None:
    contract = _audit_contract(root)
    questions = _read(root, QUESTIONS_PATH)
    _read(root, PLAN_PATH)
    if contract["clarification_status"] == "required":
        if "?" not in questions:
            raise MdpwaValidationError(
                f"clarification_status=required exige perguntas em {QUESTIONS_PATH}"
            )
        return

    plan = _plan_contract(root)
    backlog_text = _read(root, "docs/PROJECT_BACKLOG.md")
    if plan["backlog_item"] not in backlog_text.upper():
        raise MdpwaValidationError(
            f"PROJECT_BACKLOG não contém {plan['backlog_item']}"
        )


def validate_theme(root: Path) -> None:
    plan = _plan_contract(root)
    theme_file = plan["theme_file"]
    theme = _read(root, theme_file)

    missing = [marker for marker in THEME_REQUIRED_MARKERS if marker not in theme]
    for label, pattern in THEME_REQUIRED_PATTERNS:
        if not pattern.search(theme):
            missing.append(label)
    if missing:
        raise MdpwaValidationError(
            f"{theme_file} sem o contrato mínimo de tokens M3: "
            + ", ".join(missing)
        )


def validate_pwa(root: Path) -> None:
    plan = _plan_contract(root)

    manifest_text = _read(root, plan["manifest_path"])
    try:
        manifest = json.loads(manifest_text)
    except json.JSONDecodeError as exc:
        raise MdpwaValidationError(
            f"{plan['manifest_path']}: JSON inválido: {exc}"
        ) from exc
    if not isinstance(manifest, dict):
        raise MdpwaValidationError(f"{plan['manifest_path']}: raiz deve ser um objeto")

    missing_keys = [
        key for key in MANIFEST_REQUIRED_KEYS if not str(manifest.get(key) or "").strip()
    ]
    if missing_keys:
        raise MdpwaValidationError(
            f"{plan['manifest_path']} sem campos obrigatórios: "
            + ", ".join(missing_keys)
        )

    icons = manifest.get("icons")
    sizes = {
        str(icon.get("sizes") or "").strip()
        for icon in icons or []
        if isinstance(icon, dict)
    }
    missing_sizes = [size for size in MANIFEST_REQUIRED_ICON_SIZES if size not in sizes]
    if missing_sizes:
        raise MdpwaValidationError(
            f"{plan['manifest_path']}: ícones obrigatórios ausentes: "
            + ", ".join(missing_sizes)
        )

    _read(root, plan["sw_source"])
    if plan["offline_fallback"] != "generated":
        _read(root, plan["offline_fallback"])


def validate_review(root: Path) -> None:
    report = _read(root, REVIEW_PATH)
    results = re.findall(r"(?m)^\s*Resultado\s*:\s*(APPROVED|REJECTED)\s*$", report)
    if len(results) != 1:
        raise MdpwaValidationError(
            f"{REVIEW_PATH} exige exatamente uma linha "
            "`Resultado: APPROVED` ou `Resultado: REJECTED`"
        )

    statuses: dict[str, set[str]] = {item: set() for item in REVIEW_CHECKLIST}
    for line in report.splitlines():
        normalized = _normalize(line)
        found = {status for status in ("pass", "fail") if re.search(rf"\b{status}\b", normalized)}
        if len(found) != 1:
            continue
        for item in REVIEW_CHECKLIST:
            if item in normalized:
                statuses[item].add(next(iter(found)).upper())

    missing = [item for item, values in statuses.items() if not values]
    ambiguous = [item for item, values in statuses.items() if len(values) > 1]
    if missing or ambiguous:
        details: list[str] = []
        if missing:
            details.append("itens do checklist sem PASS/FAIL: " + ", ".join(missing))
        if ambiguous:
            details.append("itens com status ambíguo: " + ", ".join(ambiguous))
        raise MdpwaValidationError(f"{REVIEW_PATH}: " + "; ".join(details))

    if results[0] == "APPROVED":
        failed = [item for item, values in statuses.items() if "FAIL" in values]
        if failed:
            raise MdpwaValidationError(
                f"{REVIEW_PATH}: Resultado APPROVED exige todos os itens PASS; "
                "FAIL em " + ", ".join(failed)
            )


def validate_reconcile(root: Path) -> None:
    plan = _plan_contract(root)
    backlog = plan["backlog_item"]

    backlog_text = _read(root, "docs/PROJECT_BACKLOG.md")
    row = next(
        (
            line
            for line in backlog_text.splitlines()
            if line.strip().startswith("|") and backlog in line.upper()
        ),
        None,
    )
    if row is None:
        raise MdpwaValidationError(f"PROJECT_BACKLOG não contém {backlog}")
    if not re.search(r"\b(done|accepted)\b", _normalize(row)):
        raise MdpwaValidationError(f"{backlog} deve terminar done/accepted")

    changelog = _read(root, "CHANGELOG.md")
    entries = FEAT_CHANGELOG_RE.findall(changelog)
    if not any(backlog in entry.upper() for entry in entries):
        raise MdpwaValidationError(
            f"CHANGELOG.md sem entrada `#FEAT` referenciando {backlog}"
        )

    result = _read(root, RESULT_PATH)
    section = re.search(
        r"(?ims)^##\s+Documenta\S*\s+atualizada\s*$\n(.*?)(?=^##\s+|\Z)",
        result,
    )
    if not section:
        raise MdpwaValidationError(
            f"{RESULT_PATH} sem seção `Documentação atualizada`"
        )
    for required in ("CHANGELOG.md", "docs/PROJECT_BACKLOG.md"):
        if required not in section.group(1):
            raise MdpwaValidationError(
                f"{RESULT_PATH}: seção `Documentação atualizada` não lista {required}"
            )


VALIDATORS = {
    "preflight": validate_preflight,
    "audit": validate_audit,
    "theme": validate_theme,
    "pwa": validate_pwa,
    "review": validate_review,
    "reconcile": validate_reconcile,
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
    except MdpwaValidationError as exc:
        print(f"mdpwa validation FAIL [{args.mode}]: {exc}", file=sys.stderr)
        return 1
    print(f"mdpwa validation PASS [{args.mode}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
