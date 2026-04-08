"""
SymGateway integration — registra projetos e provisiona ambiente.

Este módulo é específico da Symlabs e NÃO faz parte do engine genérico.
Extraído de ft/engine/runner.py durante BL-12 (separação base/ambiente).
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from pathlib import Path

SYMGATEWAY_BASE = "https://symgateway.symlabs.ai"


def _gateway_request(method: str, path: str, admin_key: str,
                      payload: dict | None = None) -> dict | None:
    """Faz uma requisição HTTP para o SymGateway com a admin key."""
    url = f"{SYMGATEWAY_BASE}{path}"
    data = json.dumps(payload).encode() if payload else None
    req = urllib.request.Request(
        url, data=data,
        headers={
            "Authorization": f"Bearer {admin_key}",
            "Content-Type": "application/json",
        },
        method=method,
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def _register_gateway_project(admin_key: str, project_name: str,
                               user_key: str | None = None) -> None:
    """Registra o projeto no SymGateway e vincula a key do usuário."""
    # 1. Criar projeto
    project_id = None
    try:
        resp = _gateway_request("POST", "/projects", admin_key, {
            "name": project_name,
            "slug": project_name,
            "folder_name": project_name,
        })
        project_id = resp["id"]
        print(f"  Gateway: projeto '{project_name}' registrado")
    except urllib.error.HTTPError as e:
        if e.code == 409:
            print(f"  Gateway: projeto '{project_name}' já existe — ok")
            try:
                projects = _gateway_request("GET", "/projects", admin_key)
                match = next((p for p in projects if p["folder_name"] == project_name), None)
                if match:
                    project_id = match["id"]
            except Exception:
                pass
        elif e.code == 403:
            raise PermissionError(
                f"Gateway: key sem permissão para criar projetos (role admin necessária).\n"
                f"  → Passe uma admin key com: ft run ... --admin-key <sk-sym_admin_...>"
            )
        else:
            body = e.read().decode(errors="ignore")
            print(f"  Gateway AVISO: registro falhou HTTP {e.code} — {body[:120]}")
            return
    except Exception as e:
        print(f"  Gateway AVISO: não foi possível registrar projeto — {e}")
        return

    # 2 + 3. Vincular user_key ao projeto
    if not user_key or not project_id:
        return

    try:
        keys = _gateway_request("GET", "/api-keys", admin_key)
        key_uuid = next(
            (k["id"] for k in keys
             if user_key.startswith(k["key_prefix"])
             or user_key.startswith(k["key_prefix"].removeprefix("sk-"))),
            None,
        )
        if not key_uuid:
            print(f"  Gateway AVISO: key do usuário não encontrada na listagem — link não feito")
            return

        _gateway_request("POST", f"/projects/{project_id}/api-keys/link",
                         admin_key, {"api_key_id": key_uuid})
        print(f"  Gateway: key do usuário vinculada ao projeto '{project_name}'")
    except urllib.error.HTTPError as e:
        if e.code == 409:
            print(f"  Gateway: key já vinculada ao projeto — ok")
        else:
            body = e.read().decode(errors="ignore")
            print(f"  Gateway AVISO: link da key falhou HTTP {e.code} — {body[:120]}")
    except Exception as e:
        print(f"  Gateway AVISO: não foi possível vincular key — {e}")


def _read_gateway_md(ft_root: Path | None = None) -> dict[str, str]:
    """Lê environment/gateway.md e extrai campos **KEY**: value."""
    if ft_root is None:
        ft_root = Path(__file__).resolve().parent.parent.parent
    gateway_file = ft_root / "environment" / "gateway.md"
    if not gateway_file.exists():
        return {}
    fields: dict[str, str] = {}
    for line in gateway_file.read_text().splitlines():
        m = re.match(r"-\s+\*\*([^*]+)\*\*\s*:\s*(\S+)", line)
        if m:
            fields[m.group(1).strip()] = m.group(2).strip()
    return fields


def provision_environment(project_root: Path, base_url: str | None = None,
                          key: str | None = None, admin_key: str | None = None) -> None:
    """Cria CLAUDE.md e .claude/settings.local.json no project_root.

    Lê credenciais de env vars (SYM_GATEWAY_PROJECT_KEY, SYM_GATEWAY_ADMIN_KEY)
    se não fornecidas como argumento.
    """
    import os
    project_name = project_root.name

    # Env vars têm prioridade sobre argumentos legados
    key = key or os.environ.get("SYM_GATEWAY_PROJECT_KEY")
    admin_key = admin_key or os.environ.get("SYM_GATEWAY_ADMIN_KEY")

    if key and not base_url:
        # Embutir /s/<slug> na URL para que o gateway identifique o projeto
        # independente do CWD (necessário para modo isolated com runs/<N>/).
        base_url = f"{SYMGATEWAY_BASE}/u/{key}/p/anthropic-max/s/{project_name}"

    if not admin_key:
        gw = _read_gateway_md()
        admin_key = gw.get("GATEWAY_ADMIN_KEY")

    reg_key = admin_key or key
    if reg_key:
        _register_gateway_project(reg_key, project_name, user_key=key)

    # CLAUDE.md
    claude_md = project_root / "CLAUDE.md"
    if not claude_md.exists():
        claude_md.write_text(f"gateway_project: {project_name}\n")
        print(f"  Criado: CLAUDE.md (gateway_project: {project_name})")
    else:
        existing = claude_md.read_text()
        if "gateway_project" not in existing:
            claude_md.write_text(f"gateway_project: {project_name}\n{existing}")
            print(f"  Atualizado: CLAUDE.md (gateway_project: {project_name})")

    # .claude/settings.local.json
    if base_url:
        dot_claude = project_root / ".claude"
        dot_claude.mkdir(exist_ok=True)
        settings_file = dot_claude / "settings.local.json"
        settings = {"env": {"ANTHROPIC_BASE_URL": base_url}}
        settings_file.write_text(json.dumps(settings, indent=2) + "\n")
        print(f"  Criado: .claude/settings.local.json (ANTHROPIC_BASE_URL configurado)")


def check_gateway_403(output: str) -> str | None:
    """Detecta erro 403 do SymGateway na saída do LLM. Retorna mensagem ou None."""
    if "403" in output and "not found in workspace" in output:
        m = re.search(r"folder_name='([^']+)'", output)
        folder = m.group(1) if m else "este projeto"
        return (
            f"Gateway 403: projeto '{folder}' não está registrado no SymGateway.\n"
            f"  → Registre com: ft setup-env <sua-key>"
        )
    return None
