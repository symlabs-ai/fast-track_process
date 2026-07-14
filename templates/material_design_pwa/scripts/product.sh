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
  local root="$1"
  local candidate found=""
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
    if test -f "$root/Makefile"; then
      found="."
    else
      printf 'ERRO: Makefile do produto ausente; esperado em project/Makefile, src/Makefile ou Makefile na raiz\n' >&2
      return 1
    fi
  fi
  printf '%s\n' "$found"
}

ROOT="$(find_ft_root)" || {
  printf 'ERRO: raiz do projeto FT não encontrada a partir de %s\n' "${BASH_SOURCE[0]}" >&2
  exit 1
}
PRODUCT_REL="$(resolve_product_root "$ROOT")"
PRODUCT_ROOT="$ROOT/$PRODUCT_REL"

usage() {
  printf 'Uso: %s path|build|test|run|url\n' "$0" >&2
}

case "${1:-path}" in
  path)
    printf '%s\n' "$PRODUCT_REL"
    ;;
  test|build)
    exec env -u MAKEFLAGS -u MFLAGS -u GNUMAKEFLAGS make -C "$PRODUCT_ROOT" "$1"
    ;;
  run)
    exec make -C "$PRODUCT_ROOT" "$1"
    ;;
  url)
    exec make -s --no-print-directory -C "$PRODUCT_ROOT" url
    ;;
  *)
    usage
    exit 2
    ;;
esac
