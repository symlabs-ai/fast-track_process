# SPEC.md — [Nome do Produto]

> Versão: [X.Y.Z]
> MVP entregue em: [YYYY-MM-DD]
> Última atualização: [YYYY-MM-DD]
> Status: mvp | maintenance

---

## Visão

<!-- Uma ou duas frases descrevendo o propósito central do produto. -->
<!-- Fonte: PRD seção 2.1 (Intenção Central) -->

**Problema**: <!-- PRD seção 2.2 -->
**Público-alvo**: <!-- PRD seção 2.3 -->
**Diferencial**: <!-- PRD seção 2.4 -->

---

## Escopo

### O que está incluso

<!-- Funcionalidades entregues no MVP e ciclos subsequentes. -->
<!-- Atualizar a cada /feature done. -->

| Feature | User Story | Status | Ciclo |
|---------|-----------|--------|-------|
| <!-- ex: Login via CLI --> | US-01 | ✅ Entregue | cycle-01 |

### O que está fora do escopo

<!-- Itens explicitamente excluídos. Fonte: PRD seção 9 + tasks P2 não implementadas. -->

- <!-- ex: Interface web (v2) -->
- <!-- ex: Multi-tenant -->

---

## Funcionalidades Principais

### [Nome da Feature 1]
<!-- Descrição breve de como foi implementada. Uma ou duas frases. -->
**User Story**: US-XX — [título]
**Entrypoint**: `<!-- ex: python -m meu_produto --flag -->` ou API endpoint

### [Nome da Feature 2]
<!-- Idem -->

<!-- Adicionar uma seção por feature entregue. -->

---

## Tech Stack

<!-- Fonte: project/docs/tech_stack.md -->

| Camada | Tecnologia | Motivo |
|--------|-----------|--------|
| Linguagem | <!-- ex: Python 3.12 --> | <!-- ex: ecossistema AI, tipagem --> |
| Persistência | <!-- ex: SQLite --> | <!-- ex: MVP local, zero infra --> |
| Testes | <!-- ex: pytest + pexpect --> | <!-- ex: TDD + smoke PTY --> |

> Detalhes completos: `project/docs/tech_stack.md`

---

## Arquitetura

<!-- Referência rápida. Diagramas completos em project/docs/diagrams/ -->

```
[Visão resumida da arquitetura em ASCII ou texto]
ex:
  CLI → Application Layer → Domain → Infrastructure
         (use cases)       (entities)  (persistence)
```

> Diagramas: `project/docs/diagrams/` (class · components · database · architecture)

---

## Modo de Manutenção

Este projeto está em **maintenance mode**. Novas features são adicionadas via:

```
/feature <descrição da feature>
```

A skill `/feature` lê este SPEC.md para entender o contexto antes de implementar.
Ao finalizar uma feature (`/feature done`), SPEC.md é atualizado automaticamente.

### Convenções do projeto

<!-- Preencher com convenções estabelecidas durante o MVP que /feature deve respeitar -->

- Testes em `tests/unit/` (unitários, mocks) e `tests/smoke/` (processo real, PTY)
- Commits no formato `feat(T-XX): descrição`
- <!-- Adicionar outras convenções relevantes -->

---

## Histórico de Mudanças

| Versão | Data | Feature | Session |
|--------|------|---------|---------|
| MVP | [YYYY-MM-DD] | Entrega inicial | — |
<!-- /feature done adiciona entradas aqui -->
