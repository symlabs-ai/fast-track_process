#!/bin/bash
# =============================================================================
# teardown.sh — Limpeza apos testes E2E
# =============================================================================
# Este script deve ser sourceado (nao executado) pelos scripts de teste.
#
# Uso: source "$E2E_ROOT/shared/teardown.sh"
#
# Funcionalidades:
#   - Registro de recursos para limpeza automatica
#   - Limpeza de arquivos temporarios
#   - Restauracao de estado
#   - Hooks de finalizacao
# =============================================================================

# Diretorio raiz do E2E
E2E_SHARED_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=colors.sh
source "$E2E_SHARED_DIR/colors.sh"

# =============================================================================
# Registro de recursos para limpeza
# =============================================================================

# Array para armazenar arquivos/diretorios a limpar
declare -a CLEANUP_FILES=()
declare -a CLEANUP_DIRS=()
declare -a CLEANUP_COMMANDS=()

# Registra arquivo para limpeza automatica
# Uso: register_cleanup_file "/tmp/test_output.json"
register_cleanup_file() {
    CLEANUP_FILES+=("$1")
}

# Registra diretorio para limpeza automatica
# Uso: register_cleanup_dir "/tmp/test_workspace"
register_cleanup_dir() {
    CLEANUP_DIRS+=("$1")
}

# Registra comando para executar na limpeza
# Uso: register_cleanup_command "docker stop test_container"
register_cleanup_command() {
    CLEANUP_COMMANDS+=("$1")
}

# =============================================================================
# Funcao principal de limpeza
# =============================================================================

# Executa toda a limpeza registrada
# Chamada automaticamente via trap ou manualmente
run_cleanup() {
    local exit_code=$?

    echo ""
    print_info "Executando limpeza..."

    # Executa comandos de limpeza registrados
    for cmd in "${CLEANUP_COMMANDS[@]:-}"; do
        if [[ -n "$cmd" ]]; then
            print_info "Executando: $cmd"
            eval "$cmd" 2>/dev/null || true
        fi
    done

    # Remove arquivos temporarios
    for file in "${CLEANUP_FILES[@]:-}"; do
        if [[ -n "$file" ]] && [[ -f "$file" ]]; then
            rm -f "$file"
            print_info "Arquivo removido: $file"
        fi
    done

    # Remove diretorios temporarios
    for dir in "${CLEANUP_DIRS[@]:-}"; do
        if [[ -n "$dir" ]] && [[ -d "$dir" ]]; then
            rm -rf "$dir"
            print_info "Diretorio removido: $dir"
        fi
    done

    print_success "Limpeza concluida"

    return $exit_code
}

# =============================================================================
# Criacao de recursos temporarios com limpeza automatica
# =============================================================================

# Cria arquivo temporario e registra para limpeza
# Uso: tmp_file=$(create_temp_file "prefix" ".json")
create_temp_file() {
    local prefix="${1:-e2e}"
    local suffix="${2:-.tmp}"

    local tmp_file
    tmp_file=$(mktemp "/tmp/${prefix}_XXXXXX${suffix}")

    register_cleanup_file "$tmp_file"
    echo "$tmp_file"
}

# Cria diretorio temporario e registra para limpeza
# Uso: tmp_dir=$(create_temp_dir "workspace")
create_temp_dir() {
    local prefix="${1:-e2e}"

    local tmp_dir
    tmp_dir=$(mktemp -d "/tmp/${prefix}_XXXXXX")

    register_cleanup_dir "$tmp_dir"
    echo "$tmp_dir"
}

# =============================================================================
# Backup e restauracao
# =============================================================================

# Faz backup de arquivo antes de modificar
# Uso: backup_file "/path/to/config.yml"
backup_file() {
    local file="$1"
    local backup="${file}.e2e_backup"

    if [[ -f "$file" ]]; then
        cp "$file" "$backup"
        register_cleanup_command "mv '$backup' '$file'"
        print_info "Backup criado: $backup"
    fi
}

# =============================================================================
# Handlers de sinal
# =============================================================================

# Handler para SIGINT (Ctrl+C)
handle_interrupt() {
    echo ""
    print_warning "Teste interrompido pelo usuario (Ctrl+C)"
    run_cleanup
    exit 130
}

# Handler para SIGTERM
handle_terminate() {
    print_warning "Teste terminado por sinal"
    run_cleanup
    exit 143
}

# =============================================================================
# Configuracao automatica de traps
# =============================================================================

# Configura traps para limpeza automatica
setup_cleanup_traps() {
    trap run_cleanup EXIT
    trap handle_interrupt INT
    trap handle_terminate TERM

    print_info "Traps de limpeza configurados"
}

# Exporta funcoes
export -f register_cleanup_file register_cleanup_dir register_cleanup_command
export -f run_cleanup create_temp_file create_temp_dir
export -f backup_file setup_cleanup_traps
