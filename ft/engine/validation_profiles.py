"""Composable validation profiles resolved from the project contract.

The process graph owns *when* product validation happens.  This module owns
*what* must be proven for each selected execution surface.  Keeping the
catalog in the engine lets builder and maintenance templates share the same
contract without copying Android, iOS, browser or desktop branches into every
process YAML.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping

import yaml


VALIDATION_CONFIG_VERSION = 1
VALIDATION_MATRIX_VERSION = 1
VALIDATION_REPORT_VERSION = 1
VALIDATION_MODES = frozenset({"automatic", "explicit", "disabled"})
TEST_IDENTITY_POLICIES = frozenset({"required", "optional", "not_required"})
MOCKUP_WATERMARK_CHECK = "mockup_watermark"
ARTIFACT_INSTALL_REUSE_CHECK = "artifact_install_reuse"

DEFAULT_MATRIX_PATH = "docs/validation-matrix.yml"
DEFAULT_REPORT_PATH = "docs/platform-validation-report.yml"
DEFAULT_EVIDENCE_ROOT = "docs/evidence/platform-validation"
DEFAULT_TEST_IDENTITY_PATH = "docs/test-identity.json"


class ValidationProfileError(ValueError):
    """Raised when a validation profile contract cannot be trusted."""


@dataclass(frozen=True)
class ValidationTargetDefinition:
    id: str
    label: str
    environment_kind: str
    execution_surface: str
    make_target: str
    checks: tuple[str, ...]
    physical: bool = False
    installation_required: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "environment_kind": self.environment_kind,
            "execution_surface": self.execution_surface,
            "make_target": self.make_target,
            "checks": list(self.checks),
            "physical": self.physical,
            "installation_required": self.installation_required,
        }


@dataclass(frozen=True)
class ValidationProfileDefinition:
    id: str
    label: str
    targets: tuple[ValidationTargetDefinition, ...]

    def target(self, target_id: str) -> ValidationTargetDefinition:
        for target in self.targets:
            if target.id == target_id:
                return target
        raise ValidationProfileError(
            f"perfil {self.id!r} não possui target {target_id!r}"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "targets": [target.as_dict() for target in self.targets],
        }


_COMMON_UI_CHECKS = (
    "functional",
    "visual",
    MOCKUP_WATERMARK_CHECK,
    "accessibility",
    "navigation",
    "state_isolation",
    "state_persistence",
)


def _target(
    target_id: str,
    label: str,
    environment_kind: str,
    execution_surface: str,
    checks: tuple[str, ...],
    *,
    physical: bool = False,
    installation_required: bool = False,
) -> ValidationTargetDefinition:
    return ValidationTargetDefinition(
        id=target_id,
        label=label,
        environment_kind=environment_kind,
        execution_surface=execution_surface,
        make_target=f"validate-{execution_surface.replace('_', '-')}",
        checks=checks,
        physical=physical,
        installation_required=installation_required,
    )


BUILTIN_VALIDATION_PROFILES: dict[str, ValidationProfileDefinition] = {
    "android": ValidationProfileDefinition(
        id="android",
        label="Android",
        targets=(
            _target(
                "emulator",
                "Android emulator",
                "emulator",
                "android_emulator",
                (
                    "build_install",
                    *_COMMON_UI_CHECKS,
                    "runtime_permissions",
                    "system_insets",
                    "back_navigation",
                    "orientation",
                ),
                installation_required=True,
            ),
            _target(
                "physical",
                "Android physical device",
                "physical_device",
                "android_physical",
                (
                    "build_install",
                    ARTIFACT_INSTALL_REUSE_CHECK,
                    *_COMMON_UI_CHECKS,
                    "runtime_permissions",
                    "system_insets",
                    "back_navigation",
                    "orientation",
                    "physical_connectivity",
                ),
                physical=True,
                installation_required=True,
            ),
        ),
    ),
    "ios": ValidationProfileDefinition(
        id="ios",
        label="iOS / iPadOS",
        targets=(
            _target(
                "simulator",
                "iOS simulator",
                "simulator",
                "ios_simulator",
                (
                    "build_install",
                    *_COMMON_UI_CHECKS,
                    "runtime_permissions",
                    "safe_area",
                    "back_navigation",
                    "orientation",
                ),
                installation_required=True,
            ),
            _target(
                "physical",
                "Physical iPhone or iPad",
                "physical_device",
                "ios_physical",
                (
                    "build_install",
                    *_COMMON_UI_CHECKS,
                    "runtime_permissions",
                    "safe_area",
                    "back_navigation",
                    "orientation",
                    "signing_provisioning",
                ),
                physical=True,
                installation_required=True,
            ),
        ),
    ),
    "web": ValidationProfileDefinition(
        id="web",
        label="Web",
        targets=(
            _target(
                "desktop_browser",
                "Desktop browser",
                "browser",
                "web_desktop_browser",
                (
                    *_COMMON_UI_CHECKS,
                    "responsive_layout",
                    "keyboard_navigation",
                    "browser_navigation",
                ),
            ),
            _target(
                "mobile_browser",
                "Mobile browser viewport",
                "browser",
                "web_mobile_browser",
                (
                    *_COMMON_UI_CHECKS,
                    "responsive_layout",
                    "touch_layout",
                    "browser_navigation",
                    "orientation",
                ),
            ),
        ),
    ),
    "desktop": ValidationProfileDefinition(
        id="desktop",
        label="Native desktop",
        targets=tuple(
            _target(
                operating_system,
                label,
                "desktop_os",
                f"desktop_{operating_system}",
                (
                    "build_install",
                    *_COMMON_UI_CHECKS,
                    "window_resize",
                    "keyboard_navigation",
                    "native_navigation",
                ),
                installation_required=True,
            )
            for operating_system, label in (
                ("windows", "Windows desktop"),
                ("macos", "macOS desktop"),
                ("linux", "Linux desktop"),
            )
        ),
    ),
}


_IGNORED_SCAN_DIRS = frozenset(
    {
        ".git",
        ".ft",
        ".venv",
        "venv",
        "node_modules",
        "build",
        "dist",
        "target",
        "vendor",
        "coverage",
        "mockup",
        "mockups",
    }
)


def validation_profile_catalog() -> dict[str, Any]:
    """Return the stable, serializable built-in catalog."""

    return {
        "schema_version": VALIDATION_CONFIG_VERSION,
        "profiles": [
            BUILTIN_VALIDATION_PROFILES[profile_id].as_dict()
            for profile_id in BUILTIN_VALIDATION_PROFILES
        ],
    }


def default_validation_config() -> dict[str, Any]:
    """Default used by new projects; detection remains deterministic."""

    return {
        "schema_version": VALIDATION_CONFIG_VERSION,
        "mode": "automatic",
        "matrix_path": DEFAULT_MATRIX_PATH,
        "report_path": DEFAULT_REPORT_PATH,
        "evidence_root": DEFAULT_EVIDENCE_ROOT,
        "test_identity": {
            "policy": "optional",
            "path": DEFAULT_TEST_IDENTITY_PATH,
        },
        "platforms": {},
    }


def _relative_path(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationProfileError(f"{field} deve ser path relativo não vazio")
    candidate = Path(value.strip())
    if candidate.is_absolute() or ".." in candidate.parts or "\\" in value:
        raise ValidationProfileError(f"{field} deve permanecer dentro do projeto")
    return candidate.as_posix()


def normalize_validation_config(value: object) -> dict[str, Any]:
    """Validate and canonicalize ``.ft/project.yml.validation``."""

    if not isinstance(value, Mapping):
        raise ValidationProfileError("validation deve ser mapping")
    if value.get("schema_version") != VALIDATION_CONFIG_VERSION:
        raise ValidationProfileError(
            f"validation.schema_version deve ser {VALIDATION_CONFIG_VERSION}"
        )
    mode = value.get("mode")
    if mode not in VALIDATION_MODES:
        raise ValidationProfileError(
            "validation.mode deve ser automatic, explicit ou disabled"
        )

    test_identity = value.get("test_identity") or {
        "policy": "optional",
        "path": DEFAULT_TEST_IDENTITY_PATH,
    }
    if not isinstance(test_identity, Mapping):
        raise ValidationProfileError("validation.test_identity deve ser mapping")
    identity_policy = test_identity.get("policy", "optional")
    if identity_policy not in TEST_IDENTITY_POLICIES:
        raise ValidationProfileError(
            "validation.test_identity.policy deve ser required, optional ou "
            "not_required"
        )
    identity_path = _relative_path(
        test_identity.get("path", DEFAULT_TEST_IDENTITY_PATH),
        field="validation.test_identity.path",
    )

    raw_platforms = value.get("platforms", {})
    if not isinstance(raw_platforms, Mapping):
        raise ValidationProfileError("validation.platforms deve ser mapping")
    if mode == "automatic" and raw_platforms:
        raise ValidationProfileError(
            "validation.platforms deve ficar vazio quando mode=automatic"
        )
    if mode == "explicit" and not raw_platforms:
        raise ValidationProfileError(
            "validation.platforms não pode ficar vazio quando mode=explicit"
        )
    if mode == "disabled" and raw_platforms:
        raise ValidationProfileError(
            "validation.platforms deve ficar vazio quando mode=disabled"
        )

    reason = value.get("reason")
    if mode == "disabled" and (not isinstance(reason, str) or not reason.strip()):
        raise ValidationProfileError(
            "validation.reason é obrigatório quando mode=disabled"
        )

    platforms: dict[str, Any] = {}
    for profile_id in sorted(raw_platforms):
        if profile_id not in BUILTIN_VALIDATION_PROFILES:
            known = ", ".join(BUILTIN_VALIDATION_PROFILES)
            raise ValidationProfileError(
                f"validation.platforms.{profile_id} desconhecido; use {known}"
            )
        raw_profile = raw_platforms[profile_id]
        if not isinstance(raw_profile, Mapping):
            raise ValidationProfileError(
                f"validation.platforms.{profile_id} deve ser mapping"
            )
        raw_targets = raw_profile.get("targets")
        if not isinstance(raw_targets, Mapping) or not raw_targets:
            raise ValidationProfileError(
                f"validation.platforms.{profile_id}.targets deve ser mapping não vazio"
            )
        targets: dict[str, dict[str, bool]] = {}
        definition = BUILTIN_VALIDATION_PROFILES[profile_id]
        for target_id in sorted(raw_targets):
            definition.target(str(target_id))
            raw_target = raw_targets[target_id]
            if not isinstance(raw_target, Mapping):
                raise ValidationProfileError(
                    f"validation.platforms.{profile_id}.targets.{target_id} "
                    "deve ser mapping"
                )
            required = raw_target.get("required")
            if not isinstance(required, bool):
                raise ValidationProfileError(
                    f"validation.platforms.{profile_id}.targets.{target_id}.required "
                    "deve ser booleano"
                )
            unknown = set(raw_target) - {"required"}
            if unknown:
                raise ValidationProfileError(
                    f"target {profile_id}/{target_id} possui campos desconhecidos: "
                    + ", ".join(sorted(str(item) for item in unknown))
                )
            targets[str(target_id)] = {"required": required}
        unknown = set(raw_profile) - {"targets"}
        if unknown:
            raise ValidationProfileError(
                f"perfil {profile_id} possui campos desconhecidos: "
                + ", ".join(sorted(str(item) for item in unknown))
            )
        platforms[profile_id] = {"targets": targets}

    normalized: dict[str, Any] = {
        "schema_version": VALIDATION_CONFIG_VERSION,
        "mode": mode,
        "matrix_path": _relative_path(
            value.get("matrix_path", DEFAULT_MATRIX_PATH),
            field="validation.matrix_path",
        ),
        "report_path": _relative_path(
            value.get("report_path", DEFAULT_REPORT_PATH),
            field="validation.report_path",
        ),
        "evidence_root": _relative_path(
            value.get("evidence_root", DEFAULT_EVIDENCE_ROOT),
            field="validation.evidence_root",
        ),
        "test_identity": {
            "policy": identity_policy,
            "path": identity_path,
        },
        "platforms": platforms,
    }
    if mode == "disabled":
        normalized["reason"] = reason.strip()
    return normalized


def validation_config_digest(config: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        dict(config),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _iter_project_files(root: Path, *, limit: int = 50_000):
    count = 0
    for current, dirs, files in os.walk(root):
        dirs[:] = sorted(
            directory
            for directory in dirs
            if directory not in _IGNORED_SCAN_DIRS
            and not directory.startswith(".gradle")
        )
        for filename in sorted(files):
            count += 1
            if count > limit:
                return
            path = Path(current) / filename
            try:
                relative = path.relative_to(root).as_posix()
            except ValueError:
                continue
            yield path, relative


def _small_text(path: Path, *, limit: int = 1_000_000) -> str:
    try:
        if path.stat().st_size > limit:
            return ""
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def detect_validation_platforms(root: str | Path) -> dict[str, Any]:
    """Detect platform candidates from source manifests, never from prose."""

    project_root = Path(root).resolve()
    signals: dict[str, set[str]] = {
        profile_id: set() for profile_id in BUILTIN_VALIDATION_PROFILES
    }
    desktop_targets: set[str] = set()
    web_dependencies = {
        "@angular/core",
        "next",
        "nuxt",
        "react",
        "svelte",
        "vue",
        "vite",
        "playwright",
        "@playwright/test",
    }

    for path, relative in _iter_project_files(project_root):
        name = path.name
        lower_relative = relative.casefold()
        if name == "AndroidManifest.xml":
            signals["android"].add(relative)
        elif name in {"settings.gradle", "settings.gradle.kts"}:
            text = _small_text(path).casefold()
            if "android" in text or "/android/" in f"/{lower_relative}":
                signals["android"].add(relative)

        if name == "project.pbxproj":
            text = _small_text(path)
            if "IPHONEOS_DEPLOYMENT_TARGET" in text or "SDKROOT = iphoneos" in text:
                signals["ios"].add(relative)
            if "MACOSX_DEPLOYMENT_TARGET" in text or "SDKROOT = macosx" in text:
                signals["desktop"].add(relative)
                desktop_targets.add("macos")

        if name == "package.json":
            try:
                package = json.loads(_small_text(path))
            except (TypeError, json.JSONDecodeError):
                package = {}
            dependencies: set[str] = set()
            if isinstance(package, Mapping):
                for key in ("dependencies", "devDependencies"):
                    values = package.get(key)
                    if isinstance(values, Mapping):
                        dependencies.update(str(item) for item in values)
            if dependencies & web_dependencies:
                signals["web"].add(relative)
            if "electron" in dependencies or "@tauri-apps/api" in dependencies:
                signals["desktop"].add(relative)
                desktop_targets.update({"windows", "macos", "linux"})

        if name in {"tauri.conf.json", "tauri.conf.json5"}:
            signals["desktop"].add(relative)
            desktop_targets.update({"windows", "macos", "linux"})
        if name.endswith((".sln", ".csproj")):
            signals["desktop"].add(relative)
            desktop_targets.add("windows")
        if "/windows/" in f"/{lower_relative}" and name in {
            "CMakeLists.txt",
            "runner.rc",
        }:
            signals["desktop"].add(relative)
            desktop_targets.add("windows")
        if "/linux/" in f"/{lower_relative}" and name == "CMakeLists.txt":
            signals["desktop"].add(relative)
            desktop_targets.add("linux")
        if "/macos/" in f"/{lower_relative}" and name == "project.pbxproj":
            signals["desktop"].add(relative)
            desktop_targets.add("macos")

    platforms: dict[str, Any] = {}
    if signals["android"]:
        platforms["android"] = {"targets": {"emulator": {"required": True}}}
    if signals["ios"]:
        platforms["ios"] = {"targets": {"simulator": {"required": True}}}
    if signals["web"]:
        platforms["web"] = {
            "targets": {
                "desktop_browser": {"required": True},
                "mobile_browser": {"required": True},
            }
        }
    if signals["desktop"]:
        if not desktop_targets:
            desktop_targets.add("linux")
        platforms["desktop"] = {
            "targets": {
                target: {"required": True} for target in sorted(desktop_targets)
            }
        }
    return {
        "platforms": platforms,
        "signals": {
            profile_id: sorted(paths) for profile_id, paths in signals.items() if paths
        },
    }


def resolve_validation_matrix(
    project_root: str | Path,
    project_contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Resolve selected profile targets into an immutable validation matrix."""

    root = Path(project_root).resolve()
    raw_config = project_contract.get("validation")
    if raw_config is None:
        return {
            "schema_version": VALIDATION_MATRIX_VERSION,
            "status": "legacy_unconfigured",
            "mode": "legacy",
            "config_sha256": None,
            "matrix_path": DEFAULT_MATRIX_PATH,
            "report_path": DEFAULT_REPORT_PATH,
            "evidence_root": DEFAULT_EVIDENCE_ROOT,
            "test_identity": {
                "policy": "optional",
                "path": DEFAULT_TEST_IDENTITY_PATH,
            },
            "profiles": [],
            "signals": {},
        }

    config = normalize_validation_config(raw_config)
    mode = config["mode"]
    signals: dict[str, list[str]] = {}
    if mode == "automatic":
        detected = detect_validation_platforms(root)
        selected = detected["platforms"]
        signals = detected["signals"]
    elif mode == "explicit":
        selected = config["platforms"]
    else:
        selected = {}

    profiles: list[dict[str, Any]] = []
    for profile_id in BUILTIN_VALIDATION_PROFILES:
        if profile_id not in selected:
            continue
        definition = BUILTIN_VALIDATION_PROFILES[profile_id]
        raw_targets = selected[profile_id]["targets"]
        targets: list[dict[str, Any]] = []
        for target_definition in definition.targets:
            if target_definition.id not in raw_targets:
                continue
            target = target_definition.as_dict()
            target["required"] = bool(raw_targets[target_definition.id]["required"])
            targets.append(target)
        profiles.append(
            {
                "id": profile_id,
                "label": definition.label,
                "targets": targets,
            }
        )

    if mode == "disabled":
        status = "disabled"
    elif profiles:
        status = "active"
    else:
        status = "not_applicable"
    matrix: dict[str, Any] = {
        "schema_version": VALIDATION_MATRIX_VERSION,
        "status": status,
        "mode": mode,
        "config_sha256": validation_config_digest(config),
        "matrix_path": config["matrix_path"],
        "report_path": config["report_path"],
        "evidence_root": config["evidence_root"],
        "test_identity": config["test_identity"],
        "profiles": profiles,
        "signals": signals,
    }
    if mode == "disabled":
        matrix["reason"] = config["reason"]
    return matrix


def validation_profiles_active(
    project_root: str | Path,
    project_contract: Mapping[str, Any],
) -> bool:
    return (
        resolve_validation_matrix(project_root, project_contract)["status"] == "active"
    )


def safe_project_output(root: str | Path, relative: str) -> Path:
    project_root = Path(root).resolve()
    path = Path(_relative_path(relative, field="validation output"))
    candidate = project_root / path
    if candidate.is_symlink():
        raise ValidationProfileError(
            f"validation output não pode ser link simbólico: {relative}"
        )
    try:
        candidate.parent.resolve().relative_to(project_root)
    except ValueError as exc:
        raise ValidationProfileError(
            f"validation output escapou do projeto: {relative}"
        ) from exc
    return candidate


def write_validation_matrix(
    project_root: str | Path,
    project_contract: Mapping[str, Any],
) -> tuple[Path, dict[str, Any]]:
    """Atomically materialize the deterministic matrix configured by a project."""

    root = Path(project_root).resolve()
    matrix = resolve_validation_matrix(root, project_contract)
    destination = safe_project_output(root, str(matrix["matrix_path"]))
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        fd, raw_path = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
        )
        temporary = Path(raw_path)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            yaml.safe_dump(
                matrix,
                handle,
                allow_unicode=True,
                sort_keys=False,
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return destination, matrix
