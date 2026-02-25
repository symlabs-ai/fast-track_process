# Fast Track — Diagrama de Fluxo

```mermaid
flowchart TD
    START([🚀 Início]) --> GIT

    subgraph INIT["⚙️ Inicialização — ft_manager"]
        GIT{Remote aponta\npara template?}
        GIT -- Sim --> RECONFIG[Solicitar nova URL\nReconfigurar origin]
        GIT -- Não / já correto --> STATE
        RECONFIG --> STATE
        STATE[Ler ft_state.yml]
    end

    STATE -- projeto novo --> MDD_MODE
    STATE -- em andamento --> RESUME([Retomar step\npendente])

    MDD_MODE{PRD abrangente\nentregue?}
    MDD_MODE -- não --> MDD
    MDD_MODE -- sim --> HYPER

    subgraph HYPER["⚡ Hyper-Mode MDD — ft_coach"]
        HY1[Absorver PRD\ndo stakeholder]
        HY2[Gerar PRD.md\n+ TASK_LIST.md]
        HY3[Gerar questionário\nde alinhamento]
        HY4{Stakeholder\nresponde}
        HY5[Incorporar respostas\nfinalizar artefatos]
        HY1 --> HY2 --> HY3 --> HY4 --> HY5
    end

    HY3 -. "🔍 Pontos Ambíguos\n🕳️ Lacunas\n💡 Sugestões" .-> HY4

    subgraph MDD["📋 Fase 1: MDD normal — ft_coach"]
        H[ft.mdd.01\nhipótese]
        H --> PRD[ft.mdd.02\nredigir PRD]
        PRD --> VALPRD2[ft.mdd.03\nvalidar PRD]
    end

    HY5 --> VAL_PRD
    VALPRD2 --> VAL_PRD

    VAL_PRD{ft_manager\nvalida PRD}
    VAL_PRD -- falhou --> PRD
    VAL_PRD -- falhou hyper --> HY5
    VAL_PRD -- ok --> GO{go / no-go}

    GO -- rejected --> END_REJ([❌ Encerrado])
    GO -- approved --> PLAN

    subgraph PLAN["📝 Fase 2: Planning"]
        TL["ft.plan.01\ntask list\n[ft_coach]"]
        TL --> VAL_TL{ft_manager\nvalida task list}
        VAL_TL -- falhou --> TL
        VAL_TL -- ok --> STACK

        STACK["ft.plan.02\ntech stack\n[forge_coder]"]
        STACK --> SK_REV{stakeholder\nrevisa stack}
        SK_REV -- ajustes --> STACK
        SK_REV -- aprovado --> DIAG

        DIAG["ft.plan.03\ndiagramas\n[forge_coder]\nclass · components\ndatabase · architecture"]
        DIAG --> LOOP_START
    end

    note_hyper["ℹ️ Em hyper-mode\nTASK_LIST já gerada\nft_coach pula ft.plan.01"]
    HYPER -.-> note_hyper
    note_hyper -.-> PLAN

    subgraph LOOP["🔁 Loop por Task"]
        LOOP_START([próxima task])

        subgraph TDD["🧪 Fase 3: TDD — forge_coder"]
            SEL[ft.tdd.01\nselecionar task]
            RED[ft.tdd.02\nred — escrever teste]
            GREEN[ft.tdd.03\ngreen — implementar]
            SEL --> RED --> GREEN
        end

        subgraph DELIVERY["📦 Fase 4: Delivery — forge_coder"]
            IMPL[ft.delivery.01\nintegrar + suite]
            REVIEW[ft.delivery.02\nself-review]
            COMMIT[ft.delivery.03\ncommit]
            IMPL --> REVIEW --> COMMIT
        end

        VAL_ENT{ft_manager\nvalida entrega}
        MORE{tasks\npendentes?}

        LOOP_START --> SEL
        GREEN --> IMPL
        COMMIT --> VAL_ENT
        VAL_ENT -- falhou --> REVIEW
        VAL_ENT -- ok --> MORE
        MORE -- sim --> LOOP_START
    end

    MORE -- não --> E2E

    subgraph E2E_GATE["🔒 Fase 5: E2E Gate — forge_coder"]
        E2E[ft.e2e.01\ncli validation]
        VAL_E2E{E2E\npassou?}
        E2E --> VAL_E2E
        VAL_E2E -- falhou --> E2E
    end

    VAL_E2E -- ok --> MODO

    subgraph FEEDBACK["📊 Fase 6: Feedback — ft_coach"]
        RETRO[ft.feedback.01\nretro note]
    end

    subgraph STAKEHOLDER["👥 Decisão de Ciclo — ft_manager"]
        MODO{stakeholder\nmode?}
        MODO -- interactive --> APRESENTA[Apresentar E2E\nao stakeholder]
        MODO -- autonomous --> RETRO

        APRESENTA --> SK_DEC{decisão}
        SK_DEC -- novo ciclo --> RETRO
        SK_DEC -- MVP concluído --> MVP_OK
        SK_DEC -- continue sem validação --> SET_AUTO[set autonomous]
        SET_AUTO --> RETRO
    end

    RETRO --> CONTINUAR{continuar?}
    CONTINUAR -- novo ciclo --> PLAN
    CONTINUAR -- encerrar --> MVP_OK

    MVP_OK{autonomous\ne MVP pronto?}
    MVP_OK -- sim --> MVP_FINAL[Apresentar\nMVP final ao stakeholder]
    MVP_OK -- não --> END_OK

    MVP_FINAL --> END_OK([✅ Projeto concluído])
```
