#!/usr/bin/env python3
"""Valida os contratos próprios do processo iOS → Android.

O script nunca executa comandos descritos por LLM. Ele valida somente paths,
schemas e evidências locais; build e testes pertencem aos targets estáveis da
matriz de validação do Fast Track.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Mapping

import yaml


PLACEHOLDERS = {"", "-", "—", "n/a", "na", "none", "null", "tbd", "todo"}
CAPABILITY_ID = re.compile(r"CAP-\d{3,}")
AC_ID = re.compile(r"PAC-\d{3,}")
PB_ID = re.compile(r"PB-\d+")
ALLOWED_PARITY = {"exact", "adapted", "not_applicable"}
ALLOWED_STATUS = {"planned", "implemented", "validated"}


class ContractError(ValueError):
    pass


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractError(f"{label} deve ser mapping")
    return dict(value)


def _list(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ContractError(f"{label} deve ser lista")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or value.strip().casefold() in PLACEHOLDERS:
        raise ContractError(f"{label} deve ser texto não vazio e não-placeholder")
    return value.strip()


def _load_yaml(root: Path, relative: str) -> dict[str, Any]:
    path = _safe_path(root, relative, must_exist=True)
    try:
        return _mapping(yaml.safe_load(path.read_text(encoding="utf-8")), relative)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ContractError(f"{relative} inválido: {exc}") from exc


def _safe_path(root: Path, raw: object, *, must_exist: bool = False) -> Path:
    text = _text(raw, "path")
    relative = Path(text)
    if relative.is_absolute() or ".." in relative.parts or "\\" in text:
        raise ContractError(f"path deve permanecer no repositório: {text}")
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ContractError(f"path escapa do repositório: {text}") from exc
    if path.is_symlink():
        raise ContractError(f"path não pode ser symlink: {text}")
    if must_exist and (not path.exists() or (path.is_file() and path.stat().st_size == 0)):
        raise ContractError(f"path ausente ou vazio: {text}")
    return path


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=root, text=True, capture_output=True, check=False
    )


def _find_ios_signals(root: Path) -> list[str]:
    signals: list[str] = []
    ignored = {".git", ".ft", ".build", "build", "Pods", "DerivedData"}
    for path in root.rglob("*"):
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if any(part in ignored for part in relative.parts):
            continue
        name = path.name
        if path.is_dir() and path.suffix in {".xcodeproj", ".xcworkspace"}:
            signals.append(relative.as_posix())
        elif path.is_file() and (
            name in {"Package.swift", "project.yml", "Info.plist"}
            or path.suffix == ".swift"
        ):
            signals.append(relative.as_posix())
        if len(signals) >= 50:
            break
    return signals


def validate_preflight(root: Path) -> None:
    if _git(root, "rev-parse", "--verify", "HEAD").returncode != 0:
        raise ContractError("repositório Git com HEAD é obrigatório")
    _safe_path(root, "docs/ios-to-android-request.md", must_exist=True)
    _safe_path(root, ".ft/project.yml", must_exist=True)
    _safe_path(root, "docs/PROJECT_BACKLOG.md", must_exist=True)
    _safe_path(root, "docs/FEATURES.md", must_exist=True)
    if not _find_ios_signals(root):
        raise ContractError(
            "nenhum sinal de app iOS foi encontrado; registre o layout real na demanda"
        )


def _validate_plan(root: Path) -> dict[str, Any]:
    plan = _load_yaml(root, "docs/ios-android-port-plan.yml")
    if plan.get("schema_version") != 1:
        raise ContractError("port-plan.schema_version deve ser 1")
    status = _text(plan.get("clarification_status"), "clarification_status")
    if status not in {"clear", "required"}:
        raise ContractError("clarification_status deve ser clear ou required")
    backlog_item = _text(plan.get("backlog_item"), "backlog_item")
    if PB_ID.fullmatch(backlog_item) is None:
        raise ContractError("backlog_item deve ser exatamente PB-NNN")

    ios_roots = _list(plan.get("ios_roots"), "ios_roots")
    android_root = _text(plan.get("android_root"), "android_root")
    if not ios_roots:
        raise ContractError("ios_roots não pode ser vazio")
    resolved_ios = [_safe_path(root, item, must_exist=True) for item in ios_roots]
    resolved_android = _safe_path(root, android_root)
    if any(path == resolved_android for path in resolved_ios):
        raise ContractError("android_root deve ser distinto dos ios_roots")

    _text(plan.get("application_id"), "application_id")
    _text(plan.get("shared_strategy"), "shared_strategy")
    _list(plan.get("shared_contracts"), "shared_contracts")
    _list(plan.get("out_of_scope"), "out_of_scope")
    return plan


def _validate_capabilities(root: Path, *, require_implemented: bool) -> list[dict[str, Any]]:
    payload = _load_yaml(root, "docs/ios-android-capabilities.yml")
    if payload.get("schema_version") != 1:
        raise ContractError("capabilities.schema_version deve ser 1")
    raw_items = _list(payload.get("capabilities"), "capabilities")
    if not raw_items:
        raise ContractError("capabilities não pode ser vazio")
    seen_ids: set[str] = set()
    seen_acs: set[str] = set()
    items: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_items):
        item = _mapping(raw, f"capabilities[{index}]")
        capability_id = _text(item.get("id"), f"capabilities[{index}].id")
        if CAPABILITY_ID.fullmatch(capability_id) is None or capability_id in seen_ids:
            raise ContractError(f"ID de capacidade inválido ou duplicado: {capability_id}")
        seen_ids.add(capability_id)
        _text(item.get("name"), f"{capability_id}.name")
        parity = _text(item.get("parity"), f"{capability_id}.parity")
        if parity not in ALLOWED_PARITY:
            raise ContractError(f"{capability_id}.parity inválido")
        ios_evidence = _list(item.get("ios_evidence"), f"{capability_id}.ios_evidence")
        if not ios_evidence:
            raise ContractError(f"{capability_id}.ios_evidence não pode ser vazio")
        for evidence in ios_evidence:
            _safe_path(root, evidence, must_exist=True)
        criteria = _list(
            item.get("acceptance_criteria"), f"{capability_id}.acceptance_criteria"
        )
        if not criteria:
            raise ContractError(f"{capability_id} precisa de acceptance_criteria")
        for raw_criterion in criteria:
            criterion = _mapping(raw_criterion, f"{capability_id}.criterion")
            criterion_id = _text(criterion.get("id"), f"{capability_id}.criterion.id")
            if AC_ID.fullmatch(criterion_id) is None or criterion_id in seen_acs:
                raise ContractError(f"critério inválido ou duplicado: {criterion_id}")
            seen_acs.add(criterion_id)
            _text(criterion.get("assertion"), f"{criterion_id}.assertion")
        status = _text(item.get("status"), f"{capability_id}.status")
        if status not in ALLOWED_STATUS:
            raise ContractError(f"{capability_id}.status inválido")
        if parity == "not_applicable":
            _text(item.get("rationale"), f"{capability_id}.rationale")
        if require_implemented and parity != "not_applicable":
            if status not in {"implemented", "validated"}:
                raise ContractError(f"{capability_id} ainda está {status}")
            for field in ("android_evidence", "test_evidence"):
                values = _list(item.get(field), f"{capability_id}.{field}")
                if not values:
                    raise ContractError(f"{capability_id}.{field} não pode ser vazio")
                for evidence in values:
                    _safe_path(root, evidence, must_exist=True)
        items.append(item)
    return items


def validate_discovery(root: Path) -> None:
    plan = _validate_plan(root)
    _validate_capabilities(root, require_implemented=False)
    questions = _safe_path(root, "docs/ios-android-questions.md", must_exist=True)
    if plan["clarification_status"] == "required":
        text = questions.read_text(encoding="utf-8")
        if re.search(r"(?m)^\s*\d+[.)]\s+\S", text) is None:
            raise ContractError("clarification required exige perguntas numeradas")


def validate_contract(root: Path) -> None:
    plan = _validate_plan(root)
    android_root = _safe_path(root, plan["android_root"], must_exist=True)
    nested_git = android_root / ".git"
    if nested_git.exists():
        raise ContractError("Android deve permanecer no mesmo Git; .git aninhado encontrado")
    signals = [
        android_root / "settings.gradle",
        android_root / "settings.gradle.kts",
        android_root / "build.gradle",
        android_root / "build.gradle.kts",
        android_root / "gradlew",
    ]
    if not any(path.is_file() for path in signals):
        raise ContractError("android_root não contém projeto/build Gradle identificável")

    project = _load_yaml(root, ".ft/project.yml")
    validation = _mapping(project.get("validation"), "project.validation")
    if validation.get("mode") != "explicit":
        raise ContractError("project.validation.mode deve ser explicit")
    platforms = _mapping(validation.get("platforms"), "validation.platforms")
    required = {"android": {"emulator", "physical"}, "ios": {"simulator", "physical"}}
    for platform, target_ids in required.items():
        profile = _mapping(platforms.get(platform), f"platforms.{platform}")
        targets = _mapping(profile.get("targets"), f"platforms.{platform}.targets")
        for target_id in target_ids:
            target = _mapping(targets.get(target_id), f"{platform}.{target_id}")
            if target.get("required") is not True:
                raise ContractError(f"{platform}/{target_id} deve ser required=true")


def validate_implementation(root: Path) -> None:
    validate_contract(root)
    _validate_capabilities(root, require_implemented=True)
    report = _safe_path(root, "docs/ios-android-implementation.md", must_exist=True)
    text = report.read_text(encoding="utf-8")
    for heading in ("Arquitetura", "Capacidades", "Testes", "Riscos Residuais"):
        if re.search(rf"(?mi)^##+\s+{re.escape(heading)}\s*$", text) is None:
            raise ContractError(f"implementation report sem seção {heading}")


def validate_review(root: Path) -> None:
    capabilities = _validate_capabilities(root, require_implemented=True)
    review = _load_yaml(root, "docs/ios-android-review.yml")
    if review.get("schema_version") != 1:
        raise ContractError("review.schema_version deve ser 1")
    route = _text(review.get("review_route"), "review_route")
    if route not in {"approved", "implementation", "scope"}:
        raise ContractError("review_route inválida")
    verdict = _text(review.get("verdict"), "verdict")
    if verdict not in {"APPROVED", "REJECTED"}:
        raise ContractError("verdict deve ser APPROVED ou REJECTED")
    coverage = _mapping(review.get("capability_results"), "capability_results")
    expected = {item["id"] for item in capabilities}
    if set(coverage) != expected:
        raise ContractError("review deve cobrir exatamente todas as capacidades")
    failed = set()
    for capability_id, result in coverage.items():
        if result not in {"PASS", "FAIL", "N/A"}:
            raise ContractError(f"resultado inválido para {capability_id}")
        if result == "FAIL":
            failed.add(capability_id)
    findings = _list(review.get("findings"), "findings")
    finding_refs = {
        _text(_mapping(item, "finding").get("capability"), "finding.capability")
        for item in findings
    }
    if failed - finding_refs:
        raise ContractError("toda capacidade FAIL precisa de finding")
    if verdict == "APPROVED" and (failed or findings or route != "approved"):
        raise ContractError("APPROVED exige cobertura sem FAIL/findings e rota approved")
    _safe_path(root, "docs/ios-android-review.md", must_exist=True)


def validate_reconcile(root: Path) -> None:
    plan = _validate_plan(root)
    backlog_item = plan["backlog_item"]
    backlog = _safe_path(root, "docs/PROJECT_BACKLOG.md", must_exist=True).read_text(
        encoding="utf-8"
    )
    matching = [line for line in backlog.splitlines() if backlog_item in line and "|" in line]
    if len(matching) != 1 or re.search(r"\|\s*(done|accepted)\s*\|", matching[0], re.I) is None:
        raise ContractError(f"{backlog_item} deve aparecer uma vez como done/accepted")
    summary = _safe_path(root, "docs/ios-android-port-summary.md", must_exist=True)
    text = summary.read_text(encoding="utf-8")
    for heading in ("Resultado", "Paridade", "Evidências", "Limitações"):
        if re.search(rf"(?mi)^##+\s+{re.escape(heading)}\s*$", text) is None:
            raise ContractError(f"port summary sem seção {heading}")


VALIDATORS = {
    "preflight": validate_preflight,
    "discovery": validate_discovery,
    "contract": validate_contract,
    "implementation": validate_implementation,
    "review": validate_review,
    "reconcile": validate_reconcile,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=sorted(VALIDATORS))
    parser.add_argument("--root", default=".")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    try:
        VALIDATORS[args.mode](root)
    except (ContractError, OSError, UnicodeError) as exc:
        print(f"ios-to-android {args.mode} FAIL: {exc}", file=sys.stderr)
        return 1
    print(f"ios-to-android {args.mode}: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
