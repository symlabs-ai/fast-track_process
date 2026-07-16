#!/usr/bin/env bash
# Commit inicial do scaffold. Adiciona apenas caminhos conhecidos —
# a adoção de um diretório legado nunca commita arquivos do produto
# silenciosamente.
set -euo pipefail

known_paths=(
  .ft/manifest.yml
  .ft/.gitignore
  .ft/process/.gitkeep
  .ft/cycles/.gitkeep
  AGENTS.md
)

# Fora da adoção o diretório começou vazio (ou o repo estava limpo), então os
# arquivos base são obra deste template e entram no commit. Na adoção eles
# podem ser legado do usuário — ficam de fora e aparecem no aviso do ft init.
if [ "${FT_ADOPT:-0}" != "1" ]; then
  known_paths+=(.gitignore .env.example README.md)
fi

present=()
for path in "${known_paths[@]}"; do
  [ -e "$path" ] && present+=("$path")
done

[ "${#present[@]}" -eq 0 ] && exit 0

git add -- "${present[@]}"

if ! git diff --cached --quiet -- "${present[@]}"; then
  git -c user.name="Fast Track" -c user.email="ft@localhost" \
    commit -q -m "${FT_COMMIT_MESSAGE:-chore: initialize fast track workspace}" \
    -- "${present[@]}"
  echo "commit criado $(git rev-parse HEAD)"
fi
