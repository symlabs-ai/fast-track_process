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

# 7. Copiar plano_de_voo.md do projeto anterior (se --from-project for fornecido)
#    Uso: bash setup_env.sh --from-project /caminho/para/projeto-anterior
FROM_PROJECT=""
for arg in "$@"; do
  case $arg in
    --from-project=*) FROM_PROJECT="${arg#*=}" ;;
    --from-project)   shift; FROM_PROJECT="${1:-}" ;;
  esac
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

# 8. Criar environment/gateway.md a partir do template se não existir
ENV_DIR="${ROOT_DIR}/environment"
GATEWAY_FILE="${ENV_DIR}/gateway.md"
GATEWAY_EXAMPLE="${ENV_DIR}/gateway.example.md"

if [ ! -f "${GATEWAY_FILE}" ] && [ -f "${GATEWAY_EXAMPLE}" ]; then
  echo "==> Criando environment/gateway.md a partir do template..."
  cp "${GATEWAY_EXAMPLE}" "${GATEWAY_FILE}"
  echo "!! ATENÇÃO: Preencha ${GATEWAY_FILE} com os dados do seu workspace antes de rodar ft init."
elif [ -f "${GATEWAY_FILE}" ]; then
  echo "==> environment/gateway.md já existe."
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

