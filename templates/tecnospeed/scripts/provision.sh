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
ENV_FILE="${FT_ENGINE_ROOT:?FT_ENGINE_ROOT ausente}/environment/${ORG}.env"

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

# CLAUDE.md com gateway_project + referência às regras globais
if [ ! -f CLAUDE.md ]; then
  printf 'gateway_project: %s\n\nRegras globais: ~/dev/devops/GENERAL_RULES.md\n' "$SLUG" > CLAUDE.md
  echo "  ✓ criado CLAUDE.md (gateway_project: ${SLUG})"
elif ! grep -q "gateway_project" CLAUDE.md; then
  printf 'gateway_project: %s\n%s' "$SLUG" "$(cat CLAUDE.md)" > CLAUDE.md
  echo "  ✓ CLAUDE.md atualizado (gateway_project: ${SLUG})"
fi

# .claude/settings.local.json — ANTHROPIC_BASE_URL roteando pelo gateway.
# Gitignored (init-default já cobre .claude/settings.local.json).
mkdir -p .claude
BASE_URL="${GATEWAY_URL}/u/${CALLER_KEY}/p/${PROVIDER_PATH}/s/${SLUG}"
cat > .claude/settings.local.json <<EOF
{
  "env": {
    "ANTHROPIC_BASE_URL": "${BASE_URL}"
  }
}
EOF
echo "  ✓ criado .claude/settings.local.json (roteamento ${PROVIDER_PATH})"

# Registro no gateway (best-effort — não bloqueia se a API oscilar).
API="${GATEWAY_URL%/}/_api"
auth=(-H "x-api-key: ${ADMIN_KEY}" -H "X-Workspace-ID: ${WORKSPACE_ID}")

# Extrai um campo do projeto de `slug` a partir do JSON da lista. Usa python3
# (ambiente Python garantido); sem ele, degrada para vazio sem quebrar.
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
' "$1" "$2" "$3" 2>/dev/null || true
}

plist=""
_load_projects() {
  [ -n "$plist" ] || plist="$(curl -s "${API}/projects?status=all" "${auth[@]}" 2>/dev/null || true)"
}

code="$(curl -s -o /dev/null -w '%{http_code}' -X POST "${API}/projects" \
  "${auth[@]}" -H "Content-Type: application/json" \
  -d "{\"name\":\"${PROJECT_NAME}\",\"slug\":\"${SLUG}\",\"folder_name\":\"${PROJECT_NAME}\"}" \
  2>/dev/null || true)"
code="${code:-000}"
case "$code" in
  200|201) echo "  ✓ SymGateway: projeto '${SLUG}' registrado" ;;
  409)
    # Slug já existe no workspace. Confirma que é o NOSSO projeto (folder_name
    # bate) antes de adotar — senão estaríamos roteando para um projeto alheio.
    _load_projects
    existing_folder="$(_project_field "$plist" "$SLUG" folder_name)"
    if [ -n "$existing_folder" ] && [ "$existing_folder" != "$PROJECT_NAME" ]; then
      echo "  ✗ SymGateway: slug '${SLUG}' já pertence a outro projeto no workspace" >&2
      echo "    (folder_name='${existing_folder}', esperado '${PROJECT_NAME}')." >&2
      echo "    Renomeie o diretório ou use outro slug — não vou adotar projeto alheio." >&2
      exit 1
    fi
    echo "  → SymGateway: projeto '${SLUG}' já existe — ok"
    ;;
  000)     echo "  ⚠ SymGateway: sem resposta da API — verifique conectividade" ;;
  *)       echo "  ⚠ SymGateway: registro retornou HTTP ${code} — continuando" ;;
esac

# Linka a caller key existente ao projeto (se CALLER_KEY_ID informado).
if [ -n "$CALLER_KEY_ID" ]; then
  _load_projects
  pid="$(_project_field "$plist" "$SLUG" id)"
  if [ -n "$pid" ]; then
    lcode="$(curl -s -o /dev/null -w '%{http_code}' -X POST \
      "${API}/projects/${pid}/api-keys/link" "${auth[@]}" \
      -H "Content-Type: application/json" -d "{\"api_key_id\":\"${CALLER_KEY_ID}\"}" \
      2>/dev/null || true)"
    lcode="${lcode:-000}"
    case "$lcode" in
      200|201) echo "  ✓ SymGateway: caller linkada ao projeto" ;;
      409)     echo "  → SymGateway: caller já linkada — ok" ;;
      *)       echo "  ⚠ SymGateway: link da caller retornou HTTP ${lcode}" ;;
    esac
  fi
fi

echo "  → Projeto ${PROJECT_NAME} pronto no ambiente ${ORG}."
