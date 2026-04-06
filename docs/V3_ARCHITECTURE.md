# Fast Track V3 — Arquitetura Base/Ambiente

> Documento de contexto completo. Captura a discussão de 2026-04-06 sobre a evolução
> arquitetural do Fast Track. Referência para implementação de BL-12 a BL-15.

---

## 1. Problema

O Fast Track hoje mistura três coisas no mesmo repositório:

1. **Engine** — código Python que executa processos (runner, state, validators, CLI)
2. **Processo concreto** — `FAST_TRACK_PROCESS_V2.yml` com nós, gates, prompts, validators específicos
3. **Configuração de ambiente** — SymGateway, port-registry, staging, que são particularidades da Symlabs

Consequências:
- Qualquer pessoa que instale o Fast Track herda regras da Symlabs (ForgeBase Pulse, SymGateway, etc.)
- Customizações de domínio poluem o framework genérico
- O processo YAML só pode evoluir se commitar no repo central
- Não há como ter processos diferentes para domínios diferentes (jogos RPG vs. SaaS vs. mobile)

---

## 2. Conceito Central — Duas Camadas

### Camada 1: Base (Framework)

Vive no repositório central `fast-track/`. Instalável via `pip install ft-engine`.

Contém:
- **Engine Python** (`ft/engine/`) — runner, state manager, cycle manager, validators
- **CLI** (`ft/cli/`) — `ft init`, `ft run`, `ft continue`, `ft approve`, `ft status`, `ft graph`
- **Conceitos universais** — o que é um gate, o que é um nó, tipos de nó (build, gate, decision, retro, document), o que é cycle, como validators se plugam
- **Schema do YAML** — quais campos o processo YAML aceita, regras de validação
- **Templates/exemplos** — o `FAST_TRACK_PROCESS_V2.yml` atual migra para `examples/` ou `templates/`

NÃO contém:
- Nenhum processo concreto como "o" processo a ser usado
- Nenhuma integração com sistemas externos (SymGateway, Telegram, etc.)
- Nenhum prompt de LLM
- Nenhuma regra de negócio específica

Analogia: Django é o framework. O `settings.py` e as apps são da sua empresa. Você não commita suas regras de negócio no repositório do Django.

### Camada 2: Ambiente (Software House / Produto)

Vive no repositório do produto. Versionado com Git independente do framework.

Contém:
- **Processo concreto** (`process/FAST_TRACK_PROCESS.yml`) — nós, gates, prompts, validators, fases, sprints. Começa como cópia do template, evolui com o domínio.
- **Configuração de ambiente** (`process/environment.yml`) — integrações, portas, secrets provider, staging
- **Scripts de hook** (`process/scripts/`) — scripts bash executados pelo engine em momentos específicos
- **Conhecimento do produto** (`docs/`) — PRD, retro, plano de voo, SPEC, CHANGELOG
- **Artefatos descartáveis** (`runs/`) — código gerado, testes, screenshots, logs de cada execução

---

## 3. Analogia com Framework

```
fast-track/                      ← pip install (framework)
  ft/engine/                     ← runner, state, validators
  ft/cli/                        ← CLI commands
  ft/base/                       ← schema, tipos de nó, conceitos
  templates/
    FAST_TRACK_PROCESS_V2.yml    ← exemplo/template (não é "o" processo)

service_mate/                    ← repo Git do produto (ambiente)
  process/
    FAST_TRACK_PROCESS.yml       ← processo concreto deste domínio
    environment.yml              ← config do ambiente (SymGateway, portas, etc.)
    scripts/
      register_gateway.sh        ← hook on_init
      setup_ports.sh             ← hook on_init
      provision_claude.sh        ← hook on_env_setup
      notify_telegram.sh         ← hook on_cycle_end
      deploy_staging.sh          ← hook on_deliver
  docs/
    PRD.md                       ← evolui a cada run
    plano_de_voo.md              ← handoff do último run
    retro.md                     ← aprendizados acumulados
    SPEC.md
    CHANGELOG.md
  runs/                          ← .gitignore (descartável)
    01/                          ← artefatos do run 1
    02/                          ← artefatos do run 2
    ...
```

---

## 4. O Processo é Conhecimento, não Código

Insight central da conversa: **o que evolui entre runs não é o código gerado — é o conhecimento**. Esse conhecimento tem duas dimensões:

1. **Conhecimento do produto** — PRD, retro, plano de voo. "O que estamos construindo e o que aprendemos."
2. **Conhecimento do processo** — o YAML com nós, gates, prompts. "Como construímos e o que funciona."

Ambos devem ser versionados no repo do ambiente, não no repo central. Se depois de 5 ciclos de ServiceMate o time descobre que precisa de um gate extra para validar proxy do Vite, isso vai para o YAML do ServiceMate, não para o Fast Track genérico.

Exemplo concreto: uma software house que desenvolve jogos RPG top-down e começa com o Fast Track + um PRD. A cada ciclo, ela vai refinando:
- O PRD (melhor definição de mecânicas, sprites, tilemaps)
- O processo (gates para validar renderização, nós para testar mecânicas de combate, prompts especializados para pixel art)

Nada disso volta para o Fast Track central — é conhecimento de domínio.

---

## 5. Configuração de Ambiente — `environment.yml`

O `environment.yml` elimina a necessidade de hardcodar integrações no engine. Exemplo da Symlabs:

```yaml
# environment.yml — Symlabs
gateway:
  url: https://symgateway.symlabs.ai
  auto_register: true

ports:
  registry: ~/dev/devops/port-registry.md
  range: 8000-8049

secrets:
  provider: symvault

staging:
  deploy: gitea-actions
  domain: "*.palhano.digital"

hooks:
  on_init:
    - ./scripts/register_gateway.sh
    - ./scripts/setup_ports.sh
  on_env_setup:
    - ./scripts/provision_claude.sh
  on_cycle_end:
    - ./scripts/notify_telegram.sh
  on_deliver:
    - ./scripts/deploy_staging.sh
```

Outra pessoa que usa Fast Track teria seu próprio `environment.yml` com Vercel, AWS, Supabase — ou nenhum. Sem `environment.yml`, o engine roda igual, só sem integrações.

---

## 6. Environment Hooks — Scripts por Fase

O engine não precisa saber o que o script faz. Só precisa saber **quando** disparar.

```python
# No engine (runner.py)
for script in hooks.get("on_init", []):
    subprocess.run(script, check=True)
```

Momentos disponíveis para hooks:
- `on_init` — após `ft init`
- `on_env_setup` — após provisionar ambiente
- `on_node_start` — antes de executar qualquer nó
- `on_node_end` — após completar qualquer nó
- `on_gate_pass` — quando um gate passa
- `on_gate_fail` — quando um gate falha
- `on_cycle_end` — ao completar um ciclo
- `on_deliver` — ao entregar (MVP/release)

Se o script falhar (exit code != 0), o engine bloqueia — igual um gate. O engine permanece genérico; a inteligência de infra fica nos scripts do ambiente.

Motivação: hoje o SymGateway está implementado em código Python dentro do engine. Isso não deveria existir. Qualquer integração com sistema externo deve ser um script do ambiente, não código do framework.

---

## 7. RunMode — `isolated` vs `continuous`

### Contexto: o conceito de `cycle`

O engine já tem `CycleManager` com `current_cycle: cycle-01` no `engine_state.yml`. Esse `cycle` significa **iteração dentro do mesmo repositório/pasta** — o processo roda, entrega um MVP, e o `advance_cycle()` reinicia os steps no mesmo projeto para um segundo ciclo de melhoria.

Na prática, o usuário tem feito diferente: cria SM1, SM2, SM3... SM13 em pastas separadas. Cada uma é uma execução completa do processo, do zero. A continuidade é apenas via PRD e plano de voo (documentos de handoff).

IMPORTANTE: `cycle` já é um termo usado no engine com um sentido específico. Não reutilizar como nome para as pastas de run (evita colisão semântica). As subpastas devem usar `run_01/`, `run_02/`, etc.

### Dois modos

**`isolated`** (padrão atual):
- Cada `ft run` cria uma subpasta em `runs/N+1/`
- Gera código do zero a partir do PRD atual
- Ao final, `ft.prd.rewrite` atualiza `docs/PRD.md`
- Git dentro de cada run tem valor limitado (código é descartável)
- Git entre runs não se aplica (são projetos independentes)
- Worktrees não se aplicam
- **Bom para pesquisa** — testar o processo, comparar runs

**`continuous`**:
- `ft run` opera no mesmo diretório
- O `CycleManager` avança `cycle-01 → cycle-02`
- Código evolui incrementalmente
- Git é essencial — tags/branches por cycle
- Worktrees podem ser úteis
- **Bom para produto real** — evoluir software em produção

Configurável em `environment.yml`:
```yaml
run_mode: isolated  # ou continuous
```

O engine adapta o comportamento de `ft init`, `ft run` e `ft.end` conforme o modo.

---

## 8. Estrutura de Pastas — Git Strategy

### Modo `isolated`

```
service_mate/                    ← repo Git
  process/                       ← versionado (conhecimento de processo)
    FAST_TRACK_PROCESS.yml
    environment.yml
    scripts/
  docs/                          ← versionado (conhecimento de produto)
    PRD.md
    plano_de_voo.md
    retro.md
    CHANGELOG.md
  runs/                          ← NÃO versionado (.gitignore)
    01/                          ← artefatos do run 1 (descartável)
    02/                          ← artefatos do run 2 (descartável)
```

O `docs/` e `process/` são o repositório de verdade. Git com histórico de como o PRD evoluiu, quais decisões foram tomadas, como o processo foi refinado. Cada run lê daqui, produz artefatos, e no final do ciclo o `ft.prd.rewrite` e o `ft.handoff` escrevem de volta em `docs/`.

Os `runs/` são descartáveis — pode deletar runs antigos sem perder nada de valor.

### Modo `continuous`

```
service_mate/                    ← repo Git
  process/
    FAST_TRACK_PROCESS.yml
    environment.yml
  docs/
    PRD.md
    SPEC.md
  src/                           ← código do produto (evolui)
  tests/
  frontend/
```

Git com tags por cycle (`cycle-01-delivered`, `cycle-02-delivered`).

---

## 9. Validação do Processo YAML — `ft validate process`

O engine precisa validar o YAML do processo **antes** de executar. O usuário vai customizar
o processo e precisa de feedback claro sobre erros no schema.

### Comando

```bash
ft validate process              # valida ./process/FAST_TRACK_PROCESS.yml
ft validate process --fix        # sugere correções automáticas
```

### Regras de validação

**Estruturais (schema):**
- Todo nó tem `id`, `type`, `title`
- `type` é um dos tipos válidos: `build`, `gate`, `decision`, `retro`, `document`, `discovery`
- Nós não-terminais têm `next` apontando para nó existente
- Nó terminal é `ft.end` (ou equivalente declarado)
- `executor` é tipo conhecido: `llm_coder`, `llm_coach`, `python`
- `validators` referencia validators que existem no engine
- `sprint` é string válida (sem espaços, lowercase)
- `outputs` são paths relativos válidos

**Grafo (integridade):**
- Nenhuma referência quebrada (`next` aponta para nó inexistente)
- Nenhum nó órfão (sem ninguém apontando para ele, exceto o primeiro nó)
- Grafo é conexo — todos os nós são alcançáveis a partir do primeiro
- Grafo termina — existe ao menos um caminho até `ft.end`
- Sem ciclos infinitos (loops devem ter condição de saída via `decision`)

**Semânticos (boas práticas, warnings não-bloqueantes):**
- Gates devem ter ao menos um validator
- Nós `build` devem ter `outputs` definidos
- Nós `decision` devem ter `conditions` ou lógica de branching
- Prompts não devem estar vazios em nós que usam `llm_*`
- `max_turns` recomendado para nós LLM (warning se ausente)

### Saída

```
$ ft validate process

Validando process/FAST_TRACK_PROCESS.yml...

  ✅ Schema: 43 nós válidos
  ✅ Grafo: conexo, termina em ft.end
  ✅ Validators: todos os 12 validators referenciados existem
  ⚠️  ft.tdd.02.red: nó build sem max_turns (recomendado)
  ⚠️  gate.smoke: gate com apenas 1 validator

  Resultado: PASS (2 warnings)
```

```
$ ft validate process

Validando process/FAST_TRACK_PROCESS.yml...

  ✅ Schema: 43 nós válidos
  ❌ ft.delivery.03.test: next aponta para 'gate.inexistente' (nó não encontrado)
  ❌ ft.custom.01: executor 'llm_designer' não é reconhecido pelo engine
  ⚠️  ft.review.01: nó órfão (nenhum nó aponta para ele)

  Resultado: FAIL (2 erros, 1 warning)
```

### Quando executar

- `ft init` roda automaticamente ao inicializar
- `ft run` / `ft continue` roda antes de executar o primeiro nó
- `ft validate process` roda sob demanda pelo usuário

---

## 10. Impacto no Engine — O que Muda

### BL-12: Separação Base / Ambiente

O engine precisa:
1. **Carregar processo de `./process/`** (relativo ao CWD) em vez de path hardcoded
2. **Não embutir nenhum processo** — se `./process/FAST_TRACK_PROCESS.yml` não existir, erro claro
3. **`ft init` com template** — aceitar `--template` para copiar um processo base
4. **Remover referências hardcoded** ao `FAST_TRACK_PROCESS_V2.yml` do repo central

### BL-13: Estrutura `process/`, `docs/`, `runs/`

O engine precisa:
1. **`ft init` criar a estrutura** — `process/`, `docs/`, `runs/`, `.gitignore` para runs
2. **`ft run` criar subpasta** em `runs/` automaticamente (modo isolated)
3. **`ft.prd.rewrite` e `ft.handoff` escreverem em `docs/`** (não na pasta do run)
4. **Ler PRD de `docs/PRD.md`** em vez de `project/docs/PRD.md`

### BL-14: Environment Hooks

O engine precisa:
1. **Ler `environment.yml`** se existir
2. **Executar scripts** nos momentos definidos (on_init, on_env_setup, etc.)
3. **Bloquear** se script falhar (exit code != 0)
4. **Remover código de integrações** (SymGateway) do engine — migrar para scripts

### BL-15: RunMode

O engine precisa:
1. **Ler `run_mode`** de `environment.yml` (default: isolated)
2. **Modo isolated**: criar `runs/N+1/`, rodar lá dentro, escrever resultado em `docs/`
3. **Modo continuous**: rodar no diretório atual, usar `CycleManager.advance_cycle()`
4. **`ft.end`** adaptar comportamento conforme modo

---

## 10. Ordem de Implementação

1. **BL-12** (Base/Ambiente) — pré-requisito para tudo. O engine precisa saber carregar processo de `./process/` antes de qualquer outra mudança.
2. **BL-14** (Hooks) — pode ser feito em paralelo com BL-13. Desacopla integrações do engine.
3. **BL-13** (Estrutura de pastas) — depende de BL-12. Muda onde o engine busca e escreve arquivos.
4. **BL-15** (RunMode) — depende de BL-13. Usa a estrutura de pastas para implementar os dois modos.

---

## 11. Migração

Projetos existentes (SM1-SM13) não precisam migrar. São artefatos de pesquisa e ficam como estão.

Novos projetos criados após a implementação já nasceriam na estrutura nova:
```bash
ft init --template fast-track-v2 service_mate_14
# Cria:
#   service_mate_14/
#     process/FAST_TRACK_PROCESS.yml (cópia do template)
#     docs/
#     runs/
#     .gitignore
```

---

## 12. CLI Unificada — Decisão Tomada

Na sessão de 2026-04-06, havia dois binários `ft` no sistema:
- `ft` (miniconda) → `ft.cli.template_main` (CLI V1 antiga, template-based)
- `ft-engine` (venv) → `ft.cli.main` (engine V2 automático)

Decisão: desinstalado V1 do miniconda, `pyproject.toml` atualizado para mapear `ft → ft.cli.main:main`. Um único `ft` no sistema com os comandos do engine: `init, run, continue, status, approve, reject, graph, setup-env`.

---

_Documento gerado em 2026-04-06. Referência para implementação de BL-12 a BL-15._
