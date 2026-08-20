# Fluxo — iOS → Android no mesmo repositório

```text
preflight do iOS entregue
  → inventário + matriz CAP/PAC
  → dúvidas? ── sim → respostas humanas → refinar inventário
       │
       não
       ↓
  aprovação humana do plano
  → fundação Android + adapters + hooks dos quatro targets
  → gate estrutural da fundação
  → implementação de paridade + testes
  → gate de contratos/evidências
  → auditoria independente
       ├── erro de escopo → inventário
       ├── erro de implementação → implementação
       └── aprovado
             ↓
       matriz Android emulator + Android physical
              + iOS simulator + iOS physical
             ├── finding → correção focal → repetir matriz
             └── aprovado
                   ↓
             aceite humano
                   ├── rejeitado → implementação
                   └── aprovado
                         ↓
             backlog + FEATURES + CHANGELOG + resumo
                         ↓
                    gate final → ft close
```

O processo não possui uma branch “só Android” no fechamento. Qualquer mudança
do candidato reabre a prova dos targets obrigatórios, impedindo que o port seja
aceito às custas de uma regressão silenciosa no app iOS existente.
