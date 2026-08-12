"""Guards a Vue footgun that only shows up at runtime, on one code path.

In `<script setup>`, a ref must be read as `x.value`. In the *template* the
same ref auto-unwraps, so `x` is correct and `x.value` is a second unwrap —
which silently evaluates to `undefined`. It renders fine until something
indexes it:

    providerModelLoading.value[def.id]
    -> undefined['ollama_cloud']
    -> TypeError: Cannot read properties of undefined (reading 'ollama_cloud')

That exact bug shipped in SettingsTab.vue's provider "Refresh" button and only
surfaced when a provider card rendered. Static text like `{{ x.value }}` would
have failed silently forever, so a test is the only thing that catches it.
"""

import re
from pathlib import Path

import pytest

_COMPONENTS = Path(__file__).resolve().parent.parent / "frontend" / "src"

# `const foo = ref(...)`, `computed(...)`, `shallowRef(...)`, `reactive(...)` is
# NOT included — reactive() objects are not refs and have no .value to unwrap.
_REF_DECL = re.compile(
    r'\bconst\s+([A-Za-z_$][\w$]*)\s*=\s*(?:ref|shallowRef|computed|toRef|customRef)\s*\(',
)


def _vue_files():
    return sorted(_COMPONENTS.rglob("*.vue"))


def _split(src: str) -> tuple[str, str]:
    """Return (template_part, script_part) for a single-file component."""
    idx = src.find("<script")
    return (src, "") if idx < 0 else (src[:idx], src[idx:])


@pytest.mark.parametrize(
    "path", _vue_files(), ids=lambda p: p.name,
)
def test_template_does_not_double_unwrap_refs(path):
    template, script = _split(path.read_text(encoding="utf-8", errors="ignore"))
    refs = set(_REF_DECL.findall(script))
    if not refs:
        return

    offenders = []
    for lineno, line in enumerate(template.splitlines(), 1):
        for match in re.finditer(r'\b([A-Za-z_$][\w$]*)\.value\b', line):
            if match.group(1) in refs:
                offenders.append(f"  {path.name}:{lineno}: {line.strip()[:120]}")

    assert not offenders, (
        f"{path.name}: ref(s) read with `.value` inside the template. Refs "
        f"auto-unwrap there, so `.value` yields undefined and any indexing "
        f"into it throws at runtime. Drop the `.value`:\n" + "\n".join(offenders)
    )


def test_the_guard_actually_detects_the_shipped_bug():
    """Meta-test: prove the detector catches the real SettingsTab regression.

    Without this, a broken matcher would make every component 'pass'.
    """
    template, script = _split(
        "<template>\n"
        '  <button :disabled="providerModelLoading.value[def.id]" />\n'
        "</template>\n"
        "<script setup>\n"
        "const providerModelLoading = ref({})\n"
        "</script>\n"
    )
    refs = set(_REF_DECL.findall(script))
    assert "providerModelLoading" in refs
    found = [
        m.group(1)
        for line in template.splitlines()
        for m in re.finditer(r'\b([A-Za-z_$][\w$]*)\.value\b', line)
        if m.group(1) in refs
    ]
    assert found == ["providerModelLoading"]
