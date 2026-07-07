"""
Validadores deterministicos de artefatos.
Cada funcao retorna (passed: bool, detail: str).
"""

from __future__ import annotations

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
    path para script. O comando é executado com shell=True no project_root.
    """
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return False, f"command_succeeds FAIL: comando excedeu 120s: {command[:60]}"
    except Exception as e:
        return False, f"command_succeeds FAIL: erro ao executar: {e}"

    if result.returncode == 0:
        last_line = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "ok"
        return True, f"command_succeeds: {command[:60]} → {last_line}"

    output = (result.stdout + result.stderr).strip()
    preview = "\n".join(output.splitlines()[-5:]) if output else "(sem saída)"
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
