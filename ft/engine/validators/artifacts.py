"""
Validadores deterministicos de artefatos.
Cada funcao retorna (passed: bool, detail: str).
"""

from __future__ import annotations

import ast
import hashlib
import os
import re
import signal
import subprocess
import unicodedata
from pathlib import Path

import yaml


def _normalize(text: str) -> str:
    """Remove diacritics for accent-insensitive matching."""
    return (
        unicodedata.normalize("NFD", text)
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
    )


def _normalize_block(text: str) -> str:
    """Normaliza bloco de texto para comparação determinística."""
    return "\n".join(line.rstrip() for line in text.strip().splitlines())


def _extract_markdown_section(content: str, section: str) -> str | None:
    """Extrai uma seção Markdown pelo heading, incluindo subseções internas."""
    lines = content.splitlines()
    in_code_block = False
    target_start = None
    target_level = None

    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue

        match = re.match(r"^(#{1,6})\s+(.*)$", line)
        if not match:
            continue

        level = len(match.group(1))
        title = match.group(2).strip()
        norm_title = _normalize(title)
        norm_section = _normalize(section)
        if norm_title == norm_section or norm_section in norm_title:
            target_start = idx
            target_level = level
            break

    if target_start is None or target_level is None:
        return None

    in_code_block = False
    target_end = len(lines)
    for idx in range(target_start + 1, len(lines)):
        stripped = lines[idx].strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue

        match = re.match(r"^(#{1,6})\s+(.*)$", lines[idx])
        if match and len(match.group(1)) <= target_level:
            target_end = idx
            break

    return "\n".join(lines[target_start:target_end]).strip()


def file_exists(path: str, project_root: str = ".") -> tuple[bool, str]:
    """Verifica se arquivo existe."""
    full = Path(project_root) / path
    if full.exists():
        return True, f"file_exists: {path}"
    return False, f"file_exists FAIL: {path} nao encontrado"


def test_identity_ready(
    path: str = "docs/test-identity.json",
    project_root: str = ".",
) -> tuple[bool, str]:
    """Validate the sanitized receipt for a dedicated authenticated E2E user."""

    full = Path(project_root) / path
    try:
        payload = yaml.safe_load(full.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        return False, f"test_identity_ready FAIL: recibo ausente ou inválido ({exc})"
    if not isinstance(payload, dict):
        return False, "test_identity_ready FAIL: recibo deve ser JSON/YAML estruturado"

    identity_ref = str(payload.get("identity_ref") or "").strip().casefold()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._:-]{2,127}", identity_ref):
        return False, "test_identity_ready FAIL: identity_ref opaco inválido"
    if str(payload.get("environment") or "").strip().casefold() not in {
        "local_test",
        "isolated_test",
        "staging",
    }:
        return False, "test_identity_ready FAIL: ambiente de teste inválido"
    if str(payload.get("seed_status") or "").strip().casefold() != "ready":
        return False, "test_identity_ready FAIL: seed não está ready"
    for field in ("seeded", "idempotent", "resettable", "journey_ready"):
        if payload.get(field) is not True:
            return False, f"test_identity_ready FAIL: {field} deve ser true"
    credentials_source = str(payload.get("credentials_source") or "").strip().casefold()
    protected_sources = {
        "secret_store",
        "protected_file",
        "device_secure_store",
    }
    if credentials_source == "not_required":
        authentication = payload.get("authentication")
        no_authentication_required = (
            isinstance(authentication, dict)
            and authentication.get("required_for_journey") is False
            and str(authentication.get("status") or "").strip().casefold()
            == "not_required"
            and authentication.get("credential_material_observed_or_recorded") is False
        )
        if not no_authentication_required:
            return False, (
                "test_identity_ready FAIL: origem not_required exige jornada "
                "explicitamente sem autenticação"
            )
    elif credentials_source not in protected_sources:
        return False, "test_identity_ready FAIL: origem de credenciais desprotegida"
    if payload.get("secret_values_recorded") is not False:
        return False, "test_identity_ready FAIL: recibo pode conter segredo"

    forbidden_keys = {"email", "phone", "password", "access_token", "token", "secret"}

    def contains_sensitive_key(value: object) -> bool:
        if isinstance(value, dict):
            if forbidden_keys & {str(key).casefold() for key in value}:
                return True
            return any(contains_sensitive_key(item) for item in value.values())
        if isinstance(value, list):
            return any(contains_sensitive_key(item) for item in value)
        return False

    if contains_sensitive_key(payload):
        return False, "test_identity_ready FAIL: recibo contém campo sensível"
    return (
        True,
        f"test_identity_ready: {identity_ref} ready em {payload['environment']}",
    )


_NAVIGATION_ENTRY_POLICIES = {"public", "entitled", "contextual", "first_launch"}
_NAVIGATION_ENTRY_TYPES = {
    "primary_navigation",
    "menu",
    "first_launch",
    "visible_control",
    "external_event",
}
_NAVIGATION_ACCESS_CONTEXTS = {"public", "entitled", "contextual"}
_NAVIGATION_FORBIDDEN_SHORTCUT_RE = re.compile(
    r"direct[ _-]?route|rota direta|deep[ _-]?link(?: de debug)?|"
    r"setcontent\s*\(|component[ _-]?mount|storybook|screen[ _-]?catalog|"
    r"catalogo tecnico|catálogo técnico|adb\s+shell\s+am\s+start|"
    r"test[ _-]?only|hook de teste",
    re.IGNORECASE,
)


def _navigation_mapping(value: object, label: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{label} deve ser um mapping")
    return value


def _navigation_list(value: object, label: str) -> list:
    if not isinstance(value, list):
        raise ValueError(f"{label} deve ser uma lista")
    return value


def _navigation_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} deve ser texto não vazio")
    return value.strip()


def _navigation_repo_file(
    root: Path,
    raw_path: object,
    label: str,
    *,
    under: Path | None = None,
) -> Path:
    text = _navigation_text(raw_path, label)
    relative = Path(text)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"{label} deve ser um path relativo seguro")
    resolved = (root / relative).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"{label} escapou do repositório") from exc
    if under is not None:
        try:
            resolved.relative_to(under.resolve())
        except ValueError as exc:
            raise ValueError(
                f"{label} deve ficar sob {under.relative_to(root)}"
            ) from exc
    if not resolved.is_file() or resolved.stat().st_size == 0:
        raise ValueError(f"{label} não existe ou está vazio: {relative.as_posix()}")
    return resolved


def _navigation_scope_ids(
    scope_file: Path,
    scope_pattern: str,
    scope_exclude_pattern: str | None = None,
) -> set[str]:
    try:
        pattern = re.compile(scope_pattern, re.IGNORECASE)
    except re.error as exc:
        raise ValueError(f"scope_pattern inválido: {exc}") from exc
    text = scope_file.read_text(encoding="utf-8", errors="strict")
    if not scope_exclude_pattern:
        return {match.group(0).upper() for match in pattern.finditer(text)}
    try:
        exclude = re.compile(scope_exclude_pattern, re.IGNORECASE)
    except re.error as exc:
        raise ValueError(f"scope_exclude_pattern inválido: {exc}") from exc
    return {
        match.group(0).upper()
        for line in text.splitlines()
        if not exclude.search(line)
        for match in pattern.finditer(line)
    }


def _load_navigation_contract(
    *,
    root: Path,
    path: str,
    scope_path: str,
    scope_pattern: str,
    min_targets: int,
) -> tuple[dict, dict[str, dict], set[str]]:
    contract_file = _navigation_repo_file(root, path, "navigation contract")
    scope_file = _navigation_repo_file(root, scope_path, "navigation scope")
    payload = _navigation_mapping(
        yaml.safe_load(contract_file.read_text(encoding="utf-8", errors="strict")),
        "navigation contract",
    )
    if payload.get("schema_version") != 1:
        raise ValueError("schema_version deve ser 1")

    expected_scope_hash = hashlib.sha256(scope_file.read_bytes()).hexdigest()
    if str(payload.get("scope_sha256") or "").strip().lower() != expected_scope_hash:
        raise ValueError("scope_sha256 não corresponde aos bytes do escopo")

    scope_ids = _navigation_scope_ids(scope_file, scope_pattern)
    if not scope_ids:
        raise ValueError("o escopo não contém referências identificáveis")

    targets: dict[str, dict] = {}
    for index, raw_target in enumerate(
        _navigation_list(payload.get("targets"), "targets")
    ):
        target = _navigation_mapping(raw_target, f"targets[{index}]")
        target_id = _navigation_text(target.get("id"), f"targets[{index}].id").upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_.:-]{2,63}", target_id):
            raise ValueError(f"targets[{index}].id inválido: {target_id!r}")
        if target_id in targets:
            raise ValueError(f"target duplicado: {target_id}")
        _navigation_text(target.get("label"), f"targets[{index}].label")
        policy = _navigation_text(
            target.get("entry_policy"), f"targets[{index}].entry_policy"
        )
        if policy not in _NAVIGATION_ENTRY_POLICIES:
            raise ValueError(
                f"targets[{index}].entry_policy deve ser um de "
                f"{sorted(_NAVIGATION_ENTRY_POLICIES)}"
            )
        targets[target_id] = target
    if len(targets) < min_targets:
        raise ValueError(
            f"targets possui {len(targets)} item(ns); mínimo {min_targets}"
        )

    scope_rows: dict[str, dict] = {}
    used_targets: set[str] = set()
    for index, raw_row in enumerate(
        _navigation_list(payload.get("scope_refs"), "scope_refs")
    ):
        row = _navigation_mapping(raw_row, f"scope_refs[{index}]")
        ref = _navigation_text(row.get("ref"), f"scope_refs[{index}].ref").upper()
        if ref in scope_rows:
            raise ValueError(f"scope ref duplicada: {ref}")
        disposition = _navigation_text(
            row.get("disposition"), f"scope_refs[{index}].disposition"
        )
        if disposition not in {"ui", "non_ui"}:
            raise ValueError(f"scope_refs[{index}].disposition deve ser ui ou non_ui")
        raw_targets = _navigation_list(
            row.get("targets", []), f"scope_refs[{index}].targets"
        )
        row_targets = {
            _navigation_text(value, f"scope_refs[{index}].targets").upper()
            for value in raw_targets
        }
        if disposition == "ui" and not row_targets:
            raise ValueError(f"scope ref UI sem target: {ref}")
        if disposition == "non_ui":
            if row_targets:
                raise ValueError(f"scope ref non_ui não pode apontar targets: {ref}")
            _navigation_text(row.get("reason"), f"scope_refs[{index}].reason")
        unknown_targets = sorted(row_targets - set(targets))
        if unknown_targets:
            raise ValueError(
                f"scope ref {ref} aponta targets inexistentes: {unknown_targets}"
            )
        used_targets.update(row_targets)
        scope_rows[ref] = row

    missing_refs = sorted(scope_ids - set(scope_rows))
    extra_refs = sorted(set(scope_rows) - scope_ids)
    if missing_refs or extra_refs:
        raise ValueError(
            f"cobertura do escopo divergente; ausentes={missing_refs}, extras={extra_refs}"
        )
    orphan_targets = sorted(set(targets) - used_targets)
    if orphan_targets:
        raise ValueError(f"targets sem referência de escopo: {orphan_targets}")
    return payload, targets, scope_ids


def navigation_contract_valid(
    path: str = "docs/navigation-contract.yml",
    scope_path: str = "docs/PROJECT_BACKLOG.md",
    scope_pattern: str = r"\bPB-\d+\b",
    min_targets: int = 1,
    project_root: str = ".",
) -> tuple[bool, str]:
    """Validate a generic, scope-bound contract of user-visible entry targets."""

    try:
        _payload, targets, scope_ids = _load_navigation_contract(
            root=Path(project_root).resolve(),
            path=path,
            scope_path=scope_path,
            scope_pattern=scope_pattern,
            min_targets=min_targets,
        )
    except (OSError, UnicodeError, yaml.YAMLError, ValueError) as exc:
        return False, f"navigation_contract_valid FAIL: {exc}"
    return True, (
        "navigation_contract_valid: "
        f"{len(scope_ids)} refs classificadas, {len(targets)} targets"
    )


def navigation_reachability(
    contract_path: str = "docs/navigation-contract.yml",
    report_path: str = "docs/navigation-reachability.yml",
    evidence_root: str = "docs/evidence/navigation",
    scope_path: str = "docs/PROJECT_BACKLOG.md",
    scope_pattern: str = r"\bPB-\d+\b",
    min_targets: int = 1,
    require_approved: bool = True,
    project_root: str = ".",
) -> tuple[bool, str]:
    """Prove that every contracted target is reachable through production UI.

    A rendered component or an internal route is not sufficient. The structured
    receipt must bind to the current navigation contract, identify the observed
    candidate and cover every target through visible production controls.
    """

    root = Path(project_root).resolve()
    try:
        _contract, targets, _scope_ids = _load_navigation_contract(
            root=root,
            path=contract_path,
            scope_path=scope_path,
            scope_pattern=scope_pattern,
            min_targets=min_targets,
        )
        contract_file = _navigation_repo_file(
            root, contract_path, "navigation contract"
        )
        report_file = _navigation_repo_file(root, report_path, "reachability report")
        evidence_dir = (root / evidence_root).resolve()
        try:
            evidence_dir.relative_to(root)
        except ValueError as exc:
            raise ValueError("evidence_root escapou do repositório") from exc

        report = _navigation_mapping(
            yaml.safe_load(report_file.read_text(encoding="utf-8", errors="strict")),
            "reachability report",
        )
        if report.get("schema_version") != 1:
            raise ValueError("reachability schema_version deve ser 1")
        contract_hash = hashlib.sha256(contract_file.read_bytes()).hexdigest()
        if str(report.get("contract_sha256") or "").strip().lower() != contract_hash:
            raise ValueError("contract_sha256 não corresponde ao contrato corrente")

        verdict = _navigation_text(report.get("verdict"), "verdict").upper()
        if verdict not in {"APPROVED", "REJECTED"}:
            raise ValueError("verdict deve ser APPROVED ou REJECTED")
        if require_approved and verdict != "APPROVED":
            raise ValueError("este gate exige verdict APPROVED")

        candidate_ref = _navigation_text(report.get("candidate_ref"), "candidate_ref")
        observed_ref = _navigation_text(
            report.get("observed_candidate_ref"), "observed_candidate_ref"
        )
        environment = _navigation_mapping(report.get("environment"), "environment")
        _navigation_text(environment.get("kind"), "environment.kind")
        _navigation_text(
            environment.get("execution_surface"), "environment.execution_surface"
        )
        findings = _navigation_list(report.get("findings"), "findings")
        journeys = _navigation_list(report.get("journeys"), "journeys")
        if targets and not journeys:
            raise ValueError("journeys não pode ser vazio quando há targets")

        seen_journeys: set[str] = set()
        seen_evidence_hashes: dict[str, str] = {}
        covered_by_pass: set[str] = set()
        failed_journeys: list[str] = []
        shortcut_journeys: list[str] = []

        for index, raw_journey in enumerate(journeys):
            journey = _navigation_mapping(raw_journey, f"journeys[{index}]")
            prefix = f"journeys[{index}]"
            journey_id = _navigation_text(journey.get("id"), f"{prefix}.id")
            if journey_id in seen_journeys:
                raise ValueError(f"journey id duplicado: {journey_id}")
            seen_journeys.add(journey_id)

            entry_type = _navigation_text(
                journey.get("entry_type"), f"{prefix}.entry_type"
            )
            if entry_type not in _NAVIGATION_ENTRY_TYPES:
                raise ValueError(
                    f"{prefix}.entry_type deve ser um de "
                    f"{sorted(_NAVIGATION_ENTRY_TYPES)}"
                )
            start_surface = _navigation_text(
                journey.get("start_surface"), f"{prefix}.start_surface"
            )
            entry_point = _navigation_text(
                journey.get("entry_point"), f"{prefix}.entry_point"
            )
            if journey.get("navigation_mode") != "production_ui":
                raise ValueError(f"{prefix}.navigation_mode deve ser production_ui")
            raw_steps = _navigation_list(journey.get("steps"), f"{prefix}.steps")
            if not raw_steps:
                raise ValueError(f"{prefix}.steps não pode ser vazio")
            steps = [
                _navigation_text(step, f"{prefix}.steps[{step_index}]")
                for step_index, step in enumerate(raw_steps)
            ]

            raw_targets = _navigation_list(journey.get("targets"), f"{prefix}.targets")
            if not raw_targets:
                raise ValueError(f"{prefix}.targets não pode ser vazio")
            journey_targets = {
                _navigation_text(value, f"{prefix}.targets").upper()
                for value in raw_targets
            }
            unknown_targets = sorted(journey_targets - set(targets))
            if unknown_targets:
                raise ValueError(
                    f"{prefix} aponta targets inexistentes: {unknown_targets}"
                )

            result = _navigation_text(journey.get("result"), f"{prefix}.result").upper()
            if result not in {"PASS", "FAIL"}:
                raise ValueError(f"{prefix}.result deve ser PASS ou FAIL")
            if result == "PASS":
                covered_by_pass.update(journey_targets)
            else:
                failed_journeys.append(journey_id)

            evidence_file = _navigation_repo_file(
                root,
                journey.get("evidence"),
                f"{prefix}.evidence",
                under=evidence_dir,
            )
            evidence_hash = hashlib.sha256(evidence_file.read_bytes()).hexdigest()
            if evidence_hash in seen_evidence_hashes:
                raise ValueError(
                    f"{journey_id} reutiliza a evidência de "
                    f"{seen_evidence_hashes[evidence_hash]}"
                )
            seen_evidence_hashes[evidence_hash] = journey_id

            shortcuts = _navigation_list(
                journey.get("shortcuts"), f"{prefix}.shortcuts"
            )
            path_text = " ".join([start_surface, entry_point, *steps])
            if shortcuts or _NAVIGATION_FORBIDDEN_SHORTCUT_RE.search(path_text):
                shortcut_journeys.append(journey_id)

            access_context = _navigation_text(
                journey.get("access_context"), f"{prefix}.access_context"
            )
            if access_context not in _NAVIGATION_ACCESS_CONTEXTS:
                raise ValueError(
                    f"{prefix}.access_context deve ser um de "
                    f"{sorted(_NAVIGATION_ACCESS_CONTEXTS)}"
                )
            policies = {
                str(targets[target_id]["entry_policy"]) for target_id in journey_targets
            }
            if "public" in policies and access_context != "public":
                raise ValueError(f"{prefix} não comprova acesso público")
            if "entitled" in policies and access_context != "entitled":
                raise ValueError(f"{prefix} não comprova acesso condicionado")
            if "contextual" in policies and access_context != "contextual":
                raise ValueError(f"{prefix} não comprova entrada contextual")
            if "first_launch" in policies and entry_type != "first_launch":
                raise ValueError(f"{prefix} não comprova primeira abertura")

            if access_context == "entitled":
                _navigation_text(
                    journey.get("entitlement_setup"), f"{prefix}.entitlement_setup"
                )
                for field in ("eligible_entry_result", "ineligible_entry_result"):
                    field_result = str(journey.get(field) or "").strip().upper()
                    if field_result not in {"PASS", "FAIL"}:
                        raise ValueError(f"{prefix}.{field} deve ser PASS ou FAIL")
                    if verdict == "APPROVED" and field_result != "PASS":
                        raise ValueError(
                            f"verdict APPROVED exige {prefix}.{field}: PASS"
                        )
            if access_context == "contextual":
                _navigation_text(
                    journey.get("context_setup"), f"{prefix}.context_setup"
                )
                if str(journey.get("entry_result") or "").strip().upper() not in {
                    "PASS",
                    "FAIL",
                }:
                    raise ValueError(f"{prefix}.entry_result deve ser PASS ou FAIL")
                if (
                    verdict == "APPROVED"
                    and str(journey["entry_result"]).strip().upper() != "PASS"
                ):
                    raise ValueError(
                        f"verdict APPROVED exige {prefix}.entry_result: PASS"
                    )

        missing_targets = sorted(set(targets) - covered_by_pass)
        if verdict == "APPROVED":
            if candidate_ref != observed_ref:
                raise ValueError("candidato testado difere do candidato declarado")
            if findings:
                raise ValueError("verdict APPROVED exige findings vazio")
            if failed_journeys:
                raise ValueError(
                    f"verdict APPROVED contém jornadas FAIL: {failed_journeys}"
                )
            if shortcut_journeys:
                raise ValueError(
                    "atalhos técnicos não comprovam navegação do produto: "
                    f"{shortcut_journeys}"
                )
            if missing_targets:
                raise ValueError(
                    "targets órfãos, sem jornada PASS pela UI de produção: "
                    f"{missing_targets}"
                )
    except (OSError, UnicodeError, yaml.YAMLError, ValueError) as exc:
        return False, f"navigation_reachability FAIL: {exc}"

    return True, (
        "navigation_reachability: "
        f"{len(targets)} targets, {len(journeys)} jornadas, verdict={verdict}"
    )


# APPROVED_WITH_FINDINGS é a válvula de "fix forward": o review encontrou
# defeitos reais, mas nenhum bloqueante. O ciclo segue e os achados viram
# dívida registrada no backlog, em vez de mais uma rodada review→fix→review.
_REVIEW_OUTCOME_VERDICTS = {"APPROVED", "APPROVED_WITH_FINDINGS", "REJECTED"}
_REVIEW_BLOCKING_SEVERITY = "P0"
_REVIEW_FINDING_SEVERITIES = {"P0", "P1", "P2"}


def _review_markdown_verdict(path: Path) -> str:
    raw = path.read_text(encoding="utf-8", errors="strict")
    verdicts = [
        match.group(1).upper()
        for match in re.finditer(
            # O parecer é um documento legível: o veredito costuma vir em
            # negrito e o rótulo em português varia. Exigir exatamente um
            # veredito continua estrito; recusar ``**APPROVED**`` era
            # fragilidade, não rigor.
            r"(?mi)^\s*[*_`]*\s*"
            r"(?:verdict|veredito|veredicto|resultado|result|parecer)"
            r"\s*[*_`]*\s*[:=-]\s*[*_`]*\s*"
            r"(APPROVED_WITH_FINDINGS|APPROVED|REJECTED)\s*[*_`]*\s*$",
            raw,
        )
    ]
    if len(verdicts) != 1:
        raise ValueError(
            "o relatório Markdown deve conter exatamente um veredito explícito "
            "APPROVED, APPROVED_WITH_FINDINGS ou REJECTED"
        )
    return verdicts[0]


def _validate_review_outcome_payload(
    *,
    payload: dict,
    expected_refs: set[str],
    expected_scope_hash: str,
    markdown_file: Path | None,
    require_approved: bool,
    allow_findings: bool = False,
) -> tuple[str, set[str]]:
    if payload.get("schema_version") != 1:
        raise ValueError("schema_version deve ser 1")
    if str(payload.get("scope_sha256") or "").strip().lower() != expected_scope_hash:
        raise ValueError("scope_sha256 não corresponde aos bytes do escopo")
    if not expected_refs:
        raise ValueError("o escopo não contém referências identificáveis")

    verdict = _navigation_text(payload.get("verdict"), "verdict").upper()
    if verdict not in _REVIEW_OUTCOME_VERDICTS:
        raise ValueError(
            "verdict deve ser APPROVED, APPROVED_WITH_FINDINGS ou REJECTED"
        )
    accepted = {"APPROVED"}
    if allow_findings:
        accepted.add("APPROVED_WITH_FINDINGS")
    if require_approved and verdict not in accepted:
        raise ValueError("este gate exige verdict " + " ou ".join(sorted(accepted)))

    results: dict[str, dict] = {}
    failed_refs: set[str] = set()
    pending_refs: set[str] = set()
    for index, raw_result in enumerate(
        _navigation_list(payload.get("results"), "results")
    ):
        result = _navigation_mapping(raw_result, f"results[{index}]")
        ref = _navigation_text(result.get("ref"), f"results[{index}].ref").upper()
        if ref in results:
            raise ValueError(f"resultado duplicado para {ref}")
        status = _navigation_text(
            result.get("result"), f"results[{index}].result"
        ).upper()
        if status not in {"PASS", "FAIL", "PENDING", "NOT_RUN"}:
            raise ValueError(
                f"results[{index}].result deve ser PASS, FAIL, PENDING ou NOT_RUN"
            )
        evidence = _navigation_list(
            result.get("evidence"), f"results[{index}].evidence"
        )
        if not evidence:
            raise ValueError(f"results[{index}].evidence não pode ser vazio")
        for evidence_index, value in enumerate(evidence):
            _navigation_text(
                value,
                f"results[{index}].evidence[{evidence_index}]",
            )
        if status == "FAIL":
            failed_refs.add(ref)
        elif status in {"PENDING", "NOT_RUN"}:
            pending_refs.add(ref)
        results[ref] = result

    missing_refs = sorted(expected_refs - set(results))
    extra_refs = sorted(set(results) - expected_refs)
    if missing_refs or extra_refs:
        raise ValueError(
            f"cobertura do review divergente; ausentes={missing_refs}, "
            f"extras={extra_refs}"
        )

    finding_ids: set[str] = set()
    finding_refs: set[str] = set()
    blocking_ids: set[str] = set()
    findings = _navigation_list(payload.get("findings"), "findings")
    for index, raw_finding in enumerate(findings):
        finding = _navigation_mapping(raw_finding, f"findings[{index}]")
        finding_id = _navigation_text(
            finding.get("id"), f"findings[{index}].id"
        ).upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_.:-]{2,63}", finding_id):
            raise ValueError(f"findings[{index}].id inválido: {finding_id!r}")
        if finding_id in finding_ids:
            raise ValueError(f"finding duplicado: {finding_id}")
        finding_ids.add(finding_id)
        refs = {
            _navigation_text(value, f"findings[{index}].refs").upper()
            for value in _navigation_list(
                finding.get("refs"), f"findings[{index}].refs"
            )
        }
        if not refs:
            raise ValueError(f"findings[{index}].refs não pode ser vazio")
        unknown_refs = sorted(refs - expected_refs)
        if unknown_refs:
            raise ValueError(
                f"finding {finding_id} aponta referências inexistentes: {unknown_refs}"
            )
        _navigation_text(finding.get("summary"), f"findings[{index}].summary")
        # A severidade é opcional por compatibilidade: sem ela o finding é
        # tratado como bloqueante, preservando a semântica histórica de
        # REJECTED. Fix forward exige classificação explícita.
        raw_severity = finding.get("severity")
        severity = (
            _navigation_text(raw_severity, f"findings[{index}].severity").upper()
            if raw_severity is not None
            else _REVIEW_BLOCKING_SEVERITY
        )
        if severity not in _REVIEW_FINDING_SEVERITIES:
            raise ValueError(
                f"findings[{index}].severity deve ser "
                + ", ".join(sorted(_REVIEW_FINDING_SEVERITIES))
            )
        if severity == _REVIEW_BLOCKING_SEVERITY:
            blocking_ids.add(finding_id)
        evidence = _navigation_list(
            finding.get("evidence"), f"findings[{index}].evidence"
        )
        if not evidence:
            raise ValueError(f"findings[{index}].evidence não pode ser vazio")
        for evidence_index, value in enumerate(evidence):
            _navigation_text(
                value,
                f"findings[{index}].evidence[{evidence_index}]",
            )
        finding_refs.update(refs)

    if verdict == "APPROVED":
        if failed_refs:
            raise ValueError(
                f"verdict APPROVED contém resultados FAIL: {sorted(failed_refs)}"
            )
        if pending_refs:
            raise ValueError(
                f"verdict APPROVED contém resultados pendentes: {sorted(pending_refs)}"
            )
        if findings:
            raise ValueError("verdict APPROVED exige findings vazio")
    else:
        # APPROVED_WITH_FINDINGS e REJECTED compartilham a coerência entre
        # falhas e findings; divergem apenas na severidade admitida.
        if pending_refs and verdict == "APPROVED_WITH_FINDINGS":
            raise ValueError(
                "verdict APPROVED_WITH_FINDINGS contém resultados pendentes: "
                f"{sorted(pending_refs)}"
            )
        if not failed_refs:
            raise ValueError(f"verdict {verdict} exige ao menos um resultado FAIL")
        if not findings:
            raise ValueError(f"verdict {verdict} exige findings acionáveis")
        uncovered_failures = sorted(failed_refs - finding_refs)
        findings_without_failure = sorted(finding_refs - failed_refs)
        if uncovered_failures or findings_without_failure:
            raise ValueError(
                "findings e resultados FAIL divergem; "
                f"falhas_sem_finding={uncovered_failures}, "
                f"findings_sem_falha={findings_without_failure}"
            )
        if verdict == "APPROVED_WITH_FINDINGS" and blocking_ids:
            raise ValueError(
                "verdict APPROVED_WITH_FINDINGS não admite findings P0: "
                f"{sorted(blocking_ids)}"
            )
        if verdict == "REJECTED" and not blocking_ids:
            raise ValueError(
                "verdict REJECTED exige ao menos um finding P0; classifique o "
                "achado como P1/P2 e use APPROVED_WITH_FINDINGS para seguir"
            )

    if markdown_file is not None:
        markdown_verdict = _review_markdown_verdict(markdown_file)
        if markdown_verdict != verdict:
            raise ValueError(
                "veredito do Markdown diverge do recibo estruturado: "
                f"{markdown_verdict} != {verdict}"
            )
    return verdict, finding_ids


def _load_review_outcome(
    *,
    root: Path,
    path: str,
    expected_refs: set[str],
    scope_file: Path,
    markdown_path: str | None,
    require_approved: bool,
    allow_findings: bool = False,
) -> tuple[str, set[str]]:
    receipt_file = _navigation_repo_file(root, path, "review outcome")
    markdown_file = (
        _navigation_repo_file(root, markdown_path, "review Markdown")
        if markdown_path
        else None
    )
    payload = _navigation_mapping(
        yaml.safe_load(receipt_file.read_text(encoding="utf-8", errors="strict")),
        "review outcome",
    )
    return _validate_review_outcome_payload(
        payload=payload,
        expected_refs=expected_refs,
        expected_scope_hash=hashlib.sha256(scope_file.read_bytes()).hexdigest(),
        markdown_file=markdown_file,
        require_approved=require_approved,
        allow_findings=allow_findings,
    )


def review_outcome_valid(
    path: str,
    scope_path: str,
    scope_pattern: str,
    scope_exclude_pattern: str | None = None,
    markdown_path: str | None = None,
    require_approved: bool = False,
    allow_findings: bool = False,
    project_root: str = ".",
) -> tuple[bool, str]:
    """Validate a scope-bound, deterministic review verdict and finding set.

    ``allow_findings`` habilita fix forward: um gate que exige aprovação passa
    a aceitar APPROVED_WITH_FINDINGS (defeitos reais, nenhum bloqueante), em
    vez de devolver o ciclo a mais uma rodada review→fix→review.
    """

    root = Path(project_root).resolve()
    try:
        scope_file = _navigation_repo_file(root, scope_path, "review scope")
        expected_refs = _navigation_scope_ids(
            scope_file,
            scope_pattern,
            scope_exclude_pattern,
        )
        verdict, finding_ids = _load_review_outcome(
            root=root,
            path=path,
            expected_refs=expected_refs,
            scope_file=scope_file,
            markdown_path=markdown_path,
            require_approved=require_approved,
            allow_findings=allow_findings,
        )
    except (OSError, UnicodeError, yaml.YAMLError, ValueError) as exc:
        return False, f"review_outcome_valid FAIL: {exc}"
    return True, (
        "review_outcome_valid: "
        f"{len(expected_refs)} refs, {len(finding_ids)} findings, verdict={verdict}"
    )


def review_findings_tracked(
    review_paths: list | str,
    backlog_path: str = "docs/PROJECT_BACKLOG.md",
    project_root: str = ".",
) -> tuple[bool, str]:
    """Exige que findings não bloqueantes aceitos virem dívida registrada.

    Contrapartida determinística do fix forward: um review pode aprovar com
    findings P1/P2 e o ciclo segue, mas cada finding aceito precisa aparecer
    no backlog do produto. Sem isto, "seguir em frente" viraria "esquecer".
    """

    root = Path(project_root).resolve()
    if isinstance(review_paths, str):
        candidates = [review_paths]
    else:
        candidates = [str(item) for item in review_paths]

    accepted: dict[str, str] = {}
    try:
        for candidate in candidates:
            receipt = root / candidate
            if not receipt.is_file():
                # Rotas condicionais podem não produzir todos os receipts.
                continue
            payload = yaml.safe_load(
                receipt.read_text(encoding="utf-8", errors="strict")
            )
            if not isinstance(payload, dict):
                raise ValueError(f"{candidate}: receipt de review inválido")
            verdict = str(payload.get("verdict") or "").strip().upper()
            if verdict != "APPROVED_WITH_FINDINGS":
                continue
            for index, raw in enumerate(payload.get("findings") or []):
                if not isinstance(raw, dict):
                    raise ValueError(f"{candidate}: findings[{index}] inválido")
                finding_id = str(raw.get("id") or "").strip().upper()
                if not finding_id:
                    raise ValueError(f"{candidate}: findings[{index}].id vazio")
                accepted[finding_id] = candidate
    except (OSError, UnicodeError, yaml.YAMLError, ValueError) as exc:
        return False, f"review_findings_tracked FAIL: {exc}"

    if not accepted:
        return True, "review_findings_tracked: nenhum finding aceito a rastrear"

    backlog_file = root / backlog_path
    if not backlog_file.exists():
        return False, (
            f"review_findings_tracked FAIL: {backlog_path} ausente, mas "
            f"{len(accepted)} finding(s) foram aceitos como dívida"
        )
    backlog_text = backlog_file.read_text(encoding="utf-8", errors="ignore").upper()
    missing = sorted(
        f"{finding_id} ({origin})"
        for finding_id, origin in accepted.items()
        if finding_id not in backlog_text
    )
    if missing:
        return False, (
            "review_findings_tracked FAIL: findings aceitos ausentes do backlog: "
            + ", ".join(missing[:8])
        )
    return True, (
        f"review_findings_tracked: {len(accepted)} finding(s) aceito(s) "
        "rastreado(s) no backlog"
    )


def review_chain_approved(
    review_path: str,
    review_markdown_path: str,
    scope_path: str,
    scope_pattern: str,
    fix_review_path: str,
    fix_review_markdown_path: str,
    fix_scope_path: str | None = None,
    fix_scope_pattern: str = r"\bFX-\d+\b",
    project_root: str = ".",
) -> tuple[bool, str]:
    """Require either an approved review or an approved receipt for all findings."""

    root = Path(project_root).resolve()
    try:
        scope_file = _navigation_repo_file(root, scope_path, "review scope")
        expected_refs = _navigation_scope_ids(scope_file, scope_pattern)
        verdict, finding_ids = _load_review_outcome(
            root=root,
            path=review_path,
            expected_refs=expected_refs,
            scope_file=scope_file,
            markdown_path=review_markdown_path,
            require_approved=False,
        )
        fix_scope_candidate = root / (fix_scope_path or "")
        fix_review_candidate = root / fix_review_path
        has_fix_scope = bool(fix_scope_path) and fix_scope_candidate.is_file()
        has_fix_review = fix_review_candidate.is_file()
        if has_fix_scope != has_fix_review:
            raise ValueError(
                "fix scope e fix review devem existir juntos para comprovar o delta"
            )
        if not has_fix_scope:
            if verdict in {"APPROVED", "APPROVED_WITH_FINDINGS"}:
                return True, f"review_chain_approved: review original {verdict}"
            raise ValueError("review rejeitado ainda não possui fix review aprovado")

        fix_scope_file = _navigation_repo_file(root, fix_scope_path, "fix review scope")
        fix_refs = _navigation_scope_ids(fix_scope_file, fix_scope_pattern)
        if verdict == "REJECTED":
            if not finding_ids:
                raise ValueError("review rejeitado não possui findings para auditar")
            normalized_fix_scope = fix_scope_file.read_text(
                encoding="utf-8", errors="strict"
            ).upper()
            missing_findings = sorted(
                finding_id
                for finding_id in finding_ids
                if finding_id not in normalized_fix_scope
            )
            if missing_findings:
                raise ValueError(
                    "fix report não referencia findings do review original: "
                    f"{missing_findings}"
                )

        fix_verdict, remaining_findings = _load_review_outcome(
            root=root,
            path=fix_review_path,
            expected_refs=fix_refs,
            scope_file=fix_scope_file,
            markdown_path=fix_review_markdown_path,
            require_approved=True,
        )
        if remaining_findings:
            raise ValueError("fix review aprovado não pode manter findings")
    except (OSError, UnicodeError, yaml.YAMLError, ValueError) as exc:
        return False, f"review_chain_approved FAIL: {exc}"
    return True, (
        "review_chain_approved: "
        f"{len(fix_refs)} findings corrigidos, verdict={fix_verdict}"
    )


def builder_batch_plan_valid(
    path: str,
    request_path: str,
    policy: dict | None = None,
    project_root: str = ".",
) -> tuple[bool, str]:
    """Valida o plano natural→estruturado antes de abrir qualquer lane."""
    from ft.engine.builder_batch import BatchPlanError, load_batch_plan

    root = Path(project_root)
    try:
        plan = load_batch_plan(
            root / path,
            root / request_path,
            policy or {},
        )
    except (BatchPlanError, OSError, UnicodeError) as exc:
        return False, f"builder_batch_plan_valid FAIL: {exc}"
    return (
        True,
        "builder_batch_plan_valid: "
        f"{len(plan.lanes)} lanes, {len(plan.waves)} waves, "
        f"{len(plan.requirements)} requirements",
    )


def min_lines(path: str, n: int, project_root: str = ".") -> tuple[bool, str]:
    """Verifica se arquivo tem pelo menos N linhas."""
    full = Path(project_root) / path
    if not full.exists():
        return False, f"min_lines FAIL: {path} nao existe"
    lines = len(full.read_text().splitlines())
    if lines >= n:
        return True, f"min_lines: {path} tem {lines} linhas (min {n})"
    return False, f"min_lines FAIL: {path} tem {lines} linhas (min {n})"


def has_sections(
    path: str = "", sections: list[str] = None, project_root: str = ".", file: str = ""
) -> tuple[bool, str]:
    """Verifica se arquivo contem as secoes esperadas.
    Aceita 'path' ou 'file' como nome do argumento (aliases).
    """
    if sections is None:
        sections = []
    effective_path = file or path
    full = Path(project_root) / effective_path
    if not full.exists():
        return False, f"has_sections FAIL: {effective_path} nao existe"
    content = full.read_text()
    norm_content = _normalize(content)
    missing = [s for s in sections if _normalize(s) not in norm_content]
    if not missing:
        return (
            True,
            f"has_sections: {effective_path} tem todas as {len(sections)} secoes",
        )
    return False, f"has_sections FAIL: {effective_path} faltam secoes: {missing}"


def document_quality(
    path: str = "",
    project_root: str = ".",
    file: str = "",
    min_lines_count: int = 8,
    max_lines_count: int | None = None,
    forbidden: list[str] | None = None,
    required_terms: list[str] | None = None,
    min_required_terms: int | None = None,
) -> tuple[bool, str]:
    """Barreira genérica contra artefatos que são eco de prompt/tool call.

    Não tenta julgar conteúdo de produto; só garante que o documento tem corpo
    mínimo e não contém marcas comuns de resposta incompleta do agente.
    """
    effective_path = file or path
    full = Path(project_root) / effective_path
    if not full.exists():
        return False, f"document_quality FAIL: {effective_path} nao existe"

    content = full.read_text(encoding="utf-8", errors="ignore")
    nonblank = [line for line in content.splitlines() if line.strip()]
    if len(nonblank) < min_lines_count:
        return False, (
            f"document_quality FAIL: {effective_path} tem {len(nonblank)} linhas uteis "
            f"(min {min_lines_count})"
        )
    if max_lines_count is not None and len(nonblank) > max_lines_count:
        return False, (
            f"document_quality FAIL: {effective_path} tem {len(nonblank)} linhas uteis "
            f"(max {max_lines_count})"
        )

    forbidden_terms = forbidden or [
        "<tool_call",
        "</tool_call",
        "<arg_key",
        "<arg_value",
        "i'll help",
        "let me first",
        "i notice",
        "as an ai",
    ]
    norm_content = _normalize(content)
    found_forbidden = [
        term for term in forbidden_terms if _normalize(term) in norm_content
    ]
    if found_forbidden:
        return (
            False,
            f"document_quality FAIL: {effective_path} contem ruido de execucao: {found_forbidden[:5]}",
        )

    if required_terms:
        matched = [term for term in required_terms if _normalize(term) in norm_content]
        minimum = (
            min_required_terms
            if min_required_terms is not None
            else len(required_terms)
        )
        if len(matched) < minimum:
            missing = [term for term in required_terms if term not in matched]
            return False, (
                f"document_quality FAIL: {effective_path} cobre {len(matched)}/{minimum} "
                f"termos obrigatorios; faltam: {missing[:6]}"
            )

    return True, f"document_quality: {effective_path} tem {len(nonblank)} linhas uteis"


def expert_review_report_valid(
    path: str,
    project_root: str = ".",
) -> tuple[bool, str]:
    """Valida o contrato mínimo de um parecer produzido por expert de review.

    O check não tenta substituir a auditoria técnica. Ele impede os falsos
    positivos mais baratos: veredito ausente/ambíguo, APPROVED sem baseline,
    evidência vazia e aprovação que ainda declara finding bloqueante.
    """
    full = Path(project_root) / path
    if not full.is_file():
        return False, f"expert_review_report_valid FAIL: {path} nao existe"

    content = full.read_text(encoding="utf-8", errors="ignore")
    verdicts = re.findall(
        r"(?im)^\s*VERDICT\s*:\s*(APPROVED|REJECTED|BLOCKED)\s*$",
        content,
    )
    if len(verdicts) != 1:
        return False, (
            "expert_review_report_valid FAIL: informe exatamente um "
            "VERDICT: APPROVED|REJECTED|BLOCKED"
        )
    verdict = verdicts[0].upper()

    required_sections = {
        "baseline": "Baseline e escopo",
        "findings": "Findings bloqueantes",
        "evidence": "Evidências executadas",
        "limitations": "Limitações e riscos residuais",
    }
    sections: dict[str, str] = {}
    for key, heading in required_sections.items():
        section = _extract_markdown_section(content, heading)
        if section is None:
            return False, (
                f"expert_review_report_valid FAIL: secao {heading!r} ausente"
            )
        sections[key] = section.strip()

    evidence_norm = _normalize(sections["evidence"])
    placeholder_markers = [
        "nenhuma evidencia",
        "sem evidencia",
        "a preencher",
        "todo",
        "n/a",
    ]
    if verdict != "BLOCKED":
        placeholder_markers.append("nao verificado")
    evidence_is_placeholder = any(
        marker in evidence_norm for marker in placeholder_markers
    )
    has_source = bool(
        re.search(
            r"\b(fonte|path|arquivo|linha|comando|command|teste)\b", evidence_norm
        )
    )
    has_observation = bool(
        re.search(
            r"\b(observado|resultado|exit|pass|fail|aprov|reprov)\w*\b", evidence_norm
        )
    )
    mentions_prd = "prd" in evidence_norm
    if (
        len(evidence_norm) < 60
        or evidence_is_placeholder
        or not has_source
        or not has_observation
        or not mentions_prd
    ):
        return False, (
            "expert_review_report_valid FAIL: evidencias devem registrar fonte, "
            "resultado observado e relacao com o PRD"
        )

    findings_norm = _normalize(sections["findings"])
    limitations_norm = _normalize(sections["limitations"])
    if verdict in {"APPROVED", "REJECTED"} and not re.search(
        r"\b[0-9a-fA-F]{40,64}\b", sections["baseline"]
    ):
        return False, (
            "expert_review_report_valid FAIL: parecer conclusivo exige o hash "
            "completo da baseline"
        )

    if verdict == "APPROVED" and "nenhum finding bloqueante" not in findings_norm:
        return False, (
            "expert_review_report_valid FAIL: APPROVED exige declarar "
            "'Nenhum finding bloqueante.'"
        )
    if verdict == "REJECTED" and not re.search(r"\bF-\d{3}\b", sections["findings"]):
        return False, (
            "expert_review_report_valid FAIL: REJECTED exige finding F-001, F-002, ..."
        )
    if verdict == "BLOCKED" and not re.search(
        r"\b(ausent|indispon|nao verific|bloque)\w*\b", limitations_norm
    ):
        return False, (
            "expert_review_report_valid FAIL: BLOCKED exige explicar a evidencia ausente"
        )

    return True, f"expert_review_report_valid: {path} verdict={verdict}"


def api_contract_complete(
    path: str = "docs/api_contract.md",
    project_root: str = ".",
    min_endpoints: int = 3,
    require_health: bool = True,
    require_post_for_create: bool = True,
) -> tuple[bool, str]:
    """Valida que o contrato de API tem endpoints acionáveis, não só headings."""
    full = Path(project_root) / path
    if not full.exists():
        return False, f"api_contract_complete FAIL: {path} nao existe"

    content = full.read_text(encoding="utf-8", errors="ignore")
    endpoint_matches: set[tuple[str, str]] = set()
    patterns = [
        r"(?im)^\s*\|\s*`?(GET|POST|PUT|PATCH|DELETE)`?\s*\|\s*`?(/[^\s|`]*)`?",
        r"(?im)^\s*(?:\*\*)?`?(GET|POST|PUT|PATCH|DELETE)`?\s+`?(/[^\s`*:]*)(?:`|\*\*)?",
        r"(?im)^\s*[-*]\s*(?:\*\*)?`?(GET|POST|PUT|PATCH|DELETE)`?\s+`?(/[^\s`*:]*)(?:`|\*\*)?\s*:",
        r"(?im)^\s*`?(GET|POST|PUT|PATCH|DELETE)`?\s+`?(/[^\s|`:]*)`?",
        r"(?im)\b`?(GET|POST|PUT|PATCH|DELETE)`?\b\s*\|\s*`?(/[^\s|`]*)`?",
    ]
    for pattern in patterns:
        for method, endpoint in re.findall(pattern, content):
            normalized = endpoint.strip().rstrip(".,;")
            if normalized != "/":
                normalized = normalized.rstrip("/")
            endpoint_matches.add((method.upper(), normalized))

    root_methods = sorted(
        {method for method, endpoint in endpoint_matches if endpoint == "/"}
    )
    if root_methods:
        return False, (
            "api_contract_complete FAIL: endpoint '/' nao e contrato acionavel; "
            "use /health para health e paths concretos como /api/<recurso> "
            f"para produto (methods={root_methods})"
        )

    non_health = {
        (method, endpoint)
        for method, endpoint in endpoint_matches
        if endpoint != "/health"
    }
    if len(non_health) < min_endpoints:
        return False, (
            f"api_contract_complete FAIL: {path} tem {len(non_health)} endpoint(s) de produto "
            f"(min {min_endpoints})"
        )

    if require_health and not any(
        endpoint == "/health" for _, endpoint in endpoint_matches
    ):
        return False, "api_contract_complete FAIL: falta endpoint /health"

    if require_post_for_create:
        docs_text = ""
        for name in ("PRD.md", "ui_criteria.md", "task_list.md"):
            candidate = Path(project_root) / "docs" / name
            if candidate.exists():
                docs_text += "\n" + candidate.read_text(
                    encoding="utf-8", errors="ignore"
                )
        create_terms = [
            "criar",
            "cadastrar",
            "adicionar",
            "novo",
            "nova",
            "create",
            "add",
        ]
        if any(term in _normalize(docs_text) for term in create_terms) and not any(
            method == "POST" for method, _ in endpoint_matches
        ):
            return (
                False,
                "api_contract_complete FAIL: produto exige criacao mas contrato nao tem POST",
            )

    return (
        True,
        f"api_contract_complete: {len(non_health)} endpoint(s), methods={sorted({m for m, _ in endpoint_matches})}",
    )


def library_contract_complete(
    path: str = "docs/api_contract.md",
    project_root: str = ".",
    min_symbols: int = 3,
) -> tuple[bool, str]:
    """Validate an actionable public contract for a headless library/SDK."""

    full = Path(project_root) / path
    if not full.exists():
        return False, f"library_contract_complete FAIL: {path} nao existe"

    content = full.read_text(encoding="utf-8", errors="ignore")
    required_sections = {
        "API Publica": ("API Pública", "Public API"),
        "Operacoes": ("Operações", "Operations"),
        "Modelos": ("Modelos", "Models"),
        "Erros": ("Erros", "Errors"),
        "Compatibilidade": ("Compatibilidade", "Compatibility"),
    }
    missing = [
        label
        for label, aliases in required_sections.items()
        if not any(_extract_markdown_section(content, alias) for alias in aliases)
    ]
    if missing:
        return False, (
            f"library_contract_complete FAIL: {path} faltam secoes: {missing}"
        )

    expected_columns = {
        "symbol": {"symbol", "simbolo"},
        "kind": {"kind", "tipo"},
        "signature": {"signature", "assinatura"},
        "description": {"description", "descricao"},
        "errors": {"errors", "erros"},
    }
    table_lines = [
        line.strip() for line in content.splitlines() if line.strip().startswith("|")
    ]
    header_index: int | None = None
    column_indexes: dict[str, int] = {}
    for index, line in enumerate(table_lines):
        cells = [
            _normalize(cell.strip().strip("`")) for cell in line.strip("|").split("|")
        ]
        resolved: dict[str, int] = {}
        for canonical, aliases in expected_columns.items():
            for cell_index, cell in enumerate(cells):
                if cell in aliases:
                    resolved[canonical] = cell_index
                    break
        if len(resolved) == len(expected_columns):
            header_index = index
            column_indexes = resolved
            break

    if header_index is None:
        return False, (
            "library_contract_complete FAIL: falta tabela publica com colunas "
            "Symbol | Kind | Signature | Description | Errors"
        )

    symbols: set[str] = set()
    for line in table_lines[header_index + 1 :]:
        raw_cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(raw_cells) <= max(column_indexes.values()):
            continue
        if all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in raw_cells):
            continue
        symbol = raw_cells[column_indexes["symbol"]].strip("` ")
        signature = raw_cells[column_indexes["signature"]].strip("` ")
        description = raw_cells[column_indexes["description"]].strip()
        errors = raw_cells[column_indexes["errors"]].strip()
        if symbol and signature and description and errors:
            symbols.add(symbol)

    if len(symbols) < min_symbols:
        return False, (
            f"library_contract_complete FAIL: {path} tem {len(symbols)} "
            f"simbolo(s) publico(s) acionavel(is) (min {min_symbols})"
        )

    return True, (
        f"library_contract_complete: {len(symbols)} simbolo(s) publico(s) "
        "com assinatura, descricao e erros"
    )


def relative_dates_only(
    path: str = "docs/test_data.md", project_root: str = "."
) -> tuple[bool, str]:
    """Garante que massa de dados use datas relativas em vez de hardcode absoluto."""
    full = Path(project_root) / path
    if not full.exists():
        return False, f"relative_dates_only FAIL: {path} nao existe"
    content = full.read_text(encoding="utf-8", errors="ignore")
    absolute_patterns = [
        r"\b20\d{2}\s*[-‑–/]\s*\d{1,2}(?:\s*[-‑–/]\s*\d{1,2})?",
        r"\b\d{1,2}/\d{1,2}/20\d{2}\b",
    ]
    for pattern in absolute_patterns:
        match = re.search(pattern, content)
        if match:
            return (
                False,
                f"relative_dates_only FAIL: {path} contem data absoluta: {match.group(0)}",
            )
    norm = _normalize(content)
    relative_terms = ["hoje", "today", "amanha", "ontem", "d+", "d-", "semana atual"]
    if not any(term in norm for term in relative_terms):
        return False, f"relative_dates_only FAIL: {path} nao menciona datas relativas"
    return True, f"relative_dates_only: {path} usa datas relativas"


def _extract_ui_criteria(content: str) -> list[tuple[str, str]]:
    """Extrai critérios identificados do ui_criteria.md.

    O formato recomendado é uma linha por critério:
    - [ ] C01: texto do critério
    - C13: texto do critério
    - UI-02 - texto do critério
    """
    criteria: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line in content.splitlines():
        stripped = line.strip()
        match = re.match(
            r"^(?:[-*]\s*)?(?:\[[ xX]\]\s*)?([A-Z]{1,4}-?\d{1,3})\s*[:\-–]\s+(.+)$",
            stripped,
        )
        if not match:
            continue
        code = match.group(1).upper().replace("-", "")
        canonical_code = _canonical_ui_criterion_code(code)
        text = match.group(2).strip()
        if not text or canonical_code in seen:
            continue
        seen.add(canonical_code)
        criteria.append((code, text))
    return criteria


_REPORT_STATUS_MARKERS = (
    "pass",
    "ok",
    "approved",
    "aprov",
    "atendid",
    "conforme",
    "fail",
    "reprov",
    "nao atend",
    "pendente",
    "missing",
    "ausente",
)

_REPORT_STATUS_CELL_RE = re.compile(
    r"^(pass|fail|ok\b|aprovad|reprovad|approved|conforme|atendid|nao atendid|"
    r"pendente|ausente|missing)",
    re.IGNORECASE,
)


def _criterion_report_line(report: str, code: str) -> str:
    canonical_code = _canonical_ui_criterion_code(code)
    fallback = ""
    preferred = ""
    for line in report.splitlines():
        if canonical_code not in _extract_criterion_codes(line):
            continue
        normalized = _normalize(line)
        if not fallback:
            fallback = normalized
        cells = [cell.strip() for cell in normalized.strip().strip("|").split("|")]
        # Linha canônica do critério: a primeira célula é o próprio ID (linha
        # de tabela ou "C10: ..."). Títulos de seção com faixas ("C10–C16") e
        # menções cruzadas em outras linhas não devem sombrear a linha real.
        if cells and _extract_criterion_codes(cells[0]) == {canonical_code}:
            return normalized
        if not preferred:
            status_text = _criterion_report_status_text(normalized, code)
            if any(marker in status_text for marker in _REPORT_STATUS_MARKERS):
                preferred = normalized
    return preferred or fallback


def _canonical_ui_criterion_code(raw: str) -> str:
    code = raw.upper().replace("-", "")
    match = re.fullmatch(r"([A-Z]{1,4})0*(\d{1,3})", code)
    if match:
        return f"{match.group(1)}{int(match.group(2))}"
    return code


def _criterion_report_status_text(line: str, code: str) -> str:
    """Retorna a célula de status de uma linha Markdown quando houver tabela."""
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    if len(cells) < 2:
        return line

    canonical_code = _canonical_ui_criterion_code(code)
    for index, cell in enumerate(cells):
        if canonical_code in _extract_criterion_codes(cell):
            rest = cells[index + 1 :]
            # A célula de status pode não ser a imediatamente seguinte ao ID
            # (formato "| ID | descrição | PASS | evidência |"): usar a
            # primeira célula que parece um veredito.
            for candidate in rest:
                if _REPORT_STATUS_CELL_RE.match(candidate):
                    return candidate
            if rest:
                return rest[0]
            break
    return line


def _extract_criterion_codes(text: str) -> set[str]:
    return {
        _canonical_ui_criterion_code(match.group(0))
        for match in re.finditer(r"\b[A-Z]{1,4}-?\d{1,3}\b", text, re.IGNORECASE)
    }


def _source_criteria_codes(source_text: str) -> set[str]:
    """Extrai marcadores explícitos de cobertura de critério no fonte.

    Formatos aceitos:
    - data-ui-criteria="C01 C02"
    - ui-criteria: C01, C02
    """
    codes: set[str] = set()
    for match in re.finditer(
        r"data-ui-criteria\s*=\s*([\"'])(.*?)\1",
        source_text,
        re.IGNORECASE | re.DOTALL,
    ):
        codes.update(_extract_criterion_codes(match.group(2)))
    for match in re.finditer(
        r"ui-criteria\s*:\s*([^\n\r<]+)", source_text, re.IGNORECASE
    ):
        codes.update(_extract_criterion_codes(match.group(1)))
    return codes


def ui_criteria_ids(
    path: str = "docs/ui_criteria.md",
    min_count: int = 5,
    project_root: str = ".",
) -> tuple[bool, str]:
    """Verifica que ui_criteria.md existe e possui IDs estáveis de critérios."""
    criteria_file = Path(project_root) / path
    if not criteria_file.exists():
        return False, f"ui_criteria_ids FAIL: {path} nao encontrado"
    criteria = _extract_ui_criteria(
        criteria_file.read_text(encoding="utf-8", errors="ignore")
    )
    if len(criteria) < min_count:
        return False, (
            f"ui_criteria_ids FAIL: {path} tem {len(criteria)} criterios identificados "
            f"(min {min_count}); use IDs como C01, C02, UI-01"
        )
    return True, f"ui_criteria_ids: {path} tem {len(criteria)} criterios identificados"


def visual_p0_acceptance(
    path: str = "docs/visual-check-report.md",
    project_root: str = ".",
) -> tuple[bool, str]:
    """Require one explicit P0 verdict and reject criterion rows marked FAIL."""
    report = Path(project_root) / path
    if not report.is_file():
        return False, f"visual_p0_acceptance FAIL: {path} nao encontrado"

    raw = report.read_text(encoding="utf-8", errors="ignore")
    verdicts = re.findall(r"(?mi)^P0_ACCEPTANCE:\s*(PASS|FAIL)\s*$", raw)
    if verdicts != ["PASS"]:
        return False, (
            "visual_p0_acceptance FAIL: esperado exatamente "
            f"P0_ACCEPTANCE: PASS; encontrado {verdicts}"
        )

    failed_criteria: list[str] = []
    for line in raw.splitlines():
        parts = [part.strip() for part in line.strip().strip("|").split("|")]
        if len(parts) < 2 or not re.search(r"\bC\d+\b", parts[0], re.IGNORECASE):
            continue
        normalized = [re.sub(r"[*_`]", "", part).strip() for part in parts[1:]]
        if any(re.match(r"^FAIL\b", part, re.IGNORECASE) for part in normalized):
            failed_criteria.append(line.strip())
    if failed_criteria:
        return False, (
            "visual_p0_acceptance FAIL: criterios reprovados: "
            + "; ".join(failed_criteria[:8])
        )
    return True, "visual_p0_acceptance: veredito P0 PASS sem criterios reprovados"


def _ui_component_requirements(criteria_text: str) -> list[tuple[str, re.Pattern[str]]]:
    """Detecta componentes de UI comuns citados no critério.

    Isso é uma camada genérica e deliberadamente conservadora: se o critério
    mencionar um componente reconhecido, o fonte precisa conter alguma evidência
    estrutural daquele tipo. A cobertura semântica completa continua sendo do
    screenshot/visual review, mas o engine deixa de aceitar um relatório que
    ignora completamente componentes pedidos no contrato de UI.
    """
    norm = _normalize(criteria_text)
    specs = [
        (
            "FAB",
            (r"\bfab\b", r"floating action button", r"botao flutuante"),
            r"\bfab\b|floating|data-testid=[\"'][^\"']*fab",
        ),
        (
            "menu suspenso/dropdown",
            (r"menu suspenso", r"dropdown", r"\bselect\b", r"combobox"),
            r"<select\b|role=[\"']combobox|dropdown|menu-suspenso|data-testid=[\"'][^\"']*(select|dropdown|menu)",
        ),
        (
            "modal/dialog",
            (r"\bmodal\b", r"\bdialog\b", r"dialogo"),
            r"<dialog\b|role=[\"']dialog|modal",
        ),
        (
            "tabs/abas",
            (r"\btabs?\b", r"\babas?\b", r"tablist"),
            r"role=[\"']tab|role=[\"']tablist|\btabs?\b|\btablist\b",
        ),
        (
            "toggle/switch",
            (r"\btoggle\b", r"\bswitch\b", r"alternador"),
            r"role=[\"']switch|type=[\"']checkbox|toggle|switch",
        ),
        (
            "checkbox",
            (r"checkbox", r"caixa de selecao"),
            r"type=[\"']checkbox|checkbox",
        ),
        ("radio", (r"\bradio\b", r"opcao unica"), r"type=[\"']radio|\bradio\b"),
        (
            "slider",
            (r"\bslider\b", r"controle deslizante"),
            r"type=[\"']range|role=[\"']slider|\bslider\b",
        ),
        ("tooltip", (r"\btooltip\b", r"dica de contexto"), r"tooltip|aria-describedby"),
        ("ícone SVG", (r"icone svg", r"icones svg", r"svg"), r"<svg\b|\.svg\b"),
        (
            "estado vazio",
            (r"estado vazio", r"empty state"),
            r"estado vazio|empty state|\bvazio\b|\bempty\b",
        ),
    ]
    requirements: list[tuple[str, re.Pattern[str]]] = []
    for label, triggers, source_pattern in specs:
        if any(re.search(trigger, norm) for trigger in triggers):
            requirements.append((label, re.compile(source_pattern, re.IGNORECASE)))
    return requirements


def ui_criteria_coverage(
    criteria_path: str = "docs/ui_criteria.md",
    report_path: str | None = "docs/screenshot-review.md",
    source_dir: str | None = None,
    evidence: str = "any",
    exclude_pattern: str | None = None,
    project_root: str = ".",
) -> tuple[bool, str]:
    """Verifica cobertura genérica dos critérios de interface.

    Regras:
    - `docs/ui_criteria.md` deve ter critérios identificáveis (`C01:`, `UI-02:`).
    - `evidence=report`: o relatório deve citar cada ID com PASS/OK/APROVADO/CONFORME.
    - `evidence=code`: o fonte deve marcar cada ID com `data-ui-criteria` ou
      comentário `ui-criteria:`.
    - `evidence=both`: exige relatório e código para cada critério.
    - `evidence=any`: aceita relatório ou código para cada critério.
    - Quando código é usado como evidência, componentes comuns citados no
      critério precisam ter evidência estrutural no fonte.
    """
    root = Path(project_root)
    criteria_file = root / criteria_path
    if not criteria_file.exists():
        return False, f"ui_criteria_coverage FAIL: {criteria_path} nao encontrado"

    criteria = _extract_ui_criteria(
        criteria_file.read_text(encoding="utf-8", errors="ignore")
    )
    if exclude_pattern:
        try:
            excluded = re.compile(exclude_pattern, re.IGNORECASE)
        except re.error as exc:
            return False, f"ui_criteria_coverage FAIL: exclude_pattern inválido: {exc}"
        criteria = [item for item in criteria if not excluded.search(item[1])]
    if not criteria:
        return False, (
            "ui_criteria_coverage FAIL: nenhum criterio identificado em "
            f"{criteria_path}; use IDs como C01, C02, UI-01"
        )

    mode = _normalize(evidence or "any").strip()
    if mode not in {"any", "report", "code", "both"}:
        return (
            False,
            "ui_criteria_coverage FAIL: evidence deve ser any, report, code ou both",
        )

    needs_report = mode in {"report", "both"}
    needs_code = mode in {"code", "both"}
    if needs_report and not report_path:
        return False, "ui_criteria_coverage FAIL: evidence exige report_path"
    if needs_code and not source_dir:
        return False, "ui_criteria_coverage FAIL: evidence exige source_dir"
    if mode == "any" and not report_path and not source_dir:
        return False, "ui_criteria_coverage FAIL: informe report_path ou source_dir"

    report = ""
    report_available = False
    if report_path:
        report_file = root / report_path
        if report_file.exists():
            report = report_file.read_text(encoding="utf-8", errors="ignore")
            report_available = True
        elif needs_report or (mode == "any" and not source_dir):
            return False, f"ui_criteria_coverage FAIL: {report_path} nao encontrado"

    source_text = ""
    source_codes: set[str] = set()
    source_available = False
    if source_dir:
        source_root = root / source_dir
        if not source_root.exists():
            return (
                False,
                f"ui_criteria_coverage FAIL: source_dir {source_dir} nao encontrado",
            )
        source_text = "\n".join(
            p.read_text(encoding="utf-8", errors="ignore")
            for p in source_root.rglob("*")
            if p.is_file()
        )
        source_codes = _source_criteria_codes(source_text)
        source_available = True

    pass_markers = ("pass", "ok", "approved", "aprov", "atendid", "conforme")
    fail_markers = ("fail", "reprov", "nao atend", "pendente", "missing", "ausente")

    def report_status(code: str) -> tuple[bool, str]:
        if not report_available:
            return False, "relatorio ausente"
        line = _criterion_report_line(report, code)
        if not line:
            return False, "sem linha no relatorio"
        status_text = _criterion_report_status_text(line, code)
        if any(marker in status_text for marker in fail_markers):
            return False, "linha do relatorio indica falha"
        if not any(marker in status_text for marker in pass_markers):
            return False, "linha do relatorio sem PASS/OK"
        return True, "relatorio"

    def code_status(code: str, text: str) -> tuple[bool, str]:
        if not source_available:
            return False, "fonte ausente"
        if _canonical_ui_criterion_code(code) not in source_codes:
            return False, "codigo sem marcador data-ui-criteria/ui-criteria"
        missing_components = [
            label
            for label, pattern in _ui_component_requirements(text)
            if not pattern.search(source_text)
        ]
        if missing_components:
            return False, "componentes sem evidencia no fonte: " + ", ".join(
                missing_components
            )
        return True, "codigo"

    failures: list[str] = []
    report_count = 0
    code_count = 0
    for code, text in criteria:
        report_ok, report_reason = report_status(code)
        code_ok, code_reason = code_status(code, text)

        if mode == "report":
            covered = report_ok
        elif mode == "code":
            covered = code_ok
        elif mode == "both":
            covered = report_ok and code_ok
        else:
            covered = report_ok or code_ok

        if covered:
            report_count += int(report_ok)
            code_count += int(code_ok)
            continue

        reasons: list[str] = []
        if mode in {"any", "report", "both"}:
            reasons.append(f"relatorio: {report_reason}")
        if mode in {"any", "code", "both"}:
            reasons.append(f"codigo: {code_reason}")
        failures.append(f"{code} ({'; '.join(reasons)})")

    if failures:
        return False, "ui_criteria_coverage FAIL: " + "; ".join(failures)
    return (
        True,
        "ui_criteria_coverage: "
        f"{len(criteria)} criterios cobertos "
        f"(relatorio={report_count}, codigo={code_count}, evidence={mode})",
    )


def min_user_stories(path: str, n: int, project_root: str = ".") -> tuple[bool, str]:
    """Conta user stories nos formatos: ### US- / ## US- / **US- / US-XX."""
    full = Path(project_root) / path
    if not full.exists():
        return False, f"min_user_stories FAIL: {path} nao existe"
    content = full.read_text()
    count = len(
        re.findall(
            r"(?:###?\s+US[-\s]|\*\*US-|^US-\d)", content, re.IGNORECASE | re.MULTILINE
        )
    )
    if count >= n:
        return True, f"min_user_stories: {path} tem {count} user stories (min {n})"
    return False, f"min_user_stories FAIL: {path} tem {count} user stories (min {n})"


def tests_pass(project_root: str = ".") -> tuple[bool, str]:
    """Roda pytest e verifica se passa."""
    result = subprocess.run(
        ["python", "-m", "pytest", "--tb=short", "-q"],
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode == 0:
        # Extrair contagem de testes
        last_line = (
            result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
        )
        return True, f"tests_pass: {last_line}"
    # Extrair resumo de falhas
    last_lines = (
        result.stdout.strip().splitlines()[-3:] if result.stdout.strip() else []
    )
    summary = " | ".join(last_lines)
    return False, f"tests_pass FAIL: {summary}"


def tests_fail(project_root: str = ".") -> tuple[bool, str]:
    """Verifica que testes FALHAM (TDD red phase)."""
    result = subprocess.run(
        ["python", "-m", "pytest", "--tb=short", "-q"],
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        return True, "tests_fail: testes falharam como esperado (red phase)"
    return False, "tests_fail FAIL: testes passaram — deviam falhar na red phase"


def _is_docstring_stmt(stmt: ast.stmt) -> bool:
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )


def _meaningful_test_body(body: list[ast.stmt]) -> list[ast.stmt]:
    meaningful: list[ast.stmt] = []
    for stmt in body:
        if _is_docstring_stmt(stmt):
            continue
        if isinstance(stmt, ast.Pass):
            continue
        if (
            isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Constant)
            and stmt.value.value is Ellipsis
        ):
            continue
        meaningful.append(stmt)
    return meaningful


def _is_pytest_raises_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "raises"
        and isinstance(func.value, ast.Name)
        and func.value.id == "pytest"
    )


def _test_assertion_count(func: ast.AST) -> int:
    count = 0
    for node in ast.walk(func):
        if isinstance(node, ast.Assert):
            count += 1
        elif _is_pytest_raises_call(node):
            count += 1
    return count


def pytest_red_quality(
    tests_dir: str = "project/tests",
    min_test_files: int = 1,
    min_tests: int = 3,
    min_assertions: int = 3,
    project_root: str = ".",
) -> tuple[bool, str]:
    """Valida qualidade estrutural de testes RED sem exigir que eles passem.

    O objetivo é bloquear stubs que apenas satisfazem existência/compilação:
    arquivos vazios, extensões erradas, funções `pass` e testes sem asserts.
    """
    root = Path(project_root)
    test_root = root / tests_dir
    if not test_root.exists():
        return False, f"pytest_red_quality FAIL: {tests_dir} nao encontrado"
    files = sorted(p for p in test_root.rglob("test_*.py") if p.is_file())
    if len(files) < min_test_files:
        return False, (
            f"pytest_red_quality FAIL: {len(files)} test_*.py encontrado(s) "
            f"(min {min_test_files})"
        )

    test_count = 0
    assertion_count = 0
    stub_tests: list[str] = []
    syntax_errors: list[str] = []
    for path in files:
        try:
            tree = ast.parse(
                path.read_text(encoding="utf-8", errors="ignore"), filename=str(path)
            )
        except SyntaxError as exc:
            syntax_errors.append(f"{path.relative_to(root)}:{exc.lineno}: {exc.msg}")
            continue

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test_"):
                continue
            test_count += 1
            assertion_count += _test_assertion_count(node)
            body = _meaningful_test_body(node.body)
            if not body:
                stub_tests.append(f"{path.relative_to(root)}::{node.name}")
                continue
            if len(body) == 1 and isinstance(body[0], ast.Return):
                value = body[0].value
                if isinstance(value, ast.Constant) and value.value in {True, None}:
                    stub_tests.append(f"{path.relative_to(root)}::{node.name}")

    if syntax_errors:
        return False, "pytest_red_quality FAIL: syntax error: " + "; ".join(
            syntax_errors[:5]
        )
    if test_count < min_tests:
        return (
            False,
            f"pytest_red_quality FAIL: {test_count} teste(s) encontrado(s) (min {min_tests})",
        )
    if assertion_count < min_assertions:
        return False, (
            f"pytest_red_quality FAIL: {assertion_count} assert/pytest.raises encontrado(s) "
            f"(min {min_assertions})"
        )
    if stub_tests:
        return False, "pytest_red_quality FAIL: testes stub/pass-only: " + "; ".join(
            stub_tests[:5]
        )
    return (
        True,
        f"pytest_red_quality: {len(files)} arquivo(s), {test_count} teste(s), "
        f"{assertion_count} assert/pytest.raises",
    )


def coverage_min(min_pct: int, project_root: str = ".") -> tuple[bool, str]:
    """Roda pytest com coverage e verifica minimo."""
    result = subprocess.run(
        ["python", "-m", "pytest", "--cov=src", "--cov-report=term-missing", "-q"],
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=120,
    )
    # Procurar linha TOTAL no output
    for line in result.stdout.splitlines():
        if "TOTAL" in line:
            match = re.search(r"(\d+)%", line)
            if match:
                pct = int(match.group(1))
                if pct >= min_pct:
                    return True, f"coverage_min: {pct}% (min {min_pct}%)"
                return False, f"coverage_min FAIL: {pct}% < {min_pct}%"
    return False, "coverage_min FAIL: nao consegui extrair cobertura do output"


def read_artifact(
    path: str, key: str, pattern: str, project_root: str = "."
) -> tuple[bool, str]:
    """Le arquivo e extrai valor via regex. Detail tem formato 'read_artifact: key=value'."""
    import re as _re

    full = Path(project_root) / path
    if not full.exists():
        return False, f"read_artifact FAIL: {path} nao encontrado"
    content = full.read_text()
    match = _re.search(pattern, content, _re.IGNORECASE | _re.MULTILINE)
    if not match:
        return False, f"read_artifact FAIL: padrao nao encontrado em {path}"
    value = match.group(1).strip().lower()
    return True, f"read_artifact: {key}={value}"


def sections_unchanged(
    path: str,
    snapshot_path: str,
    sections: list[str],
    project_root: str = ".",
) -> tuple[bool, str]:
    """Garante que seções críticas permaneçam idênticas ao baseline."""
    current_full = Path(project_root) / path
    snapshot_full = Path(project_root) / snapshot_path

    if not current_full.exists():
        return False, f"sections_unchanged FAIL: {path} nao encontrado"
    if not snapshot_full.exists():
        return False, f"sections_unchanged FAIL: baseline ausente em {snapshot_path}"

    current_content = current_full.read_text()
    snapshot_content = snapshot_full.read_text()
    changed: list[str] = []

    for section in sections:
        current_section = _extract_markdown_section(current_content, section)
        snapshot_section = _extract_markdown_section(snapshot_content, section)

        if snapshot_section is None:
            return False, (
                f"sections_unchanged FAIL: secao '{section}' ausente no baseline {snapshot_path}"
            )
        if current_section is None:
            return (
                False,
                f"sections_unchanged FAIL: secao '{section}' ausente em {path}",
            )

        if _normalize_block(current_section) != _normalize_block(snapshot_section):
            changed.append(section)

    if changed:
        return False, (
            "sections_unchanged FAIL: secoes imutaveis alteradas sem aprovacao do stakeholder: "
            f"{changed}"
        )

    return True, (f"sections_unchanged: secoes preservadas ({', '.join(sections)})")


_BACKLOG_ID_RE = re.compile(r"\b(?:PB|BL|US|DV)-\d+[A-Z]?\b", re.IGNORECASE)
_BACKLOG_PRIORITIES = {"P0", "P1", "P2"}
_BACKLOG_STATUSES = {
    "planned",
    "ready",
    "in_progress",
    "done",
    "deferred",
    "blocked",
    "rejected",
    "accepted",
}
_BACKLOG_UNDECIDED_OPEN = {"planned", "ready", "in_progress"}


def _normalize_header(value: str) -> str:
    value = _normalize(value)
    value = re.sub(r"[^a-z0-9]+", "_", value).strip("_")
    return value


def _markdown_table_records(content: str) -> list[dict[str, str]]:
    """Parse simple Markdown tables into dictionaries."""
    records: list[dict[str, str]] = []
    header: list[str] | None = None
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not (line.startswith("|") and line.endswith("|")):
            header = None
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if not cells:
            continue
        if all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells):
            continue
        if header is None:
            header = [_normalize_header(cell) for cell in cells]
            continue
        row = {
            header[idx]: cells[idx]
            for idx in range(min(len(header), len(cells)))
            if header[idx]
        }
        records.append(row)
    return records


def _row_value(row: dict[str, str], *names: str) -> str:
    normalized = {_normalize_header(name) for name in names}
    for key, value in row.items():
        if key in normalized:
            return value.strip()
    for key, value in row.items():
        if any(name in key for name in normalized):
            return value.strip()
    return ""


def _backlog_rows(content: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for row in _markdown_table_records(content):
        raw_id = _row_value(row, "id", "item", "backlog")
        match = _BACKLOG_ID_RE.search(raw_id)
        if not match:
            joined = " | ".join(row.values())
            match = _BACKLOG_ID_RE.search(joined)
        if not match:
            continue
        normalized = dict(row)
        normalized["_id"] = match.group(0).upper()
        normalized["_priority"] = _row_value(row, "prioridade", "priority").upper()
        normalized["_status"] = _normalize(_row_value(row, "status", "estado")).replace(
            "-", "_"
        )
        normalized["_decision"] = _row_value(
            row,
            "decisao",
            "decisão",
            "notas",
            "nota",
            "motivo",
            "racional",
        )
        rows.append(normalized)
    return rows


def project_backlog_summary(
    path: str = "docs/PROJECT_BACKLOG.md",
    project_root: str = ".",
) -> dict[str, object]:
    """Return deterministic counters for PROJECT_BACKLOG.md."""
    backlog_file = Path(project_root) / path
    rows = (
        _backlog_rows(backlog_file.read_text(encoding="utf-8", errors="ignore"))
        if backlog_file.exists()
        else []
    )
    by_status: dict[str, int] = {}
    by_priority: dict[str, int] = {}
    undecided_p0_p1: list[str] = []
    for row in rows:
        status = row.get("_status", "")
        priority = row.get("_priority", "")
        by_status[status] = by_status.get(status, 0) + 1
        by_priority[priority] = by_priority.get(priority, 0) + 1
        decision = row.get("_decision", "").strip(" -—")
        if priority in {"P0", "P1"}:
            if status in _BACKLOG_UNDECIDED_OPEN:
                undecided_p0_p1.append(row["_id"])
            elif status in {"blocked", "deferred"} and not decision:
                undecided_p0_p1.append(row["_id"])
    return {
        "total": len(rows),
        "by_status": by_status,
        "by_priority": by_priority,
        "undecided_p0_p1": undecided_p0_p1,
    }


def project_backlog_valid(
    path: str = "docs/PROJECT_BACKLOG.md",
    min_items: int = 1,
    project_root: str = ".",
) -> tuple[bool, str]:
    """Validate the canonical product backlog shape."""
    backlog_file = Path(project_root) / path
    if not backlog_file.exists():
        return False, f"project_backlog_valid FAIL: {path} nao encontrado"
    rows = _backlog_rows(backlog_file.read_text(encoding="utf-8", errors="ignore"))
    if len(rows) < min_items:
        return False, (
            f"project_backlog_valid FAIL: {path} tem {len(rows)} item(ns), "
            f"min {min_items}"
        )
    ids = [row["_id"] for row in rows]
    duplicated = sorted({item for item in ids if ids.count(item) > 1})
    if duplicated:
        return (
            False,
            f"project_backlog_valid FAIL: IDs duplicados: {', '.join(duplicated)}",
        )
    bad_priority = [
        row["_id"] for row in rows if row.get("_priority") not in _BACKLOG_PRIORITIES
    ]
    if bad_priority:
        return (
            False,
            f"project_backlog_valid FAIL: prioridade invalida em {', '.join(bad_priority[:8])}",
        )
    bad_status = [
        row["_id"] for row in rows if row.get("_status") not in _BACKLOG_STATUSES
    ]
    if bad_status:
        return (
            False,
            f"project_backlog_valid FAIL: status invalido em {', '.join(bad_status[:8])}",
        )
    return True, f"project_backlog_valid: {len(rows)} item(ns) validos em {path}"


def project_contract_valid(
    path: str = ".ft/project.yml",
    project_root: str = ".",
) -> tuple[bool, str]:
    """Validate the versioned project objective and global Definition of Done."""
    from ft.project.lifecycle import (
        ProjectContractError,
        read_project_contract,
        validate_project_contract,
    )

    root = Path(project_root).resolve()
    full = root / path
    if not full.is_file() or full.is_symlink():
        return False, f"project_contract_valid FAIL: {path} nao encontrado ou inseguro"
    try:
        if Path(path).as_posix() == ".ft/project.yml":
            contract = read_project_contract(root)
        else:
            payload = yaml.safe_load(full.read_text(encoding="utf-8")) or {}
            contract = validate_project_contract(payload, path=full)
    except (OSError, UnicodeError, yaml.YAMLError, ProjectContractError) as exc:
        return False, f"project_contract_valid FAIL: {exc}"
    if contract is None:
        return False, f"project_contract_valid FAIL: {path} ausente"
    if contract["lifecycle"]["phase"] != "building":
        return False, (
            "project_contract_valid FAIL: processo construtor exige phase=building"
        )
    return True, (
        "project_contract_valid: objetivo e DoD globais válidos "
        f"para {contract['project_id']}"
    )


def task_list_references_backlog(
    task_path: str = "docs/task_list.md",
    backlog_path: str = "docs/PROJECT_BACKLOG.md",
    min_refs: int = 1,
    project_root: str = ".",
) -> tuple[bool, str]:
    """Ensure cycle task list is derived from the canonical backlog."""
    root = Path(project_root)
    task_file = root / task_path
    backlog_file = root / backlog_path
    if not task_file.exists():
        return False, f"task_list_references_backlog FAIL: {task_path} nao encontrado"
    if not backlog_file.exists():
        return (
            False,
            f"task_list_references_backlog FAIL: {backlog_path} nao encontrado",
        )
    backlog_rows = _backlog_rows(
        backlog_file.read_text(encoding="utf-8", errors="ignore")
    )
    backlog_ids = {row["_id"] for row in backlog_rows}
    if not backlog_ids:
        return (
            False,
            f"task_list_references_backlog FAIL: nenhum ID de backlog em {backlog_path}",
        )
    text = task_file.read_text(encoding="utf-8", errors="ignore").upper()
    referenced = sorted(
        {match.group(0).upper() for match in _BACKLOG_ID_RE.finditer(text)}
        & backlog_ids
    )
    if len(referenced) < min_refs:
        return False, (
            f"task_list_references_backlog FAIL: {task_path} referencia "
            f"{len(referenced)} item(ns) de backlog, min {min_refs}"
        )
    return (
        True,
        f"task_list_references_backlog: {len(referenced)} item(ns) referenciados",
    )


def backlog_pending_decisions(
    path: str = "docs/PROJECT_BACKLOG.md",
    project_root: str = ".",
) -> tuple[bool, str]:
    """Block P0/P1 backlog items that remain open without explicit decision."""
    backlog_file = Path(project_root) / path
    if not backlog_file.exists():
        return True, f"backlog_pending_decisions: {path} nao existe — pulando"
    summary = project_backlog_summary(path=path, project_root=project_root)
    undecided = summary.get("undecided_p0_p1", [])
    if undecided:
        return False, (
            "backlog_pending_decisions FAIL: P0/P1 sem decisao explicita: "
            + ", ".join(undecided[:12])
        )
    return True, "backlog_pending_decisions: nenhum P0/P1 aberto sem decisao"


def backlog_referenced_decisions(
    references_path: str,
    backlog_path: str = "docs/PROJECT_BACKLOG.md",
    reference_field: str | None = None,
    required_count: int | None = None,
    accepted_statuses: list[str] | tuple[str, ...] | set[str] | None = None,
    project_root: str = ".",
) -> tuple[bool, str]:
    """Validate only backlog items selected by one cycle artifact."""
    root = Path(project_root)
    relative = Path(references_path)
    if relative.is_absolute() or ".." in relative.parts:
        return (
            False,
            f"backlog_referenced_decisions FAIL: path inseguro: {references_path}",
        )
    reference_file = root / relative
    backlog_file = root / backlog_path
    try:
        reference_file.resolve().relative_to(root.resolve())
        backlog_file.resolve().relative_to(root.resolve())
    except ValueError:
        return (
            False,
            "backlog_referenced_decisions FAIL: artefato escapa da raiz do projeto",
        )
    if not reference_file.is_file():
        return False, (
            f"backlog_referenced_decisions FAIL: {references_path} nao encontrado"
        )
    if not backlog_file.is_file():
        return (
            False,
            f"backlog_referenced_decisions FAIL: {backlog_path} nao encontrado",
        )

    reference_text = reference_file.read_text(encoding="utf-8", errors="ignore")
    if reference_field:
        match = re.search(
            rf"(?im)^\s*{re.escape(reference_field)}\s*:\s*[\"']?"
            rf"({_FEATURE_BACKLOG_ID_RE.pattern})[\"']?\s*$",
            reference_text,
        )
        referenced = [match.group(1).upper()] if match else []
    else:
        referenced = sorted(
            {
                match.group(0).upper()
                for match in _FEATURE_BACKLOG_ID_RE.finditer(reference_text)
            }
        )
    if not referenced:
        field_hint = f" no campo {reference_field}" if reference_field else ""
        return False, (
            "backlog_referenced_decisions FAIL: nenhuma referencia PB-*"
            f"{field_hint} em {references_path}"
        )
    if required_count is not None and len(referenced) != int(required_count):
        return False, (
            "backlog_referenced_decisions FAIL: "
            f"esperado {required_count} PB(s), encontrado {len(referenced)}"
        )

    rows = _backlog_rows(backlog_file.read_text(encoding="utf-8", errors="ignore"))
    by_id = {str(row["_id"]): row for row in rows}
    unknown = sorted(set(referenced) - set(by_id))
    if unknown:
        return False, (
            "backlog_referenced_decisions FAIL: PBs desconhecidos: "
            + ", ".join(unknown)
        )
    raw_statuses = accepted_statuses or {"done", "accepted"}
    if isinstance(raw_statuses, str):
        raw_statuses = [raw_statuses]
    allowed = {_normalize(str(status)).replace("-", "_") for status in raw_statuses}
    unfinished = sorted(
        backlog_id
        for backlog_id in referenced
        if str(by_id[backlog_id].get("_status", "")) not in allowed
    )
    if unfinished:
        return False, (
            "backlog_referenced_decisions FAIL: PBs selecionados nao concluidos: "
            + ", ".join(unfinished)
        )
    return True, (
        "backlog_referenced_decisions: " + ", ".join(referenced) + " concluido(s)"
    )


_FEATURE_ID_RE = re.compile(r"\bFEAT-\d{3}\b", re.IGNORECASE)
_FEATURE_BACKLOG_ID_RE = re.compile(r"\bPB-\d+[A-Z]?\b", re.IGNORECASE)
_FEATURE_STATUSES = {"active", "deprecated", "removed"}
_FEATURE_TABLE_HEADERS = (
    "id",
    "status",
    "backlog",
    "titulo",
    "descricao",
    "entregue_em",
    "evidencia",
    "ultima_evolucao",
    "notas",
)
_DEFAULT_FEATURE_TYPES = {"us", "feature", "recurso", "story"}
_EMPTY_FEATURE_VALUES = {"", "-", "—", "–", "n/a", "na", "none", "null", "tbd", "todo"}


def _markdown_table_cells(line: str) -> list[str] | None:
    stripped = line.strip()
    if not (stripped.startswith("|") and stripped.endswith("|")):
        return None
    return [cell.strip() for cell in stripped.strip("|").split("|")]


def _feature_table_records(content: str) -> tuple[list[dict[str, str]], str | None]:
    """Return rows from the exact FEATURES table and an optional schema error."""
    lines = content.splitlines()
    for index, line in enumerate(lines):
        cells = _markdown_table_cells(line)
        if cells is None:
            continue
        headers = tuple(_normalize_header(cell) for cell in cells)
        if headers != _FEATURE_TABLE_HEADERS:
            continue
        if index + 1 >= len(lines):
            return [], "separador da tabela ausente"
        separator = _markdown_table_cells(lines[index + 1])
        if (
            separator is None
            or len(separator) != len(_FEATURE_TABLE_HEADERS)
            or not all(
                re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in separator
            )
        ):
            return [], "separador da tabela invalido"

        records: list[dict[str, str]] = []
        for row_number, raw_line in enumerate(lines[index + 2 :], start=index + 3):
            row_cells = _markdown_table_cells(raw_line)
            if row_cells is None:
                break
            if len(row_cells) != len(_FEATURE_TABLE_HEADERS):
                return (
                    [],
                    f"linha {row_number} tem {len(row_cells)} coluna(s), esperado 9",
                )
            records.append(dict(zip(_FEATURE_TABLE_HEADERS, row_cells)))
        return records, None
    return [], (
        "tabela obrigatoria ausente; esperado: "
        "ID | Status | Backlog | Título | Descrição | Entregue em | "
        "Evidência | Última evolução | Notas"
    )


def _feature_rows(content: str) -> tuple[list[dict[str, object]], str | None]:
    records, schema_error = _feature_table_records(content)
    rows: list[dict[str, object]] = []
    for record in records:
        raw_id = record.get("id", "").strip().strip("`")
        id_match = _FEATURE_ID_RE.fullmatch(raw_id)
        backlog_ids = sorted(
            {
                match.group(0).upper()
                for match in _FEATURE_BACKLOG_ID_RE.finditer(record.get("backlog", ""))
            }
        )
        normalized: dict[str, object] = dict(record)
        normalized["_id"] = id_match.group(0).upper() if id_match else ""
        normalized["_raw_id"] = raw_id
        normalized["_status"] = (
            _normalize(record.get("status", "")).replace("-", "_").strip()
        )
        normalized["_backlog_ids"] = backlog_ids
        rows.append(normalized)
    return rows, schema_error


def _feature_value_filled(value: object) -> bool:
    normalized = _normalize(str(value)).strip()
    return normalized not in _EMPTY_FEATURE_VALUES


def features_summary(
    path: str = "docs/FEATURES.md",
    project_root: str = ".",
) -> dict[str, object]:
    """Return deterministic counters for the canonical feature catalogue."""
    feature_file = Path(project_root) / path
    if not feature_file.exists():
        return {"total": 0, "by_status": {}, "backlog_ids": []}
    rows, _ = _feature_rows(feature_file.read_text(encoding="utf-8", errors="ignore"))
    by_status: dict[str, int] = {}
    backlog_ids: set[str] = set()
    for row in rows:
        status = str(row.get("_status", ""))
        by_status[status] = by_status.get(status, 0) + 1
        backlog_ids.update(str(item) for item in row.get("_backlog_ids", []))
    return {
        "total": len(rows),
        "by_status": by_status,
        "backlog_ids": sorted(backlog_ids),
    }


def features_catalog_valid(
    path: str = "docs/FEATURES.md",
    backlog_path: str = "docs/PROJECT_BACKLOG.md",
    min_items: int = 0,
    project_root: str = ".",
) -> tuple[bool, str]:
    """Validate FEATURES schema and references to implemented backlog items."""
    root = Path(project_root)
    feature_file = root / path
    backlog_file = root / backlog_path
    if not feature_file.exists():
        return False, f"features_catalog_valid FAIL: {path} nao encontrado"
    if not backlog_file.exists():
        return False, f"features_catalog_valid FAIL: {backlog_path} nao encontrado"

    rows, schema_error = _feature_rows(
        feature_file.read_text(encoding="utf-8", errors="ignore")
    )
    if schema_error:
        return (
            False,
            f"features_catalog_valid FAIL: schema invalido em {path}: {schema_error}",
        )
    if len(rows) < min_items:
        return False, (
            f"features_catalog_valid FAIL: {path} tem {len(rows)} item(ns), "
            f"min {min_items}"
        )

    invalid_ids = [
        str(row.get("_raw_id") or "<vazio>") for row in rows if not row.get("_id")
    ]
    if invalid_ids:
        return False, "features_catalog_valid FAIL: IDs invalidos: " + ", ".join(
            invalid_ids[:8]
        )
    ids = [str(row["_id"]) for row in rows]
    duplicated = sorted({item for item in ids if ids.count(item) > 1})
    if duplicated:
        return (
            False,
            f"features_catalog_valid FAIL: IDs duplicados: {', '.join(duplicated)}",
        )

    bad_status = [
        str(row["_id"]) for row in rows if row.get("_status") not in _FEATURE_STATUSES
    ]
    if bad_status:
        return (
            False,
            f"features_catalog_valid FAIL: status invalido em {', '.join(bad_status[:8])}",
        )

    required_fields = {
        "titulo": "Título",
        "descricao": "Descrição",
        "entregue_em": "Entregue em",
        "evidencia": "Evidência",
    }
    missing_fields: list[str] = []
    for row in rows:
        for field, label in required_fields.items():
            if not _feature_value_filled(row.get(field, "")):
                missing_fields.append(f"{row['_id']}:{label}")
    if missing_fields:
        return False, (
            "features_catalog_valid FAIL: campos obrigatorios vazios: "
            + ", ".join(missing_fields[:12])
        )

    without_backlog = [str(row["_id"]) for row in rows if not row.get("_backlog_ids")]
    if without_backlog:
        return False, (
            "features_catalog_valid FAIL: feature sem referencia PB: "
            + ", ".join(without_backlog[:8])
        )

    backlog_rows = _backlog_rows(
        backlog_file.read_text(encoding="utf-8", errors="ignore")
    )
    backlog_by_id = {
        str(row["_id"]): row
        for row in backlog_rows
        if str(row["_id"]).startswith("PB-")
    }
    referenced = {
        str(backlog_id) for row in rows for backlog_id in row.get("_backlog_ids", [])
    }
    unknown = sorted(referenced - set(backlog_by_id))
    if unknown:
        return (
            False,
            f"features_catalog_valid FAIL: PBs desconhecidos: {', '.join(unknown[:12])}",
        )
    not_implemented = sorted(
        backlog_id
        for backlog_id in referenced
        if backlog_by_id[backlog_id].get("_status") not in {"done", "accepted"}
    )
    if not_implemented:
        return False, (
            "features_catalog_valid FAIL: PBs ainda nao implementados: "
            + ", ".join(not_implemented[:12])
        )
    return True, f"features_catalog_valid: {len(rows)} feature(s) valida(s) em {path}"


def implemented_backlog_covered_by_features(
    features_path: str = "docs/FEATURES.md",
    backlog_path: str = "docs/PROJECT_BACKLOG.md",
    feature_types: list[str] | tuple[str, ...] | set[str] | str | None = None,
    project_root: str = ".",
) -> tuple[bool, str]:
    """Ensure delivered product backlog items are represented in FEATURES."""
    root = Path(project_root)
    feature_file = root / features_path
    backlog_file = root / backlog_path
    if not feature_file.exists():
        return (
            False,
            f"implemented_backlog_covered_by_features FAIL: {features_path} nao encontrado",
        )
    if not backlog_file.exists():
        return (
            False,
            f"implemented_backlog_covered_by_features FAIL: {backlog_path} nao encontrado",
        )

    feature_rows, schema_error = _feature_rows(
        feature_file.read_text(encoding="utf-8", errors="ignore")
    )
    if schema_error:
        return False, (
            "implemented_backlog_covered_by_features FAIL: "
            f"schema invalido em {features_path}: {schema_error}"
        )
    covered = {
        str(backlog_id)
        for row in feature_rows
        for backlog_id in row.get("_backlog_ids", [])
    }

    if feature_types is None:
        allowed_types = set(_DEFAULT_FEATURE_TYPES)
    else:
        raw_types = [feature_types] if isinstance(feature_types, str) else feature_types
        allowed_types = {_normalize(str(value)).strip() for value in raw_types}

    backlog_rows = _backlog_rows(
        backlog_file.read_text(encoding="utf-8", errors="ignore")
    )
    implemented = {
        str(row["_id"])
        for row in backlog_rows
        if str(row["_id"]).startswith("PB-")
        and row.get("_status") in {"done", "accepted"}
        and _normalize(_row_value(row, "tipo", "type")).strip() in allowed_types
    }
    missing = sorted(implemented - covered)
    if missing:
        return False, (
            "implemented_backlog_covered_by_features FAIL: PBs entregues sem feature: "
            + ", ".join(missing[:12])
        )
    return True, (
        "implemented_backlog_covered_by_features: "
        f"{len(implemented)} PB(s) implementado(s) coberto(s)"
    )


def process_improvements_classified(
    path: str = "docs/process-improvements.yml",
    report_path: str = "docs/process-improvements.md",
    require_pending_global: bool = True,
    project_root: str = ".",
) -> tuple[bool, str]:
    """Validate the structured local/global process-improvement decision."""
    from ft.engine.process_improvements import (
        ProcessImprovementError,
        load_process_improvement_review,
    )

    try:
        review = load_process_improvement_review(
            project_root,
            path=path,
            report_path=report_path,
        )
    except ProcessImprovementError as exc:
        return False, f"process_improvements_classified FAIL: {exc}"

    if require_pending_global:
        self_resolved = [
            item.improvement_id
            for item in review.global_candidates
            if item.status != "pending"
        ]
        if self_resolved:
            return False, (
                "process_improvements_classified FAIL: o ciclo nao pode resolver "
                "sua propria promocao global: " + ", ".join(self_resolved)
            )

    local_count = sum(
        1 for item in review.improvements if item.get("classification") == "local"
    )
    rejected_count = sum(
        1 for item in review.improvements if item.get("classification") == "rejected"
    )
    return True, (
        "process_improvements_classified: "
        f"{len(review.improvements)} achado(s), {local_count} local(is), "
        f"{len(review.global_candidates)} candidato(s) global(is), "
        f"{rejected_count} rejeitado(s)"
    )


def demand_coverage(
    prd_path: str = "docs/PRD.md",
    demand_path: str = "docs/demanda.md",
    project_root: str = ".",
) -> tuple[bool, str]:
    """Verifica deterministicamente se o PRD cobre a demanda original.

    Só roda na primeira run (quando demanda.md existe).
    Nas runs seguintes, demanda.md não existe e o validator passa automaticamente.
    """
    demand_file = Path(project_root) / demand_path
    prd_file = Path(project_root) / prd_path

    # Sem demanda = run subsequente, pular
    if not demand_file.exists():
        return True, "demand_coverage: sem demanda original — pulando (run subsequente)"

    if not prd_file.exists():
        return False, f"demand_coverage FAIL: {prd_path} não encontrado"

    demand_text = demand_file.read_text()
    prd_text = prd_file.read_text()

    stop_words = {
        "como",
        "quero",
        "preciso",
        "para",
        "que",
        "com",
        "sem",
        "por",
        "uma",
        "um",
        "de",
        "do",
        "da",
        "dos",
        "das",
        "no",
        "na",
        "nos",
        "nas",
        "ao",
        "em",
        "os",
        "as",
        "se",
        "ou",
        "ter",
        "ser",
        "ver",
        "usar",
        "deve",
        "devem",
        "deveria",
        "produto",
        "sistema",
        "usuario",
        "usuaria",
        "eu",
        "meu",
        "minha",
        "us",
        "the",
        "a",
        "an",
        "in",
        "on",
        "of",
        "to",
        "and",
        "or",
        "is",
        "with",
        "from",
        "for",
        "by",
        "at",
        "be",
        "have",
        "this",
        "that",
        "user",
        "system",
        "should",
        "must",
        "can",
        "want",
        "need",
    }
    short_requirement_tokens = {
        "ai",
        "ia",
        "ui",
        "ux",
        "api",
        "csv",
        "pdf",
        "xml",
        "sms",
        "sso",
        "mfa",
        "2fa",
        "otp",
        "pix",
        "cpf",
    }

    def _is_significant_short_token(raw_word: str, word: str) -> bool:
        if not 2 <= len(word) <= 3:
            return False
        raw_ascii = (
            unicodedata.normalize("NFD", raw_word)
            .encode("ascii", "ignore")
            .decode("ascii")
        )
        if not any(char.isalpha() for char in raw_ascii):
            return False
        return (
            any(char.isdigit() for char in word)
            or word in short_requirement_tokens
            or raw_ascii.isupper()
        )

    def _tokens(text: str) -> list[str]:
        tokens: list[str] = []
        for raw_word in re.findall(r"[A-Za-z0-9áéíóúãõâêôçàÁÉÍÓÚÃÕÂÊÔÇÀ]+", text):
            word = _normalize(raw_word)
            if not word or word in stop_words:
                continue
            if len(word) > 3 or _is_significant_short_token(raw_word, word):
                tokens.append(word)
        return tokens

    def _requirement_lines(text: str) -> list[str]:
        candidates: list[str] = []
        for raw_line in text.splitlines():
            line = raw_line.strip(" \t-*•0123456789.)")
            if len(line) < 12:
                continue
            lower = _normalize(line)
            explicit = raw_line.lstrip().startswith(("-", "*", "•")) or re.match(
                r"^\s*\d+[.)]", raw_line
            )
            intent = any(
                marker in lower
                for marker in (
                    "quero",
                    "preciso",
                    "deve",
                    "devem",
                    "permitir",
                    "visualizar",
                    "criar",
                    "editar",
                    "remover",
                    "listar",
                    "filtrar",
                    "buscar",
                    "acompanhar",
                    "exportar",
                    "importar",
                    "validar",
                    "mostrar",
                    "i want",
                    "i need",
                    "should",
                    "must",
                    "allow",
                    "create",
                    "edit",
                    "delete",
                    "list",
                    "filter",
                    "search",
                    "export",
                    "import",
                    "validate",
                    "show",
                )
            )
            if explicit or intent:
                candidates.append(line)
        if candidates:
            return candidates[:20]
        paragraphs = [
            p.strip() for p in re.split(r"\n\s*\n", text) if len(p.strip()) >= 40
        ]
        return paragraphs[:10]

    prd_tokens = set(_tokens(prd_text))
    if not prd_tokens:
        return False, "demand_coverage FAIL: PRD sem termos verificáveis"

    missing: list[str] = []
    covered = 0
    for requirement in _requirement_lines(demand_text):
        req_tokens = list(dict.fromkeys(_tokens(requirement)))
        if not req_tokens:
            continue
        missing_short = [
            tok for tok in req_tokens if len(tok) <= 3 and tok not in prd_tokens
        ]
        if missing_short:
            missing.append(
                f"{requirement[:120]} (faltam termos: {', '.join(missing_short)})"
            )
            continue
        hits = [tok for tok in req_tokens if tok in prd_tokens]
        ratio = len(hits) / len(req_tokens)
        if ratio >= 0.45 or len(hits) >= min(3, len(req_tokens)):
            covered += 1
        else:
            missing.append(requirement[:120])

    total = covered + len(missing)
    if total == 0:
        demand_tokens = set(_tokens(demand_text))
        if not demand_tokens:
            return (
                True,
                "demand_coverage: demanda sem requisitos verificáveis — pulando",
            )
        missing_short = [
            tok for tok in demand_tokens if len(tok) <= 3 and tok not in prd_tokens
        ]
        if missing_short:
            return (
                False,
                f"demand_coverage FAIL: faltam termos curtos: {', '.join(sorted(missing_short))}",
            )
        overlap = len(demand_tokens & prd_tokens) / len(demand_tokens)
        if overlap >= 0.35:
            return True, f"demand_coverage: PASS — overlap global {overlap:.0%}"
        return False, f"demand_coverage FAIL: overlap global {overlap:.0%} < 35%"

    if not missing:
        return (
            True,
            f"demand_coverage: PASS — {covered}/{total} requisito(s) coberto(s)",
        )

    missing_str = "; ".join(missing[:5])
    return False, (
        f"demand_coverage FAIL: {covered}/{total} requisito(s) coberto(s); "
        f"faltam: {missing_str}"
    )


def prd_coverage(
    prd_path: str = "docs/PRD.md",
    output_dirs: list[str] | None = None,
    min_ratio: float = 0.7,
    project_root: str = ".",
) -> tuple[bool, str]:
    """Verifica se as User Stories do PRD têm evidência no código gerado.

    Extrai US do PRD via regex, busca keywords nos arquivos de output_dirs.
    PASS se >= min_ratio das US têm pelo menos 1 match.
    """
    import re

    root = Path(project_root)
    prd_file = root / prd_path
    if not prd_file.exists():
        return True, f"prd_coverage: {prd_path} não encontrado — pulando"

    prd_text = prd_file.read_text(encoding="utf-8")

    # Extrair User Stories: ### US-NN — Título
    us_pattern = re.compile(r"###\s+(US-\d+)\s*[—–-]\s*(.+)")
    stories = us_pattern.findall(prd_text)
    if not stories:
        return True, "prd_coverage: nenhuma US encontrada no PRD — pulando"

    # Resolver dirs de output para busca
    if not output_dirs:
        output_dirs = ["frontend/src", "src", "backend"]
    search_dirs = [root / d for d in output_dirs if (root / d).is_dir()]
    if not search_dirs:
        return (
            False,
            f"prd_coverage FAIL: nenhum diretório de output encontrado ({output_dirs})",
        )

    # Coletar todo o texto dos arquivos de código
    code_text = []
    for d in search_dirs:
        for f in d.rglob("*"):
            if f.is_file() and f.suffix in (
                ".js",
                ".ts",
                ".jsx",
                ".tsx",
                ".svelte",
                ".vue",
                ".py",
                ".css",
                ".html",
                ".json",
            ):
                try:
                    code_text.append(f.read_text(encoding="utf-8", errors="ignore"))
                except OSError:
                    continue
    all_code = "\n".join(code_text).lower()

    if not all_code.strip():
        return (
            False,
            "prd_coverage FAIL: nenhum código encontrado nos diretórios de output",
        )

    # Verificar cada US
    STOP_WORDS = {
        "como",
        "quero",
        "para",
        "que",
        "com",
        "sem",
        "por",
        "uma",
        "um",
        "de",
        "do",
        "da",
        "dos",
        "das",
        "no",
        "na",
        "nos",
        "nas",
        "ao",
        "em",
        "os",
        "as",
        "se",
        "ou",
        "ter",
        "ser",
        "ver",
        "usar",
        "the",
        "a",
        "an",
        "in",
        "on",
        "of",
        "to",
        "and",
        "or",
        "is",
        "with",
        "from",
        "for",
        "by",
        "at",
        "be",
        "have",
        "this",
        "that",
    }
    # Mapeamento PT→EN para keywords comuns em UI/dev
    PT_EN = {
        "grafo": "graph",
        "visualizar": "graph",
        "diagrama": "diagram",
        "navegar": "navigate",
        "navegação": "nav",
        "estado": "state",
        "progresso": "progress",
        "terminal": "terminal",
        "editor": "editor",
        "validação": "validat",
        "validar": "validat",
        "árvore": "tree",
        "arquivo": "file",
        "arquivos": "file",
        "processo": "process",
        "dados": "data",
        "reais": "real",
        "acompanhar": "progress",
        "sprint": "sprint",
        "sprints": "sprint",
        "nodes": "node",
        "embutido": "embed",
        "painel": "panel",
        "abas": "tab",
        "tabs": "tab",
        "yaml": "yaml",
        "linhas": "line",
        "sidebar": "sidebar",
        "explorer": "explorer",
    }
    covered = []
    missing = []

    for us_id, us_title in stories:
        # Extrair keywords significativas do título
        words = re.findall(r"[a-záéíóúãõâêôçà]+", us_title.lower())
        keywords = [w for w in words if len(w) > 3 and w not in STOP_WORDS]

        # Verificar cada keyword (original ou tradução conta como hit)
        if not keywords:
            covered.append(us_id)
            continue
        hits = 0
        for kw in keywords:
            if kw in all_code:
                hits += 1
            elif kw in PT_EN and PT_EN[kw] in all_code:
                hits += 1
        ratio = hits / len(keywords)
        if ratio >= 0.4:
            covered.append(us_id)
        else:
            missing.append(us_id)

    total = len(stories)
    cov_ratio = len(covered) / total if total else 1.0

    if cov_ratio >= min_ratio:
        return True, f"prd_coverage: {len(covered)}/{total} US cobertas"

    missing_str = ", ".join(missing[:5])
    return False, (
        f"prd_coverage FAIL: {len(covered)}/{total} US cobertas "
        f"(min {min_ratio:.0%}) — faltam: {missing_str}"
    )


def unique_screenshots(
    screenshots_dir: str = "docs/screenshots",
    min_count: int = 2,
    project_root: str = ".",
) -> tuple[bool, str]:
    """Verifica que os screenshots em um diretório são arquivos distintos (sem cópias).

    Falha se:
    - O diretório não existe ou tem menos de min_count imagens
    - Dois ou mais arquivos têm hash MD5 idêntico (LLM copiou em vez de capturar)
    """
    root = Path(project_root)
    sdir = root / screenshots_dir
    if not sdir.exists():
        return (
            False,
            f"unique_screenshots FAIL: diretório {screenshots_dir} não encontrado",
        )

    IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
    images = [f for f in sdir.rglob("*") if f.suffix.lower() in IMAGE_EXTS]

    if len(images) < min_count:
        return False, (
            f"unique_screenshots FAIL: apenas {len(images)} imagem(ns) em {screenshots_dir} "
            f"(mínimo: {min_count})"
        )

    hashes: dict[str, list[str]] = {}
    for img in images:
        try:
            h = hashlib.md5(img.read_bytes()).hexdigest()
        except OSError:
            continue
        rel = str(img.relative_to(root))
        hashes.setdefault(h, []).append(rel)

    duplicates = {h: paths for h, paths in hashes.items() if len(paths) > 1}
    if duplicates:
        examples = []
        for paths in list(duplicates.values())[:3]:
            examples.append(f"{paths[0]} = {paths[1]}")
        return False, (
            f"unique_screenshots FAIL: {len(duplicates)} grupo(s) de screenshots idênticos "
            f"— {'; '.join(examples)}"
        )

    return (
        True,
        f"unique_screenshots: {len(images)} screenshots únicos em {screenshots_dir}",
    )


def bash_passes(script: str, project_root: str = ".") -> tuple[bool, str]:
    """Roda um script bash e verifica se sai com código 0.

    O script é resolvido relativo ao project_root.
    stdout/stderr são capturados; em caso de falha, as últimas linhas são exibidas.
    """
    script_path = Path(project_root) / script
    if not script_path.exists():
        return False, f"bash_passes FAIL: script não encontrado: {script}"
    try:
        result = subprocess.run(
            ["bash", str(script_path)],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return False, f"bash_passes FAIL: script excedeu 60s: {script}"
    except Exception as e:
        return False, f"bash_passes FAIL: erro ao executar {script}: {e}"

    if result.returncode == 0:
        last_line = (
            result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "ok"
        )
        return True, f"bash_passes: {script} → {last_line}"

    output = (result.stdout + result.stderr).strip()
    preview = "\n".join(output.splitlines()[-5:]) if output else "(sem saída)"
    return (
        False,
        f"bash_passes FAIL: {script} saiu com código {result.returncode}\n{preview}",
    )


def _stop_command_process_group(
    process: subprocess.Popen[str],
    *,
    terminate_timeout: float = 0.25,
    kill_timeout: float = 1.0,
) -> None:
    """Terminate a timed-out shell and every descendant in its process group."""
    if os.name == "posix":
        # `_run_shell_command` starts a new session, so the shell PID is also
        # its process-group ID.  Signal the group even if the shell exits after
        # SIGTERM: a child may still be alive and holding the capture pipes.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

        try:
            process.wait(timeout=terminate_timeout)
        except subprocess.TimeoutExpired:
            pass

        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    else:  # pragma: no cover - the engine test/runtime target is POSIX
        try:
            process.terminate()
            process.wait(timeout=terminate_timeout)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                process.kill()
            except ProcessLookupError:
                pass

    try:
        process.wait(timeout=kill_timeout)
    except subprocess.TimeoutExpired:
        # Defensive fallback for platforms without POSIX process groups.
        try:
            process.kill()
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=kill_timeout)
        except subprocess.TimeoutExpired:
            pass


def _run_shell_command(
    command: str,
    project_root: str,
    timeout: int | float,
) -> subprocess.CompletedProcess[str]:
    """Run a pipefail shell in an isolated group and reap it on timeout."""
    process = subprocess.Popen(
        ["bash", "-o", "pipefail", "-c", command],
        cwd=project_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _stop_command_process_group(process)
        try:
            stdout, stderr = process.communicate(timeout=1)
        except subprocess.TimeoutExpired:
            # The process group has already received SIGKILL.  Closing the
            # capture ends any remaining inherited descriptors defensively.
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
            stdout = exc.output or ""
            stderr = exc.stderr or ""
        raise subprocess.TimeoutExpired(
            process.args,
            timeout,
            output=stdout,
            stderr=stderr,
        ) from exc
    return subprocess.CompletedProcess(
        process.args,
        process.returncode,
        stdout,
        stderr,
    )


def command_succeeds(
    command: str,
    project_root: str = ".",
    timeout: int | float = 120,
) -> tuple[bool, str]:
    """Executa um comando shell e verifica se sai com código 0.

    Diferente de bash_passes, recebe um comando direto (string) em vez de um
    path para script. O comando é executado via bash com pipefail para que
    pipelines como `pytest | tail` nao mascarem falhas do comando principal.
    """
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or timeout <= 0
    ):
        return False, "command_succeeds FAIL: timeout deve ser um número positivo"

    try:
        result = _run_shell_command(command, project_root, timeout)
    except subprocess.TimeoutExpired:
        return False, (
            f"command_succeeds FAIL: comando excedeu {timeout:g}s: {command[:60]}"
        )
    except Exception as e:
        return False, f"command_succeeds FAIL: erro ao executar: {e}"

    def _preview(output_text: str, limit: int = 12) -> str:
        if not output_text:
            return "(sem saída)"
        lines = output_text.splitlines()
        if len(lines) <= limit:
            return "\n".join(lines)
        head_count = max(3, limit // 3)
        tail_count = limit - head_count - 1
        return "\n".join(lines[:head_count] + ["..."] + lines[-tail_count:])

    output = (result.stdout + result.stderr).strip()
    if (
        result.returncode == 0
        and "pytest" in command
        and "no tests ran" in output.lower()
    ):
        preview = _preview(output, limit=8)
        return (
            False,
            f"command_succeeds FAIL: pytest nao executou nenhum teste\n{preview}",
        )

    if result.returncode == 0:
        last_line = (
            result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "ok"
        )
        return True, f"command_succeeds: {command[:60]} → {last_line}"

    if not output and "--silent" in command:
        diagnostic_command = command.replace("--silent", "")
        try:
            diagnostic = _run_shell_command(
                diagnostic_command,
                project_root,
                timeout,
            )
            diagnostic_output = (diagnostic.stdout + diagnostic.stderr).strip()
            if diagnostic_output:
                output = "diagnostico sem --silent:\n" + diagnostic_output
        except Exception:
            pass

    preview = _preview(output, limit=12)
    return (
        False,
        f"command_succeeds FAIL: saiu com código {result.returncode}\n{preview}",
    )


def git_diff_not_empty(path: str = ".", project_root: str = ".") -> tuple[bool, str]:
    """Passa se o ciclo produziu mudança versionável em `path` (código real).

    Pega o node de build "ocioso" que recebe PASS sem tocar o código-alvo
    (lição vibeos cycle-02: frontend.02.implement passou sem escrever o shell).
    Considera: (a) mudanças uncommitted no working tree; (b) commits do branch
    do ciclo desde o merge-base com main/master. Fora de um repo git (modo
    diretório puro), passa com aviso explícito — não verificável ali.
    """
    import subprocess

    def _git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args], cwd=project_root, capture_output=True, text=True
        )

    if _git("rev-parse", "--git-dir").returncode != 0:
        return True, f"git_diff_not_empty: {path} não verificável (sem git) — AVISO"

    dirty = _git("status", "--porcelain", "--", path).stdout.strip()
    if dirty:
        n = len(dirty.splitlines())
        return True, f"git_diff_not_empty: {path} tem {n} mudança(s) no working tree"

    for base_branch in ("main", "master"):
        if _git("rev-parse", "--verify", "--quiet", base_branch).returncode != 0:
            continue
        base = _git("merge-base", "HEAD", base_branch).stdout.strip()
        if not base:
            continue
        changed = _git("diff", "--name-only", base, "HEAD", "--", path).stdout.strip()
        if changed:
            n = len(changed.splitlines())
            return (
                True,
                f"git_diff_not_empty: {path} tem {n} arquivo(s) alterado(s) desde {base[:7]}",
            )
        return False, (
            f"git_diff_not_empty FAIL: nenhuma mudança em {path} neste ciclo "
            f"(nem uncommitted, nem commits desde {base[:7]}) — node de build ocioso?"
        )

    return (
        True,
        f"git_diff_not_empty: {path} sem branch base (main/master) — não verificável, AVISO",
    )
