#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PROJECT_ROOT="$ROOT/src"
cd "$ROOT"

BASE_PORT="${PORT:-8021}"
case "$BASE_PORT" in
  ''|*[!0-9]*) BASE_PORT=8021 ;;
esac
if (( BASE_PORT < 1 || BASE_PORT > 65535 )); then
  BASE_PORT=8021
fi

process_mode() {
  cut -d: -f1 .serve.pid 2>/dev/null || true
}

process_pid() {
  cut -d: -f2 .serve.pid 2>/dev/null || true
}

recorded_process_start() {
  cut -d: -f3 .serve.pid 2>/dev/null || true
}

process_start() {
  awk '{print $22}' "/proc/$1/stat" 2>/dev/null || true
}

process_is_live() {
  local state
  kill -0 "$1" 2>/dev/null || return 1
  state="$(awk '{print $3}' "/proc/$1/stat" 2>/dev/null || true)"
  test -n "$state" && test "$state" != Z
}

owned_process_exists() {
  test -s .serve.pid || return 1
  local pid expected_start actual_start cwd
  pid="$(process_pid)"
  expected_start="$(recorded_process_start)"
  test -n "$pid" && process_is_live "$pid" || return 1
  if test -n "$expected_start"; then
    actual_start="$(process_start "$pid")"
    test "$actual_start" = "$expected_start" || return 1
  fi
  cwd="$(readlink -f "/proc/$pid/cwd" 2>/dev/null || true)"
  test "$cwd" = "$(cd "$PROJECT_ROOT" && pwd -P)"
}

owned_server_is_ready() {
  test -s .serve.pid && test -s .serve_url || return 1
  local url
  owned_process_exists || return 1
  url="$(cat .serve_url)"
  curl --fail --silent --show-error --max-time 1 "$url/health" >/dev/null
}

stop_owned_process() {
  owned_process_exists || return 0
  local mode pid
  mode="$(process_mode)"
  pid="$(process_pid)"
  if test "$mode" = group; then
    kill -- "-$pid" 2>/dev/null || true
  else
    kill "$pid" 2>/dev/null || true
  fi
  for _ in $(seq 1 20); do
    process_is_live "$pid" || return 0
    sleep 0.1
  done
  return 1
}

presentation_mode() {
  cut -d: -f1 .presentation.pid 2>/dev/null || true
}

presentation_pid() {
  cut -d: -f2 .presentation.pid 2>/dev/null || true
}

presentation_start() {
  cut -d: -f3 .presentation.pid 2>/dev/null || true
}

owned_presentation_exists() {
  test -s .presentation.pid || return 1
  local pid expected_start actual_start
  pid="$(presentation_pid)"
  expected_start="$(presentation_start)"
  test -n "$pid" && process_is_live "$pid" || return 1
  actual_start="$(process_start "$pid")"
  test -n "$expected_start" && test "$actual_start" = "$expected_start"
}

stop_owned_presentation() {
  owned_presentation_exists || return 0
  local mode pid
  mode="$(presentation_mode)"
  pid="$(presentation_pid)"
  if test "$mode" = group; then
    kill -- "-$pid" 2>/dev/null || true
  else
    kill "$pid" 2>/dev/null || true
  fi
  for _ in $(seq 1 20); do
    process_is_live "$pid" || return 0
    sleep 0.1
  done
  return 1
}

desktop_linux_selected() {
  test -f docs/validation-matrix.yml || return 1
  grep -Eq \
    '^[[:space:]]*execution_surface:[[:space:]]*desktop_linux([[:space:]]*(#.*)?)$' \
    docs/validation-matrix.yml
}

native_surface_selected() {
  test -f docs/validation-matrix.yml || return 1
  grep -Eq \
    '^[[:space:]]*execution_surface:[[:space:]]*(android_|ios_|desktop_)[[:alnum:]_]*([[:space:]]*(#.*)?)$' \
    docs/validation-matrix.yml
}

present_desktop_linux() {
  local artifact artifact_rel mode pid started
  local -a artifacts
  test -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" || {
    echo "desktop Linux presentation requires DISPLAY or WAYLAND_DISPLAY" >&2
    return 1
  }

  mapfile -d '' artifacts < <(
    find "$PROJECT_ROOT/dist" -maxdepth 4 -type f -name '*.AppImage' \
      -perm -u=x -print0 2>/dev/null
  )
  if ((${#artifacts[@]} != 1)); then
    echo "desktop Linux gate requires exactly one executable AppImage in src/dist; found ${#artifacts[@]}" >&2
    return 1
  fi
  artifact="${artifacts[0]}"
  artifact_rel="${artifact#"$ROOT"/}"

  stop_owned_process || {
    echo "refusing to replace an owned web server that did not stop cleanly" >&2
    return 1
  }
  stop_owned_presentation || {
    echo "refusing to replace an owned desktop presentation that did not stop cleanly" >&2
    return 1
  }
  rm -f .serve.pid .serve.log .serve_url .presented_artifact \
    .presentation.pid .presentation.log

  if command -v setsid >/dev/null 2>&1; then
    setsid "$artifact" > .presentation.log 2>&1 < /dev/null &
    pid=$!
    mode=group
  else
    "$artifact" > .presentation.log 2>&1 < /dev/null &
    pid=$!
    mode=pid
  fi
  started=""
  for _ in $(seq 1 40); do
    started="$(process_start "$pid")"
    test -n "$started" && break
    process_is_live "$pid" || break
    sleep 0.05
  done
  if test -z "$started"; then
    cat .presentation.log >&2 2>/dev/null || true
    return 1
  fi
  printf '%s:%s:%s\n' "$mode" "$pid" "$started" > .presentation.pid

  for _ in $(seq 1 20); do
    if owned_presentation_exists; then
      printf '%s\n' "$artifact_rel" > .presented_artifact
      return 0
    fi
    sleep 0.1
  done
  cat .presentation.log >&2 2>/dev/null || true
  return 1
}

if desktop_linux_selected; then
  present_desktop_linux
  exit $?
fi

stop_owned_presentation || {
  echo "refusing to replace an owned desktop presentation that did not stop cleanly" >&2
  exit 1
}
rm -f .presented_artifact .presentation.pid .presentation.log

# Quando o produto entrega um launcher próprio, ele é a fonte de verdade do
# lifecycle e deve apresentar a superfície selecionada no contrato.
PROJECT_SERVE="$PROJECT_ROOT/scripts/serve.sh"
if test -x "$PROJECT_SERVE"; then
  PORT="$BASE_PORT" exec "$PROJECT_SERVE"
fi

# Uma superfície nativa sem launcher explícito deve falhar fechada. Cair no
# servidor HTTP abaixo apresentaria outro produto e mascararia a ausência do
# entrypoint Android, iOS ou desktop requerido.
if native_surface_selected; then
  echo "native presentation requires executable src/scripts/serve.sh for the selected surface" >&2
  exit 1
fi

port_is_free() {
  python3 - "$1" <<'PY'
import socket
import sys

port = int(sys.argv[1])
with socket.socket() as sock:
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("127.0.0.1", port))
    except OSError:
        raise SystemExit(1)
PY
}

stop_owned_process || {
  echo "refusing to replace an owned server that did not stop cleanly" >&2
  exit 1
}

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

URL="$(cd src && make -s url)"
printf '%s\n' "$URL" > .serve_url
rm -f .serve.pid .serve.log

if command -v setsid >/dev/null 2>&1; then
  (cd src && exec setsid env PORT="$PORT" make run) > .serve.log 2>&1 < /dev/null &
  mode=group
else
  (cd src && exec env PORT="$PORT" make run) > .serve.log 2>&1 < /dev/null &
  mode=pid
fi
server_pid=$!
server_start=""
for _ in $(seq 1 20); do
  server_start="$(process_start "$server_pid")"
  test -n "$server_start" && break
  kill -0 "$server_pid" 2>/dev/null || break
  sleep 0.05
done
if test -z "$server_start"; then
  kill "$server_pid" 2>/dev/null || true
  echo "server process exited before ownership could be recorded" >&2
  exit 1
fi
printf '%s:%s:%s\n' "$mode" "$server_pid" "$server_start" > .serve.pid

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
