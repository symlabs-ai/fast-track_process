# Feature — Diagrama de Fluxo

Fluxo do processo definido em [`process.yml`](./process.yml) (`id: feature`,
versão `1.2.0`). Gerado a partir dos `nodes`, seus `next`, `branches`,
`reject_next` e `on_fail`.

## Legenda

| Forma | Tipo de node |
|---|---|
| Hexágono | `gate` (validação determinística) |
| Retângulo | `discovery` / `build` / `review` / `document` (executor de agente) |
| Losango | `decision` (branch por condição) |
| Paralelogramo | `human_gate` (aprovação humana) |
| Estádio | `end` |

Arestas tracejadas em vermelho são caminhos de **rejeição / correção** que
voltam o ciclo para trás.

## Fluxo

```mermaid
flowchart TD
    start([ft feature --template feature]) --> preflight

    subgraph s1["Sprint feature-01-scope"]
        preflight{{"feature.preflight<br/>Preflight do Produto Existente<br/>(gate)"}}
        discovery["feature.discovery<br/>Elucidar Demanda e Planejar<br/>(discovery · claude)"]
        discovery_gate{{"feature.discovery_gate<br/>Registrar Resultado do Discovery<br/>(gate)"}}
        clarity{"feature.clarity<br/>Demanda está clara?<br/>(decision)"}
        questions[/"feature.questions<br/>Perguntas sobre a Feature<br/>(human_gate)"/]
        scope_gate[/"feature.scope_gate<br/>Aprovação do Escopo<br/>(human_gate)"/]
    end

    subgraph s2["Sprint feature-02-build"]
        implement["feature.implement<br/>Implementar Feature e Testes<br/>(build · claude)"]
        review["feature.review<br/>Revisão Independente<br/>(review · claude)"]
    end

    subgraph s3["Sprint feature-03-acceptance"]
        acceptance[/"feature.acceptance<br/>Aceite da Feature<br/>(human_gate)"/]
        reconcile["feature.reconcile<br/>Reconciliar Backlog e Catálogo<br/>(document · claude)"]
        final_gate{{"feature.final_gate<br/>Gate Final<br/>(gate)"}}
        endnode([feature.end<br/>Feature Pronta para Merge])
    end

    preflight --> discovery
    discovery --> discovery_gate
    discovery_gate --> clarity
    clarity -->|clear| scope_gate
    clarity -->|"required / _default"| questions
    questions -.->|respostas voltam ao discovery| discovery

    scope_gate --> implement
    scope_gate -.->|reject_next| discovery

    implement --> review
    review --> acceptance
    review -.->|on_fail → human_gate| implement

    acceptance --> reconcile
    acceptance -.->|reject_next| implement

    reconcile --> final_gate
    final_gate --> endnode
    endnode --> close([ft close --merge full])

    classDef human fill:#fde68a,stroke:#b45309,color:#000;
    classDef gate fill:#bfdbfe,stroke:#1e40af,color:#000;
    classDef terminal fill:#bbf7d0,stroke:#166534,color:#000;
    class questions,scope_gate,acceptance human;
    class preflight,discovery_gate,final_gate gate;
    class start,endnode,close terminal;

    linkStyle 6,8,11,13 stroke:#dc2626,stroke-width:2px;
```

## Resumo dos caminhos

- **Caminho feliz:** `preflight → discovery → discovery_gate → clarity(clear) →
  scope_gate → implement → review → acceptance → reconcile → final_gate → end`.
- **Loop de clarificação:** `clarity(required) → questions → discovery →
  discovery_gate` — repete até o discovery marcar
  `clarification_status: clear`.
- **Rejeição de escopo:** `scope_gate` (reject) volta para `discovery`.
- **Falha de revisão:** `review` com `on_fail` abre um human_gate e retorna para
  `implement`.
- **Rejeição no aceite:** `acceptance` (reject) volta para `implement`, repetindo
  `make build` / `make test`.
