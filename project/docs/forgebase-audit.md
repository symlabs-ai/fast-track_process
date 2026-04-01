# ForgeBase Audit Report

**Projeto:** ForgeProcess Fast Track — ft engine
**Data:** 2026-04-01
**Auditor:** ForgeBase Auditor (forge_coder)
**Escopo:** `ft/`, `src/`

---

## 1. UseCaseRunner Wiring

| UseCase | Invocação via Runner | Composition Root | Status |
|---------|---------------------|------------------|--------|
| — | Nenhum UseCase implementado ainda | — | ⚠️ N/A |

**Contexto:** O projeto usa `StepRunner` (`ft/engine/runner.py`) como orquestrador do processo deterministico.
A camada `src/application/usecases/` existe (scaffolded) mas não contém UseCases implementados.
Os `__init__.py` de `src/application/usecases/` comentam corretamente: *"Executados via UseCaseRunner.run(), nunca .execute() direto"* — padrão está mapeado, implementação pendente.

**Resumo**: 0/0 UseCases (nenhum implementado ainda — sprint de domínio ainda não iniciada).

---

## 2. Value Tracks & Support Tracks

**Arquivo esperado:** `forgepulse.value_tracks.yml`

| UseCase | Track | Tipo | Status |
|---------|-------|------|--------|
| — | — | — | ❌ Arquivo ausente |

- [ ] Todo UseCase implementado está mapeado
- [ ] Support Tracks têm `supports:` correto
- [ ] Sem `track_type` como campo explícito
- [ ] Descrições claras e alinhadas ao domínio

**Problemas encontrados:**
- `forgepulse.value_tracks.yml` não existe no projeto
- Nenhum UseCase foi implementado ainda, portanto nenhum mapeamento é possível

**Resumo**: 0/0 UseCases mapeados. `forgepulse.value_tracks.yml` precisa ser criado quando os UseCases forem implementados.

---

## 3. Observabilidade (Pulse)

**Arquivo esperado:** `artifacts/pulse_snapshot.json`

- [ ] Arquivo existe
- [ ] `mapping_source: "spec"`
- [ ] Agregação por `value_track` (não apenas `legacy`)
- [ ] Métricas presentes: count, duration, success, error
- [ ] Eventos mínimos: start, finish, error

**Problemas encontrados:**
- `artifacts/pulse_snapshot.json` não existe
- Nenhuma integração com `TrackMetrics` ou `LogService` do ForgeBase no código atual
- O motor rastreia métricas em memória (`state.metrics`) mas sem exportar para Pulse

**Resumo**: Observabilidade Pulse não implementada. Métricas básicas (`steps_completed`, `llm_calls`) existem no estado YAML mas não estão integradas ao ecossistema ForgeBase Pulse.

---

## 4. Logging

> ⚠️ Seção mais crítica — uso extensivo de `print()` no código de produção.

### Problemas encontrados

| Arquivo | Linha | Problema | Severidade | Correção |
|---------|-------|----------|-----------|----------|
| `ft/engine/runner.py` | 254-256 | `print()` em `init_state()` | ❌ CRÍTICO | `logger.info(...)` |
| `ft/engine/runner.py` | 271 | `print(f"  ERRO: {e}")` | ❌ CRÍTICO | `logger.error("Erro ao carregar state", exc_info=True)` |
| `ft/engine/runner.py` | 275 | `print("Processo nao inicializado...")` | ❌ CRÍTICO | `logger.warning(...)` |
| `ft/engine/runner.py` | 286-295 | `print()` em loop de processo | ❌ CRÍTICO | `logger.info(...)` |
| `ft/engine/runner.py` | 305-309 | `print()` para node info | ❌ CRÍTICO | `logger.info(...)` |
| `ft/engine/runner.py` | 388-457 | `print()` em `_run_llm_step()` (~15 ocorrências) | ❌ CRÍTICO | `logger.info/error(...)` |
| `ft/engine/runner.py` | 461-473 | `print()` em `_run_gate()` | ❌ CRÍTICO | `logger.info/warning(...)` |
| `ft/engine/runner.py` | 495-497 | `print()` em `_maybe_auto_commit()` | ❌ CRÍTICO | `logger.info(...)` |
| `ft/engine/runner.py` | 534-599 | `print()` em `_run_review()` e `_run_parallel_group()` | ❌ CRÍTICO | `logger.info/error(...)` |
| `ft/engine/runner.py` | 690-750 | `print()` em `approve()` e `reject()` | ❌ CRÍTICO | `logger.info(...)` |
| `ft/engine/runner.py` | 763-811 | `print()` em `status()` | ⚠️ MÉDIO | `logger.info(...)` ou stdout intencional |
| `ft/engine/state.py` | — | Sem logging algum | ⚠️ MÉDIO | Adicionar `logger = logging.getLogger(__name__)` |

**Nota contextual:** O motor é uma CLI — alguns `print()` em `status()` e no loop de saída podem ser **intencionais** (saída para o usuário), não logging de aplicação. Recomenda-se separar:
- Saída de usuário (CLI output): manter `print()` ou usar `click.echo()`
- Logging de diagnóstico (erros, estados internos, LLM calls): migrar para `logger`

### Checklist

- [ ] Sem `print()` em código de produção (exceto CLI output intencional)
- [ ] Logs estruturados (não strings concatenadas)
- [ ] Níveis corretos: DEBUG detalhe, INFO fluxo, WARNING degradação, ERROR falhas
- [x] Sem dados sensíveis nos logs (tokens, passwords, PII) — não encontrado
- [ ] Sem logs excessivos em loops (há `print()` dentro do loop principal em `runner.py`)
- [ ] Mensagens descritivas (não "error occurred" genérico)
- [ ] Logger por módulo: `logging.getLogger(__name__)` — nenhum módulo usa logging estruturado

**Resumo**: 0 módulos usam `logging` estruturado. `ft/engine/runner.py` contém ~40+ chamadas `print()`. Correção necessária antes de produção.

---

## 5. Arquitetura Clean/Hex

### Estrutura atual

```
ft/engine/          # Motor deterministico (não segue Clean/Hex)
src/
├── domain/         # ✅ Estrutura scaffolded (EntityBase mapeado em comentários)
├── application/    # ✅ Estrutura scaffolded (UseCaseBase, UseCaseRunner mapeados)
├── infrastructure/ # ✅ Diretório criado
└── adapters/cli/   # ✅ Diretório criado
```

- [x] Estrutura de pastas Clean/Hex criada (`src/domain`, `src/application`, `src/adapters`, `src/infrastructure`)
- [ ] Domínio puro: sem I/O, sem imports de infrastructure/adapters — **não implementado**
- [ ] Ports definidos como abstrações (ABC ou Protocol) — `src/application/ports/` vazio
- [ ] Adapters implementam ports, não ao contrário — nenhum adapter implementado
- [ ] Sem dependência circular entre camadas — não aplicável (sem código)

### Violações encontradas no código existente (`ft/`)

| Camada | Arquivo | Violação | Correção |
|--------|---------|----------|----------|
| Engine (não Clean/Hex) | `ft/engine/runner.py` | I/O direto: `print()`, `Path.read_text()`, `subprocess` | Extrair para adapters/ports |
| Engine | `ft/engine/runner.py` | `from ft.engine.delegate import delegate_with_feedback` importado dentro de método (linha 718) | Mover para nível de módulo |
| Engine | `ft/engine/state.py` | `yaml.dump/load` direto (sem port) | Abstrair em `StateRepositoryPort` |
| Engine | `ft/engine/runner.py` | Sem herança de `UseCaseBase` | Refatorar para usar base classes ForgeBase |

**Contexto importante:** O código em `ft/` foi desenvolvido como motor deterministico (processo, não domínio de negócio). A camada `src/` está corretamente estruturada para receber a lógica de domínio futura. A violação principal é que `ft/engine/` não foi escrito seguindo Clean/Hex, mas esta camada pode ser considerada "infrastructure/framework" no contexto ForgeBase.

**Resumo**: Estrutura Clean/Hex scaffolded em `src/` mas não implementada. `ft/engine/` opera fora da arquitetura ForgeBase por design (motor do processo). Quando UseCases forem implementados em `src/`, devem seguir rigorosamente as regras.

---

## Resultado Final

| Grupo | Itens | Pass | Fail | Status |
|-------|-------|------|------|--------|
| UseCaseRunner | 0 implementados | 0 | 0 | ⚠️ N/A |
| Value/Support Tracks | 1 | 0 | 1 | ❌ |
| Observabilidade | 5 | 0 | 5 | ❌ |
| Logging | 7 | 1 | 6 | ❌ |
| Arquitetura | 4 | 1 | 3 | ❌ |

**Status Geral**: **REPROVADO**

> O projeto está em fase inicial de implementação (sprint de domínio não iniciada). As camadas `src/domain`, `src/application`, `src/adapters` e `src/infrastructure` existem mas estão vazias. As violações principais são:
>
> 1. **Logging**: `ft/engine/runner.py` usa `print()` extensivamente — migrar para `logging.getLogger(__name__)` nas chamadas de diagnóstico
> 2. **Value Tracks**: `forgepulse.value_tracks.yml` deve ser criado junto com os primeiros UseCases
> 3. **Observabilidade**: Integrar com Pulse quando UseCases forem implementados
> 4. **Arquitetura**: Implementar UseCases, Entities e Ports em `src/` seguindo as base classes ForgeBase
>
> Re-auditar após sprint de implementação de domínio.
