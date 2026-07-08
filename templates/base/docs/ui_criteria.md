# Critérios Visuais de UI

> Template genérico. Ajuste os textos para o domínio do projeto antes de rodar o ciclo quando houver requisitos específicos no PRD. Mantenha IDs estáveis (`C01`, `C02`, ...) porque o engine usa esses IDs para validar evidência em relatório visual ou código.
>
> Evidência aceita:
> - Relatório visual com linha por critério e resultado `PASS`.
> - Código marcado com `data-ui-criteria="C01"` ou comentário `ui-criteria: C01`.

## Telas P0
- [ ] C01: A tela inicial ou dashboard apresenta o estado principal do produto, com título claro, informação prioritária e caminho para as ações P0.
- [ ] C02: Cada tela P0 descrita no PRD possui rota ou navegação acessível, título identificável e conteúdo suficiente para validar sua finalidade.
- [ ] C03: Telas de listagem, consulta ou seleção exibem dados reais/semente, estado vazio quando aplicável e ação primária visível quando o PRD exigir criação ou alteração.
- [ ] C04: Fluxos de criação, edição ou envio definidos no PRD usam tela, modal ou etapa dedicada, sem misturar formulário complexo com listagem de forma confusa.
- [ ] C05: Telas de detalhe, status ou confirmação definidas no PRD exibem informações críticas, feedback de resultado e ação de retorno ou continuidade.

## Estados e Fluxos
- [ ] C06: Estado carregado exibe dados realistas o suficiente para validar hierarquia, espaçamento, formatos e legibilidade.
- [ ] C07: Após submit de criação, edição ou envio, quando aplicável, a UI mostra feedback claro e atualiza a lista, detalhe ou resumo correspondente.
- [ ] C08: Erros de validação, falha de rede ou entrada inválida não quebram a navegação e mostram mensagem acionável perto do contexto correto.

## Responsividade e Navegação
- [ ] C09: Layout principal funciona em viewport mobile de 390x844 sem overflow horizontal, cortes de texto críticos ou controles inacessíveis.
- [ ] C10: Navegação principal permanece visível ou facilmente acessível nas telas P0, com estado ativo distinguível.
- [ ] C11: Controles de formulário têm labels associados, ordem de foco previsível e botão de submit explícito.

## Componentes e Acabamento
- [ ] C12: Componentes específicos pedidos no PRD, como menu suspenso, modal, tabs, tooltip, gráfico, upload, calendário ou ação flutuante, estão presentes e interativos quando aplicáveis.
- [ ] C13: Ícones, estados vazios, mensagens e botões usam linguagem consistente com o produto, sem placeholders, lorem ipsum, emojis substituindo ícones funcionais ou textos quebrados.

## Evidência Obrigatória
- [ ] C14: Há evidência de cada tela P0 por screenshot real ou marcação explícita no código com o ID do critério.
- [ ] C15: Há evidência dos fluxos interativos P0 após ação do usuário, especialmente criação, edição, filtro, navegação, erro e confirmação quando aplicáveis.
