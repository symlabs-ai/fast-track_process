"""Deterministic, bounded context profiles for incremental product work.

Profiles are intentionally narrower than HyperMode.  They read an explicit
allowlist, inject a stable feedback sentinel and never discover files by
walking the checkout.  HyperMode remains the default for nodes without a
``context_profile``.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import subprocess
from typing import Mapping
import unicodedata


FEATURE_DELTA_PREFIX = "feature_delta."
TWEAK_PROFILE = "tweak.direct"
BUG_DIRECT_PROFILE = "bug.direct"
BUG_RECONCILE_PROFILE = "bug.reconcile"
CONTEXT_BEGIN = "<FT_CONTEXT_PROFILE>"
CONTEXT_END = "</FT_CONTEXT_PROFILE>"

# Paths with these components are never eligible for profile context.  The
# allowlists below do not contain them; the guard also protects future edits.
_FORBIDDEN_COMPONENTS = frozenset({"state", "log", "logs", "archive", "archives"})
_FORBIDDEN_PREFIXES = ((".ft", "cycles"),)
_GIT_OBJECT_RE = re.compile(r"[0-9a-fA-F]{7,64}")
_DELTA_TEXT_SUFFIXES = frozenset({
    ".css", ".go", ".graphql", ".html", ".java", ".js", ".json",
    ".jsx", ".kt", ".md", ".php", ".py", ".rb", ".rs", ".scss",
    ".sh", ".sql", ".svelte", ".toml", ".ts", ".tsx", ".vue",
    ".xml", ".yaml", ".yml",
})
_DELTA_ROOTS = frozenset({"project", "src", "test", "tests"})
_TWEAK_UI_RE = re.compile(
    r"(?<![a-z0-9_])(?:"
    r"ui|ux|front-?end|interface|screen|tela|pagina|page|button|botao|"
    r"color|cor|css|style|estilo|layout|icon|icone|label|rotulo|tooltip|"
    r"modal|menu|sidebar|navbar|visual|theme|tema|spacing|espacamento|"
    r"typography|tipografia|responsive|form|formulario"
    r")(?![a-z0-9_])"
)
_TWEAK_API_RE = re.compile(
    r"(?<![a-z0-9_])(?:"
    r"api|endpoint|graphql|webhook|http|rest|payload|request|response|"
    r"rota|route|controller|controlador|schema"
    r")(?![a-z0-9_])|\b(?:api contract|contrato da api)\b"
)


@dataclass(frozen=True)
class ContextProfileSpec:
    name: str
    max_chars: int
    paths: tuple[str, ...]
    compact_receipt: bool = False
    include_changed_delta: bool = False
    include_product_manifest: bool = False
    include_allowlisted_delta: bool = False
    priority_paths: tuple[str, ...] = ()
    conditional_paths: tuple[str, ...] = ()
    max_section_chars: int = 8_000
    manifest_max_paths: int = 400
    git_namespace: str = "feature-delta"
    delta_before_manifest: bool = False
    compact_receipt_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContextProfileResult:
    """Context composition result consumed by the runner and focused tests."""

    prompt: str
    context: str
    loaded_paths: tuple[str, ...]
    deny_read_paths: tuple[str, ...]
    truncated: bool


_TECH_STACK_PATHS = ("docs/TECH_STACK.md", "docs/tech_stack.md")
_RECEIPT_PATH = "docs/feature-validation.json"


FEATURE_DELTA_PROFILES: Mapping[str, ContextProfileSpec] = {
    "feature_delta.discovery": ContextProfileSpec(
        name="feature_delta.discovery",
        max_chars=64_000,
        paths=(
            "docs/feature-request.md",
            "docs/feature-discovery.md",
            "docs/feature-questions.md",
            "docs/feature.md",
            "docs/feature-plan.md",
            "docs/feature-baseline.yml",
            "docs/PRD.md",
            *_TECH_STACK_PATHS,
            "docs/PROJECT_BACKLOG.md",
            "docs/FEATURES.md",
            "docs/ui_criteria.md",
            "docs/api_contract.md",
        ),
        include_product_manifest=True,
        priority_paths=(
            "docs/feature-request.md",
            "docs/feature-discovery.md",
            "docs/feature-questions.md",
            "docs/feature.md",
            "docs/feature-plan.md",
            "docs/PROJECT_BACKLOG.md",
            "docs/FEATURES.md",
        ),
    ),
    "feature_delta.implement": ContextProfileSpec(
        name="feature_delta.implement",
        max_chars=48_000,
        paths=(
            "docs/feature-request.md",
            "docs/feature.md",
            "docs/feature-plan.md",
            *_TECH_STACK_PATHS,
            "docs/ui_criteria.md",
            "docs/api_contract.md",
            "docs/implementation-report.md",
            "docs/feature-review.md",
            "docs/stakeholder-feedback.md",
            _RECEIPT_PATH,
        ),
        compact_receipt=True,
        include_changed_delta=True,
        priority_paths=(
            "docs/feature-request.md",
            "docs/feature.md",
            "docs/feature-plan.md",
            _RECEIPT_PATH,
            "docs/feature-review.md",
            "docs/stakeholder-feedback.md",
        ),
    ),
    "feature_delta.review": ContextProfileSpec(
        name="feature_delta.review",
        max_chars=56_000,
        paths=(
            "docs/feature.md",
            "docs/feature-plan.md",
            "docs/implementation-report.md",
            *_TECH_STACK_PATHS,
            "docs/ui_criteria.md",
            "docs/api_contract.md",
            _RECEIPT_PATH,
        ),
        compact_receipt=True,
        include_changed_delta=True,
        priority_paths=(
            "docs/feature.md",
            "docs/feature-plan.md",
            "docs/implementation-report.md",
            _RECEIPT_PATH,
        ),
    ),
    "feature_delta.reconcile": ContextProfileSpec(
        name="feature_delta.reconcile",
        max_chars=72_000,
        paths=(
            "docs/feature-request.md",
            "docs/feature.md",
            "docs/feature-plan.md",
            "docs/implementation-report.md",
            "docs/feature-review.md",
            "docs/stakeholder-feedback.md",
            _RECEIPT_PATH,
            "docs/PRD.md",
            *_TECH_STACK_PATHS,
            "docs/ui_criteria.md",
            "docs/api_contract.md",
            "docs/test_data.md",
            "docs/PROJECT_BACKLOG.md",
            "docs/FEATURES.md",
            "CHANGELOG.md",
        ),
        compact_receipt=True,
        include_changed_delta=True,
        include_allowlisted_delta=True,
        priority_paths=(
            "docs/feature.md",
            "docs/feature-plan.md",
            "docs/implementation-report.md",
            "docs/feature-review.md",
            "docs/stakeholder-feedback.md",
            _RECEIPT_PATH,
            "docs/PROJECT_BACKLOG.md",
            "docs/FEATURES.md",
        ),
    ),
}

TWEAK_PROFILES: Mapping[str, ContextProfileSpec] = {
    TWEAK_PROFILE: ContextProfileSpec(
        name=TWEAK_PROFILE,
        max_chars=24_000,
        paths=(
            "docs/feature-request.md",
            "docs/tweak-baseline.yml",
            *_TECH_STACK_PATHS,
            "docs/ui_criteria.md",
            "docs/api_contract.md",
        ),
        include_changed_delta=True,
        include_product_manifest=True,
        priority_paths=(
            "docs/feature-request.md",
            "docs/tweak-baseline.yml",
            *_TECH_STACK_PATHS,
            "docs/ui_criteria.md",
            "docs/api_contract.md",
        ),
        conditional_paths=(
            "docs/ui_criteria.md",
            "docs/api_contract.md",
        ),
        max_section_chars=4_000,
        manifest_max_paths=120,
        git_namespace="tweak",
        delta_before_manifest=True,
    ),
}

BUG_PROFILES: Mapping[str, ContextProfileSpec] = {
    BUG_DIRECT_PROFILE: ContextProfileSpec(
        name=BUG_DIRECT_PROFILE,
        max_chars=40_000,
        paths=(
            "docs/feature-request.md",
            "docs/bug-baseline.yml",
            "docs/bug-report.md",
            "docs/bug-validation.json",
            *_TECH_STACK_PATHS,
            "docs/ui_criteria.md",
            "docs/api_contract.md",
            "docs/PROJECT_BACKLOG.md",
            "docs/FEATURES.md",
        ),
        compact_receipt=True,
        include_changed_delta=True,
        include_product_manifest=True,
        priority_paths=(
            "docs/feature-request.md",
            "docs/bug-baseline.yml",
            "docs/bug-report.md",
            "docs/bug-validation.json",
            "docs/PROJECT_BACKLOG.md",
            "docs/FEATURES.md",
        ),
        manifest_max_paths=240,
        git_namespace="bug",
        delta_before_manifest=True,
        compact_receipt_paths=("docs/bug-validation.json",),
    ),
    BUG_RECONCILE_PROFILE: ContextProfileSpec(
        name=BUG_RECONCILE_PROFILE,
        max_chars=40_000,
        paths=(
            "docs/bug-report.md",
            "docs/bug-validation.json",
            "docs/stakeholder-feedback.md",
            "docs/bug-result.md",
            "docs/PROJECT_BACKLOG.md",
            "docs/FEATURES.md",
            "CHANGELOG.md",
        ),
        compact_receipt=True,
        priority_paths=(
            "docs/bug-report.md",
            "docs/bug-validation.json",
            "docs/stakeholder-feedback.md",
            "docs/bug-result.md",
            "docs/PROJECT_BACKLOG.md",
            "docs/FEATURES.md",
            "CHANGELOG.md",
        ),
        git_namespace="bug-reconcile",
        compact_receipt_paths=("docs/bug-validation.json",),
    ),
}

CONTEXT_PROFILES: Mapping[str, ContextProfileSpec] = {
    **FEATURE_DELTA_PROFILES,
    **TWEAK_PROFILES,
    **BUG_PROFILES,
}

KNOWN_CONTEXT_PROFILES = frozenset(CONTEXT_PROFILES)
HYPER_MODE_FIELDS = (
    "hyper_mode_docs",
    "hyper_mode_full_docs",
    "hyper_mode_preview_lines",
    "hyper_mode_full_max_lines",
)


def _is_forbidden_path(relative: str) -> bool:
    parts = tuple(part.lower() for part in Path(relative).parts)
    if any(part in _FORBIDDEN_COMPONENTS for part in parts):
        return True
    return any(parts[: len(prefix)] == prefix for prefix in _FORBIDDEN_PREFIXES)


def _safe_candidate(root: Path, relative: str) -> Path | None:
    path = Path(relative)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return None
    if _is_forbidden_path(relative):
        return None
    candidate = root
    for part in path.parts:
        candidate = candidate / part
        # A lexical allowlist must not be redirectable through a symlink, even
        # when its resolved target remains inside the checkout (for example a
        # canonical doc pointing at .ft/cycles history).
        if candidate.is_symlink():
            return None
    try:
        candidate.resolve().relative_to(root)
    except (OSError, ValueError):
        return None
    return candidate


def _read_bounded_text(path: Path, limit: int) -> tuple[str, bool] | None:
    try:
        if not path.is_file():
            return None
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            content = handle.read(max(0, limit) + 1)
    except OSError:
        return None
    if len(content) > limit:
        return content[:limit], True
    return content, False


def _read_head_tail_text(path: Path, limit: int) -> tuple[str, bool] | None:
    """Read a deterministic head+tail excerpt without retaining the full file."""
    if limit <= 0:
        try:
            return "", bool(path.is_file() and path.stat().st_size)
        except OSError:
            return None
    try:
        if not path.is_file():
            return None
        size = path.stat().st_size
        # Small files may contain multibyte text, so allow a bounded byte
        # multiplier before deciding whether an excerpt is necessary.
        if size <= limit * 4:
            content = path.read_text(encoding="utf-8", errors="replace")
            if len(content) <= limit:
                return content, False
        half = max(1, limit // 2)
        with path.open("rb") as handle:
            head = handle.read(half).decode("utf-8", errors="replace")
            handle.seek(max(0, size - half))
            tail = handle.read(half).decode("utf-8", errors="replace")
    except OSError:
        return None
    marker = "\n...[middle omitted]...\n"
    available = max(0, limit - len(marker))
    head_limit = available // 2
    tail_limit = available - head_limit
    return head[:head_limit] + marker + tail[-tail_limit:], True


def _feature_target_ids(root: Path) -> tuple[str, ...]:
    feature_path = _safe_candidate(root, "docs/feature.md")
    if feature_path is None:
        return ()
    result = _read_bounded_text(feature_path, 64_000)
    if result is None:
        return ()
    field_pattern = re.compile(
        r"(?mi)^\s*(?:backlog_item|target_feature)\s*:\s*"
        r"['\"]?((?:PB|FEAT)-\d{3,})['\"]?\s*(?:#.*)?$"
    )
    return tuple(dict.fromkeys(field_pattern.findall(result[0])))


def _targeted_id_lines(path: Path, target_ids: tuple[str, ...], limit: int = 2_000) -> str:
    """Stream a canonical doc and retain only rows mentioning this feature's IDs."""
    if not target_ids or limit <= 0:
        return ""
    selected: list[str] = []
    used = 0
    target_pattern = re.compile(
        r"(?<![A-Za-z0-9_-])(?:"
        + "|".join(re.escape(target_id) for target_id in target_ids)
        + r")(?![A-Za-z0-9_-])"
    )
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if not target_pattern.search(line):
                    continue
                clean = line.rstrip()
                remaining = limit - used
                if remaining <= 0:
                    break
                selected.append(clean[:remaining])
                used += min(len(clean), remaining) + 1
    except OSError:
        return ""
    return "\n".join(selected)


def _compact_receipt(path: Path) -> str | None:
    """Return receipt evidence without embedding the per-file hash inventory."""
    try:
        if not path.is_file():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    files = payload.get("files")
    compact = {
        "schema_version": payload.get("schema_version"),
        "kind": payload.get("kind"),
        "result": payload.get("result"),
        "fingerprint": payload.get("fingerprint"),
        "recorded_at": payload.get("recorded_at"),
        "product_root": payload.get("product_root"),
        "commands": payload.get("commands") if isinstance(payload.get("commands"), list) else [],
        "file_count": (
            len(files)
            if isinstance(files, list)
            else payload.get("file_count", 0)
        ),
    }
    return json.dumps(compact, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _feedback_sentinel(last_approval_message: str | None) -> str:
    text = last_approval_message if isinstance(last_approval_message, str) else None
    if text is not None and not text.strip():
        text = None
    payload = {
        "state": "present" if text is not None else "none",
        "text": text,
    }
    return "CURRENT_FEEDBACK=" + json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _files_equal(left: Path, right: Path) -> bool:
    try:
        if not left.is_file() or not right.is_file():
            return False
        if left.stat().st_size != right.stat().st_size:
            return False
        with left.open("rb") as left_handle, right.open("rb") as right_handle:
            while True:
                left_chunk = left_handle.read(64 * 1024)
                right_chunk = right_handle.read(64 * 1024)
                if left_chunk != right_chunk:
                    return False
                if not left_chunk:
                    return True
    except OSError:
        return False


def _tech_stack_selection(root: Path) -> tuple[tuple[str, ...], str | None]:
    """Prefer uppercase alias; inject lowercase too only when contents conflict."""
    upper_path = _safe_candidate(root, _TECH_STACK_PATHS[0])
    lower_path = _safe_candidate(root, _TECH_STACK_PATHS[1])
    upper_exists = bool(upper_path and upper_path.is_file())
    lower_exists = bool(lower_path and lower_path.is_file())
    if upper_exists and lower_exists:
        assert upper_path is not None and lower_path is not None
        if _files_equal(upper_path, lower_path):
            manifest = {
                "state": "identical",
                "preferred": _TECH_STACK_PATHS[0],
                "omitted_alias": _TECH_STACK_PATHS[1],
            }
            return (_TECH_STACK_PATHS[0],), json.dumps(
                manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
        manifest = {
            "state": "conflict",
            "preferred": _TECH_STACK_PATHS[0],
            "included": list(_TECH_STACK_PATHS),
        }
        return _TECH_STACK_PATHS, json.dumps(
            manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    if upper_exists:
        return (_TECH_STACK_PATHS[0],), None
    if lower_exists:
        manifest = {
            "state": "lowercase_only",
            "preferred": _TECH_STACK_PATHS[1],
        }
        return (_TECH_STACK_PATHS[1],), json.dumps(
            manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    return (), None


def _normalized_relevance_text(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(char for char in decomposed if not unicodedata.combining(char)).lower()


def _tweak_conditional_paths(root: Path) -> frozenset[str]:
    """Select optional UI/API docs from the immutable request and baseline only."""
    request = ""
    request_path = _safe_candidate(root, "docs/feature-request.md")
    if request_path is not None:
        request_result = _read_bounded_text(request_path, 12_000)
        if request_result is not None:
            request = request_result[0]

    classification = ""
    baseline_path = _safe_candidate(root, "docs/tweak-baseline.yml")
    if baseline_path is not None:
        baseline_result = _read_bounded_text(baseline_path, 4_000)
        if baseline_result is not None:
            match = re.search(
                r"(?mi)^\s*classification\s*:\s*['\"]?([a-z_]+)",
                baseline_result[0],
            )
            if match:
                classification = match.group(1).lower()

    normalized = _normalized_relevance_text(request)
    api_relevant = bool(_TWEAK_API_RE.search(normalized))
    ui_relevant = (
        classification == "visual"
        or bool(_TWEAK_UI_RE.search(normalized))
        or (classification == "copy" and not api_relevant)
    )

    selected: set[str] = set()
    if ui_relevant:
        selected.add("docs/ui_criteria.md")
    if api_relevant:
        selected.add("docs/api_contract.md")
    return frozenset(selected)


def _effective_paths(spec: ContextProfileSpec, root: Path) -> tuple[tuple[str, ...], str | None]:
    selected_tech, tech_manifest = _tech_stack_selection(root)
    selected_conditional = (
        _tweak_conditional_paths(root)
        if spec.name == TWEAK_PROFILE
        else frozenset(spec.conditional_paths)
    )
    paths: list[str] = []
    tech_added = False
    for relative in spec.paths:
        if relative in spec.conditional_paths and relative not in selected_conditional:
            continue
        if relative in _TECH_STACK_PATHS:
            if not tech_added:
                paths.extend(selected_tech)
                tech_added = True
            continue
        paths.append(relative)
    return tuple(dict.fromkeys(paths)), tech_manifest


def _git_output(root: Path, args: list[str], *, max_bytes: int = 128_000) -> bytes | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            check=False,
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout[:max_bytes]


def _eligible_delta_path(relative: str) -> bool:
    if _is_forbidden_path(relative):
        return False
    path = Path(relative)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return False
    return bool(path.parts) and path.parts[0].lower() in _DELTA_ROOTS


def _changed_delta_sections(
    root: Path,
    base_commit: str | None,
    *,
    extra_paths: tuple[str, ...] = (),
    excluded_paths: tuple[str, ...] = (),
    namespace: str = "feature-delta",
) -> list[tuple[str, str]]:
    """Build focal diff/current excerpts from Git paths, never a filesystem walk."""
    if not base_commit or not _GIT_OBJECT_RE.fullmatch(base_commit):
        return []
    if _git_output(root, ["cat-file", "-e", f"{base_commit}^{{commit}}"], max_bytes=1) is None:
        return []

    tracked_raw = _git_output(
        root,
        ["diff", "--name-only", "-z", base_commit, "--"],
    )
    untracked_raw = _git_output(
        root,
        ["ls-files", "--others", "--exclude-standard", "-z"],
    )
    excluded = frozenset(excluded_paths)
    allowed_extra = frozenset(
        path
        for path in extra_paths
        if path not in excluded and not _is_forbidden_path(path)
    )
    changed: list[str] = []
    for raw in (tracked_raw or b"", untracked_raw or b""):
        for value in raw.decode("utf-8", errors="replace").split("\0"):
            relative = value.strip()
            if (
                relative
                and (_eligible_delta_path(relative) or relative in allowed_extra)
                and relative not in changed
            ):
                changed.append(relative)
    changed.sort()
    if not changed:
        return []

    manifest = json.dumps(
        {"base_commit": base_commit, "paths": changed},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    sections: list[tuple[str, str]] = [(f"git:{namespace}.changed-files", manifest)]

    # One bounded tracked diff supplies the high-signal before/after view.
    diff_raw = _git_output(
        root,
        [
            "diff", "--no-ext-diff", "--no-color", "--unified=2",
            base_commit, "--", *changed,
        ],
        max_bytes=16_001,
    )
    if diff_raw:
        diff_text = diff_raw.decode("utf-8", errors="replace")
        if len(diff_text) > 16_000:
            diff_text = diff_text[:16_000] + "\n...[diff truncated]"
        sections.append((f"git:{namespace}.diff", diff_text))

    # Current excerpts also cover untracked files (which `git diff` omits).
    excerpt_budget = 12_000
    for relative in changed:
        if excerpt_budget <= 0:
            break
        if not _eligible_delta_path(relative):
            continue
        path = Path(relative)
        if path.suffix.lower() not in _DELTA_TEXT_SUFFIXES and path.name not in {
            "Dockerfile", "Makefile", "Procfile",
        }:
            continue
        candidate = _safe_candidate(root, relative)
        if candidate is None:
            continue
        per_file = min(4_000, excerpt_budget)
        result = _read_bounded_text(candidate, per_file)
        if result is None:
            continue
        content, was_truncated = result
        if was_truncated:
            content += "\n...[file excerpt truncated]"
        sections.append((f"current:{relative}", content))
        excerpt_budget -= len(content)
    return sections


def _product_manifest_section(
    root: Path,
    *,
    namespace: str = "feature-delta",
    max_paths: int = 400,
) -> tuple[str, str] | None:
    """Describe tracked product/test files from Git without walking the tree."""
    raw = _git_output(
        root,
        ["ls-files", "-z", "--", "project", "src", "test", "tests"],
    )
    if raw is None:
        return None
    paths = sorted({
        value
        for value in raw.decode("utf-8", errors="replace").split("\0")
        if value and _eligible_delta_path(value)
    })
    if not paths:
        return None
    payload = {
        "tracked_count": len(paths),
        "paths": paths[:max_paths],
        "truncated": len(paths) > max_paths,
    }
    return (
        f"git:{namespace}.product-manifest",
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    )


def _append_bounded(parts: list[str], value: str, cap: int) -> tuple[bool, bool]:
    """Append within cap. Return (appended_anything, truncated)."""
    used = sum(len(part) for part in parts)
    remaining = cap - used
    if remaining <= 0:
        return False, bool(value)
    if len(value) <= remaining:
        parts.append(value)
        return True, False
    marker = "\n...[context truncated]"
    body_limit = max(0, remaining - len(marker))
    parts.append(value[:body_limit] + marker[: remaining - body_limit])
    return True, True


def compose_context_profile(
    profile_name: str,
    project_root: str | Path,
    original_prompt: str,
    *,
    last_approval_message: str | None = None,
    base_commit: str | None = None,
) -> ContextProfileResult:
    """Compose one bounded profile around ``original_prompt``.

    Only ``last_approval_message`` may populate ``CURRENT_FEEDBACK``.  No cycle
    state, archived artifacts, logs or process KB are read by this function.
    """
    try:
        spec = CONTEXT_PROFILES[profile_name]
    except KeyError as exc:
        raise ValueError(f"context_profile desconhecido: {profile_name}") from exc

    root = Path(project_root).resolve()
    paths, tech_manifest = _effective_paths(spec, root)
    compact_receipt_paths = (
        spec.compact_receipt_paths
        if spec.compact_receipt_paths
        else ((_RECEIPT_PATH,) if spec.compact_receipt else ())
    )
    target_ids = (
        _feature_target_ids(root)
        if {"docs/PROJECT_BACKLOG.md", "docs/FEATURES.md"}.intersection(paths)
        else ()
    )
    header = (
        f"{CONTEXT_BEGIN}\n"
        f"CONTEXT_PROFILE={profile_name}\n"
        "CONTEXT_POLICY=deterministic_allowlist\n"
        f"{_feedback_sentinel(last_approval_message)}\n"
        "CONTEXT_RESTRICTION=Use somente os documentos injetados abaixo como contexto documental; "
        "nao leia .ft/cycles, state, logs ou archives. Codigo e testes do produto podem ser lidos "
        "quando a tarefa exigir.\n"
    )
    suffix = f"\n{CONTEXT_END}"
    content_cap = max(0, spec.max_chars - len(suffix))
    parts = [header[:content_cap]]
    truncated = len(header) > content_cap
    cap_exhausted = truncated
    loaded: list[str] = []

    if tech_manifest and not cap_exhausted:
        _added, did_truncate = _append_bounded(
            parts,
            f"TECH_STACK_ALIAS_MANIFEST={tech_manifest}\n",
            content_cap,
        )
        truncated = truncated or did_truncate
        cap_exhausted = cap_exhausted or did_truncate

    def append_file(relative: str) -> None:
        nonlocal truncated, cap_exhausted
        if cap_exhausted:
            return
        candidate = _safe_candidate(root, relative)
        if candidate is None:
            return
        if relative in compact_receipt_paths and spec.compact_receipt:
            content = _compact_receipt(candidate)
            source_truncated = False
        else:
            # Fairness: one large canonical doc cannot starve the remaining
            # feature spec, receipt, backlog or catalog entries.
            marker = "\n...[document excerpt truncated]"
            per_section_cap = min(
                spec.max_section_chars,
                max(0, content_cap - sum(len(part) for part in parts)),
            )
            targeted = (
                _targeted_id_lines(candidate, target_ids)
                if relative in {"docs/PROJECT_BACKLOG.md", "docs/FEATURES.md"}
                else ""
            )
            targeted_block = (
                f"\nTARGETED_ID_EXCERPTS:\n{targeted}"
                if targeted
                else ""
            )
            read_result = _read_head_tail_text(
                candidate,
                max(0, per_section_cap - len(marker) - len(targeted_block)),
            )
            if read_result is None:
                return
            content, source_truncated = read_result
            if source_truncated:
                content += marker
            content += targeted_block
        if content is None:
            return
        if len(content) > spec.max_section_chars:
            content = content[
                : spec.max_section_chars - len("\n...[document excerpt truncated]")
            ]
            content += "\n...[document excerpt truncated]"
            source_truncated = True
        section = f"\n### {relative}\n{content.rstrip()}\n"
        added, did_truncate = _append_bounded(parts, section, content_cap)
        if added:
            loaded.append(relative)
        truncated = truncated or source_truncated or did_truncate
        cap_exhausted = cap_exhausted or did_truncate

    priority_set = set(spec.priority_paths)
    for relative in spec.priority_paths:
        if relative in paths:
            append_file(relative)

    def append_product_manifest() -> None:
        nonlocal truncated, cap_exhausted
        if not spec.include_product_manifest or cap_exhausted:
            return
        manifest_section = _product_manifest_section(
            root,
            namespace=spec.git_namespace,
            max_paths=spec.manifest_max_paths,
        )
        if manifest_section:
            virtual_path, content = manifest_section
            added, did_truncate = _append_bounded(
                parts,
                f"\n### {virtual_path}\n{content}\n",
                content_cap,
            )
            if added:
                loaded.append(virtual_path)
            truncated = truncated or did_truncate
            cap_exhausted = cap_exhausted or did_truncate

    def append_changed_delta() -> None:
        nonlocal truncated, cap_exhausted
        if not spec.include_changed_delta or cap_exhausted:
            return
        extra_delta_paths = paths if spec.include_allowlisted_delta else ()
        for virtual_path, content in _changed_delta_sections(
            root,
            base_commit,
            extra_paths=extra_delta_paths,
            excluded_paths=compact_receipt_paths,
            namespace=spec.git_namespace,
        ):
            section = f"\n### {virtual_path}\n{content.rstrip()}\n"
            added, did_truncate = _append_bounded(parts, section, content_cap)
            if added:
                loaded.append(virtual_path)
            truncated = truncated or did_truncate
            cap_exhausted = cap_exhausted or did_truncate
            if cap_exhausted:
                break

    if spec.delta_before_manifest:
        append_changed_delta()
        append_product_manifest()
    else:
        append_product_manifest()
        append_changed_delta()

    for relative in paths:
        if cap_exhausted:
            break
        if relative in priority_set:
            continue
        append_file(relative)

    context = "".join(parts) + suffix
    if len(context) > spec.max_chars:
        context = context[: spec.max_chars]
        truncated = True

    # OpenCode receives hard read denials in addition to the prompt policy.
    # Other providers intentionally rely on the prompt restriction only.
    deny_paths = tuple(dict.fromkeys((
        # Deny the declared profile universe, not only sections that happened
        # to fit in the cap.  Otherwise an omitted tail document could be read
        # back by OpenCode and make the effective context unbounded.
        *spec.paths,
        "docs/**",
        ".ft/cycles/**",
        "state/**",
        "**/log/**",
        "**/logs/**",
        "**/archive/**",
        "**/archives/**",
    )))
    prompt = f"{context}\n\n---\n\n{original_prompt}"
    return ContextProfileResult(
        prompt=prompt,
        context=context,
        loaded_paths=tuple(loaded),
        deny_read_paths=deny_paths,
        truncated=truncated,
    )
