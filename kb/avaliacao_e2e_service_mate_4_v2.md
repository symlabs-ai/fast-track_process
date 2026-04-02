# Avaliação E2E — service_mate_4 (segunda tentativa)

> Data: 2026-04-02
> Processo: Fast Track V2 — v0.8.0 (FAST_TRACK_PROCESS_V2.yml)
> Projeto-teste: service_mate_4 (PRD do ServiceMate v2.2.0)
> Comparação com: avaliacao_e2e_service_mate_4.md (nota anterior: 4/10)
> **Nota: 7/10**

---

## Contexto

Segunda rodada, iniciada após o redesenho completo do processo v0.8.0 que adicionou:
- Detecção de `interface_type` via `read_artifact`
- Decision node que ramifica para sprint-03-frontend quando `interface_type=ui`
- `gate.frontend` validando estrutura mínima PWA
- `gate.mvp.frontend` no handoff para projetos UI/mixed

Resultado do run: **29/29 nodes — PASS** (todos os gates aprovados, 130 testes passando, lint limpo).

---

## O que funcionou bem

### Processo
- `interface_type=ui` detectado corretamente em `tech_stack.md`
- Roteamento pelo decision node funcionou sem intervenção
- sprint-03-frontend executado (scaffold → implement → gate)
- `gate.mvp.frontend` ativado no handoff — processo não finalizou sem verificar o frontend
- 29/29 nodes PASS, sem bloqueios manuais
- LLM calls: 23 (eficiente para escopo completo)

### Produto entregue
- **Backend FastAPI**: rotas REST completas (clients, services, appointments, catalog, billing, assistant)
- **Frontend React/Vite PWA**: 6 rotas funcionais (agenda, clientes, serviços, catálogo, faturamento, assistente)
- **Integração API**: proxy `/api` → backend funcionou após correção do rewrite
- **Assistente IA**: respondeu corretamente a linguagem natural consultando o backend
- **130 testes** passando (101 unitários + 17 E2E + 12 outros), lint limpo
- Catálogo de serviços com CRUD (US-02 do PRD atendida)
- Financeiro com abas Cobranças/Resumo

---

## O que falhou ou ficou abaixo do esperado

### Bugs de processo (encontrados e corrigidos durante o run)
1. **`min_lines` em diretório**: `ft.frontend.02.implement` tinha `min_lines: 50` como validator com output `frontend/src/` (diretório). Causou `IsADirectoryError`. Corrigido no YAML.
2. **`--mvp` não auto-aprovava**: `requires_approval` bloqueava mesmo em modo MVP. Corrigido adicionando `_auto_approve` no runner.
3. **`index.html` na pasta errada**: LLM colocou em `frontend/public/` em vez de `frontend/` (raiz do Vite). Servidor retornava 404. Corrigido manualmente.
4. **Proxy sem rewrite**: Vite proxy encaminhava `/api/appointments` para o backend sem remover o prefixo `/api`. Backend só tinha `/appointments`. Corrigido adicionando `rewrite` no `vite.config.js`.

### Qualidade de UX (não são bugs de processo — são limitações do LLM como designer)
- **Catálogo sem acesso no bottom nav**: acessível apenas via ícone ambíguo no AppBar de Serviços Prestados. Discoverabilidade zero.
- **Ícone não-descritivo**: ícone no canto superior direito de `/servicos` não é autoexplicativo — usuário real não descobriria que leva ao catálogo.
- Ambos são falhas de UX geradas pelo LLM, não falhas do processo em si.

---

## Comparativo com tentativa anterior

| Critério | Tentativa 1 (v0.7.x) | Tentativa 2 (v0.8.0) |
|---|---|---|
| Nodes completados | 22/22 | 29/29 |
| Frontend entregue | ✗ Não | ✓ Sim |
| PWA scaffold | ✗ Não | ✓ Sim |
| Testes | 283 (só back) | 130 (back + E2E) |
| Integração API | N/A | ✓ Funcional |
| Gate MVP honesto | ✗ Aprovado sem frontend | ✓ Verificou frontend |
| Intervenção manual | Alta (bugs críticos) | Média (4 correções menores) |
| Nota | 4/10 | 7/10 |

---

## Causa das 3 notas perdidas

- **-1**: Bugs de processo ainda presentes (3 de 4 eram previsíveis e evitáveis com testes do próprio engine)
- **-1**: UX fraca — LLM não tem senso de discoverabilidade. Catálogo inacessível na prática
- **-1**: `index.html` na pasta errada indica que o prompt do `ft.frontend.01.scaffold` não é suficientemente prescritivo sobre a estrutura do Vite

---

## Próximos passos sugeridos

1. **Corrigir prompts do scaffold**: especificar explicitamente que `index.html` vai na raiz, não em `public/`
2. **Adicionar validator de estrutura Vite**: checar `index.html` na raiz como parte do `gate.frontend`
3. **Testar `--mvp` auto-approve** com cobertura de teste unitário no engine
4. **Guideline de navegação no prompt do frontend**: exigir que todas as seções do PRD apareçam no bottom nav ou menu explícito
