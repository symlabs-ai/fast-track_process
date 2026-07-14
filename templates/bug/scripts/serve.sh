#!/usr/bin/env bash
set -euo pipefail

find_project_root() {
  local current
  current="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
  while test "$current" != "/"; do
    if test -f "$current/.ft/manifest.yml"; then
      printf '%s\n' "$current"
      return 0
    fi
    current="$(dirname "$current")"
  done
  return 1
}

ROOT="$(find_project_root)" || {
  printf 'ERRO: raiz do projeto FT não encontrada\n' >&2
  exit 1
}
PRODUCT_HELPER="$ROOT/.ft/process/bug/scripts/product.sh"
PRODUCT_REL="$(bash "$PRODUCT_HELPER" path)"
PRODUCT_ROOT="$ROOT/$PRODUCT_REL"
cd "$ROOT"

BASE_PORT="${PORT:-8021}"
case "$BASE_PORT" in
  ''|*[!0-9]*) BASE_PORT=8021 ;;
esac

process_pid() {
  local token
  token="$(cat .serve.pid 2>/dev/null || true)"
  printf '%s\n' "${token#*:}"
}

stop_owned_server() {
  if ! test -s .serve.pid; then
    rm -f .serve_url .serve.log
    return 0
  fi
  local token mode pid cwd
  token="$(cat .serve.pid 2>/dev/null || true)"
  mode="${token%%:*}"
  pid="${token#*:}"
  cwd="$(readlink -f "/proc/$pid/cwd" 2>/dev/null || true)"
  if test -n "$pid" && test "$cwd" = "$(cd "$PRODUCT_ROOT" && pwd -P)"; then
    if test "$mode" = "group"; then
      kill -- "-$pid" 2>/dev/null || true
    else
      kill "$pid" 2>/dev/null || true
    fi
    for _ in $(seq 1 40); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.1
    done
  fi
  rm -f .serve.pid .serve_url .serve.log
}

case "${1:-start}" in
  start) ;;
  stop)
    stop_owned_server
    exit 0
    ;;
  *)
    printf 'Uso: %s [start|stop]\n' "$0" >&2
    exit 2
    ;;
esac

owned_server_is_ready() {
  test -s .serve.pid && test -s .serve_url || return 1
  local pid url cwd
  pid="$(process_pid)"
  url="$(cat .serve_url)"
  test -n "$pid" && kill -0 "$pid" 2>/dev/null || return 1
  cwd="$(readlink -f "/proc/$pid/cwd" 2>/dev/null || true)"
  test "$cwd" = "$(cd "$PRODUCT_ROOT" && pwd -P)" || return 1
  curl -sf "$url/health" >/dev/null 2>&1
}

port_is_free() {
  python3 - "$1" <<'PY'
import socket
import sys

with socket.socket() as sock:
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("127.0.0.1", int(sys.argv[1])))
    except OSError:
        raise SystemExit(1)
PY
}

if owned_server_is_ready; then
  exit 0
fi

PORT=""
for candidate in $(seq "$BASE_PORT" "$((BASE_PORT + 50))"); do
  if port_is_free "$candidate"; then
    PORT="$candidate"
    break
  fi
done
if test -z "$PORT"; then
  PORT="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
fi
export PORT

URL="$(bash "$PRODUCT_HELPER" url)"
printf '%s\n' "$URL" > .serve_url
rm -f .serve.pid .serve.log

if command -v setsid >/dev/null 2>&1; then
  (cd "$PRODUCT_ROOT" && exec setsid env PORT="$PORT" make run) > .serve.log 2>&1 < /dev/null &
  printf 'group:%s\n' "$!" > .serve.pid
else
  (cd "$PRODUCT_ROOT" && exec env PORT="$PORT" make run) > .serve.log 2>&1 < /dev/null &
  printf 'pid:%s\n' "$!" > .serve.pid
fi

for _ in $(seq 1 80); do
  if owned_server_is_ready; then
    exit 0
  fi
  pid="$(process_pid)"
  if test -z "$pid" || ! kill -0 "$pid" 2>/dev/null; then
    break
  fi
  sleep 0.25
done

cat .serve.log >&2 2>/dev/null || true
exit 1
