# Alertas Fast Track

Verifique este arquivo no início de cada sessão. Itens ativos exigem análise antes de incluir um modelo no roteamento de processos.

## Ativos

### 2026-09-01 — 🟠 Claude Fable 5.1 requer probe e eval antes de entrar nas rotas FT

**Site original:** [What's new in Claude Fable 5.1 — Anthropic](https://platform.claude.com/docs/en/models/fable-5-1/whats-new-fable-5-1)

**Análise completa do Jarvis:** `~/dev/jarvis/docs/kb/articles/2026-09-01-claude-fable-5-1-novidades-migracao.md`

#### O que mudou

Fable 5.1 oferece contexto de 1 milhão de tokens, saída de até 128 mil tokens e cache read a US$ 0,25/MTok. Adaptive thinking é obrigatório. Forced tool choice deixa de funcionar, blocos de `thinking` passam a depender do prefixo exato e um modelo anterior não consegue reaproveitar o raciocínio produzido pelo 5.1.

O modelo é apresentado como melhor em código agentivo longo, pesquisa, documentos, visão e computer use, mas a página não publica benchmarks. A Anthropic também documenta menor paralelismo de tools em alguns loops, menos progresso textual, menos retrieval em esforço baixo, citações não marcadas e reescritas integrais de arquivo.

#### Impacto direto no Fast Track

- `docs/ft_model_orchestration.md` já exige capability probe, escolha por node e ausência de fallback silencioso. Fable 5.1 não deve entrar como novo default apenas pela alegação do fabricante.
- Sessões Claude usam `--session-id` e `--resume`. Trocar o modelo entre episódios da mesma sessão pode descartar thinking; a rota escolhida precisa permanecer fixa enquanto a sessão for retomável.
- A telemetria reconhece `thinking_delta` e `thinking_tokens`, mas Fable 5.1 pode emitir menos texto e ocultar progress updates por padrão. O supervisor deve continuar usando stream, processo, CPU/I/O e worktree para decidir inatividade.
- A tendência a reescrever arquivos inteiros aumenta ruído de diff e risco de violar `write_scope`; validators precisam comprovar o delta permitido.
- MDD, sínteses e pesquisa precisam exigir citações marcadas e retrieval quando atualidade for requisito.
- A retenção obrigatória de 30 dias precisa ser considerada por ambiente executor e projeto antes de selecionar o modelo.

#### Ação obrigatória antes de habilitar o modelo

1. Adicionar o modelo somente após `ft llm-capabilities` confirmar disponibilidade e betas na rota real.
2. Executar o mesmo corpus de nodes em Fable 5.1 e na baseline atual, medindo sucesso, correções, tokens, tempo e custo.
3. Cobrir resume sem troca de modelo, tool batching, progresso, busca, citações, compactação e diffs localizados.
4. Fixar modelo e effort por node; qualquer escalonamento deve começar um novo episódio explícito e auditável.
5. Bloquear execução quando a retenção de 30 dias for incompatível com o projeto.
6. Atualizar o manual de orquestração apenas depois de evidência reproduzível.

**Critério para baixar este alerta:** capability probe e eval comparativo devem demonstrar benefício por classe de node; resume e telemetria precisam permanecer corretos; não pode haver fallback silencioso nem violação de write scope; e a política de retenção deve estar registrada.
