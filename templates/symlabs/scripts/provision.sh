#!/usr/bin/env bash
# Provisiona um projeto no ambiente da Symlabs.
# A config da organização vive em `environment/symlabs.env` no repo do engine
# (gitignored); credenciais por projeto ficam somente no próprio checkout.
#
# Passos: (1) carrega e valida a config da org; (2) scaffold Poetry/src;
# (3) registra o projeto e cria uma caller dedicada; (4) configura Claude e
# Codex/OpenAI exclusivamente via SymGateway; (5) registra esse contrato no
# AGENTS.md do projeto.
#
# Idempotente: não sobrescreve arquivos de produto; 409 no gateway = ok.
set -euo pipefail

PROJECT_ROOT="${FT_PROJECT_ROOT:-$PWD}"
PROJECT_NAME="$(basename "$PROJECT_ROOT")"
PKG_NAME="$(printf '%s' "$PROJECT_NAME" | tr '-' '_' | tr -cd 'a-zA-Z0-9_')"

# ---------------------------------------------------------------------------
# 1. Config da organização — environment/<org>.env no repo do engine
# ---------------------------------------------------------------------------
ORG="$(basename "${FT_TEMPLATE_DIR:?FT_TEMPLATE_DIR ausente}")"
ORG_UPPER="$(printf '%s' "$ORG" | tr '[:lower:]-' '[:upper:]_')"
CONFIG_ROOT="${FT_ORG_CONFIG_ROOT:-${FT_ENGINE_ROOT:?FT_ENGINE_ROOT ausente}/environment}"
ENV_FILE="${CONFIG_ROOT}/${ORG}.env"

if [ ! -f "$ENV_FILE" ]; then
  echo "  ✗ Config da organização '${ORG}' não encontrada: ${ENV_FILE}" >&2
  echo "    Crie a partir do exemplo:" >&2
  echo "      cp environment/${ORG}.env.example environment/${ORG}.env" >&2
  echo "    e preencha workspace e admin key (peça ao DevOps: /ask devops)." >&2
  exit 1
fi

# shellcheck disable=SC1090
set -a; . "$ENV_FILE"; set +a

# Resolve variáveis com prefixo da org (ex.: SYMLABS_WORKSPACE_ID)
_get() { eval "printf '%s' \"\${${ORG_UPPER}_$1:-}\""; }
GATEWAY_URL="$(_get GATEWAY_URL)"
WORKSPACE_ID="$(_get WORKSPACE_ID)"
ANTHROPIC_PROVIDER_PATH="$(_get ANTHROPIC_PROVIDER_PATH)"
[ -n "$ANTHROPIC_PROVIDER_PATH" ] || ANTHROPIC_PROVIDER_PATH="$(_get PROVIDER_PATH)"
ADMIN_KEY="$(_get ADMIN_KEY)"

# Placeholder = não provisionado. Falha alto e cedo.
missing=""
for var in GATEWAY_URL WORKSPACE_ID ANTHROPIC_PROVIDER_PATH ADMIN_KEY; do
  val="$(eval "printf '%s' \"\$$var\"")"
  case "$val" in
    ""|CHANGE_ME*|"<"*) missing="${missing} ${ORG_UPPER}_${var}" ;;
  esac
done
if [ -n "$missing" ]; then
  echo "  ✗ Organização '${ORG}' não provisionada — faltam:${missing}" >&2
  echo "    Preencha ${ENV_FILE} (peça ao DevOps: /ask devops)." >&2
  exit 1
fi

echo "  → Organização: ${ORG} (workspace ${WORKSPACE_ID})"

# ---------------------------------------------------------------------------
# 2. Scaffold Poetry / estrutura de código (não sobrescreve o que existe)
# ---------------------------------------------------------------------------
cd "$PROJECT_ROOT"

if [ ! -e pyproject.toml ]; then
  cat > pyproject.toml <<EOF
[tool.poetry]
name = "${PROJECT_NAME}"
version = "0.0.1"
description = ""
authors = ["Symlabs <dev@symlabs.ai>"]
packages = [{ include = "${PKG_NAME}", from = "src" }]

[tool.poetry.dependencies]
python = "^3.12"

[build-system]
requires = ["poetry-core"]
build-backend = "poetry.core.masonry.api"
EOF
  echo "  ✓ criado pyproject.toml (v0.0.1)"
fi

if [ ! -e "src/${PKG_NAME}/__init__.py" ]; then
  mkdir -p "src/${PKG_NAME}"
  printf '__version__ = "0.0.1"\n' > "src/${PKG_NAME}/__init__.py"
  echo "  ✓ criado src/${PKG_NAME}/"
fi
[ -e docs/.gitkeep ] || { mkdir -p docs; : > docs/.gitkeep; }
[ -e tests/.gitkeep ] || { mkdir -p tests; : > tests/.gitkeep; }

# .env de dev (sem segredos — .envrc.private e settings.local.json cuidam do
# roteamento local dos agentes).
# PORT é responsabilidade do DevOps (/ask devops); não alocamos aqui.
if [ ! -e .env ]; then
  cat > .env <<EOF
DEV_MODE=true
# PORT= (peça alocação ao DevOps: /ask devops)
EOF
  echo "  ✓ criado .env (DEV_MODE=true)"
fi

# Poetry install best-effort (não bloqueia o init se o poetry faltar).
if command -v poetry >/dev/null 2>&1; then
  poetry config virtualenvs.in-project true --local >/dev/null 2>&1 || true
  if poetry install >/dev/null 2>&1; then
    echo "  ✓ poetry install ok (.venv/)"
  else
    echo "  ⚠ poetry install falhou — rode manualmente depois"
  fi
else
  echo "  ⚠ poetry não encontrado — instale e rode 'poetry install'"
fi

# ---------------------------------------------------------------------------
# 3. Codex — perfil global compartilhado (sem chave nem slug de projeto)
# ---------------------------------------------------------------------------
SLUG="$PROJECT_NAME"
if [[ ! "$SLUG" =~ ^[a-z0-9][a-z0-9_-]*$ ]]; then
  echo "  ✗ Nome de diretório inválido para slug do SymGateway: '${SLUG}'" >&2
  echo "    Use apenas minúsculas, números, hífen e underscore." >&2
  exit 1
fi

CODEX_PROFILE_NAME="symgateway-dev"
CODEX_CONFIG_ROOT="${CODEX_HOME:-${HOME:?HOME ausente}/.codex}"
CODEX_PROFILE_FILE="${CODEX_CONFIG_ROOT}/${CODEX_PROFILE_NAME}.config.toml"
CODEX_OPENAI_BASE_URL="${GATEWAY_URL%/}/p/openai/v1"
CODEX_PROFILE_TEMP=""
AUTH_HEADER_FILE=""
KEY_RESPONSE_FILE=""
PRIVATE_TEMP=""

_cleanup() {
  local file
  for file in "$CODEX_PROFILE_TEMP" "$AUTH_HEADER_FILE" "$KEY_RESPONSE_FILE" "$PRIVATE_TEMP"; do
    if [ -n "$file" ]; then
      rm -f -- "$file"
    fi
  done
  return 0
}
trap _cleanup EXIT

_validate_codex_profile() {
  python3 - "$CODEX_PROFILE_FILE" "$CODEX_OPENAI_BASE_URL" <<'PY'
import sys
import tomllib

path, base_url = sys.argv[1:]
try:
    with open(path, "rb") as source:
        data = tomllib.load(source)
except (OSError, tomllib.TOMLDecodeError) as exc:
    print(f"  ✗ Perfil Codex inválido em {path}: {exc}", file=sys.stderr)
    raise SystemExit(1)

provider = (data.get("model_providers") or {}).get("symgateway_openai_dev") or {}
# O contrato validado é a fiação do gateway (provider, base_url, chaves e
# protocolo). Defaults de modelo/effort são preferência do usuário: um perfil
# com `model` customizado continua válido e não deve bloquear o ft init.
expected = {
    "model_provider": "symgateway_openai_dev",
    "provider.name": "SymGateway OpenAI OAuth — Symlabs DEV",
    "provider.base_url": base_url,
    "provider.env_key": "SYMGATEWAY_API_KEY",
    "provider.env_http_headers": {"X-Project-Slug": "SYMGATEWAY_PROJECT_SLUG"},
    "provider.wire_api": "responses",
    "provider.supports_websockets": False,
}
actual = {
    "model_provider": data.get("model_provider"),
    "provider.name": provider.get("name"),
    "provider.base_url": provider.get("base_url"),
    "provider.env_key": provider.get("env_key"),
    "provider.env_http_headers": provider.get("env_http_headers"),
    "provider.wire_api": provider.get("wire_api"),
    "provider.supports_websockets": provider.get("supports_websockets"),
}
mismatches = [key for key, value in expected.items() if actual.get(key) != value]
model = data.get("model")
if not mismatches and model and model != "gpt-5.6-sol":
    print(f"  → perfil Codex mantém model default local: {model}")
if mismatches:
    print(
        f"  ✗ Perfil Codex existente incompatível em {path}: " + ", ".join(mismatches),
        file=sys.stderr,
    )
    print("    Corrija-o ou mova-o antes de repetir o ft init.", file=sys.stderr)
    raise SystemExit(1)
PY
}

if [ -L "$CODEX_PROFILE_FILE" ]; then
  echo "  ✗ Perfil Codex não pode ser link simbólico: ${CODEX_PROFILE_FILE}" >&2
  exit 1
elif [ -e "$CODEX_PROFILE_FILE" ]; then
  _validate_codex_profile
  echo "  → perfil Codex global '${CODEX_PROFILE_NAME}' já configurado — ok"
else
  mkdir -p "$CODEX_CONFIG_ROOT"
  CODEX_PROFILE_TEMP="$(mktemp "${CODEX_CONFIG_ROOT}/.${CODEX_PROFILE_NAME}.XXXXXX")"
  chmod 600 "$CODEX_PROFILE_TEMP"
  cat > "$CODEX_PROFILE_TEMP" <<EOF
model_provider = "symgateway_openai_dev"
model = "gpt-5.6-sol"

[model_providers.symgateway_openai_dev]
name = "SymGateway OpenAI OAuth — Symlabs DEV"
base_url = "${CODEX_OPENAI_BASE_URL}"
env_key = "SYMGATEWAY_API_KEY"
env_http_headers = { "X-Project-Slug" = "SYMGATEWAY_PROJECT_SLUG" }
wire_api = "responses"
supports_websockets = false
EOF
  mv -- "$CODEX_PROFILE_TEMP" "$CODEX_PROFILE_FILE"
  CODEX_PROFILE_TEMP=""
  echo "  ✓ criado perfil Codex global '${CODEX_PROFILE_NAME}'"
fi

# ---------------------------------------------------------------------------
# 4. SymGateway — registra projeto e garante uma caller exclusiva
# ---------------------------------------------------------------------------
API="${GATEWAY_URL%/}/_api"
AUTH_HEADER_FILE="$(mktemp "${TMPDIR:-/tmp}/ft-gateway-headers.XXXXXX")"
KEY_RESPONSE_FILE="$(mktemp "${TMPDIR:-/tmp}/ft-gateway-key.XXXXXX")"
chmod 600 "$AUTH_HEADER_FILE" "$KEY_RESPONSE_FILE"
printf 'Authorization: Bearer %s\nX-Workspace-ID: %s\n' \
  "$ADMIN_KEY" "$WORKSPACE_ID" > "$AUTH_HEADER_FILE"
curl_common=(
  --silent
  --show-error
  --connect-timeout 10
  --max-time 30
  --header "@${AUTH_HEADER_FILE}"
)

_project_field() {
  python3 -c '
import sys, json
try:
    data = json.loads(sys.argv[1] or "[]")
except Exception:
    sys.exit(0)
items = data if isinstance(data, list) else (
    data.get("projects") or data.get("items") or data.get("data") or [])
for item in items:
    if isinstance(item, dict) and item.get("slug") == sys.argv[2]:
        print(item.get(sys.argv[3], "") or "")
        break
' "$1" "$2" "$3" 2>/dev/null
}

# As listagens de keys nunca contêm o segredo bruto.
_api_key_field() {
  python3 -c '
import sys, json
try:
    data = json.loads(sys.argv[1] or "[]")
except Exception:
    sys.exit(0)
items = data if isinstance(data, list) else (
    data.get("api_keys") or data.get("items") or data.get("data") or [])
name, prefix, field = sys.argv[2:]
for item in items:
    if not isinstance(item, dict):
        continue
    if name and item.get("name") != name:
        continue
    if prefix and item.get("key_prefix") != prefix:
        continue
    print(item.get(field, "") or "")
    break
' "$1" "$2" "$3" "$4" 2>/dev/null
}

_created_key_field() {
  python3 -c '
import sys, json
try:
    with open(sys.argv[1], encoding="utf-8") as source:
        data = json.load(source)
except Exception:
    sys.exit(0)
if isinstance(data, dict):
    print(data.get(sys.argv[2], "") or "")
' "$KEY_RESPONSE_FILE" "$1" 2>/dev/null
}

_read_private_key() {
  python3 - "$1" <<'PY'
import re
import sys

pattern = re.compile(
    r'^\s*export\s+SYMGATEWAY_API_KEY\s*=\s*'
    r'(?:(?:"([^"]*)")|(?:\x27([^\x27]*)\x27)|([^\s#]+))\s*(?:#.*)?$'
)
values = []
try:
    lines = open(sys.argv[1], encoding="utf-8").read().splitlines()
except OSError:
    raise SystemExit(0)
for line in lines:
    match = pattern.match(line)
    if match:
        values.append(next(value for value in match.groups() if value is not None))
if len(values) == 1:
    print(values[0])
PY
}

_valid_caller_key() {
  [[ "$1" =~ ^sk-sym_[A-Za-z0-9]{20,}$ ]]
}

plist=""
_load_projects() {
  if [ -n "$plist" ]; then
    return 0
  fi
  if ! plist="$(curl "${curl_common[@]}" --fail "${API}/projects?status=all")"; then
    echo "  ✗ SymGateway: falha ao consultar projetos; provisionamento não confirmado" >&2
    return 1
  fi
}

project_payload="$(python3 -c '
import json, sys
print(json.dumps({"name": sys.argv[1], "slug": sys.argv[2], "folder_name": sys.argv[1]}))
' "$PROJECT_NAME" "$SLUG")"
if ! code="$(curl "${curl_common[@]}" -o /dev/null -w '%{http_code}' \
  -X POST "${API}/projects" -H "Content-Type: application/json" \
  --data "$project_payload")"; then
  echo "  ✗ SymGateway: falha de rede ao registrar projeto; init não concluído" >&2
  exit 1
fi
code="${code:-000}"
case "$code" in
  2??) echo "  ✓ SymGateway: projeto '${SLUG}' registrado" ;;
  409)
    _load_projects || exit 1
    existing_folder="$(_project_field "$plist" "$SLUG" folder_name)"
    if [ -z "$existing_folder" ]; then
      echo "  ✗ SymGateway: slug '${SLUG}' retornou 409, mas não foi possível confirmar ownership" >&2
      exit 1
    fi
    if [ "$existing_folder" != "$PROJECT_NAME" ]; then
      echo "  ✗ SymGateway: slug '${SLUG}' já pertence a outro projeto no workspace" >&2
      echo "    (folder_name='${existing_folder}', esperado '${PROJECT_NAME}')." >&2
      echo "    Renomeie o diretório ou use outro slug — não vou adotar projeto alheio." >&2
      exit 1
    fi
    echo "  → SymGateway: projeto '${SLUG}' já existe — ok"
    ;;
  401|403)
    echo "  ✗ SymGateway: sem permissão (HTTP ${code}) — ADMIN_KEY inválida; projeto NÃO registrado" >&2
    exit 1
    ;;
  000)
    echo "  ✗ SymGateway: sem resposta da API — init não concluído" >&2
    exit 1
    ;;
  *)
    echo "  ✗ SymGateway: registro retornou HTTP ${code} — init não concluído" >&2
    exit 1
    ;;
esac

_load_projects || exit 1
pid="$(_project_field "$plist" "$SLUG" id)"
if [ -z "$pid" ]; then
  echo "  ✗ SymGateway: projeto '${SLUG}' sem id; caller dedicada não pôde ser criada" >&2
  exit 1
fi

if [ ! -e .gitignore ]; then
  : > .gitignore
fi
if ! grep -Fqx '.envrc.private' .gitignore; then
  printf '\n# Segredo local do SymGateway (nunca versionar).\n.envrc.private\n' >> .gitignore
  echo "  ✓ .envrc.private adicionado ao .gitignore"
fi

CALLER_NAME="ft-${SLUG}"
if ! project_keys="$(curl "${curl_common[@]}" --fail "${API}/projects/${pid}/api-keys")"; then
  echo "  ✗ SymGateway: falha ao consultar callers do projeto" >&2
  exit 1
fi

PROJECT_CALLER_KEY=""
if [ -L .envrc.private ]; then
  echo "  ✗ .envrc.private não pode ser link simbólico" >&2
  exit 1
elif [ -e .envrc.private ]; then
  PROJECT_CALLER_KEY="$(_read_private_key .envrc.private)"
  if ! _valid_caller_key "$PROJECT_CALLER_KEY"; then
    echo "  ✗ .envrc.private não contém exatamente uma SYMGATEWAY_API_KEY válida" >&2
    exit 1
  fi
  key_prefix="${PROJECT_CALLER_KEY:0:12}"
  linked_id="$(_api_key_field "$project_keys" "$CALLER_NAME" "$key_prefix" id)"
  linked_role="$(_api_key_field "$project_keys" "$CALLER_NAME" "$key_prefix" role)"
  linked_status="$(_api_key_field "$project_keys" "$CALLER_NAME" "$key_prefix" status)"
  same_name_prefix="$(_api_key_field "$project_keys" "$CALLER_NAME" "" key_prefix)"
  if [ -n "$same_name_prefix" ] && [ "$same_name_prefix" != "$key_prefix" ]; then
    echo "  ✗ Caller '${CALLER_NAME}' foi rotacionada remotamente; a chave local está obsoleta" >&2
    echo "    Restaure o segredo atual em .envrc.private antes de repetir o init." >&2
    exit 1
  fi
  if [ -n "$linked_id" ] \
    && { [ "$linked_role" != "caller" ] || [ "$linked_status" != "active" ]; }; then
    echo "  ✗ Caller '${CALLER_NAME}' vinculada não está ativa com role caller" >&2
    exit 1
  elif [ -z "$linked_id" ]; then
    if ! all_keys="$(curl "${curl_common[@]}" --fail "${API}/api-keys")"; then
      echo "  ✗ SymGateway: falha ao confirmar caller local no workspace" >&2
      exit 1
    fi
    caller_id="$(_api_key_field "$all_keys" "$CALLER_NAME" "$key_prefix" id)"
    caller_role="$(_api_key_field "$all_keys" "$CALLER_NAME" "$key_prefix" role)"
    caller_status="$(_api_key_field "$all_keys" "$CALLER_NAME" "$key_prefix" status)"
    if [ -z "$caller_id" ] || [ "$caller_role" != "caller" ] || [ "$caller_status" != "active" ]; then
      echo "  ✗ A SYMGATEWAY_API_KEY local não é uma caller ativa deste workspace" >&2
      exit 1
    fi
    link_payload="$(python3 -c '
import json, sys
print(json.dumps({"api_key_id": sys.argv[1]}))
' "$caller_id")"
    if ! lcode="$(curl "${curl_common[@]}" -o /dev/null -w '%{http_code}' \
      -X POST "${API}/projects/${pid}/api-keys/link" \
      -H "Content-Type: application/json" --data "$link_payload")"; then
      echo "  ✗ SymGateway: falha de rede ao religar caller local" >&2
      exit 1
    fi
    case "${lcode:-000}" in
      2??|409) echo "  ✓ SymGateway: caller local religada ao projeto" ;;
      *)
        echo "  ✗ SymGateway: link da caller retornou HTTP ${lcode:-000}" >&2
        exit 1
        ;;
    esac
  else
    echo "  → SymGateway: caller dedicada já confirmada — ok"
  fi
else
  existing_key_id="$(_api_key_field "$project_keys" "$CALLER_NAME" "" id)"
  if [ -n "$existing_key_id" ]; then
    echo "  ✗ Caller '${CALLER_NAME}' já existe, mas .envrc.private está ausente" >&2
    echo "    Restaure a chave pelo secret store; o gateway não reexibe o segredo bruto." >&2
    exit 1
  fi
  caller_payload="$(python3 -c '
import json, sys
print(json.dumps({"name": sys.argv[1], "role": "caller"}))
' "$CALLER_NAME")"
  : > "$KEY_RESPONSE_FILE"
  if ! kcode="$(curl "${curl_common[@]}" -o "$KEY_RESPONSE_FILE" -w '%{http_code}' \
    -X POST "${API}/projects/${pid}/api-keys" \
    -H "Content-Type: application/json" --data "$caller_payload")"; then
    echo "  ✗ SymGateway: falha de rede ao criar caller dedicada" >&2
    exit 1
  fi
  case "${kcode:-000}" in
    2??) ;;
    *)
      echo "  ✗ SymGateway: criação da caller retornou HTTP ${kcode:-000}" >&2
      exit 1
      ;;
  esac
  PROJECT_CALLER_KEY="$(_created_key_field key)"
  created_role="$(_created_key_field role)"
  created_prefix="$(_created_key_field key_prefix)"
  if ! _valid_caller_key "$PROJECT_CALLER_KEY" \
    || [ "$created_role" != "caller" ] \
    || [ "$created_prefix" != "${PROJECT_CALLER_KEY:0:12}" ]; then
    echo "  ✗ SymGateway: resposta inválida ao criar caller; segredo não foi publicado" >&2
    exit 1
  fi
  PRIVATE_TEMP="${PROJECT_ROOT}/.envrc.private.ft-tmp.$$"
  (umask 077; printf 'export SYMGATEWAY_API_KEY="%s"\n' "$PROJECT_CALLER_KEY" > "$PRIVATE_TEMP")
  mv -- "$PRIVATE_TEMP" .envrc.private
  PRIVATE_TEMP=""
  chmod 600 .envrc.private
  : > "$KEY_RESPONSE_FILE"
  echo "  ✓ SymGateway: caller dedicada criada e salva em .envrc.private"
fi

# ---------------------------------------------------------------------------
# 5. Configuração por repositório — Codex e Claude somente via SymGateway
# ---------------------------------------------------------------------------
_ensure_envrc_export() {
  local variable="$1"
  local value="$2"
  local expected="export ${variable}=\"${value}\""
  if grep -Eq "^[[:space:]]*export[[:space:]]+${variable}=" .envrc; then
    if ! grep -Fqx "$expected" .envrc; then
      echo "  ✗ .envrc já define ${variable} com outro valor" >&2
      exit 1
    fi
  else
    printf '%s\n' "$expected" >> .envrc
  fi
}

if [ ! -e .envrc ]; then
  cat > .envrc <<EOF
# Configuração versionável do FT/SymGateway; segredos ficam em .envrc.private.
export SYMGATEWAY_PROJECT_SLUG="${SLUG}"
export FT_LLM_ENGINE="codex"
export FT_CODEX_PROFILE="${CODEX_PROFILE_NAME}"
source_env_if_exists .envrc.private
EOF
  echo "  ✓ criado .envrc (Codex via ${CODEX_PROFILE_NAME})"
else
  _ensure_envrc_export SYMGATEWAY_PROJECT_SLUG "$SLUG"
  _ensure_envrc_export FT_LLM_ENGINE codex
  _ensure_envrc_export FT_CODEX_PROFILE "$CODEX_PROFILE_NAME"
  if ! grep -Eq '^[[:space:]]*source_env_if_exists[[:space:]]+\.envrc\.private([[:space:]]|$)' .envrc; then
    printf 'source_env_if_exists .envrc.private\n' >> .envrc
  fi
  echo "  → .envrc já configurado — ok"
fi

if [ ! -f CLAUDE.md ]; then
  printf 'gateway_project: %s\n\nRegras globais: ~/dev/devops/GENERAL_RULES.md\n' "$SLUG" > CLAUDE.md
  echo "  ✓ criado CLAUDE.md (gateway_project: ${SLUG})"
elif grep -Eq '^gateway_project:[[:space:]]*' CLAUDE.md; then
  existing_gateway_project="$(sed -n 's/^gateway_project:[[:space:]]*//p' CLAUDE.md | head -n 1)"
  if [ "$existing_gateway_project" != "$SLUG" ]; then
    echo "  ✗ CLAUDE.md aponta para gateway_project '${existing_gateway_project}', esperado '${SLUG}'" >&2
    exit 1
  fi
else
  printf 'gateway_project: %s\n%s' "$SLUG" "$(cat CLAUDE.md)" > CLAUDE.md
  echo "  ✓ CLAUDE.md atualizado (gateway_project: ${SLUG})"
fi

mkdir -p .claude
if [ -L .claude/settings.local.json ]; then
  echo "  ✗ .claude/settings.local.json não pode ser link simbólico" >&2
  exit 1
fi
ANTHROPIC_BASE_URL="${GATEWAY_URL%/}/p/${ANTHROPIC_PROVIDER_PATH}/s/${SLUG}"
printf '%s\0%s' "$ANTHROPIC_BASE_URL" "$PROJECT_CALLER_KEY" | python3 -c '
import json, sys
base_url, api_key = sys.stdin.buffer.read().split(b"\0", 1)
json.dump(
    {
        "env": {
            "ANTHROPIC_BASE_URL": base_url.decode(),
            "ANTHROPIC_API_KEY": api_key.decode(),
        }
    },
    sys.stdout,
    indent=2,
)
print()
' > .claude/settings.local.json
chmod 600 .claude/settings.local.json
echo "  ✓ criado .claude/settings.local.json (Anthropic via ${ANTHROPIC_PROVIDER_PATH})"

AGENTS_START='<!-- symlabs-symgateway:start -->'
AGENTS_END='<!-- symlabs-symgateway:end -->'
if [ -L AGENTS.md ]; then
  echo "  ✗ AGENTS.md não pode ser link simbólico" >&2
  exit 1
elif [ ! -e AGENTS.md ]; then
  : > AGENTS.md
fi
agents_start_count="$(grep -Fxc "$AGENTS_START" AGENTS.md || true)"
agents_end_count="$(grep -Fxc "$AGENTS_END" AGENTS.md || true)"
if [ "$agents_start_count" = 0 ] && [ "$agents_end_count" = 0 ]; then
  cat >> AGENTS.md <<'EOF'

<!-- symlabs-symgateway:start -->
## Symlabs — LLMs via SymGateway por default

Todo uso de OpenAI ou Anthropic neste repositório deve atravessar o
SymGateway, salvo a exceção FT explícita abaixo. Não crie bypass ad hoc.

- **Codex/OpenAI:** use o profile `symgateway-dev`, com
  `SYMGATEWAY_API_KEY` e `SYMGATEWAY_PROJECT_SLUG` carregados pelo ambiente do
  projeto.
- **Exceção de imagem built-in:** somente um node de processo Fast Track que
  declare `codex_auth: chatgpt` pode usar o provider OpenAI direto. A exceção
  cobre o node inteiro, deve exigir login ChatGPT e existe apenas para uma
  capability built-in requerida, como `$imagegen`/`image_gen`, indisponível em
  custom providers. Sem login ou capability, bloqueie; não improvise fallback.
- **Claude/Anthropic:** use `ANTHROPIC_BASE_URL` e `ANTHROPIC_API_KEY` definidos
  em `.claude/settings.local.json`. Não execute `claude auth login` e não use
  credencial ou endpoint direto da Anthropic.
- Segredos permanecem apenas em `.envrc.private` e
  `.claude/settings.local.json`, ambos ignorados pelo Git. Nunca copie chaves
  para arquivos versionados, argumentos de processo, logs ou documentação.
- Fora da exceção declarativa acima, se o roteamento do SymGateway estiver
  ausente ou indisponível, interrompa a execução e corrija a configuração.
<!-- symlabs-symgateway:end -->
EOF
  echo "  ✓ AGENTS.md atualizado (SymGateway por default)"
elif [ "$agents_start_count" != 1 ] || [ "$agents_end_count" != 1 ]; then
  echo "  ✗ Bloco Symlabs/SymGateway inválido ou duplicado em AGENTS.md" >&2
  exit 1
else
  echo "  → AGENTS.md já contém a política SymGateway — ok"
fi

echo "  → Projeto ${PROJECT_NAME} pronto: Codex/OpenAI + Claude/Anthropic via SymGateway."
