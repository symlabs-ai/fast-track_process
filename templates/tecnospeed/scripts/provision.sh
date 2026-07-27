#!/usr/bin/env bash
# Provisiona um projeto no ambiente de uma organização (Symlabs/Tecnospeed).
# O template é genérico: a organização é o próprio nome do template, e a
# config vive em `environment/<org>.env` no repo do engine (gitignored).
#
# Passos: (1) carrega e valida a config da org; (2) scaffold Poetry/src;
# (3) registra o projeto no SymGateway e escreve CLAUDE.md + settings.
#
# Idempotente: não sobrescreve arquivos existentes; 409 no gateway = ok.
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
  echo "    e preencha workspace, caller key e admin key (peça ao DevOps: /ask devops)." >&2
  exit 1
fi

# shellcheck disable=SC1090
set -a; . "$ENV_FILE"; set +a

# Resolve variáveis com prefixo da org (ex.: SYMLABS_WORKSPACE_ID)
_get() { eval "printf '%s' \"\${${ORG_UPPER}_$1:-}\""; }
GATEWAY_URL="$(_get GATEWAY_URL)"
WORKSPACE_ID="$(_get WORKSPACE_ID)"
PROVIDER_PATH="$(_get PROVIDER_PATH)"
ADMIN_KEY="$(_get ADMIN_KEY)"
CALLER_KEY="$(_get CALLER_KEY)"
CALLER_KEY_ID="$(_get CALLER_KEY_ID)"

# Placeholder = não provisionado. Falha alto e cedo.
missing=""
for var in GATEWAY_URL WORKSPACE_ID PROVIDER_PATH ADMIN_KEY CALLER_KEY; do
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

# .env de dev (sem segredos — settings.local.json cuida do roteamento).
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
# 3. SymGateway — registra projeto, linka caller, escreve CLAUDE.md + settings
# ---------------------------------------------------------------------------
SLUG="$PROJECT_NAME"

# Registro no gateway é obrigatório. O init só recebe marker depois que projeto
# e caller (quando configurada) estão confirmados remotamente.
API="${GATEWAY_URL%/}/_api"
AUTH_HEADER_FILE="$(mktemp "${TMPDIR:-/tmp}/ft-gateway-headers.XXXXXX")"
chmod 600 "$AUTH_HEADER_FILE"
printf 'Authorization: Bearer %s\nX-Workspace-ID: %s\n' \
  "$ADMIN_KEY" "$WORKSPACE_ID" > "$AUTH_HEADER_FILE"
trap 'rm -f "$AUTH_HEADER_FILE"' EXIT
curl_common=(
  --silent
  --show-error
  --connect-timeout 10
  --max-time 30
  --header "@${AUTH_HEADER_FILE}"
)

# Extrai um campo do projeto de `slug` a partir do JSON da lista.
_project_field() {
  python3 -c '
import sys, json
try:
    data = json.loads(sys.argv[1] or "[]")
except Exception:
    sys.exit(0)
items = data if isinstance(data, list) else (
    data.get("projects") or data.get("items") or data.get("data") or [])
for p in items:
    if isinstance(p, dict) and p.get("slug") == sys.argv[2]:
        print(p.get(sys.argv[3], "") or ""); break
' "$1" "$2" "$3" 2>/dev/null
}

plist=""
_load_projects() {
  if [ -n "$plist" ]; then
    return 0
  fi
  if ! plist="$(curl "${curl_common[@]}" "${API}/projects?status=all")"; then
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
    # Slug já existe no workspace. Confirma que é o NOSSO projeto (folder_name
    # bate) antes de adotar — senão estaríamos roteando para um projeto alheio.
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

# Linka a caller key existente ao projeto (se CALLER_KEY_ID informado).
if [ -n "$CALLER_KEY_ID" ]; then
  _load_projects || exit 1
  pid="$(_project_field "$plist" "$SLUG" id)"
  if [ -z "$pid" ]; then
    echo "  ✗ SymGateway: projeto '${SLUG}' sem id; caller não pôde ser linkada" >&2
    exit 1
  fi
  link_payload="$(python3 -c '
import json, sys
print(json.dumps({"api_key_id": sys.argv[1]}))
' "$CALLER_KEY_ID")"
  if ! lcode="$(curl "${curl_common[@]}" -o /dev/null -w '%{http_code}' \
    -X POST "${API}/projects/${pid}/api-keys/link" \
    -H "Content-Type: application/json" --data "$link_payload")"; then
    echo "  ✗ SymGateway: falha de rede ao linkar caller; init não concluído" >&2
    exit 1
  fi
  lcode="${lcode:-000}"
  case "$lcode" in
    2??) echo "  ✓ SymGateway: caller linkada ao projeto" ;;
    409) echo "  → SymGateway: caller já linkada — ok" ;;
    *)
      echo "  ✗ SymGateway: link da caller retornou HTTP ${lcode}; init não concluído" >&2
      exit 1
      ;;
  esac
fi

# Só publica configuração local depois da confirmação remota.
if [ ! -f CLAUDE.md ]; then
  printf 'gateway_project: %s\n\nRegras globais: ~/dev/devops/GENERAL_RULES.md\n' "$SLUG" > CLAUDE.md
  echo "  ✓ criado CLAUDE.md (gateway_project: ${SLUG})"
elif ! grep -q "gateway_project" CLAUDE.md; then
  printf 'gateway_project: %s\n%s' "$SLUG" "$(cat CLAUDE.md)" > CLAUDE.md
  echo "  ✓ CLAUDE.md atualizado (gateway_project: ${SLUG})"
fi

mkdir -p .claude
BASE_URL="${GATEWAY_URL}/u/${CALLER_KEY}/p/${PROVIDER_PATH}/s/${SLUG}"
python3 -c '
import json, sys
json.dump({"env": {"ANTHROPIC_BASE_URL": sys.argv[1]}}, sys.stdout, indent=2)
print()
' "$BASE_URL" > .claude/settings.local.json
echo "  ✓ criado .claude/settings.local.json (roteamento ${PROVIDER_PATH})"

echo "  → Projeto ${PROJECT_NAME} pronto no ambiente ${ORG}."
