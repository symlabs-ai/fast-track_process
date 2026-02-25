# Sessões do Forge Coder

Registros internos de execução do symbiota Forge Coder.

## Formato

```
forge_coder_YYYY-MM-DD_<descrição>.md
```

## Conteúdo das Sessões

Cada sessão registra:

1. **Contexto** — Task sendo implementada, ciclo atual
2. **RED** — Testes escritos, como falharam
3. **GREEN** — Implementação realizada, decisões de design
4. **Self-Review** — Checklist: secrets, nomes, edge cases, lint/types
5. **Decisão** — Commit realizado ou issues encontradas

## Sessões Internas vs Formais

| Local | Tipo |
|-------|------|
| `symbiotes/forge_coder/sessions/` | Interno (raciocínio do agente) |
| `project/docs/sessions/forge_coder/` | Formal (resumo para o dev) |
