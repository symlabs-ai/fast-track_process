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
RECEIPT_HELPER="$ROOT/.ft/process/feature/scripts/product_receipt.py"

resolve_python() {
  if test -n "${PYTHON:-}"; then
    command -v "$PYTHON" >/dev/null 2>&1 || {
      printf 'ERRO: interpretador PYTHON não encontrado: %s\n' "$PYTHON" >&2
      return 1
    }
    printf '%s\n' "$PYTHON"
  elif command -v python3 >/dev/null 2>&1; then
    printf '%s\n' python3
  elif command -v python >/dev/null 2>&1; then
    printf '%s\n' python
  else
    printf 'ERRO: Python não encontrado para gerar o receipt de validação\n' >&2
    return 1
  fi
}

receipt_tool() {
  test -f "$RECEIPT_HELPER" || {
    printf 'ERRO: helper de receipt ausente: %s\n' "$RECEIPT_HELPER" >&2
    return 1
  }
  local python_bin
  python_bin="$(resolve_python)" || return 1
  "$python_bin" "$RECEIPT_HELPER" "$@" \
    --root "$ROOT" --product-root "$PRODUCT_REL"
}

usage() {
  cat >&2 <<EOF
Uso:
  $0 path|test|build|run|url
  $0 full --record <receipt.json>
  $0 verify <receipt.json>
  $0 focal -- <comando> [argumentos...]

full executa build+test e grava um receipt ligado aos arquivos, lockfiles,
scripts, versões das ferramentas e comandos exatos. verify não reexecuta a
suíte: aceita somente um receipt PASS correspondente ao estado atual. focal
executa um comando direto (sem eval) a partir de $PRODUCT_REL/.
EOF
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
  full)
    shift
    if test "$#" -ne 2 || test "$1" != "--record" || test -z "$2"; then
      usage
      exit 2
    fi
    receipt="$2"
    receipt_tool invalidate --receipt "$receipt"
    before="$(receipt_tool fingerprint)"
    if (cd "$ROOT" && env -u MAKEFLAGS -u MFLAGS -u GNUMAKEFLAGS make -C "$PRODUCT_REL" build); then
      :
    else
      status="$?"
      printf 'ERRO: validação completa falhou em build; receipt não foi gravado\n' >&2
      exit "$status"
    fi
    if (cd "$ROOT" && env -u MAKEFLAGS -u MFLAGS -u GNUMAKEFLAGS make -C "$PRODUCT_REL" test); then
      :
    else
      status="$?"
      printf 'ERRO: validação completa falhou em test; receipt não foi gravado\n' >&2
      exit "$status"
    fi
    receipt_tool record --receipt "$receipt" --expected "$before"
    ;;
  verify)
    shift
    if test "$#" -ne 1 || test -z "$1"; then
      usage
      exit 2
    fi
    receipt_tool verify --receipt "$1"
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
