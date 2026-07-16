# Feedback do template `feature` v1.3 — fricções observadas (ft_ui, 2026-07-15/16)

Orquestrei ~6 ciclos `feature`/`tweak` no ft_ui com a engine 0.15 / template
v1.3 (Opus e Sonnet @ high). O processo entregou tudo com qualidade, mas
estas fricções recorrentes exigiram intervenção manual em quase todo ciclo.
Cada uma é um ponto de melhoria concreto no template/engine.

## 1. `feature.implement` termina sem alterar `src/` (recorrente, ~3/4 ciclos)

**Sintoma:** a delegação de implement conclui, mas `feature.product_validate`
bloqueia com `implementação não alterou nenhum arquivo em src/`. Recuperável
com `ft fix "<instrução>" --auto` (o fix implementa de fato), mas custa uma
rodada inteira e a instrução manual.

**Hipótese:** o prompt do `implement` não está forçando a escrita, ou o
budget/turns se esgota na exploração antes de escrever. Vale (a) endurecer o
prompt do node para "produza o diff agora", ou (b) detectar delta-vazio e
auto-retry com a instrução reforçada, em vez de bloquear.

## 2. Teto de 300s no gate de validação estoura com suíte grande

**Sintoma:** `product.sh ensure --record` excede 300s quando a suíte passa de
~300 testes (E2E Chromium reais incluídos). O ciclo bloqueia por timeout, não
por falha. Recuperação: rodar `product.sh full --record` fora do gate e
`ft retry`.

**Sugestão:** o item que ficou pendente na retro anterior (suíte incremental
nos gates intermediários — focais+build no product_validate, suíte completa só
no final_gate) resolve isso e o custo de wall-clock ao mesmo tempo. Alternativa
mínima: tornar o teto configurável por `environment.yml`.

## 3. Receipt sensível a mudança pós-geração

**Sintoma:** `feature.final_gate` reprova com `receipt não corresponde ao
estado atual do produto` sempre que qualquer arquivo muda entre a geração do
receipt e o gate (ex.: reconciliar um teste depois). Correto em espírito, mas
força regenerar o receipt a cada micro-ajuste.

## 4. `implementation` valida `git status --porcelain` (não-commitado)

**Sintoma:** se o orquestrador **commita** o trabalho do ciclo (boa higiene
git durante fixes), o worktree fica limpo e o validador conclui "src/ não
mudou" — mesmo com o trabalho todo presente nos commits. Tive que `git reset
--soft` para devolver as mudanças ao working tree.

**Sugestão:** comparar contra `base_commit` (registrado no state) em vez de
`git status`, cobrindo tanto commitado quanto pendente.

## 5. Regra "exatamente um PB" conflita com menções de contexto

**Sintoma:** uma demanda que legitimamente **referencia** outro PB como
contexto (ex.: "PB-031 usa a base do PB-030") é rejeitada no preflight
(`deve referenciar exatamente um PB-* preexistente`). Tive que reescrever a
demanda removendo a menção.

**Sugestão:** distinguir o "PB canônico do ciclo" (o `backlog_item` do
frontmatter) das menções no corpo — validar só o primeiro.

## 6. `ft run --template` recusa registro migrado v3 com `entrypoint`/marker legado

**Sintoma:** pós-migração v3 do workspace, `ft run . --template feature` e
`--template tweak` falhavam com `TemplateCatalogError: entrypoint do YAML não
corresponde ao registro` / `v2_run_compatibility incompatível`. O manifest
tinha `entrypoint: feature` + marker v2 enquanto o bundle v1.3 usa
`entrypoint: run`. Corrigi manualmente o registro para `entrypoint: run` sem
marker. Um passo de migração automática (ou uma msg de erro acionável com o
fix) evitaria o tropeço.

## Nota de ambiente (não é do template, mas mordeu o CI)

O venv do staging tem FastAPI/Starlette mais novos que o dev; testes que
introspectavam `app.routes` (buscando `.path`/`.endpoint`) quebraram no host
porque o FastAPI novo embrulha `include_router` em containers opacos. Migrei
os testes do ft_ui para prova por comportamento (a rota responde via
TestClient). Menciono aqui porque outros projetos sob o mesmo processo podem
ter o mesmo padrão frágil.
