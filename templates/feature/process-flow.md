# Feature — Diagrama de Fluxo

Fluxo do processo definido em [`process.yml`](./process.yml) (`id: feature`,
versão `1.3.0`). O template continua executando um ciclo independente por
demanda; não existe planner, wave ou batch oculto.

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
    start([ft run . --template feature]) --> preflight

    subgraph scope["feature-01-scope"]
        preflight{{"preflight<br/>checks estáticos → baseline"}}
        discovery["discovery<br/>contrato + plano + workset"]
        discovery_gate{{"discovery_gate<br/>extrair clareza"}}
        clarity{"clarity"}
        questions[/"questions<br/>responder pendências"/]
        reserve_ids{{"reserve_ids<br/>PB distinto + FEAT reservado"}}
        scope_gate[/"scope_gate<br/>aprovar escopo"/]
    end

    subgraph build["feature-02-build"]
        implement["implement<br/>somente código e testes"]
        product_validate{{"product_validate<br/>ensure local: build + test"}}
        evidence["evidence<br/>somente referências e relatório"]
        evidence_gate{{"evidence_gate<br/>integridade referencial"}}
        review["review<br/>avaliação semântica independente"]
        review_route{{"review_route<br/>extrair rota estruturada"}}
        review_decision{"review_decision"}
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
    scope_gate --> implement --> product_validate --> evidence --> evidence_gate --> review
    product_validate -. falha focal .-> implement
    evidence_gate -. referência inválida .-> evidence
    review --> review_route --> review_decision
    review_decision -->|approved| accept
    review_decision -. implementation .-> implement
    review_decision -. evidence .-> evidence
    review_decision -. scope .-> discovery
    review_decision -. inválida .-> review
    accept -. rejeição semântica .-> implement
    accept --> reconcile --> final_gate --> endnode --> close([ft close --merge full])

    classDef human fill:#fde68a,stroke:#b45309,color:#000;
    classDef gate fill:#bfdbfe,stroke:#1e40af,color:#000;
    classDef terminal fill:#bbf7d0,stroke:#166534,color:#000;
    class questions,scope_gate,accept human;
    class preflight,discovery_gate,reserve_ids,product_validate,evidence_gate,review_route,final_gate gate;
    class start,endnode,close terminal;
```

## Salvaguardas de desempenho

- `preflight` e os demais gates caros usam `validation_mode: fail_fast`; checks
  estáticos vêm antes de build/test.
- `implement` não produz evidência narrativa. `product_validate` roda a suíte
  completa uma vez por snapshot e `ensure` reutiliza somente o receipt local
  válido.
- `evidence` não altera código e o gate seguinte comprova apenas referências;
  a suficiência semântica permanece na review.
- O episódio de implementação tem deadline por chamada e orçamento cumulativo.
  Rotas semânticas `implementation` e `scope`, além de rejeição humana legítima,
  iniciam um episódio novo; esgotamento pausa preservando o diff.
- `reconcile` propõe conteúdo estruturado, o engine valida IDs autorizados e só
  então aplica os documentos canônicos.
- Ciclos paralelos exigem PBs preexistentes distintos. FEATs novos são
  reservados sob lock curto, e o close tenta a reconciliação conservadora de
  CHANGELOG, backlog e catálogo antes de pedir merge manual.
