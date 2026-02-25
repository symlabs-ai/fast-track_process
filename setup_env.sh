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

# 5. Descompactar git-dev.zip (configuração de pre-commit/ruff) se existir
GIT_ENV_ZIP="env/git-dev.zip"

if [ -f "${GIT_ENV_ZIP}" ]; then
  echo "==> Encontrado ${GIT_ENV_ZIP}. Preparando configuração de pre-commit/ruff..."

  if ! command -v unzip >/dev/null 2>&1; then
    echo "!! Aviso: 'unzip' não encontrado. Pulei a extração de ${GIT_ENV_ZIP}."
  else
    if [ ! -f "pre-commit-config.yaml" ]; then
      echo "==> Extraindo ${GIT_ENV_ZIP} na raiz de ${ROOT_DIR}..."
      unzip -o "${GIT_ENV_ZIP}"
    else
      echo "==> pre-commit-config.yaml já existe, não vou sobrescrever."
    fi
  fi
else
  echo "==> ${GIT_ENV_ZIP} não encontrado; pulei configuração automática de pre-commit/ruff."
fi

# 6. Instalar hooks de pre-commit (se configuração disponível)
if command -v pre-commit >/dev/null 2>&1; then
  if [ -f "pre-commit-config.yaml" ]; then
    echo "==> Instalando hooks de pre-commit..."
    pre-commit install
  else
    echo "!! Aviso: pre-commit instalado, mas pre-commit-config.yaml não existe nesta raiz."
    echo "   Você pode extraí-lo de env/git-dev.zip ou criar sua própria configuração."
  fi
else
  echo "!! Aviso: pre-commit não encontrado no ambiente, algo deu errado na instalação."
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

