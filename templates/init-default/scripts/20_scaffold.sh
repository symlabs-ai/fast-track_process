#!/usr/bin/env bash
# Arquivos base do projeto. Só cria o que não existe — nunca sobrescreve
# nada de um projeto adotado.
set -euo pipefail

if [ ! -e .gitignore ]; then
  cat > .gitignore <<'EOF'
# Segredos nunca entram no Git (regra Symlabs).
.env
.env.local
.env.*.local

# Ambientes e artefatos de build.
.venv/
venv/
__pycache__/
*.pyc
node_modules/
dist/
build/

# Editor/OS.
.DS_Store
EOF
  echo "criado .gitignore"
fi

if [ ! -e .env.example ]; then
  cat > .env.example <<'EOF'
# Copie para .env e preencha os valores locais (o .env é gitignored).
# Credenciais de produção vivem no SymVault — solicite via DevOps.
EOF
  echo "criado .env.example"
fi

if [ ! -e README.md ]; then
  printf '# %s\n' "$(basename "${FT_PROJECT_ROOT:-$PWD}")" > README.md
  echo "criado README.md"
fi

# Playbook dos agentes distribuído com o engine.
if [ ! -e AGENTS.md ] && [ -n "${FT_ENGINE_ROOT:-}" ] && [ -f "${FT_ENGINE_ROOT}/AGENTS.md" ]; then
  cp "${FT_ENGINE_ROOT}/AGENTS.md" AGENTS.md
  echo "criado AGENTS.md"
fi
