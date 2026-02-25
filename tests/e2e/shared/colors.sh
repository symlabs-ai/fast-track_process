#!/bin/bash
# =============================================================================
# colors.sh — Cores para output de terminal
# =============================================================================
# Uso: source colors.sh
#
# Variaveis disponiveis:
#   - RED, GREEN, YELLOW, BLUE, CYAN, MAGENTA, BOLD, NC (no color)
#
# Exemplo:
#   echo -e "${GREEN}Sucesso!${NC}"
#   echo -e "${RED}Erro!${NC}"
#   echo -e "${BOLD}${YELLOW}Aviso${NC}"
# =============================================================================

# Detecta se terminal suporta cores
if [[ -t 1 ]] && [[ "${TERM:-dumb}" != "dumb" ]]; then
    # Cores basicas
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[0;33m'
    BLUE='\033[0;34m'
    MAGENTA='\033[0;35m'
    CYAN='\033[0;36m'

    # Cores brilhantes
    BRIGHT_RED='\033[1;31m'
    BRIGHT_GREEN='\033[1;32m'
    BRIGHT_YELLOW='\033[1;33m'
    BRIGHT_BLUE='\033[1;34m'

    # Estilos
    BOLD='\033[1m'
    DIM='\033[2m'
    UNDERLINE='\033[4m'

    # Reset
    NC='\033[0m'
else
    # Sem cores em terminais que nao suportam
    RED=''
    GREEN=''
    YELLOW=''
    BLUE=''
    MAGENTA=''
    CYAN=''
    BRIGHT_RED=''
    BRIGHT_GREEN=''
    BRIGHT_YELLOW=''
    BRIGHT_BLUE=''
    BOLD=''
    DIM=''
    UNDERLINE=''
    NC=''
fi

# Exporta todas as variaveis
export RED GREEN YELLOW BLUE MAGENTA CYAN
export BRIGHT_RED BRIGHT_GREEN BRIGHT_YELLOW BRIGHT_BLUE
export BOLD DIM UNDERLINE NC

# =============================================================================
# Funcoes auxiliares de output
# =============================================================================

# Imprime mensagem de sucesso
print_success() {
    echo -e "${GREEN}✓${NC} $*"
}

# Imprime mensagem de erro
print_error() {
    echo -e "${RED}✗${NC} $*" >&2
}

# Imprime mensagem de aviso
print_warning() {
    echo -e "${YELLOW}!${NC} $*"
}

# Imprime mensagem informativa
print_info() {
    echo -e "${BLUE}→${NC} $*"
}

# Imprime cabecalho de secao
print_header() {
    local title="$1"
    local width="${2:-60}"
    local line
    line=$(printf '═%.0s' $(seq 1 "$width"))

    echo ""
    echo -e "${BOLD}${line}${NC}"
    echo -e "${BOLD}  $title${NC}"
    echo -e "${BOLD}${line}${NC}"
}

# Imprime subcabecalho
print_subheader() {
    local title="$1"
    local width="${2:-40}"
    local line
    line=$(printf '─%.0s' $(seq 1 "$width"))

    echo ""
    echo -e "${DIM}${line}${NC}"
    echo -e "  ${BOLD}$title${NC}"
    echo -e "${DIM}${line}${NC}"
}

# Exporta funcoes
export -f print_success print_error print_warning print_info
export -f print_header print_subheader
