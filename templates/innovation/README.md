# Innovation — Discovery, Validação e Business Case Automatizados

Cobre a fase **pré-delivery** do funil de inovação:

```
Demanda bruta → Intake → Pesquisa (3 lentes, web) → Validação → Business Case
                                                        │             │
                                                   refuted        go_nogo (humano)
                                                        │        go │      │ no-go
                                                        ▼           ▼      ▼
                                                  Post-Mortem     PRD   Post-Mortem
                                                        │           │       │
                                                        └────► innovation.end ◄┘
                                                     (no-go: post-mortem.md │ go: handoff.md
                                                                            → mvp-builder-fast
                                                                              ou feature-fast)
```

O processo **não implementa nada**: termina onde `mvp-builder-fast` (produto
novo) ou `feature-fast` (evolução) começam. A saída go é `docs/PRD.md` +
`docs/handoff.md`; a saída no-go é `docs/post-mortem.md` — ambos são fins
legítimos. Matar uma ideia barata, com dossiê, é entrega de valor.

## Uso

```bash
ft run . --template innovation
```

A demanda bruta vai em `docs/demanda.md` (ver `examples/demanda.md`). Pode
misturar produto e processo — o intake classifica e separa.

## Garantias determinísticas (scripts/validate_innovation.py)

| Stage | O que bloqueia |
|---|---|
| intake | idea.md sem `kind`, hipóteses sem padrão `## H-NN — afirmação` |
| research | claim sem URL/data/confiança, `supports` órfão, hipótese sem nenhum claim |
| validation | H-* sem verdict, verdict sem EV-* citado, EV-* inexistente |
| business_case | SC-* sem Métrica/Alvo/Prazo, frontmatter incompleto |
| prd | SC-* do business case sem AC-* na tabela de Rastreabilidade |
| post_mortem | causa da morte sem citar evidência real |

O gate não julga se a pesquisa é *verdadeira* — julga se é *auditável*: todo
claim tem fonte consultável. A qualidade semântica é desafiada pelo node de
validação e, em última instância, pelo go/no-go humano.

## Gates humanos

- `innovation.questions` (condicional): só dispara quando o verdict é
  `inconclusive` — perguntas sobre dados que a web não tem (dado interno,
  disposição a pagar, restrição). Respostas voltam à pesquisa, que preenche
  apenas as lacunas.
- `innovation.go_nogo` (obrigatório): decisão de investimento. É o único ponto
  em que o processo para sempre — automação honesta não decide alocação de
  capital.

## Limitações conhecidas (v0.1)

- Somente executor `claude` (os nodes de pesquisa dependem de web search).
- Pesquisa tem validade: `research_date` fica nos evidence.yml e o handoff
  alerta re-checagem se o delivery começar >8 semanas depois.
- Sem limite automático de rodadas research↔questions; o gate humano é o
  próprio freio. Em `--auto`, um verdict inconclusive para no gate.
