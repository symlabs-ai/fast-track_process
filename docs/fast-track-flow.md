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
        H_DOC["📄 hipotese.md"]
        H --> H_DOC --> PRD[ft.mdd.02\nredigir PRD]
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
        DIAG --> SPRINT_PREP
    end

    note_hyper["ℹ️ Em hyper-mode\nTASK_LIST já gerada\nft_coach pula ft.plan.01"]
    HYPER -.-> note_hyper
    note_hyper -.-> PLAN

    subgraph LOOP["🔁 Loop por Sprint"]
        SPRINT_PREP([alinhar\nsprint atual])
        LOOP_START([próxima task\nda sprint])

        subgraph TDD["🧪 Fase 3: TDD — forge_coder"]
            SEL[ft.tdd.01\nselecionar task]
            RED[ft.tdd.02\nred — escrever teste]
            GREEN[ft.tdd.03\ngreen — implementar\n+ suite completa]
            SEL --> RED --> GREEN
        end

        subgraph DELIVERY["📦 Fase 4: Delivery — forge_coder"]
            REVIEW[ft.delivery.01\nself-review\n10 itens · 3 grupos]
            REFACTOR[ft.delivery.02\nrefactor]
            COMMIT[ft.delivery.03\ncommit]
            REVIEW --> REFACTOR --> COMMIT
        end

        VAL_ENT{ft_manager\nvalida entrega\n+ cov >= 85%}
        MORE{tasks pendentes\nna sprint?}
        SPRINT_PREFLIGHT{pre-flight da sprint\nok?}
        SPRINT_GATE["Sprint Expert Gate\n/ask fast-track"]
        SPRINT_FIX{há correções\nobrigatórias?}
        NEXT_SPRINT{sprint seguinte\nno ciclo?}

        SPRINT_PREP --> LOOP_START
        LOOP_START --> SEL
        GREEN --> REVIEW
        COMMIT --> VAL_ENT
        VAL_ENT -- falhou --> REVIEW
        VAL_ENT -- ok --> MORE
        MORE -- sim --> LOOP_START
        MORE -- não --> SPRINT_PREFLIGHT
        SPRINT_PREFLIGHT -- gap --> LOOP_START
        SPRINT_PREFLIGHT -- ok --> SPRINT_GATE
        SPRINT_GATE --> SPRINT_FIX
        SPRINT_FIX -- sim --> LOOP_START
        SPRINT_FIX -- não --> NEXT_SPRINT
        NEXT_SPRINT -- sim --> SPRINT_PREP
    end

    NEXT_SPRINT -- não --> SMOKE

    subgraph SMOKE_GATE["🔥 Fase 5a: Smoke Gate — forge_coder"]
        SMOKE[ft.smoke.01\ncli run]
        SMOKE_R["📄 smoke-cycle-XX.md\noutput real documentado"]
        VAL_SMOKE{smoke\nPASSOUU?}
        SMOKE --> SMOKE_R --> VAL_SMOKE
        VAL_SMOKE -- TRAVOU --> SMOKE
    end

    VAL_SMOKE -- PASSOU --> E2E

    subgraph E2E_GATE["🔒 Fase 5b: E2E Gate — forge_coder"]
        E2E[ft.e2e.01\ncli validation\nunit + smoke]
        VAL_E2E{E2E\npassou?}
        E2E --> VAL_E2E
        VAL_E2E -- falhou --> E2E
    end

    VAL_E2E -- ok --> ACCEPT_DEC

    subgraph ACCEPTANCE_GATE["🎯 Fase 5c: Acceptance Gate — forge_coder"]
        ACCEPT_DEC{interface_type\n!= cli_only?}
        ACCEPT_DEC -- cli_only\nskip --> MODO
        ACCEPT_DEC -- api/ui/mixed --> ACCEPT
        ACCEPT[ft.acceptance.01\ninterface validation\nACs × interface real]
        ACCEPT_R["📄 acceptance-cycle-XX.md\nmapeamento US→AC→Teste"]
        VAL_ACCEPT{acceptance\npassou?}
        ACCEPT --> ACCEPT_R --> VAL_ACCEPT
        VAL_ACCEPT -- falhou --> ACCEPT
        VAL_ACCEPT -- ok --> MODO
    end

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
    MVP_OK -- não --> AUDIT

    MVP_FINAL --> AUDIT

    subgraph AUDIT_PHASE["🔍 Fase 8: Auditoria ForgeBase — forge_coder"]
        AUDIT[ft.audit.01\nForgeBase audit]
        AUDIT_ITEMS["UseCaseRunner wiring\nValue/Support Tracks\nPulse snapshot\nLogging quality\nClean/Hex"]
        VAL_AUDIT{auditoria\npassou?}
        AUDIT --> AUDIT_ITEMS --> VAL_AUDIT
        VAL_AUDIT -- falhou --> AUDIT
    end

    VAL_AUDIT -- ok --> HANDOFF

    subgraph HANDOFF_PHASE["📄 Fase 9: Handoff — ft_coach"]
        HANDOFF[ft.handoff.01\ngerar SPEC.md]
        HANDOFF_VAL{SPEC.md\nválido?}
        HANDOFF --> HANDOFF_VAL
        HANDOFF_VAL -- falhou --> HANDOFF
    end

    HANDOFF_VAL -- ok --> SET_MAINT[set maintenance_mode: true]
    SET_MAINT --> END_OK([✅ Projeto concluído\nmaintenance_mode ativo])

    END_OK -. "🔧 Manutenção via\n/feature descrição" .-> FEATURE_NOTE["📝 /feature lê SPEC.md\nantes de implementar\natualiza SPEC.md\nao finalizar"]
```
