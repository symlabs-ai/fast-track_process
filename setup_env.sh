#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# ForgeProcess / ForgeLLMClient — Ambiente de Desenvolvimento
# ============================================================================
#
# O que este script faz:
#   1. Cria (se necessário) um virtualenv `.venv` com Python 3.12.
#   2. Instala dependências de runtime **sempre via pip + git**:
#        - forgebase      (git+https://github.com/symlabs-ai/forgebase.git)
#        - forgellmclient (git+https://github.com/symlabs-ai/forgellmclient.git)
#   3. Instala ferramentas de desenvolvimento:
#        - pre-commit, mypy, ruff, pytest, pytest-bdd, pytest-cov.
#   4. Descompacta `env/git-dev.zip` (se presente) com:
#        - pre-commit-config.yaml
#        - ruff.toml
#        - dev-requirements.txt
#        - install_precommit.sh
#   5. Instala e registra hooks de pre-commit.
#
# Documentação relevante (projeto alvo):
#   - docs/integrations/forgebase_guides/**
#   - docs/integrations/forge_llm_guides/**
#
# Uso recomendado (em um projeto alvo):
#   cd <raiz-do-projeto>
#   bash forgeprocess/setup_env.sh
#   source .venv/bin/activate
# ============================================================================

# A raiz do projeto é o diretório pai deste script (onde existem docs/, process/, src/, etc.)
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT_DIR}"

echo "==> Diretório do projeto: ${ROOT_DIR}"

# 1. Localizar Python 3.12 e criar .venv
PY_BIN="${PYTHON_BIN:-python3.12}"

if ! command -v "${PY_BIN}" >/dev/null 2>&1; then
  echo "ERRO: Não encontrei '${PY_BIN}'." >&2
  echo "Instale Python 3.12 ou defina PYTHON_BIN apontando para um binário compatível." >&2
  exit 1
fi

if [ ! -d ".venv" ]; then
  echo "==> Criando virtualenv .venv com ${PY_BIN}..."
  "${PY_BIN}" -m venv .venv
else
  echo "==> Virtualenv .venv já existe, reutilizando."
fi

echo "==> Ativando .venv..."
# shellcheck disable=SC1091
source .venv/bin/activate

echo "==> Atualizando pip..."
pip install --upgrade pip

# 2. Instalar ForgeBase (sempre via git)
echo "==> Instalando ForgeBase via git (git+https://github.com/symlabs-ai/forgebase.git)..."
pip install "git+https://github.com/symlabs-ai/forgebase.git"

# 3. Instalar ForgeLLMClient (sempre via git)
echo "==> Instalando ForgeLLMClient via git (git+https://github.com/symlabs-ai/forgellmclient.git)..."
pip install "git+https://github.com/symlabs-ai/forgellmclient.git"

# 4. Instalar ferramentas de desenvolvimento adicionais
echo "==> Instalando ferramentas de desenvolvimento (pre-commit, mypy, ruff, pytest, pytest-bdd, pytest-cov)..."
pip install pre-commit mypy ruff pytest pytest-bdd pytest-cov

# 5. Copiar configuração de pre-commit/ruff se existir
GIT_ENV_DIR="env/git-dev"

if [ -d "${GIT_ENV_DIR}" ]; then
  echo "==> Encontrado ${GIT_ENV_DIR}/. Preparando configuração de pre-commit/ruff..."
  if [ ! -f "pre-commit-config.yaml" ]; then
    echo "==> Copiando configuração de ${GIT_ENV_DIR}/ para raiz..."
    cp "${GIT_ENV_DIR}/pre-commit-config.yaml" .
    cp "${GIT_ENV_DIR}/ruff.toml" .
  else
    echo "==> pre-commit-config.yaml já existe, não vou sobrescrever."
  fi
else
  echo "==> ${GIT_ENV_DIR}/ não encontrado; pulei configuração automática de pre-commit/ruff."
fi

# 6. Instalar hooks de pre-commit (se configuração disponível)
if command -v pre-commit >/dev/null 2>&1; then
  if [ -f "pre-commit-config.yaml" ]; then
    echo "==> Instalando hooks de pre-commit..."
    pre-commit install
  else
    echo "!! Aviso: pre-commit instalado, mas pre-commit-config.yaml não existe nesta raiz."
    echo "   Veja env/git-dev/ ou crie sua própria configuração."
  fi
else
  echo "!! Aviso: pre-commit não encontrado no ambiente, algo deu errado na instalação."
fi

# 7. Parsear argumentos opcionais
FROM_PROJECT=""
GATEWAY_KEY=""
i=1
while [ $i -le $# ]; do
  arg="${!i}"
  case $arg in
    --from-project=*) FROM_PROJECT="${arg#*=}" ;;
    --from-project)   i=$((i+1)); FROM_PROJECT="${!i:-}" ;;
    --key=*)          GATEWAY_KEY="${arg#*=}" ;;
    --key)            i=$((i+1)); GATEWAY_KEY="${!i:-}" ;;
  esac
  i=$((i+1))
done

if [ -n "${FROM_PROJECT}" ]; then
  PREV_PLANO="${FROM_PROJECT}/project/docs/plano_de_voo.md"
  if [ -f "${PREV_PLANO}" ]; then
    TARGET_DOCS="${ROOT_DIR}/project/docs"
    mkdir -p "${TARGET_DOCS}"
    cp "${PREV_PLANO}" "${TARGET_DOCS}/plano_de_voo.md"
    echo "==> Plano de voo copiado de ${PREV_PLANO} → project/docs/plano_de_voo.md"
    echo "    (será injetado automaticamente no contexto dos agentes pelo hyper-mode)"
  else
    echo "!! Aviso: --from-project especificado mas ${PREV_PLANO} não encontrado."
  fi
fi

# 8. Provisionar CLAUDE.md + .claude/settings.local.json (se --key fornecida)
#    A key deriva automaticamente a ANTHROPIC_BASE_URL.
#    Uso: bash setup_env.sh --key sk-sym_SUA_KEY
if [ -n "${GATEWAY_KEY}" ]; then
  BASE_URL="https://symgateway.symlabs.ai/u/${GATEWAY_KEY}/p/anthropic-max"
  PROJECT_NAME="$(basename "${ROOT_DIR}")"

  # CLAUDE.md
  CLAUDE_FILE="${ROOT_DIR}/CLAUDE.md"
  if [ ! -f "${CLAUDE_FILE}" ]; then
    printf 'gateway_project: %s\n' "${PROJECT_NAME}" > "${CLAUDE_FILE}"
    echo "==> Criado: CLAUDE.md (gateway_project: ${PROJECT_NAME})"
  elif ! grep -q "gateway_project" "${CLAUDE_FILE}"; then
    printf 'gateway_project: %s\n' "${PROJECT_NAME}" | cat - "${CLAUDE_FILE}" > /tmp/_claude_md_tmp && mv /tmp/_claude_md_tmp "${CLAUDE_FILE}"
    echo "==> Atualizado: CLAUDE.md (gateway_project: ${PROJECT_NAME})"
  else
    echo "==> CLAUDE.md já contém gateway_project."
  fi

  # .claude/settings.local.json
  DOT_CLAUDE="${ROOT_DIR}/.claude"
  mkdir -p "${DOT_CLAUDE}"
  SETTINGS_FILE="${DOT_CLAUDE}/settings.local.json"
  printf '{\n  "env": {\n    "ANTHROPIC_BASE_URL": "%s"\n  }\n}\n' "${BASE_URL}" > "${SETTINGS_FILE}"
  echo "==> Criado: .claude/settings.local.json (ANTHROPIC_BASE_URL configurado)"
else
  echo "!! Aviso: --key não fornecida. CLAUDE.md e settings.local.json não foram criados."
  echo "   Use: bash setup_env.sh --key sk-sym_SUA_KEY"
fi

cat <<EOF

============================================================
Ambiente configurado para ForgeProcess / ForgeLLMClient
------------------------------------------------------------
Virtualenv:   ${ROOT_DIR}/.venv
Python bin:   ${PY_BIN}

Pacotes instalados (principais):
  - forgebase (git+https://github.com/symlabs-ai/forgebase.git)
  - forgellmclient (git+https://github.com/symlabs-ai/forgellmclient.git)
  - pre-commit, mypy, ruff, pytest, pytest-bdd, pytest-cov

Documentação útil:
  - docs/integrations/forgebase_guides/**
  - docs/integrations/forge_llm_guides/**

Para usar o ambiente em um novo shell:

  cd ${ROOT_DIR}
  source .venv/bin/activate

============================================================
EOF

