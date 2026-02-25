#!/bin/bash
# =============================================================================
# setup.sh — Configuracao de ambiente para testes E2E
# =============================================================================
# Este script deve ser sourceado (nao executado) pelos scripts de teste.
#
# Uso: source "$E2E_ROOT/shared/setup.sh"
#
# Funcionalidades:
#   - Carrega variaveis de ambiente de .env.e2e
#   - Valida variaveis obrigatorias
#   - Verifica disponibilidade da CLI
#   - Configura diretorio de trabalho
# =============================================================================

set -euo pipefail

# Diretorio raiz do E2E (onde esta a pasta shared/)
E2E_SHARED_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
E2E_ROOT="$(dirname "$E2E_SHARED_DIR")"

# Carrega cores
# shellcheck source=colors.sh
source "$E2E_SHARED_DIR/colors.sh"

# =============================================================================
# Carregamento de variaveis de ambiente
# =============================================================================

# Procura .env.e2e em ordem de prioridade:
# 1. Diretorio atual
# 2. Raiz do projeto (2 niveis acima de tests/e2e)
# 3. Home do usuario
load_env_file() {
    local env_file=""
    local search_paths=(
        ".env.e2e"
        "$E2E_ROOT/../../.env.e2e"
        "$HOME/.env.e2e"
    )

    for path in "${search_paths[@]}"; do
        if [[ -f "$path" ]]; then
            env_file="$path"
            break
        fi
    done

    if [[ -n "$env_file" ]]; then
        print_info "Carregando variaveis de: $env_file"
        # Exporta variaveis ignorando comentarios e linhas vazias
        set -a
        # shellcheck source=/dev/null
        source "$env_file"
        set +a
    else
        print_warning "Arquivo .env.e2e nao encontrado. Usando variaveis de ambiente existentes."
    fi
}

# =============================================================================
# Validacao de variaveis obrigatorias
# =============================================================================

# Verifica se variaveis obrigatorias estao definidas
# Uso: check_required_vars "VAR1" "VAR2" "VAR3"
check_required_vars() {
    local missing=()

    for var in "$@"; do
        if [[ -z "${!var:-}" ]]; then
            missing+=("$var")
        fi
    done

    if [[ ${#missing[@]} -gt 0 ]]; then
        print_error "Variaveis obrigatorias nao definidas:"
        for var in "${missing[@]}"; do
            echo "  - $var"
        done
        echo ""
        echo "Defina estas variaveis em .env.e2e ou exporte-as antes de executar."
        exit 1
    fi
}

# =============================================================================
# Validacao de CLI
# =============================================================================

# Verifica se uma CLI esta disponivel no PATH
# Uso: check_cli "myapp"
check_cli() {
    local cli_name="$1"
    local install_hint="${2:-pip install -e .}"

    if ! command -v "$cli_name" &> /dev/null; then
        print_error "CLI '$cli_name' nao encontrada no PATH."
        echo ""
        echo "Para instalar, execute:"
        echo "  $install_hint"
        exit 1
    fi

    print_success "CLI '$cli_name' encontrada: $(command -v "$cli_name")"
}

# Verifica versao minima de uma CLI
# Uso: check_cli_version "myapp" "1.0.0" "--version"
check_cli_version() {
    local cli_name="$1"
    local min_version="$2"
    local version_flag="${3:---version}"

    local current_version
    current_version=$("$cli_name" "$version_flag" 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)

    if [[ -z "$current_version" ]]; then
        print_warning "Nao foi possivel detectar versao de '$cli_name'"
        return 0
    fi

    # Comparacao simples de versao (funciona para maioria dos casos)
    if [[ "$(printf '%s\n' "$min_version" "$current_version" | sort -V | head -1)" != "$min_version" ]]; then
        print_error "Versao de '$cli_name' ($current_version) e menor que a minima requerida ($min_version)"
        exit 1
    fi

    print_success "CLI '$cli_name' versao $current_version (>= $min_version)"
}

# =============================================================================
# Utilitarios de diretorio
# =============================================================================

# Cria diretorio se nao existir
ensure_dir() {
    local dir="$1"
    if [[ ! -d "$dir" ]]; then
        mkdir -p "$dir"
        print_info "Diretorio criado: $dir"
    fi
}

# Retorna o diretorio raiz do projeto (assumindo tests/e2e/...)
get_project_root() {
    echo "$(cd "$E2E_ROOT/../.." && pwd)"
}

# =============================================================================
# Utilitarios de rede
# =============================================================================

# Verifica se um servico esta acessivel
# Uso: check_service_available "http://localhost:8080/health"
check_service_available() {
    local url="$1"
    local timeout="${2:-5}"

    if curl --silent --fail --max-time "$timeout" "$url" > /dev/null 2>&1; then
        print_success "Servico acessivel: $url"
        return 0
    else
        print_error "Servico inacessivel: $url"
        return 1
    fi
}

# =============================================================================
# Inicializacao automatica
# =============================================================================

# Carrega variaveis de ambiente automaticamente ao sourcear este arquivo
load_env_file

# Exporta funcoes para uso em subshells
export -f check_required_vars check_cli check_cli_version
export -f ensure_dir get_project_root check_service_available
export E2E_ROOT E2E_SHARED_DIR
