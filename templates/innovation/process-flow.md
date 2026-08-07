# Innovation — Fluxo do Processo (v0.3.3)

Grafo executável de `process.yml`: 14 nodes, dois desfechos legítimos
(handoff/go e post-mortem/no-go) num único `end`. LLM executa pesquisa e
julgamento; Python executa gates determinísticos; humanos decidem nos dois
human gates (bypassáveis com `--bypass-human-gates`).

```mermaid
flowchart TD
    START(["ft run . --template innovation<br/>--input demanda.md"]) --> INTAKE

    subgraph SPRINT1["Sprint 01 — Research"]
        INTAKE["🧠 intake<br/>Recebe a demanda bruta, separa produto<br/>de processo, formula o problema sem viés<br/>de solução e deriva hipóteses<br/>falseáveis numeradas (H-NN)"]
        INTAKE --> |"gate: kind válido,<br/>H-NN no padrão"| RM

        RM["🔎 research_market<br/>Pesquisa na web se o problema existe,<br/>seu tamanho e sinais de demanda.<br/>Toda afirmação vira claim com<br/>fonte, data e confiança"]
        RC["🔎 research_competitors<br/>Pesquisa quem já resolve o problema,<br/>como e a que preço — incluindo a<br/>alternativa 'não fazer nada' e<br/>soluções improvisadas"]
        RF["🔎⚡ research_feasibility<br/>Pesquisa se o caminho técnico é viável:<br/>tecnologias, APIs, limites e custos.<br/>Claims decisivos viram SONDA real<br/>(HTTP GET, máx. 3/alvo, resposta salva)"]
        RM --> RC --> RF

        RF --> RG{"research_gate<br/>Confere deterministicamente o schema<br/>das três lentes: todo claim tem fonte<br/>URL, data e confiança, e referencia<br/>hipóteses existentes. Não julga mérito"}
        RG --> |PASS| VAL

        VAL["⚖️ validation<br/>Julga cada hipótese exclusivamente<br/>contra os claims coletados e emite<br/>verdict por H-* e geral. Observação<br/>direta (probe) prevalece sobre<br/>desk research da mesma rota"]
        VAL --> VG{"validation_gate<br/>Registra o verdict de forma<br/>determinística para o roteamento"}

        VG --> ROUTE{{"validation_route<br/>supported → business case<br/>refuted → post-mortem<br/>inconclusive → stakeholder"}}
        ROUTE --> |inconclusive| QUESTIONS
        QUESTIONS["👤 questions (human gate)<br/>Perguntas que a web não responde:<br/>dado interno, acesso, disposição a<br/>pagar. Respostas via ft approve<br/>─────<br/>bypass: LLM responde com atribuição<br/>[LLM Responde / Modelo] e<br/>premissas conservadoras declaradas"]
        QUESTIONS --> |"respostas voltam<br/>(preenche só lacunas)"| RM
    end

    ROUTE --> |refuted| PM
    ROUTE --> |supported| BC

    subgraph SPRINT2["Sprint 02 — Business Case"]
        BC["📊 business_case<br/>Consolida evidências em decisão de<br/>investimento: custo, retorno, riscos,<br/>alternativas e critérios de sucesso<br/>SC-NN mensuráveis (Métrica/Alvo/Prazo)<br/>que o delivery herdará"]
        BC --> BCG{"gate<br/>recommendation go|no_go presente,<br/>esforço estimado, todo SC-*<br/>com Métrica, Alvo e Prazo"}
        BCG --> |PASS| GONOGO
        GONOGO["👤 go_nogo (human gate)<br/>Único gate humano obrigatório:<br/>decisão de investimento não se<br/>automatiza com honestidade. A<br/>recomendação do LLM não substitui<br/>a decisão do stakeholder<br/>─────<br/>bypass: segue direto para o PRD"]
    end

    GONOGO --> |reject| PM
    GONOGO --> |approve| PRD

    subgraph SPRINT3["Sprint 03 — Handoff"]
        PRD["📝 prd<br/>Converte o business case aprovado em<br/>PRD com user stories US-NN e critérios<br/>de aceite AC-NN cobrindo todos os SC-*,<br/>mais handoff.md apontando o processo<br/>de delivery adequado"]
        PRD --> PRDG{"gate<br/>Todo SC-* coberto por AC-* na tabela<br/>de rastreabilidade; next_process é<br/>mvp-builder-fast ou feature-fast"}

        PM["🪦 post_mortem<br/>Documenta por que a ideia não seguiu<br/>— citando as evidências EV-* — o que<br/>a reativaria (condições observáveis)<br/>e o que é aproveitável. O dossiê<br/>permanece como ativo reutilizável"]
    end

    PRDG --> |PASS| END_GO
    PM --> |"gate: seções + EV-* reais"| END_NOGO

    END_GO(["✅ GO — handoff.md indica a sequência<br/>(mdd → mvp-builder-fast | feature-fast),<br/>o que copiar para o seed e os SC-*<br/>como critérios do futuro piloto"])
    END_NOGO(["🗄️ NO-GO — ideia investigada e morta<br/>barata, com dossiê auditável e<br/>condições de reativação observáveis.<br/>Fim legítimo do processo"])

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
- **Boundary de delivery explícito**: um GO gera discovery e handoff, mas o
  projeto permanece `building`. Para produto novo, `mdd` precede e deve ser
  encerrado antes de `mvp-builder-fast`. O builder cria/reconcilia
  `docs/PROJECT_BACKLOG.md`, `.ft/project.yml` e a matriz de validação. O
  handoff registra separadamente se a implementação foi autorizada e nunca
  anuncia `docs/research/` como path durável após o close.
