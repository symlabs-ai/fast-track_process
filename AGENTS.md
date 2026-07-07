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
ft close                                # 6. merge dos artefatos + remover worktree
  → validação do stakeholder            # 7. feedback vira US no PRD → próximo ciclo
```

O ciclo roda num **worktree externo** em `~/.ft/worktrees/<projeto>/cycle-NN/` — o repo
do projeto fica limpo até o `ft close` fazer o merge. Ciclos são **descartáveis**:
mudanças de PRD/processo vão sempre na **raiz do projeto**, nunca dentro do ciclo.

## 0. Criar o projeto

```bash
ft init meu-projeto --template fast-track-v3   # cria a pasta e a estrutura (process/, docs/, src/)
cd meu-projeto
git init && git add -A && git commit -m "chore: bootstrap fast track"
```

Templates disponíveis (`templates/` no repo do engine):

| Template | Uso |
|----------|-----|
| `base` | Estrutura mínima com docs seed: `process.yml` + `docs/PRD.md` + `docs/TECH_STACK.md` + `src/` |
| `fast-track-v3` | Processo completo V3 (MDD → TDD → E2E → stakeholder), recomendado — só o `process.yml`; escreva os docs |
| `fast-track-v2` | Processo V2 legado |
| `ft-ui-prototype` | Prototipagem rápida de UI |
| `symgateway` | Exemplo de ambiente com scripts de integração SymGateway |

Integrações externas são opt-in via scripts em `process/scripts/`. Para SymGateway,
use um template de ambiente que forneça `process/scripts/register_gateway.sh` e rode
`ft setup-env` com `SYM_GATEWAY_PROJECT_KEY` definida. Precisa da key? Peça ao
DevOps — nunca ao usuário.

## 1. Semear conhecimento

O engine delega construção ao LLM com o contexto de `docs/`. Antes de rodar:

- **Tem PRD pronto?** Preencha `docs/PRD.md` (e `docs/TECH_STACK.md`). O HyperMode do
  template v3 detecta PRD existente e pula o discovery (MDD).
- **Só uma ideia/demanda bruta?** Use `ft run . --input demanda.md` — o engine classifica
  produto vs. processo e conduz o discovery.
- **Hipótese já escrita?** `ft run . --hipotese hipotese.md` pula o step de hipótese.

> **Commite antes de rodar.** O worktree do ciclo nasce do último commit — mudanças não
> commitadas ficam de fora.

## 2. Rodar o ciclo

```bash
ft run .                       # interativo: para nos human_gates
ft run . --auto                # autônomo: avança até MVP sem parar
ft run . --codex               # trocar engine LLM (--claude [modelo] | --codex | --gemini | --opencode)
ft run . --force               # novo ciclo mesmo com um ativo
ft run . --from-project PATH   # retomada: copia plano_de_voo do ciclo anterior
```

Modos de avanço (também no `ft continue`):

| Modo | Comando | Quando usar |
|------|---------|-------------|
| step | `ft continue` | Depurar node a node |
| sprint | `ft continue --sprint` | Avançar uma sprint e revisar |
| auto | `ft continue --auto` | Ciclo autônomo até MVP |

`--bypass-human-gates` deixa o LLM decidir nos gates humanos (use com critério; `--auto` já pula).

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

Depois do close:

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
| `SYM_GATEWAY_PROJECT_KEY` / `SYM_GATEWAY_ADMIN_KEY` | Usadas por scripts de ambiente opt-in, como `process/scripts/register_gateway.sh` |

## Referências

No projeto:

- Processo do ciclo: `process/process.yml`
- Conhecimento seed: `docs/PRD.md`, `docs/TECH_STACK.md`

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
