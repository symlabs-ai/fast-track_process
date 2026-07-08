"""
Validadores deterministicos de artefatos.
Cada funcao retorna (passed: bool, detail: str).
"""

from __future__ import annotations

import ast
import hashlib
import re
import subprocess
import unicodedata
from pathlib import Path


def _normalize(text: str) -> str:
    """Remove diacritics for accent-insensitive matching."""
    return unicodedata.normalize("NFD", text).encode("ascii", "ignore").decode("ascii").lower()


def _normalize_block(text: str) -> str:
    """Normaliza bloco de texto para comparação determinística."""
    return "\n".join(line.rstrip() for line in text.strip().splitlines())


def _extract_markdown_section(content: str, section: str) -> str | None:
    """Extrai uma seção Markdown pelo heading, incluindo subseções internas."""
    lines = content.splitlines()
    in_code_block = False
    target_start = None
    target_level = None

    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue

        match = re.match(r"^(#{1,6})\s+(.*)$", line)
        if not match:
            continue

        level = len(match.group(1))
        title = match.group(2).strip()
        norm_title = _normalize(title)
        norm_section = _normalize(section)
        if norm_title == norm_section or norm_section in norm_title:
            target_start = idx
            target_level = level
            break

    if target_start is None or target_level is None:
        return None

    in_code_block = False
    target_end = len(lines)
    for idx in range(target_start + 1, len(lines)):
        stripped = lines[idx].strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue

        match = re.match(r"^(#{1,6})\s+(.*)$", lines[idx])
        if match and len(match.group(1)) <= target_level:
            target_end = idx
            break

    return "\n".join(lines[target_start:target_end]).strip()


def file_exists(path: str, project_root: str = ".") -> tuple[bool, str]:
    """Verifica se arquivo existe."""
    full = Path(project_root) / path
    if full.exists():
        return True, f"file_exists: {path}"
    return False, f"file_exists FAIL: {path} nao encontrado"


def min_lines(path: str, n: int, project_root: str = ".") -> tuple[bool, str]:
    """Verifica se arquivo tem pelo menos N linhas."""
    full = Path(project_root) / path
    if not full.exists():
        return False, f"min_lines FAIL: {path} nao existe"
    lines = len(full.read_text().splitlines())
    if lines >= n:
        return True, f"min_lines: {path} tem {lines} linhas (min {n})"
    return False, f"min_lines FAIL: {path} tem {lines} linhas (min {n})"


def has_sections(path: str = "", sections: list[str] = None, project_root: str = ".", file: str = "") -> tuple[bool, str]:
    """Verifica se arquivo contem as secoes esperadas.
    Aceita 'path' ou 'file' como nome do argumento (aliases).
    """
    if sections is None:
        sections = []
    effective_path = file or path
    full = Path(project_root) / effective_path
    if not full.exists():
        return False, f"has_sections FAIL: {effective_path} nao existe"
    content = full.read_text()
    norm_content = _normalize(content)
    missing = [s for s in sections if _normalize(s) not in norm_content]
    if not missing:
        return True, f"has_sections: {effective_path} tem todas as {len(sections)} secoes"
    return False, f"has_sections FAIL: {effective_path} faltam secoes: {missing}"


def document_quality(
    path: str = "",
    project_root: str = ".",
    file: str = "",
    min_lines_count: int = 8,
    max_lines_count: int | None = None,
    forbidden: list[str] | None = None,
    required_terms: list[str] | None = None,
    min_required_terms: int | None = None,
) -> tuple[bool, str]:
    """Barreira genérica contra artefatos que são eco de prompt/tool call.

    Não tenta julgar conteúdo de produto; só garante que o documento tem corpo
    mínimo e não contém marcas comuns de resposta incompleta do agente.
    """
    effective_path = file or path
    full = Path(project_root) / effective_path
    if not full.exists():
        return False, f"document_quality FAIL: {effective_path} nao existe"

    content = full.read_text(encoding="utf-8", errors="ignore")
    nonblank = [line for line in content.splitlines() if line.strip()]
    if len(nonblank) < min_lines_count:
        return False, (
            f"document_quality FAIL: {effective_path} tem {len(nonblank)} linhas uteis "
            f"(min {min_lines_count})"
        )
    if max_lines_count is not None and len(nonblank) > max_lines_count:
        return False, (
            f"document_quality FAIL: {effective_path} tem {len(nonblank)} linhas uteis "
            f"(max {max_lines_count})"
        )

    forbidden_terms = forbidden or [
        "<tool_call",
        "</tool_call",
        "<arg_key",
        "<arg_value",
        "i'll help",
        "let me first",
        "i notice",
        "as an ai",
    ]
    norm_content = _normalize(content)
    found_forbidden = [term for term in forbidden_terms if _normalize(term) in norm_content]
    if found_forbidden:
        return False, f"document_quality FAIL: {effective_path} contem ruido de execucao: {found_forbidden[:5]}"

    if required_terms:
        matched = [term for term in required_terms if _normalize(term) in norm_content]
        minimum = min_required_terms if min_required_terms is not None else len(required_terms)
        if len(matched) < minimum:
            missing = [term for term in required_terms if term not in matched]
            return False, (
                f"document_quality FAIL: {effective_path} cobre {len(matched)}/{minimum} "
                f"termos obrigatorios; faltam: {missing[:6]}"
            )

    return True, f"document_quality: {effective_path} tem {len(nonblank)} linhas uteis"


def api_contract_complete(
    path: str = "docs/api_contract.md",
    project_root: str = ".",
    min_endpoints: int = 3,
    require_health: bool = True,
    require_post_for_create: bool = True,
) -> tuple[bool, str]:
    """Valida que o contrato de API tem endpoints acionáveis, não só headings."""
    full = Path(project_root) / path
    if not full.exists():
        return False, f"api_contract_complete FAIL: {path} nao existe"

    content = full.read_text(encoding="utf-8", errors="ignore")
    endpoint_matches: set[tuple[str, str]] = set()
    patterns = [
        r"(?im)^\s*\|\s*`?(GET|POST|PUT|PATCH|DELETE)`?\s*\|\s*`?(/[^\s|`]*)`?",
        r"(?im)^\s*(?:\*\*)?`?(GET|POST|PUT|PATCH|DELETE)`?\s+`?(/[^\s`*:]*)(?:`|\*\*)?",
        r"(?im)^\s*[-*]\s*(?:\*\*)?`?(GET|POST|PUT|PATCH|DELETE)`?\s+`?(/[^\s`*:]*)(?:`|\*\*)?\s*:",
        r"(?im)^\s*`?(GET|POST|PUT|PATCH|DELETE)`?\s+`?(/[^\s|`:]*)`?",
        r"(?im)\b`?(GET|POST|PUT|PATCH|DELETE)`?\b\s*\|\s*`?(/[^\s|`]*)`?",
    ]
    for pattern in patterns:
        for method, endpoint in re.findall(pattern, content):
            normalized = endpoint.strip().rstrip(".,;")
            if normalized != "/":
                normalized = normalized.rstrip("/")
            endpoint_matches.add((method.upper(), normalized))

    root_methods = sorted({method for method, endpoint in endpoint_matches if endpoint == "/"})
    if root_methods:
        return False, (
            "api_contract_complete FAIL: endpoint '/' nao e contrato acionavel; "
            "use /health para health e paths concretos como /api/<recurso> "
            f"para produto (methods={root_methods})"
        )

    non_health = {(method, endpoint) for method, endpoint in endpoint_matches if endpoint != "/health"}
    if len(non_health) < min_endpoints:
        return False, (
            f"api_contract_complete FAIL: {path} tem {len(non_health)} endpoint(s) de produto "
            f"(min {min_endpoints})"
        )

    if require_health and not any(endpoint == "/health" for _, endpoint in endpoint_matches):
        return False, "api_contract_complete FAIL: falta endpoint /health"

    if require_post_for_create:
        docs_text = ""
        for name in ("PRD.md", "ui_criteria.md", "task_list.md"):
            candidate = Path(project_root) / "docs" / name
            if candidate.exists():
                docs_text += "\n" + candidate.read_text(encoding="utf-8", errors="ignore")
        create_terms = ["criar", "cadastrar", "adicionar", "novo", "nova", "create", "add"]
        if any(term in _normalize(docs_text) for term in create_terms) and not any(
            method == "POST" for method, _ in endpoint_matches
        ):
            return False, "api_contract_complete FAIL: produto exige criacao mas contrato nao tem POST"

    return True, f"api_contract_complete: {len(non_health)} endpoint(s), methods={sorted({m for m, _ in endpoint_matches})}"


def relative_dates_only(path: str = "docs/test_data.md", project_root: str = ".") -> tuple[bool, str]:
    """Garante que massa de dados use datas relativas em vez de hardcode absoluto."""
    full = Path(project_root) / path
    if not full.exists():
        return False, f"relative_dates_only FAIL: {path} nao existe"
    content = full.read_text(encoding="utf-8", errors="ignore")
    absolute_patterns = [
        r"\b20\d{2}\s*[-‑–/]\s*\d{1,2}(?:\s*[-‑–/]\s*\d{1,2})?",
        r"\b\d{1,2}/\d{1,2}/20\d{2}\b",
    ]
    for pattern in absolute_patterns:
        match = re.search(pattern, content)
        if match:
            return False, f"relative_dates_only FAIL: {path} contem data absoluta: {match.group(0)}"
    norm = _normalize(content)
    relative_terms = ["hoje", "today", "amanha", "ontem", "d+", "d-", "semana atual"]
    if not any(term in norm for term in relative_terms):
        return False, f"relative_dates_only FAIL: {path} nao menciona datas relativas"
    return True, f"relative_dates_only: {path} usa datas relativas"


def _extract_ui_criteria(content: str) -> list[tuple[str, str]]:
    """Extrai critérios identificados do ui_criteria.md.

    O formato recomendado é uma linha por critério:
    - [ ] C01: texto do critério
    - C13: texto do critério
    - UI-02 - texto do critério
    """
    criteria: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line in content.splitlines():
        stripped = line.strip()
        match = re.match(
            r"^(?:[-*]\s*)?(?:\[[ xX]\]\s*)?([A-Z]{1,4}-?\d{1,3})\s*[:\-–]\s+(.+)$",
            stripped,
        )
        if not match:
            continue
        code = match.group(1).upper().replace("-", "")
        canonical_code = _canonical_ui_criterion_code(code)
        text = match.group(2).strip()
        if not text or canonical_code in seen:
            continue
        seen.add(canonical_code)
        criteria.append((code, text))
    return criteria


def _criterion_report_line(report: str, code: str) -> str:
    canonical_code = _canonical_ui_criterion_code(code)
    for line in report.splitlines():
        if canonical_code in _extract_criterion_codes(line):
            return _normalize(line)
    return ""


def _canonical_ui_criterion_code(raw: str) -> str:
    code = raw.upper().replace("-", "")
    match = re.fullmatch(r"([A-Z]{1,4})0*(\d{1,3})", code)
    if match:
        return f"{match.group(1)}{int(match.group(2))}"
    return code


def _criterion_report_status_text(line: str, code: str) -> str:
    """Retorna a célula de status de uma linha Markdown quando houver tabela."""
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    if len(cells) < 2:
        return line

    canonical_code = _canonical_ui_criterion_code(code)
    for index, cell in enumerate(cells):
        if canonical_code in _extract_criterion_codes(cell):
            if index + 1 < len(cells):
                return cells[index + 1]
            break
    return line


def _extract_criterion_codes(text: str) -> set[str]:
    return {
        _canonical_ui_criterion_code(match.group(0))
        for match in re.finditer(r"\b[A-Z]{1,4}-?\d{1,3}\b", text, re.IGNORECASE)
    }


def _source_criteria_codes(source_text: str) -> set[str]:
    """Extrai marcadores explícitos de cobertura de critério no fonte.

    Formatos aceitos:
    - data-ui-criteria="C01 C02"
    - ui-criteria: C01, C02
    """
    codes: set[str] = set()
    for match in re.finditer(
        r"data-ui-criteria\s*=\s*([\"'])(.*?)\1",
        source_text,
        re.IGNORECASE | re.DOTALL,
    ):
        codes.update(_extract_criterion_codes(match.group(2)))
    for match in re.finditer(r"ui-criteria\s*:\s*([^\n\r<]+)", source_text, re.IGNORECASE):
        codes.update(_extract_criterion_codes(match.group(1)))
    return codes


def ui_criteria_ids(
    path: str = "docs/ui_criteria.md",
    min_count: int = 5,
    project_root: str = ".",
) -> tuple[bool, str]:
    """Verifica que ui_criteria.md existe e possui IDs estáveis de critérios."""
    criteria_file = Path(project_root) / path
    if not criteria_file.exists():
        return False, f"ui_criteria_ids FAIL: {path} nao encontrado"
    criteria = _extract_ui_criteria(criteria_file.read_text(encoding="utf-8", errors="ignore"))
    if len(criteria) < min_count:
        return False, (
            f"ui_criteria_ids FAIL: {path} tem {len(criteria)} criterios identificados "
            f"(min {min_count}); use IDs como C01, C02, UI-01"
        )
    return True, f"ui_criteria_ids: {path} tem {len(criteria)} criterios identificados"


def _ui_component_requirements(criteria_text: str) -> list[tuple[str, re.Pattern[str]]]:
    """Detecta componentes de UI comuns citados no critério.

    Isso é uma camada genérica e deliberadamente conservadora: se o critério
    mencionar um componente reconhecido, o fonte precisa conter alguma evidência
    estrutural daquele tipo. A cobertura semântica completa continua sendo do
    screenshot/visual review, mas o engine deixa de aceitar um relatório que
    ignora completamente componentes pedidos no contrato de UI.
    """
    norm = _normalize(criteria_text)
    specs = [
        (
            "FAB",
            (r"\bfab\b", r"floating action button", r"botao flutuante"),
            r"\bfab\b|floating|data-testid=[\"'][^\"']*fab",
        ),
        (
            "menu suspenso/dropdown",
            (r"menu suspenso", r"dropdown", r"\bselect\b", r"combobox"),
            r"<select\b|role=[\"']combobox|dropdown|menu-suspenso|data-testid=[\"'][^\"']*(select|dropdown|menu)",
        ),
        (
            "modal/dialog",
            (r"\bmodal\b", r"\bdialog\b", r"dialogo"),
            r"<dialog\b|role=[\"']dialog|modal",
        ),
        (
            "tabs/abas",
            (r"\btabs?\b", r"\babas?\b", r"tablist"),
            r"role=[\"']tab|role=[\"']tablist|\btabs?\b|\btablist\b",
        ),
        (
            "toggle/switch",
            (r"\btoggle\b", r"\bswitch\b", r"alternador"),
            r"role=[\"']switch|type=[\"']checkbox|toggle|switch",
        ),
        ("checkbox", (r"checkbox", r"caixa de selecao"), r"type=[\"']checkbox|checkbox"),
        ("radio", (r"\bradio\b", r"opcao unica"), r"type=[\"']radio|\bradio\b"),
        (
            "slider",
            (r"\bslider\b", r"controle deslizante"),
            r"type=[\"']range|role=[\"']slider|\bslider\b",
        ),
        ("tooltip", (r"\btooltip\b", r"dica de contexto"), r"tooltip|aria-describedby"),
        ("ícone SVG", (r"icone svg", r"icones svg", r"svg"), r"<svg\b|\.svg\b"),
        ("estado vazio", (r"estado vazio", r"empty state"), r"estado vazio|empty state|\bvazio\b|\bempty\b"),
    ]
    requirements: list[tuple[str, re.Pattern[str]]] = []
    for label, triggers, source_pattern in specs:
        if any(re.search(trigger, norm) for trigger in triggers):
            requirements.append((label, re.compile(source_pattern, re.IGNORECASE)))
    return requirements


def ui_criteria_coverage(
    criteria_path: str = "docs/ui_criteria.md",
    report_path: str | None = "docs/screenshot-review.md",
    source_dir: str | None = None,
    evidence: str = "any",
    project_root: str = ".",
) -> tuple[bool, str]:
    """Verifica cobertura genérica dos critérios de interface.

    Regras:
    - `docs/ui_criteria.md` deve ter critérios identificáveis (`C01:`, `UI-02:`).
    - `evidence=report`: o relatório deve citar cada ID com PASS/OK/APROVADO/CONFORME.
    - `evidence=code`: o fonte deve marcar cada ID com `data-ui-criteria` ou
      comentário `ui-criteria:`.
    - `evidence=both`: exige relatório e código para cada critério.
    - `evidence=any`: aceita relatório ou código para cada critério.
    - Quando código é usado como evidência, componentes comuns citados no
      critério precisam ter evidência estrutural no fonte.
    """
    root = Path(project_root)
    criteria_file = root / criteria_path
    if not criteria_file.exists():
        return False, f"ui_criteria_coverage FAIL: {criteria_path} nao encontrado"

    criteria = _extract_ui_criteria(criteria_file.read_text(encoding="utf-8", errors="ignore"))
    if not criteria:
        return False, (
            "ui_criteria_coverage FAIL: nenhum criterio identificado em "
            f"{criteria_path}; use IDs como C01, C02, UI-01"
        )

    mode = _normalize(evidence or "any").strip()
    if mode not in {"any", "report", "code", "both"}:
        return False, "ui_criteria_coverage FAIL: evidence deve ser any, report, code ou both"

    needs_report = mode in {"report", "both"}
    needs_code = mode in {"code", "both"}
    if needs_report and not report_path:
        return False, "ui_criteria_coverage FAIL: evidence exige report_path"
    if needs_code and not source_dir:
        return False, "ui_criteria_coverage FAIL: evidence exige source_dir"
    if mode == "any" and not report_path and not source_dir:
        return False, "ui_criteria_coverage FAIL: informe report_path ou source_dir"

    report = ""
    report_available = False
    if report_path:
        report_file = root / report_path
        if report_file.exists():
            report = report_file.read_text(encoding="utf-8", errors="ignore")
            report_available = True
        elif needs_report or (mode == "any" and not source_dir):
            return False, f"ui_criteria_coverage FAIL: {report_path} nao encontrado"

    source_text = ""
    source_codes: set[str] = set()
    source_available = False
    if source_dir:
        source_root = root / source_dir
        if not source_root.exists():
            return False, f"ui_criteria_coverage FAIL: source_dir {source_dir} nao encontrado"
        source_text = "\n".join(
            p.read_text(encoding="utf-8", errors="ignore")
            for p in source_root.rglob("*")
            if p.is_file()
        )
        source_codes = _source_criteria_codes(source_text)
        source_available = True

    pass_markers = ("pass", "ok", "approved", "aprov", "atendid", "conforme")
    fail_markers = ("fail", "reprov", "nao atend", "pendente", "missing", "ausente")

    def report_status(code: str) -> tuple[bool, str]:
        if not report_available:
            return False, "relatorio ausente"
        line = _criterion_report_line(report, code)
        if not line:
            return False, "sem linha no relatorio"
        status_text = _criterion_report_status_text(line, code)
        if any(marker in status_text for marker in fail_markers):
            return False, "linha do relatorio indica falha"
        if not any(marker in status_text for marker in pass_markers):
            return False, "linha do relatorio sem PASS/OK"
        return True, "relatorio"

    def code_status(code: str, text: str) -> tuple[bool, str]:
        if not source_available:
            return False, "fonte ausente"
        if _canonical_ui_criterion_code(code) not in source_codes:
            return False, "codigo sem marcador data-ui-criteria/ui-criteria"
        missing_components = [
            label
            for label, pattern in _ui_component_requirements(text)
            if not pattern.search(source_text)
        ]
        if missing_components:
            return False, "componentes sem evidencia no fonte: " + ", ".join(missing_components)
        return True, "codigo"

    failures: list[str] = []
    report_count = 0
    code_count = 0
    for code, text in criteria:
        report_ok, report_reason = report_status(code)
        code_ok, code_reason = code_status(code, text)

        if mode == "report":
            covered = report_ok
        elif mode == "code":
            covered = code_ok
        elif mode == "both":
            covered = report_ok and code_ok
        else:
            covered = report_ok or code_ok

        if covered:
            report_count += int(report_ok)
            code_count += int(code_ok)
            continue

        reasons: list[str] = []
        if mode in {"any", "report", "both"}:
            reasons.append(f"relatorio: {report_reason}")
        if mode in {"any", "code", "both"}:
            reasons.append(f"codigo: {code_reason}")
        failures.append(f"{code} ({'; '.join(reasons)})")

    if failures:
        return False, "ui_criteria_coverage FAIL: " + "; ".join(failures)
    return (
        True,
        "ui_criteria_coverage: "
        f"{len(criteria)} criterios cobertos "
        f"(relatorio={report_count}, codigo={code_count}, evidence={mode})",
    )


def min_user_stories(path: str, n: int, project_root: str = ".") -> tuple[bool, str]:
    """Conta user stories nos formatos: ### US- / ## US- / **US- / US-XX."""
    full = Path(project_root) / path
    if not full.exists():
        return False, f"min_user_stories FAIL: {path} nao existe"
    content = full.read_text()
    count = len(re.findall(r'(?:###?\s+US[-\s]|\*\*US-|^US-\d)', content, re.IGNORECASE | re.MULTILINE))
    if count >= n:
        return True, f"min_user_stories: {path} tem {count} user stories (min {n})"
    return False, f"min_user_stories FAIL: {path} tem {count} user stories (min {n})"


def tests_pass(project_root: str = ".") -> tuple[bool, str]:
    """Roda pytest e verifica se passa."""
    result = subprocess.run(
        ["python", "-m", "pytest", "--tb=short", "-q"],
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode == 0:
        # Extrair contagem de testes
        last_line = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
        return True, f"tests_pass: {last_line}"
    # Extrair resumo de falhas
    last_lines = result.stdout.strip().splitlines()[-3:] if result.stdout.strip() else []
    summary = " | ".join(last_lines)
    return False, f"tests_pass FAIL: {summary}"


def tests_fail(project_root: str = ".") -> tuple[bool, str]:
    """Verifica que testes FALHAM (TDD red phase)."""
    result = subprocess.run(
        ["python", "-m", "pytest", "--tb=short", "-q"],
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        return True, "tests_fail: testes falharam como esperado (red phase)"
    return False, "tests_fail FAIL: testes passaram — deviam falhar na red phase"


def _is_docstring_stmt(stmt: ast.stmt) -> bool:
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )


def _meaningful_test_body(body: list[ast.stmt]) -> list[ast.stmt]:
    meaningful: list[ast.stmt] = []
    for stmt in body:
        if _is_docstring_stmt(stmt):
            continue
        if isinstance(stmt, ast.Pass):
            continue
        if (
            isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Constant)
            and stmt.value.value is Ellipsis
        ):
            continue
        meaningful.append(stmt)
    return meaningful


def _is_pytest_raises_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "raises"
        and isinstance(func.value, ast.Name)
        and func.value.id == "pytest"
    )


def _test_assertion_count(func: ast.AST) -> int:
    count = 0
    for node in ast.walk(func):
        if isinstance(node, ast.Assert):
            count += 1
        elif _is_pytest_raises_call(node):
            count += 1
    return count


def pytest_red_quality(
    tests_dir: str = "project/tests",
    min_test_files: int = 1,
    min_tests: int = 3,
    min_assertions: int = 3,
    project_root: str = ".",
) -> tuple[bool, str]:
    """Valida qualidade estrutural de testes RED sem exigir que eles passem.

    O objetivo é bloquear stubs que apenas satisfazem existência/compilação:
    arquivos vazios, extensões erradas, funções `pass` e testes sem asserts.
    """
    root = Path(project_root)
    test_root = root / tests_dir
    if not test_root.exists():
        return False, f"pytest_red_quality FAIL: {tests_dir} nao encontrado"
    files = sorted(p for p in test_root.rglob("test_*.py") if p.is_file())
    if len(files) < min_test_files:
        return False, (
            f"pytest_red_quality FAIL: {len(files)} test_*.py encontrado(s) "
            f"(min {min_test_files})"
        )

    test_count = 0
    assertion_count = 0
    stub_tests: list[str] = []
    syntax_errors: list[str] = []
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"), filename=str(path))
        except SyntaxError as exc:
            syntax_errors.append(f"{path.relative_to(root)}:{exc.lineno}: {exc.msg}")
            continue

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test_"):
                continue
            test_count += 1
            assertion_count += _test_assertion_count(node)
            body = _meaningful_test_body(node.body)
            if not body:
                stub_tests.append(f"{path.relative_to(root)}::{node.name}")
                continue
            if len(body) == 1 and isinstance(body[0], ast.Return):
                value = body[0].value
                if isinstance(value, ast.Constant) and value.value in {True, None}:
                    stub_tests.append(f"{path.relative_to(root)}::{node.name}")

    if syntax_errors:
        return False, "pytest_red_quality FAIL: syntax error: " + "; ".join(syntax_errors[:5])
    if test_count < min_tests:
        return False, f"pytest_red_quality FAIL: {test_count} teste(s) encontrado(s) (min {min_tests})"
    if assertion_count < min_assertions:
        return False, (
            f"pytest_red_quality FAIL: {assertion_count} assert/pytest.raises encontrado(s) "
            f"(min {min_assertions})"
        )
    if stub_tests:
        return False, "pytest_red_quality FAIL: testes stub/pass-only: " + "; ".join(stub_tests[:5])
    return (
        True,
        f"pytest_red_quality: {len(files)} arquivo(s), {test_count} teste(s), "
        f"{assertion_count} assert/pytest.raises",
    )


def coverage_min(min_pct: int, project_root: str = ".") -> tuple[bool, str]:
    """Roda pytest com coverage e verifica minimo."""
    result = subprocess.run(
        ["python", "-m", "pytest", "--cov=src", "--cov-report=term-missing", "-q"],
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=120,
    )
    # Procurar linha TOTAL no output
    for line in result.stdout.splitlines():
        if "TOTAL" in line:
            match = re.search(r'(\d+)%', line)
            if match:
                pct = int(match.group(1))
                if pct >= min_pct:
                    return True, f"coverage_min: {pct}% (min {min_pct}%)"
                return False, f"coverage_min FAIL: {pct}% < {min_pct}%"
    return False, f"coverage_min FAIL: nao consegui extrair cobertura do output"


def read_artifact(path: str, key: str, pattern: str, project_root: str = ".") -> tuple[bool, str]:
    """Le arquivo e extrai valor via regex. Detail tem formato 'read_artifact: key=value'."""
    import re as _re
    full = Path(project_root) / path
    if not full.exists():
        return False, f"read_artifact FAIL: {path} nao encontrado"
    content = full.read_text()
    match = _re.search(pattern, content, _re.IGNORECASE | _re.MULTILINE)
    if not match:
        return False, f"read_artifact FAIL: padrao nao encontrado em {path}"
    value = match.group(1).strip().lower()
    return True, f"read_artifact: {key}={value}"


def sections_unchanged(
    path: str,
    snapshot_path: str,
    sections: list[str],
    project_root: str = ".",
) -> tuple[bool, str]:
    """Garante que seções críticas permaneçam idênticas ao baseline."""
    current_full = Path(project_root) / path
    snapshot_full = Path(project_root) / snapshot_path

    if not current_full.exists():
        return False, f"sections_unchanged FAIL: {path} nao encontrado"
    if not snapshot_full.exists():
        return False, f"sections_unchanged FAIL: baseline ausente em {snapshot_path}"

    current_content = current_full.read_text()
    snapshot_content = snapshot_full.read_text()
    changed: list[str] = []

    for section in sections:
        current_section = _extract_markdown_section(current_content, section)
        snapshot_section = _extract_markdown_section(snapshot_content, section)

        if snapshot_section is None:
            return False, (
                f"sections_unchanged FAIL: secao '{section}' ausente no baseline {snapshot_path}"
            )
        if current_section is None:
            return False, f"sections_unchanged FAIL: secao '{section}' ausente em {path}"

        if _normalize_block(current_section) != _normalize_block(snapshot_section):
            changed.append(section)

    if changed:
        return False, (
            "sections_unchanged FAIL: secoes imutaveis alteradas sem aprovacao do stakeholder: "
            f"{changed}"
        )

    return True, (
        "sections_unchanged: secoes preservadas "
        f"({', '.join(sections)})"
    )


def demand_coverage(
    prd_path: str = "docs/PRD.md",
    demand_path: str = "docs/demanda.md",
    project_root: str = ".",
) -> tuple[bool, str]:
    """Verifica deterministicamente se o PRD cobre a demanda original.

    Só roda na primeira run (quando demanda.md existe).
    Nas runs seguintes, demanda.md não existe e o validator passa automaticamente.
    """
    demand_file = Path(project_root) / demand_path
    prd_file = Path(project_root) / prd_path

    # Sem demanda = run subsequente, pular
    if not demand_file.exists():
        return True, "demand_coverage: sem demanda original — pulando (run subsequente)"

    if not prd_file.exists():
        return False, f"demand_coverage FAIL: {prd_path} não encontrado"

    demand_text = demand_file.read_text()
    prd_text = prd_file.read_text()

    stop_words = {
        "como", "quero", "preciso", "para", "que", "com", "sem", "por", "uma",
        "um", "de", "do", "da", "dos", "das", "no", "na", "nos", "nas", "ao",
        "em", "os", "as", "se", "ou", "ter", "ser", "ver", "usar", "deve",
        "devem", "deveria", "produto", "sistema", "usuario", "usuaria",
        "eu", "meu", "minha", "us",
        "the", "a", "an", "in", "on", "of", "to", "and", "or", "is", "with",
        "from", "for", "by", "at", "be", "have", "this", "that", "user",
        "system", "should", "must", "can", "want", "need",
    }
    short_requirement_tokens = {
        "ai", "ia", "ui", "ux", "api", "csv", "pdf", "xml", "sms", "sso",
        "mfa", "2fa", "otp", "pix", "cpf",
    }

    def _is_significant_short_token(raw_word: str, word: str) -> bool:
        if not 2 <= len(word) <= 3:
            return False
        raw_ascii = unicodedata.normalize("NFD", raw_word).encode("ascii", "ignore").decode("ascii")
        if not any(char.isalpha() for char in raw_ascii):
            return False
        return any(char.isdigit() for char in word) or word in short_requirement_tokens or raw_ascii.isupper()

    def _tokens(text: str) -> list[str]:
        tokens: list[str] = []
        for raw_word in re.findall(r"[A-Za-z0-9áéíóúãõâêôçàÁÉÍÓÚÃÕÂÊÔÇÀ]+", text):
            word = _normalize(raw_word)
            if not word or word in stop_words:
                continue
            if len(word) > 3 or _is_significant_short_token(raw_word, word):
                tokens.append(word)
        return tokens

    def _requirement_lines(text: str) -> list[str]:
        candidates: list[str] = []
        for raw_line in text.splitlines():
            line = raw_line.strip(" \t-*•0123456789.)")
            if len(line) < 12:
                continue
            lower = _normalize(line)
            explicit = raw_line.lstrip().startswith(("-", "*", "•")) or re.match(r"^\s*\d+[.)]", raw_line)
            intent = any(
                marker in lower
                for marker in (
                    "quero", "preciso", "deve", "devem", "permitir", "visualizar",
                    "criar", "editar", "remover", "listar", "filtrar", "buscar",
                    "acompanhar", "exportar", "importar", "validar", "mostrar",
                    "i want", "i need", "should", "must", "allow", "create",
                    "edit", "delete", "list", "filter", "search", "export",
                    "import", "validate", "show",
                )
            )
            if explicit or intent:
                candidates.append(line)
        if candidates:
            return candidates[:20]
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if len(p.strip()) >= 40]
        return paragraphs[:10]

    prd_tokens = set(_tokens(prd_text))
    if not prd_tokens:
        return False, "demand_coverage FAIL: PRD sem termos verificáveis"

    missing: list[str] = []
    covered = 0
    for requirement in _requirement_lines(demand_text):
        req_tokens = list(dict.fromkeys(_tokens(requirement)))
        if not req_tokens:
            continue
        missing_short = [
            tok for tok in req_tokens
            if len(tok) <= 3 and tok not in prd_tokens
        ]
        if missing_short:
            missing.append(f"{requirement[:120]} (faltam termos: {', '.join(missing_short)})")
            continue
        hits = [tok for tok in req_tokens if tok in prd_tokens]
        ratio = len(hits) / len(req_tokens)
        if ratio >= 0.45 or len(hits) >= min(3, len(req_tokens)):
            covered += 1
        else:
            missing.append(requirement[:120])

    total = covered + len(missing)
    if total == 0:
        demand_tokens = set(_tokens(demand_text))
        if not demand_tokens:
            return True, "demand_coverage: demanda sem requisitos verificáveis — pulando"
        missing_short = [tok for tok in demand_tokens if len(tok) <= 3 and tok not in prd_tokens]
        if missing_short:
            return False, f"demand_coverage FAIL: faltam termos curtos: {', '.join(sorted(missing_short))}"
        overlap = len(demand_tokens & prd_tokens) / len(demand_tokens)
        if overlap >= 0.35:
            return True, f"demand_coverage: PASS — overlap global {overlap:.0%}"
        return False, f"demand_coverage FAIL: overlap global {overlap:.0%} < 35%"

    if not missing:
        return True, f"demand_coverage: PASS — {covered}/{total} requisito(s) coberto(s)"

    missing_str = "; ".join(missing[:5])
    return False, (
        f"demand_coverage FAIL: {covered}/{total} requisito(s) coberto(s); "
        f"faltam: {missing_str}"
    )


def prd_coverage(
    prd_path: str = "docs/PRD.md",
    output_dirs: list[str] | None = None,
    min_ratio: float = 0.7,
    project_root: str = ".",
) -> tuple[bool, str]:
    """Verifica se as User Stories do PRD têm evidência no código gerado.

    Extrai US do PRD via regex, busca keywords nos arquivos de output_dirs.
    PASS se >= min_ratio das US têm pelo menos 1 match.
    """
    import re

    root = Path(project_root)
    prd_file = root / prd_path
    if not prd_file.exists():
        return True, f"prd_coverage: {prd_path} não encontrado — pulando"

    prd_text = prd_file.read_text(encoding="utf-8")

    # Extrair User Stories: ### US-NN — Título
    us_pattern = re.compile(r"###\s+(US-\d+)\s*[—–-]\s*(.+)")
    stories = us_pattern.findall(prd_text)
    if not stories:
        return True, "prd_coverage: nenhuma US encontrada no PRD — pulando"

    # Resolver dirs de output para busca
    if not output_dirs:
        output_dirs = ["frontend/src", "src", "backend"]
    search_dirs = [root / d for d in output_dirs if (root / d).is_dir()]
    if not search_dirs:
        return False, f"prd_coverage FAIL: nenhum diretório de output encontrado ({output_dirs})"

    # Coletar todo o texto dos arquivos de código
    code_text = []
    for d in search_dirs:
        for f in d.rglob("*"):
            if f.is_file() and f.suffix in (
                ".js", ".ts", ".jsx", ".tsx", ".svelte", ".vue",
                ".py", ".css", ".html", ".json",
            ):
                try:
                    code_text.append(f.read_text(encoding="utf-8", errors="ignore"))
                except OSError:
                    continue
    all_code = "\n".join(code_text).lower()

    if not all_code.strip():
        return False, "prd_coverage FAIL: nenhum código encontrado nos diretórios de output"

    # Verificar cada US
    STOP_WORDS = {
        "como", "quero", "para", "que", "com", "sem", "por", "uma", "um",
        "de", "do", "da", "dos", "das", "no", "na", "nos", "nas", "ao",
        "em", "os", "as", "se", "ou", "ter", "ser", "ver", "usar",
        "the", "a", "an", "in", "on", "of", "to", "and", "or", "is",
        "with", "from", "for", "by", "at", "be", "have", "this", "that",
    }
    # Mapeamento PT→EN para keywords comuns em UI/dev
    PT_EN = {
        "grafo": "graph", "visualizar": "graph", "diagrama": "diagram",
        "navegar": "navigate", "navegação": "nav", "estado": "state",
        "progresso": "progress", "terminal": "terminal", "editor": "editor",
        "validação": "validat", "validar": "validat", "árvore": "tree",
        "arquivo": "file", "arquivos": "file", "processo": "process",
        "dados": "data", "reais": "real", "acompanhar": "progress",
        "sprint": "sprint", "sprints": "sprint", "nodes": "node",
        "embutido": "embed", "painel": "panel", "abas": "tab",
        "tabs": "tab", "yaml": "yaml", "linhas": "line",
        "sidebar": "sidebar", "explorer": "explorer",
    }
    covered = []
    missing = []

    for us_id, us_title in stories:
        # Extrair keywords significativas do título
        words = re.findall(r"[a-záéíóúãõâêôçà]+", us_title.lower())
        keywords = [w for w in words if len(w) > 3 and w not in STOP_WORDS]

        # Verificar cada keyword (original ou tradução conta como hit)
        if not keywords:
            covered.append(us_id)
            continue
        hits = 0
        for kw in keywords:
            if kw in all_code:
                hits += 1
            elif kw in PT_EN and PT_EN[kw] in all_code:
                hits += 1
        ratio = hits / len(keywords)
        if ratio >= 0.4:
            covered.append(us_id)
        else:
            missing.append(us_id)

    total = len(stories)
    cov_ratio = len(covered) / total if total else 1.0

    if cov_ratio >= min_ratio:
        return True, f"prd_coverage: {len(covered)}/{total} US cobertas"

    missing_str = ", ".join(missing[:5])
    return False, (
        f"prd_coverage FAIL: {len(covered)}/{total} US cobertas "
        f"(min {min_ratio:.0%}) — faltam: {missing_str}"
    )


def unique_screenshots(
    screenshots_dir: str = "docs/screenshots",
    min_count: int = 2,
    project_root: str = ".",
) -> tuple[bool, str]:
    """Verifica que os screenshots em um diretório são arquivos distintos (sem cópias).

    Falha se:
    - O diretório não existe ou tem menos de min_count imagens
    - Dois ou mais arquivos têm hash MD5 idêntico (LLM copiou em vez de capturar)
    """
    root = Path(project_root)
    sdir = root / screenshots_dir
    if not sdir.exists():
        return False, f"unique_screenshots FAIL: diretório {screenshots_dir} não encontrado"

    IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
    images = [f for f in sdir.rglob("*") if f.suffix.lower() in IMAGE_EXTS]

    if len(images) < min_count:
        return False, (
            f"unique_screenshots FAIL: apenas {len(images)} imagem(ns) em {screenshots_dir} "
            f"(mínimo: {min_count})"
        )

    hashes: dict[str, list[str]] = {}
    for img in images:
        try:
            h = hashlib.md5(img.read_bytes()).hexdigest()
        except OSError:
            continue
        rel = str(img.relative_to(root))
        hashes.setdefault(h, []).append(rel)

    duplicates = {h: paths for h, paths in hashes.items() if len(paths) > 1}
    if duplicates:
        examples = []
        for paths in list(duplicates.values())[:3]:
            examples.append(f"{paths[0]} = {paths[1]}")
        return False, (
            f"unique_screenshots FAIL: {len(duplicates)} grupo(s) de screenshots idênticos "
            f"— {'; '.join(examples)}"
        )

    return True, f"unique_screenshots: {len(images)} screenshots únicos em {screenshots_dir}"


def bash_passes(script: str, project_root: str = ".") -> tuple[bool, str]:
    """Roda um script bash e verifica se sai com código 0.

    O script é resolvido relativo ao project_root.
    stdout/stderr são capturados; em caso de falha, as últimas linhas são exibidas.
    """
    script_path = Path(project_root) / script
    if not script_path.exists():
        return False, f"bash_passes FAIL: script não encontrado: {script}"
    try:
        result = subprocess.run(
            ["bash", str(script_path)],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return False, f"bash_passes FAIL: script excedeu 60s: {script}"
    except Exception as e:
        return False, f"bash_passes FAIL: erro ao executar {script}: {e}"

    if result.returncode == 0:
        last_line = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "ok"
        return True, f"bash_passes: {script} → {last_line}"

    output = (result.stdout + result.stderr).strip()
    preview = "\n".join(output.splitlines()[-5:]) if output else "(sem saída)"
    return False, f"bash_passes FAIL: {script} saiu com código {result.returncode}\n{preview}"


def command_succeeds(command: str, project_root: str = ".") -> tuple[bool, str]:
    """Executa um comando shell e verifica se sai com código 0.

    Diferente de bash_passes, recebe um comando direto (string) em vez de um
    path para script. O comando é executado via bash com pipefail para que
    pipelines como `pytest | tail` nao mascarem falhas do comando principal.
    """
    try:
        result = subprocess.run(
            ["bash", "-o", "pipefail", "-c", command],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return False, f"command_succeeds FAIL: comando excedeu 120s: {command[:60]}"
    except Exception as e:
        return False, f"command_succeeds FAIL: erro ao executar: {e}"

    def _preview(output_text: str, limit: int = 12) -> str:
        if not output_text:
            return "(sem saída)"
        lines = output_text.splitlines()
        if len(lines) <= limit:
            return "\n".join(lines)
        head_count = max(3, limit // 3)
        tail_count = limit - head_count - 1
        return "\n".join(lines[:head_count] + ["..."] + lines[-tail_count:])

    output = (result.stdout + result.stderr).strip()
    if (
        result.returncode == 0
        and "pytest" in command
        and "no tests ran" in output.lower()
    ):
        preview = _preview(output, limit=8)
        return False, f"command_succeeds FAIL: pytest nao executou nenhum teste\n{preview}"

    if result.returncode == 0:
        last_line = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "ok"
        return True, f"command_succeeds: {command[:60]} → {last_line}"

    if not output and "--silent" in command:
        diagnostic_command = command.replace("--silent", "")
        try:
            diagnostic = subprocess.run(
                ["bash", "-o", "pipefail", "-c", diagnostic_command],
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=120,
            )
            diagnostic_output = (diagnostic.stdout + diagnostic.stderr).strip()
            if diagnostic_output:
                output = "diagnostico sem --silent:\n" + diagnostic_output
        except Exception:
            pass

    preview = _preview(output, limit=12)
    return False, f"command_succeeds FAIL: saiu com código {result.returncode}\n{preview}"


def git_diff_not_empty(path: str = ".", project_root: str = ".") -> tuple[bool, str]:
    """Passa se o ciclo produziu mudança versionável em `path` (código real).

    Pega o node de build "ocioso" que recebe PASS sem tocar o código-alvo
    (lição vibeos cycle-02: frontend.02.implement passou sem escrever o shell).
    Considera: (a) mudanças uncommitted no working tree; (b) commits do branch
    do ciclo desde o merge-base com main/master. Fora de um repo git (modo
    diretório puro), passa com aviso explícito — não verificável ali.
    """
    import subprocess

    def _git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args], cwd=project_root, capture_output=True, text=True
        )

    if _git("rev-parse", "--git-dir").returncode != 0:
        return True, f"git_diff_not_empty: {path} não verificável (sem git) — AVISO"

    dirty = _git("status", "--porcelain", "--", path).stdout.strip()
    if dirty:
        n = len(dirty.splitlines())
        return True, f"git_diff_not_empty: {path} tem {n} mudança(s) no working tree"

    for base_branch in ("main", "master"):
        if _git("rev-parse", "--verify", "--quiet", base_branch).returncode != 0:
            continue
        base = _git("merge-base", "HEAD", base_branch).stdout.strip()
        if not base:
            continue
        changed = _git("diff", "--name-only", base, "HEAD", "--", path).stdout.strip()
        if changed:
            n = len(changed.splitlines())
            return True, f"git_diff_not_empty: {path} tem {n} arquivo(s) alterado(s) desde {base[:7]}"
        return False, (
            f"git_diff_not_empty FAIL: nenhuma mudança em {path} neste ciclo "
            f"(nem uncommitted, nem commits desde {base[:7]}) — node de build ocioso?"
        )

    return True, f"git_diff_not_empty: {path} sem branch base (main/master) — não verificável, AVISO"
