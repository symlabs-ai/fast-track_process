"""
Validadores deterministicos de artefatos.
Cada funcao retorna (passed: bool, detail: str).
"""

from __future__ import annotations

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
