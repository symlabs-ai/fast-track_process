# Avaliação E2E — Service Mate 7

**Data:** 2026-04-03  
**Processo:** Fast Track V2 — v0.8.9  
**Ciclo:** cycle-01  
**Nota geral:** 8.5/10

---

## Resumo Executivo

SM7 é o primeiro ciclo do ServiceMate a receber veredicto **RELEASE_CANDIDATE** do processo automatizado. O `api_contract.md` gerado em `ft.plan.05.api_contract` eliminou completamente o problema de mismatch de rotas presente em SM4–SM6: frontend e backend usaram os mesmos 21 endpoints sem divergência. O acceptance CLI (novo em v0.8.9) validou 24/24 registros via HTTP real. O `frontend-prd-review` foi APPROVED pela primeira vez — bottom nav com 5 itens corretos, FAB do Assistente no Layout, rota `/catalogo` presente. O `ft.handoff.02.plano_voo` foi gerado automaticamente com veredicto estruturado e debts priorizados para SM8.

---

## Checklist de Telas

| Tela | Carrega | Navegação | URL change | API funcional | Observações |
|------|---------|-----------|------------|---------------|-------------|
| Dashboard (/) | ✅ | ✅ | ✅ | ✅ | GET /api/dashboard — próximos agendamentos + total pendente |
| Clientes (/clientes) | ✅ | ✅ | ✅ | ✅ | CRUD completo; busca por nome |
| Catálogo (/catalogo) | ✅ | ✅ | ✅ | ✅ | Era DT-04 herdada do SM6 — resolvida |
| Agenda (/agenda) | ✅ | ✅ | ✅ | ✅ | Lista cronológica; agendamentos futuros destacados |
| Cobranças (/cobrancas) | ✅ | ✅ | ✅ | ✅ | Status pendente/pago; PATCH /api/cobrancas/{id}/status |
| Assistente (FAB) | ✅ | ✅ | n/a | ✅ | FAB fixo em todas as telas; parser de linguagem natural + voz |

---

## O Que Funcionou

### Correções do processo v0.8.9

- **API sem mismatch**: `api_contract.md` gerado no planning → frontend (`/api/clientes`, `/api/servicos`, `/api/agendamentos`, `/api/cobrancas`) e backend (mesmo esquema) 100% alinhados. Mismatch presente em SM4–SM6 não ocorreu.
- **Acceptance CLI 24/24**: `ft.acceptance.01.cli` validou todos os endpoints com `test_data.md` via HTTP real antes do smoke. `gate.acceptance.cli` passou sem bloqueios.
- **Frontend PRD review APPROVED**: bottom nav com 5 itens exatos (`Início | Clientes | Catálogo | Agenda | Cobranças`), FAB do Assistente em `Layout.jsx` (position: fixed, z-index: 200), rota `/catalogo` presente. KB-P5 não foi acionado.
- **Deep links funcionam**: `App.jsx` usa `Routes` + `Route path=` (React Router DOM) — KB-P4 não foi acionado. Navegação direta para `/clientes`, `/catalogo` etc. funcionaria.
- **Plano de voo automático**: `ft.handoff.02.plano_voo` gerou `project/docs/plano_de_voo.md` com veredicto RELEASE_CANDIDATE, débitos priorizados e comandos para SM8.

### Entregáveis P0

- ✅ US-01/02 — Cadastro e listagem de clientes (CRUD completo, busca)
- ✅ US-03/04 — Catálogo de serviços (era DT-04 do SM6)
- ✅ US-05/06 — Agendamentos com conflito de horário
- ✅ US-07 — Cobranças com status pendente/pago/vencido
- ✅ US-08 — Assistente por texto e voz (FAB flutuante, Web Speech API)
- ✅ 125 testes unitários + 14 smoke tests + 24 acceptance tests passando
- ✅ ForgeBase Audit (gate.audit) PASS — lint limpo, estrutura backend OK

---

## O Que Falhou

### P0 — Intervenções manuais necessárias

| Problema | Causa | Intervenção |
|---------|-------|-------------|
| `interface_type: ui` em vez de `mixed` | LLM escolheu `ui` no tech_stack apesar de PRD §8.6 indicar `mixed` | Editar manualmente `tech_stack.md` + `engine_state.yml` |
| `main.py` sem `sys.path` para `src/` | Import `from backend.app.database` falha ao subir via uvicorn | Adicionar `sys.path.insert(0, str(Path(__file__).parent / "src"))` manualmente |
| `/health` registrado como `/api/health` | LLM colocou health no prefixo `/api` em vez de na raiz | Adicionar `@app.get("/health")` manualmente |
| Gateway não registrado | Projeto novo sem registro no Symlabs [DEV] (workspace errado) | DevOps corrigiu workspace + vinculou API key |
| `CLAUDE.md` ausente | `ft init` não cria `CLAUDE.md` automaticamente | Criado manualmente; `ft init` atualizado (v0.8.9+) para criar automaticamente |

### P1 — Não entregues

- ❌ ForgeBase Pulse: 0/5 tracks instrumentados (3º ciclo consecutivo sem instrumentação) — elevado a P0 no SM8 pelo plano_de_voo
- ❌ SQLite em vez de PostgreSQL — tech stack diz PostgreSQL mas LLM entregou SQLite
- ❌ DB sem inicialização no startup — `create_all()` não é chamado; primeiro deploy limpo retorna 500

---

## Análise do api_contract.md

### Resultado: ZERO mismatch em 21 endpoints

| Módulo | Frontend (client.js) | Backend (main.py) | Match |
|--------|----------------------|-------------------|-------|
| Clientes | `/clientes` | `/api/clientes` | ✅ |
| Serviços | `/servicos` | `/api/servicos` | ✅ |
| Agendamentos | `/agendamentos` | `/api/agendamentos` | ✅ |
| Cobranças | `/cobrancas` | `/api/cobrancas` | ✅ |
| Dashboard | `/dashboard` | `/api/dashboard` | ✅ |
| Assistente | `/assistente/comando` | `/api/assistente/comando` | ✅ |

O LLM do `ft.plan.05.api_contract` estabeleceu a convenção "paths em português, lowercase, sem acentos" e ambos os executores (frontend e backend) seguiram. Mismatch principal do SM6 (`clients` vs `clientes`) não ocorreu.

---

## Análise dos Novos Gates (v0.8.9)

| Gate | Resultado | Observação |
|------|-----------|-----------|
| `ft.plan.04.test_data` | ✅ PASS | Massa de dados realista gerada (5 clientes, 6 serviços, 6 agendamentos, 7 cobranças) |
| `ft.plan.05.api_contract` | ✅ PASS | 21 endpoints com convenção PT-lowercase |
| `ft.acceptance.01.cli` | ✅ PASS | 24/24 registros via HTTP real; edge cases testados |
| `gate.acceptance.cli` | ✅ PASS | Relatório gerado sem FAILs |
| `ft.handoff.02.plano_voo` | ✅ PASS | Veredicto RELEASE_CANDIDATE; 12 docs carregados em hyper-mode |
| `gate.kb_review` (KB-SM5) | ❌ BLOCK | `interface_type=ui` com backend Python — corrigido manualmente |
| `gate.kb_review` (KB-P4) | ✅ n/a | BrowserRouter presente — não acionado |
| `gate.kb_review` (KB-P5) | ✅ n/a | prd_review APPROVED — não acionado |

---

## Causa Raiz dos Problemas Remanescentes

**`interface_type: ui` persistente (SM5 → SM6 → SM7):**
O LLM gera `interface_type: ui` quando o projeto tem frontend visível, mesmo que o PRD §8.6 diga `mixed`. O gate `gate.kb_review` KB-SM5 bloqueia corretamente, mas requer intervenção manual. Solução: o prompt de `ft.plan.02.tech_stack` deve incluir instrução explícita: "se o projeto tem backend Python + frontend separado, interface_type DEVE ser `mixed`".

**`main.py` sem `sys.path`:**
O LLM gerou `from src.backend.app.main import app` em `main.py` mas `src/backend/app/main.py` usa `from backend.app.database import ...` — inconsistência de path relativo. O gate `gate_server_starts` detecta o problema (sobe mas não responde), mas o prompt de `ft.delivery.00.entrypoint` não instrui o coder sobre o padrão correto de import.

---

## Comparação SM4 → SM7

| Critério | SM4 | SM5 | SM6 | SM7 |
|----------|-----|-----|-----|-----|
| Backend HTTP | ❌ | ❌ | ✅ | ✅ |
| Frontend renderiza | ✅ | ✅ | ✅ | ✅ |
| API integrada (zero mismatch) | ❌ | ❌ | ❌ | ✅ |
| Rotas com URL change | ❌ | — | ❌ | ✅ |
| Nav contract (5 itens, FAB) | ❌ | ❌ | ❌ | ✅ |
| Catálogo presente | ❌ | ❌ | ❌ | ✅ |
| Acceptance CLI PASS | — | — | — | ✅ |
| Plano de voo automático | ❌ | ❌ | ❌ | ✅ |
| Veredicto processo | — | — | ITERATE | **RELEASE_CANDIDATE** |
| Intervenções manuais | ∞ | 3 | 1 | 5 |
| Nota | 4/10 | 6/10 | 6.5/10 | **8.5/10** |

---

## Nota por Dimensão

| Dimensão | Nota | Justificativa |
|----------|------|---------------|
| Backend HTTP | 9/10 | 21 endpoints, health, CRUD completo, smoke 14/14 |
| Frontend UI | 9/10 | 5 telas, nav contract OK, FAB, deep links |
| Integração API | 9/10 | Zero mismatch; acceptance CLI 24/24; proxy Vite funcional |
| Navegação/Routing | 9/10 | BrowserRouter + Route path= em todas as telas |
| Conformidade PRD | 8/10 | prd_review APPROVED; ForgeBase Pulse ausente |
| Processo Fast Track | 7/10 | 38/38 steps; 5 intervenções manuais necessárias |

**Média: 8.5/10**

---

## Lições para o Processo

1. **`ft.plan.02.tech_stack` deve instruir `mixed` explicitamente**: se `interface_type` precisa ser `mixed`, o prompt deve dizer "quando há backend Python e frontend separado, SEMPRE escolha `mixed`". O PRD §8.6 não é suficiente — o LLM não lê seções de restrição com consistência.

2. **`ft.delivery.00.entrypoint` deve padronizar o `sys.path`**: o prompt deve incluir o padrão correto: `main.py` na raiz deve adicionar `src/` ao `sys.path` antes de importar, OU o coder deve usar `uvicorn src.backend.app.main:app` diretamente.

3. **`/health` deve estar na raiz, não em `/api/health`**: o gate `gate_server_starts` espera `/health` (sem prefixo). O prompt do entrypoint deve especificar `@app.get("/health")` sem prefixo.

4. **ForgeBase Pulse em 3 ciclos sem instrumentação**: o gate.audit passa mesmo sem instrumentação. Adicionar validador `gate_pulse_instrumented` que verifica presença de `UseCaseRunner` no código antes de gate.audit PASS.

5. **`ft init` agora cria `CLAUDE.md` e `.claude/settings.local.json` automaticamente** (corrigido em v0.8.9 durante este ciclo). Também requer registro do projeto no gateway via `/ask devops` antes do primeiro `continue`.

---

## Artefatos Novos do v0.8.9 — Qualidade

| Artefato | Qualidade | Observação |
|---------|-----------|-----------|
| `test_data.md` | ✅ Alta | Dados realistas, edge cases cobertos (email null, valor mínimo, datas passadas) |
| `api_contract.md` | ✅ Alta | 21 endpoints, convenção PT consistente, ambos os lados seguiram |
| `acceptance-cli-report.md` | ✅ Alta | 24/24, edge cases documentados, problema de port detectado |
| `plano_de_voo.md` (auto) | ✅ Alta | Veredicto correto, débits priorizados, comandos para SM8 |
