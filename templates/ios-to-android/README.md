# Template `ios-to-android`

Processo de manutenção para portar um aplicativo iOS já entregue para Android,
mantendo os dois alvos no mesmo repositório e tratando-os como um único produto.

O template não traduz Swift para outra linguagem mecanicamente. Ele inventaria
o comportamento comprovado no iOS, congela uma matriz `CAP-*`/`PAC-*`, preserva
contratos compartilháveis e implementa adapters próprios de cada sistema. O
ciclo só termina depois de revalidar Android e iOS, inclusive em aparelhos
físicos, contra o mesmo candidato.

## Quando usar

Use quando:

- o app iOS está entregue e o projeto FT está em `maintenance`;
- Android será um novo alvo do mesmo produto e do mesmo Git;
- existe um `PB-*` para o port em `docs/PROJECT_BACKLOG.md`;
- `docs/PRD.md`, `docs/TECH_STACK.md` e `docs/FEATURES.md` descrevem o produto;
- há ambientes autorizados para Android emulator/physical e iOS
  simulator/physical antes do gate final.

Se o repositório ainda não foi adotado pelo Fast Track, execute primeiro o
template `fastfy`. Se o produto ainda estiver em `building`, complete o builder
e `ft project-close`; este template não contorna o DoD global.

## Uso

```bash
ft run . --template ios-to-android \
  --request "Portar PB-042 para Android preservando paridade funcional" \
  --codex
```

Na primeira execução, o bundle é copiado para
`.ft/process/ios-to-android/`. Customizações específicas do produto devem ser
feitas nessa cópia local, nunca no catálogo global.

## Contrato do processo

O ciclo produz três contratos principais:

- `docs/ios-android-port-plan.yml`: roots, identidade, estratégia compartilhada,
  ordem e riscos;
- `docs/ios-android-capabilities.yml`: inventário rastreável do comportamento
  iOS e da paridade Android;
- `docs/platform-validation-report.yml`: receipt do candidato executado nos
  quatro targets obrigatórios.

O processo cria um `Makefile` na raiz, ou reconcilia o existente, com targets
reais e não-stub:

```text
validate-android-emulator
validate-android-physical
validate-ios-simulator
validate-ios-physical
```

Esses targets pertencem ao produto. O template não conhece nem executa comandos
arbitrários escritos pelo LLM; seu validator próprio apenas confere schemas,
paths e evidências. A matriz global do FT determina os checks de cada target.

## Gates humanos

1. dúvidas que não podem ser inferidas do iOS;
2. aprovação do plano e da matriz de capacidades antes do código;
3. aceite do Android instalado e confirmação de ausência de regressão no iOS.

A aprovação final não publica nas lojas. Credenciais, keystores, certificados,
profiles e material de assinatura permanecem fora do Git. Release na Google
Play ou App Store exige autorização e ciclo próprios.

## Encerramento

Depois do node terminal:

```bash
ft close --merge full
```

O merge incorpora o Android, preserva o iOS no mesmo repositório e arquiva os
artefatos de evidência do ciclo. Confirme o checkout promovido com os targets do
Makefile antes de demonstrar ou iniciar um release.
