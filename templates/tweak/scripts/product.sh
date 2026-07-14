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
    printf 'ERRO: Makefile do produto ausente; esperado em project/Makefile ou src/Makefile\n' >&2
    return 1
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
  cat >&2 <<EOF
Uso:
  $0 path|quick|run|url
  $0 focal -- <comando> [argumentos...]

quick executa somente make build, com flags herdadas do make removidas.
focal executa um único comando diretamente a partir de $PRODUCT_REL/, sem eval.
EOF
}

case "${1:-path}" in
  path)
    printf '%s\n' "$PRODUCT_REL"
    ;;
  quick)
    exec python "$ROOT/.ft/process/tweak/scripts/validate_tweak.py" quick
    ;;
  run)
    exec make -C "$PRODUCT_ROOT" run
    ;;
  url)
    exec make -s --no-print-directory -C "$PRODUCT_ROOT" url
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
    exec python "$ROOT/.ft/process/tweak/scripts/validate_tweak.py" focal -- "$@"
    ;;
  *)
    usage
    exit 2
    ;;
esac
