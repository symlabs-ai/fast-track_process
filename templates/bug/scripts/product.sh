#!/usr/bin/env bash
set -euo pipefail

find_ft_root() {
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

resolve_product_root() {
  local root="$1" candidate found=""
  for candidate in project src; do
    if test -f "$root/$candidate/Makefile"; then
      if test -n "$found"; then
        printf 'ERRO: mais de um diretório de produto possui Makefile: %s e %s\n' \
          "$found" "$candidate" >&2
        return 1
      fi
      found="$candidate"
    fi
  done
  if test -z "$found"; then
    printf 'ERRO: Makefile ausente; esperado em project/ ou src/\n' >&2
    return 1
  fi
  printf '%s\n' "$found"
}

ROOT="$(find_ft_root)" || {
  printf 'ERRO: raiz do projeto FT não encontrada\n' >&2
  exit 1
}
PRODUCT_REL="$(resolve_product_root "$ROOT")"
PRODUCT_ROOT="$ROOT/$PRODUCT_REL"
VALIDATOR="$ROOT/.ft/process/bug/scripts/validate_bug.py"

usage() {
  printf '%s\n' \
    "Uso: $0 path|build|test|run|url|status" \
    "     $0 red|green -- <comando> [argumentos...]" \
    "     $0 focal -- <comando> [argumentos...]" >&2
}

case "${1:-path}" in
  path)
    printf '%s\n' "$PRODUCT_REL"
    ;;
  build|test)
    exec env -u MAKEFLAGS -u MFLAGS -u GNUMAKEFLAGS \
      make -C "$PRODUCT_ROOT" "$1"
    ;;
  run)
    exec make -C "$PRODUCT_ROOT" run
    ;;
  url)
    exec make -s --no-print-directory -C "$PRODUCT_ROOT" url
    ;;
  status)
    exec python "$VALIDATOR" status
    ;;
  red|green)
    phase="$1"
    shift
    if test "${1:-}" != "--"; then
      usage
      exit 2
    fi
    shift
    if test "$#" -eq 0; then
      usage
      exit 2
    fi
    exec python "$VALIDATOR" "$phase" -- "$@"
    ;;
  focal)
    shift
    if test "${1:-}" != "--"; then
      usage
      exit 2
    fi
    shift
    if test "$#" -eq 0; then
      usage
      exit 2
    fi
    (cd "$PRODUCT_ROOT" && exec "$@")
    ;;
  *)
    usage
    exit 2
    ;;
esac
