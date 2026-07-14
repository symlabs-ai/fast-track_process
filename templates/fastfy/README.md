# Fastfy — Adoção de Repositório Legado

Transforma um repositório que ainda não é Fast Track em um projeto FT
completo. Em um único ciclo, o processo:

1. **Analisa** o código, o histórico Git e a documentação existente;
2. **Pergunta** ao stakeholder somente o que muda a documentação ou o harness;
3. **Constrói** a documentação canônica — `docs/PRD.md` (as-is),
   `docs/TECH_STACK.md`, `docs/PROJECT_BACKLOG.md`, `docs/FEATURES.md` e
   `CHANGELOG.md` reconstruído a partir de tags/`git log`;
4. **Cria o harness** de validação: `Makefile` com targets `build`/`test`/`run`
   (e `url` quando há interface) delegando aos comandos reais do legado, mais
   um smoke test mínimo quando o repositório não tem testes;
5. **Valida** com baseline determinística (build + test) e revisão
   independente antes do aceite do stakeholder.

Ao final, o projeto passa no preflight do template `feature` e evolui pelo
fluxo incremental normal.

## Uso

```bash
cd repo-legado/
ft init .                    # reaproveita o Git existente (exige checkout limpo)
# ft init . --adopt          # legado sem Git ou com arquivos não commitados
ft run . --template fastfy
# ... responder perguntas / aprovar plano / aceitar adoção ...
ft close --merge full
ft run . --template feature  # próximo passo: evolução incremental
```

## Convenções adotadas

- **Produto na raiz**: `product_root: .` é suportado — o `Makefile` do harness
  pode viver na raiz do repositório (legados raramente seguem `project//src/`).
- **Backlog**: capacidades já entregues viram `PB-*` com status `done`
  (origem `adoption`) cobertas por `FEAT-*`; dívidas viram `planned` P2 ou
  `deferred` com decisão preenchida — nenhum P0/P1 fica aberto sem decisão.
- **CHANGELOG**: seções reconstruídas do histórico Git, encerradas por uma
  entrada `#FEAT` referenciando o PB da adoção.
- **Nada inventado**: toda FEAT-* precisa de evidência real (arquivos, rotas,
  testes); o que for incerto vira dívida, não feature. A revisão independente
  rejeita documentação sem lastro no código.

## Fluxo

```
preflight → survey ⇄ questions → scope_gate → docs → harness → baseline
  → review → acceptance → final_gate → end (ft close --merge full)
```

A única alteração de código feita pela adoção é o harness (Makefile + smoke
test opcional); todo o resto é documentação.
