# Fast Track — Backlog de Melhorias

> Melhorias identificadas na análise do repositório. Ordenadas por impacto.

## Prioridade Crítica (pré-requisito)

### BL-00: Reorganização do repositório — separação estático vs. dinâmico
- **Problema**: `process/` misturava definição do processo (estática) com estado do projeto (dinâmico). CLI e tooling em `process/` contaminavam o repo do projeto. Sem `.gitignore`.
- **Solução**: Mover `ft_state.yml` para `project/state/`. Adicionar `.gitignore` que exclui `process/` (estático, vem do template). Processo pode ser atualizado sobrescrevendo `process/` sem perder estado.
- **Entrega**: `project/state/ft_state.yml`, `.gitignore`, referências atualizadas em 8 arquivos (75 refs a ft_state.yml, ~80 refs a process/ paths)
- **Status**: concluido

## Prioridade Alta (impacto imediato)

### BL-01: CLI unificada (ft.py) com validador
- **Problema**: ft_gatekeeper é "determinístico" mas só existe como prompt MD — nenhum código valida gates programaticamente
- **Solução**: CLI unificada `ft.py` data-driven (lê FAST_TRACK_PROCESS.yml e schemas em runtime). Subcommands: `init`, `validate state`, `validate artifacts`, `validate gate`, `tokens`, `self-check`
- **Entrega**: `process/fast_track/tools/ft.py`
- **Status**: concluido

### BL-02: JSON Schema para ft_state.yml
- **Problema**: `ft_state.yml` não tinha schema — agentes LLM podiam gravar campos inválidos e corromper estado
- **Solução**: JSON Schema com todos os enums, tipos e constraints do state. Consumido pelo validador em runtime.
- **Entrega**: `process/fast_track/schemas/ft_state.schema.json`
- **Status**: concluido

### BL-03: Scaffold Clean/Hex em src/
- **Problema**: `src/` estava vazio (.gitkeep) — cada projeto reinventava a estrutura
- **Solução**: Scaffold com `domain/`, `application/`, `infrastructure/`, `adapters/` conforme ForgeBase Rules. Integrado ao `ft.py init` (cria automaticamente se ausente).
- **Entrega**: `src/` populado + `ft.py init` verifica/cria scaffold
- **Status**: concluido

## Prioridade Média (qualidade do processo)

### BL-04: Consistência YAML ↔ MD do processo
- **Problema**: `FAST_TRACK_PROCESS.yml` e `.md` descrevem o mesmo processo separadamente — propenso a drift
- **Solução**: `ft.py generate ids` (gera FAST_TRACK_IDS.md do YAML) + `ft.py generate check` (valida consistência YAML vs. IDs, MD, Summary)
- **Entrega**: Subcommands `generate ids` e `generate check` no ft.py
- **Status**: concluido

### BL-05: Tiering das regras críticas
- **Problema**: 32 regras todas com mesmo peso — carga cognitiva alta para LLMs
- **Solução**: Reorganizado em 3 tiers: Tier 1 invioláveis (10), Tier 2 defaults (18), Tier 3 contextuais (4). Tiers com headings e descrições no SUMMARY_FOR_AGENTS.md
- **Entrega**: SUMMARY_FOR_AGENTS.md atualizado
- **Status**: concluido

### BL-06: Procedimento de rollback/recovery
- **Problema**: State machine só avançava — sem procedimento para cenários de bloqueio
- **Solução**: Tabela de recovery no SUMMARY_FOR_AGENTS.md com 6 cenários: sprint gate 3x, smoke trava, gate.delivery repetido, stakeholder ausente, estado corrompido, divergência processo/state
- **Entrega**: Seção "Recovery" no SUMMARY_FOR_AGENTS.md
- **Status**: concluido

### BL-07: Hyper-mode parcial
- **Problema**: Hyper-mode assumia PRD "abrangente" mas não tratava PRDs parciais
- **Solução**: Quando 5+ seções ausentes, ft_coach conduz discovery conversacional para seções ausentes (como no modo normal) em vez de questionário gigante. Documentado no processo e no prompt do ft_coach
- **Entrega**: FAST_TRACK_PROCESS.md + ft_coach/prompt.md atualizados
- **Status**: concluido

## Prioridade Baixa (housekeeping)

### BL-08: Runner E2E funcional
- **Problema**: Nenhum runner real — processo exigia "E2E CLI gate obrigatório" mas template não entregava isso
- **Solução**: `tests/e2e/run-all.sh` que roda pytest (unit+smoke) + tracks E2E por ciclo, com exit codes e output colorido
- **Entrega**: `tests/e2e/run-all.sh` (executável)
- **Status**: concluido

### BL-09: Eliminar git-dev.zip
- **Problema**: Blob binário opaco dificultava review e versionamento
- **Solução**: Extraído para `env/git-dev/` (4 arquivos: pre-commit-config, ruff.toml, install script, requirements). setup_env.sh atualizado para copiar em vez de unzip.
- **Entrega**: `env/git-dev/` + `setup_env.sh` atualizado + zip removido
- **Status**: concluido

### BL-10: Mover changelog para CHANGELOG.md
- **Problema**: ~230 linhas de changelog no README.md
- **Solução**: Movido para `CHANGELOG.md`. README mantém só versão atual + link.
- **Entrega**: `CHANGELOG.md` + README enxuto
- **Status**: concluido

### BL-11: Documento de anti-patterns
- **Problema**: Regras genéricas são menos efetivas que exemplos concretos de erros
- **Solução**: 10 anti-patterns com cenário, consequência e correção: mvp_status sem smoke, pular sprint expert gate, step IDs inventados, N/A em gates, paths errados, acceptance falsos, tasks de sprint futura, ft_manager auto-validando, hyper-mode com seções ausentes, ignorar cobertura
- **Entrega**: `process/fast_track/ANTI_PATTERNS.md`
- **Status**: concluido

---

## Resumo

| ID | Título | Prioridade | Status |
|----|--------|------------|--------|
| BL-00 | Reorganização estático/dinâmico | Crítica | concluido |
| BL-01 | CLI unificada (ft.py) | Alta | concluido |
| BL-02 | JSON Schema ft_state | Alta | concluido |
| BL-03 | Scaffold Clean/Hex | Alta | concluido |
| BL-04 | Consistência YAML ↔ MD | Média | concluido |
| BL-05 | Tiering das regras | Média | concluido |
| BL-06 | Rollback/recovery | Média | concluido |
| BL-07 | Hyper-mode parcial | Média | concluido |
| BL-08 | Runner E2E funcional | Baixa | concluido |
| BL-09 | Eliminar git-dev.zip | Baixa | concluido |
| BL-10 | Mover changelog | Baixa | concluido |
| BL-11 | Anti-patterns doc | Baixa | concluido |
