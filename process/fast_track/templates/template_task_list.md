# Task List — [Nome do Projeto]

> Ciclo: cycle-XX
> Derivado de: project/docs/PRD.md
> Data: [YYYY-MM-DD]

---

## Tasks

| ID | Task | From US | Value Track | Priority | Size | Status | BlockedBy |
|----|------|---------|-------------|----------|------|--------|-----------|
| T-01 | <!-- Descrição da task --> | US-01 | <!-- track_id --> | P0 | S | pending | — |
| T-02 | <!-- Descrição da task --> | US-01 | <!-- track_id --> | P0 | M | pending | T-01 |
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

### Paralelização

Quando `parallel_mode: true` no `ft_state.yml`, tasks em Value Tracks diferentes e sem `BlockedBy`
mútuo podem ser executadas em paralelo pelo ft_manager (via git worktrees).

- Tasks no **mesmo Value Track + mesma entidade** NÃO paralelizam.
- Tasks com **dependência de contrato** (port/interface compartilhada) NÃO paralelizam.
- Duas tasks **Size L** NÃO paralelizam simultaneamente.
- O forge_coder avalia independência técnica em `ft.tdd.01.selecao` e recomenda PARALELO ou SEQUENCIAL.
