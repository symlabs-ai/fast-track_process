# Fluxo do template Desenvolvimento Orientado ao Mercado (MDD)

```mermaid
flowchart TD
    START([ft run --template mdd]) --> H[Hipótese]
    H --> HG{{Aprovar hipótese}}
    HG -. rejeitar .-> H
    HG --> V[Visão]
    V --> VG{{Aprovar visão}}
    VG -. rejeitar .-> V
    VG --> P[PRD]
    P --> PG{{Aprovar PRD}}
    PG -. rejeitar .-> P
    PG --> DG[[Gate da definição]]
    DG --> ES[Sumário executivo]
    ES --> PD[Pitch deck]
    PD --> SP[Proposta comercial do site<br/>para usuários finais]
    SP --> PKG[[Gate estrutural do pacote]]
    PKG --> HR{{Aprovar pacote MDD}}
    HR -. rejeitar .-> REV[Revisão focal dos derivados]
    REV --> PKG
    HR --> PI[12 PNGs do pitch deck<br/>Sol Max + image_gen]
    PI --> SI[PNG vertical do protótipo do site<br/>Sol Max + image_gen]
    SI --> AIG[[Gate técnico dos assets visuais]]
    AIG --> VR{{Aprovar slides e protótipo}}
    VR -. rejeitar .-> VREV[Revisão focal dos PNGs]
    VREV --> AIG
    VR --> HO[Handoff]
    HO --> END([MDD concluída])
```

A definição é aprovada antes da narrativa. O sumário executivo deriva de
hipótese, visão e PRD; o deck deriva do sumário; a proposta comercial do site
deriva da narrativa consolidada e fala diretamente ao usuário final. Somente
após a aprovação textual, nodes fixados em
`gpt-5.6-sol`/`max` produzem 12 PNGs de slides e um PNG vertical do site. O
`ft close` promove os documentos, bitmaps e receipts canônicos. O template não
muda a fase do projeto nem assume o papel de builder.
