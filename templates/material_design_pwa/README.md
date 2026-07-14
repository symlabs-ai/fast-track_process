# Material Design PWA — M3 + instalável/offline

Aplica as guidelines de **Material Design 3** (incluindo M3 Expressive) à UI
de um projeto Fast Track existente e o transforma em um **PWA** instalável e
offline-first. As guidelines completas viajam com o processo em
`guidelines/material_design_pwa.md` — os nodes LLM trabalham a partir delas,
não de memória.

## O que o ciclo entrega

1. **Auditoria** da UI atual contra M3 e do estado PWA (manifesto, service
   worker, offline), com perguntas ao stakeholder quando a decisão é dele
   (cor da marca, dark mode, telas prioritárias, rotas offline);
2. **Fase 1 — Tema**: contrato de tokens M3 (color roles, type scale, shape,
   spacing e motion locais), dark mode via `prefers-color-scheme`, foco
   visível e acessibilidade base (WCAG 2.2, alvos 48×48);
3. **Fase 2 — Shell**: navegação adaptativa pelos breakpoints oficiais
   (navigation bar < 600px, rail 600–1199px, drawer persistente ≥ 1200px),
   componentes híbridos (`@material/web` onde suportado, HTML/CSS próprio no
   shell) com imports por componente;
4. **Fase 3 — PWA**: manifesto completo (ícones 192/512 + maskable), service
   worker com estratégia de cache por tipo de recurso, fallback offline,
   CTA de instalação contextual e aviso discreto de atualização;
5. **Validação**: baseline determinística (build + test + tokens/manifesto),
   revisão independente contra o checklist de QA das guidelines e aceite do
   stakeholder com o produto servido.

Ao final, `docs/ui_criteria.md` fica atualizado com critérios M3 que guiam os
próximos ciclos de feature.

## Pré-requisitos e uso

O projeto precisa da base canônica Fast Track (PRD, PROJECT_BACKLOG,
FEATURES) e de um harness executável (`Makefile` com `build/test/run/url` em
`./`, `project/` ou `src/`). Projeto legado sem essa base? Rode
`ft run . --template fastfy` primeiro.

```bash
cd projeto/
ft run . --template material_design_pwa
# ... responder perguntas / aprovar plano / aceitar ...
ft close --merge full
```

## Fluxo

```
preflight → audit ⇄ questions → scope_gate
  → theme → shell → pwa → baseline
  → review → acceptance → reconcile → final_gate → end
```

## Contratos determinísticos

- `docs/mdpwa-audit.md` (frontmatter): `framework`, `ui_root`,
  `has_manifest`, `has_service_worker`, `clarification_status`.
- `docs/mdpwa-plan.md` (frontmatter): `backlog_item` (PB-*), `theme_file`,
  `manifest_path`, `sw_source`, `offline_fallback` (path ou `generated`).
- Fase 1 valida o `theme_file`: roles `--md-sys-color-*`, typescale, shape,
  `:focus-visible` e bloco dark.
- Fase 3 valida o manifesto (name, short_name, start_url, display,
  theme_color, ícones 192/512), o `sw_source` e o fallback offline.
- A revisão exige `Resultado: APPROVED|REJECTED` e PASS/FAIL por item do
  checklist: Tokens, Tipografia, Responsividade, Navegação, Acessibilidade,
  Instalação, Offline, Atualização e Payload.

## Notas

- O `write_scope` dos nodes de build cobre os layouts web mais comuns
  (`project/`, `src/`, `app/`, `frontend/`, `public/`, `static/`, arquivos de
  config na raiz). Layout exótico? Ajuste o fork local em
  `.ft/process/material_design_pwa/process.yml` — o processo é seu após a
  materialização.
- Auditoria com Lighthouse/PageSpeed em CI é uma evolução natural deste
  template (hoje a medição fica com a revisão e o stakeholder).
