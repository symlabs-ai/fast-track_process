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
  local validation_kind="${1:?validation kind ausente}"
  shift
  python_bin="$(resolve_python)" || return 1
  "$python_bin" "$RECEIPT_HELPER" "$@" \
    --root "$ROOT" --product-root "$PRODUCT_REL" \
    --validation-kind "$validation_kind"
}

usage() {
  cat >&2 <<EOF
Uso:
  $0 path|test|build|run|url
  $0 full --record <receipt.json>
  $0 ensure --record <receipt.json>
  $0 verify <receipt.json>
  $0 ensure-baseline --record <receipt.json>
  $0 verify-baseline <receipt.json>
  $0 focal -- <comando> [argumentos...]

full executa build+test e grava um receipt ligado aos arquivos, lockfiles,
scripts, versões das ferramentas e comandos exatos. verify não reexecuta a
suíte: aceita somente um receipt PASS correspondente ao estado atual. focal
executa um comando direto (sem eval) a partir de $PRODUCT_REL/.
EOF
}

run_full_validation() {
  local validation_kind="$1"
  local receipt="$2"
  receipt_tool "$validation_kind" invalidate --receipt "$receipt"
  local before
  before="$(receipt_tool "$validation_kind" fingerprint)"
  if (cd "$ROOT" && env -u MAKEFLAGS -u MFLAGS -u GNUMAKEFLAGS make -C "$PRODUCT_REL" build); then
    :
  else
    local status="$?"
    printf 'ERRO: validação completa falhou em build; receipt não foi gravado\n' >&2
    return "$status"
  fi
  if (cd "$ROOT" && env -u MAKEFLAGS -u MFLAGS -u GNUMAKEFLAGS make -C "$PRODUCT_REL" test); then
    :
  else
    local status="$?"
    printf 'ERRO: validação completa falhou em test; receipt não foi gravado\n' >&2
    return "$status"
  fi
  receipt_tool "$validation_kind" record --receipt "$receipt" --expected "$before"
}

ensure_validation() {
  local validation_kind="$1"
  local receipt="$2"
  if receipt_tool "$validation_kind" verify --receipt "$receipt" >/dev/null 2>&1; then
    printf 'product validation receipt REUSED: %s\n' "$receipt"
    return 0
  fi
  if test "${FT_FEATURE_SHARED_CACHE:-0}" = "1"; then
    if test "${FT_FEATURE_VALIDATION_HERMETIC:-0}" != "1"; then
      printf 'cache compartilhado ignorado: validação não declarada hermética\n' >&2
    elif command -v flock >/dev/null 2>&1; then
      ensure_from_shared_cache "$validation_kind" "$receipt"
      return $?
    else
      printf 'cache compartilhado ignorado: flock indisponível\n' >&2
    fi
  fi
  run_full_validation "$validation_kind" "$receipt"
}

ensure_from_shared_cache() {
  local validation_kind="$1"
  local receipt="$2"
  local fingerprint key cache_root cache_file lock_file ttl now modified age
  fingerprint="$(receipt_tool "$validation_kind" fingerprint)" || return 1
  key="${fingerprint#sha256:}"
  cache_root="${FT_HOME:-$HOME/.ft}/cache/feature-validation"
  cache_file="$cache_root/${validation_kind}-${key}.json"
  lock_file="$cache_root/${validation_kind}-${key}.lock"
  ttl="${FT_FEATURE_SHARED_CACHE_TTL_SECONDS:-3600}"
  case "$ttl" in
    ''|*[!0-9]*)
      printf 'ERRO: FT_FEATURE_SHARED_CACHE_TTL_SECONDS deve ser inteiro >= 0\n' >&2
      return 2
      ;;
  esac
  mkdir -p "$cache_root"
  exec {cache_lock_fd}>"$lock_file"
  flock -x "$cache_lock_fd"

  if test -f "$cache_file"; then
    now="$(date +%s)"
    modified="$(stat -c %Y "$cache_file" 2>/dev/null || printf '0')"
    age=$((now - modified))
    if test "$age" -le "$ttl"; then
      receipt_tool "$validation_kind" invalidate --receipt "$receipt" || return 1
      cp -- "$cache_file" "$ROOT/$receipt"
      if receipt_tool "$validation_kind" verify --receipt "$receipt" >/dev/null 2>&1; then
        printf 'product validation receipt SHARED-CACHE: %s\n' "$receipt"
        return 0
      fi
      receipt_tool "$validation_kind" invalidate --receipt "$receipt" || return 1
    fi
  fi

  run_full_validation "$validation_kind" "$receipt" || return $?
  local temporary="$cache_file.tmp.$$"
  cp -- "$ROOT/$receipt" "$temporary"
  chmod 600 "$temporary"
  mv -f -- "$temporary" "$cache_file"
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
    run_full_validation implementation "$2"
    ;;
  ensure)
    shift
    if test "$#" -ne 2 || test "$1" != "--record" || test -z "$2"; then
      usage
      exit 2
    fi
    ensure_validation implementation "$2"
    ;;
  verify)
    shift
    if test "$#" -ne 1 || test -z "$1"; then
      usage
      exit 2
    fi
    receipt_tool implementation verify --receipt "$1"
    ;;
  ensure-baseline)
    shift
    if test "$#" -ne 2 || test "$1" != "--record" || test -z "$2"; then
      usage
      exit 2
    fi
    ensure_validation baseline "$2"
    ;;
  verify-baseline)
    shift
    if test "$#" -ne 1 || test -z "$1"; then
      usage
      exit 2
    fi
    receipt_tool baseline verify --receipt "$1"
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
