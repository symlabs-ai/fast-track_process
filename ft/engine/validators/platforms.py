"""Deterministic validators for composed platform-validation profiles."""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
from typing import Any, Mapping

import yaml

from ft.engine.validation_profiles import (
    MOCKUP_WATERMARK_CHECK,
    VALIDATION_MATRIX_VERSION,
    VALIDATION_REPORT_VERSION,
    ValidationProfileError,
    resolve_validation_matrix,
    safe_project_output,
)


_CANDIDATE_PLACEHOLDERS = frozenset(
    {"", "-", "n/a", "na", "none", "null", "unknown", "tbd", "latest"}
)
_SENSITIVE_KEYS = frozenset(
    {
        "access_token",
        "bssid",
        "email",
        "ip",
        "ip_address",
        "password",
        "phone",
        "secret",
        "serial",
        "ssid",
        "token",
        "udid",
    }
)
_SCREENSHOT_SUFFIXES = frozenset({".jpeg", ".jpg", ".png", ".webp"})
_MOCKUP_REF_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9._-]{1,63}")


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} deve ser mapping")
    return dict(value)


def _list(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} deve ser lista")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} deve ser texto não vazio")
    return value.strip()


def _load_yaml(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f"{label} ausente ou vazio: {path}")
    try:
        return _mapping(
            yaml.safe_load(path.read_text(encoding="utf-8", errors="strict")),
            label,
        )
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValueError(f"{label} inválido: {exc}") from exc


def _canonical(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _canonical(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    return value


def _project_contract(root: Path) -> dict[str, Any]:
    return _load_yaml(root / ".ft" / "project.yml", "project contract")


def _contains_sensitive_key(value: object) -> bool:
    if isinstance(value, Mapping):
        keys = {str(key).casefold() for key in value}
        if keys & _SENSITIVE_KEYS:
            return True
        return any(_contains_sensitive_key(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_sensitive_key(item) for item in value)
    return False


def _evidence_file(root: Path, evidence_root: Path, raw_path: object) -> Path:
    relative = Path(_text(raw_path, "evidence path"))
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("evidence path deve ser relativo e seguro")
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(evidence_root.resolve())
    except ValueError as exc:
        raise ValueError(
            f"evidência deve ficar sob {evidence_root.relative_to(root)}"
        ) from exc
    if (
        candidate.is_symlink()
        or not candidate.is_file()
        or candidate.stat().st_size == 0
    ):
        raise ValueError(f"evidência ausente ou vazia: {relative.as_posix()}")
    return candidate


def validation_matrix_valid(
    path: str = "docs/validation-matrix.yml",
    project_root: str = ".",
) -> tuple[bool, str]:
    """Ensure the materialized matrix exactly matches the project contract."""

    root = Path(project_root).resolve()
    try:
        contract = _project_contract(root)
        expected = resolve_validation_matrix(root, contract)
        matrix_path = safe_project_output(root, path)
        actual = _load_yaml(matrix_path, "validation matrix")
        if actual.get("schema_version") != VALIDATION_MATRIX_VERSION:
            raise ValueError(
                f"validation matrix schema_version deve ser {VALIDATION_MATRIX_VERSION}"
            )
        if _canonical(actual) != _canonical(expected):
            raise ValueError(
                "validation matrix diverge da resolução corrente de .ft/project.yml"
            )
    except (OSError, ValueError, ValidationProfileError) as exc:
        return False, f"validation_matrix_valid FAIL: {exc}"
    target_count = sum(len(profile["targets"]) for profile in expected["profiles"])
    return True, (
        "validation_matrix_valid: "
        f"{len(expected['profiles'])} perfil(is), {target_count} target(s), "
        f"status={expected['status']}"
    )


def validation_profile_hooks(
    path: str = "project/Makefile",
    project_root: str = ".",
) -> tuple[bool, str]:
    """Require one stable Make target for every selected execution surface."""

    root = Path(project_root).resolve()
    try:
        contract = _project_contract(root)
        matrix = resolve_validation_matrix(root, contract)
        if matrix["status"] != "active":
            return True, f"validation_profile_hooks: {matrix['status']}"
        makefile = safe_project_output(root, path)
        if makefile.is_symlink() or not makefile.is_file():
            raise ValueError(f"Makefile ausente: {path}")
        text = makefile.read_text(encoding="utf-8", errors="strict")
        missing: list[str] = []
        selected: list[str] = []
        for profile in matrix["profiles"]:
            for target in profile["targets"]:
                make_target = str(target["make_target"])
                selected.append(make_target)
                pattern = rf"(?m)^{re.escape(make_target)}\s*(?::|::)"
                if re.search(pattern, text) is None:
                    missing.append(make_target)
        if missing:
            raise ValueError("Make targets ausentes: " + ", ".join(missing))
    except (OSError, UnicodeError, ValueError, ValidationProfileError) as exc:
        return False, f"validation_profile_hooks FAIL: {exc}"
    return True, f"validation_profile_hooks: {len(selected)} target(s) declarado(s)"


def _matrix_target_map(
    matrix: Mapping[str, Any],
) -> dict[tuple[str, str], dict[str, Any]]:
    targets: dict[tuple[str, str], dict[str, Any]] = {}
    for profile_index, raw_profile in enumerate(
        _list(matrix.get("profiles"), "profiles")
    ):
        profile = _mapping(raw_profile, f"profiles[{profile_index}]")
        profile_id = _text(profile.get("id"), f"profiles[{profile_index}].id")
        for target_index, raw_target in enumerate(
            _list(profile.get("targets"), f"profiles[{profile_index}].targets")
        ):
            target = _mapping(
                raw_target,
                f"profiles[{profile_index}].targets[{target_index}]",
            )
            target_id = _text(
                target.get("id"),
                f"profiles[{profile_index}].targets[{target_index}].id",
            )
            key = (profile_id, target_id)
            if key in targets:
                raise ValueError(
                    f"target duplicado na matrix: {profile_id}/{target_id}"
                )
            targets[key] = target
    return targets


def _validate_installation(
    target: Mapping[str, Any],
    report_target: Mapping[str, Any],
    prefix: str,
) -> None:
    if not target.get("installation_required"):
        return
    installation = _mapping(report_target.get("installation"), f"{prefix}.installation")
    if installation.get("result") != "PASS":
        raise ValueError(f"{prefix}.installation.result deve ser PASS")
    local_hash = _text(
        installation.get("artifact_sha256"),
        f"{prefix}.installation.artifact_sha256",
    ).casefold()
    observed_hash = _text(
        installation.get("observed_artifact_sha256"),
        f"{prefix}.installation.observed_artifact_sha256",
    ).casefold()
    if not re.fullmatch(r"[0-9a-f]{64}", local_hash):
        raise ValueError(f"{prefix}.installation.artifact_sha256 inválido")
    if observed_hash != local_hash:
        raise ValueError(f"{prefix}: artefato observado diverge do candidato instalado")


def _validate_mockup_watermark_check(
    root: Path,
    evidence_root: Path,
    check: Mapping[str, Any],
    check_result: str,
    check_evidence: list[Any],
    prefix: str,
) -> None:
    """Bind every discovered product screen to a visible mockup watermark.

    The validator cannot infer a framework's complete navigation graph, so the
    platform reviewer supplies a structured inventory.  It does, however,
    reject incomplete inventories, placeholder references, reused screenshots
    and generic text files presented as visual proof.
    """

    inventory_complete = check.get("inventory_complete")
    if not isinstance(inventory_complete, bool):
        raise ValueError(f"{prefix}.inventory_complete deve ser booleano")
    unmapped = _list(check.get("unmapped_screens"), f"{prefix}.unmapped_screens")
    unmapped_screens = [
        _text(value, f"{prefix}.unmapped_screens[{index}]")
        for index, value in enumerate(unmapped)
    ]
    if len(unmapped_screens) != len(set(unmapped_screens)):
        raise ValueError(f"{prefix}.unmapped_screens contém duplicatas")
    if inventory_complete != (not unmapped_screens):
        raise ValueError(f"{prefix}.inventory_complete diverge de unmapped_screens")

    screens = _list(check.get("screens"), f"{prefix}.screens")
    if not screens:
        raise ValueError(f"{prefix}.screens não pode ser vazio")
    discovered_screen_count = check.get("discovered_screen_count")
    if (
        not isinstance(discovered_screen_count, int)
        or isinstance(discovered_screen_count, bool)
        or discovered_screen_count < 1
    ):
        raise ValueError(f"{prefix}.discovered_screen_count deve ser inteiro positivo")
    if discovered_screen_count != len(screens) + len(unmapped_screens):
        raise ValueError(
            f"{prefix}.discovered_screen_count diverge de screens + unmapped_screens"
        )

    declared_evidence = {_text(value, f"{prefix}.evidence") for value in check_evidence}
    screen_ids: set[str] = set()
    screen_evidence: set[str] = set()
    screen_results: list[str] = []
    for screen_index, raw_screen in enumerate(screens):
        screen_prefix = f"{prefix}.screens[{screen_index}]"
        screen = _mapping(raw_screen, screen_prefix)
        screen_id = _text(screen.get("id"), f"{screen_prefix}.id")
        if screen_id in screen_ids:
            raise ValueError(f"{prefix}.screens contém id duplicado: {screen_id}")
        screen_ids.add(screen_id)

        mockup_ref = _text(screen.get("mockup_ref"), f"{screen_prefix}.mockup_ref")
        if (
            mockup_ref.casefold() in _CANDIDATE_PLACEHOLDERS
            or _MOCKUP_REF_PATTERN.fullmatch(mockup_ref) is None
        ):
            raise ValueError(f"{screen_prefix}.mockup_ref inválido")
        watermark_text = _text(
            screen.get("watermark_text"),
            f"{screen_prefix}.watermark_text",
        )
        if watermark_text != mockup_ref:
            raise ValueError(
                f"{screen_prefix}.watermark_text deve coincidir com mockup_ref"
            )

        result = _text(screen.get("result"), f"{screen_prefix}.result").upper()
        if result not in {"PASS", "FAIL"}:
            raise ValueError(f"{screen_prefix}.result deve ser PASS ou FAIL")
        screen_results.append(result)

        raw_screen_evidence = _list(screen.get("evidence"), f"{screen_prefix}.evidence")
        if not raw_screen_evidence:
            raise ValueError(f"{screen_prefix}.evidence não pode ser vazio")
        for raw_path in raw_screen_evidence:
            relative = _text(raw_path, f"{screen_prefix}.evidence")
            if relative in screen_evidence:
                raise ValueError(
                    f"{prefix}: screenshot reutilizado por mais de uma tela: {relative}"
                )
            evidence_file = _evidence_file(
                root,
                evidence_root,
                relative,
            )
            if evidence_file.suffix.casefold() not in _SCREENSHOT_SUFFIXES:
                raise ValueError(
                    f"{screen_prefix}.evidence deve ser screenshot PNG/JPEG/WebP"
                )
            if evidence_file.stat().st_size < 1000:
                raise ValueError(
                    f"{screen_prefix}.evidence é pequeno demais para screenshot"
                )
            screen_evidence.add(relative)

    if declared_evidence != screen_evidence:
        raise ValueError(
            f"{prefix}.evidence deve conter exatamente os screenshots de screens"
        )
    expected_result = (
        "PASS"
        if inventory_complete and all(result == "PASS" for result in screen_results)
        else "FAIL"
    )
    if check_result != expected_result:
        raise ValueError(
            f"{prefix}.result {check_result} diverge do inventário; "
            f"esperado {expected_result}"
        )


def platform_validation_report(
    matrix_path: str = "docs/validation-matrix.yml",
    report_path: str = "docs/platform-validation-report.yml",
    evidence_root: str = "docs/evidence/platform-validation",
    test_identity_path: str = "docs/test-identity.json",
    require_approved: bool = True,
    project_root: str = ".",
) -> tuple[bool, str]:
    """Validate the fan-in receipt for every selected platform target.

    Required targets must pass every catalog check.  Optional targets may be
    skipped with a reason, but an observed product failure is never downgraded
    to SKIP.  Physical targets additionally bind installation and device
    evidence to the same candidate without persisting serials or credentials.
    """

    root = Path(project_root).resolve()
    try:
        matrix_ok, matrix_detail = validation_matrix_valid(
            matrix_path,
            project_root=str(root),
        )
        if not matrix_ok:
            raise ValueError(matrix_detail)
        matrix_file = safe_project_output(root, matrix_path)
        matrix = _load_yaml(matrix_file, "validation matrix")
        if matrix.get("status") != "active":
            raise ValueError("platform validation report exige matrix status=active")

        configured_report = matrix.get("report_path")
        if report_path != configured_report:
            raise ValueError(
                f"report_path deve coincidir com a matrix: {configured_report}"
            )
        configured_evidence = matrix.get("evidence_root")
        if evidence_root != configured_evidence:
            raise ValueError(
                f"evidence_root deve coincidir com a matrix: {configured_evidence}"
            )
        configured_identity = _mapping(
            matrix.get("test_identity"), "matrix.test_identity"
        )
        if test_identity_path != configured_identity.get("path"):
            raise ValueError(
                "test_identity_path deve coincidir com matrix.test_identity.path"
            )

        report_file = safe_project_output(root, report_path)
        report = _load_yaml(report_file, "platform validation report")
        if report.get("schema_version") != VALIDATION_REPORT_VERSION:
            raise ValueError(
                f"report schema_version deve ser {VALIDATION_REPORT_VERSION}"
            )
        matrix_hash = hashlib.sha256(matrix_file.read_bytes()).hexdigest()
        if str(report.get("matrix_sha256") or "").casefold() != matrix_hash:
            raise ValueError("matrix_sha256 não corresponde à matrix corrente")
        if _contains_sensitive_key(report):
            raise ValueError("report contém chave sensível ou identificador bruto")

        verdict = _text(report.get("verdict"), "verdict").upper()
        if verdict not in {"APPROVED", "REJECTED"}:
            raise ValueError("verdict deve ser APPROVED ou REJECTED")
        if require_approved and verdict != "APPROVED":
            raise ValueError("este gate exige verdict APPROVED")
        candidate_ref = _text(report.get("candidate_ref"), "candidate_ref")
        if candidate_ref.casefold() in _CANDIDATE_PLACEHOLDERS:
            raise ValueError("candidate_ref não pode ser placeholder")

        expected_targets = _matrix_target_map(matrix)
        observed_targets: dict[tuple[str, str], dict[str, Any]] = {}
        failed_refs: set[tuple[str, str, str]] = set()
        target_results: dict[tuple[str, str], str] = {}
        evidence_owners: dict[str, tuple[str, str]] = {}

        for profile_index, raw_profile in enumerate(
            _list(report.get("profiles"), "report.profiles")
        ):
            profile = _mapping(raw_profile, f"report.profiles[{profile_index}]")
            profile_id = _text(
                profile.get("id"), f"report.profiles[{profile_index}].id"
            )
            for target_index, raw_target in enumerate(
                _list(
                    profile.get("targets"),
                    f"report.profiles[{profile_index}].targets",
                )
            ):
                prefix = f"report.profiles[{profile_index}].targets[{target_index}]"
                report_target = _mapping(raw_target, prefix)
                target_id = _text(report_target.get("id"), f"{prefix}.id")
                key = (profile_id, target_id)
                if key in observed_targets:
                    raise ValueError(
                        f"target duplicado no report: {profile_id}/{target_id}"
                    )
                if key not in expected_targets:
                    raise ValueError(
                        f"target inesperado no report: {profile_id}/{target_id}"
                    )
                observed_targets[key] = report_target
                target = expected_targets[key]
                if report_target.get("required") is not target.get("required"):
                    raise ValueError(f"{prefix}.required diverge da matrix")

                result = _text(report_target.get("result"), f"{prefix}.result").upper()
                if result not in {"PASS", "FAIL", "SKIP"}:
                    raise ValueError(f"{prefix}.result deve ser PASS, FAIL ou SKIP")
                target_results[key] = result
                if result == "SKIP":
                    if target.get("required") is True:
                        raise ValueError(f"target obrigatório não pode ser SKIP: {key}")
                    _text(report_target.get("reason"), f"{prefix}.reason")
                    if report_target.get("checks") not in ([], None):
                        raise ValueError(f"{prefix}.checks deve ficar vazio em SKIP")
                    continue

                observed_ref = _text(
                    report_target.get("observed_candidate_ref"),
                    f"{prefix}.observed_candidate_ref",
                )
                if observed_ref != candidate_ref:
                    raise ValueError(f"{prefix}: candidato observado diverge do report")
                environment = _mapping(
                    report_target.get("environment"), f"{prefix}.environment"
                )
                if environment.get("kind") != target.get("environment_kind"):
                    raise ValueError(f"{prefix}.environment.kind diverge da matrix")
                if environment.get("execution_surface") != target.get(
                    "execution_surface"
                ):
                    raise ValueError(
                        f"{prefix}.environment.execution_surface diverge da matrix"
                    )
                _text(environment.get("os_name"), f"{prefix}.environment.os_name")
                _text(environment.get("os_version"), f"{prefix}.environment.os_version")
                if target.get("physical") is True:
                    device_ref = _text(
                        environment.get("device_ref"),
                        f"{prefix}.environment.device_ref",
                    )
                    if not re.fullmatch(
                        r"[A-Za-z0-9][A-Za-z0-9._:-]{2,127}", device_ref
                    ):
                        raise ValueError(
                            f"{prefix}.environment.device_ref opaco inválido"
                        )
                _validate_installation(target, report_target, prefix)

                expected_checks = list(target.get("checks") or [])
                seen_checks: dict[str, str] = {}
                for check_index, raw_check in enumerate(
                    _list(report_target.get("checks"), f"{prefix}.checks")
                ):
                    check_prefix = f"{prefix}.checks[{check_index}]"
                    check = _mapping(raw_check, check_prefix)
                    check_id = _text(check.get("id"), f"{check_prefix}.id")
                    if check_id in seen_checks:
                        raise ValueError(f"check duplicado em {key}: {check_id}")
                    check_result = _text(
                        check.get("result"), f"{check_prefix}.result"
                    ).upper()
                    if check_result not in {"PASS", "FAIL"}:
                        raise ValueError(f"{check_prefix}.result deve ser PASS ou FAIL")
                    seen_checks[check_id] = check_result
                    raw_evidence = _list(
                        check.get("evidence"), f"{check_prefix}.evidence"
                    )
                    if not raw_evidence:
                        raise ValueError(f"{check_prefix}.evidence não pode ser vazio")
                    for evidence in raw_evidence:
                        evidence_file = _evidence_file(
                            root,
                            safe_project_output(root, evidence_root),
                            evidence,
                        )
                        digest = hashlib.sha256(evidence_file.read_bytes()).hexdigest()
                        previous_owner = evidence_owners.get(digest)
                        if previous_owner is not None and previous_owner != key:
                            raise ValueError(
                                f"{key} reutiliza evidência do target {previous_owner}"
                            )
                        evidence_owners[digest] = key
                    if check_id == MOCKUP_WATERMARK_CHECK:
                        _validate_mockup_watermark_check(
                            root,
                            safe_project_output(root, evidence_root),
                            check,
                            check_result,
                            raw_evidence,
                            check_prefix,
                        )
                    if check_result == "FAIL":
                        failed_refs.add((profile_id, target_id, check_id))

                if list(seen_checks) != expected_checks:
                    missing = sorted(set(expected_checks) - set(seen_checks))
                    extra = sorted(set(seen_checks) - set(expected_checks))
                    raise ValueError(
                        f"checks divergentes em {profile_id}/{target_id}; "
                        f"ausentes={missing}, extras={extra}, ordem deve seguir a matrix"
                    )
                if result == "PASS" and any(
                    check_result != "PASS" for check_result in seen_checks.values()
                ):
                    raise ValueError(f"target {key} PASS contém check reprovado")
                if result == "FAIL" and not any(
                    check_result == "FAIL" for check_result in seen_checks.values()
                ):
                    raise ValueError(f"target {key} FAIL não contém check reprovado")

        if set(observed_targets) != set(expected_targets):
            missing = sorted(set(expected_targets) - set(observed_targets))
            extra = sorted(set(observed_targets) - set(expected_targets))
            raise ValueError(
                f"cobertura de targets divergente; ausentes={missing}, extras={extra}"
            )

        finding_refs: set[tuple[str, str, str]] = set()
        findings = _list(report.get("findings"), "findings")
        seen_finding_ids: set[str] = set()
        for index, raw_finding in enumerate(findings):
            finding = _mapping(raw_finding, f"findings[{index}]")
            finding_id = _text(finding.get("id"), f"findings[{index}].id")
            if not re.fullmatch(r"PV-F-\d{3,}", finding_id):
                raise ValueError(f"findings[{index}].id inválido")
            if finding_id in seen_finding_ids:
                raise ValueError(f"finding duplicado: {finding_id}")
            seen_finding_ids.add(finding_id)
            ref = (
                _text(finding.get("profile"), f"findings[{index}].profile"),
                _text(finding.get("target"), f"findings[{index}].target"),
                _text(finding.get("check"), f"findings[{index}].check"),
            )
            if ref not in expected_targets and ref[:2] not in expected_targets:
                raise ValueError(f"finding aponta target inexistente: {ref[:2]}")
            _text(finding.get("summary"), f"findings[{index}].summary")
            finding_refs.add(ref)
        if finding_refs != failed_refs:
            raise ValueError(
                f"findings não correspondem aos checks reprovados; "
                f"esperados={sorted(failed_refs)}, recebidos={sorted(finding_refs)}"
            )

        any_fail = any(result == "FAIL" for result in target_results.values())
        required_not_pass = any(
            expected_targets[key].get("required") is True and result != "PASS"
            for key, result in target_results.items()
        )
        expected_verdict = "REJECTED" if any_fail or required_not_pass else "APPROVED"
        if verdict != expected_verdict:
            raise ValueError(
                f"verdict {verdict} diverge dos targets; esperado {expected_verdict}"
            )
        if verdict == "APPROVED" and findings:
            raise ValueError("APPROVED exige findings vazio")

        if configured_identity.get("policy") == "required":
            from ft.engine.validators.artifacts import test_identity_ready

            identity_ok, identity_detail = test_identity_ready(
                test_identity_path,
                project_root=str(root),
            )
            if not identity_ok:
                raise ValueError(identity_detail)
    except (
        OSError,
        UnicodeError,
        yaml.YAMLError,
        ValueError,
        ValidationProfileError,
    ) as exc:
        return False, f"platform_validation_report FAIL: {exc}"

    return True, (
        "platform_validation_report: "
        f"{len(expected_targets)} target(s), verdict={verdict}, "
        f"candidate={candidate_ref}"
    )


def platform_validation_ready(
    matrix_path: str = "docs/validation-matrix.yml",
    report_path: str = "docs/platform-validation-report.yml",
    evidence_root: str = "docs/evidence/platform-validation",
    test_identity_path: str = "docs/test-identity.json",
    project_root: str = ".",
) -> tuple[bool, str]:
    """Aggregate gate: inactive profiles pass; active profiles need approval."""

    root = Path(project_root).resolve()
    try:
        contract = _project_contract(root)
        matrix = resolve_validation_matrix(root, contract)
    except (OSError, UnicodeError, ValueError, ValidationProfileError) as exc:
        return False, f"platform_validation_ready FAIL: {exc}"
    if matrix["status"] != "active":
        return True, f"platform_validation_ready: {matrix['status']}"
    return platform_validation_report(
        matrix_path=matrix_path,
        report_path=report_path,
        evidence_root=evidence_root,
        test_identity_path=test_identity_path,
        require_approved=True,
        project_root=str(root),
    )
