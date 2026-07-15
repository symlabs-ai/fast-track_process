"""Deterministic three-way merge for Fast Track canonical documents.

Independent feature cycles may close branches that changed the same canonical
Markdown files even when their product-code changes do not overlap.
This module resolves only that narrow case.  It reads the merge base, ours and
the worker version from index stages 1, 2 and 3, computes every result before
touching the checkout, and stages all resolved paths in one Git invocation.

The resolver is intentionally conservative: unknown conflicted paths,
add/delete conflicts, malformed tables, duplicate IDs and ambiguous concurrent
cell edits all fail without changing either the worktree or the index.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


CANONICAL_CONFLICT_PATHS = frozenset(
    {
        "CHANGELOG.md",
        "docs/PROJECT_BACKLOG.md",
        "docs/FEATURES.md",
    }
)

_REGULAR_GIT_MODES = {"100644", "100755"}
_TABLE_SEPARATOR_RE = re.compile(r"^:?-{3,}:?$")
_BACKLOG_ID_RE = re.compile(r"^PB-(\d+)([A-Z]?)$", re.IGNORECASE)
_FEATURE_ID_RE = re.compile(r"^FEAT-(\d+)([A-Z]?)$", re.IGNORECASE)
_PB_REFERENCE_RE = re.compile(r"\bPB-(\d+)([A-Z]?)\b", re.IGNORECASE)
_ADDITIVE_FEATURE_COLUMNS = {"evidencia", "ultimaevolucao", "notas"}


class CanonicalMergeError(ValueError):
    """A canonical conflict cannot be reconciled without guessing."""


@dataclass(frozen=True)
class CanonicalMergeResult:
    """Outcome of :func:`resolve_canonical_conflicts`.

    ``resolved`` is empty on failure, even when more than one document was
    successfully calculated in memory, because no partial result is applied.
    """

    success: bool
    resolved: tuple[str, ...] = ()
    error: str | None = None


@dataclass(frozen=True)
class _IndexStage:
    mode: str
    object_id: str


@dataclass(frozen=True)
class _TableRow:
    cells: tuple[str, ...]
    source_line: str


@dataclass(frozen=True)
class _MarkdownTable:
    prefix: tuple[str, ...]
    header_line: str
    separator_line: str
    suffix: tuple[str, ...]
    headers: tuple[str, ...]
    normalized_headers: tuple[str, ...]
    rows: dict[str, _TableRow]
    order: tuple[str, ...]
    newline: str
    final_newline: bool


def resolve_canonical_conflicts(
    repo_root: str | os.PathLike[str],
) -> CanonicalMergeResult:
    """Resolve safe canonical-doc conflicts in an ongoing Git merge.

    The index is the sole source of merge inputs.  Every unresolved path must
    belong to :data:`CANONICAL_CONFLICT_PATHS` and expose regular-file stages
    ``:1``, ``:2`` and ``:3``.  Expected failures are returned as data so the
    caller can fall back to its normal manual-conflict flow.
    """

    root = Path(repo_root).resolve()
    try:
        _require_ongoing_merge(root)
        stages, raw_index_state = _read_unmerged_index(root)
        if not stages:
            raise CanonicalMergeError("o merge não possui conflitos no índice")

        unexpected = sorted(set(stages) - CANONICAL_CONFLICT_PATHS)
        if unexpected:
            raise CanonicalMergeError(
                "há conflitos fora dos documentos canônicos permitidos: "
                + ", ".join(unexpected)
            )

        merged: dict[str, bytes] = {}
        for relative in sorted(stages):
            by_stage = stages[relative]
            if set(by_stage) != {1, 2, 3}:
                found = ", ".join(f":{number}" for number in sorted(by_stage))
                raise CanonicalMergeError(
                    f"{relative} precisa dos stages :1/:2/:3; encontrados {found or 'nenhum'}"
                )
            modes = {entry.mode for entry in by_stage.values()}
            if not modes.issubset(_REGULAR_GIT_MODES):
                raise CanonicalMergeError(
                    f"{relative} não é arquivo regular em todos os stages"
                )

            versions = tuple(
                _read_stage_text(root, relative, stage) for stage in (1, 2, 3)
            )
            merger = _merger_for(relative)
            merged[relative] = merger(*versions).encode("utf-8")

        # A second read closes the window in which another Git process could
        # have changed the merge index while the documents were being parsed.
        _current_stages, current_raw_state = _read_unmerged_index(root)
        if current_raw_state != raw_index_state:
            raise CanonicalMergeError(
                "o índice do merge mudou durante a reconciliação; tente novamente"
            )
    except (CanonicalMergeError, OSError, UnicodeError) as exc:
        return CanonicalMergeResult(success=False, error=str(exc))

    try:
        _apply_transaction(root, merged)
    except (CanonicalMergeError, OSError) as exc:
        return CanonicalMergeResult(success=False, error=str(exc))

    return CanonicalMergeResult(success=True, resolved=tuple(sorted(merged)))


def _require_ongoing_merge(root: Path) -> None:
    result = _git(root, "rev-parse", "-q", "--verify", "MERGE_HEAD")
    if result.returncode != 0:
        raise CanonicalMergeError("não há merge Git em andamento")


def _read_unmerged_index(
    root: Path,
) -> tuple[dict[str, dict[int, _IndexStage]], bytes]:
    result = _git(root, "ls-files", "-u", "-z")
    if result.returncode != 0:
        raise CanonicalMergeError(_git_failure("não foi possível ler o índice", result))

    paths: dict[str, dict[int, _IndexStage]] = {}
    for record in result.stdout.split(b"\0"):
        if not record:
            continue
        metadata, separator, raw_path = record.partition(b"\t")
        if not separator:
            raise CanonicalMergeError("entrada de conflito inválida no índice Git")
        try:
            mode, object_id, raw_stage = metadata.decode("ascii").split()
            stage = int(raw_stage)
            relative = raw_path.decode("utf-8")
        except (UnicodeError, ValueError) as exc:
            raise CanonicalMergeError(
                "entrada de conflito inválida no índice Git"
            ) from exc
        if stage not in {1, 2, 3}:
            raise CanonicalMergeError(f"stage inesperado :{stage} para {relative}")
        by_stage = paths.setdefault(relative, {})
        if stage in by_stage:
            raise CanonicalMergeError(f"stage :{stage} duplicado para {relative}")
        by_stage[stage] = _IndexStage(mode=mode, object_id=object_id)
    return paths, result.stdout


def _read_stage_text(root: Path, relative: str, stage: int) -> str:
    result = _git(root, "show", f":{stage}:{relative}")
    if result.returncode != 0:
        raise CanonicalMergeError(
            _git_failure(f"não foi possível ler :{stage}:{relative}", result)
        )
    try:
        return result.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CanonicalMergeError(
            f":{stage}:{relative} não contém UTF-8 válido"
        ) from exc


def _merger_for(relative: str) -> Callable[[str, str, str], str]:
    if relative == "CHANGELOG.md":
        return _merge_changelog
    if relative == "docs/PROJECT_BACKLOG.md":
        return lambda base, ours, theirs: _merge_table_document(
            relative, base, ours, theirs, kind="backlog"
        )
    if relative == "docs/FEATURES.md":
        return lambda base, ours, theirs: _merge_table_document(
            relative, base, ours, theirs, kind="features"
        )
    raise CanonicalMergeError(f"documento canônico não suportado: {relative}")


def _merge_changelog(base: str, ours: str, theirs: str) -> str:
    base_lines = base.splitlines()
    ours_lines = ours.splitlines()
    theirs_lines = theirs.splitlines()

    ours_gaps = _append_only_gaps(base_lines, ours_lines, "ours")
    theirs_gaps = _append_only_gaps(base_lines, theirs_lines, "theirs")
    output: list[str] = []
    seen = {_line_identity(line) for line in base_lines if line.strip()}

    for index in range(len(base_lines) + 1):
        for line in ours_gaps[index]:
            output.append(line)
            if line.strip():
                seen.add(_line_identity(line))
        for line in theirs_gaps[index]:
            # Blank-line layout comes from ours.  Only unique textual entries
            # are imported from the worker branch.
            identity = _line_identity(line)
            if not line.strip() or identity in seen:
                continue
            output.append(line)
            seen.add(identity)
        if index < len(base_lines):
            output.append(base_lines[index])

    newline = _newline_style(ours)
    final_newline = ours.endswith(("\n", "\r")) or theirs.endswith(("\n", "\r"))
    return newline.join(output) + (newline if final_newline else "")


def _append_only_gaps(
    base: list[str], variant: list[str], label: str
) -> list[list[str]]:
    """Return lines inserted in each base gap; reject edits/deletions."""

    gaps: list[list[str]] = [[] for _ in range(len(base) + 1)]
    cursor = 0
    for base_index, base_line in enumerate(base):
        try:
            match = variant.index(base_line, cursor)
        except ValueError as exc:
            raise CanonicalMergeError(
                f"CHANGELOG.md {label} alterou ou removeu linha histórica"
            ) from exc
        gaps[base_index].extend(variant[cursor:match])
        cursor = match + 1
    gaps[-1].extend(variant[cursor:])
    return gaps


def _line_identity(line: str) -> str:
    return re.sub(r"\s+", " ", line.strip()).casefold()


def _merge_table_document(
    relative: str,
    base_text: str,
    ours_text: str,
    theirs_text: str,
    *,
    kind: str,
) -> str:
    id_pattern = _BACKLOG_ID_RE if kind == "backlog" else _FEATURE_ID_RE
    base = _parse_table(relative, base_text, id_pattern=id_pattern, kind=kind)
    ours = _parse_table(relative, ours_text, id_pattern=id_pattern, kind=kind)
    theirs = _parse_table(relative, theirs_text, id_pattern=id_pattern, kind=kind)

    if base.normalized_headers != ours.normalized_headers:
        raise CanonicalMergeError(f"{relative}: ours alterou a estrutura da tabela")
    if base.normalized_headers != theirs.normalized_headers:
        raise CanonicalMergeError(f"{relative}: worker alterou a estrutura da tabela")
    if base.prefix != theirs.prefix or base.suffix != theirs.suffix:
        raise CanonicalMergeError(
            f"{relative}: worker alterou conteúdo fora da tabela canônica"
        )

    rows: dict[str, _TableRow] = dict(ours.rows)
    order = list(ours.order)

    missing_from_ours = set(base.rows) - set(ours.rows)
    missing_from_theirs = set(base.rows) - set(theirs.rows)
    if missing_from_ours or missing_from_theirs:
        removed = sorted(missing_from_ours | missing_from_theirs, key=_id_sort_key)
        raise CanonicalMergeError(
            f"{relative}: remoção de ID não é reconciliada automaticamente: "
            + ", ".join(removed)
        )

    for row_id in base.order:
        base_row = base.rows[row_id]
        ours_row = ours.rows[row_id]
        theirs_row = theirs.rows[row_id]
        if theirs_row.cells == base_row.cells:
            continue
        if ours_row.cells == theirs_row.cells:
            continue
        if ours_row.cells == base_row.cells:
            rows[row_id] = theirs_row
            continue
        merged_cells = _merge_row_cells(
            relative,
            row_id,
            base_row.cells,
            ours_row.cells,
            theirs_row.cells,
            base.normalized_headers,
            kind=kind,
        )
        rows[row_id] = _row_with_cells(ours_row, merged_cells)

    for row_id in theirs.order:
        if row_id in base.rows:
            continue
        theirs_row = theirs.rows[row_id]
        if row_id not in rows:
            rows[row_id] = theirs_row
            order.append(row_id)
            continue
        ours_row = rows[row_id]
        if ours_row.cells == theirs_row.cells:
            continue
        if kind != "features":
            raise CanonicalMergeError(
                f"{relative}: ID {row_id} foi adicionado com conteúdos diferentes"
            )
        empty_base = tuple(
            row_id if header == "id" else "—"
            for header in base.normalized_headers
        )
        merged_cells = _merge_row_cells(
            relative,
            row_id,
            empty_base,
            ours_row.cells,
            theirs_row.cells,
            base.normalized_headers,
            kind=kind,
        )
        rows[row_id] = _row_with_cells(ours_row, merged_cells)

    # Canonical IDs are normally monotonic.  Keep that invariant (and make the
    # result independent of worker close order) without reordering a catalogue
    # that was intentionally arranged another way.
    if list(ours.order) == sorted(ours.order, key=_id_sort_key):
        order = sorted(order, key=_id_sort_key)

    rendered_rows = [rows[row_id].source_line for row_id in order]
    lines = [
        *ours.prefix,
        ours.header_line,
        ours.separator_line,
        *rendered_rows,
        *ours.suffix,
    ]
    return ours.newline.join(lines) + (ours.newline if ours.final_newline else "")


def _parse_table(
    relative: str,
    text: str,
    *,
    id_pattern: re.Pattern[str],
    kind: str,
) -> _MarkdownTable:
    lines = text.splitlines()
    candidates: list[tuple[int, tuple[str, ...], tuple[str, ...]]] = []
    for index in range(len(lines) - 1):
        header = _split_markdown_row(lines[index])
        separator = _split_markdown_row(lines[index + 1])
        if not header or not separator or len(header) != len(separator):
            continue
        if "id" not in {_normalize_header(cell) for cell in header}:
            continue
        if not all(_TABLE_SEPARATOR_RE.fullmatch(cell.strip()) for cell in separator):
            continue
        candidates.append((index, header, separator))

    if len(candidates) != 1:
        raise CanonicalMergeError(
            f"{relative}: esperado exatamente um catálogo Markdown com coluna ID"
        )

    header_index, headers, _separator = candidates[0]
    normalized_headers = tuple(_normalize_header(cell) for cell in headers)
    if len(set(normalized_headers)) != len(normalized_headers):
        raise CanonicalMergeError(f"{relative}: cabeçalhos duplicados na tabela")
    required = {"id"}
    if kind == "features":
        required.update({"backlog", "evidencia", "notas"})
    missing = required - set(normalized_headers)
    if missing:
        raise CanonicalMergeError(
            f"{relative}: colunas obrigatórias ausentes: {', '.join(sorted(missing))}"
        )

    id_column = normalized_headers.index("id")
    rows: dict[str, _TableRow] = {}
    order: list[str] = []
    cursor = header_index + 2
    while cursor < len(lines):
        line = lines[cursor]
        cells = _split_markdown_row(line)
        if cells is None:
            break
        if len(cells) != len(headers):
            raise CanonicalMergeError(
                f"{relative}: linha {cursor + 1} possui {len(cells)} células; "
                f"esperadas {len(headers)}"
            )
        raw_id = cells[id_column].strip().upper()
        match = id_pattern.fullmatch(raw_id)
        if not match:
            raise CanonicalMergeError(
                f"{relative}: ID inválido na linha {cursor + 1}: {raw_id or '<vazio>'}"
            )
        prefix = "PB" if kind == "backlog" else "FEAT"
        row_id = f"{prefix}-{int(match.group(1)):03d}{match.group(2).upper()}"
        if row_id in rows:
            raise CanonicalMergeError(f"{relative}: ID duplicado: {row_id}")
        normalized_cells = list(cells)
        normalized_cells[id_column] = row_id
        row = _TableRow(cells=tuple(normalized_cells), source_line=line)
        rows[row_id] = row
        order.append(row_id)
        cursor += 1

    if not rows:
        raise CanonicalMergeError(f"{relative}: tabela canônica sem itens")

    return _MarkdownTable(
        prefix=tuple(lines[:header_index]),
        header_line=lines[header_index],
        separator_line=lines[header_index + 1],
        suffix=tuple(lines[cursor:]),
        headers=headers,
        normalized_headers=normalized_headers,
        rows=rows,
        order=tuple(order),
        newline=_newline_style(text),
        final_newline=text.endswith(("\n", "\r")),
    )


def _split_markdown_row(line: str) -> tuple[str, ...] | None:
    text = line.strip()
    if "|" not in text:
        return None

    cells: list[str] = []
    current: list[str] = []
    escaped = False
    for char in text:
        if escaped:
            current.append(char)
            escaped = False
            continue
        if char == "\\":
            current.append(char)
            escaped = True
            continue
        if char == "|":
            cells.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    cells.append("".join(current).strip())

    if text.startswith("|"):
        cells = cells[1:]
    if text.endswith("|") and cells:
        cells = cells[:-1]
    return tuple(cells)


def _normalize_header(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_value = decomposed.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", ascii_value.casefold())


def _merge_row_cells(
    relative: str,
    row_id: str,
    base: tuple[str, ...],
    ours: tuple[str, ...],
    theirs: tuple[str, ...],
    headers: tuple[str, ...],
    *,
    kind: str,
) -> tuple[str, ...]:
    if not (len(base) == len(ours) == len(theirs) == len(headers)):
        raise CanonicalMergeError(f"{relative}: largura incompatível em {row_id}")

    merged: list[str] = []
    for header, base_cell, ours_cell, theirs_cell in zip(
        headers, base, ours, theirs, strict=True
    ):
        if theirs_cell == base_cell or ours_cell == theirs_cell:
            merged.append(ours_cell)
        elif ours_cell == base_cell:
            merged.append(theirs_cell)
        elif kind == "features" and header == "backlog":
            merged.append(_merge_pb_references(base_cell, ours_cell, theirs_cell))
        elif kind == "features" and header in _ADDITIVE_FEATURE_COLUMNS:
            merged.append(_merge_additive_text(base_cell, ours_cell, theirs_cell))
        else:
            raise CanonicalMergeError(
                f"{relative}: edição concorrente ambígua em {row_id}/{header}"
            )
    return tuple(merged)


def _merge_pb_references(base: str, ours: str, theirs: str) -> str:
    references: list[str] = []
    for value in (base, ours, theirs):
        parsed = _parse_pb_references(value)
        for reference in parsed:
            if reference not in references:
                references.append(reference)
    if not references:
        return "—"
    return ", ".join(sorted(references, key=_id_sort_key))


def _parse_pb_references(value: str) -> list[str]:
    stripped = value.strip()
    if stripped in {"", "-", "—"}:
        return []
    references = [
        f"PB-{int(match.group(1)):03d}{match.group(2).upper()}"
        for match in _PB_REFERENCE_RE.finditer(stripped)
    ]
    remainder = _PB_REFERENCE_RE.sub("", stripped)
    remainder = re.sub(r"<br\s*/?>", "", remainder, flags=re.IGNORECASE)
    remainder = re.sub(r"(?:\be\b|[,;/+&\s]|—|-)+", "", remainder, flags=re.IGNORECASE)
    if remainder or not references:
        raise CanonicalMergeError(
            f"célula Backlog contém texto além de referências PB: {value!r}"
        )
    return list(dict.fromkeys(references))


def _merge_additive_text(base: str, ours: str, theirs: str) -> str:
    contributions: list[str] = []
    seen: set[str] = set()
    for value in (base, ours, theirs):
        for part in re.split(r"\s*(?:;|<br\s*/?>)\s*", value, flags=re.IGNORECASE):
            clean = re.sub(r"\s+", " ", part.strip())
            if clean in {"", "-", "—"}:
                continue
            identity = clean.casefold()
            if identity in seen:
                continue
            seen.add(identity)
            contributions.append(clean)
    return "; ".join(contributions) if contributions else "—"


def _row_with_cells(row: _TableRow, cells: tuple[str, ...]) -> _TableRow:
    if cells == row.cells:
        return row
    return _TableRow(cells=cells, source_line="| " + " | ".join(cells) + " |")


def _id_sort_key(value: str) -> tuple[str, int, str]:
    prefix, _, tail = value.partition("-")
    match = re.fullmatch(r"(\d+)([A-Z]?)", tail, re.IGNORECASE)
    if not match:
        return prefix, 2**31, tail
    return prefix, int(match.group(1)), match.group(2).upper()


def _newline_style(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _apply_transaction(root: Path, merged: dict[str, bytes]) -> None:
    originals: dict[str, tuple[bytes, int]] = {}
    prepared: dict[str, Path] = {}
    index_path = _git_index_path(root)
    index_bytes = index_path.read_bytes()
    index_mode = stat.S_IMODE(index_path.stat().st_mode)

    try:
        for relative, content in merged.items():
            target = root / relative
            if target.is_symlink() or not target.is_file():
                raise CanonicalMergeError(
                    f"{relative} não é arquivo regular no checkout do merge"
                )
            originals[relative] = (
                target.read_bytes(),
                stat.S_IMODE(target.stat().st_mode),
            )
            prepared[relative] = _prepare_atomic_file(target, content, originals[relative][1])

        for relative in sorted(prepared):
            os.replace(prepared[relative], root / relative)

        result = _git(root, "add", "--", *sorted(merged))
        if result.returncode != 0:
            raise CanonicalMergeError(
                _git_failure("não foi possível estagiar a reconciliação", result)
            )
    except Exception:
        for relative, (content, mode) in originals.items():
            target = root / relative
            restore = _prepare_atomic_file(target, content, mode)
            os.replace(restore, target)
        # ``git add`` writes the index under lock and atomically, but restoring
        # its snapshot as well makes the no-partial-state contract explicit.
        restore_index = _prepare_atomic_file(index_path, index_bytes, index_mode)
        os.replace(restore_index, index_path)
        raise
    finally:
        for temporary in prepared.values():
            temporary.unlink(missing_ok=True)


def _git_index_path(root: Path) -> Path:
    result = _git(root, "rev-parse", "--git-path", "index")
    if result.returncode != 0:
        raise CanonicalMergeError(_git_failure("não foi possível localizar o índice", result))
    raw = result.stdout.decode("utf-8").strip()
    path = Path(raw)
    return path if path.is_absolute() else root / path


def _prepare_atomic_file(target: Path, content: bytes, mode: int) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_path = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    temporary = Path(raw_path)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise CanonicalMergeError(f"não foi possível executar git: {exc}") from exc


def _git_failure(prefix: str, result: subprocess.CompletedProcess[bytes]) -> str:
    detail = result.stderr.decode("utf-8", errors="replace").strip()
    return f"{prefix}: {detail}" if detail else prefix
