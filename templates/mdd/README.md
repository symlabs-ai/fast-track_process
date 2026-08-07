# MDD — Desenvolvimento Orientado ao Mercado

Template independente de Market-Driven Development (MDD) para transformar uma
demanda em uma definição de produto aprovada e, somente depois, derivar a
narrativa executiva e a presença comercial dirigida ao usuário final.

```bash
ft run . --template mdd --request "Descrever o problema e o resultado esperado" --auto
ft approve --cycle <id>  # hipótese
ft continue --cycle <id> --auto
ft approve --cycle <id>  # visão
ft continue --cycle <id> --auto
ft approve --cycle <id>  # PRD
ft continue --cycle <id> --auto
ft approve --cycle <id>  # pacote executivo, deck e proposta de site
ft continue --cycle <id> --auto
ft approve "slides e protótipo visual aprovados" --cycle <id>
ft continue --cycle <id> --auto
ft close --cycle <id>
```

O template tem papel `neutral`: prepara conhecimento sem assumir ownership do
objetivo construtor. Depois do close, use `mvp-builder-fast` para implementar:

```bash
ft run . --template mvp-builder-fast --auto
```

Artefatos canônicos:

- `docs/demanda.md`;
- `docs/hipotese.md`;
- `docs/VISION.md`;
- `docs/PRD.md`;
- `docs/executive-summary.md`;
- `docs/pitch-deck.md`;
- `docs/site-proposal.md`;
- `docs/pitch-deck-images/`, com 12 PNGs — um por slide — e receipt;
- `docs/site-prototype/`, com um PNG vertical comprido da home e receipt;
- `docs/mdd-handoff.md`.

## Ordem do processo

1. A hipótese delimita o problema e os sinais falseáveis.
2. A visão define propósito, promessa, posicionamento e princípios.
3. O PRD converte a direção aprovada em comportamento verificável.
4. O sumário executivo condensa a definição aprovada.
5. O pitch deck transforma a definição em proposta de valor para stakeholders:
   problema, público, benefícios, solução, diferenciais, impacto e convite. Não
   funciona como relatório de progresso, backlog, blockers ou readiness.
6. A proposta de site traduz a narrativa em uma presença comercial moderna,
   objetiva e dirigida ao usuário final. A home apresenta o produto definido
   como oferta, sem linguagem de projeto, piloto, roadmap ou progresso.
7. O stakeholder aprova o pacote textual completo.
8. Dois nodes fixados em `gpt-5.6-sol` com esforço `max` e
   `codex_auth: chatgpt` usam a ferramenta built-in `image_gen`: o primeiro gera uma imagem PNG própria para cada um dos
   12 slides; o segundo gera um PNG vertical comprido do protótipo da home.
9. Receipts, hashes, dimensões e formatos passam por gate determinístico.
10. O stakeholder revisa e aprova os 13 bitmaps antes do handoff; rejeições
    reabrem somente as imagens citadas.

A proposta e o protótipo do site não são relatório de projeto. Eles priorizam
promessa, benefício, funcionamento, recursos, confiança e conversão. Download,
plataformas, preço ou outra chamada principal aparecem somente quando forem
coerentes com a visão e o Documento de Requisitos do Produto (Product
Requirements Document — PRD) aprovados. A aprovação visual não implementa nem
publica o site.

Slides e protótipos públicos passam também por auditoria de linguagem: toda
sigla técnica ou institucional é escrita por extenso na primeira ocorrência,
seguida da forma abreviada entre parênteses. Identificadores rastreáveis, como
`PB-013` e `FEAT-001`, permanecem exatos.
