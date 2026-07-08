#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

PORT="${PORT:-${SERVICE_MATE_PORT:-8021}}"
export PORT
export SERVICE_MATE_PORT="$PORT"

URL="$(cd project && make -s url)"
printf '%s\n' "$URL" > .serve_url

if curl -sf "$URL/health" >/dev/null 2>&1; then
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
  if curl -sf "$URL/health" >/dev/null 2>&1; then
    exit 0
  fi
  sleep 0.2
done

cat .serve.log >&2 2>/dev/null || true
exit 1
