# Feature Fast — Diagrama de Fluxo

Fluxo do processo definido em [`process.yml`](./process.yml)
(`id: feature_fast`, versão `2.0.0`). O template executa um ciclo independente
por demanda, precedido por um plano interno consultivo. O grafo e seus gates
continuam autoritativos.

## Legenda

| Forma | Tipo de node |
|---|---|
| Hexágono | `gate` determinístico |
| Retângulo | node LLM focal |
| Losango | `decision` determinístico |
| Paralelogramo | `human_gate` |
| Estádio | início/fim |

## Fluxo

```mermaid
flowchart TD
    start([ft run . --template feature-fast]) --> plan["plano interno consultivo"]
    plan --> preflight

    subgraph scope["feature-01-scope"]
        preflight{{"preflight<br/>checks estáticos → baseline"}}
        discovery["discovery<br/>contrato + plano + workset"]
        discovery_gate{{"discovery_gate<br/>extrair clareza"}}
        clarity{"clarity"}
        questions[/"questions<br/>responder pendências"/]
        reserve_ids{{"reserve_ids<br/>PB distinto + FEAT reservado"}}
        scope_gate[/"scope_gate<br/>aprovar escopo"/]
        receipt_baseline{{"receipt_baseline<br/>dependências antes do delta"}}
    end

    subgraph build["feature-02-build"]
        implement["implement<br/>checks/AC-* + código e testes"]
        impact_prepare{{"impact_prepare<br/>workset dinâmico + lanes"}}
        checks{{"checks<br/>cobertura AC ↔ checks/"}}
        pre_review_route{{"pre_review_route<br/>extrair rota"}}
        pre_review_decision{"pre_review_decision"}
        product_validate{{"product_validate<br/>ensure local: build + test"}}
        evidence_gate{{"evidence_gate<br/>derivar e auditar evidência"}}
        review_prepare{{"review_prepare<br/>review_id + receipts atuais"}}
        verify{{"verify<br/>executa um check por AC-*"}}
        review_route{{"review_route<br/>extrair rota estruturada"}}
        review_decision{"review_decision"}
        fix_prepare{{"fix_prepare<br/>congelar review + F-* + commit"}}
        fix["fix<br/>somente achados rejeitados"]
        fix_validate{{"fix_validate<br/>delta focal válido"}}
        fix_full_validate{{"fix_full_validate<br/>build + test uma vez"}}
    end

    subgraph acceptance["feature-03-acceptance"]
        accept[/"acceptance<br/>aceite do stakeholder"/]
        reconcile["reconcile<br/>proposta documental"]
        final_gate{{"final_gate<br/>receipt + reconciliação"}}
        endnode([feature.end])
    end

    preflight --> discovery --> discovery_gate --> clarity
    clarity -->|required| questions
    questions -. respostas .-> discovery
    clarity -->|clear| reserve_ids --> scope_gate
    scope_gate -. rejeição .-> discovery
    scope_gate --> receipt_baseline --> implement --> impact_prepare --> checks
    checks --> pre_review_route --> pre_review_decision
    checks -. cobertura incompleta .-> implement
    pre_review_decision -->|approved| product_validate
    pre_review_decision -. implementation .-> implement
    pre_review_decision -. scope .-> discovery
    pre_review_decision -. inválida .-> checks
    product_validate --> evidence_gate --> review_prepare --> verify
    product_validate -. falha focal .-> implement
    evidence_gate -. evidência incompleta .-> implement
    verify --> review_route --> review_decision
    review_decision -->|approved| accept
    review_decision -->|implementation| fix_prepare --> fix --> fix_validate
    fix_validate --> fix_full_validate --> review_prepare
    fix_validate -. falha focal .-> fix
    fix_full_validate -. regressão .-> fix
    review_decision -. evidence .-> evidence_gate
    review_decision -. scope .-> discovery
    review_decision -. inválida .-> verify
    accept -. rejeição semântica .-> implement
    accept --> reconcile --> final_gate --> endnode --> close([ft close --merge full])

    classDef human fill:#fde68a,stroke:#b45309,color:#000;
    classDef gate fill:#bfdbfe,stroke:#1e40af,color:#000;
    classDef terminal fill:#bbf7d0,stroke:#166534,color:#000;
    class questions,scope_gate,accept human;
    class preflight,discovery_gate,reserve_ids,receipt_baseline,impact_prepare,checks,pre_review_route,product_validate,evidence_gate,review_prepare,verify,review_route,fix_prepare,fix_validate,fix_full_validate,final_gate gate;
    class start,endnode,close terminal;
```

## Salvaguardas de desempenho

- `preflight` e os demais gates caros usam `validation_mode: fail_fast`; checks
  estáticos vêm antes de build/test.
- Demandas com mais de 6 ACs voltam ao discovery para serem divididas em
  fatias verticais de 4–6 ACs; um único ciclo não absorve escopo gigante.
- `impact_prepare` expande o workset pelo delta e por testes/pares relacionados.
  `pre_review` detecta defeitos semânticos antes de pagar o build/test completo.
- `implement` não produz evidência narrativa. `product_validate` roda a suíte
  completa uma vez por snapshot e `ensure` reutiliza somente o receipt local
  válido.
- `evidence_gate` deriva relatório e evidência do receipt executado e audita
  as referências; o veredicto por AC-* fica em `verify`, o único gate que
  executa os checks.
- `checks` e `verify` substituem as três revisões por LLM. Cada AC-* tem um
  `checks/AC-NNN.py` escrito antes da implementação e ancorado no código de
  produção; a qualidade deles é medida por mutação no aceite do stakeholder.
- Uma rejeição de implementação não reinicia `implement → evidence → review`.
  Ela congela os F-* e executa `fix → fix_validate → fix_full_validate`;
  a atestação é refeita do zero em `verify`. Apenas
  expansão do workset/contrato retorna ao caminho completo.
- Tentativas intermediárias do fix executam somente a validação focal.
  `fix_full_validate` paga build+test e renova receipts uma única vez, antes
  de a atestação ser refeita.
- Toda review recebe um ID ligado ao snapshot atual. Receipts adicionais
  declaram seus paths: uma lane física só é refeita quando essas dependências
  mudam, inclusive durante um fix focal; caso contrário, sua prova anterior é
  reutilizada explicitamente.
- O episódio de implementação herda a lease global de produtividade: a janela
  temporal dispara sondas e é renovada por stream, worktree ou processo ativo.
  O orçamento cumulativo é telemetria; rotas semânticas `implementation` e
  `scope`, além de rejeição humana legítima, iniciam um episódio novo.
- `reconcile` propõe conteúdo estruturado, o engine valida IDs autorizados e só
  então aplica os documentos canônicos.
- Ciclos paralelos exigem PBs preexistentes distintos. FEATs novos são
  reservados sob lock curto, e o close tenta a reconciliação conservadora de
  CHANGELOG, backlog e catálogo antes de pedir merge manual.
