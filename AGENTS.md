# Symbiotas e Agents — Guia Rápido

## Criar novo projeto

> **Requer**: `ft` instalado em `~/.local/bin/ft` (script global do Fast Track).

```bash
ft init meu-projeto
ft init meu-projeto --remote git@github.com:user/meu-projeto.git
```

Isto clona o template, desconecta do remote original, inicializa o projeto (dirs, scaffold,
agents do Claude Code, .gitignore, token tracking) e opcionalmente conecta ao remote do projeto.

## Projeto ja clonado — instalar agents

Se o projeto ja foi clonado mas os agents do Claude Code ainda nao existem:

```bash
ft init            # de dentro do projeto — cria agents + configura tudo
ft init --check    # verifica sem criar nada
```

Sem os agents instalados, os symbiotas nao estarao disponiveis no Claude Code.

## Ponto de entrada: `ft_manager`

**Toda sessão começa pelo `ft_manager`.** Ele é o orquestrador — lê o estado, decide o que fazer e
delega para os outros symbiotas. Nunca inicie diretamente pelo `ft_coach` ou `forge_coder`.

Carregue o prompt: `process/symbiotes/ft_manager/prompt.md`

## Primeiros passos (nova sessão)

O `ft_manager` DEVE seguir este fluxo ao iniciar:

1. Executar `ft init --check`.
   - Se BLOCK: executar `ft.py init` para resolver. Repetir ate PASS.
2. Ler `project/state/ft_state.yml`.
3. **Se projeto novo** (`current_phase: null`):
   - Atualizar `ft_state.yml`: `current_phase: ft_mdd`, `current_cycle: cycle-01`.
   - Delegar ao `ft_coach`: iniciar `ft.mdd.01.hipotese`.
4. **Se projeto em andamento**:
   - Informar: "Retomando de [next_step]. Último step: [last_completed_step]."
   - Informar também a sprint ativa: `current_sprint` e `sprint_status`, quando preenchidos.
   - Continuar o fluxo a partir dali, delegando ao symbiota correto.

> **Regra**: Nunca ficar parado esperando. Leu o estado → age.

## Referências obrigatórias

- Prompts dos symbiotas:
  - `process/symbiotes/ft_manager/prompt.md` ← **ponto de entrada**
  - `process/symbiotes/ft_gatekeeper/prompt.md`
  - `process/symbiotes/ft_acceptance/prompt.md`
  - `process/symbiotes/ft_coach/prompt.md`
  - `process/symbiotes/forge_coder/prompt.md`
- Processo e estado:
  - `process/fast_track/FAST_TRACK_PROCESS.yml`
  - `process/fast_track/FAST_TRACK_PROCESS.md`
  - `project/state/ft_state.yml`
  - `process/fast_track/SUMMARY_FOR_AGENTS.md`
- Regras de arquitetura e código:
  - `docs/integrations/forgebase_guides/usuarios/forgebase-rules.md`
  - `docs/integrations/forgebase_guides/agentes-ia/`

## Symbiotas

| Symbiota | Papel | Prompt |
|----------|-------|--------|
| `ft_manager` | Orquestrador — gerencia o processo completo, delega validações ao gatekeeper e interage com o stakeholder | `process/symbiotes/ft_manager/prompt.md` |
| `ft_gatekeeper` | Validador determinístico de stage gates — PASS ou BLOCK, sem interpretação criativa | `process/symbiotes/ft_gatekeeper/prompt.md` |
| `ft_acceptance` | Especialista em design de cenários de aceitação por Value/Support Track | `process/symbiotes/ft_acceptance/prompt.md` |
| `ft_coach` | MDD, Planning, Feedback — conduzido pelo ft_manager | `process/symbiotes/ft_coach/prompt.md` |
| `forge_coder` | TDD, Delivery, E2E — orquestrado pelo ft_manager | `process/symbiotes/forge_coder/prompt.md` |

## CLI do processo (ft.py)

Ferramenta de validação determinística em `process/fast_track/tools/ft.py`. Data-driven — lê o YAML
do processo e schemas em runtime.

```bash
ft <command>
```

| Comando | Quem usa | Quando |
|---------|----------|--------|
| `init --check` | ft_manager | Bootstrap — antes de qualquer fase |
| `init` | ft_manager / forge_coder | Criar dirs, scaffold, sincronizar versão |
| `validate state` | ft_manager, ft_gatekeeper | Após cada atualização do ft_state.yml |
| `validate artifacts` | ft_manager | Antes do handoff |
| `validate integration` | ft_gatekeeper | Mock audit, dead code, wiring — antes do gate.audit |
| `validate gate <id>` | ft_gatekeeper | Pre-flight mecânico antes de cada gate |
| `generate check` | ft_manager | Verificar consistência YAML ↔ MD |
| `generate ids` | ft_manager | Regenerar FAST_TRACK_IDS.md após mudança no processo |
| `tokens snapshot --step <id>` | ft_manager | Momentos-chave de token tracking |
| `self-check` | ft_manager | Verificar consistência interna da CLI vs. processo |

> **Regra**: Se qualquer comando retornar BLOCK, parar e resolver antes de prosseguir.

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
