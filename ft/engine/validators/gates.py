"""
Gate validators compostos.
Cada gate e uma combinacao de validadores basicos.
Retorna (passed: bool, detail: str).
"""

from __future__ import annotations

from pathlib import Path

from ft.engine.validators.artifacts import (
    file_exists,
    has_sections,
    min_lines,
    tests_pass,
    coverage_min,
)


def gate_delivery(
    outputs: list[str],
    project_root: str = ".",
) -> tuple[bool, str]:
    """Gate de delivery — verifica que todos os outputs existem e testes passam."""
    failures = []

    for output_path in outputs:
        passed, detail = file_exists(output_path, project_root)
        if not passed:
            failures.append(detail)

    t_passed, t_detail = tests_pass(project_root)
    if not t_passed:
        failures.append(t_detail)

    if failures:
        return False, f"gate_delivery FAIL: {'; '.join(failures)}"
    return True, f"gate_delivery: {len(outputs)} arquivos + testes OK"


def gate_smoke(
    project_root: str = ".",
    smoke_cmd: str | None = None,
) -> tuple[bool, str]:
    """Gate de smoke test — roda testes e opcionalmente um comando custom."""
    import subprocess

    t_passed, t_detail = tests_pass(project_root)
    if not t_passed:
        return False, f"gate_smoke FAIL: {t_detail}"

    if smoke_cmd:
        try:
            result = subprocess.run(
                smoke_cmd,
                shell=True,
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode != 0:
                return False, f"gate_smoke FAIL: comando '{smoke_cmd}' retornou {result.returncode}"
        except subprocess.TimeoutExpired:
            return False, f"gate_smoke FAIL: comando '{smoke_cmd}' timeout"

    return True, "gate_smoke: testes + smoke OK"


def gate_mvp(
    required_docs: list[str],
    min_coverage: int = 70,
    project_root: str = ".",
) -> tuple[bool, str]:
    """Gate MVP — verifica docs, testes e cobertura."""
    failures = []

    for doc_path in required_docs:
        passed, detail = file_exists(doc_path, project_root)
        if not passed:
            failures.append(detail)

    t_passed, t_detail = tests_pass(project_root)
    if not t_passed:
        failures.append(t_detail)

    c_passed, c_detail = coverage_min(min_coverage, project_root)
    if not c_passed:
        failures.append(c_detail)

    if failures:
        return False, f"gate_mvp FAIL: {'; '.join(failures)}"
    return True, f"gate_mvp: docs + testes + cobertura >= {min_coverage}% OK"


def gate_tdd_sequence(
    tdd_log: dict,
    project_root: str = ".",
) -> tuple[bool, str]:
    """Gate TDD — exige red phase antes de green (testes passando)."""
    if not tdd_log.get("red_phase_completed"):
        return False, "gate_tdd_sequence FAIL: red phase não foi completada"
    if not tdd_log.get("tests_passing"):
        return False, "gate_tdd_sequence FAIL: testes não estão passando"
    return True, "gate_tdd_sequence: OK — red→green sequencial confirmado"


def gate_coverage_80(
    project_root: str = ".",
) -> tuple[bool, str]:
    """Gate de cobertura — bloqueia se cobertura < 80%."""
    passed, detail = coverage_min(80, project_root)
    if not passed:
        return False, f"gate_coverage_80 FAIL: {detail}"
    return True, f"gate_coverage_80: PASS — {detail}"


def gate_e2e_all_pass(
    scenarios: list[dict],
    project_root: str = ".",
) -> tuple[bool, str]:
    """Gate E2E — falha se qualquer cenário não passar ou lista vazia."""
    if not scenarios:
        return False, "gate_e2e_all_pass FAIL: lista de cenários vazia"
    failed = [s["id"] for s in scenarios if not s.get("passed")]
    if failed:
        return False, f"gate_e2e_all_pass FAIL: cenários falharam: {', '.join(failed)}"
    return True, f"gate_e2e_all_pass: PASS — {len(scenarios)} cenários OK"


def gate_server_starts(
    project_root: str = ".",
    skip_if_interface: str | None = None,
) -> tuple[bool, str]:
    """Verifica que o backend tem entry point HTTP e consegue iniciar.

    skip_if_interface: se o artifacts/interface_type bater com esse valor, pula o gate.
    """
    import subprocess
    import time

    # Verificar interface_type salvo nos artefatos
    if skip_if_interface:
        itype_file = Path(project_root) / "project/docs/tech_stack.md"
        if itype_file.exists():
            content = itype_file.read_text().lower()
            if skip_if_interface.lower() in content:
                return True, f"gate_server_starts: pulado (interface_type={skip_if_interface})"
        # Também checar engine_state
        state_file = Path(project_root) / "project/state/engine_state.yml"
        if state_file.exists():
            state_content = state_file.read_text()
            if f"interface_type: {skip_if_interface}" in state_content:
                return True, f"gate_server_starts: pulado (interface_type={skip_if_interface})"

    root = Path(project_root)

    # Procura entry points conhecidos — raiz tem prioridade sobre src/
    # (src/main.py pode ser scaffold interno, não o servidor HTTP)
    http_keywords = ("FastAPI", "Flask", "Starlette", "uvicorn", "import app")
    candidates = [
        "main.py",
        "app.py",
        "server.py",
        "backend/main.py",
        "backend/app.py",
        "backend/server.py",
        "src/backend/app/main.py",
        "src/main.py",
        "src/app.py",
        "src/server.py",
    ]
    entry = next(
        (c for c in candidates
         if (root / c).exists()
         and any(kw in (root / c).read_text(errors="ignore") for kw in http_keywords)),
        None,
    )
    if not entry:
        return False, "gate_server_starts FAIL: nenhum entry point HTTP encontrado (main.py / app.py / server.py)"

    # Verifica que tem FastAPI ou Flask no arquivo
    content = (root / entry).read_text()
    if not any(kw in content for kw in ("FastAPI", "Flask", "Starlette", "app =", "uvicorn", "import app")):
        return False, f"gate_server_starts FAIL: {entry} nao parece um servidor HTTP (FastAPI/Flask nao encontrado)"

    # Tenta subir o servidor em porta temporaria e bater em /health ou /
    try:
        proc = subprocess.Popen(
            ["python", "-m", "uvicorn", entry.replace("/", ".").replace(".py", "") + ":app",
             "--host", "127.0.0.1", "--port", "18765", "--timeout-keep-alive", "1"],
            cwd=project_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        time.sleep(3)
        check = subprocess.run(
            ["curl", "-sf", "http://127.0.0.1:18765/health"],
            capture_output=True, timeout=5,
        )
        proc.terminate()
        proc.wait(timeout=5)
        if check.returncode == 0:
            return True, f"gate_server_starts: {entry} sobe e responde em /health"
        # Tenta / como fallback
        check2 = subprocess.run(
            ["curl", "-sf", "http://127.0.0.1:18765/"],
            capture_output=True, timeout=5,
        )
        proc2_ok = check2.returncode == 0
        return (True, f"gate_server_starts: {entry} sobe OK") if proc2_ok else \
               (False, f"gate_server_starts FAIL: {entry} sobe mas nao responde em / nem /health")
    except Exception as exc:
        try:
            proc.terminate()
        except Exception:
            pass
        return False, f"gate_server_starts FAIL: erro ao iniciar servidor — {exc}"


def gate_kb_review(project_root: str = ".") -> tuple[bool, str]:
    """
    Gate final de liberação — verifica que o projeto não repete pitfalls
    documentados na KB de runs anteriores.

    Checks derivados das avaliações:
      SM4:    interface_type=ui/mixed → frontend deve existir (package.json + index.html)
      SM5:    interface_type=mixed/api → entry point HTTP deve existir (FastAPI/Flask)
      SM5:    interface_type=mixed → vite.config.js deve ter proxy para backend
      SM5:    interface_type=ui + Python backend = provável mixed
      SM6-P4: frontend sem BrowserRouter/Route path → deep links quebrados
      SM6-P5: frontend-prd-review.md com REJECTED → nav contract não validado
    """
    # Derivar ft_root a partir de gates.py: ft/engine/validators/gates.py → 4 níveis acima
    ft_root = Path(__file__).resolve().parent.parent.parent.parent

    # Ler interface_type do tech_stack.md
    root = Path(project_root)
    tech_stack = root / "project/docs/tech_stack.md"
    interface_type = "unknown"
    if tech_stack.exists():
        import re
        m = re.search(r"interface_type:\s*(\w+)", tech_stack.read_text())
        if m:
            interface_type = m.group(1).lower()

    # Verificar KB disponível
    kb_dir = ft_root / "kb"
    kb_entries = sorted(kb_dir.glob("avaliacao_e2e_*.md")) if kb_dir.exists() else []
    kb_count = len(kb_entries)

    failures = []

    # ── Pitfall SM4: sem frontend apesar de interface_type exigir UI ──────────
    if interface_type in ("ui", "mixed"):
        for path in ("frontend/package.json", "frontend/index.html"):
            if not (root / path).exists():
                failures.append(
                    f"KB-SM4: interface_type={interface_type} exige frontend, "
                    f"mas '{path}' não encontrado"
                )

    # ── Pitfall SM5: sem entry point HTTP apesar de interface_type exigir server ──
    if interface_type in ("mixed", "api"):
        _http_kw = ("FastAPI", "Flask", "Starlette", "uvicorn", "import app")
        http_candidates = [
            "main.py",
            "app.py",
            "server.py",
            "backend/main.py",
            "backend/app.py",
            "backend/server.py",
            "src/backend/app/main.py",
            "src/main.py",
            "src/app.py",
            "src/server.py",
        ]
        entry = next(
            (c for c in http_candidates
             if (root / c).exists()
             and any(kw in (root / c).read_text(errors="ignore") for kw in _http_kw)),
            None,
        )
        if not entry:
            failures.append(
                f"KB-SM5: interface_type={interface_type} exige servidor HTTP, "
                f"mas nenhum entry point (main.py/app.py) encontrado"
            )
        else:
            content = (root / entry).read_text()
            if not any(kw in content for kw in ("FastAPI", "Flask", "Starlette", "app =", "uvicorn", "import app")):
                failures.append(
                    f"KB-SM5: {entry} existe mas não parece servidor HTTP "
                    f"(FastAPI/Flask/Starlette não encontrado)"
                )

    # ── Pitfall SM5: proxy do Vite ausente em projetos mixed ─────────────────
    if interface_type == "mixed":
        vite_cfg = root / "frontend/vite.config.js"
        if vite_cfg.exists():
            if "proxy" not in vite_cfg.read_text():
                failures.append(
                    "KB-SM5: vite.config.js sem configuração de proxy — "
                    "frontend não conseguirá se comunicar com o backend"
                )
        else:
            failures.append(
                "KB-SM5: frontend/vite.config.js ausente — "
                "proxy do Vite não configurado"
            )

    # ── Pitfall SM5: interface_type=ui mas código Python de backend existe (deveria ser mixed) ──
    if interface_type == "ui":
        backend_dirs = [root / d for d in ("src", "backend") if (root / d).is_dir()]
        py_files_exist = any(list(d.glob("**/*.py")) for d in backend_dirs)
        frontend_exists = (root / "frontend").is_dir()
        if frontend_exists and py_files_exist:
            failures.append(
                "KB-SM5: interface_type=ui mas existem frontend/ E src/ com Python — "
                "provável interface_type=mixed; sem essa correção gate_server_starts não "
                "verificará se o backend HTTP está funcional"
            )

    # ── Pitfall SM6-P4: routing sem URL change — deep links quebrados ──────────
    if interface_type in ("ui", "mixed"):
        frontend_src = root / "frontend/src"
        if frontend_src.is_dir():
            import re as _re
            path_routing_found = False
            for f in list(frontend_src.rglob("*.jsx")) + list(frontend_src.rglob("*.tsx")) + list(frontend_src.rglob("*.js")):
                try:
                    content = f.read_text(errors="ignore")
                    if _re.search(r'(BrowserRouter|HashRouter|createBrowserRouter|<Route\s+path=)', content):
                        path_routing_found = True
                        break
                except Exception:
                    continue
            if not path_routing_found:
                failures.append(
                    "KB-P4: frontend sem roteamento baseado em URL (BrowserRouter/Route path não encontrado) — "
                    "deep links e navegação direta por URL não funcionarão; "
                    "use BrowserRouter com <Route path=...> para cada tela"
                )

    # ── Pitfall SM6-P5: prd_review REJECTED sem correção posterior ──────────────
    if interface_type in ("ui", "mixed"):
        prd_review = root / "project/docs/frontend-prd-review.md"
        if prd_review.exists():
            import re as _re
            review_content = prd_review.read_text()
            # Verifica se o veredicto final é REJECTED (não APPROVED)
            if _re.search(r'Veredicto:\s*REJECTED', review_content, _re.IGNORECASE):
                failures.append(
                    "KB-P5: frontend-prd-review.md com veredicto REJECTED — "
                    "conformidade de navegação (nav contract) não foi validada; "
                    "corrija os itens rejeitados antes de liberar"
                )

    if failures:
        return False, (
            f"gate_kb_review FAIL ({kb_count} avaliações na KB, "
            f"interface_type={interface_type}): " + "; ".join(failures)
        )

    return True, (
        f"gate_kb_review: PASS — {kb_count} pitfalls da KB verificados, "
        f"interface_type={interface_type}, nenhum replicado"
    )


def gate_acceptance_cli(project_root: str = ".") -> tuple[bool, str]:
    """Gate de acceptance CLI — verifica que o relatório existe e não tem FAILs."""
    import re

    report = Path(project_root) / "project/docs/acceptance-cli-report.md"
    if not report.exists():
        return False, "gate_acceptance_cli FAIL: acceptance-cli-report.md não encontrado"

    content = report.read_text()
    lines = [l for l in content.splitlines() if l.strip()]
    if len(lines) < 10:
        return False, f"gate_acceptance_cli FAIL: relatório muito curto ({len(lines)} linhas)"

    fail_lines = [l.strip() for l in content.splitlines() if re.search(r'\[FAIL\]', l)]
    if fail_lines:
        preview = "; ".join(fail_lines[:3])
        return False, (
            f"gate_acceptance_cli FAIL: {len(fail_lines)} falha(s) na API — {preview}"
        )

    return True, "gate_acceptance_cli: PASS — todos os registros aceitos pela API"


def gate_pulse_instrumented(project_root: str = ".") -> tuple[bool, str]:
    """Gate de ForgeBase Pulse — bloqueia se nenhum track estiver instrumentado no código.

    Procura referências ao SDK do ForgeBase Pulse em src/ e frontend/src/:
    - UseCaseRunner (track de negócio)
    - track-infra / track-erro / track-negocio / track-perf / track-dx
    - forge_pulse / forgepulse / ForgeBase (import direto)

    Se interface_type=cli_only, verifica apenas src/.
    Se nenhuma referência encontrada, retorna FAIL com orientação de implementação.
    """
    import re

    root = Path(project_root)
    patterns = [
        r'UseCaseRunner',
        r'track-infra',
        r'track-erro',
        r'track-negocio',
        r'track-perf',
        r'track-dx',
        r'forge_pulse',
        r'forgepulse',
        r'ForgeBase',
    ]
    combined = re.compile("|".join(patterns))

    search_dirs = []
    for candidate in ("src", "backend", "frontend/src"):
        d = root / candidate
        if d.is_dir():
            search_dirs.append(d)

    found_in = []
    for search_dir in search_dirs:
        for ext in ("*.py", "*.js", "*.jsx", "*.ts", "*.tsx"):
            for f in search_dir.rglob(ext):
                try:
                    if combined.search(f.read_text(errors="ignore")):
                        found_in.append(str(f.relative_to(root)))
                        break
                except Exception:
                    continue
        if found_in:
            break

    if not found_in:
        return False, (
            "gate_pulse_instrumented FAIL: nenhum track do ForgeBase Pulse encontrado em src/ "
            "nem frontend/src/. Implemente ao menos track-infra e track-negocio antes do gate.audit. "
            "Ver plano_de_voo.md §Correções obrigatórias para a ordem de implementação recomendada."
        )

    return True, f"gate_pulse_instrumented: PASS — Pulse encontrado em {found_in[0]}"


def screenshot_review_passed(project_root: str = ".") -> tuple[bool, str]:
    """Verifica que o relatório de screenshot review existe e contém Veredicto: APPROVED
    sem critérios não avaliados (linhas com '[ ]').
    """
    import re

    report = Path(project_root) / "project/docs/screenshot-review.md"
    if not report.exists():
        return False, "screenshot_review_passed FAIL: screenshot-review.md não encontrado"

    content = report.read_text()

    if not re.search(r"Veredicto:\s*APPROVED", content, re.IGNORECASE):
        return False, "screenshot_review_passed FAIL: veredicto não é APPROVED"

    pending = [l.strip() for l in content.splitlines() if re.search(r'\[ \]', l)]
    if pending:
        preview = "; ".join(pending[:3])
        return False, (
            f"screenshot_review_passed FAIL: {len(pending)} critério(s) não avaliado(s) — {preview}"
        )

    return True, "screenshot_review_passed: PASS — veredicto APPROVED, todos os critérios avaliados"


def gate_frontend(project_root: str = ".") -> tuple[bool, str]:
    """Gate de frontend — verifica estrutura minima de PWA."""
    import json
    failures = []
    for path in ["frontend/package.json", "frontend/index.html", "frontend/public/manifest.json", "frontend/src/"]:
        full = Path(project_root) / path
        if not full.exists():
            failures.append(f"{path} nao encontrado")
    manifest = Path(project_root) / "frontend/public/manifest.json"
    if manifest.exists():
        try:
            data = json.loads(manifest.read_text())
            for field in ("name", "start_url", "display"):
                if field not in data:
                    failures.append(f"manifest.json sem campo '{field}'")
        except Exception:
            failures.append("manifest.json invalido")
    if failures:
        return False, f"gate_frontend FAIL: {'; '.join(failures)}"
    return True, "gate_frontend: estrutura PWA OK"
