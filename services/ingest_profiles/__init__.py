"""Vendor ingest profiles — deterministic column-role and type overrides.

Usage
-----
    from services.ingest_profiles import detect_profile, apply_profile

    profile = detect_profile(filename="Schwab_Unrealized_2024.csv", columns=df.columns)
    if profile:
        df, role_overrides, type_overrides = apply_profile(profile, df)

Each YAML file in this directory defines one or more profile dicts.  At runtime
all YAML files are loaded once and cached.  Profiles are matched in definition
order; the first match wins.

Profile matching
----------------
A profile matches when ALL of the following that are specified hold:

1. ``filename_patterns`` — at least one fnmatch glob matches the base filename.
2. ``required_strings`` — every string appears somewhere in the first
   ``_SCAN_LINES`` lines of the file (case-insensitive).
3. ``column_hints`` — at least ``_HINT_MATCH_THRESHOLD`` fraction of the
   hint column names appear (case-insensitive) in the actual column list.

If no signature keys are given, the profile is never matched by detection
(it can still be applied manually by vendor name).
"""
from __future__ import annotations

import fnmatch
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_PROFILES_DIR = Path(__file__).parent
_SCAN_LINES = 40          # how many file lines to scan for required_strings
_HINT_MATCH_THRESHOLD = 0.6  # fraction of column_hints that must match

# Loaded once on first import
_PROFILES: Optional[List[Dict[str, Any]]] = None


def _load_profiles() -> List[Dict[str, Any]]:
    global _PROFILES
    if _PROFILES is not None:
        return _PROFILES

    try:
        import yaml  # type: ignore
    except ImportError:
        logger.warning(
            "PyYAML not installed — vendor profile matching disabled. "
            "Install with: pip install pyyaml"
        )
        _PROFILES = []
        return _PROFILES

    profiles: List[Dict[str, Any]] = []
    for yaml_path in sorted(_PROFILES_DIR.glob('*.yaml')):
        try:
            with open(yaml_path, encoding='utf-8') as fh:
                data = yaml.safe_load(fh)
            if isinstance(data, list):
                profiles.extend(data)
            elif isinstance(data, dict):
                profiles.append(data)
        except Exception as e:
            logger.warning(f"Failed to load ingest profile {yaml_path.name}: {e}")

    _PROFILES = profiles
    logger.info(f"Loaded {len(profiles)} vendor ingest profile(s)")
    return _PROFILES


def _head_lines(file_path: Path, n: int) -> str:
    """Return the first n lines of a file as a single lowercased string."""
    lines: List[str] = []
    try:
        with open(file_path, encoding='utf-8', errors='replace') as fh:
            for i, line in enumerate(fh):
                if i >= n:
                    break
                lines.append(line)
    except Exception:
        pass
    return '\n'.join(lines).lower()


def detect_profile(
    filename: str,
    columns: List[str],
    file_path: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """Return the first matching vendor profile, or None if no profile matches.

    Parameters
    ----------
    filename:
        The base filename (or full path — only the stem+suffix is used for
        glob matching).
    columns:
        List of column names as parsed from the file header row.
    file_path:
        Optional path to the physical file, used for ``required_strings``
        scanning.  If omitted, the required_strings check is skipped.
    """
    profiles = _load_profiles()
    if not profiles:
        return None

    base_name = Path(filename).name
    col_set = {c.lower().strip() for c in columns}
    file_head = _head_lines(file_path, _SCAN_LINES) if file_path else ''

    for profile in profiles:
        sig = profile.get('file_signatures', {})
        if not sig:
            continue  # no signature → never auto-match

        # 1. Filename glob
        fn_patterns = sig.get('filename_patterns', [])
        if fn_patterns and not any(fnmatch.fnmatch(base_name, p) for p in fn_patterns):
            continue

        # 2. Required strings in file head
        req_strings = sig.get('required_strings', [])
        if req_strings and file_path:
            if not all(s.lower() in file_head for s in req_strings):
                continue

        # 3. Column hints
        hints = sig.get('column_hints', [])
        if hints:
            hint_set = {h.lower().strip() for h in hints}
            matched = hint_set & col_set
            if len(matched) / len(hint_set) < _HINT_MATCH_THRESHOLD:
                continue

        logger.info(
            f"Matched vendor profile '{profile.get('display_name', profile.get('vendor'))}' "
            f"for file '{base_name}'"
        )
        return profile

    return None


def apply_profile(
    profile: Dict[str, Any],
    columns: List[str],
    rows: List[Dict[str, Any]],
) -> Tuple[List[str], List[Dict[str, Any]], Dict[str, str], Dict[str, str]]:
    """Apply a profile to a sheet's columns and rows.

    Returns
    -------
    columns : List[str]
        Possibly unchanged column list.
    rows : List[Dict[str, Any]]
        Rows with grouping/subtotal rows removed (if ``drop_rows_where_col_empty``
        is set in the profile).
    role_overrides : Dict[str, str]
        Mapping of *original column name* → semantic role.
    type_overrides : Dict[str, str]
        Mapping of *original column name* → forced type string.
    """
    col_role_map: Dict[str, str] = {}
    col_type_map: Dict[str, str] = {}

    col_role_raw: Dict[str, str] = profile.get('column_roles', {})
    col_type_raw: Dict[str, str] = profile.get('type_overrides', {})

    # Build case-insensitive lookup against the actual column names
    col_lower = {c.lower().strip(): c for c in columns}
    for src_col, role in col_role_raw.items():
        actual = col_lower.get(src_col.lower().strip())
        if actual:
            col_role_map[actual] = role
    for src_col, typ in col_type_raw.items():
        actual = col_lower.get(src_col.lower().strip())
        if actual:
            col_type_map[actual] = typ

    # Drop grouping / subtotal rows (e.g. Pershing hierarchical format)
    drop_col_raw = profile.get('drop_rows_where_col_empty')
    if drop_col_raw:
        drop_col = col_lower.get(drop_col_raw.lower().strip())
        if drop_col:
            before = len(rows)
            rows = [
                r for r in rows
                if r.get(drop_col) is not None and str(r.get(drop_col, '')).strip() != ''
            ]
            dropped = before - len(rows)
            if dropped:
                logger.info(
                    f"Profile '{profile.get('display_name')}': dropped {dropped} "
                    f"grouping/subtotal rows (empty '{drop_col}')"
                )

    return columns, rows, col_role_map, col_type_map
