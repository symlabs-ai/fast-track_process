# Plano de Voo — ServiceMate 7

> Criado em: 2026-04-03
> Sessão de origem: fast-track (main, v0.8.9)
> Próxima sessão: ServiceMate 7 — teste end-to-end do processo v0.8.9

---

## 1. O que mudou no processo desde o SM6

| Mudança | Impacto esperado no SM7 |
|---------|------------------------|
| `ft.plan.04.test_data` — massa de dados gerada no planning | Coder tem dados realistas para testes de integração e acceptance antes de qualquer implementação |
| `ft.plan.05.api_contract` — contrato de API gerado ANTES do código | Frontend e backend usam o mesmo `api_contract.md` como fonte de verdade → sem mismatch de rotas |
| `ft.acceptance.01.cli` + `gate.acceptance.cli` — acceptance via HTTP antes do Smoke | API testada com `test_data.md` em chamadas HTTP reais → integração validada antes do gate final |
| `gate_kb_review` KB-P4 — detecta frontend sem BrowserRouter/Route path | Deep links quebrados (SM6 P3) bloqueados em `gate.kb_review` |
| `gate_kb_review` KB-P5 — detecta `prd_review REJECTED` sem correção | Nav contract violada (SM6 P1) bloqueada em `gate.kb_review` |
| `ft.handoff.02.plano_voo` — plano_de_voo automático com veredicto ITERATE/RELEASE_CANDIDATE | Contexto do próximo ciclo gerado automaticamente; critérios explícitos para RELEASE_CANDIDATE |
| `setup_env --from-project` — copia `plano_de_voo.md` do projeto anterior | Sessão de SM7 começa com contexto do SM6 automaticamente disponível |

**Meta SM7: 8+/10 com zero intervenções manuais. Foco: eliminar API mismatch e deep links.**

---

## 2. O que SM6 falhou (e por quê SM7 deve acertar)

| Falha SM6 | Causa Raiz | Correção no v0.8.9 |
|-----------|-----------|-------------------|
| API mismatch: frontend `/api/clients` ≠ backend `/clientes` | Sem contrato de API antes do código; cada executor nomeou rotas independentemente | `ft.plan.05.api_contract` gerado na fase Planning — ambos leem o mesmo arquivo |
| Deep links quebrados (routing por estado interno) | Sem BrowserRouter no frontend | KB-P4 no `gate.kb_review` bloqueia se não encontrar `BrowserRouter` / `Route path=` |
| Assistente no bottom nav (viola PRD §8.5) | `ft.frontend.03.prd_review` retornou REJECTED(forced) — ciclo avançou sem corrigir | KB-P5 no `gate.kb_review` bloqueia se `prd_review` tiver veredicto REJECTED |
| Sem acceptance CLI | `ft.acceptance.01.cli` não existia — aceitação era só pelo Smoke | Novo gate formal com `gate_acceptance_cli` valida relatório antes do Smoke |
| Plano de voo manual e incompleto | `ft.handoff.02.plano_voo` não existia — dependia do avaliador externo | LLM coach gera automaticamente com veredicto ITERATE/RELEASE_CANDIDATE |

---

## 3. Criar o projeto SM7

### 3.1 Criar diretório e copiar PRD do SM6

```bash
mkdir -p ~/dev/projects/examples/service_mate_7
cp ~/dev/projects/examples/service_mate_6/project/docs/PRD.md \
   ~/dev/projects/examples/service_mate_7/PRD.md
```

> O PRD v2.0 do SM6 já tem §8.5 (nav contract) e §8.6 (HTTP integration contract).
> O processo v0.8.9 vai gerar `api_contract.md` durante o planning com base nele.
> **Não adicionar rotas manualmente ao PRD** — deixar o `ft.plan.05.api_contract` fazer isso.

### 3.2 Inicializar o engine

```bash
cd ~/dev/projects/examples/service_mate_7
python -m ft.cli.main \
  -p ~/dev/research/fast-track/process/fast_track/FAST_TRACK_PROCESS_V2.yml \
  init
```

Confirmar: **39 nodes** (34 do SM6 + 5 novos: test_data, api_contract, acceptance.01.cli, gate.acceptance.cli, handoff.02.plano_voo).

### 3.3 Rodar em modo MVP

```bash
cd ~/dev/projects/examples/service_mate_7
PYTHONUNBUFFERED=1 nohup python -m ft.cli.main \
  -p ~/dev/research/fast-track/process/fast_track/FAST_TRACK_PROCESS_V2.yml \
  continue --mvp > /tmp/sm7_run.log 2>&1 &
echo "PID: $!"
```

Monitorar:
```bash
tail -f /tmp/sm7_run.log
```

---

## 4. Gates críticos para acompanhar

| Gate | O que valida | Bug SM6 que cobre |
|------|-------------|-------------------|
| `gate.planning` | TASK_LIST + tech_stack + diagrams + test_data + api_contract | — |
| `ft.plan.05.api_contract` | Contrato de API gerado antes do código | SM6: mismatch de rotas |
| `ft.acceptance.01.cli` | HTTP requests com test_data → backend real | SM6: sem acceptance CLI |
| `gate.acceptance.cli` | Relatório existe e sem FAILs | SM6: sem acceptance gate |
| `gate.kb_review` KB-P4 | BrowserRouter/Route path presente | SM6: deep links quebrados |
| `gate.kb_review` KB-P5 | prd_review não está REJECTED | SM6: Assistente no nav |

**Se `ft.plan.05.api_contract` gerar `api_contract.md` correto**: o `ft.frontend.02.implement` e `ft.delivery.00.entrypoint` devem usar EXATAMENTE os mesmos paths. Qualquer divergência = BLOCK no acceptance CLI.

---

## 5. O que esperar do SM7

### Deve funcionar (corrigido pelo processo v0.8.9)

- ✅ `api_contract.md` gerado no planning com rotas canônicas (ex: `/clientes`, `/servicos`, `/cobranças`, `/agendamentos`)
- ✅ Frontend usa exatamente os paths do `api_contract.md` (prompt do `ft.frontend.02.implement` lê o arquivo)
- ✅ Backend usa exatamente os paths do `api_contract.md` (prompt do `ft.delivery.00.entrypoint` lê o arquivo)
- ✅ `gate_acceptance_cli` bloqueia se acceptance-cli-report.md não existe ou tem FAILs
- ✅ KB-P4 bloqueia se frontend não usa BrowserRouter — deep links devem funcionar
- ✅ `plano_de_voo.md` auto-gerado com veredicto ITERATE ou RELEASE_CANDIDATE

### Ainda pode falhar (não coberto ainda)

- ⚠️ Conformidade de nav contract (5 itens no bottom nav, FAB assistente) — KB-P5 só bloqueia se prd_review for REJECTED, mas depende do LLM ter passado pelo gate
- ⚠️ Proxy Vite `/api → localhost:8000` — DT-01 herdado do SM6; nenhum gate verifica isso
- ⚠️ US-08 (comandos de voz) — P1, provavelmente não entra no MVP
- ⚠️ ForgeBase Pulse — dependência `forge_base` pode não ser instalada corretamente

---

## 6. Avaliação de referência

| Iteração | Nota | Principal problema |
|----------|------|--------------------|
| service_mate_4 (v0.8.0) | 7/10 | Frontend ok, 4 bugs UX manuais |
| service_mate_5 (v0.8.2) | 6/10 | Sem HTTP server, interface_type errado |
| service_mate_6 (v0.8.3/0.8.9) | 6.5/10 | Backend ok, API mismatch, deep links quebrados |
| **service_mate_7 (v0.8.9)** | **meta: 8+/10** | api_contract + acceptance CLI + KB-P4/P5 |

---

## 7. Artefatos esperados ao final

| Artefato | Path | Status esperado |
|---------|------|-----------------|
| test_data.md | project/docs/test_data.md | ✅ gerado em ft.plan.04 |
| api_contract.md | project/docs/api_contract.md | ✅ gerado em ft.plan.05 |
| acceptance-cli-report.md | project/docs/acceptance-cli-report.md | ✅ gerado em ft.acceptance.01.cli |
| smoke-report.md | project/docs/smoke-report.md | ✅ gerado em ft.smoke.01 |
| plano_de_voo.md | project/docs/plano_de_voo.md | ✅ auto-gerado em ft.handoff.02 |
| SPEC.md | project/docs/SPEC.md | ✅ gerado em ft.handoff.01 |
