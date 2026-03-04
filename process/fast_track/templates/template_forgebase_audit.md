# ForgeBase Audit Report

## 1. UseCaseRunner Wiring

| UseCase | Invocação via Runner | Composition Root | Status |
|---------|---------------------|------------------|--------|
| [NomeUseCase] | `runner.run(dto)` em [arquivo:linha] | [cli.py / routes.py] | ✅ / ❌ |

**Resumo**: X/Y UseCases corretamente wired.

## 2. Value Tracks & Support Tracks

**Arquivo**: `forgepulse.value_tracks.yml`

| UseCase | Track | Tipo | Status |
|---------|-------|------|--------|
| [NomeUseCase] | [track_name] | value / support | ✅ / ❌ |

- [ ] Todo UseCase implementado está mapeado
- [ ] Support Tracks têm `supports:` correto
- [ ] Sem `track_type` como campo explícito
- [ ] Descrições claras e alinhadas ao domínio

**Resumo**: X/Y UseCases mapeados. Z Support Tracks verificados.

## 3. Observabilidade (Pulse)

**Arquivo**: `artifacts/pulse_snapshot.json`

- [ ] Arquivo existe
- [ ] `mapping_source: "spec"`
- [ ] Agregação por `value_track` (não apenas `legacy`)
- [ ] Métricas presentes: count, duration, success, error
- [ ] Eventos mínimos: start, finish, error

**Resumo**: [status geral da observabilidade]

## 4. Logging

> ⚠️ Seção mais crítica — historicamente onde a qualidade mais cai.

### Problemas encontrados

| Arquivo | Linha | Problema | Severidade | Correção |
|---------|-------|----------|-----------|----------|
| [path] | [N] | `print("debug")` | ❌ CRÍTICO | Remover ou substituir por logger |
| [path] | [N] | `f"error: {e}"` | ❌ CRÍTICO | `logger.error("msg", exc_info=True)` |
| [path] | [N] | `logger.info("Error...")` | ⚠️ NÍVEL | Usar `logger.error()` |

### Checklist

- [ ] Sem `print()` em código de produção
- [ ] Logs estruturados (não strings concatenadas)
- [ ] Níveis corretos: DEBUG detalhe, INFO fluxo, WARNING degradação, ERROR falhas
- [ ] Sem dados sensíveis nos logs (tokens, passwords, PII)
- [ ] Sem logs excessivos em loops (log 1x com contagem, não N vezes)
- [ ] Mensagens descritivas (não "error occurred" genérico)
- [ ] Logger por módulo: `logging.getLogger(__name__)`

**Resumo**: X problemas encontrados, Y corrigidos.

## 5. Arquitetura Clean/Hex

- [ ] Domínio puro: sem I/O, sem imports de infrastructure/adapters
- [ ] Ports definidos como abstrações (ABC ou Protocol)
- [ ] Adapters implementam ports, não ao contrário
- [ ] Sem dependência circular entre camadas

### Violações encontradas

| Camada | Arquivo | Import indevido | Correção |
|--------|---------|-----------------|----------|
| [domain] | [path] | `from infrastructure.repo import ...` | Mover para port |

**Resumo**: [status geral da arquitetura]

## Resultado Final

| Grupo | Itens | Pass | Fail | Status |
|-------|-------|------|------|--------|
| UseCaseRunner | X | X | 0 | ✅ / ❌ |
| Value/Support Tracks | X | X | 0 | ✅ / ❌ |
| Observabilidade | X | X | 0 | ✅ / ❌ |
| Logging | X | X | 0 | ✅ / ❌ |
| Arquitetura | X | X | 0 | ✅ / ❌ |

**Status Geral**: **APROVADO** / **REPROVADO**

> Se REPROVADO: corrigir issues e re-executar auditoria. MVP não é entregue sem todos os grupos ✅.
