"""Project-derived context for API contract retries."""

from __future__ import annotations

from pathlib import Path
import re


def _normalise_api_path(raw: str) -> str:
    path = "/" + raw.strip().strip("`").lstrip("/")
    path = path.rstrip(".,;:)").replace("//", "/")
    if path == "/api/health":
        return "/health"
    if path != "/health" and not path.startswith("/api/"):
        return f"/api{path}"
    return path


def extract_api_endpoint_candidates(
    work_root: str | Path,
) -> list[tuple[str, str, str]]:
    """Extract explicit endpoints from project-owned PRD and task documents."""
    root = Path(work_root)
    sources = (root / "docs" / "task_list.md", root / "docs" / "PRD.md")
    endpoint_re = re.compile(
        r"\b(GET|POST|PUT|PATCH|DELETE)\b\s*(?:\|\s*|\s+)"
        r"`?(/(?:health\b|api/[A-Za-z0-9_./{}-]+|[A-Za-z0-9_./{}-]+))`?",
        re.IGNORECASE,
    )

    candidates: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for source in sources:
        if not source.exists():
            continue
        try:
            lines = source.read_text(
                encoding="utf-8",
                errors="ignore",
            ).splitlines()
        except OSError:
            continue
        for line in lines:
            for match in endpoint_re.finditer(line):
                method = match.group(1).upper()
                path = _normalise_api_path(match.group(2))
                key = (method, path)
                if key in seen:
                    continue
                seen.add(key)
                description = ""
                if "|" in line:
                    cells = [
                        cell.strip().strip("`")
                        for cell in line.strip().strip("|").split("|")
                    ]
                    for index, cell in enumerate(cells):
                        if (
                            cell.upper() == method
                            and index + 2 < len(cells)
                            and _normalise_api_path(cells[index + 1]) == path
                        ):
                            description = cells[index + 2].strip()
                            break
                if not description:
                    description = (
                        line[match.end() :].strip(" |-–:") or f"{method} {path}"
                    )
                description = re.sub(
                    r"\s+",
                    " ",
                    description.replace("|", " "),
                ).strip(" -–:`")[:72]
                candidates.append(
                    (method, path, description or f"{method} {path}")
                )

    if ("GET", "/health") not in {
        (method, path) for method, path, _description in candidates
    }:
        candidates.insert(0, ("GET", "/health", "Health check"))
    product = [
        item
        for item in candidates
        if item[1].startswith("/api/") and item[1] != "/api/health"
    ]
    health = [item for item in candidates if item[1] == "/health"]
    return (health[:1] + product)[:14]


def enrich_api_contract_feedback(
    node_id: str,
    feedback: str,
    work_root: str | Path,
) -> str:
    """Add actionable, project-derived endpoint rows to API contract feedback."""
    if node_id != "ft.plan.03.api_contract":
        return feedback
    candidates = extract_api_endpoint_candidates(work_root)
    if not candidates:
        return feedback
    rows: list[str] = []
    for method, path, description in candidates:
        request = "-" if method == "GET" else "`{...}`"
        response = (
            '`{ "status": "ok" }`'
            if path == "/health"
            else '`{ "items": [...] }`'
        )
        errors = "500" if method == "GET" else "400, 500"
        rows.append(
            f"| {method} | {path} | {description or method + ' ' + path} | "
            f"{request} | {response} | {errors} |"
        )
    return (
        f"{feedback}\n\n"
        "DIAGNOSTICO ESPECIFICO DO CONTRATO DE API:\n"
        "- O artefato anterior falhou na validacao; ele foi omitido para evitar "
        "contaminacao do retry.\n"
        "- Reescreva o arquivo inteiro. Nao preserve o formato anterior.\n"
        "- Cada endpoint deve ser uma linha Markdown com 6 colunas separadas por `|`.\n"
        "- A coluna Path deve conter `/health` ou `/api/...`; nunca URL completa.\n"
        "- Use estes endpoints explícitos já encontrados no PRD/task_list como base:\n"
        f"{chr(10).join(rows)}\n\n"
        "SAIDA ESPERADA: somente o Markdown final de docs/api_contract.md, "
        "começando em `## Base URL`."
    )
