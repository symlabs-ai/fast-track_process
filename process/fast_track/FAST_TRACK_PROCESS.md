# ForgeProcess — Fast Track

> Solo dev + AI. 12 steps, 6 fases. Valor > cerimônia.

---

## Filosofia

**Um solo dev com AI não precisa de cerimônia de time.** O processo foca em rigor (TDD, E2E gate) sem burocracia (sprints formais, BDD Gherkin, reviews de 3 pessoas).

**Pilares:**
- MDD completo (hipótese -> PRD validado)
- TDD Red-Green (teste primeiro, sempre)
- E2E CLI gate (obrigatório para fechar ciclo)
- Rastreabilidade (User Story -> Task -> Teste -> Código)
- 3 symbiotas (ft_manager orquestra; ft_coach + forge_coder executam)

---

## Modos de MDD

### `normal` (padrão)
Discovery conduzido por conversa: ft_coach pergunta, dev responde, hipótese → PRD → validação → task list.

### `hyper`
Ativado quando o **stakeholder entrega um PRD abrangente de entrada**.
ft_coach consome o documento e, em um único pass:
1. Produz `project/docs/PRD.md` completo (mapeando para o template, inferindo seções ausentes)
2. Produz `project/docs/TASK_LIST.md`
3. Gera `project/docs/hyper_questionnaire.md` com três seções:
   - **🔍 Pontos Ambíguos** — onde o PRD é vago ou interpretável de mais de uma forma
   - **🕳️ Lacunas** — informações necessárias para implementação que estão ausentes
   - **💡 Sugestões de Melhoria** — melhorias identificadas para o produto ou implementação

O stakeholder responde → ft_coach incorpora → artefatos finalizados → segue para validação normal.

Template: `process/fast_track/templates/template_hyper_questionnaire.md`

---

## Fases e Steps

### Fase 1: MDD (comprimido) — 3 steps

#### ft.mdd.01.hipotese — Capturar Hipótese
- **Input**: Conversa com dev
- **Output**: Seções 1-2 do PRD preenchidas (Hipótese + Visão)
- **Symbiota**: ft_coach
- **Critério**: Contexto, sinal de mercado e oportunidade claros

#### ft.mdd.02.prd — Redigir PRD
- **Input**: Hipótese capturada
- **Output**: PRD completo (`project/docs/PRD.md`)
- **Template**: `process/fast_track/templates/template_prd.md`
- **Symbiota**: ft_coach
- **Critério**: Seções 1-9 preenchidas, pelo menos 2 User Stories com ACs

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
- **Conteúdo**: linguagem/runtime, framework, persistência, libs-chave, ferramentas de dev, alternativas descartadas, dúvidas para o stakeholder

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
- **Critério**: Teste passa, sem quebrar testes existentes

### Fase 4: Delivery — 3 steps (por task)

#### ft.delivery.01.implement — Integrar e Rodar Suite
- **Input**: Código implementado
- **Output**: Suite completa de testes passando
- **Symbiota**: forge_coder
- **Critério**: Zero falhas na suite completa

#### ft.delivery.02.self_review — Self-Review
- **Input**: Diff do código
- **Output**: Issues corrigidas
- **Symbiota**: forge_coder
- **Checklist**:
  - Sem secrets ou dados sensíveis
  - Nomes claros e consistentes
  - Edge cases cobertos
  - Sem código morto ou debug prints
  - Lint e type check passando

#### ft.delivery.03.commit — Commit
- **Input**: Código revisado
- **Output**: Commit no branch
- **Symbiota**: forge_coder
- **Critério**: Mensagem referencia task ID (ex: `feat(T-01): implement user login`)

> **Loop**: Após commit, se há tasks pendentes -> volta para ft.tdd.01.selecao.
> Quando todas as tasks estiverem done -> avança para E2E.

### Fase 5: E2E Gate — 1 step

#### ft.e2e.01.cli_validation — E2E CLI Validation
- **Input**: `src/`, `tests/`
- **Output**: `tests/e2e/cycle-XX/` com resultados
- **Symbiota**: forge_coder
- **Critério**: `run-all.sh` executa com sucesso
- **GATE OBRIGATÓRIO**: Ciclo não pode ser encerrado sem E2E passando

### Fase 6: Feedback — 1 step

#### ft.feedback.01.retro_note — Retro Note
- **Input**: Ciclo completo
- **Output**: `project/docs/retro-cycle-XX.md`
- **Template**: `process/fast_track/templates/template_retro_note.md`
- **Symbiota**: ft_coach
- **Critério**: Seções preenchidas (o que funcionou, o que não, foco próximo)

> **Decisão final**: Iniciar novo ciclo (volta para ft.plan.01) ou encerrar.

### Fase 7: Handoff — 1 step *(executado uma única vez, ao encerrar o projeto)*

#### ft.handoff.01.specs — Gerar SPEC.md

- **Gatilho**: stakeholder confirma "MVP concluído"
- **Input**: PRD.md + TASK_LIST.md + tech_stack.md + todos os retro-cycle-XX.md
- **Output**: `project/docs/SPEC.md`
- **Template**: `process/fast_track/templates/template_specs.md`
- **Symbiota**: ft_coach
- **Critério**: SPEC.md cobre visão, escopo entregue, funcionalidades com entrypoints reais, tech stack e instruções de manutenção via `/feature`

**O que é o SPEC.md:**
- Registro do que foi construído (não o plano — esse é o PRD)
- Contexto permanente lido pelo `/feature` antes de implementar extensões
- Documento vivo: atualizado a cada `/feature done`

**Após geração:** `maintenance_mode: true` é gravado no state. O projeto passa a ser evoluído via `/feature`, que lê o SPEC.md como contexto.

---

## Regras

1. **PRD é a fonte única de verdade** — Toda decisão de produto está no PRD. Não há documentos separados de visão, hipótese, ADR ou backlog.

2. **TDD Red-Green é obrigatório** — Nenhum código de produção sem teste falhando primeiro. Sem exceções.

3. **E2E CLI gate é obrigatório** — O ciclo só fecha quando `tests/e2e/cycle-XX/run-all.sh` passa.

4. **Acceptance Criteria substituem BDD** — ACs no formato Given/When/Then dentro do PRD. Sem `.feature` files separados.

5. **Self-review substitui review formal** — Checklist automatizado em vez de 3 reviewers.

6. **Task list substitui roadmap** — Um arquivo (`TASK_LIST.md`) em vez de ROADMAP + BACKLOG + estimates.

---

## Symbiotas

| Symbiota | Papel | Responsabilidade |
|----------|-------|------------------|
| `ft_manager` | Orquestrador | Gerencia o fluxo completo, valida entregas, aciona o stakeholder |
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

1. Copie o template PRD:
   ```bash
   cp process/fast_track/templates/template_prd.md project/docs/PRD.md
   ```

2. Inicie com `ft.mdd.01.hipotese` — descreva sua ideia para o ft_coach.

3. O processo guia você até o E2E gate passando.

---

## Referências

- Step IDs: `process/fast_track/FAST_TRACK_IDS.md`
- Estado: `process/fast_track/state/ft_state.yml`
- YAML completo: `process/fast_track/FAST_TRACK_PROCESS.yml`
- Summary para agentes: `process/fast_track/SUMMARY_FOR_AGENTS.md`
