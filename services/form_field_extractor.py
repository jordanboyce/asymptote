"""Utilities for extracting form-like key/value fields from OCR text."""

from __future__ import annotations

import re
from typing import Dict


_KEY_VALUE_PATTERNS = [
    # Key: Value / Key = Value
    re.compile(r"^(?P<key>[A-Za-z][A-Za-z0-9 /&().,_-]{1,90}?)\s*[:=]\s*(?P<value>.+)$"),
    # Key - Value (with surrounding whitespace)
    re.compile(r"^(?P<key>[A-Za-z][A-Za-z0-9 /&().,_-]{1,90}?)\s+-\s+(?P<value>.+)$"),
    # Key .... Value (common in forms)
    re.compile(r"^(?P<key>[A-Za-z][A-Za-z0-9 /&().,_-]{1,90}?)\s+\.{2,}\s*(?P<value>.+)$"),
]


def _clean_line(line: str) -> str:
    """Normalize a line for pattern matching."""
    line = line.strip()
    line = re.sub(r"^#{1,6}\s+", "", line)  # markdown headings
    line = re.sub(r"^[*\-\u2022]\s+", "", line)  # bullets
    line = re.sub(r"\s+", " ", line)
    return line.strip()


def _clean_key(key: str) -> str:
    key = key.strip(" \t:=-.|")
    key = re.sub(r"\s+", " ", key)
    return key


def _clean_value(value: str) -> str:
    value = value.strip(" \t|")
    value = re.sub(r"\s+", " ", value)
    return value


def estimate_form_likelihood(text: str) -> float:
    """
    Estimate whether text is form-like (0.0-1.0).

    Higher scores indicate the content likely contains key/value style data.
    """
    if not text or not text.strip():
        return 0.0

    lines = [_clean_line(line) for line in text.splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return 0.0

    total = len(lines)
    kv_hits = 0
    table_hits = 0
    dotted_hits = 0
    short_lines = 0
    long_sentence_lines = 0

    for line in lines:
        if len(line.split()) <= 8:
            short_lines += 1
        if len(line.split()) >= 18:
            long_sentence_lines += 1

        if "..." in line:
            dotted_hits += 1

        if line.startswith("|") and line.endswith("|"):
            parts = [p.strip() for p in line.split("|") if p.strip()]
            if len(parts) == 2:
                table_hits += 1

        for pattern in _KEY_VALUE_PATTERNS:
            if pattern.match(line):
                kv_hits += 1
                break

    kv_ratio = kv_hits / total
    table_ratio = table_hits / total
    dotted_ratio = dotted_hits / total
    short_ratio = short_lines / total
    long_ratio = long_sentence_lines / total

    # Weighted heuristic:
    # - key/value and table signatures increase form-likeness
    # - many long sentence lines reduce it (likely narrative text)
    score = (
        (0.60 * kv_ratio) +
        (0.20 * table_ratio) +
        (0.10 * dotted_ratio) +
        (0.15 * short_ratio) -
        (0.35 * long_ratio)
    )

    # Clamp to [0, 1]
    return max(0.0, min(1.0, score))


def is_form_like_text(text: str, threshold: float = 0.22) -> bool:
    """Return True if text likely represents a form-like document layout."""
    return estimate_form_likelihood(text) >= threshold


def extract_form_fields(text: str, max_fields: int = 50) -> Dict[str, str]:
    """
    Extract likely key/value pairs from OCR text.

    This is heuristic and intended for form-like documents.
    """
    fields: Dict[str, str] = {}
    if not text or not text.strip():
        return fields

    for raw_line in text.splitlines():
        if len(fields) >= max_fields:
            break

        line = _clean_line(raw_line)
        if not line or len(line) < 4:
            continue

        # Skip lines that are mostly separators/punctuation.
        non_alnum = sum(1 for ch in line if not ch.isalnum())
        if non_alnum / max(len(line), 1) > 0.7:
            continue

        # Handle markdown table row: | key | value |
        if line.startswith("|") and line.endswith("|"):
            parts = [p.strip() for p in line.split("|") if p.strip()]
            if len(parts) == 2:
                key = _clean_key(parts[0])
                value = _clean_value(parts[1])
                if key and value and key.lower() not in fields:
                    fields[key.lower()] = value
            continue

        for pattern in _KEY_VALUE_PATTERNS:
            match = pattern.match(line)
            if not match:
                continue

            key = _clean_key(match.group("key"))
            value = _clean_value(match.group("value"))
            if not key or not value:
                continue

            # Filter obvious false positives.
            if len(key) < 2 or len(key.split()) > 10:
                continue
            if value.lower() in {"n/a", "na", "none", "null"}:
                continue

            normalized_key = key.lower()
            if normalized_key not in fields:
                fields[normalized_key] = value
            break

    # Restore user-friendly key casing in output.
    pretty_fields: Dict[str, str] = {}
    for norm_key, value in fields.items():
        pretty_key = " ".join(part.capitalize() for part in norm_key.split())
        pretty_fields[pretty_key] = value

    return pretty_fields
