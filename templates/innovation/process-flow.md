# Innovation — Fluxo do Processo (v0.2.0)

Grafo executável de `process.yml`: 14 nodes, dois desfechos legítimos
(handoff/go e post-mortem/no-go) num único `end`. LLM executa pesquisa e
julgamento; Python executa gates determinísticos; humanos decidem nos dois
human gates (bypassáveis com `--bypass-human-gates`).

```mermaid
flowchart TD
    START(["ft run . --template innovation<br/>--input demanda.md"]) --> INTAKE

    subgraph SPRINT1["Sprint 01 — Research"]
        INTAKE["🧠 intake<br/>classifica demanda,<br/>formula H-NN falseáveis"]
        INTAKE --> |"gate: kind válido,<br/>H-NN no padrão"| RM

        RM["🔎 research_market<br/>problema existe? tamanho?<br/>sinais de demanda"]
        RC["🔎 research_competitors<br/>quem já resolve, preços,<br/>alternativa 'não fazer nada'"]
        RF["🔎⚡ research_feasibility<br/>viabilidade técnica<br/>+ SONDAS fire-proof<br/>(HTTP GET real, máx. 3/alvo)"]
        RM --> RC --> RF

        RF --> RG{"research_gate<br/>todo claim tem<br/>URL + data + confiança?<br/>supports → H-* existente?"}
        RG --> |PASS| VAL

        VAL["⚖️ validation<br/>cruza H-* × EV-*<br/>probe > desk (mesma rota)"]
        VAL --> VG{"validation_gate<br/>overall_verdict"}

        VG --> ROUTE{{"validation_route"}}
        ROUTE --> |inconclusive| QUESTIONS
        QUESTIONS["👤 questions (human gate)<br/>dados que só o stakeholder tem<br/>─────<br/>bypass: LLM responde com<br/>[LLM Responde / Modelo]<br/>+ premissas conservadoras"]
        QUESTIONS --> |"respostas voltam<br/>(preenche só lacunas)"| RM
    end

    ROUTE --> |refuted| PM
    ROUTE --> |supported| BC

    subgraph SPRINT2["Sprint 02 — Business Case"]
        BC["📊 business_case<br/>custo, retorno, riscos,<br/>SC-NN com Métrica/Alvo/Prazo"]
        BC --> BCG{"gate: recommendation,<br/>effort, SC mensuráveis"}
        BCG --> |PASS| GONOGO
        GONOGO["👤 go_nogo (human gate)<br/>decisão de investimento<br/>─────<br/>bypass: segue direto"]
    end

    GONOGO --> |reject| PM
    GONOGO --> |approve| PRD

    subgraph SPRINT3["Sprint 03 — Handoff"]
        PRD["📝 prd<br/>US-NN + AC-NN,<br/>rastreabilidade SC→AC,<br/>handoff.md (next_process)"]
        PRD --> PRDG{"gate: todo SC-*<br/>coberto por AC-*,<br/>next_process válido"}

        PM["🪦 post_mortem<br/>por que morreu (cita EV-*),<br/>o que reativaria,<br/>aproveitável"]
    end

    PRDG --> |PASS| END_GO
    PM --> |"gate: seções + EV-* reais"| END_NOGO

    END_GO(["✅ GO — handoff.md<br/>→ ft run --template<br/>mvp-builder-fast | feature-fast<br/>SC-* viram critérios do piloto"])
    END_NOGO(["🗄️ NO-GO — dossiê arquivado<br/>post-mortem com condições<br/>de reativação observáveis"])

    style INTAKE fill:#e8f0fe,stroke:#4285f4
    style RM fill:#e8f0fe,stroke:#4285f4
    style RC fill:#e8f0fe,stroke:#4285f4
    style RF fill:#e8f0fe,stroke:#4285f4
    style VAL fill:#e8f0fe,stroke:#4285f4
    style BC fill:#e8f0fe,stroke:#4285f4
    style PRD fill:#e8f0fe,stroke:#4285f4
    style PM fill:#e8f0fe,stroke:#4285f4
    style QUESTIONS fill:#fef7e0,stroke:#f9ab00
    style GONOGO fill:#fef7e0,stroke:#f9ab00
    style RG fill:#e6f4ea,stroke:#34a853
    style VG fill:#e6f4ea,stroke:#34a853
    style BCG fill:#e6f4ea,stroke:#34a853
    style PRDG fill:#e6f4ea,stroke:#34a853
    style ROUTE fill:#e6f4ea,stroke:#34a853
    style END_GO fill:#d2f8d2,stroke:#188038,stroke-width:2px
    style END_NOGO fill:#f3e8fd,stroke:#a142f4,stroke-width:2px
```

## Legenda

| Cor | Executor | Papel |
|---|---|---|
| 🔵 Azul | LLM (claude) | Pesquisa, julgamento e escrita — nunca decide rota |
| 🟢 Verde | Python | Gates determinísticos e decisions — binários, auditáveis |
| 🟡 Amarelo | Humano | `questions` (condicional) e `go_nogo` (investimento) |

## Garantias determinísticas por gate

- **intake**: `kind: product|process|mixed`; toda hipótese no padrão `## H-NN — afirmação`.
- **research_gate**: claim sem URL/data/confiança bloqueia; `supports` órfão bloqueia; hipótese sem claim bloqueia.
- **validation_gate**: todo H-* com verdict; verdict sem EV-* citado bloqueia; EV-* inexistente bloqueia.
- **business_case gate**: `recommendation: go|no_go`; todo SC-* com Métrica/Alvo/Prazo preenchidos.
- **prd_gate**: todo SC-* do business case coberto por AC-* na tabela de rastreabilidade; `next_process: mvp-builder-fast|feature-fast`.
- **post_mortem gate**: seções obrigatórias; "por que morreu" cita EV-* reais do dossiê.

## Propriedades do desenho

- **No-go é sucesso**: matar uma ideia barata, com dossiê auditável e condições
  de reativação, é entrega de valor — três das quatro investigações do Clari
  terminaram aqui em ~30 min cada.
- **Sondas fire-proof** (v0.2.0): claim decisivo verificável por HTTP GET vira
  observação direta (`method: probe`, resposta bruta salva) e prevalece sobre
  desk research da mesma rota.
- **Ciclo descartável**: cada run é uma worktree isolada; `ft close --merge full`
  leva só os artefatos canônicos ao checkout; o dossiê completo fica em
  `.ft/cycles/<cycle>/`.
