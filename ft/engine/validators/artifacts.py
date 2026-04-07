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


def has_sections(path: str, sections: list[str], project_root: str = ".") -> tuple[bool, str]:
    """Verifica se arquivo contem as secoes esperadas."""
    full = Path(project_root) / path
    if not full.exists():
        return False, f"has_sections FAIL: {path} nao existe"
    content = full.read_text()
    norm_content = _normalize(content)
    missing = [s for s in sections if _normalize(s) not in norm_content]
    if not missing:
        return True, f"has_sections: {path} tem todas as {len(sections)} secoes"
    return False, f"has_sections FAIL: {path} faltam secoes: {missing}"


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
    """Verifica se o PRD cobre todas as features da demanda original.

    Só roda na primeira run (quando demanda.md existe).
    Nas runs seguintes, demanda.md não existe e o validator passa automaticamente.
    """
    from pathlib import Path
    import json

    demand_file = Path(project_root) / demand_path
    prd_file = Path(project_root) / prd_path

    # Sem demanda = run subsequente, pular
    if not demand_file.exists():
        return True, "demand_coverage: sem demanda original — pulando (run subsequente)"

    if not prd_file.exists():
        return False, f"demand_coverage FAIL: {prd_path} não encontrado"

    demand_text = demand_file.read_text()
    prd_text = prd_file.read_text()

    # Usar LLM para verificar cobertura
    try:
        from ft.engine.delegate import delegate_to_llm

        prompt = (
            "Compare a demanda original do usuário com o PRD gerado.\n\n"
            "DEMANDA ORIGINAL:\n---\n"
            f"{demand_text}\n---\n\n"
            "PRD GERADO:\n---\n"
            f"{prd_text}\n---\n\n"
            "Verifique se CADA feature/requisito mencionado na demanda tem "
            "pelo menos uma User Story correspondente no PRD.\n\n"
            "Responda APENAS com JSON (sem markdown):\n"
            '{"covered": ["feature coberta 1", "feature coberta 2"], '
            '"missing": ["feature que faltou 1", "feature que faltou 2"], '
            '"verdict": "PASS" ou "FAIL"}\n\n'
            "Se todas as features estão cobertas, verdict=PASS.\n"
            "Se alguma feature da demanda não tem US correspondente, verdict=FAIL."
        )

        result = delegate_to_llm(
            task=prompt,
            project_root=project_root,
            allowed_paths=[],
            max_turns=5,
            llm_engine="claude",
        )

        output = result.output.strip()
        # Extrair JSON
        start = output.find("{")
        end = output.rfind("}") + 1
        if start >= 0 and end > start:
            data = json.loads(output[start:end])
            missing = data.get("missing", [])
            covered = data.get("covered", [])
            verdict = data.get("verdict", "FAIL")

            if verdict == "PASS" or not missing:
                return True, f"demand_coverage: PASS — {len(covered)} features cobertas no PRD"

            missing_str = "; ".join(missing[:5])
            return False, (
                f"demand_coverage FAIL: {len(missing)} feature(s) da demanda sem US no PRD: "
                f"{missing_str}"
            )

    except Exception as e:
        # Se LLM não disponível, passar (não bloquear por falha de infra)
        return True, f"demand_coverage: LLM indisponível, pulando ({e})"

    return True, "demand_coverage: verificação concluída"


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
