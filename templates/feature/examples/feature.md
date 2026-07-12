---
type: evolution
target_feature: FEAT-003
backlog_item: PB-012
priority: P1
interface: ui
---

# Busca de clientes por telefone

## Objetivo

Permitir que o usuário encontre clientes pelo telefone sem perder as buscas já
existentes por nome e documento.

## Comportamento Esperado

A busca aceita telefone com ou sem formatação e mantém a paginação atual.

## Critérios de Aceite

- AC-01: A busca encontra o cliente pelo telefone completo.
- AC-02: Telefones com e sem formatação produzem o mesmo resultado.
- AC-03: As buscas existentes por nome e documento continuam funcionando.

## Fora do Escopo

- Busca fuzzy.
- Histórico de pesquisas.

## Restrições

- Preservar o contrato público da API.
- Não alterar a paginação existente.
