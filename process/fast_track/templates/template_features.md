# FEATURES

> Catálogo canônico das capacidades efetivamente implementadas e validadas do
> produto. O `PROJECT_BACKLOG` planeja e acompanha demandas; este documento mostra
> o que o produto realmente oferece hoje e preserva seu ciclo de vida.

## Catálogo de Features

| ID | Status | Backlog | Título | Descrição | Entregue em | Evidência | Última evolução | Notas |
|---|---|---|---|---|---|---|---|---|

<!--
Exemplo de linha (remova o comentário ao usar):
| FEAT-001 | active | PB-001 | Cadastro de clientes | Permite criar e consultar clientes. | cycle-01 | acceptance-report.md; e2e-report.md | — | Entrega inicial. |
-->

## Regras de Manutenção

- Somente itens `PB-*` com status `done` ou `accepted` podem aparecer no catálogo;
  apenas tipos `US`, `feature`, `recurso` ou `story` criam uma capacidade nova.
- IDs `FEAT-NNN` são estáveis: nunca renumere, reutilize ou apague uma feature.
- Status permitidos: `active`, `deprecated` e `removed`.
- Evoluções preservam a `FEAT-*` e acrescentam seu `PB-*` à coluna Backlog.
  Bugs, dívidas e manutenções só podem ser anexados a uma feature existente;
  nunca criam uma nova capacidade por si só.
- Features depreciadas ou removidas permanecem listadas para preservar a rastreabilidade.
- `Entregue em` registra o primeiro ciclo; `Última evolução` registra o ciclo da
  mudança mais recente ou `—` quando não houve evolução posterior.
- Evidências devem apontar para testes, relatórios ou artefatos verificáveis.
