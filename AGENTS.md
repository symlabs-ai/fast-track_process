# AGENTS.md — Conduzindo um projeto com o ft engine

> Playbook para um agente (ou humano) conduzir um projeto Fast Track de ponta a ponta
> com o **motor determinístico V2**: do `ft init` ao `ft close`.
> O `ft init` copia este arquivo para a raiz de cada projeto novo — se você está lendo
> isto dentro de um projeto, este é o seu manual de condução.
>
> No V2 os papéis são: **Python orquestra** (grafo, gates, validadores, worktrees),
> **LLM constrói** (código, docs — delegado pelo engine), **você conduz** (decide nos
> human gates, destrava bloqueios, encerra o ciclo). Você não orquestra steps — o engine faz isso.
>
> Fluxo V1 (orquestração por LLM com symbiotas)? Veja [Legado V1](#legado-v1) no final.

---

## Regra zero — nunca opere no repo do template

O repositório do template/engine (`fast-track`) nunca é um projeto — o `ft` recusa rodar
lá (guard global em todos os comandos). Projetos são criados **fora**, com `ft init <nome>`.

- Nunca clone o repo do template para "virar projeto". Nunca rode `ft init`/`ft run .` dentro dele.
- Desenvolvimento do próprio engine: `FT_ALLOW_ENGINE_REPO=1` (só nesse caso).

## Visão geral do ciclo

```
ft init <nome> --template <T>          # 0. criar projeto
  → preencher docs/PRD.md, TECH_STACK  # 1. semear conhecimento
  → git commit                          # 2. snapshot (obrigatório antes de rodar)
ft run . [--auto]                       # 3. rodar o ciclo (worktree externo)
  → ft status / graph                   # 4. monitorar
  → approve | reject | fix              # 5. decidir nos human gates / destravar
ft close                                # 6. arquivar ciclo, fazer merge e remover worktree
  → validação do stakeholder            # 7. feedback vira US no PRD → próximo ciclo
```

O ciclo roda num **worktree externo** em `~/.ft/worktrees/<projeto>/cycle-NN/` — o repo
do projeto fica limpo até o `ft close` fazer o merge. Ciclos são **descartáveis**:
mudanças de PRD/processo entram pelo checkout principal antes de um ciclo; melhorias
produzidas pelo próprio processo são integradas pelo `ft close`.

## 0. Criar o projeto

```bash
ft init meu-projeto --template mvp-builder     # cria .ft/process/, docs/ e src/, sem runtime
cd meu-projeto
git init && git add -A && git commit -m "chore: bootstrap fast track"
```

`--template` é obrigatório. Rode `ft init --help` para ver os nomes compatíveis
com o init; o engine não escolhe um processo automaticamente. Templates de
outros entrypoints, como `feature`, não são aceitos nesse comando.
Uma segunda chamada no mesmo projeto falha porque `.ft/manifest.yml` já existe.
O manifesto pode registrar vários processos locais: um default e processos
nomeados em `.ft/process/<template>/`. Quando um comando recebe `--template`, ele
materializa a cópia aplicável na primeira vez e preserva o fork local depois; o
engine nunca executa diretamente o catálogo global `templates/`.

Templates disponíveis (`templates/` no repo do engine):

| Template | Uso |
|----------|-----|
| `base` | Estrutura mínima com `.ft/process/process.yml`, docs seed e `src/` |
| `feature` | Evolução incremental de uma única feature em produto já entregue; destinado ao comando `ft feature` |
| `mvp-builder` | Processo completo de MVP (MDD → TDD → E2E → stakeholder), recomendado — só o `process.yml`; escreva os docs |
| `fast-track-v2` | Processo V2 legado |
| `ft-ui-prototype` | Prototipagem rápida de UI |
| `symgateway` | Exemplo de ambiente com scripts de integração SymGateway |

Projetos anteriores que ainda possuem `process/` precisam de migração explícita:

```bash
ft migrate-layout . --dry-run
ft migrate-layout .
```

Se os documentos soltos pertencem a um ciclo conhecido, informe-o explicitamente:
`ft migrate-layout . --cycle-id cycle-08-claude`. Históricos em
`docs/archive/<ciclo>/` também são importados; runtime legado sai do repositório e é
preservado sob `$FT_HOME/migrations/` apenas como backup inativo.
Referências inequívocas nos arquivos atuais são atualizadas para `.ft/process/`; os
arquivos históricos em `.ft/cycles/` nunca são reescritos.

O CLI atual não faz descoberta automática do layout antigo.

Integrações externas são opt-in via scripts em `.ft/process/scripts/`. Para SymGateway,
use um template de ambiente que forneça `.ft/process/scripts/register_gateway.sh` e rode
`ft setup-env` com `SYM_GATEWAY_PROJECT_KEY` definida. Precisa da key? Peça ao
DevOps — nunca ao usuário.

## 1. Semear conhecimento

O engine delega construção ao LLM com o contexto de `docs/`. Antes de rodar:

- **Tem PRD pronto?** Preencha `docs/PRD.md` (e `docs/TECH_STACK.md`). O HyperMode do
  template v3 detecta PRD existente e pula o discovery (MDD).
- **Produto existente?** Preserve `docs/PROJECT_BACKLOG.md` como histórico das mudanças
  desejadas e `docs/FEATURES.md` como catálogo das capacidades já entregues. O V3 cria
  ou reconcilia o catálogo antes do planejamento e novamente no handoff.
- **Só uma ideia/demanda bruta?** Use `ft run . --input demanda.md` — o engine classifica
  produto vs. processo e conduz o discovery.
- **Hipótese já escrita?** `ft run . --hipotese hipotese.md` pula o step de hipótese.

> **Commite antes de rodar.** O worktree do ciclo nasce do último commit — mudanças não
> commitadas ficam de fora.

### Evoluir uma feature em produto existente

Use o entrypoint incremental em vez de iniciar outro ciclo completo de MVP:

```bash
ft feature "Adicionar busca por telefone" --template feature --claude
# ou
ft feature --input demanda.md --template feature --codex
```

O comando exige projeto inicializado, repositório Git com HEAD e checkout limpo.
Na primeira chamada, copia o template para `.ft/process/feature/`; depois preserva
esse fork local. A demanda é gravada somente na worktree. O processo pode perguntar
para elucidar o escopo, exige aprovação antes do código, repete implementação e
review após rejeições e encerra com `ft close` em merge full. P0/P1 alheios não
bloqueiam o close: apenas o `backlog_item` selecionado precisa estar aceito.

## 2. Rodar o ciclo

```bash
ft run .                       # interativo: para nos human_gates
ft run . --auto                # autônomo: avança até human_gate, MVP ou BLOCK
ft run . --auto --bypass-human-gates  # sem intervenção: LLM decide human_gates
ft run . --codex               # trocar engine LLM (--claude [modelo] | --codex | --gemini | --opencode)
ft run . --codex gpt-5.6-sol --effort max  # trocar também modelo/effort
ft run . --force               # novo ciclo mesmo com um ativo
ft run . --from-project PATH   # retomada: copia plano_de_voo do ciclo anterior
```

Modos de avanço (também no `ft continue`):

| Modo | Comando | Quando usar |
|------|---------|-------------|
| step | `ft continue` | Depurar node a node |
| sprint | `ft continue --sprint` | Avançar uma sprint e revisar |
| auto | `ft continue --auto` | Avançar até o próximo human_gate, MVP ou BLOCK |
| sem intervenção | `ft continue --auto --bypass-human-gates` | Ciclo autônomo até MVP deixando o LLM decidir human_gates |

`--bypass-human-gates` deixa o LLM decidir nos gates humanos. Use com critério:
`--auto` sozinho **não** pula human_gates.

Descoberta e defaults reais do projeto:

```bash
ft llm-capabilities --json
ft llm-defaults --agent codex --model gpt-5.6-sol --effort max --json
```

O segundo comando valida a combinação por um probe fresco e atualiza
atomicamente `defaults.llm_engine`, `defaults.llm_model` e `defaults.llm_effort`
em `.ft/manifest.yml`.

## 3. Monitorar

```bash
ft status          # node atual, fase, progresso
ft status --full   # + grafo e artefatos
ft status --report # tempo e tokens por node
ft graph           # grafo com status de cada node
ft runs            # tabela comparativa de todos os ciclos
```

Durante a delegação o engine mostra heartbeat com o log do LLM. Logs completos:
`<worktree>/state/llm_logs/`.

## 4. Human gates e decisões

Quando o processo pausa num `human_gate`, o engine sobe o ambiente (`env_setup`), mostra
URL e artefatos, e espera sua decisão:

```bash
ft approve                       # aprova e continua (--no-continue para só aprovar)
ft approve "mensagem"            # aprova com instrução injetada no próximo node
ft reject "motivo objetivo"      # rejeita → engine reenvia ao LLM com o feedback
ft reject "motivo" --no-retry    # rejeita e bloqueia (sem reenvio)
ft fix "instrução de correção"   # correção dirigida no worktree + revalida
ft explore "pedido livre"        # pedido ao LLM sem avançar o processo
```

Regras de condução:

1. **Não pare sem motivo.** Gate passou → siga. Só pare em: BLOCK que exige humano,
   MVP completo, ou erro irrecuperável.
2. **Rejeite com motivo acionável** — o texto vira contexto do retry do LLM.
3. **Feedback do stakeholder vira US no PRD da raiz** — nunca edite PRD/processo dentro
   do worktree do ciclo.

## 5. Bloqueios e recuperação

| Situação | Ação |
|----------|------|
| Node bloqueado, quer retentar igual | `ft retry` |
| Node bloqueado, precisa de correção | `ft fix "o que corrigir"` (`--auto` para seguir até MVP após o fix) |
| Ciclo deu errado, descartar tudo | `ft abort` (remove worktree e branch, sem merge) |
| Cancelar com justificativa | `ft cancel "motivo"` |
| Smart retry | O engine detecta erro idêntico repetido e marca BLOCKED cedo — leia o motivo no `ft status` antes de retentar |

## 6. Encerrar o ciclo

```bash
ft close                     # merge interativo dos artefatos + remove worktree
ft close --merge full        # merge de tudo | docs | selective (--merge-paths) | none
ft close --keep-worktree     # preserva o worktree no disco
ft close --force             # encerra mesmo incompleto
```

Antes do close, revise aprendizados de processo estruturados:

```bash
ft process-candidates
ft process-candidates PI-001 --status promoted \
  --reason "Aplicado e testado no engine" --reference "commit/path"
```

O template `mvp-builder` só aceita `global_candidate` quando a melhoria é independente de
domínio, não contém identificadores do produto, é configurável, foi verificada no
ciclo e é retrocompatível. O ciclo altera apenas seu fork local; `ft close` bloqueia
candidatos globais `pending` até o mantenedor registrar `promoted`, `deferred` ou
`rejected`. Nunca marque `promoted` sem atualizar e testar o global referenciado.

Depois do close:

0. **Histórico do ciclo**: relatórios, task list, evidências, retro e handoff ficam em
   `.ft/cycles/<cycle>/`. `docs/` mantém somente as fontes de verdade humanas, incluindo
   `PROJECT_BACKLOG.md` para mudanças desejadas e `FEATURES.md` para capacidades entregues.

1. **Validação do stakeholder**: suba o servidor do projeto e apresente o link — feedback
   vira User Stories no `docs/PRD.md` (raiz).
2. **Análise crítica**: liste melhorias que o stakeholder não vislumbrou; as escolhidas
   viram US também.
3. **Próximo ciclo**: ajuste PRD/processo na raiz, commit, `ft run . --auto`.

## Variáveis de ambiente

| Variável | Efeito |
|----------|--------|
| `FT_HOME` | Base de dados do ft (default `~/.ft`); worktrees em `$FT_HOME/worktrees/<projeto>/` |
| `FT_ALLOW_ENGINE_REPO` | Libera rodar no repo do template — só para dev do engine |
| `FT_SKIP_HEALTH_CHECK` | Pula o health check da API no `ft run` |
| `FT_LLM_ENGINE` | Engine LLM default (`claude`, `codex`, `gemini`, `opencode`) |
| `FT_LLM_EFFORT` | Effort herdado quando node, flag e estado não definem override |
| `FT_CODEX_REASONING_EFFORT` | Override explícito do `model_reasoning_effort` do Codex; ausente, respeita o `config.toml` do Codex |
| `FT_LLM_EXECUTOR_TIMEOUT` | Timeout geral de cada turno delegado, em segundos; default 1800 |
| `FT_CODEX_EXECUTOR_TIMEOUT` | Override do timeout de turnos Codex; reasoning `ultra` usa 3600 por default |
| `FT_OPENCODE_CONTEXT_LIMIT` / `FT_OPENCODE_CONTEXT_WINDOW` | Janela de contexto anunciada ao OpenCode; default 200000 para `pgx/zai-org_glm-4.7-flash` |
| `FT_OPENCODE_OUTPUT_LIMIT` / `FT_OPENCODE_MAX_OUTPUT` | Limite de saída anunciado ao OpenCode; default 32768 para `pgx/zai-org_glm-4.7-flash` |
| `FT_OPENCODE_PROVIDER_TIMEOUT` / `FT_OPENCODE_TIMEOUT` | Timeout total do provider OpenCode, em milissegundos |
| `FT_OPENCODE_CHUNK_TIMEOUT` / `FT_OPENCODE_PROVIDER_CHUNK_TIMEOUT` | Timeout entre chunks do stream OpenCode, em milissegundos |
| `FT_OPENCODE_HEADER_TIMEOUT` / `FT_OPENCODE_PROVIDER_HEADER_TIMEOUT` | Timeout de headers do provider OpenCode, em milissegundos |
| `FT_OPENCODE_SANDBOX` | Sandbox de filesystem via `bwrap` para OpenCode; default ligado, monta apenas outputs/write_scope como writable |
| `FT_OPENCODE_DENY_EDIT_TOOLS` | Opt-in para modo legado: nega ferramentas nativas de edição do OpenCode em nodes de código |
| `FT_OPENCODE_BUNDLE_MODE` | Opt-in para modo file-bundle XML em nodes de código OpenCode |
| `FT_OPENCODE_SCRIPT_MODE` | Opt-in para modo script Bash em nodes de código OpenCode |
| `FT_OPENCODE_DEBUG` | Ativa logs detalhados do OpenCode (`--print-logs --log-level DEBUG`) |
| `FT_OPENCODE_THINKING` | Exibe reasoning do OpenCode (`--thinking`); use só para diagnóstico, pois pode aumentar latência |
| `SYM_GATEWAY_PROJECT_KEY` / `SYM_GATEWAY_ADMIN_KEY` | Usadas por scripts de ambiente opt-in, como `.ft/process/scripts/register_gateway.sh` |

## Referências

No projeto:

- Processo default versionado: `.ft/process/process.yml`
- Processos nomeados materializados: `.ft/process/<template>/process.yml`
- Histórico versionado dos ciclos: `.ft/cycles/<cycle>/`
- Conhecimento seed: `docs/PRD.md`, `docs/TECH_STACK.md`, `docs/PROJECT_BACKLOG.md`,
  `docs/FEATURES.md`

No repo do engine (`fast-track`):

- Guia completo do engine: `docs/ft_engine_usage.md`
- Templates de processo: `templates/`
- Regras de arquitetura e código: `docs/integrations/forgebase_guides/`

---

## Legado V1

O fluxo original — Claude Code assume o papel de `ft_manager` e orquestra symbiotas
(`ft_coach`, `forge_coder`, `ft_gatekeeper`, `ft_acceptance`) via prompts — foi
substituído pelo engine determinístico. Os prompts continuam no repo do engine em
`process/symbiotes/` e `process/fast_track/SUMMARY_FOR_AGENTS.md`, mas **referenciam comandos da
CLI antiga (`ft.py`, `ft init --check`) que não existem no `ft` atual**. Use apenas como
referência histórica ou em projetos antigos que ainda seguem o V1.
