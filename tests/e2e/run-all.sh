#!/bin/bash
# =============================================================================
# run-all.sh — Runner E2E principal
# =============================================================================
# Detecta ciclos disponíveis e executa seus run-all.sh.
# Também roda unit tests (pytest) e smoke tests como parte do E2E gate.
#
# Uso:
#   ./tests/e2e/run-all.sh              # Executa tudo
#   ./tests/e2e/run-all.sh --cycle 01   # Executa ciclo específico
#   ./tests/e2e/run-all.sh --unit-only  # Só unit + smoke (sem tracks)
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Cores
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

CYCLE=""
UNIT_ONLY=false
FAILURES=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --cycle) CYCLE="$2"; shift 2 ;;
        --unit-only) UNIT_ONLY=true; shift ;;
        -h|--help)
            echo "Uso: $0 [--cycle XX] [--unit-only]"
            exit 0
            ;;
        *) echo "Opcao desconhecida: $1"; exit 1 ;;
    esac
done

echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BOLD}  E2E Gate — Fast Track${NC}"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

# 1. Unit tests
echo -e "\n${BOLD}[1/3] Unit tests (pytest)${NC}"
if [ -d "$PROJECT_ROOT/tests" ]; then
    cd "$PROJECT_ROOT"
    if python -m pytest tests/ -q --tb=short 2>/dev/null; then
        echo -e "${GREEN}Unit tests: PASSED${NC}"
    else
        echo -e "${RED}Unit tests: FAILED${NC}"
        ((FAILURES++))
    fi
else
    echo -e "${YELLOW}tests/ nao encontrado — skip${NC}"
fi

# 2. Smoke tests
echo -e "\n${BOLD}[2/3] Smoke tests${NC}"
if [ -d "$PROJECT_ROOT/tests/smoke" ]; then
    cd "$PROJECT_ROOT"
    if python -m pytest tests/smoke/ -q --tb=short 2>/dev/null; then
        echo -e "${GREEN}Smoke tests: PASSED${NC}"
    else
        echo -e "${RED}Smoke tests: FAILED${NC}"
        ((FAILURES++))
    fi
else
    echo -e "${YELLOW}tests/smoke/ nao encontrado — skip${NC}"
fi

if [[ "$UNIT_ONLY" == "true" ]]; then
    echo -e "\n${BOLD}--unit-only: pulando tracks E2E${NC}"
else
    # 3. E2E tracks por ciclo
    echo -e "\n${BOLD}[3/3] E2E tracks${NC}"

    if [[ -n "$CYCLE" ]]; then
        # Ciclo específico
        cycle_dirs=("$SCRIPT_DIR/cycle-$CYCLE")
    else
        # Todos os ciclos
        cycle_dirs=("$SCRIPT_DIR"/cycle-*)
    fi

    found_cycles=false
    for cycle_dir in "${cycle_dirs[@]}"; do
        if [[ -d "$cycle_dir" && -f "$cycle_dir/run-all.sh" ]]; then
            found_cycles=true
            cycle_name=$(basename "$cycle_dir")
            echo -e "\n  ${BOLD}Ciclo: $cycle_name${NC}"
            if bash "$cycle_dir/run-all.sh"; then
                echo -e "  ${GREEN}$cycle_name: PASSED${NC}"
            else
                echo -e "  ${RED}$cycle_name: FAILED${NC}"
                ((FAILURES++))
            fi
        fi
    done

    if [[ "$found_cycles" == "false" ]]; then
        echo -e "  ${YELLOW}Nenhum ciclo E2E encontrado (tests/e2e/cycle-XX/)${NC}"
    fi
fi

# Resultado final
echo -e "\n${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
if [[ $FAILURES -eq 0 ]]; then
    echo -e "  ${GREEN}${BOLD}E2E GATE: PASS${NC}"
else
    echo -e "  ${RED}${BOLD}E2E GATE: BLOCK ($FAILURES falhas)${NC}"
fi
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

exit $FAILURES
