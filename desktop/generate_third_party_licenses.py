"""Generate THIRD_PARTY_LICENSES.txt from installed Python packages.

Walks the active virtual environment's site-packages, extracts each
package's name, version, license metadata, and full license text, and
writes them to a single concatenated file at the project root. The
PyInstaller spec ships this file inside the bundle so end users have
the third-party notices required by MIT/BSD/Apache attribution clauses.

Run as part of the desktop build, after `pip install -r ...` and before
PyInstaller. Stdlib-only — no extra deps needed.
"""
from __future__ import annotations

import sys
import sysconfig
from email.parser import Parser
from pathlib import Path

LICENSE_FILE_NAMES = (
    "LICENSE",
    "LICENSE.txt",
    "LICENSE.md",
    "LICENSE.rst",
    "LICENCE",
    "LICENCE.txt",
    "COPYING",
    "COPYING.txt",
    "NOTICE",
    "NOTICE.txt",
)


def find_license_text(dist_info: Path) -> str | None:
    """Return the concatenated text of any license files in dist-info."""
    texts: list[str] = []
    for name in LICENSE_FILE_NAMES:
        p = dist_info / name
        if p.is_file():
            try:
                texts.append(p.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                continue

    licenses_dir = dist_info / "licenses"
    if licenses_dir.is_dir():
        for f in sorted(licenses_dir.rglob("*")):
            if not f.is_file():
                continue
            try:
                rel = f.relative_to(dist_info)
                body = f.read_text(encoding="utf-8", errors="replace")
                texts.append(f"--- {rel.as_posix()} ---\n{body}")
            except Exception:
                continue

    return "\n\n".join(t.strip() for t in texts if t.strip()) or None


def extract_metadata(dist_info: Path) -> dict[str, str]:
    meta_file = dist_info / "METADATA"
    if not meta_file.is_file():
        meta_file = dist_info / "PKG-INFO"
    if not meta_file.is_file():
        return {}
    try:
        text = meta_file.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return {}
    headers, _, _ = text.partition("\n\n")
    parsed = Parser().parsestr(headers)
    return {
        "name": parsed.get("Name", "") or "",
        "version": parsed.get("Version", "") or "",
        "license": (parsed.get("License", "") or "").splitlines()[0] if parsed.get("License") else "",
        "license_expression": parsed.get("License-Expression", "") or "",
        "homepage": parsed.get("Home-page", "") or parsed.get("Project-URL", "") or "",
    }


def main() -> int:
    site_packages = Path(sysconfig.get_paths()["purelib"])
    if not site_packages.is_dir():
        print(f"ERROR: site-packages not found: {site_packages}", file=sys.stderr)
        return 1

    project_root = Path(__file__).resolve().parents[1]
    output = project_root / "THIRD_PARTY_LICENSES.txt"

    dist_infos = sorted(site_packages.glob("*.dist-info"), key=lambda p: p.name.lower())

    sep = "=" * 72
    lines: list[str] = [
        "Finn -- Third-Party Software Notices",
        sep,
        "",
        "Finn includes open-source software components, listed below, each",
        "governed by its own license terms. Those terms apply only to the",
        "components themselves, not to Finn. Finn itself is licensed under",
        "the End-User License Agreement included with the installer.",
        "",
        "This file is generated automatically at build time from package",
        "metadata. If a component's license text is not reproduced below",
        "in full, it is available at the component's project homepage.",
        "",
        sep,
        "",
    ]

    seen: set[str] = set()
    count = 0
    for d in dist_infos:
        meta = extract_metadata(d)
        name = meta.get("name", "")
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)

        version = meta.get("version", "")
        license_id = meta.get("license_expression") or meta.get("license") or ""
        homepage = meta.get("homepage", "")
        text = find_license_text(d)

        header = f"{name} {version}".strip()
        lines.append(f"--- {header} ---")
        if license_id:
            lines.append(f"License: {license_id}")
        if homepage:
            lines.append(f"Homepage: {homepage}")
        lines.append("")
        if text:
            lines.append(text.strip())
        else:
            lines.append(
                "(License text not bundled with package; see homepage for "
                "the full terms.)"
            )
        lines.append("")
        lines.append(sep)
        lines.append("")
        count += 1

    output.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {count} package notices to {output.relative_to(project_root)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
