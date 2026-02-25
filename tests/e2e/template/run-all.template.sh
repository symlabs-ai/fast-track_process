#!/bin/bash
# =============================================================================
# run-all.sh — Executa todos os testes E2E do Ciclo [XX]
# =============================================================================
# Este script executa todos os ValueTracks e SupportTracks do ciclo,
# gerando um log de evidencia com timestamp.
#
# Uso:
#   ./run-all.sh              # Executa todos os tracks
#   ./run-all.sh --verbose    # Modo verbose
#   ./run-all.sh --dry-run    # Lista tracks sem executar
#
# Saida:
#   - Output colorido no terminal
#   - Log salvo em evidence/run-all_[TIMESTAMP].log
# =============================================================================

set -euo pipefail

# =============================================================================
# Configuracao
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
E2E_ROOT="$(dirname "$SCRIPT_DIR")"

# Numero do ciclo (AJUSTE AQUI)
CYCLE="XX"

# Carrega scripts compartilhados
# shellcheck source=../shared/setup.sh
source "$E2E_ROOT/shared/setup.sh"
# shellcheck source=../shared/colors.sh
source "$E2E_ROOT/shared/colors.sh"

# Diretorios
EVIDENCE_DIR="$SCRIPT_DIR/evidence"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOG_FILE="$EVIDENCE_DIR/run-all_${TIMESTAMP}.log"

# Contadores
TOTAL_PASSED=0
TOTAL_FAILED=0
TOTAL_SKIPPED=0

# Flags
VERBOSE=false
DRY_RUN=false

# =============================================================================
# Parsing de argumentos
# =============================================================================

while [[ $# -gt 0 ]]; do
    case "$1" in
        -v|--verbose)
            VERBOSE=true
            shift
            ;;
        -n|--dry-run)
            DRY_RUN=true
            shift
            ;;
        -h|--help)
            echo "Uso: $0 [opcoes]"
            echo ""
            echo "Opcoes:"
            echo "  -v, --verbose   Modo verbose"
            echo "  -n, --dry-run   Lista tracks sem executar"
            echo "  -h, --help      Mostra esta ajuda"
            exit 0
            ;;
        *)
            echo "Opcao desconhecida: $1"
            exit 1
            ;;
    esac
done

# =============================================================================
# Funcoes auxiliares
# =============================================================================

log() {
    echo "$@" | tee -a "$LOG_FILE"
}

log_header() {
    log ""
    log "═══════════════════════════════════════════════════════"
    log "  $1"
    log "═══════════════════════════════════════════════════════"
}

run_track() {
    local track_dir="$1"
    local track_name
    track_name=$(basename "$track_dir")

    log ""
    log -e "${BOLD}▶ Executando: $track_name${NC}"

    if [[ "$DRY_RUN" == "true" ]]; then
        log "  [DRY-RUN] Pulando execucao"
        return 0
    fi

    if [[ ! -x "$track_dir/run.sh" ]]; then
        log -e "  ${YELLOW}○ SKIP${NC}: run.sh nao encontrado ou nao executavel"
        ((TOTAL_SKIPPED++))
        return 0
    fi

    local start_time end_time duration
    start_time=$(date +%s)

    if "$track_dir/run.sh" 2>&1 | tee -a "$LOG_FILE"; then
        end_time=$(date +%s)
        duration=$((end_time - start_time))
        log -e "${GREEN}✓ $track_name: PASSED${NC} (${duration}s)"
        ((TOTAL_PASSED++))
        return 0
    else
        end_time=$(date +%s)
        duration=$((end_time - start_time))
        log -e "${RED}✗ $track_name: FAILED${NC} (${duration}s)"
        ((TOTAL_FAILED++))
        return 1
    fi
}

# =============================================================================
# Main
# =============================================================================

main() {
    # Cria diretorio de evidencias
    mkdir -p "$EVIDENCE_DIR"

    # Cabecalho
    log_header "E2E VALIDATION - CICLO $CYCLE"
    log "  Data: $(date)"
    log "  Usuario: ${USER:-unknown}"
    log "  Diretorio: $SCRIPT_DIR"
    if [[ "$DRY_RUN" == "true" ]]; then
        log "  Modo: DRY-RUN (sem execucao real)"
    fi
    log "═══════════════════════════════════════════════════════"

    local start_time_total
    start_time_total=$(date +%s)

    # Coleta tracks (vt-* e st-*)
    local tracks=()
    for track_dir in "$SCRIPT_DIR"/vt-* "$SCRIPT_DIR"/st-*; do
        [[ -d "$track_dir" ]] && tracks+=("$track_dir")
    done

    if [[ ${#tracks[@]} -eq 0 ]]; then
        log ""
        log -e "${YELLOW}Nenhum track encontrado!${NC}"
        log "Crie subdiretorios vt-XX-nome/ ou st-XX-nome/ com run.sh"
        exit 1
    fi

    log ""
    log "Tracks encontrados: ${#tracks[@]}"

    # Executa cada track
    for track_dir in "${tracks[@]}"; do
        run_track "$track_dir" || true
    done

    local end_time_total
    end_time_total=$(date +%s)
    local duration_total=$((end_time_total - start_time_total))

    # Resumo final
    log ""
    log_header "RESULTADO FINAL - CICLO $CYCLE"
    log ""
    log "  Tracks testados: $((TOTAL_PASSED + TOTAL_FAILED + TOTAL_SKIPPED))"
    log -e "  ${GREEN}PASSED${NC}:  $TOTAL_PASSED"
    log -e "  ${RED}FAILED${NC}:  $TOTAL_FAILED"
    log -e "  ${YELLOW}SKIPPED${NC}: $TOTAL_SKIPPED"
    log ""
    log "  Tempo total: ${duration_total}s"
    log "  Evidencia: $LOG_FILE"
    log ""
    log "═══════════════════════════════════════════════════════"

    if [[ $TOTAL_FAILED -eq 0 ]]; then
        log -e "  ${GREEN}${BOLD}CICLO $CYCLE VALIDADO COM SUCESSO${NC}"
        log "═══════════════════════════════════════════════════════"
        exit 0
    else
        log -e "  ${RED}${BOLD}CICLO $CYCLE COM FALHAS - REQUER ATENCAO${NC}"
        log "═══════════════════════════════════════════════════════"
        exit 1
    fi
}

# Executa
main "$@"
