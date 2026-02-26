---
description: Desenvolver novas features em modo manutenção com contexto persistente
argument-hint: [descrição | status | done | save | todo | files | commit]
---

> ⛔ **Exclusiva do modo manutenção.** Só disponível após `maintenance_mode: true` em `ft_state.yml`.
> Se o projeto ainda estiver em Fast Track, o ft_manager rejeitará o uso desta skill.

Skill de manutenção do projeto. Lê `project/docs/SPEC.md` para entender o contexto antes de implementar qualquer feature.

## Uso

```
/feature <descrição>           # Iniciar nova feature
/feature status                # Ver status da sessão atual
/feature done                  # Finalizar feature (testes + commit + atualiza SPEC.md e CHANGELOG.md)
/feature save                  # Salvar contexto manualmente
/feature todo <descrição>      # Adicionar TODO à sessão
/feature files                 # Listar arquivos tocados nesta sessão
/feature commit [msg]          # Commit parcial (apenas arquivos desta sessão)
```

## Documentos lidos ao iniciar

| Documento | Path | Propósito |
|-----------|------|-----------|
| Especificação | `project/docs/SPEC.md` | Contexto do produto, arquitetura, convenções |
| Backlog | `BACKLOG.md` | Ideias existentes — evitar sobreposição |
| Changelog | `CHANGELOG.md` | Histórico — entender o que já foi entregue |

## Documentos atualizados ao finalizar (`/feature done`)

| Documento | Atualização |
|-----------|-------------|
| `project/docs/SPEC.md` | Nova feature adicionada na seção "Escopo — incluso" + "Funcionalidades Principais" |
| `CHANGELOG.md` | Nova seção `## [vX.Y.Z] — data` com a feature entregue |

## Critério de Pronto

1. Todos os testes passando (`pytest tests/ -v`)
2. Aceite explícito do usuário
3. SPEC.md e CHANGELOG.md atualizados

## Contexto de Sessão

Cada sessão cria `.context/<session-id>.md` com metadados, arquivos tocados, decisões e TODOs.
Suporta múltiplos agentes em paralelo via `.context/index.json`.

## Integração com `/backlog`

- Ao iniciar: consultar `BACKLOG.md` para verificar se a feature corresponde a um item existente.
- Ao finalizar: se veio de um item do backlog, executar `/backlog done <B-XX>`.

## Onde instalar

Copiar para `~/.claude/commands/feature.md` ou usar via skill global do Claude Code.
