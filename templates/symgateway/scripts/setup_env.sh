#!/usr/bin/env bash
# Setup do ambiente — roda uma vez no on_init de cada ciclo.
# Customize conforme as necessidades do projeto.
set -e

echo "  → Verificando Node.js / npm..."
node --version 2>/dev/null || echo "  ⚠ Node.js não encontrado"
npm --version 2>/dev/null || echo "  ⚠ npm não encontrado"

echo "  → Ambiente pronto."
