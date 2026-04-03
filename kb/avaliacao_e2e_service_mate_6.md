# Avaliação E2E — Service Mate 6

**Data:** 2026-04-02  
**Processo:** Fast Track V2 — v0.8.2  
**Ciclo:** cycle-01  
**Nota geral:** 6.5/10

---

## Resumo Executivo

SM6 completou o processo Fast Track pela primeira vez com o node `ft.delivery.00.entrypoint` e
o `ft.prd.rewrite` ativos — ambos adicionados como resposta direta aos gaps do SM5.
O backend HTTP existe e sobe corretamente. O frontend renderiza todas as 5 telas do bottom nav.
Porém, a integração frontend↔backend está quebrada por mismatch de nomes de rota.

---

## Checklist de Telas

| Tela | Carrega | Navegação | API funcional | Observações |
|------|---------|-----------|---------------|-------------|
| Dashboard (/) | ✅ | ✅ | ❌ | Chama `/api/dashboard` → 404; backend tem `/financeiro/resumo` |
| Clientes | ✅ | ✅ | ❌ | Chama `/api/clients` → 404; backend tem `/clientes` |
| Agenda | ✅ | ✅ | ❓ | Não observado erro visual; agendamentos podem estar zerados |
| Cobranças | ✅ | ✅ | ❌ | Chama `/api/charges` → 404; backend tem `/cobranças`; toast de erro visível |
| Assistente | ✅ | ✅ | ❓ | Tela de chat renderiza; integração com LLM não testada |
| Catálogo (/catalogo) | ❌ | ❌ | ❌ | Rota não existe — redireciona para home |

---

## O Que Funcionou

- **Backend HTTP existe e sobe**: `main.py` + `uvicorn` respondendo em `/health` → `{"status":"ok"}`
- **5 telas do bottom nav**: Início, Clientes, Agenda, Cobranças, Assistente — todas renderizam
- **Empty states corretos**: Clientes, Agenda, Cobranças com mensagens e CTAs adequados
- **FABs de página**: Presentes em Clientes (+), Agenda (+), Cobranças (+)
- **Calendário de Agenda**: Navegação semanal funcional, botão "Hoje"
- **Resistência a falha de API**: Frontend não crasha quando API retorna 404 — exibe toast de erro

---

## O Que Falhou

### P0 — Integração API quebrada por mismatch de nomes de rota

| Frontend chama | Backend tem | Status |
|---------------|-------------|--------|
| `GET /api/dashboard` | `GET /financeiro/resumo` | ❌ 404 |
| `GET /api/clients` | `GET /clientes` | ❌ 404 |
| `GET /api/charges` | `GET /cobranças` | ❌ 404 |

O coder gerou o frontend com nomes em inglês (`clients`, `charges`, `dashboard`) enquanto o
backend foi implementado com nomes em português (`clientes`, `cobranças`, `financeiro/resumo`).
Nenhum gate ou review detectou esse mismatch antes da entrega.

### P1 — Assistente no bottom nav (viola PRD §8.5)

O PRD revisado pela `ft.prd.rewrite` especifica que o Assistente deve ser FAB flutuante, não
item do bottom nav. O coder manteve Assistente como 5º item do nav. O `gate.mvp.frontend`
passou ainda assim (estrutura PWA OK, mas conformidade nav não verificada).

### P2 — Catálogo sem rota

`/catalogo` não existe como rota — qualquer link direto ou deeplink para Catálogo retorna a
tela Home. Não há item de Catálogo no bottom nav nem rota registrada.

### P3 — Routing sem URL change (estado interno)

A navegação entre telas não muda a URL (permanece `localhost:5173/`). Deep links não
funcionam — navegar diretamente para `/clientes` ou `/cobranças` renderiza a tela Home.
Causa: app usa roteamento por estado interno ao invés de React Router com paths.

---

## Causa Raiz

O PRD §8.6 (Contrato de Integração HTTP) foi adicionado pelo `ft.prd.rewrite` mas chegou
**tarde demais** — o frontend já havia sido implementado na Fase 3 sem esse contrato.
Na Fase 5 (`ft.delivery.00.entrypoint`), o coder criou o backend sem verificar se os nomes
de rota eram compatíveis com o frontend existente. Nenhum dos gates verifica consistência
entre os nomes de rota do frontend e do backend.

---

## Lições para o Processo

1. **Contrato de API deve existir ANTES do frontend e do backend**: PRD §8.6 deve ser gerado
   no planning (sprint-02), não na retro (sprint-10). Sem ele, frontend e backend crescem
   independentes e convertem nomes de forma diferente.

2. **gate_integration**: Falta um gate que verifica se todas as chamadas de API do frontend
   (`fetch('/api/...')`) têm correspondência em rotas do backend. Pode ser implementado
   comparando imports/chamadas do `src/api/client.js` com o OpenAPI do backend.

3. **gate_mvp.frontend não verifica conformidade de nav**: Passou mesmo com Assistente
   no bottom nav (violando PRD §8.5). Falta validação de nav contract.

4. **Routing com URL change**: O coder deve usar `BrowserRouter` com paths reais para que
   deep links funcionem. Vite serve `index.html` em qualquer rota (configuração `historyApiFallback`)
   mas o app não configura as rotas no router.

---

## Comparação SM4 → SM5 → SM6

| Critério | SM4 | SM5 | SM6 |
|----------|-----|-----|-----|
| Backend HTTP | ❌ | ❌ | ✅ |
| Frontend renderiza | ✅ | ✅ | ✅ |
| API integrada (dados reais) | ❌ | ❌ | ❌ |
| Rotas com URL change | ❌ | — | ❌ |
| PRD reescrito no ciclo | ❌ | ❌ | ✅ |
| Processo concluiu sem blocker manual | ❌ | ❌ | ⚠️ (1 intervenção) |

---

## Nota por Dimensão

| Dimensão | Nota | Justificativa |
|----------|------|---------------|
| Backend HTTP | 9/10 | Existe, sobe, tem /health, routers registrados |
| Frontend UI | 7/10 | 5 telas, empty states, FABs — mas Catálogo ausente |
| Integração API | 2/10 | 3 de 4 chamadas principais em 404 |
| Navegação/Routing | 5/10 | Nav funcional por click, mas deep links não funcionam |
| Conformidade PRD | 5/10 | Assistente no nav e Catálogo ausente violam §8.5 |
| Processo Fast Track | 8/10 | Completou 38 steps, ft.prd.rewrite funcionou |

**Média: 6.0/10** (arredondado para 6.5 pelo progresso estrutural vs SM5)
