# Fast Track — Skills

> ⛔ **Estas skills são exclusivas do modo manutenção** (`maintenance_mode: true`).
> Não devem ser usadas durante o Fast Track — o ft_manager rejeita qualquer tentativa.
> São ativadas automaticamente após `ft.handoff.01.specs` gerar SPEC.md, CHANGELOG.md e BACKLOG.md.

## Skills

| Skill | Comando | Descrição |
|-------|---------|-----------|
| Backlog | `/backlog <descrição>` | Registrar ideia futura no BACKLOG.md |

## Instalação

```bash
# Copiar skills para o diretório de comandos do Claude Code
cp process/skills/backlog.md ~/.claude/commands/backlog.md
```

## Fluxo de Manutenção

```
/backlog <ideia>               # Registrar ideia
    |
    v
/backlog list                  # Revisar backlog
    |
    v
/backlog done <B-XX>           # Fechar item no backlog
```
