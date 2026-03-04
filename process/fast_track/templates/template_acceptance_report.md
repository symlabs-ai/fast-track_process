# Acceptance Report — Cycle XX

## Interface testada
- Tipo: [CLI | API | UI | Mixed]
- Ferramenta: [Playwright | httpx | requests | shell | Chrome automation]
- URL/endpoint base: [...]

## Ambiente de execução (teste final)
- Tipo de ambiente: [produção | staging | produção local]
- Build: [comando usado — ex: `npm run build`, `python -m build`]
- Servidor: [Nginx | Gunicorn | `serve -s build` | outro]
- HTTPS: [sim — certificado real / sim — localhost cert / não]
- Variáveis de ambiente: [.env.production | staging vars | produção real]
- Playwright mode: [headed ✅ | headless ⚠️]
- Screenshots/vídeo: [path para evidências visuais]
- Como reproduzir: [comandos para subir o ambiente e rodar os testes]

> ⚠️ A execução final do gate deve usar build de produção no ambiente do cliente.
> Testes durante o dev contra `npm run dev` são aceitáveis, mas não contam como execução final.
> UI tests devem rodar com Playwright headed (browser visível).

## Mapeamento ACs → Testes

| US | AC | Descrição (Given/When/Then) | Test file | Status |
|----|-----|---------------------------|-----------|--------|
| US-01 | AC-01.1 | Given ... When ... Then ... | test_us01_ac01.py:test_happy_path | PASS / FAIL |
| US-01 | AC-01.2 | Given ... When ... Then ... | test_us01_ac02.py:test_edge_case | PASS / FAIL |

## Value Tracks cobertos

| Track | Fluxo testado | Test file | Status |
|-------|--------------|-----------|--------|
| vt-01 | [descrição do fluxo] | test_vt01_flow.py | PASS / FAIL |

## Resumo
- Total ACs: X
- Cobertos: Y (Z%)
- Pendentes: [listar ACs não cobertos, se houver — meta é 0]
- Value Tracks testados: A / B
- Status: **APROVADO** / **REPROVADO**

## Evidência de Execução Real
- Servidor/UI rodando em: [URL:porta — deve ser build de produção, não dev server]
- Comando de execução dos testes: [ex: pytest tests/acceptance/ -v]
- Tempo total de execução: [X segundos]
- Screenshots/logs: [path ou "inline abaixo"]

> ⚠️ Este report é INVÁLIDO se:
> - Os testes não interagiram com a aplicação rodando
> - Testes fazem grep em arquivos ou verificam existência de arquivos
> - Testes rodaram contra servidor de desenvolvimento (`npm run dev`, `flask run --debug`, etc.)
> - PWA testada sem HTTPS

## Observações
[Comportamentos inesperados, edge cases detectados, notas de ambiente]
