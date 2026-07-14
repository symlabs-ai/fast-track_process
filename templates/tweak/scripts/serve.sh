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
  printf 'ERRO: raiz do projeto FT não encontrada a partir de %s\n' "${BASH_SOURCE[0]}" >&2
  exit 1
}
PRODUCT_HELPER="$ROOT/.ft/process/tweak/scripts/product.sh"
PRODUCT_REL="$(bash "$PRODUCT_HELPER" path)"
PRODUCT_ROOT="$ROOT/$PRODUCT_REL"

assert_runtime_paths_safe() {
  local path
  for path in "$ROOT/.serve_url" "$ROOT/.serve.pid" "$ROOT/.serve.log"; do
    if test -L "$path"; then
      printf 'ERRO: arquivo de controle do servidor não pode ser symlink: %s\n' \
        "$path" >&2
      return 1
    fi
  done
}

atomic_write_runtime_file() {
  local path="$1" content="$2" temporary
  if test -L "$path"; then
    printf 'ERRO: recusando escrita em symlink: %s\n' "$path" >&2
    return 1
  fi
  temporary="$(mktemp "$ROOT/.serve-write.XXXXXX")"
  chmod 600 "$temporary"
  printf '%s\n' "$content" > "$temporary"
  if test -L "$path"; then
    rm -f "$temporary"
    printf 'ERRO: recusando escrita em symlink: %s\n' "$path" >&2
    return 1
  fi
  mv -fT "$temporary" "$path"
}

prepare_runtime_log() {
  local path="$ROOT/.serve.log" temporary
  if test -L "$path"; then
    printf 'ERRO: recusando log em symlink: %s\n' "$path" >&2
    return 1
  fi
  temporary="$(mktemp "$ROOT/.serve-log.XXXXXX")"
  chmod 600 "$temporary"
  exec 9> "$temporary"
  if test -L "$path"; then
    exec 9>&-
    rm -f "$temporary"
    printf 'ERRO: recusando log em symlink: %s\n' "$path" >&2
    return 1
  fi
  mv -fT "$temporary" "$path"
}

assert_runtime_paths_safe || exit 1

BASE_PORT="${PORT:-8021}"
case "$BASE_PORT" in
  ''|*[!0-9]*) BASE_PORT=8021 ;;
esac

process_pid() {
  local token
  test ! -L "$ROOT/.serve.pid" || return 1
  token="$(cat "$ROOT/.serve.pid" 2>/dev/null || true)"
  printf '%s\n' "${token#*:}"
}

stop_owned_server() {
  if ! test -s "$ROOT/.serve.pid"; then
    rm -f "$ROOT/.serve_url" "$ROOT/.serve.log"
    return 0
  fi
  local token mode pid cwd
  token="$(cat "$ROOT/.serve.pid" 2>/dev/null || true)"
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
    if kill -0 "$pid" 2>/dev/null; then
      if test "$mode" = "group"; then
        kill -KILL -- "-$pid" 2>/dev/null || true
      else
        kill -KILL "$pid" 2>/dev/null || true
      fi
    fi
  fi
  rm -f "$ROOT/.serve.pid" "$ROOT/.serve_url" "$ROOT/.serve.log"
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
  test -s "$ROOT/.serve.pid" && test -s "$ROOT/.serve_url" || return 1
  local expected_port="${1:-}" pid url cwd
  test ! -L "$ROOT/.serve.pid" && test ! -L "$ROOT/.serve_url" || return 1
  pid="$(process_pid)"
  url="$(cat "$ROOT/.serve_url")"
  test -n "$pid" && kill -0 "$pid" 2>/dev/null || return 1
  cwd="$(readlink -f "/proc/$pid/cwd" 2>/dev/null || true)"
  test "$cwd" = "$(cd "$PRODUCT_ROOT" && pwd -P)" || return 1
  owned_process_listens_on_url "$pid" "$url" "$expected_port" || return 1
  curl -sf "$url/health" >/dev/null 2>&1
}

owned_process_listens_on_url() {
  python3 - "$1" "$2" "${3:-}" <<'PY'
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

try:
    root_pid = int(sys.argv[1])
    port = urlsplit(sys.argv[2]).port
    expected_port = int(sys.argv[3]) if sys.argv[3] else port
except (TypeError, ValueError):
    raise SystemExit(1)
if port is None or port != expected_port or not 1 <= port <= 65535:
    raise SystemExit(1)

pending = [root_pid]
owned_pids = set()
while pending:
    pid = pending.pop()
    if pid in owned_pids or not Path(f"/proc/{pid}").is_dir():
        continue
    owned_pids.add(pid)
    children_path = Path(f"/proc/{pid}/task/{pid}/children")
    try:
        pending.extend(int(value) for value in children_path.read_text().split())
    except (FileNotFoundError, PermissionError, ProcessLookupError, ValueError):
        pass

listening_inodes = set()
for table_name in ("tcp", "tcp6"):
    try:
        rows = Path(f"/proc/net/{table_name}").read_text().splitlines()[1:]
    except (FileNotFoundError, PermissionError):
        continue
    for row in rows:
        fields = row.split()
        if len(fields) < 10 or fields[3] != "0A":
            continue
        try:
            local_port = int(fields[1].rsplit(":", 1)[1], 16)
        except (IndexError, ValueError):
            continue
        if local_port == port:
            listening_inodes.add(fields[9])

if not listening_inodes:
    raise SystemExit(1)

for pid in owned_pids:
    fd_dir = Path(f"/proc/{pid}/fd")
    try:
        descriptors = list(fd_dir.iterdir())
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        continue
    for descriptor in descriptors:
        try:
            target = os.readlink(descriptor)
        except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
            continue
        if target.startswith("socket:[") and target[8:-1] in listening_inodes:
            raise SystemExit(0)
raise SystemExit(1)
PY
}

if owned_server_is_ready; then
  exit 0
fi

log_reports_port_collision() {
  test ! -L "$ROOT/.serve.log" || return 1
  grep -Eiq \
    'address already in use|eaddrinuse|errno[[:space:]]*98|port [0-9]+ (is )?(already )?in use' \
    "$ROOT/.serve.log" 2>/dev/null
}

start_candidate() {
  local candidate="$1" url pid token
  export PORT="$candidate"
  if ! url="$(bash "$PRODUCT_HELPER" url)"; then
    printf 'ERRO: não foi possível resolver a URL para a porta %s\n' \
      "$candidate" >&2
    return 1
  fi
  atomic_write_runtime_file "$ROOT/.serve_url" "$url" || return 1
  prepare_runtime_log || return 1

  if command -v setsid >/dev/null 2>&1; then
    (cd "$PRODUCT_ROOT" && exec setsid env PORT="$PORT" make run) \
      >&9 2>&1 < /dev/null &
    pid="$!"
    token="group:$pid"
  else
    (cd "$PRODUCT_ROOT" && exec env PORT="$PORT" make run) \
      >&9 2>&1 < /dev/null &
    pid="$!"
    token="pid:$pid"
  fi
  exec 9>&-
  if ! atomic_write_runtime_file "$ROOT/.serve.pid" "$token"; then
    if test "${token%%:*}" = "group"; then
      kill -- "-$pid" 2>/dev/null || true
    else
      kill "$pid" 2>/dev/null || true
    fi
    return 1
  fi

  for _ in $(seq 1 40); do
    if owned_server_is_ready "$candidate"; then
      return 0
    fi
    pid="$(process_pid)"
    if test -z "$pid" || ! kill -0 "$pid" 2>/dev/null; then
      break
    fi
    sleep 0.25
  done

  if log_reports_port_collision; then
    stop_owned_server
    return 75
  fi

  if ! test -L "$ROOT/.serve.log"; then
    cat "$ROOT/.serve.log" >&2 2>/dev/null || true
  fi
  stop_owned_server
  return 1
}

for candidate in $(seq "$BASE_PORT" "$((BASE_PORT + 50))"); do
  if start_candidate "$candidate"; then
    exit 0
  else
    status="$?"
  fi
  if test "$status" -eq 75; then
    continue
  fi
  exit "$status"
done

printf 'ERRO: todas as portas de %s a %s estão em uso\n' \
  "$BASE_PORT" "$((BASE_PORT + 50))" >&2
exit 1
