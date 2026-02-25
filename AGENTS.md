# Symbiotas e Agents — Guia Rápido

## Ponto de entrada: `ft_manager`

**Toda sessão começa pelo `ft_manager`.** Ele é o orquestrador — lê o estado, decide o que fazer e
delega para os outros symbiotas. Nunca inicie diretamente pelo `ft_coach` ou `forge_coder`.

Carregue o prompt: `process/symbiotes/ft_manager/prompt.md`

## Primeiros passos (nova sessão)

O `ft_manager` DEVE seguir este fluxo ao iniciar:

1. Ler `process/fast_track/state/ft_state.yml`.
2. **Se projeto novo** (`current_phase: null`):
   - Atualizar `ft_state.yml`: `current_phase: ft_mdd`, `current_cycle: cycle-01`.
   - Delegar ao `ft_coach`: iniciar `ft.mdd.01.hipotese`.
3. **Se projeto em andamento**:
   - Informar: "Retomando de [next_recommended_step]. Último step: [last_completed_step]."
   - Continuar o fluxo a partir dali, delegando ao symbiota correto.

> **Regra**: Nunca ficar parado esperando. Leu o estado → age.

## Referências obrigatórias

- Prompts dos symbiotas:
  - `process/symbiotes/ft_manager/prompt.md` ← **ponto de entrada**
  - `process/symbiotes/ft_coach/prompt.md`
  - `process/symbiotes/forge_coder/prompt.md`
- Processo e estado:
  - `process/fast_track/FAST_TRACK_PROCESS.yml`
  - `process/fast_track/FAST_TRACK_PROCESS.md`
  - `process/fast_track/state/ft_state.yml`
  - `process/fast_track/SUMMARY_FOR_AGENTS.md`
- Regras de arquitetura e código:
  - `docs/integrations/forgebase_guides/usuarios/forgebase-rules.md`
  - `docs/integrations/forgebase_guides/agentes-ia/`

## Symbiotas

| Symbiota | Papel | Prompt |
|----------|-------|--------|
| `ft_manager` | Orquestrador — gerencia o processo completo, valida entregas e interage com o stakeholder | `process/symbiotes/ft_manager/prompt.md` |
| `ft_coach` | MDD, Planning, Feedback — conduzido pelo ft_manager | `process/symbiotes/ft_coach/prompt.md` |
| `forge_coder` | TDD, Delivery, E2E — orquestrado pelo ft_manager | `process/symbiotes/forge_coder/prompt.md` |

## Defaults para qualquer symbiota

- Clean/Hex: domínio é puro; adapters só via ports/usecases; nunca colocar I/O no domínio.
- CLI-first e offline: validar via CLI; evitar HTTP/TUI no MVP; sem rede externa por padrão.
- Persistência: estados em YAML; auto-commit Git por step quando habilitado.
- Documentar sessões em `project/docs/sessions/` quando aplicável.

## Symbiotas de código/tests (TDD)

- Consultar:
  - `docs/integrations/forgebase_guides/agentes-ia/guia-completo.md`
  - `docs/integrations/forgebase_guides/usuarios/forgebase-rules.md`
  - Prompt em `process/symbiotes/forge_coder/prompt.md`.
- Seguir o fluxo Fast Track:
  - PRD em `project/docs/PRD.md`
  - Task list em `project/docs/TASK_LIST.md`
  - Testes em `tests/`
  - Código em `src/` seguindo camadas ForgeBase.
- Usar exceções específicas e logging/métricas do ForgeBase; Rich apenas para UX em CLI.

## Outras observações

- Quando o usuário pedir para carregar/impersonar uma persona de symbiota ou agente,
  responda sempre com o nome do symbiota na cor verde entre chaves, por exemplo:
  `[ft_manager] diz: Retomando de ft.tdd.01.selecao...`.
- O `ft_manager` usa colchetes verdes para identificar qual symbiota está falando em cada momento,
  por exemplo: `[ft_coach]`, `[forge_coder]`, `[ft_manager]`.
