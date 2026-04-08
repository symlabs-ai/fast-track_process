#!/usr/bin/env bash
# Registra o projeto no SymGateway e configura CLAUDE.md + settings.
# Requer: SYM_GATEWAY_PROJECT_KEY (e opcionalmente SYM_GATEWAY_ADMIN_KEY)
set -e

PROJECT_NAME=$(basename "$(pwd)")
GATEWAY_URL="https://symgateway.symlabs.ai"

# Verificar se já está configurado
if [ -f CLAUDE.md ] && grep -q "gateway_project" CLAUDE.md; then
    echo "  → SymGateway: projeto já configurado"
    exit 0
fi

# Verificar chave do projeto (obrigatória)
if [ -z "${SYM_GATEWAY_PROJECT_KEY:-}" ]; then
    echo ""
    echo "  ✗ SYM_GATEWAY_PROJECT_KEY não definida"
    echo ""
    echo "    Exporte antes de rodar:"
    echo "      export SYM_GATEWAY_PROJECT_KEY=sk-sym_..."
    echo "      export SYM_GATEWAY_ADMIN_KEY=sk-sym_...  # opcional — usa PROJECT_KEY se ausente"
    echo ""
    exit 1
fi

PROJECT_KEY="$SYM_GATEWAY_PROJECT_KEY"
ADMIN_KEY="${SYM_GATEWAY_ADMIN_KEY:-$PROJECT_KEY}"

# Criar CLAUDE.md com gateway_project
if [ ! -f CLAUDE.md ]; then
    echo "gateway_project: ${PROJECT_NAME}" > CLAUDE.md
    echo "  → CLAUDE.md criado (gateway_project: ${PROJECT_NAME})"
else
    echo -e "gateway_project: ${PROJECT_NAME}\n$(cat CLAUDE.md)" > CLAUDE.md
    echo "  → CLAUDE.md atualizado (gateway_project: ${PROJECT_NAME})"
fi

# Criar .claude/settings.local.json com ANTHROPIC_BASE_URL
mkdir -p .claude
BASE_URL="${GATEWAY_URL}/u/${PROJECT_KEY}/p/anthropic-max/s/${PROJECT_NAME}"
cat > .claude/settings.local.json <<EOF
{
  "env": {
    "ANTHROPIC_BASE_URL": "${BASE_URL}"
  }
}
EOF
echo "  → .claude/settings.local.json criado (ANTHROPIC_BASE_URL configurado)"

# Registrar projeto via API (best-effort)
RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" \
    -X POST "${GATEWAY_URL}/projects" \
    -H "Authorization: Bearer ${ADMIN_KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"name\":\"${PROJECT_NAME}\",\"slug\":\"${PROJECT_NAME}\",\"folder_name\":\"${PROJECT_NAME}\"}" \
    2>/dev/null) || true

case "$RESPONSE" in
    200|201) echo "  → SymGateway: projeto '${PROJECT_NAME}' registrado" ;;
    409)     echo "  → SymGateway: projeto '${PROJECT_NAME}' já existe — ok" ;;
    403)     echo "  ⚠ SymGateway: SYM_GATEWAY_ADMIN_KEY sem permissão — projeto pode não estar registrado" ;;
    *)       echo "  ⚠ SymGateway: registro retornou HTTP ${RESPONSE} — continuando" ;;
esac
