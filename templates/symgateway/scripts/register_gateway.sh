#!/usr/bin/env bash
# Registra o projeto no SymGateway e configura CLAUDE.md + settings.
# Requer: SYMGATEWAY_KEY (ou --key no ft run)
set -e

PROJECT_NAME=$(basename "$(pwd)")
GATEWAY_URL="https://symgateway.symlabs.ai"

# Verificar se já está configurado
if [ -f CLAUDE.md ] && grep -q "gateway_project" CLAUDE.md; then
    echo "  → SymGateway: projeto já configurado"
    exit 0
fi

# Verificar key
KEY="${SYMGATEWAY_KEY:-}"
if [ -z "$KEY" ]; then
    echo "  ⚠ SymGateway: SYMGATEWAY_KEY não definida — pulando registro"
    echo "    Use: ft run . --key <sk-sym_...> ou exporte SYMGATEWAY_KEY"
    exit 0
fi

# Criar CLAUDE.md com gateway_project
if [ ! -f CLAUDE.md ]; then
    echo "gateway_project: ${PROJECT_NAME}" > CLAUDE.md
    echo "  → CLAUDE.md criado (gateway_project: ${PROJECT_NAME})"
else
    # Adicionar gateway_project no topo se ausente
    echo -e "gateway_project: ${PROJECT_NAME}\n$(cat CLAUDE.md)" > CLAUDE.md
    echo "  → CLAUDE.md atualizado (gateway_project: ${PROJECT_NAME})"
fi

# Criar .claude/settings.local.json com ANTHROPIC_BASE_URL
mkdir -p .claude
BASE_URL="${GATEWAY_URL}/u/${KEY}/p/anthropic-max/s/${PROJECT_NAME}"
cat > .claude/settings.local.json <<EOF
{
  "env": {
    "ANTHROPIC_BASE_URL": "${BASE_URL}"
  }
}
EOF
echo "  → .claude/settings.local.json criado (ANTHROPIC_BASE_URL configurado)"

# Tentar registrar o projeto via API (best-effort)
ADMIN_KEY="${SYMGATEWAY_ADMIN_KEY:-$KEY}"
RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" \
    -X POST "${GATEWAY_URL}/projects" \
    -H "Authorization: Bearer ${ADMIN_KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"name\":\"${PROJECT_NAME}\",\"slug\":\"${PROJECT_NAME}\",\"folder_name\":\"${PROJECT_NAME}\"}" \
    2>/dev/null) || true

case "$RESPONSE" in
    200|201) echo "  → SymGateway: projeto '${PROJECT_NAME}' registrado" ;;
    409)     echo "  → SymGateway: projeto '${PROJECT_NAME}' já existe — ok" ;;
    403)     echo "  ⚠ SymGateway: key sem permissão admin — projeto pode não estar registrado" ;;
    *)       echo "  ⚠ SymGateway: registro retornou HTTP ${RESPONSE} — continuando" ;;
esac
