#!/bin/bash
# =============================================================================
# assertions.sh — Funcoes de assercao para testes E2E
# =============================================================================
# Este script deve ser sourceado (nao executado) pelos scripts de teste.
#
# Uso: source "$E2E_ROOT/shared/assertions.sh"
#
# Funcoes disponiveis:
#   - assert_eq: Compara igualdade
#   - assert_not_eq: Compara diferenca
#   - assert_contains: Verifica se contem substring
#   - assert_not_contains: Verifica se NAO contem substring
#   - assert_matches: Verifica regex
#   - assert_exit_code: Verifica codigo de saida
#   - assert_file_exists: Verifica se arquivo existe
#   - assert_json_field: Verifica campo em JSON
#   - print_summary: Imprime resumo dos testes
# =============================================================================

# Diretorio raiz do E2E
E2E_SHARED_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=colors.sh
source "$E2E_SHARED_DIR/colors.sh"

# =============================================================================
# Contadores globais
# =============================================================================

TESTS_PASSED=0
TESTS_FAILED=0
TESTS_SKIPPED=0
CURRENT_TEST=""

# =============================================================================
# Funcoes auxiliares internas
# =============================================================================

_record_pass() {
    local message="$1"
    ((TESTS_PASSED++))
    echo -e "${GREEN}✓ PASS${NC}: $message"
}

_record_fail() {
    local message="$1"
    ((TESTS_FAILED++))
    echo -e "${RED}✗ FAIL${NC}: $message"
}

_record_skip() {
    local message="$1"
    ((TESTS_SKIPPED++))
    echo -e "${YELLOW}○ SKIP${NC}: $message"
}

# =============================================================================
# Assercoes de igualdade
# =============================================================================

# Verifica se dois valores sao iguais
# Uso: assert_eq "esperado" "atual" "mensagem"
assert_eq() {
    local expected="$1"
    local actual="$2"
    local message="${3:-Valores devem ser iguais}"

    if [[ "$expected" == "$actual" ]]; then
        _record_pass "$message"
        return 0
    else
        _record_fail "$message"
        echo "    Esperado: $expected"
        echo "    Recebido: $actual"
        return 1
    fi
}

# Verifica se dois valores sao diferentes
# Uso: assert_not_eq "nao_esperado" "atual" "mensagem"
assert_not_eq() {
    local not_expected="$1"
    local actual="$2"
    local message="${3:-Valores devem ser diferentes}"

    if [[ "$not_expected" != "$actual" ]]; then
        _record_pass "$message"
        return 0
    else
        _record_fail "$message"
        echo "    Nao deveria ser: $not_expected"
        echo "    Mas recebeu: $actual"
        return 1
    fi
}

# =============================================================================
# Assercoes de substring
# =============================================================================

# Verifica se string contem substring
# Uso: assert_contains "$output" "texto esperado" "mensagem"
assert_contains() {
    local haystack="$1"
    local needle="$2"
    local message="${3:-Deve conter substring}"

    if [[ "$haystack" == *"$needle"* ]]; then
        _record_pass "$message"
        return 0
    else
        _record_fail "$message"
        echo "    Texto: ${haystack:0:200}..."
        echo "    Nao contem: $needle"
        return 1
    fi
}

# Verifica se string NAO contem substring
# Uso: assert_not_contains "$output" "texto indesejado" "mensagem"
assert_not_contains() {
    local haystack="$1"
    local needle="$2"
    local message="${3:-Nao deve conter substring}"

    if [[ "$haystack" != *"$needle"* ]]; then
        _record_pass "$message"
        return 0
    else
        _record_fail "$message"
        echo "    Texto contem indevidamente: $needle"
        return 1
    fi
}

# =============================================================================
# Assercoes de regex
# =============================================================================

# Verifica se string corresponde a regex
# Uso: assert_matches "$output" "^[0-9]+$" "mensagem"
assert_matches() {
    local string="$1"
    local pattern="$2"
    local message="${3:-Deve corresponder ao padrao}"

    if [[ "$string" =~ $pattern ]]; then
        _record_pass "$message"
        return 0
    else
        _record_fail "$message"
        echo "    Texto: $string"
        echo "    Padrao: $pattern"
        return 1
    fi
}

# =============================================================================
# Assercoes de exit code
# =============================================================================

# Verifica codigo de saida
# Uso: assert_exit_code 0 $? "mensagem"
assert_exit_code() {
    local expected="$1"
    local actual="$2"
    local message="${3:-Exit code correto}"

    assert_eq "$expected" "$actual" "$message (exit code: $actual)"
}

# Verifica se comando teve sucesso (exit code 0)
# Uso: assert_success $? "mensagem"
assert_success() {
    local exit_code="$1"
    local message="${2:-Comando deve ter sucesso}"

    assert_exit_code 0 "$exit_code" "$message"
}

# Verifica se comando falhou (exit code != 0)
# Uso: assert_failure $? "mensagem"
assert_failure() {
    local exit_code="$1"
    local message="${2:-Comando deve falhar}"

    if [[ "$exit_code" -ne 0 ]]; then
        _record_pass "$message (exit code: $exit_code)"
        return 0
    else
        _record_fail "$message"
        echo "    Esperado: exit code != 0"
        echo "    Recebido: exit code = 0"
        return 1
    fi
}

# =============================================================================
# Assercoes de arquivo
# =============================================================================

# Verifica se arquivo existe
# Uso: assert_file_exists "/path/to/file" "mensagem"
assert_file_exists() {
    local file="$1"
    local message="${2:-Arquivo deve existir}"

    if [[ -f "$file" ]]; then
        _record_pass "$message"
        return 0
    else
        _record_fail "$message"
        echo "    Arquivo nao encontrado: $file"
        return 1
    fi
}

# Verifica se diretorio existe
# Uso: assert_dir_exists "/path/to/dir" "mensagem"
assert_dir_exists() {
    local dir="$1"
    local message="${2:-Diretorio deve existir}"

    if [[ -d "$dir" ]]; then
        _record_pass "$message"
        return 0
    else
        _record_fail "$message"
        echo "    Diretorio nao encontrado: $dir"
        return 1
    fi
}

# Verifica se arquivo contem texto
# Uso: assert_file_contains "/path/to/file" "texto" "mensagem"
assert_file_contains() {
    local file="$1"
    local needle="$2"
    local message="${3:-Arquivo deve conter texto}"

    if [[ ! -f "$file" ]]; then
        _record_fail "$message"
        echo "    Arquivo nao encontrado: $file"
        return 1
    fi

    if grep -q "$needle" "$file"; then
        _record_pass "$message"
        return 0
    else
        _record_fail "$message"
        echo "    Arquivo: $file"
        echo "    Nao contem: $needle"
        return 1
    fi
}

# =============================================================================
# Assercoes de JSON
# =============================================================================

# Verifica campo em JSON usando jq
# Uso: assert_json_field "$json" ".status" "approved" "mensagem"
assert_json_field() {
    local json="$1"
    local field="$2"
    local expected="$3"
    local message="${4:-Campo JSON deve ter valor esperado}"

    if ! command -v jq &> /dev/null; then
        _record_skip "$message (jq nao instalado)"
        return 0
    fi

    local actual
    actual=$(echo "$json" | jq -r "$field" 2>/dev/null)

    if [[ "$actual" == "$expected" ]]; then
        _record_pass "$message"
        return 0
    else
        _record_fail "$message"
        echo "    Campo: $field"
        echo "    Esperado: $expected"
        echo "    Recebido: $actual"
        return 1
    fi
}

# Verifica se JSON tem campo definido (nao null)
# Uso: assert_json_has_field "$json" ".transaction_id" "mensagem"
assert_json_has_field() {
    local json="$1"
    local field="$2"
    local message="${3:-Campo JSON deve existir}"

    if ! command -v jq &> /dev/null; then
        _record_skip "$message (jq nao instalado)"
        return 0
    fi

    local value
    value=$(echo "$json" | jq -r "$field" 2>/dev/null)

    if [[ -n "$value" ]] && [[ "$value" != "null" ]]; then
        _record_pass "$message"
        return 0
    else
        _record_fail "$message"
        echo "    Campo nao encontrado ou null: $field"
        return 1
    fi
}

# =============================================================================
# Assercoes de tempo
# =============================================================================

# Verifica se comando executa dentro do tempo limite
# Uso: assert_timeout 5 "comando" "mensagem"
assert_timeout() {
    local max_seconds="$1"
    local command="$2"
    local message="${3:-Comando deve completar em tempo}"

    local start_time end_time duration

    start_time=$(date +%s)
    eval "$command" > /dev/null 2>&1
    local exit_code=$?
    end_time=$(date +%s)

    duration=$((end_time - start_time))

    if [[ $duration -le $max_seconds ]]; then
        _record_pass "$message (${duration}s <= ${max_seconds}s)"
        return $exit_code
    else
        _record_fail "$message"
        echo "    Tempo maximo: ${max_seconds}s"
        echo "    Tempo real: ${duration}s"
        return 1
    fi
}

# =============================================================================
# Skip condicional
# =============================================================================

# Pula teste se condicao for verdadeira
# Uso: skip_if [[ -z "$API_KEY" ]] "API_KEY nao definida"
skip_if() {
    local condition="$1"
    local reason="${2:-Condicao de skip atendida}"

    if eval "$condition"; then
        _record_skip "$reason"
        return 0
    fi
    return 1
}

# =============================================================================
# Resumo dos testes
# =============================================================================

# Imprime resumo e retorna exit code apropriado
print_summary() {
    local total=$((TESTS_PASSED + TESTS_FAILED + TESTS_SKIPPED))

    echo ""
    echo "═══════════════════════════════════════════════════════"
    echo -e "  ${BOLD}RESULTADO DOS TESTES${NC}"
    echo "═══════════════════════════════════════════════════════"
    echo ""
    echo -e "  Total:   $total testes"
    echo -e "  ${GREEN}Passed${NC}:  $TESTS_PASSED"
    echo -e "  ${RED}Failed${NC}:  $TESTS_FAILED"
    echo -e "  ${YELLOW}Skipped${NC}: $TESTS_SKIPPED"
    echo ""
    echo "═══════════════════════════════════════════════════════"

    if [[ $TESTS_FAILED -eq 0 ]]; then
        echo -e "  ${GREEN}${BOLD}TODOS OS TESTES PASSARAM${NC}"
        echo "═══════════════════════════════════════════════════════"
        return 0
    else
        echo -e "  ${RED}${BOLD}$TESTS_FAILED TESTE(S) FALHARAM${NC}"
        echo "═══════════════════════════════════════════════════════"
        return 1
    fi
}

# Reseta contadores (util para testes isolados)
reset_counters() {
    TESTS_PASSED=0
    TESTS_FAILED=0
    TESTS_SKIPPED=0
}

# Exporta funcoes
export -f assert_eq assert_not_eq assert_contains assert_not_contains
export -f assert_matches assert_exit_code assert_success assert_failure
export -f assert_file_exists assert_dir_exists assert_file_contains
export -f assert_json_field assert_json_has_field assert_timeout
export -f skip_if print_summary reset_counters

# Exporta contadores
export TESTS_PASSED TESTS_FAILED TESTS_SKIPPED
