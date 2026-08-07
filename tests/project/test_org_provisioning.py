"""Real-shell contracts for transactional organization provisioning."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import tomllib

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
output_file=""
want_output="0"
for arg in "$@"; do
  if [ "$want_output" = "1" ]; then
    output_file="$arg"
    want_output="0"
  elif [ "$arg" = "-o" ] || [ "$arg" = "--output" ]; then
    want_output="1"
  fi
done
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
  *"/projects/project-id/api-keys"*)
    if [[ "$args" == *"-X POST"* ]]; then
      key="${FAKE_CALLER_KEY:-sk-sym_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA}"
      if [ -n "$output_file" ] && [ "$output_file" != "/dev/null" ]; then
        printf '{"id":"dedicated-caller-id","name":"ft-%s","role":"caller","key":"%s","key_prefix":"%s","created_at":"2026-08-01T00:00:00"}' \
          "${FAKE_PROJECT_NAME:?}" "$key" "${key:0:12}" > "$output_file"
      fi
      printf '%s' "${FAKE_KEY_CREATE_CODE:-201}"
    else
      printf '%s' "${FAKE_PROJECT_KEYS_JSON:-[]}"
    fi
    ;;
  *"/api-keys"*)
    printf '%s' "${FAKE_ALL_KEYS_JSON:-[]}"
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
    values = [
        f"{prefix}_GATEWAY_URL=https://gateway.example.invalid",
        f"{prefix}_WORKSPACE_ID=workspace-id",
        f"{prefix}_ADMIN_KEY=admin-secret",
    ]
    if org == "symlabs":
        values.append(f"{prefix}_ANTHROPIC_PROVIDER_PATH=anthropic-max")
    else:
        values.extend(
            [
                f"{prefix}_PROVIDER_PATH=provider",
                f"{prefix}_CALLER_KEY=caller-secret",
                f"{prefix}_CALLER_KEY_ID=caller-id",
            ]
        )
    (config_root / f"{org}.env").write_text(
        "\n".join([*values, ""]),
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
    key_create_code: str = "201",
) -> None:
    monkeypatch.setenv("PATH", f"{binary}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FT_ORG_CONFIG_ROOT", str(config_root))
    monkeypatch.setenv("FAKE_CURL_LOG", str(log))
    monkeypatch.setenv("FAKE_PROJECT_NAME", project_name)
    monkeypatch.setenv("FAKE_CREATE_CODE", create_code)
    monkeypatch.setenv("FAKE_LINK_CODE", link_code)
    monkeypatch.setenv("FAKE_KEY_CREATE_CODE", key_create_code)
    monkeypatch.setenv("CODEX_HOME", str(binary.parent / "codex-home"))


def test_tecnospeed_template_records_marker_only_after_remote_confirmation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    org = "tecnospeed"
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


def test_symlabs_template_configures_codex_and_claude_with_dedicated_key(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = tmp_path / "demo-project"
    project.mkdir()
    (project / ".gitignore").write_text(".env\n", encoding="utf-8")
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

    execute_and_record_init_template(
        TemplateCatalog().get_init("symlabs"),
        project,
    )

    assert "symlabs" in read_init_marker(project)
    assert "gateway_project: demo-project" in (
        project / "CLAUDE.md"
    ).read_text(encoding="utf-8")

    caller_key = "sk-sym_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    private = project / ".envrc.private"
    assert private.read_text(encoding="utf-8") == (
        f'export SYMGATEWAY_API_KEY="{caller_key}"\n'
    )
    assert stat.S_IMODE(private.stat().st_mode) == 0o600
    assert ".envrc.private" in (project / ".gitignore").read_text(encoding="utf-8")

    envrc = (project / ".envrc").read_text(encoding="utf-8")
    assert 'export SYMGATEWAY_PROJECT_SLUG="demo-project"' in envrc
    assert 'export FT_LLM_ENGINE="codex"' in envrc
    assert 'export FT_CODEX_PROFILE="symgateway-dev"' in envrc
    assert "source_env_if_exists .envrc.private" in envrc
    assert caller_key not in envrc

    settings = json.loads(
        (project / ".claude" / "settings.local.json").read_text(encoding="utf-8")
    )
    assert settings == {
        "env": {
            "ANTHROPIC_BASE_URL": (
                "https://gateway.example.invalid/p/anthropic-max/s/demo-project"
            ),
            "ANTHROPIC_API_KEY": caller_key,
        }
    }
    assert stat.S_IMODE(
        (project / ".claude" / "settings.local.json").stat().st_mode
    ) == 0o600

    agents = (project / "AGENTS.md").read_text(encoding="utf-8")
    assert agents.count("<!-- symlabs-symgateway:start -->") == 1
    assert agents.count("<!-- symlabs-symgateway:end -->") == 1
    assert "LLMs via SymGateway por default" in agents
    assert "`codex_auth: chatgpt`" in agents
    assert "A exceção cobre o node inteiro" in " ".join(agents.split())
    assert "Não execute `claude auth login`" in agents
    assert "Não crie bypass ad hoc" in agents

    profile_path = tmp_path / "codex-home" / "symgateway-dev.config.toml"
    with profile_path.open("rb") as source:
        profile = tomllib.load(source)
    assert profile["model_provider"] == "symgateway_openai_dev"
    assert profile["model"] == "gpt-5.6-sol"
    provider = profile["model_providers"]["symgateway_openai_dev"]
    assert provider == {
        "name": "SymGateway OpenAI OAuth — Symlabs DEV",
        "base_url": "https://gateway.example.invalid/p/openai/v1",
        "env_key": "SYMGATEWAY_API_KEY",
        "env_http_headers": {"X-Project-Slug": "SYMGATEWAY_PROJECT_SLUG"},
        "wire_api": "responses",
        "supports_websockets": False,
    }

    curl_argv = log.read_text(encoding="utf-8")
    assert "admin-secret" not in curl_argv
    assert caller_key not in curl_argv
    assert "/projects/project-id/api-keys" in curl_argv
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
    _org_config(config, "tecnospeed")
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
            TemplateCatalog().get_init("tecnospeed"),
            project,
        )

    assert read_init_marker(project) == {}
    assert not (project / ".claude" / "settings.local.json").exists()


def test_symlabs_dedicated_caller_failure_blocks_local_secrets(
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
        key_create_code="403",
    )

    with pytest.raises(InitScriptError, match="criação da caller retornou HTTP 403"):
        execute_and_record_init_template(
            TemplateCatalog().get_init("symlabs"),
            project,
        )

    assert read_init_marker(project) == {}
    assert not (project / ".envrc.private").exists()
    assert not (project / ".claude" / "settings.local.json").exists()


def test_symlabs_refuses_to_duplicate_remote_key_when_local_secret_is_missing(
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
    )
    monkeypatch.setenv(
        "FAKE_PROJECT_KEYS_JSON",
        '[{"id":"dedicated-caller-id","name":"ft-demo-project",'
        '"role":"caller","key_prefix":"sk-sym_AAAAA","status":"active"}]',
    )

    with pytest.raises(InitScriptError, match="já existe.*envrc.private está ausente"):
        execute_and_record_init_template(
            TemplateCatalog().get_init("symlabs"),
            project,
        )

    assert read_init_marker(project) == {}
    assert not (project / ".envrc.private").exists()
    create_endpoint = (
        "-X POST https://gateway.example.invalid/_api/projects/"
        "project-id/api-keys "
    )
    assert create_endpoint not in log.read_text(encoding="utf-8")


def test_symlabs_reuses_confirmed_dedicated_key_on_fix(
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
    )
    descriptor = TemplateCatalog().get_init("symlabs")

    execute_and_record_init_template(descriptor, project)
    private_before = (project / ".envrc.private").read_bytes()
    monkeypatch.setenv(
        "FAKE_PROJECT_KEYS_JSON",
        '[{"id":"dedicated-caller-id","name":"ft-demo-project",'
        '"role":"caller","key_prefix":"sk-sym_AAAAA","status":"active"}]',
    )

    execute_and_record_init_template(descriptor, project, mode="fix", adopt=True)

    assert (project / ".envrc.private").read_bytes() == private_before
    agents = (project / "AGENTS.md").read_text(encoding="utf-8")
    assert agents.count("<!-- symlabs-symgateway:start -->") == 1
    assert agents.count("<!-- symlabs-symgateway:end -->") == 1
    create_endpoint = (
        "-X POST https://gateway.example.invalid/_api/projects/"
        "project-id/api-keys "
    )
    assert log.read_text(encoding="utf-8").count(create_endpoint) == 1
