#!/usr/bin/env bash
# Sobe backend + frontend (Next) em portas livres, captura screenshots M3 em
# múltiplos breakpoints/temas e derruba tudo. Best-effort e idempotente.
# Uso: capture_with_server.sh [out_dir]   (default docs/mdpwa-screenshots)
set -uo pipefail

find_root() {
  local c; c="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
  while test "$c" != "/"; do
    test -f "$c/.ft/manifest.yml" && { printf '%s\n' "$c"; return 0; }
    c="$(dirname "$c")"
  done
  return 1
}
ROOT="$(find_root)" || { echo "ERRO: raiz FT não encontrada" >&2; exit 1; }
cd "$ROOT"
PRODUCT_REL="$(bash .ft/process/material_design_pwa/scripts/product.sh path)"
PROJECT="$ROOT/$PRODUCT_REL"
OUT="${1:-docs/mdpwa-screenshots}"

free_port() { python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()'; }
BE_PORT="$(free_port)"; FE_PORT="$(free_port)"
BE_PID=""; FE_PID=""
cleanup() {
  [ -n "$FE_PID" ] && kill -- "-$FE_PID" 2>/dev/null || true
  [ -n "$BE_PID" ] && kill -- "-$BE_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Backend
( cd "$PROJECT" && exec setsid env PORT="$BE_PORT" python3 -m uvicorn backend.main:app --host 127.0.0.1 --port "$BE_PORT" ) >/tmp/mdpwa_be.log 2>&1 &
BE_PID=$!
# Frontend (Next) — deps garantidas pelo make deps
( cd "$PROJECT" && make deps >/dev/null 2>&1; cd "$PROJECT/frontend" && exec setsid env BACKEND_URL="http://127.0.0.1:$BE_PORT" npx next dev -p "$FE_PORT" ) >/tmp/mdpwa_fe.log 2>&1 &
FE_PID=$!

# Espera o frontend responder
ready=0
for _ in $(seq 1 120); do
  if curl -sf "http://127.0.0.1:$FE_PORT/" >/dev/null 2>&1; then ready=1; break; fi
  sleep 1
done
if [ "$ready" -ne 1 ]; then
  echo "ERRO: frontend não subiu em 120s" >&2
  tail -20 /tmp/mdpwa_fe.log >&2 || true
  exit 1
fi

python3 .ft/process/material_design_pwa/scripts/capture_mdpwa_screenshots.py "http://127.0.0.1:$FE_PORT" "$OUT"
rc=$?
exit $rc
