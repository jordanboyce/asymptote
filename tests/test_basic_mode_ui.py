"""Static regression checks for the Basic Mode UI surface (R8).

There is no JS test runner in this repo, so we treat the source files as
text and assert the gating contract for each surface that should react to
`isExpertMode`. The checks are intentionally narrow — each one pins a single
behavior the spec calls out in tasks 10.1 / 10.2 / 10.3 — so a regression
fails loudly with a pointer to the offending file rather than a vague diff.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_SRC = REPO_ROOT / "frontend" / "src"


def _read(rel_path: str) -> str:
    p = FRONTEND_SRC / rel_path
    assert p.exists(), f"expected {p} to exist"
    return p.read_text(encoding="utf-8")


# ── 10.3: tab visibility in Basic Mode ──────────────────────────────────────


def test_app_vue_expertise_tab_is_expert_only():
    """The Expertise tab must only appear in Expert Mode."""
    src = _read("App.vue")
    # The push for `expertise` must sit inside an `if (isExpertMode.value)` arm.
    # We grep for the literal pattern the file currently uses; if the structure
    # is rewritten, this test should fail and force the author to think about
    # whether expertise still belongs to Expert Mode only.
    assert re.search(
        r"if\s*\(\s*isExpertMode\.value\s*\).*?\bid:\s*'expertise'",
        src,
        re.DOTALL,
    ), "App.vue must gate the Expertise tab behind isExpertMode"


def test_app_vue_tools_dropdown_is_expert_only():
    """MCP / OCR / Tokenizer / Diagnostics live behind the Tools dropdown,
    which must itself be gated by isExpertMode so basic-mode advisors never
    see those entries."""
    src = _read("App.vue")
    assert "!isCollectionsView && isExpertMode" in src, (
        "App.vue Tools dropdown must use the !isCollectionsView && isExpertMode "
        "guard so it disappears in Basic Mode"
    )


def test_app_vue_basic_hidden_tabs_bounce_to_chat():
    """Toggling out of Expert Mode while sitting on a hidden tab must rebound
    to chat — otherwise an advisor flipping to Basic mid-flight gets stuck on
    a now-invisible tab."""
    src = _read("App.vue")
    assert "BASIC_HIDDEN_TABS" in src, "App.vue must define BASIC_HIDDEN_TABS"
    # Expected hidden-in-basic tab ids
    for tab_id in ("expertise", "mcp", "ocr", "tokenizer", "diagnostics"):
        assert f"'{tab_id}'" in src, f"BASIC_HIDDEN_TABS must include '{tab_id}'"
    assert re.search(
        r"watch\s*\(\s*isExpertMode\b.*?activeTab\.value\s*=\s*'chat'",
        src,
        re.DOTALL,
    ), "App.vue must watch isExpertMode and bounce activeTab back to 'chat'"


# ── 10.1: SettingsTab sections gated behind Expert Mode ─────────────────────


@pytest.mark.parametrize(
    "section_title",
    [
        "System",                # System info / DB backend
        "UI Features",           # chat-tab toggle
        "OCR Settings",          # OCR config
        "LLM Column Role Inference",  # schema inference
        "Appearance",            # theme picker
        "Privacy",               # PII enable/disable
        "Re-index Collection",   # power-user op
        "Danger Zone",           # destructive op
    ],
)
def test_settings_tab_sections_are_expert_only(section_title: str):
    """Each Settings card the spec marks as expert-only must sit inside a
    `v-if="isExpertMode"` parent. We look for the card title and walk back
    to the nearest enclosing card-root `<div>` (matching `class="card ` —
    deliberately not `card-body` or `card-title`), asserting it carries
    the gate."""
    src = _read("components/SettingsTab.vue")
    title_marker = f">{section_title}<"
    idx = src.find(title_marker)
    assert idx >= 0, f"could not find Settings section titled {section_title!r}"
    # Card-root match: <div class="..card .." or <div class="card .." — but
    # never card-body / card-title / card-actions.
    card_root_re = re.compile(r"<div [^>]*class=\"[^\"]*\bcard\b(?!-)[^\"]*\"[^>]*>")
    head = src[:idx]
    matches = list(card_root_re.finditer(head))
    assert matches, f"could not find enclosing card <div for {section_title!r}"
    card_opener = matches[-1].group(0)
    assert 'v-if="isExpertMode"' in card_opener, (
        f"{section_title!r} card must be gated by v-if=\"isExpertMode\" — got: "
        f"{card_opener!r}"
    )


# ── 10.2: friendlyError util exists with the right vocabulary ───────────────


def test_friendly_error_util_exists():
    """The util must export friendlyError + classifyError, and must cover
    each error category enumerated in task 10.2 (502/503 → server_error,
    timeout, decode, etc.)."""
    src = _read("utils/friendlyError.js")
    # Public API
    assert "export function friendlyError" in src
    assert "export function classifyError" in src
    # Required category branches — each one corresponds to a class of failure
    # an advisor will hit on a flaky network or a misbehaving provider.
    for category in (
        "server_error",
        "timeout",
        "network",
        "auth",
        "rate_limit",
        "decode",
        "unsupported_file",
        "unknown",
    ):
        assert f"'{category}'" in src or f'"{category}"' in src, (
            f"friendlyError.js must classify the {category!r} category"
        )


def test_chat_tab_uses_friendly_error():
    """ChatTab must funnel its three error-assignment sites (catch, SSE error
    event, stream-onError) through friendlyError so Basic Mode never shows a
    raw stack message."""
    src = _read("components/ChatTab.vue")
    assert "from '../utils/friendlyError.js'" in src, (
        "ChatTab must import friendlyError"
    )
    # Count assignments — should be at least three friendlyError() calls.
    assignments = re.findall(r"friendlyError\(", src)
    assert len(assignments) >= 3, (
        f"ChatTab.vue should call friendlyError at all error-assignment sites; "
        f"found only {len(assignments)}"
    )
    # And the bare `err.message ||` fallback should be gone from the chat
    # error assignments (it would bypass the friendly mapping).
    assert "error.value = err.message" not in src, (
        "ChatTab.vue still assigns err.message directly — should route through "
        "friendlyError so Basic Mode sees plain-language copy"
    )


def test_sources_sidebar_uses_friendly_error():
    """The upload toast (indexError) must funnel through friendlyError so a
    failed CSV upload reads like an actionable hint instead of a stack trace."""
    src = _read("components/SourcesSidebar.vue")
    assert "from '../utils/friendlyError.js'" in src, (
        "SourcesSidebar must import friendlyError"
    )
    assignments = re.findall(r"friendlyError\(", src)
    # File picker, folder picker, scan-folder, per-file index, folder index,
    # background-job error, loadDocuments, deleteDocument — at least 6 sites.
    assert len(assignments) >= 6, (
        f"SourcesSidebar.vue should route error-message construction through "
        f"friendlyError; found only {len(assignments)} call sites"
    )
