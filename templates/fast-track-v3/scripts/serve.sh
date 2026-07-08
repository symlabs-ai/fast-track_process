#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

BASE_PORT="${PORT:-${SERVICE_MATE_PORT:-8021}}"
case "$BASE_PORT" in
  ''|*[!0-9]*) BASE_PORT=8021 ;;
esac
EXPECTED_PROJECT_ROOT="$(cd project && pwd)"

is_current_server() {
  local url="$1"
  curl -sf "$url/health" 2>/dev/null | python -c 'import json,sys; data=json.load(sys.stdin); sys.exit(0 if data.get("project_root")==sys.argv[1] else 1)' "$EXPECTED_PROJECT_ROOT" >/dev/null 2>&1
}

PORT="$BASE_PORT"
for candidate in $(seq "$BASE_PORT" "$((BASE_PORT + 50))"); do
  candidate_url="http://127.0.0.1:$candidate"
  if is_current_server "$candidate_url"; then
    PORT="$candidate"
    export PORT
    export SERVICE_MATE_PORT="$PORT"
    printf '%s\n' "$candidate_url" > .serve_url
    exit 0
  fi
  if ! fuser "$candidate/tcp" >/dev/null 2>&1; then
    PORT="$candidate"
    break
  fi
done

export PORT
export SERVICE_MATE_PORT="$PORT"

URL="$(cd project && make -s url)"
printf '%s\n' "$URL" > .serve_url

if is_current_server "$URL"; then
  exit 0
fi

rm -f .serve.pid .serve.log
(
  cd project
  if command -v setsid >/dev/null 2>&1; then
    setsid env PORT="$PORT" SERVICE_MATE_PORT="$PORT" make run > ../.serve.log 2>&1 < /dev/null &
  else
    nohup env PORT="$PORT" SERVICE_MATE_PORT="$PORT" make run > ../.serve.log 2>&1 < /dev/null &
  fi
  printf '%s\n' "$!" > ../.serve.pid
)

for _ in $(seq 1 50); do
  if is_current_server "$URL"; then
    exit 0
  fi
  sleep 0.2
done

cat .serve.log >&2 2>/dev/null || true
exit 1
