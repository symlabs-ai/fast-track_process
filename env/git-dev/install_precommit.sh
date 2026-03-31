#!/usr/bin/env bash
set -euo pipefail

# Instala e configura o pre-commit usando a config em scripts/,
# respeitando a regra de não escrever na raiz com arquivos de config.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CFG="$SCRIPT_DIR/pre-commit-config.yaml"

if ! command -v pre-commit >/dev/null 2>&1; then
  echo "[pre-commit] 'pre-commit' não encontrado. Instalando dependências de dev..."
  if command -v pip >/dev/null 2>&1; then
    pip install -r "$SCRIPT_DIR/dev-requirements.txt"
  else
    echo "[pre-commit] pip não encontrado. Instale pre-commit manualmente: pip install pre-commit"
    exit 1
  fi
fi

echo "[pre-commit] Instalando hook com config em $CFG"
pre-commit install --config "$CFG"

echo "[pre-commit] Rodando em todos os arquivos para baseline (pode levar um pouco)"
pre-commit run --config "$CFG" --all-files || true

echo "[pre-commit] Pronto. Os hooks rodarão antes de cada commit."
