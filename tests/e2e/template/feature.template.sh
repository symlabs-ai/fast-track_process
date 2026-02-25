#!/bin/bash
# =============================================================================
# [NN]-[feature-name].sh — Teste E2E de Feature
# =============================================================================
# Feature: [Nome da Feature]
# BDD: project/specs/bdd/[prefix]_[domain]/[feature].feature
#
# Este script testa a feature via CLI com integracoes reais.
#
# Cenarios cobertos:
#   1. [Cenario happy path]
#   2. [Cenario de erro]
#   3. [Cenario de borda]
# =============================================================================

set -euo pipefail

# =============================================================================
# Configuracao
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
E2E_ROOT="$(dirname "$(dirname "$(dirname "$SCRIPT_DIR")")")"

# Carrega scripts compartilhados
# shellcheck source=../../shared/setup.sh
source "$E2E_ROOT/shared/setup.sh"
# shellcheck source=../../shared/assertions.sh
source "$E2E_ROOT/shared/assertions.sh"
# shellcheck source=../../shared/teardown.sh
source "$E2E_ROOT/shared/teardown.sh"

# Configura limpeza automatica
setup_cleanup_traps

# =============================================================================
# Pre-requisitos
# =============================================================================

# Variaveis obrigatorias para esta feature
check_required_vars "API_KEY"

# CLI necessaria
check_cli "myapp" "pip install -e ."

# =============================================================================
# Feature: [Nome da Feature]
# =============================================================================

echo "Feature: [Nome da Feature]"
echo "BDD: project/specs/bdd/[prefix]_[domain]/[feature].feature"
echo ""

# -----------------------------------------------------------------------------
# Cenario 1: [Happy Path]
# -----------------------------------------------------------------------------

echo "Cenario: [Descricao do cenario happy path]"

# Dado [contexto inicial]
# (setup se necessario)

# Quando [acao executada]
output=$(myapp command subcommand \
    --param1 "value1" \
    --param2 "value2" \
    --format json 2>&1)
exit_code=$?

# Entao [resultado esperado]
assert_exit_code 0 $exit_code "Comando deve executar com sucesso"
assert_contains "$output" '"status": "success"' "Status deve ser success"
assert_json_has_field "$output" ".id" "Deve retornar ID"

echo ""

# -----------------------------------------------------------------------------
# Cenario 2: [Tratamento de Erro]
# -----------------------------------------------------------------------------

echo "Cenario: [Descricao do cenario de erro]"

# Dado [contexto que causa erro]
# (setup se necessario)

# Quando [acao que deve falhar]
output=$(myapp command subcommand \
    --param1 "invalid_value" \
    --format json 2>&1) || true
exit_code=$?

# Entao [erro tratado corretamente]
assert_failure $exit_code "Comando deve falhar com entrada invalida"
assert_contains "$output" '"error"' "Deve retornar mensagem de erro"
assert_not_contains "$output" "stack trace" "Nao deve expor stack trace"

echo ""

# -----------------------------------------------------------------------------
# Cenario 3: [Caso de Borda] (opcional)
# -----------------------------------------------------------------------------

# echo "Cenario: [Descricao do caso de borda]"
# ...

# =============================================================================
# Resumo
# =============================================================================

print_summary
