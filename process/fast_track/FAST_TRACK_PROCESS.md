# ForgeProcess — Fast Track

> Solo dev + AI. 18 steps, 9 fases. Valor > cerimônia.

---

## Filosofia

**Um solo dev com AI não precisa de cerimônia de time.** O processo foca em rigor (TDD, E2E gate) sem burocracia (sprints formais, BDD Gherkin, reviews de 3 pessoas).

**Pilares:**
- MDD completo (hipótese -> PRD validado)
- TDD Red-Green (teste primeiro, sempre)
- E2E CLI gate (obrigatório para fechar ciclo)
- Rastreabilidade (User Story -> Task -> Teste -> Código)
- Acceptance gate condicional (interface real quando != CLI-only)
- 4 symbiotas (ft_manager orquestra; ft_gatekeeper valida gates; ft_coach + forge_coder executam)

---

## Modos de MDD

### `normal` (padrão)
Discovery conduzido por conversa: ft_coach pergunta, dev responde, hipótese → PRD → validação → task list.

### `hyper`
Ativado quando o **stakeholder entrega um PRD abrangente de entrada**.
ft_coach consome o documento, **audita contra o processo normal** e, em um único pass:
1. **Audita** cada seção do PRD contra o que teria sido produzido no fluxo normal, classificando como:
   - `✅ presente` — informação suficiente no original
   - `⚠️ inferido` — derivado do contexto, precisa confirmação
   - `❌ ausente` — obrigatório, stakeholder deve fornecer
2. Produz `project/docs/PRD.md` completo (com marcações de status por seção)
3. Produz `project/docs/TASK_LIST.md` (tasks de seções inferidas marcadas `[pendente confirmação]`)
4. Gera `project/docs/hyper_questionnaire.md` com **quatro** seções:
   - **📋 Obrigatórias Ausentes** — informações que o processo normal teria extraído e que **bloqueiam** o avanço
   - **🔍 Pontos Ambíguos** — onde o PRD é vago ou interpretável de mais de uma forma
   - **🕳️ Lacunas** — informações necessárias para implementação que estão ausentes
   - **💡 Sugestões de Melhoria** — melhorias identificadas para o produto ou implementação
5. **Apresenta diagnóstico ao stakeholder** com tabela de status por seção e opções de como prosseguir

O stakeholder decide como tratar → responde → ft_coach incorpora → **todas as seções `❌ ausente` resolvidas** → artefatos finalizados → segue para validação normal.

Template: `process/fast_track/templates/template_hyper_questionnaire.md`

---

## Fases e Steps

### Fase 1: MDD (comprimido) — 3 steps

#### ft.mdd.01.hipotese — Capturar Hipótese
- **Input**: Conversa com dev
- **Output**: `project/docs/hipotese.md` (inclui Value Tracks candidatos)
- **Template**: `process/fast_track/templates/template_hipotese.md`
- **Symbiota**: ft_coach
- **Critério**: Contexto, sinal de mercado e oportunidade claros; 2-5 Value Tracks candidatos identificados; hipótese registrada em arquivo próprio antes de evoluir para PRD

#### ft.mdd.02.prd — Redigir PRD
- **Input**: `project/docs/hipotese.md` (hipótese confirmada)
- **Output**: PRD completo (`project/docs/PRD.md`)
- **Template**: `process/fast_track/templates/template_prd.md`
- **Symbiota**: ft_coach
- **Critério**: Seções 1-10 preenchidas, pelo menos 2 User Stories com ACs, Value Tracks formalizados com KPIs, cada US mapeada para pelo menos 1 track

#### ft.mdd.03.validacao — Validar PRD
- **Input**: PRD completo
- **Output**: Decisão: approved | rejected
- **Symbiota**: ft_coach
- **Critério**: Dev confirma que PRD reflete a intenção e é implementável
- **Se rejeitado**: Processo termina (pode reiniciar com nova hipótese)

### Fase 2: Planning — 3 steps

#### ft.plan.01.task_list — Criar Task List
- **Input**: PRD seção 5 (User Stories)
- **Output**: `project/docs/TASK_LIST.md`
- **Template**: `process/fast_track/templates/template_task_list.md`
- **Symbiota**: ft_coach
- **Critério**: Cada User Story tem pelo menos 1 task, todas priorizadas e estimadas

#### ft.plan.02.tech_stack — Propor Tech Stack *(primeiro ciclo apenas)*
- **Input**: PRD + TASK_LIST
- **Output**: `project/docs/tech_stack.md`
- **Symbiota**: forge_coder
- **Gate**: aprovação do stakeholder (ft_manager apresenta, stakeholder revisa e aprova)
- **Critério**: stack aprovada pelo stakeholder; dúvidas respondidas; decision log preenchido
- **Base obrigatória**: sempre propor **ForgeBase** como base arquitetural; sempre propor **Forge_LLM** quando o PRD contiver features que acessem LLMs
- **Conteúdo**: linguagem/runtime, framework, persistência, libs-chave, ferramentas de dev, alternativas descartadas, dúvidas para o stakeholder
- **UI Design System** *(condicional — quando `interface_type` != `cli_only`)*:
  - Propor design system com justificativa (ex: Material Design / M3, Fluent, Ant Design, Chakra, shadcn/ui, Carbon, Tailwind UI)
  - Apresentar 2-3 alternativas com prós/contras para o stakeholder decidir
  - Registrar escolha no `tech_stack.md` na seção "UI Design System"
  - Definir `interface_type` no `ft_state.yml` neste step

#### ft.plan.03.diagrams — Gerar Diagramas Técnicos *(primeiro ciclo; revisado se estrutura mudar)*
- **Input**: PRD + TASK_LIST + tech_stack.md aprovada
- **Output**: `project/docs/diagrams/` (4 arquivos Mermaid)
- **Symbiota**: forge_coder
- **Critério**: diagramas derivados do PRD, sem especulação; escopo limitado ao ciclo atual

| Diagrama | Arquivo | Formato Mermaid |
|----------|---------|-----------------|
| Classes | `diagrams/class.md` | `classDiagram` |
| Componentes | `diagrams/components.md` | `flowchart TD` |
| Banco de Dados | `diagrams/database.md` | `erDiagram` |
| Arquitetura | `diagrams/architecture.md` | `flowchart TD` |

### Fase 3: TDD — 3 steps (loop por task)

> **Pré-requisito (primeiro ciclo):** forge_coder roda `bash setup_env.sh` para configurar o ambiente (`.venv`, ForgeBase, ferramentas de dev) antes de escrever qualquer código.

#### ft.tdd.01.selecao — Selecionar Task
- **Input**: TASK_LIST.md
- **Output**: Task selecionada, status -> in_progress
- **Symbiota**: forge_coder
- **Critério**: Task de maior prioridade pendente selecionada

#### ft.tdd.02.red — Escrever Teste
- **Input**: Task selecionada + ACs da User Story
- **Output**: Teste em `tests/` que falha
- **Symbiota**: forge_coder
- **Critério**: Teste compila/executa e falha pelo motivo esperado

#### ft.tdd.03.green — Implementar
- **Input**: Teste falhando
- **Output**: Código em `src/` que faz o teste passar
- **Symbiota**: forge_coder
- **Critério**: Teste passa, sem quebrar testes existentes. **Suite completa de testes passa** (não apenas o teste da task atual).

### Fase 4: Delivery — 3 steps (por task)

#### ft.delivery.01.self_review — Self-Review (expandido)
- **Input**: Diff do código
- **Output**: Issues corrigidas
- **Symbiota**: forge_coder
- **Checklist** (10 itens, 3 grupos):

  **Segurança & Higiene:**
  - Sem secrets ou dados sensíveis
  - Sem código morto ou debug prints
  - Lint e type check passando

  **Qualidade de Código:**
  - Nomes claros e consistentes
  - Edge cases cobertos por testes
  - Cobertura de testes >= 85% nos arquivos alterados (desejável 90%)

  **Arquitetura (Clean/Hex + ForgeBase):**
  - Domínio puro: sem I/O, sem imports de infrastructure/adapters
  - UseCases passam por `UseCaseRunner` (nunca `.execute()` direto)
  - Todo UseCase novo está mapeado em `forgepulse.value_tracks.yml`
  - Diagramas atualizados se estrutura mudou (class/components/database/architecture)

#### ft.delivery.02.refactor — Refactor
- **Input**: Issues identificadas no self-review + diff
- **Output**: Código refatorado, suite verde
- **Symbiota**: forge_coder
- **Critério**: Refactoring aplicado OU "nenhum refactoring necessário" documentado. Suite continua verde.

#### ft.delivery.03.commit — Commit
- **Input**: Código revisado
- **Output**: Commit no branch
- **Symbiota**: forge_coder
- **Critério**: Mensagem referencia task ID (ex: `feat(T-01): implement user login`)
- **Estratégia**:
  - **Default**: 1 commit por task
  - **Ciclos longos (> 5 tasks)**: ft_manager pode instruir squash ao final do ciclo antes do smoke. Convenção: `feat(cycle-XX): summary`

> **Loop**: Após commit, se há tasks pendentes -> volta para ft.tdd.01.selecao.
> Quando todas as tasks estiverem done -> avança para Smoke Gate.

### Fase 5a: Smoke Gate — 1 step

#### ft.smoke.01.cli_run — Smoke CLI Run
- **Input**: `src/`, `tests/smoke/`, `forgepulse.value_tracks.yml`
- **Output**: `project/docs/smoke-cycle-XX.md` + `artifacts/pulse_snapshot.json`
- **Symbiota**: forge_coder
- **GATE OBRIGATÓRIO**: Ciclo não avança sem smoke passando
- **Checklist**:
  - Processo sobe sem erro
  - Input injetado via PTY (pexpect ou ptyprocess) — sem simulação
  - Output observado documentado literalmente
  - Nenhum freeze ou hang detectado
  - `pulse_snapshot.json` gerado via ForgeBase Pulse com `mapping_source: "spec"` e agregação por `value_track`
  - Resultado explícito: PASSOU ou TRAVOU

> ⚠️ `mvp_status: demonstravel` só pode ser gravado após smoke PASSAR com output real + pulse evidence documentados.

### Fase 5b: E2E Gate — 1 step

#### ft.e2e.01.cli_validation — E2E CLI Validation
- **Input**: `src/`, `tests/`
- **Output**: `tests/e2e/cycle-XX/` com resultados
- **Symbiota**: forge_coder
- **Critério**: `run-all.sh` executa com sucesso
- **GATE OBRIGATÓRIO**: Ciclo não pode ser encerrado sem E2E passando

### Fase 5c: Acceptance — Validação de Interface *(condicional)* — 1 step

> Gate condicional — executado apenas quando `interface_type` != `cli_only` no `ft_state.yml`.
> Se produto é CLI-only, E2E CLI já cobre → skip com nota.

#### ft.acceptance.01.interface_validation — Acceptance Test
- **Input**: PRD (seção 5 — ACs), `src/`, interface do produto (CLI/API/UI)
- **Output**: `project/docs/acceptance-cycle-XX.md` + `tests/acceptance/cycle-XX/`
- **Template**: `process/fast_track/templates/template_acceptance_report.md`
- **Symbiota**: forge_coder
- **GATE**: Obrigatório quando `interface_type` != `cli_only`
- **Mapeamento AC → Teste**:
  - Cada AC do PRD (Given/When/Then) gera pelo menos 1 teste de aceitação
  - Testes organizados por US → AC, com rastreabilidade explícita
  - Todos os Value Tracks devem ter pelo menos 1 fluxo testado pela interface

| Interface | Ferramenta | Diretório |
|-----------|-----------|-----------|
| CLI | Shell scripts (existente) | `tests/e2e/` (coberto pelo E2E gate) |
| API (REST/GraphQL) | pytest + httpx/requests | `tests/acceptance/` |
| UI (Web) | Playwright ou Chrome automation | `tests/acceptance/` |
| UI (Desktop) | Playwright (Electron) ou pyautogui | `tests/acceptance/` |

### Fase 6: Feedback — 1 step

#### ft.feedback.01.retro_note — Retro Note
- **Input**: Ciclo completo
- **Output**: `project/docs/retro-cycle-XX.md`
- **Template**: `process/fast_track/templates/template_retro_note.md`
- **Symbiota**: ft_coach
- **Critério**: Seções preenchidas (o que funcionou, o que não, foco próximo)

> **Decisão final**: ft_manager analisa o estado do projeto contra os critérios de MVP antes de oferecer opções.
> Se o MVP está claramente incompleto (tasks P0 pendentes, interface não entregue quando `interface_type` != `cli_only`),
> recomenda novo ciclo. "Encerrar MVP" só é oferecido como opção primária quando todos os critérios são atendidos.

### Fase 8: Auditoria ForgeBase — 1 step *(executado uma vez, antes do handoff)*

> Gate obrigatório — auditoria consolidada de ForgeBase, observabilidade, Value/Support Tracks e qualidade de logging antes de declarar o MVP entregue.

#### ft.audit.01.forgebase — Auditoria ForgeBase

- **Gatilho**: stakeholder confirmou "MVP concluído", antes do handoff
- **Input**: `src/`, `forgepulse.value_tracks.yml`, `artifacts/pulse_snapshot.json`, PRD (Value Tracks/KPIs)
- **Output**: `project/docs/forgebase-audit.md`
- **Template**: `process/fast_track/templates/template_forgebase_audit.md`
- **Symbiota**: forge_coder
- **GATE**: Obrigatório — MVP não é entregue sem auditoria passando

**Checklist (5 grupos, 20+ itens):**

**1. UseCaseRunner Wiring:**
- Todo UseCase é executado via `UseCaseRunner.run()`, nunca `.execute()` direto
- Composition root (CLI/routes) usa runner para todos os endpoints
- Nenhum UseCase "escapa" do runner

**2. Value Tracks & Support Tracks:**
- `forgepulse.value_tracks.yml` existe e está completo
- Todo UseCase implementado está mapeado em pelo menos 1 track
- Support Tracks têm `supports:` referenciando value tracks existentes
- Descrições são claras e refletem o domínio
- Sem `track_type` como campo no YAML (derivado pela seção)

**3. Observabilidade (Pulse):**
- `artifacts/pulse_snapshot.json` gerado com `mapping_source: "spec"`
- Snapshot agrega por `value_track` (não apenas `legacy`)
- Métricas por execução: count, duration, success, error
- Eventos mínimos: start, finish, error
- Contrato de observabilidade do tech_stack.md atendido

**4. Logging:**
- Sem `print()` em código de produção
- Logs estruturados (não strings concatenadas: `f"error: {e}"` → `logger.error("msg", exc_info=True)`)
- Níveis corretos: DEBUG detalhe, INFO fluxo, WARNING degradação, ERROR falhas
- Sem dados sensíveis nos logs (tokens, passwords, PII)
- Sem logs excessivos em loops (log uma vez com contagem, não N vezes)
- Mensagens descritivas (não "error occurred" ou "something went wrong")
- Logger configurado por módulo (`logging.getLogger(__name__)`)

**5. Arquitetura Clean/Hex:**
- Domínio puro: sem I/O, sem imports de infrastructure/adapters
- Ports definidos como abstrações (ABC ou Protocol)
- Adapters implementam ports, não ao contrário
- Nenhuma dependência circular entre camadas

### Fase 9: Handoff — 1 step *(executado uma única vez, ao encerrar o projeto)*

#### ft.handoff.01.specs — Gerar SPEC.md

- **Gatilho**: stakeholder confirma "MVP concluído"
- **Input**: PRD.md + TASK_LIST.md + tech_stack.md + todos os retro-cycle-XX.md
- **Outputs**: `project/docs/SPEC.md` · `CHANGELOG.md`
- **Templates**: `template_specs.md` · `template_changelog.md`
- **Symbiota**: ft_coach
- **Critério**: SPEC.md cobre visão, escopo entregue, funcionalidades com entrypoints reais, tech stack e instruções de manutenção via `/feature`

**O que é o SPEC.md:**
- Registro do que foi construído (não o plano — esse é o PRD)
- Contexto permanente lido pelo `/feature` antes de implementar extensões
- Documento vivo: atualizado a cada `/feature done`

**O que é o CHANGELOG.md:**
- Histórico de mudanças do produto, iniciado com seção `## [MVP]`
- Cada `/feature done` adiciona uma nova seção de versão
- Formato compatível com Keep a Changelog

**Após geração:** `maintenance_mode: true` é gravado no state. O projeto passa a ser evoluído via `/feature`, que lê SPEC.md como contexto e atualiza SPEC.md + CHANGELOG.md a cada entrega.

---

## Regras

1. **PRD é a fonte única de verdade** — Toda decisão de produto está no PRD. A exceção é `hipotese.md`, que registra a hipótese antes do PRD existir e é absorvido por ele. Não há documentos separados de visão, ADR ou backlog.

2. **TDD Red-Green é obrigatório** — Nenhum código de produção sem teste falhando primeiro. Sem exceções.

3. **E2E CLI gate é obrigatório** — O ciclo só fecha quando `tests/e2e/cycle-XX/run-all.sh` passa.

4. **Acceptance Criteria substituem BDD** — ACs no formato Given/When/Then dentro do PRD. Sem `.feature` files separados.

5. **Self-review substitui review formal** — Checklist automatizado em vez de 3 reviewers.

6. **Task list substitui roadmap** — Um arquivo (`TASK_LIST.md`) em vez de ROADMAP + BACKLOG + estimates.

---

## Symbiotas

| Symbiota | Papel | Responsabilidade |
|----------|-------|------------------|
| `ft_manager` | Orquestrador | Gerencia o fluxo completo, delega validações ao gatekeeper, aciona o stakeholder |
| `ft_gatekeeper` | Validador de Gates | Verifica condições binárias nos stage gates (PASS/BLOCK), independente do orquestrador |
| `ft_coach` | Executor — Discovery | PRD, task list, retro (delegado pelo ft_manager) |
| `forge_coder` | Executor — Código | Testes, implementação, review, commit, E2E (orquestrado pelo ft_manager) |

### Modo de operação

**`interactive`** (padrão): ao final de cada ciclo, ft_manager apresenta os resultados E2E ao stakeholder e aguarda decisão (novo ciclo, ajustes ou MVP concluído).

**`autonomous`**: ativado quando o stakeholder diz "continue sem validação". ft_manager roda todos os ciclos sem interrupção, valida internamente, e aciona o stakeholder apenas na entrega final do MVP.

### Modo `maintenance`

Ativado após `ft.handoff.01.specs` ser concluído (`maintenance_mode: true` no state).
O projeto saiu do Fast Track e é evoluído via `/feature`:

```
/feature <descrição da nova feature>
```

O agente `/feature` lê `project/docs/SPEC.md` para entender o contexto e atualiza o SPEC.md ao finalizar (`/feature done`).

---

## Getting Started

1. Inicie com `ft.mdd.01.hipotese` — descreva sua ideia para o ft_coach.

2. O ft_coach registra a hipótese em `project/docs/hipotese.md` (template: `process/fast_track/templates/template_hipotese.md`).

3. O processo guia você até o E2E gate passando.

---

## Referências

- Step IDs: `process/fast_track/FAST_TRACK_IDS.md`
- Estado: `process/fast_track/state/ft_state.yml`
- YAML completo: `process/fast_track/FAST_TRACK_PROCESS.yml`
- Summary para agentes: `process/fast_track/SUMMARY_FOR_AGENTS.md`
