# Template `tecnospeed` — inicialização de projeto da organização Tecnospeed

Template de **inicialização** (`kind: init`): roda uma única vez no `ft init`
de um projeto novo para prepará-lo no ambiente da organização **Tecnospeed**.
Não é um processo de ciclo — `ft run` o recusa.

```bash
ft init meu-projeto --template tecnospeed   # cadeia init-default → tecnospeed
ft init . --fix --template tecnospeed        # re-executa para consertar o ambiente
```

Mesma mecânica do template irmão [`symlabs`](../symlabs/README.md) — o
`provision.sh` é **idêntico** e genérico: resolve a organização pelo nome do
template e lê `environment/tecnospeed.env`. A diferença é só a config da org.

## O que o `provision.sh` faz

1. Carrega a config de `environment/tecnospeed.env` (falha alto se ausente).
2. Scaffold Poetry (`pyproject.toml` v0.0.1, `src/<nome>/`, `docs/`, `tests/`,
   `.env` `DEV_MODE=true`) + `poetry install` best-effort.
3. Registra o projeto no workspace da org no SymGateway (`POST /_api/projects`,
   409 idempotente com guard de `folder_name`), linka a caller e escreve
   `CLAUDE.md` + `.claude/settings.local.json`.

## Configuração

```bash
cp environment/tecnospeed.env.example environment/tecnospeed.env
```

| Variável | Descrição | Fonte |
|---|---|---|
| `TECNOSPEED_GATEWAY_URL` | Base URL do SymGateway | fixo |
| `TECNOSPEED_WORKSPACE_ID` | UUID do workspace `Desenvolvimento` | **DevOps** |
| `TECNOSPEED_PROVIDER_PATH` | Provider de roteamento (ex.: `anthropic-max`) | **DevOps** |
| `TECNOSPEED_ADMIN_KEY` | Admin key do workspace | **DevOps / SymVault** |
| `TECNOSPEED_CALLER_KEY` | Caller key `palhano` | **DevOps / SymVault** |
| `TECNOSPEED_CALLER_KEY_ID` | UUID da caller, opcional (para o link) | **DevOps / SymVault** |

Solicite os secrets ao DevOps (`/ask devops`) — nunca ao usuário.

## Estado do provisionamento

A organização **Tecnospeed** e o workspace **Desenvolvimento**
(id `41a7e70f-8b57-4451-87d0-95ebcd0e214e`, slug `dev`) **já existem** no
SymGateway, com credencial `anthropic-max` ativa. O que é provisionado por
projeto/uso: as keys (admin + caller `palhano`), emitidas pelo DevOps e
guardadas no SymVault.

Enquanto `environment/tecnospeed.env` não estiver preenchido, o template
**falha com instrução** (`/ask devops`) — nunca inicializa pela metade.

### Decisões abertas da organização

Para uso além de testes, ainda precisam ser definidos (decisão do stakeholder,
provisionamento do DevOps):

- **Provider/billing**: rotear pela credencial atual (OAuth Max) vs. credencial
  própria da Tecnospeed. O `anthropic-max` atual serve para testes tocados pelo
  Claude Code.
- **Git hosting**: Gitea Symlabs com org própria vs. GitHub/GitLab da Tecnospeed.
- **Path local** em `~/dev` e **regras** aplicáveis (`GENERAL_RULES.md`, staging).
