#!/bin/bash
# =============================================================================
# run.sh — Executa todos os testes E2E de um Track (VT ou ST)
# =============================================================================
# Template para script de execucao de um ValueTrack ou SupportTrack.
#
# Estrutura esperada:
#   vt-XX-nome/
#   ├── run.sh              # Este arquivo
#   ├── 01-feature-a.sh     # Feature script 1
#   ├── 02-feature-b.sh     # Feature script 2
#   └── ...
#
# Uso:
#   ./run.sh              # Executa todas as features
#   ./run.sh --verbose    # Modo verbose
# =============================================================================

set -euo pipefail

# =============================================================================
# Configuracao
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
E2E_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"

# Identificacao do track (AJUSTE AQUI)
TRACK_ID="VT-XX"
TRACK_NAME="[Nome do Track]"

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
# Validacao de pre-requisitos
# =============================================================================

# Descomente e ajuste conforme necessidade:
# check_required_vars "API_KEY" "API_SECRET"
# check_cli "myapp"
# check_service_available "http://localhost:8080/health"

# =============================================================================
# Cabecalho
# =============================================================================

print_header "$TRACK_ID: $TRACK_NAME"

# =============================================================================
# Execucao das features
# =============================================================================

FEATURES_EXECUTED=0
FEATURES_FAILED=0

for feature_script in "$SCRIPT_DIR"/*.sh; do
    # Ignora o proprio run.sh
    [[ "$(basename "$feature_script")" == "run.sh" ]] && continue

    # Ignora arquivos nao executaveis
    [[ -x "$feature_script" ]] || continue

    feature_name=$(basename "$feature_script" .sh)

    echo ""
    print_subheader "Feature: $feature_name"

    # Reseta contadores para esta feature
    reset_counters

    # Executa feature
    if "$feature_script"; then
        ((FEATURES_EXECUTED++))
    else
        ((FEATURES_FAILED++))
    fi
done

# =============================================================================
# Resumo do Track
# =============================================================================

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  $TRACK_ID: $TRACK_NAME — RESUMO"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "  Features executadas: $FEATURES_EXECUTED"
echo "  Features com falha:  $FEATURES_FAILED"
echo ""

if [[ $FEATURES_FAILED -eq 0 ]]; then
    echo -e "  ${GREEN}${BOLD}TRACK APROVADO${NC}"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    exit 0
else
    echo -e "  ${RED}${BOLD}TRACK COM FALHAS${NC}"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    exit 1
fi
