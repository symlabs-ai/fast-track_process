# Task List — [Nome do Projeto]

> Ciclo: cycle-XX
> Derivado de: project/docs/PRD.md
> Data: [YYYY-MM-DD]

---

## Tasks

### Sequência de Sprints

| Sprint | Objetivo | Tasks | Escopo | Gate de saída |
|--------|----------|-------|--------|---------------|
| sprint-01 | <!-- Fundação do MVP --> | <!-- T-01, T-02 --> | current_cycle | Sprint Expert Gate (`/ask fast-track`) |
| sprint-02 | <!-- Próximo incremento --> | <!-- T-03, T-04 --> | current_cycle | Sprint Expert Gate (`/ask fast-track`) |

> Marque como `current_cycle` apenas as sprints que fazem parte do corte ativo do ciclo.
> Sprints futuras ou pós-MVP devem aparecer como `backlog`.

### Sprint 1 — [Nome da Sprint]

Objetivo: [o que esta sprint destrava para o MVP]

| ID | Task | From US | Value Track | Priority | Size | Status | BlockedBy |
|----|------|---------|-------------|----------|------|--------|-----------|
| T-01 | <!-- Descrição da task --> | US-01 | <!-- track_id --> | P0 | S | pending | — |
| T-02 | <!-- Descrição da task --> | US-01 | <!-- track_id --> | P0 | M | pending | T-01 |

### Sprint 2 — [Nome da Sprint]

Objetivo: [o que esta sprint agrega ao MVP]

| ID | Task | From US | Value Track | Priority | Size | Status | BlockedBy |
|----|------|---------|-------------|----------|------|--------|-----------|
| T-03 | <!-- Descrição da task --> | US-02 | <!-- track_id --> | P1 | S | pending | — |

### Legenda

**Priority**: P0 (must-have MVP) | P1 (should-have) | P2 (nice-to-have)

**Size**: XS (< 30min) | S (30min-2h) | M (2h-4h) | L (4h+)

**Status**: pending | in_progress | done | skipped

**BlockedBy**: IDs de tasks pré-requisito (ex: `T-01, T-03`) ou `—` se nenhuma dependência.
Preenchido pelo ft_coach na criação da task list, refinado pelo forge_coder em `ft.tdd.01.selecao`.

---

## Notas
<!-- Dependências entre tasks, ordem sugerida, observações -->

### Sprint Expert Gate

Ao concluir todas as tasks de uma sprint:

1. O `ft_manager` chama `/ask fast-track` com o contexto da sprint concluída.
2. O feedback é salvo em `project/docs/sprint-review-sprint-XX.md`.
3. Todas as recomendações do especialista viram correções obrigatórias dentro da sprint atual.
4. A próxima sprint só pode começar depois que o feedback estiver integralmente tratado.

### Paralelização

Quando `parallel_mode: true` no `ft_state.yml`, tasks em Value Tracks diferentes e sem `BlockedBy`
mútuo podem ser executadas em paralelo pelo ft_manager (via git worktrees).

- Tasks no **mesmo Value Track + mesma entidade** NÃO paralelizam.
- Tasks com **dependência de contrato** (port/interface compartilhada) NÃO paralelizam.
- Duas tasks **Size L** NÃO paralelizam simultaneamente.
- O forge_coder avalia independência técnica em `ft.tdd.01.selecao` e recomenda PARALELO ou SEQUENCIAL.
- Paralelização nunca atravessa duas sprints ao mesmo tempo. Só existe paralelização dentro da sprint atual.
