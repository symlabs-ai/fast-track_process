# Company Valuation — avaliação de empresa para aquisição

Adapta as lentes de pesquisa do `innovation` para uma **empresa-alvo**. O
resultado durável é `docs/valuation-report.pdf` junto com
`docs/valuation-report.md`, um relatório para apoiar a
decisão sobre diligência e negociação. O template não decide comprar, não
autoriza oferta e não substitui avaliação profissional quando ela for exigida.

## Fluxo

```text
pedido + compradora → escopo → perfil da compradora → mercado → concorrência
       → dados financeiros do alvo → valor standalone → sinergias e teto
       → relatório → PDF diagramado → revisão humana → fim
```

O relatório apresenta tabelas sintéticas de tamanho de mercado, concorrentes,
tendências e perspectivas. O dossiê com claims, fontes e datas fica arquivado
no ciclo. Cada número relevante deve ser rastreável à fonte ou ao documento
financeiro e sua página. A data-base, moeda e participação são explícitas.
O PDF é renderizado por Chromium a partir do Markdown e dos YAMLs do ciclo.
Usa capa editorial, formas vetoriais suaves, gráficos de receita e valuation,
tabelas compactas e paleta sóbria. A validação confere estrutura e texto
extraído do PDF antes do gate humano.

O modelo aceita fluxo de caixa descontado, múltiplos comparáveis e ativos
líquidos ajustados quando aplicáveis. O validador recalcula o DCF e a ponte
`valor da operação − dívida líquida = valor do patrimônio`, confere o
múltiplo e exige que a faixa cubra os métodos usados. A escolha e a ponderação
dos métodos continuam sendo julgamentos que o revisor deve conferir. Se os
dados não sustentam um número, o relatório registra **valor não estimável com
os dados disponíveis** e lista a diligência necessária.
Mesmo assim, a seção de triagem traz uma decisão A/B/C, nota comparável,
faixa hipotética de EV para conversa e cenários de margem, preço e payback.
O sumário executivo também mostra, quando os dados permitem, o efeito
numérico para a compradora: preço pago, EBITDA alvo, payback, receita
incremental necessária pelo canal e valor líquido ilustrativo sob um múltiplo
explicitamente assumido. A quantidade de candidatos é variável e não faz
parte das premissas do processo.
As premissas de sensibilidade são identificadas como hipóteses, para permitir
escolher onde investir tempo antes de receber uma diligência completa.
Quando o teaser permite apenas uma estimativa preliminar do valor da operação
(EV), o relatório pode mostrar essa faixa, mas mantém o valor do patrimônio e
o preço da participação em aberto até conhecer dívida líquida e ajustes.

A análise da compradora avalia encaixe de produtos, clientes, canais e
capacidades. Sinergias quantificadas usam fluxo de caixa incremental,
probabilidade de realização e custos de integração. O relatório separa o
valor standalone do alvo, o teto econômico específico da compradora e o teto
financiável. Valuation da compradora não demonstra caixa disponível. Sem
dados financeiros do alvo ou sinergias defensáveis, o preço específico fica
não estimável.

## Uso

Prepare um projeto FT fora do repositório do engine e faça commit dos documentos
permitidos que o template deverá ler. A entrada pode ser uma URL de site, um
briefing em texto ou um PDF:

```bash
ft run . --template company-valuation --request "https://empresa.example" --auto
ft run . --template company-valuation --input briefing.md --auto
ft run . --template company-valuation --input apresentacao.pdf --auto
ft status --cycle <id>
```

O briefing pode seguir [`examples/valuation-request.md`](examples/valuation-request.md).
Um PDF passado em `--input` é copiado para `docs/valuation-source.pdf` no ciclo;
o pedido textual aponta para esse arquivo. Para site, use `--request` com URL.
Dados ausentes são registrados como lacunas. Cenários explícitos usam premissas
de sensibilidade e não são apresentados como fatos observados.

Para ordenar vários teasers após concluir um ciclo por alvo, compare as notas
registradas nos respectivos `buyer-case.yml`:

```bash
python templates/company-valuation/scripts/rank_candidates.py \
  /caminho/alvo-a/docs/buyer-case.yml /caminho/alvo-b/docs/buyer-case.yml
```

O comando valida a régua completa e imprime uma tabela Markdown com classe,
nota, decisão, faixa de EV, próximo passo e arquivo de origem. A moeda e a
escala aparecem em cada linha, sem câmbio implícito. Com vários casos, informa
o total recebido e a ordem por nota; com um único caso, omite ordem e universo.
A nota ajuda a ordenar candidatos, mas diferenças
pequenas devem ser revistas à luz da qualidade das fontes e da tese estratégica.

## Roteamento de modelos

O template fixa `gpt-6-astra/max` nos nodes de pesquisa da compradora,
mercado e concorrentes, conforme o pedido para este teste. O intake usa
`gpt-6-astra/medium`; análise financeira, modelo, caso da compradora e escrita
do relatório usam `gpt-6-astra/high` porque tratam de dados financeiros e
decisão de aquisição. Gates, cálculos e PDF rodam em Python. O probe
`ft llm-capabilities --json` deve confirmar cada combinação no projeto antes
do ciclo. Retry preserva a rota; qualquer mudança fica para um node novo com
evidência de truncamento ou validação incompleta. Compare duração, tokens,
retries e aprovações com `ft status --report` e `ft runs --done-detailed`,
conforme [`docs/ft_model_orchestration.md`](../../docs/ft_model_orchestration.md).

Identifique a compradora no briefing de entrada ou em
`docs/buyer-briefing.yml` já commitado no projeto. O arquivo
[`examples/buyer-tecnospeed.yml`](examples/buyer-tecnospeed.yml) mostra um
exemplo para copiar e adaptar. Seus indicadores de 2026 são dados fornecidos
pelo stakeholder e não foram verificados no site público.

O fluxo para no gate de revisão do relatório. Uma aprovação nesse gate permite
encerrar o ciclo com `ft close --cycle <id>`. O relatório é canônico; o escopo,
pesquisas, insumos e modelo ficam em `.ft/cycles/<cycle-id>/` após o close.
Não coloque dados confidenciais ou credenciais no pedido ou em documentos
versionados. Fontes internas devem ser acessíveis por paths repo-locais seguros
ou resumidas em documentos autorizados para arquivamento.

## Critérios da revisão humana

- Escopo: empresa, participação, data-base, moeda, jurisdição e base de valor.
- Mercado: definição da categoria, geografia, ano, tamanho e crescimento.
- Concorrência: comparabilidade real e origem de qualquer múltiplo.
- Primeira página após a capa: decisão, classe/nota, motivo, próximo passo,
  condições de mudança e resultados aplicáveis legíveis, antes dos gráficos.
  A conferência textual por página não substitui a revisão visual.
- Financeiro: qualidade das demonstrações, ajustes e dívida líquida.
- Valuation: método, premissas, sensibilidade, faixa e riscos.
- Compradora: encaixe, sinergias líquidas, custos, capacidade de financiar e
  diferença entre teto econômico e proposta negociável.

O relatório usa a estrutura de escopo, bases, métodos, dados e documentação
das [International Valuation Standards](https://ivsc.org/standards/) como
referência. Os métodos para empresas privadas seguem as categorias de renda,
mercado e ativos descritas pelo [CFA Institute](https://www.cfainstitute.org/insights/professional-learning/refresher-readings/2026/private-company-valuation).
O template não afirma conformidade formal com IVS.

## Contrato 0.5.0 e migração

`buyer-profile.yml`, `buyer-case.yml` e `competitors-evidence.yml` passam a
`schema_version: 2`. Os demais YAMLs continuam na versão 1. IDs, sequência dos
nodes, pesos da triagem, gates e política de execução foram preservados.
Artefatos antigos são recusados com indicação de versão/campos ausentes;
não há migração automática de notas ou reescrita de ciclos encerrados.

Para usar uma entrada antiga em um novo ciclo, preencha os contratos abaixo
nas cópias de trabalho, com evidência ou lacuna explícita. Extraia a letra de
`priority_band`, transfira a explicação para `decision_rationale` e justifique
cada nota. Substitua paybacks isolados e campos soltos de margem pela matriz
`scenario_analysis`/`scenarios`. Não basta trocar `schema_version`.

### Régua e decisão individual

`screening.scoring` exige uma ocorrência de cada critério:

| criterion | weight |
| --- | ---: |
| encaixe_com_a_compradora | 25 |
| crescimento_recente | 15 |
| margem_e_estabilidade | 20 |
| sinais_de_produto_e_clientes | 15 |
| potencial_de_sinergia | 15 |
| risco_de_execucao | 10 |

Cada entrada traz `grade_0_to_5`, `weighted_points = weight * grade_0_to_5 / 5`
e `rationale`. A soma é `priority_score`; `priority_band` contém somente A
(>=70), B (>=50 e <70) ou C (<50). A correspondência padrão de `decision`
é A=`priority`, B=`selective_shortlist`, C=`pass`. Uma divergência exige
`decision_override_reason`, sem alterar a nota silenciosamente.
`decision_rationale`, `near_term_action` e `decision_change_conditions`
(lista não vazia) sustentam a decisão. Não afirmam posição numa carteira
desconhecida. Faixa hipotética: `screening_ev_low/high`,
`preferred_ev_ceiling` (opcional dentro da faixa), `value_basis`. Sem base,
os números ficam null e `ev_unavailable_reason` explica a ausência.

### Bases, canal e escala

Moeda vem de `currency`; `unit` aceita `units`, `thousands`, `millions`
(fatores 1, 1000, 1000000). Cenários declaram sua moeda/unidade, iguais ao
caso; todos os seus insumos/resultados monetários usam essa base, salvo
`customer_arpa`, que declara sua própria escala e a mesma moeda. Não há câmbio.
Unidade não suportada deve ser normalizada com fonte ou registrada como
lacuna em cenário `not_calculable`, sem produzir resultados numéricos.

`buyer-profile.channel_base` substitui campos setoriais como
`active_software_house_base`; `strategic_value_case.channel_base` substitui
`software_houses_available` e deve reproduzir a base do perfil:

```yaml
channel_base:
  status: available
  size: 500
  population_definition: membros elegíveis do canal informado na entrada
  population_kind: active  # active ou published; publicado não implica ativo
  as_of: '2026-01-01'
  source_reference: https://sources.example/channel
  source_kind: stakeholder  # stakeholder, document ou public
  verification: unverified
```

Este exemplo é sintético, sem valor padrão. Na ausência de base, use
`status: unavailable`, `size: null`, `gaps: ["denominador não informado"]`.
Contagens são membros, taxas são frações e datas identificam a população.
Dados de stakeholder permanecem `unverified` até confirmação documental.

### Matriz de margem, preço e retorno

`screening.scenario_analysis` exige `status`. Para `not_calculable` ou
`not_applicable`, forneça `rationale` e `gaps` não vazios, `scenarios: []` e
`summary_scenario_ids: []`. Isso permite triagem qualitativa e justificativas
de inaplicabilidade mesmo havendo dados parciais; o revisor avalia o motivo.

Para `calculated`, declare `currency`, `unit`, `financial_year`, `revenue`,
`baseline_ebitda`, `source_reference`, `assumptions`, `limitations`,
`horizon_years`, `margin_hypotheses` e `price_hypotheses`. As bases conciliam
com o período de `financial-inputs.yml`. Inclua a margem atual e ao menos duas
hipóteses distintas de margem (intermediária e meta), escolhidas para o caso,
e 2–3 preços hipotéticos distintos. Receita permanece constante nesta
sensibilidade; crescimento incremental pertence ao cenário de canal.

`screening.scenarios` cobre todos os pares preço × margem, sem duplicatas.
Cada item tem `id: SC-01...`, `price`, `margin`, `annual_result`,
`required_annual_improvement`, `ramp_rationale`, `ramp` e `returns`:

- `annual_result = revenue * margin`; melhoria = resultado − EBITDA base.
- `ramp` lista cada ano `year: 1..horizon_years`, sua `margin` e
  `annual_result = revenue * margin`; a última margem atinge a do cenário.
- `returns.basis: ebitda_proxy`,
  `returns.formula: undiscounted_cumulative_annual_result` e `limitations`
  explicitam que EBITDA é proxy, sem desconto, com custos e exclusões descritos.
- `returns.without_ramp` e `returns.with_ramp` trazem `status` e `years`.
  Sem rampa usa-se o resultado estabilizado em todos os anos; com rampa,
  acumulam-se os resultados anuais declarados, incluindo negativos.
- Ao recuperar o preço: `status: recovered`, anos = anos completos anteriores
  + (preço − acumulado anterior) / resultado do ano da recuperação. A parcela
  anual supõe distribuição uniforme do resultado, sem equivalência a caixa.
- Sem recuperação no horizonte: `years: null`; status `non_positive_result`
  se o resultado final for <=0, ou `beyond_horizon` se for positivo.

`summary_scenario_ids` seleciona 2–3 IDs existentes com contraste de preços
e margens. O relatório copia os resultados validados; não cria outra conta.
Custos, impostos, investimentos, capital de giro e tempo da rampa devem ser
descritos como premissas/exclusões, sem confundir proxy com fluxo de caixa.

### Canal e valor incremental condicionais

`screening.strategic_value_case` exige `status: calculated|not_calculable|not_applicable`.
Sem cálculo, registre `gaps` e mantenha resultados null. Com cálculo, forneça
`currency`, `unit`, `assumptions`, `limitations`, `channel_base`,
`customers_per_converted_member` e `customer_arpa` (`value`, `currency`,
`unit`, `period: year`, `source_reference`). Este último substitui o antigo
`implied_target_customer_arpa_year`, cuja escala não era declarada.

Insumos restantes: `acquisition_ev_assumed`, `working_revenue_multiple`,
`target_revenue_base`, `target_baseline_growth`, `channel_extra_growth_pp` e
`target_margin_assumed`. As contas verificadas são:

- `next_year_growth_total = target_baseline_growth + channel_extra_growth_pp`;
  `next_year_revenue = target_revenue_base * (1 + next_year_growth_total)`;
  `next_year_ebitda = next_year_revenue * target_margin_assumed`.
- `channel_extra_revenue = target_revenue_base * channel_extra_growth_pp`;
  `equivalent_new_customers = channel_extra_revenue * fator_da_receita /
  (customer_arpa.value * fator_do_ARPA)`;
  `implied_partner_conversion = equivalent_new_customers /
  (channel_base.size * customers_per_converted_member)`.
- `target_value_at_base_revenue` e `target_value_at_next_year_revenue` são
  cada receita × múltiplo hipotético; os respectivos
  `value_created_net_of_purchase_at_base_revenue` e
  `value_created_net_of_purchase_at_next_year_revenue` deduzem o preço.
- `payback_on_next_year_ebitda_years = acquisition_ev_assumed / next_year_ebitda`,
  com `payback_status: calculated`; se EBITDA <=0, anos null e
  `payback_status: non_positive_result`. É quociente estabilizado, sem rampa;
  o retorno com cronograma está na matriz, não neste quociente.

Denominadores ausentes ou não positivos exigem lacuna, sem divisão inválida.
Conversão acima de 100% sinaliza hipótese inviável a discutir, não é truncada.
Valor ilustrativo não prova múltiplo transferível, ganho capturado, caixa ou
capacidade de financiamento; as regras de valuation formal continuam válidas.

### Comparações rastreáveis

`competitors-evidence.yml` mantém `claims` com IDs EV-C*. Acrescenta
`comparisons`, sem quantidade fixa: `id: CMP-01...`, `name`,
`category: direct|adjacent|manual|do_nothing|transaction`,
`scale_class: similar|larger|smaller|unknown|not_applicable` e
`comparability_rationale`. Dimensões obrigatórias: `solution`, `scale`,
`public_price`, `history`, `differences`. Cada uma declara
`status: available|unavailable`, `value` e `evidence_ids` existentes.

Dado ausente exige `value: null`, `limitation` e claims das fontes consultadas.
`scale` disponível exige `metric` e `period`; sem porte, a classe é `unknown`
ou `not_applicable`. `public_price` disponível exige `kind: commercial` e
`basis` (moeda, período e objeto do preço). Dimensões opcionais
`transaction_price`/`valuation_multiple` usam a mesma estrutura com
`kind: transaction`/`valuation_multiple` e base própria; não são substitutas
do preço comercial. A equivalência econômica permanece sujeita à revisão.
Sem comparáveis, use `comparisons: []` e `comparison_gap` com `limitation`
e `evidence_ids`. Ausência de pares semelhantes não impede alternativas maiores
ou menores, desde que identificadas e justificadas.

### Sumário e PDF

Após validar `buyer_case` e `competitors`, execute:

```bash
python .ft/process/company-valuation/scripts/validate_valuation.py report_blocks
```

O comando imprime o Sumário Executivo e a tabela de Concorrentes de Porte
Similar derivados do YAML, sem escrever arquivos. Copie esses blocos para as
seções correspondentes do relatório. O sumário contém somente a síntese
canônica; detalhes pertencem às outras seções. Mantenha motivos, ações e
limitações breves no node que os produz. A comparação preserva dimensões,
IDs, referências e classe de porte; pode vir acompanhada de explicação.

O validador rejeita sumário vazio, decisão só no corpo, divergências na nota
ou omissão de cenários. O renderizador coloca o sumário imediatamente após
a capa e os gráficos depois dele. O check do PDF exige os campos e resultados
na segunda página, inclusive com retorno não calculável. Transbordamento
reprova o check; a correção deve respeitar o write_scope de cada node.
A legibilidade permanece no gate humano existente, sem aprovação adicional.

Testes sintéticos do contrato: `python -B -m unittest discover -s
targets/global/company-valuation/tests` no staging. Os testes usam diretórios
temporários dentro de `report/valuation-tests` (ou `VALUATION_TEST_ROOT`).
