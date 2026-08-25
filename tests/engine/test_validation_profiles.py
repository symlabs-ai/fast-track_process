from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from ft.engine.validation_profiles import (
    ARTIFACT_INSTALL_REUSE_CHECK,
    BUILTIN_VALIDATION_PROFILES,
    MOCKUP_WATERMARK_CHECK,
    ValidationProfileError,
    default_validation_config,
    detect_validation_platforms,
    normalize_validation_config,
    resolve_validation_matrix,
    validation_profile_catalog,
    write_validation_matrix,
)
from ft.engine.validators.platforms import (
    platform_validation_report,
    validation_matrix_valid,
    validation_profile_hooks,
)


def _explicit_config(
    profile: str,
    target: str,
    *,
    required: bool = True,
    test_identity: str = "optional",
) -> dict:
    config = default_validation_config()
    config["mode"] = "explicit"
    config["test_identity"]["policy"] = test_identity
    config["platforms"] = {profile: {"targets": {target: {"required": required}}}}
    return config


def _project(tmp_path: Path, config: dict) -> tuple[Path, dict]:
    root = tmp_path / "product"
    (root / ".ft").mkdir(parents=True)
    contract = {"validation": config}
    (root / ".ft" / "project.yml").write_text(
        yaml.safe_dump(contract, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return root, contract


def _identity(root: Path) -> None:
    path = root / "docs" / "test-identity.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "identity_ref": "agent.validation.001",
                "environment": "isolated_test",
                "seed_status": "ready",
                "seeded": True,
                "idempotent": True,
                "resettable": True,
                "journey_ready": True,
                "credentials_source": "secret_store",
                "secret_values_recorded": False,
            }
        ),
        encoding="utf-8",
    )


def _approved_report(root: Path, matrix: dict) -> dict:
    matrix_path = root / matrix["matrix_path"]
    evidence_root = root / matrix["evidence_root"]
    evidence_root.mkdir(parents=True, exist_ok=True)
    candidate = "candidate-abc123"
    profiles = []
    for profile in matrix["profiles"]:
        report_targets = []
        for target in profile["targets"]:
            evidence = evidence_root / f"{profile['id']}-{target['id']}.txt"
            evidence.write_text(
                f"fresh proof for {profile['id']}/{target['id']}\n",
                encoding="utf-8",
            )
            screenshot = (
                evidence_root / f"{profile['id']}-{target['id']}-screen-S01.png"
            )
            screenshot.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 2048)
            checks = []
            for check in target["checks"]:
                if check == MOCKUP_WATERMARK_CHECK:
                    screenshot_path = screenshot.relative_to(root).as_posix()
                    checks.append(
                        {
                            "id": check,
                            "result": "PASS",
                            "evidence": [screenshot_path],
                            "inventory_complete": True,
                            "discovered_screen_count": 1,
                            "unmapped_screens": [],
                            "screens": [
                                {
                                    "id": "home",
                                    "mockup_ref": "S01",
                                    "watermark_text": "S01",
                                    "result": "PASS",
                                    "evidence": [screenshot_path],
                                }
                            ],
                        }
                    )
                    continue
                checks.append(
                    {
                        "id": check,
                        "result": "PASS",
                        "evidence": [evidence.relative_to(root).as_posix()],
                    }
                )
            target_report = {
                "id": target["id"],
                "required": target["required"],
                "result": "PASS",
                "observed_candidate_ref": candidate,
                "environment": {
                    "kind": target["environment_kind"],
                    "execution_surface": target["execution_surface"],
                    "os_name": profile["id"],
                    "os_version": "test-1",
                },
                "checks": checks,
            }
            if target["physical"]:
                target_report["environment"]["device_ref"] = "device.agent.001"
            if target["installation_required"]:
                target_report["installation"] = {
                    "result": "PASS",
                    "artifact_sha256": "a" * 64,
                    "observed_artifact_sha256": "a" * 64,
                }
            report_targets.append(target_report)
        profiles.append({"id": profile["id"], "targets": report_targets})
    return {
        "schema_version": 1,
        "matrix_sha256": hashlib.sha256(matrix_path.read_bytes()).hexdigest(),
        "verdict": "APPROVED",
        "candidate_ref": candidate,
        "profiles": profiles,
        "findings": [],
    }


def _write_report(root: Path, matrix: dict, report: dict) -> None:
    path = root / matrix["report_path"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(report, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def test_catalog_has_mobile_web_and_native_desktop_profiles():
    catalog = validation_profile_catalog()
    profiles = {profile["id"]: profile for profile in catalog["profiles"]}

    assert set(profiles) == {"android", "ios", "web", "desktop"}
    assert {target["id"] for target in profiles["ios"]["targets"]} == {
        "simulator",
        "physical",
    }
    assert "iphone" not in profiles
    assert {target["id"] for target in profiles["desktop"]["targets"]} == {
        "windows",
        "macos",
        "linux",
    }
    assert all(
        MOCKUP_WATERMARK_CHECK in target["checks"]
        for profile in profiles.values()
        for target in profile["targets"]
    )
    assert all(
        "accessibility" not in target["checks"]
        for profile in profiles.values()
        for target in profile["targets"]
    )


def test_explicit_android_physical_matrix_carries_physical_validation_learnings(
    tmp_path,
):
    config = _explicit_config(
        "android",
        "physical",
        test_identity="required",
    )
    root, contract = _project(tmp_path, config)

    matrix = resolve_validation_matrix(root, contract)
    target = matrix["profiles"][0]["targets"][0]

    assert matrix["status"] == "active"
    assert target["physical"] is True
    assert target["installation_required"] is True
    assert {
        "visual",
        "navigation",
        "state_isolation",
        "state_persistence",
        "runtime_permissions",
        "system_insets",
        "back_navigation",
        "physical_connectivity",
        ARTIFACT_INSTALL_REUSE_CHECK,
    }.issubset(target["checks"])
    assert target["make_target"] == "validate-android-physical"


def test_explicit_config_rejects_iphone_as_a_separate_profile():
    config = default_validation_config()
    config["mode"] = "explicit"
    config["platforms"] = {"iphone": {"targets": {"physical": {"required": True}}}}

    try:
        normalize_validation_config(config)
    except ValidationProfileError as exc:
        assert "desconhecido" in str(exc)
    else:
        raise AssertionError("iphone deveria ser target físico do perfil ios")


def test_automatic_detection_resolves_all_supported_surfaces(tmp_path):
    root = tmp_path / "product"
    (root / "android" / "app" / "src" / "main").mkdir(parents=True)
    (root / "android" / "app" / "src" / "main" / "AndroidManifest.xml").write_text(
        "<manifest />\n",
        encoding="utf-8",
    )
    ios = root / "ios" / "App.xcodeproj"
    ios.mkdir(parents=True)
    (ios / "project.pbxproj").write_text(
        "IPHONEOS_DEPLOYMENT_TARGET = 18.0; SDKROOT = iphoneos;\n",
        encoding="utf-8",
    )
    (root / "package.json").write_text(
        json.dumps(
            {
                "dependencies": {
                    "react": "1",
                    "electron": "1",
                }
            }
        ),
        encoding="utf-8",
    )

    detected = detect_validation_platforms(root)

    assert set(detected["platforms"]) == {"android", "ios", "web", "desktop"}
    assert set(detected["platforms"]["desktop"]["targets"]) == {
        "windows",
        "macos",
        "linux",
    }


def test_matrix_and_android_physical_report_are_bound_and_approved(tmp_path):
    config = _explicit_config(
        "android",
        "physical",
        test_identity="required",
    )
    root, contract = _project(tmp_path, config)
    matrix_path, matrix = write_validation_matrix(root, contract)
    _identity(root)
    report = _approved_report(root, matrix)
    _write_report(root, matrix, report)

    assert matrix_path == root / "docs" / "validation-matrix.yml"
    assert validation_matrix_valid(project_root=str(root))[0]
    passed, detail = platform_validation_report(project_root=str(root))

    assert passed, detail
    assert "1 target(s)" in detail


def test_report_rejects_mockup_watermark_without_per_screen_visual_proof(tmp_path):
    config = _explicit_config("web", "desktop_browser")
    root, contract = _project(tmp_path, config)
    _path, matrix = write_validation_matrix(root, contract)
    report = _approved_report(root, matrix)
    checks = report["profiles"][0]["targets"][0]["checks"]
    watermark = next(check for check in checks if check["id"] == MOCKUP_WATERMARK_CHECK)
    watermark["screens"][0]["watermark_text"] = "S02"
    _write_report(root, matrix, report)

    passed, detail = platform_validation_report(project_root=str(root))

    assert not passed
    assert "watermark_text deve coincidir com mockup_ref" in detail


def test_report_rejects_incomplete_or_reused_screen_inventory(tmp_path):
    config = _explicit_config("desktop", "linux")
    root, contract = _project(tmp_path, config)
    _path, matrix = write_validation_matrix(root, contract)
    report = _approved_report(root, matrix)
    checks = report["profiles"][0]["targets"][0]["checks"]
    watermark = next(check for check in checks if check["id"] == MOCKUP_WATERMARK_CHECK)
    watermark["discovered_screen_count"] = 2
    watermark["unmapped_screens"] = ["settings"]
    _write_report(root, matrix, report)

    passed, detail = platform_validation_report(project_root=str(root))

    assert not passed
    assert "inventory_complete diverge" in detail


def test_physical_report_rejects_raw_device_identifier_and_candidate_drift(tmp_path):
    config = _explicit_config("ios", "physical")
    root, contract = _project(tmp_path, config)
    _path, matrix = write_validation_matrix(root, contract)
    report = _approved_report(root, matrix)
    target = report["profiles"][0]["targets"][0]
    target["environment"]["serial"] = "raw-device-serial"
    target["observed_candidate_ref"] = "different-candidate"
    _write_report(root, matrix, report)

    passed, detail = platform_validation_report(project_root=str(root))

    assert not passed
    assert "sensível" in detail or "diverge" in detail


def test_optional_target_can_skip_but_required_target_cannot(tmp_path):
    config = _explicit_config("desktop", "linux", required=False)
    root, contract = _project(tmp_path, config)
    _path, matrix = write_validation_matrix(root, contract)
    report = _approved_report(root, matrix)
    target = report["profiles"][0]["targets"][0]
    target["result"] = "SKIP"
    target["reason"] = "runner Linux indisponível nesta execução"
    target["checks"] = []
    target.pop("installation")
    target.pop("observed_candidate_ref")
    target.pop("environment")
    _write_report(root, matrix, report)

    assert platform_validation_report(project_root=str(root))[0]

    config["platforms"]["desktop"]["targets"]["linux"]["required"] = True
    (root / ".ft" / "project.yml").write_text(
        yaml.safe_dump({"validation": config}, sort_keys=False),
        encoding="utf-8",
    )
    _path, matrix = write_validation_matrix(root, {"validation": config})
    report["matrix_sha256"] = hashlib.sha256(_path.read_bytes()).hexdigest()
    report["profiles"][0]["targets"][0]["required"] = True
    _write_report(root, matrix, report)

    passed, detail = platform_validation_report(project_root=str(root))
    assert not passed
    assert "obrigatório não pode ser SKIP" in detail


def test_report_requires_every_catalog_check_in_matrix_order(tmp_path):
    config = _explicit_config("web", "desktop_browser")
    root, contract = _project(tmp_path, config)
    _path, matrix = write_validation_matrix(root, contract)
    report = _approved_report(root, matrix)
    report["profiles"][0]["targets"][0]["checks"].pop()
    _write_report(root, matrix, report)

    passed, detail = platform_validation_report(project_root=str(root))

    assert not passed
    assert "checks divergentes" in detail


def test_selected_targets_require_stable_make_hooks(tmp_path):
    config = _explicit_config("android", "physical")
    root, _contract = _project(tmp_path, config)
    makefile = root / "project" / "Makefile"
    makefile.parent.mkdir(parents=True)
    makefile.write_text("verify:\n\t@true\n", encoding="utf-8")

    passed, detail = validation_profile_hooks(project_root=str(root))
    assert not passed
    assert "validate-android-physical" in detail

    makefile.write_text(
        "verify:\n\t@true\n\nvalidate-android-physical:\n\t@true\n",
        encoding="utf-8",
    )
    assert validation_profile_hooks(project_root=str(root))[0]


def test_builtin_registry_has_unique_checks_and_make_targets():
    make_targets: set[str] = set()
    for profile in BUILTIN_VALIDATION_PROFILES.values():
        for target in profile.targets:
            assert len(target.checks) == len(set(target.checks))
            assert target.make_target not in make_targets
            make_targets.add(target.make_target)
