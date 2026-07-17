# Template `symlabs` — inicialização de projeto da organização Symlabs

Template de **inicialização** (`kind: init`): roda uma única vez no `ft init`
de um projeto novo para prepará-lo no ambiente da organização **Symlabs**.
Não é um processo de ciclo — `ft run` o recusa.

```bash
ft init meu-projeto --template symlabs   # cadeia init-default → symlabs
ft init . --fix --template symlabs       # re-executa para consertar o ambiente
```

`ft init --template symlabs` roda primeiro o `init-default` (git, `.gitignore`,
`.env.example`, commit inicial) e depois este template.

## O que o `provision.sh` faz

1. **Carrega a config da org** de `environment/symlabs.env` no repo do engine
   (ver [configuração](#configuração)). Falha alto se ausente/incompleta.
2. **Scaffold Poetry**: `pyproject.toml` (v0.0.1), `src/<nome>/`, `docs/`,
   `tests/`, `.env` de dev (`DEV_MODE=true`). Roda `poetry install`
   best-effort. Não sobrescreve arquivos existentes.
3. **Registra o projeto no SymGateway** (workspace da org):
   - `POST /_api/projects` — 409 (já existe) é idempotente; antes de adotar,
     confere o `folder_name` e **falha se o slug for de outro projeto**.
   - Linka a caller key ao projeto (se `CALLER_KEY_ID` informado).
   - Escreve `CLAUDE.md` (`gateway_project`) e `.claude/settings.local.json`
     com `ANTHROPIC_BASE_URL` roteando pelo gateway.

Idempotente: rodar de novo (ou `--fix`) é seguro. O marker
`.ft/runtime/init.yml` (gitignored) impede re-execução acidental por ciclo.

## Configuração

A identidade da organização vive em `environment/symlabs.env` no repo do
engine — **gitignored** (`environment/*.env`), nunca no bundle do template.
Copie do exemplo versionado e preencha:

```bash
cp environment/symlabs.env.example environment/symlabs.env
```

| Variável | Descrição | Fonte |
|---|---|---|
| `SYMLABS_GATEWAY_URL` | Base URL do SymGateway | fixo |
| `SYMLABS_WORKSPACE_ID` | UUID do workspace `Symlabs [DEV]` | fixo |
| `SYMLABS_PROVIDER_PATH` | Provider de roteamento (ex.: `anthropic-max`) | fixo |
| `SYMLABS_ADMIN_KEY` | Admin key do workspace — registra o projeto e linka a caller | **DevOps / SymVault** |
| `SYMLABS_CALLER_KEY` | Caller key (vai na URL do `settings.local.json`) | **DevOps / SymVault** |
| `SYMLABS_CALLER_KEY_ID` | UUID da caller, opcional (para o link) | **DevOps / SymVault** |

Credenciais nunca são pedidas ao usuário nem entram no repo do projeto:
solicite ao DevOps (`/ask devops`). A **admin key** só registra o projeto e
jamais é escrita em arquivo do projeto; o `settings.local.json` (gitignored)
recebe apenas a **caller key** na URL.

## Segurança

- Header de gestão do gateway: `Authorization: Bearer <ADMIN_KEY>`.
- `.claude/settings.local.json` e `.env` são gitignored (via `init-default`).
- Registro é best-effort: falhas de rede não bloqueiam o init; falha de auth
  (401/403) é reportada de forma visível, não silenciosa.

## Como estender para outra organização

O `provision.sh` é **genérico**: resolve a organização pelo nome do próprio
template (`FT_TEMPLATE_DIR`) e lê `environment/<org>.env`. Para uma nova org,
copie a pasta do template, ajuste `template.yml` e crie
`environment/<org>.env.example`. Ver o template irmão `tecnospeed`.
