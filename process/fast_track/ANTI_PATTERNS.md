# Fast Track — Anti-Patterns

> Erros comuns que agentes LLM cometem ao executar o Fast Track. Cada um com cenário, consequência e correção.

---

### 1. Declarar mvp_status sem smoke

**Cenário**: forge_coder termina unit tests, todos passam. ft_manager grava `mvp_status: demonstravel`.
**Consequência**: O produto nunca foi executado de verdade. Unit tests passam com mocks, mas o binário pode nem subir.
**Correção**: `mvp_status: demonstravel` exige `smoke-cycle-XX.md` com output real documentado. Validar com `ft.py validate gate smoke`.

### 2. Pular Sprint Expert Gate

**Cenário**: Todas as tasks da sprint passaram gate.delivery. ft_manager avança direto para smoke.
**Consequência**: Sem review externo, erros de design acumulam entre sprints. A dívida técnica só aparece no E2E.
**Correção**: Toda sprint termina com `/ask fast-track`. Report salvo em `project/docs/sprint-review-sprint-XX.md`. Sem exceção.

### 3. Gravar step IDs inventados

**Cenário**: ft_manager grava `completed_steps: ["ft.tdd.01.selection"]` (em inglês, em vez de `ft.tdd.01.selecao`).
**Consequência**: Estado corrompido. Pre-flights e validate não reconhecem o step. Progresso reportado diverge do real.
**Correção**: `ft.py validate state` detecta IDs inválidos. Step IDs vêm do FAST_TRACK_PROCESS.yml, nunca inventados.

### 4. Aceitar N/A como resultado de gate

**Cenário**: ft_gatekeeper marca item como "N/A — não implementado ainda" em vez de BLOCK.
**Consequência**: Gate passa com pendência real. O problema sobrevive até o E2E ou pior, até produção.
**Correção**: N/A = BLOCK. Sempre. Se o item é obrigatório e não está implementado, é BLOCK.

### 5. Artefatos em paths errados

**Cenário**: forge_coder salva smoke report em `process/fast_track/state/smoke-cycle-01.md` em vez de `project/docs/`.
**Consequência**: ft_gatekeeper não encontra o artefato, bloqueia. Ou pior: encontra por busca e aceita — mas o path errado indica confusão entre processo e projeto.
**Correção**: Artefatos de projeto vão em `project/docs/`. `ft.py validate artifacts` verifica paths canônicos.

### 6. Acceptance tests que não testam nada

**Cenário**: forge_coder cria testes de acceptance que fazem `assert os.path.exists("src/main.py")` ou leem arquivos de config.
**Consequência**: 100% dos ACs "passam" sem nenhuma interação com a interface real. O produto pode estar quebrado.
**Correção**: Acceptance requer interação real (HTTP requests, Playwright, Chrome). ft_gatekeeper deve abrir os arquivos de teste e confirmar.

### 7. Puxar tasks de sprint futura

**Cenário**: forge_coder está rápido e começa a implementar tasks da sprint-02 enquanto sprint-01 ainda está ativa.
**Consequência**: Sprint Expert Gate não cobre as tasks extra. gate.delivery pode não ter sido executado. Estado inconsistente.
**Correção**: `current_sprint` no state define o escopo. Nenhuma task fora da sprint atual pode ser selecionada.

### 8. ft_manager validando seus próprios gates

**Cenário**: ft_manager executa o checklist de gate.delivery internamente em vez de delegar ao ft_gatekeeper.
**Consequência**: Conflito de interesse — quem orquestra não pode ser quem valida. Tendência a ser leniente.
**Correção**: Separação de responsabilidades: ft_manager orquestra, ft_gatekeeper bloqueia. Sempre.

### 9. Avançar com seções ausentes no hyper-mode

**Cenário**: ft_coach detecta 3 seções `❌ ausente` no PRD do stakeholder. Em vez de insistir, gera o PRD com placeholders e avança.
**Consequência**: Tasks derivadas de seções ausentes são vagas. Implementação diverge da intenção. Retrabalho garantido.
**Correção**: Nenhuma seção `❌ ausente` pode permanecer após incorporação. ft_coach insiste até resolver.

### 10. Ignorar cobertura no self-review

**Cenário**: forge_coder faz self-review mas pula o check de cobertura (`--cov`). Reporta "tudo ok".
**Consequência**: gate.delivery pode passar (se ft_gatekeeper não rodou coverage), mas arquivos críticos ficam sem teste.
**Correção**: Self-review item 6: "Cobertura >= 85% nos arquivos alterados". Rodar com `--cov` e reportar número real.
