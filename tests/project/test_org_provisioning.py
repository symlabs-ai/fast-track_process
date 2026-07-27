"""Real-shell contracts for transactional organization provisioning."""

from __future__ import annotations

import os
from pathlib import Path
import stat

import pytest

from ft.project.init_scripts import (
    execute_and_record_init_template,
    InitScriptError,
    read_init_marker,
)
from ft.templates import TemplateCatalog


def _executable(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _fake_tools(tmp_path: Path) -> tuple[Path, Path]:
    binary = tmp_path / "bin"
    binary.mkdir()
    log = tmp_path / "curl-argv.log"
    _executable(
        binary / "curl",
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "${FAKE_CURL_LOG:?}"
args="$*"
if [ "${FAKE_NETWORK_FAILURE:-0}" = "1" ]; then
  exit 7
fi
case "$args" in
  *"?status=all"*)
    if [ "${FAKE_LIST_FAILURE:-0}" = "1" ]; then
      exit 6
    fi
    printf '[{"slug":"%s","folder_name":"%s","id":"project-id"}]' \
      "${FAKE_PROJECT_NAME:?}" "${FAKE_EXISTING_FOLDER:-${FAKE_PROJECT_NAME}}"
    ;;
  *"/api-keys/link"*)
    printf '%s' "${FAKE_LINK_CODE:-201}"
    ;;
  *)
    printf '%s' "${FAKE_CREATE_CODE:-201}"
    ;;
esac
""",
    )
    _executable(binary / "poetry", "#!/usr/bin/env bash\nexit 0\n")
    return binary, log


def _org_config(config_root: Path, org: str) -> None:
    prefix = org.upper().replace("-", "_")
    config_root.mkdir()
    (config_root / f"{org}.env").write_text(
        "\n".join(
            [
                f"{prefix}_GATEWAY_URL=https://gateway.example.invalid",
                f"{prefix}_WORKSPACE_ID=workspace-id",
                f"{prefix}_PROVIDER_PATH=provider",
                f"{prefix}_ADMIN_KEY=admin-secret",
                f"{prefix}_CALLER_KEY=caller-secret",
                f"{prefix}_CALLER_KEY_ID=caller-id",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _configure(
    monkeypatch,
    *,
    binary: Path,
    log: Path,
    config_root: Path,
    project_name: str,
    create_code: str = "201",
    link_code: str = "201",
) -> None:
    monkeypatch.setenv("PATH", f"{binary}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FT_ORG_CONFIG_ROOT", str(config_root))
    monkeypatch.setenv("FAKE_CURL_LOG", str(log))
    monkeypatch.setenv("FAKE_PROJECT_NAME", project_name)
    monkeypatch.setenv("FAKE_CREATE_CODE", create_code)
    monkeypatch.setenv("FAKE_LINK_CODE", link_code)


@pytest.mark.parametrize("org", ["symlabs", "tecnospeed"])
def test_org_template_records_marker_only_after_remote_confirmation(
    tmp_path: Path,
    monkeypatch,
    org: str,
) -> None:
    project = tmp_path / "demo-project"
    project.mkdir()
    config = tmp_path / "config"
    _org_config(config, org)
    binary, log = _fake_tools(tmp_path)
    _configure(
        monkeypatch,
        binary=binary,
        log=log,
        config_root=config,
        project_name=project.name,
    )
    descriptor = TemplateCatalog().get_init(org)

    execute_and_record_init_template(descriptor, project)

    assert org in read_init_marker(project)
    assert "gateway_project: demo-project" in (
        project / "CLAUDE.md"
    ).read_text(encoding="utf-8")
    settings = (project / ".claude" / "settings.local.json").read_text(
        encoding="utf-8"
    )
    assert "/u/caller-secret/p/provider/s/demo-project" in settings
    curl_argv = log.read_text(encoding="utf-8")
    assert "admin-secret" not in curl_argv
    assert "--header @" in curl_argv


@pytest.mark.parametrize("status", ["401", "403", "500", "000"])
def test_gateway_registration_failure_blocks_marker_and_local_routing(
    tmp_path: Path,
    monkeypatch,
    status: str,
) -> None:
    project = tmp_path / "demo-project"
    project.mkdir()
    config = tmp_path / "config"
    _org_config(config, "symlabs")
    binary, log = _fake_tools(tmp_path)
    _configure(
        monkeypatch,
        binary=binary,
        log=log,
        config_root=config,
        project_name=project.name,
        create_code=status,
    )
    descriptor = TemplateCatalog().get_init("symlabs")

    with pytest.raises(InitScriptError, match="init não concluído|NÃO registrado"):
        execute_and_record_init_template(descriptor, project)

    assert read_init_marker(project) == {}
    assert not (project / "CLAUDE.md").exists()
    assert not (project / ".claude" / "settings.local.json").exists()


def test_gateway_network_failure_blocks_marker(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "demo-project"
    project.mkdir()
    config = tmp_path / "config"
    _org_config(config, "symlabs")
    binary, log = _fake_tools(tmp_path)
    _configure(
        monkeypatch,
        binary=binary,
        log=log,
        config_root=config,
        project_name=project.name,
    )
    monkeypatch.setenv("FAKE_NETWORK_FAILURE", "1")

    with pytest.raises(InitScriptError, match="falha de rede"):
        execute_and_record_init_template(
            TemplateCatalog().get_init("symlabs"),
            project,
        )

    assert read_init_marker(project) == {}


def test_gateway_conflict_requires_confirmed_matching_owner(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = tmp_path / "demo-project"
    project.mkdir()
    config = tmp_path / "config"
    _org_config(config, "symlabs")
    binary, log = _fake_tools(tmp_path)
    _configure(
        monkeypatch,
        binary=binary,
        log=log,
        config_root=config,
        project_name=project.name,
        create_code="409",
    )
    monkeypatch.setenv("FAKE_EXISTING_FOLDER", "another-project")

    with pytest.raises(InitScriptError, match="outro projeto"):
        execute_and_record_init_template(
            TemplateCatalog().get_init("symlabs"),
            project,
        )

    assert read_init_marker(project) == {}


def test_caller_link_failure_blocks_marker_and_local_routing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = tmp_path / "demo-project"
    project.mkdir()
    config = tmp_path / "config"
    _org_config(config, "symlabs")
    binary, log = _fake_tools(tmp_path)
    _configure(
        monkeypatch,
        binary=binary,
        log=log,
        config_root=config,
        project_name=project.name,
        link_code="403",
    )

    with pytest.raises(InitScriptError, match="link da caller retornou HTTP 403"):
        execute_and_record_init_template(
            TemplateCatalog().get_init("symlabs"),
            project,
        )

    assert read_init_marker(project) == {}
    assert not (project / ".claude" / "settings.local.json").exists()
