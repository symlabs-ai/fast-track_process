#!/usr/bin/env bash
set -euo pipefail

# Instala os hooks canônicos versionados na raiz do engine.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

cd "$ROOT_DIR"
echo "[pre-commit] Instalando dependências de desenvolvimento..."
"$PYTHON_BIN" -m pip install -e ".[dev]"

echo "[pre-commit] Instalando hook da raiz"
"$PYTHON_BIN" -m pre_commit install

echo "[pre-commit] Validando todos os arquivos"
"$PYTHON_BIN" -m pre_commit run --all-files

echo "[pre-commit] Pronto. Os hooks rodarão antes de cada commit."
