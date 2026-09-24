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

O comando imprime uma tabela Markdown com ordem, nota, decisão, faixa de EV
para triagem e próximo passo. A nota ajuda a ordenar candidatos, mas diferenças
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
- Financeiro: qualidade das demonstrações, ajustes e dívida líquida.
- Valuation: método, premissas, sensibilidade, faixa e riscos.
- Compradora: encaixe, sinergias líquidas, custos, capacidade de financiar e
  diferença entre teto econômico e proposta negociável.

O relatório usa a estrutura de escopo, bases, métodos, dados e documentação
das [International Valuation Standards](https://ivsc.org/standards/) como
referência. Os métodos para empresas privadas seguem as categorias de renda,
mercado e ativos descritas pelo [CFA Institute](https://www.cfainstitute.org/insights/professional-learning/refresher-readings/2026/private-company-valuation).
O template não afirma conformidade formal com IVS.
