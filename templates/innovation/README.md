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

Após um GO, o projeto continua deliberadamente em `building` e pode aparecer
como `BLOCKED` em `ft project-status`: innovation não cria
`docs/PROJECT_BACKLOG.md`, não reconcilia `.ft/project.yml` e não seleciona a
matriz de validação. Essas são responsabilidades do builder indicado no
handoff, antes da construção. O handoff também distingue o GO de uma autorização
explícita para iniciar implementação.

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
| handoff | boundary de delivery ausente, autorização ambígua ou path de pesquisa que ficará inválido após o close |
| post_mortem | causa da morte sem citar evidência real |

O gate não julga se a pesquisa é *verdadeira* — julga se é *auditável*: todo
claim tem fonte consultável. A qualidade semântica é desafiada pelo node de
validação e, em última instância, pelo go/no-go humano.

## Sondas empíricas (fire proof)

A lente de viabilidade não se limita a desk research: quando uma hipótese
declara teste empírico ou um claim decisivo é verificável com HTTP GET
read-only, o node executa a sonda (máx. 3 requisições educadas por alvo, sem
auth, sem contornar captcha), salva a resposta bruta em
`docs/research/probes/` e registra o claim com `method: probe` e
confidence high. Na validação, probe prevalece sobre desk research quando
ambos falam da mesma rota. Motivação: no caso Clari, desk research refutou
um mecanismo (captcha na SEFAZ) que uma única requisição real provou aberto —
sonda vale mais que dez claims lidos.

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
- O dossiê é cycle-local: durante a execução está em `docs/research/`; após
  `ft close`, o path durável é `.ft/cycles/<cycle-id>/research/`. O handoff não
  pode anunciar o path transitório como fonte permanente.
- Sem limite automático de rodadas research↔questions; o gate humano é o
  próprio freio. Em `--auto`, um verdict inconclusive para no gate.
- Com `--bypass-human-gates`, o gate de perguntas não é pulado às cegas: o
  LLM responde o questionário (pesquisando quando possível), com cada
  resposta atribuída como `**[LLM Responde / <Engine Modelo>]**` e premissas
  conservadoras declaradas para dado interno indisponível. O go/no-go
  bypassado segue direto para o PRD — a decisão de investimento continua
  sendo recomendável como gate humano.
