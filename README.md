# ForgeProcess — Fast Track

> Processo ágil para solo dev + AI. 19 steps, 9 fases, valor > cerimônia, com sprints técnicas.

**5 symbiotas** · **TDD obrigatório** · **E2E CLI gate** · **Hyper-mode** · **Maintenance mode**

---

## O que é

Fast Track é uma variante do ForgeProcess para desenvolvedor solo trabalhando com assistentes de IA.
Define um fluxo completo — do insight à entrega — com rigor (TDD, Sprint Expert Gate, E2E gate) e sem burocracia
de squad (cerimônias tradicionais, BDD Gherkin, reviews de 3 pessoas).

## Symbiotas

| Symbiota | Papel |
|----------|-------|
| `ft_manager` | Orquestra o processo, delega validações ao gatekeeper e interage com o stakeholder |
| `ft_gatekeeper` | Valida stage gates (PASS/BLOCK) — determinístico, sem interpretação criativa |
| `ft_acceptance` | Projeta cenários de teste de aceitação por Value/Support Track |
| `ft_coach` | Conduz MDD, planning e feedback |
| `forge_coder` | Executa TDD, delivery e E2E |

## Início rápido

```bash
# 1. Clone e desconecte do template
git clone https://github.com/symlabs-ai/fast-track_process.git meu-projeto
cd meu-projeto
git remote remove origin
git remote add origin <url-do-seu-repo>
git push -u origin main

# 2. Carregue o ft_manager como system prompt
#    → process/symbiotes/ft_manager/prompt.md

# 3. O ft_manager conduz tudo a partir daí
```

## ft engine (v0.7+)

O Fast Track agora inclui um **motor determinístico Python** que substitui a orquestração por LLM:

```bash
ft-engine init               # inicializar engine_state.yml
ft-engine continue --sprint  # rodar sprint completa
ft-engine approve            # aprovar artefato pendente
ft-engine status --full      # ver grafo com progresso
```

O LLM só executa tarefas de construção — o Python controla todo o fluxo, validações e gates.
Neste repositório, `ft` é a CLI do template/processo; `ft-engine` é a CLI do motor determinístico.

- **Guia completo**: [`docs/ft_engine_usage.md`](docs/ft_engine_usage.md)
- **Processo V2**: `process/fast_track/FAST_TRACK_PROCESS_V2.yml`

---

## Documentação

- **Processo**: `process/fast_track/FAST_TRACK_PROCESS.md`
- **YAML (machine-readable)**: `process/fast_track/FAST_TRACK_PROCESS.yml`
- **YAML V2 (engine)**: `process/fast_track/FAST_TRACK_PROCESS_V2.yml`
- **ft engine**: `docs/ft_engine_usage.md`
- **Resumo para agentes**: `process/fast_track/SUMMARY_FOR_AGENTS.md`
- **Diagrama de fluxo**: `docs/fast-track-flow.md`
- **Guia de agentes**: `AGENTS.md`

---

## Changelog

Versão atual: **v0.8.0** — Changelog completo em [`CHANGELOG.md`](CHANGELOG.md)
