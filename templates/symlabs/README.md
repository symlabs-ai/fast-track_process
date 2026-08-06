# Template `symlabs` — ambiente Claude + Codex da Symlabs

Template de **inicialização** (`kind: init`) para projetos do workspace
**Symlabs [DEV]**. O Codex/OpenAI passa a ser o executor default do Fast Track,
mas o Claude/Anthropic continua configurado e disponível no mesmo projeto.

```bash
ft init meu-projeto --template symlabs   # init-default → symlabs
ft init . --fix --template symlabs       # valida e repara a configuração
```

## O que o `provision.sh` faz

1. Carrega a configuração administrativa de `environment/symlabs.env` no repo
   do engine.
2. Cria o scaffold Poetry (`pyproject.toml`, `src/`, `docs/`, `tests/`) e o
   `.env` de desenvolvimento sem sobrescrever arquivos de produto existentes.
3. Cria uma única vez o profile global
   `~/.codex/symgateway-dev.config.toml`. Se já existir, valida todos os campos
   de provider em vez de sobrescrevê-lo.
4. Registra o projeto no SymGateway e cria uma caller key exclusiva chamada
   `ft-<slug>`, já vinculada ao projeto.
5. Salva o segredo somente em `.envrc.private` com mode `0600` e garante que o
   arquivo esteja no `.gitignore`.
6. Cria o `.envrc` versionável com slug, Codex como engine default do FT,
   profile `symgateway-dev` e carregamento do arquivo privado.
7. Configura o Claude em `.claude/settings.local.json` com a mesma caller
   dedicada no header e a rota `anthropic-max`, sem login OAuth local.
8. Cria ou complementa o `AGENTS.md` com a regra obrigatória: Codex/OpenAI e
   Claude/Anthropic só podem ser usados por meio do SymGateway, sem fallback
   direto para os providers.

O profile global não contém chave nem projeto. O header `X-Project-Slug` e a
caller são resolvidos pelas variáveis carregadas em cada repositório.

## Profile Codex compartilhado

O arquivo criado em `$CODEX_HOME/symgateway-dev.config.toml` (por default,
`~/.codex/symgateway-dev.config.toml`) é:

```toml
model_provider = "symgateway_openai_dev"
model = "gpt-5.6-sol"

[model_providers.symgateway_openai_dev]
name = "SymGateway OpenAI OAuth — Symlabs DEV"
base_url = "https://symgateway.symlabs.ai/p/openai/v1"
env_key = "SYMGATEWAY_API_KEY"
env_http_headers = { "X-Project-Slug" = "SYMGATEWAY_PROJECT_SLUG" }
wire_api = "responses"
supports_websockets = false
```

O FT lê `FT_CODEX_PROFILE` e executa internamente o equivalente a:

```bash
codex --profile symgateway-dev exec ...
```

## Configuração criada no repositório

O `.envrc` é versionável e não contém segredo:

```bash
export SYMGATEWAY_PROJECT_SLUG="meu-projeto"
export FT_LLM_ENGINE="codex"
export FT_CODEX_PROFILE="symgateway-dev"
source_env_if_exists .envrc.private
```

O `.envrc.private` é local, gitignored e contém somente:

```bash
export SYMGATEWAY_API_KEY="sk-sym_..."
```

O Claude recebe a caller pelo arquivo local gitignored
`.claude/settings.local.json`:

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "https://symgateway.symlabs.ai/p/anthropic-max/s/meu-projeto",
    "ANTHROPIC_API_KEY": "sk-sym_..."
  }
}
```

Essa configuração usa a sessão OAuth mantida pelo próprio SymGateway. Não rode
`claude auth login` e não configure credenciais diretas da Anthropic no projeto.

Com direnv:

```bash
direnv allow
ft run . --template mvp-builder-fast --auto
```

Sem direnv, exporte as mesmas variáveis na sessão antes de usar FT ou Codex:

```bash
export SYMGATEWAY_PROJECT_SLUG="meu-projeto"
source .envrc.private
export FT_LLM_ENGINE="codex"
export FT_CODEX_PROFILE="symgateway-dev"
codex --profile symgateway-dev
```

Para selecionar outro modelo apenas neste repositório, crie o arquivo
versionado `.codex/config.toml`:

```toml
model = "gpt-5.6-terra"
```

Provider e autenticação permanecem no profile global; configuração de projeto
serve apenas como override do modelo.

## Configuração administrativa do template

Copie o exemplo versionado e preencha a admin key localmente:

```bash
cp environment/symlabs.env.example environment/symlabs.env
```

| Variável | Descrição | Fonte |
|---|---|---|
| `SYMLABS_GATEWAY_URL` | Base URL do SymGateway | fixo |
| `SYMLABS_WORKSPACE_ID` | UUID do workspace `Symlabs [DEV]` | fixo |
| `SYMLABS_ANTHROPIC_PROVIDER_PATH` | Provider Account usado pelo Claude | fixo (`anthropic-max`) |
| `SYMLABS_ADMIN_KEY` | Registra o projeto e cria a caller dedicada | **DevOps / SymVault** |

`SYMLABS_PROVIDER_PATH` continua aceito como alias legado do provider
Anthropic. As antigas `SYMLABS_CALLER_KEY` e `SYMLABS_CALLER_KEY_ID` não são
mais necessárias para novos projetos.

## Idempotência e recuperação

- `409` ao registrar um projeto só é aceito após confirmar que o
  `folder_name` pertence ao diretório corrente.
- Em `--fix`, o template compara o prefixo da chave local com a caller remota e
  confirma o vínculo ao projeto; se ela estiver apenas desvinculada, religa-a.
- Se `ft-<slug>` existir remotamente mas `.envrc.private` estiver ausente, o
  init falha sem criar uma segunda key. O SymGateway mostra o segredo bruto uma
  única vez; restaure-o pelo secret store ou faça uma rotação deliberada.
- Profile global, `.envrc` ou `CLAUDE.md` com valores conflitantes também
  bloqueiam o init em vez de serem sobrescritos silenciosamente.

## Segurança e escopo

- A admin key é enviada ao `curl` por arquivo temporário mode `0600`; não entra
  na linha de comando nem em arquivo do projeto.
- A caller bruta não aparece nos argumentos do `curl`, logs do provisionamento
  ou arquivos versionados.
- `.envrc.private`, `.claude/settings.local.json`, `CLAUDE.md` e `.env` são
  gitignored; os dois arquivos que contêm a caller usam mode `0600`.
- O `AGENTS.md` versionável exige SymGateway para ambos os agentes e proíbe
  autenticação ou fallback direto na OpenAI e na Anthropic.
- Caller key e `X-Project-Slug` devem apontar para o mesmo projeto, senão o
  gateway retorna `403`.
- A Provider Account OpenAI API e sua credencial OAuth pertencem ao workspace
  Symlabs [DEV]. Projetos PROD exigem uma Provider Account OAuth no PROD.
- Os projetos DEV compartilham a mesma conta e cota OpenAI atualmente pinada no
  Provider Account; a caller separada mantém auditoria, rate limit e revogação
  por projeto.

Referência de configuração do Codex:
[Profiles e custom providers](https://learn.chatgpt.com/docs/config-file/config-advanced#profiles).
