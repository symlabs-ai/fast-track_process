# ForgeProcess — Fast Track

> Processo ágil para solo dev + AI. 12 steps, 6 fases, valor > cerimônia.

**3 symbiotas** · **TDD obrigatório** · **E2E CLI gate** · **Hyper-mode**

---

## O que é

Fast Track é uma variante do ForgeProcess para desenvolvedor solo trabalhando com assistentes de IA.
Define um fluxo completo — do insight à entrega — com rigor (TDD, E2E gate) e sem burocracia
(sprints formais, BDD Gherkin, reviews de 3 pessoas).

## Symbiotas

| Symbiota | Papel |
|----------|-------|
| `ft_manager` | Orquestra o processo, valida entregas e interage com o stakeholder |
| `ft_coach` | Conduz MDD, planning e feedback |
| `forge_coder` | Executa TDD, delivery e E2E |

## Início rápido

```bash
# 1. Clone e desconecte do template
git clone https://github.com/symlabs-ai/fast-track_process.git meu-projeto
cd meu-projeto
git remote remove origin
git remote add origin <url-do-seu-repo>
git push -u origin main

# 2. Carregue o ft_manager como system prompt
#    → process/symbiotes/ft_manager/prompt.md

# 3. O ft_manager conduz tudo a partir daí
```

## Documentação

- **Processo**: `process/fast_track/FAST_TRACK_PROCESS.md`
- **YAML (machine-readable)**: `process/fast_track/FAST_TRACK_PROCESS.yml`
- **Resumo para agentes**: `process/fast_track/SUMMARY_FOR_AGENTS.md`
- **Diagrama de fluxo**: `docs/fast-track-flow.md`
- **Guia de agentes**: `AGENTS.md`

---

## Changelog

### [v0.1.4] — 2026-02-25

#### Fixed
- **ft_manager**: detecção de hyper-mode tornada obrigatória na seção de delegação de discovery.
  Antes, a verificação existia apenas na inicialização e era ignorada ao entrar no fluxo de
  delegação ao ft_coach. Adicionada regra ⚠️ explícita no topo da seção, com sinais de detecção
  e instrução de perguntar ao stakeholder em caso de dúvida.

---

### [v0.1.3] — 2026-02-25

#### Added
- **Hyper-mode**: quando o stakeholder entrega um PRD abrangente de entrada, o ft_coach processa
  o documento em um único pass, gerando `PRD.md`, `TASK_LIST.md` e um questionário de alinhamento
  estruturado em três seções — pontos ambíguos, lacunas e sugestões de melhoria. O stakeholder
  responde o questionário antes de o fluxo avançar.
- **template_hyper_questionnaire.md**: template para o questionário de alinhamento do hyper-mode.
- **Campo `mdd_mode`** em `ft_state.yml`: `normal | hyper`.
- **Diagrama de fluxo** (`docs/fast-track-flow.md`) atualizado com bifurcação normal/hyper.

---

### [v0.1.2] — 2026-02-25

#### Added
- **ft_manager**: verificação de vínculo git na inicialização. Se o repositório ainda apontar para
  o template original (`symlabs-ai/fast-track_process`), o agente detecta, alerta e orienta o dev
  a reconfigurar o remote para o repositório próprio antes de começar.

---

### [v0.1.1] — 2026-02-25

#### Added
- **ft_manager** (`process/symbiotes/ft_manager/prompt.md`): novo symbiota orquestrador.
  Gerencia o fluxo completo do projeto, valida todas as entregas contra os critérios do processo
  e é o único ponto de contato com o stakeholder.
  - Modo `interactive` (padrão): apresenta E2E ao stakeholder ao final de cada ciclo.
  - Modo `autonomous`: roda ciclos sem interrupção até o MVP, apresenta stakeholder apenas na entrega final.
  - Checkpoints de validação em três pontos: PRD, task list e entrega por task.
- **Campos no `ft_state.yml`**: `orchestrator`, `stakeholder_mode`, `mvp_delivered`.
- **`FAST_TRACK_PROCESS.yml`**: ft_manager adicionado como symbiote com `can_decide: true`;
  três nós de validação inseridos no flow (`ft_manager_valida_prd`, `ft_manager_valida_task_list`,
  `ft_manager_valida_entrega`); bloco de decisão de ciclo com modos interactive/autonomous.
- **`AGENTS.md`**: ft_manager definido como ponto de entrada obrigatório de toda sessão.
- **`target_profile.stakeholders`**: alterado de `false` para `optional`.

---

### [v0.1.0] — 2026-02-25

#### Added
- Estrutura inicial do Fast Track: 12 steps, 6 fases.
- Symbiotas `ft_coach` (MDD, Planning, Feedback) e `forge_coder` (TDD, Delivery, E2E).
- Templates: `template_prd.md`, `template_task_list.md`, `template_retro_note.md`.
- `ft_state.yml`: controle de estado do processo.
- `FAST_TRACK_PROCESS.yml`: especificação formal do processo em YAML.
- `FAST_TRACK_PROCESS.md`: especificação legível das 6 fases e 12 steps.
- `SUMMARY_FOR_AGENTS.md`: resumo compacto para LLMs.
- `AGENTS.md`: guia rápido de onboarding para agentes.
- `setup_env.sh`: setup de ambiente (Python 3.12, ForgeBase, ForgeLLMClient, dev tools).
- `tests/e2e/`: estrutura de testes E2E CLI com shared utilities e templates.
- `docs/integrations/`: guias técnicos de ForgeBase e ForgeLLMClient.
