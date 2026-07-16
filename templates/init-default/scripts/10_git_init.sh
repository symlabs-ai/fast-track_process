#!/usr/bin/env bash
# Cria o repositório Git do projeto se ainda não existir.
# Idempotente: repo existente é preservado sem nenhuma alteração.
set -euo pipefail

if [ ! -e .git ]; then
  git init -q
  echo "repositório Git criado"
fi
