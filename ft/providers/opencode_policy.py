"""Provider-neutral OpenCode execution policy.

Product generation belongs to project templates and prompts. This module only
contains transport/tooling preferences that are valid for every project.
"""

from __future__ import annotations

import os


def opencode_deny_edit_tools_enabled() -> bool:
    """Whether to avoid OpenCode native Edit/Write tools by default."""
    raw = os.environ.get("FT_OPENCODE_DENY_EDIT_TOOLS", "").strip().lower()
    return raw not in {"0", "false", "no", "nao", "não", "off"}
