---
description: Gerenciar o backlog de features do projeto em modo manutenção
argument-hint: [descrição | list | promote <B-XX> | done <B-XX>]
---

> ⛔ **Exclusiva do modo manutenção.** Só disponível após `maintenance_mode: true` em `ft_state.yml`.
> Se o projeto ainda estiver em Fast Track, o ft_manager rejeitará o uso desta skill.

Gerenciar ideias de features futuras no `BACKLOG.md` do projeto.

## Uso

```
/backlog <descrição>        # Adicionar nova ideia ao backlog
/backlog list               # Listar ideias pendentes
/backlog promote <B-XX>     # Mover item para "Em andamento" (ao iniciar /feature)
/backlog done <B-XX>        # Mover item para "Implementadas" (ao finalizar /feature)
```

## Arquivo

`BACKLOG.md` na raiz do projeto. Criado automaticamente pelo `ft.handoff.01.specs`.

## Fluxo

```
/backlog <ideia>
    └─> adiciona linha em ## Ideias com próximo ID (B-XX) e data atual

/backlog promote B-01
    └─> move linha de ## Ideias para ## Em andamento

/feature <descrição do B-01>
    └─> implementa a feature (lê BACKLOG.md como contexto adicional)
    └─> /feature done → move B-01 para ## Implementadas, atualiza CHANGELOG.md
```

## Fluxo de Trabalho

### 1. Adicionar ideia (`/backlog <descrição>`)

1. Ler `BACKLOG.md`.
2. Determinar próximo ID: contar linhas em `## Ideias` + `## Em andamento` + `## Implementadas`,
   incrementar. Formato: `B-01`, `B-02`, etc.
3. Adicionar linha na tabela `## Ideias`:
   ```
   | B-XX | <descrição> | <data hoje YYYY-MM-DD> | dev |
   ```
4. Salvar `BACKLOG.md`.
5. Confirmar: "Adicionado **B-XX** ao backlog."

### 2. Listar ideias (`/backlog list`)

1. Ler `BACKLOG.md`.
2. Exibir tabela `## Ideias` formatada.
3. Mostrar contagem: `X ideias pendentes · Y em andamento · Z implementadas`.

### 3. Promover item (`/backlog promote <B-XX>`)

1. Ler `BACKLOG.md`.
2. Mover linha de `## Ideias` para `## Em andamento`, adicionando coluna `Iniciado em` com data atual.
3. Salvar `BACKLOG.md`.
4. Sugerir: "Item promovido. Para implementar: `/feature <descrição do B-XX>`"

### 4. Concluir item (`/backlog done <B-XX>`)

1. Ler `BACKLOG.md`.
2. Mover linha de `## Em andamento` para `## Implementadas`, adicionando `Implementada em` e versão.
3. Salvar `BACKLOG.md`.

## Integração com `/feature`

Ao iniciar `/feature`, o agente pode consultar `BACKLOG.md` para entender o contexto de ideias
existentes e evitar sobreposição. Ao finalizar (`/feature done`), se a feature veio de um item
do backlog, usar `/backlog done <B-XX>` para fechar o ciclo.

## Regras

- IDs são sequenciais e nunca reutilizados.
- Descrições são concisas (1 linha). Detalhes vão no `/feature` quando for implementar.
- `Origem` indica quem sugeriu: `dev`, `stakeholder`, `retro`, `uso` (observação de uso real).
- Itens implementados nunca são deletados — ficam em `## Implementadas` como histórico.
