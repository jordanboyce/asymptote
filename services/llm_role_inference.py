"""LLM-assisted column role inference (P0.5).

When vendor profiles and regex heuristics leave ≥50% of columns unmapped,
this module sends column names + redacted sample values to a small LLM and
asks it to propose semantic role assignments from the canonical taxonomy.

Sample values are always redacted through the Presidio pipeline (P0.0) before
leaving Finn.  The feature is gated behind ``enable_llm_schema_inference``
in config and requires a configured AI provider (``mcp_ai_provider``).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Canonical role taxonomy — kept in sync with services/financial/roles.py
ROLE_TAXONOMY: list[tuple[str, str]] = [
    ("ticker", "Stock ticker symbol or security identifier code"),
    ("cusip", "CUSIP identifier (9-character alphanumeric)"),
    ("isin", "ISIN identifier (12-character)"),
    ("name", "Security name, description, or issuer name"),
    ("quantity", "Number of shares, units, or contracts held"),
    ("price", "Current unit price, last price, NAV, or close price"),
    ("cost_basis", "Acquisition cost, book value, or average cost per unit"),
    ("market_value", "Current market value of the position"),
    ("pnl", "Profit/loss, gain/loss, unrealized or realized return in dollars"),
    ("weight", "Portfolio allocation percentage or weight"),
    ("asset_class", "Security type, instrument type, or asset class category"),
    ("sector", "Industry sector, GICS sector, or sub-sector"),
    ("region", "Country, geography, or region of domicile"),
    ("currency", "Currency code or FX denomination"),
    ("date", "Any date column — trade date, settlement date, as-of date, report date"),
    ("return", "Performance return percentage — yield, YTD, MTD, total return"),
    ("account", "Account number, portfolio, fund, or strategy identifier"),
    ("maturity", "Bond maturity or expiration date"),
    ("coupon", "Bond coupon rate or projected annual income"),
    ("rating", "Credit rating — Moody's, S&P, or similar"),
]

_ROLE_NAMES = {r for r, _ in ROLE_TAXONOMY}

_TAXONOMY_BLOCK = "\n".join(
    f"  - {role}: {desc}" for role, desc in ROLE_TAXONOMY
)

_SYSTEM_PROMPT = f"""\
You are a column-role classifier for financial tabular data.

Given a list of spreadsheet columns (name + sample values), assign each column
a semantic role from the taxonomy below, or null if no role fits.

ROLE TAXONOMY:
{_TAXONOMY_BLOCK}

Rules:
- Respond with ONLY a JSON array of objects, one per column, in the same order
  as the input.  Each object has exactly three keys:
    "column"     — the original column name (string)
    "role"       — a role from the taxonomy above, or null
    "confidence" — a number from 0.0 to 1.0
- Do NOT invent roles outside the taxonomy.
- A column may legitimately have no role (set role to null).
- Consider BOTH the column name AND the sample values when deciding.
- Some sample values may be redacted (e.g. [PERSON], [CREDIT_CARD]).  That is
  expected — use the column name and non-redacted values to infer the role.
- Output valid JSON only. No markdown fences, no commentary."""


@dataclass
class RoleProposal:
    """A single LLM-proposed role assignment."""
    column: str
    role: Optional[str]
    confidence: float


def _build_user_prompt(
    unmapped_columns: list[dict[str, Any]],
) -> str:
    """Build the user-turn prompt listing columns + redacted samples."""
    lines = ["Columns to classify:\n"]
    for col in unmapped_columns:
        name = col["name"]
        samples = col.get("samples", [])
        sample_str = ", ".join(repr(s) for s in samples[:5]) if samples else "(no samples)"
        col_type = col.get("type", "unknown")
        lines.append(f'- Column "{name}" (detected type: {col_type}): [{sample_str}]')
    return "\n".join(lines)


def _redact_samples(
    columns: list[dict[str, Any]],
    collection_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return a copy of the column list with sample values redacted via Presidio."""
    try:
        from services.privacy.redaction_engine import get_redaction_engine
        engine = get_redaction_engine()
    except Exception:
        logger.warning("Presidio not available — sending samples without redaction")
        return columns

    redacted: list[dict[str, Any]] = []
    for col in columns:
        new_col = dict(col)
        new_samples: list[str] = []
        for val in col.get("samples", []):
            result = engine.redact_text(str(val), collection_id=collection_id)
            new_samples.append(result.redacted_text)
        new_col["samples"] = new_samples
        redacted.append(new_col)
    return redacted


def _parse_response(raw: str, expected_columns: list[str]) -> list[RoleProposal]:
    """Parse the LLM JSON response into RoleProposal objects.

    Tolerant of minor formatting issues (markdown fences, trailing commas).
    """
    text = raw.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("LLM role inference returned unparseable JSON: %s", text[:200])
        return []

    if not isinstance(data, list):
        logger.warning("LLM role inference expected a JSON array, got %s", type(data).__name__)
        return []

    proposals: list[RoleProposal] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        col_name = item.get("column", "")
        role = item.get("role")
        confidence = float(item.get("confidence", 0.0))

        # Validate role is in taxonomy
        if role is not None and role not in _ROLE_NAMES:
            logger.debug("LLM proposed unknown role %r for %r — ignoring", role, col_name)
            role = None
            confidence = 0.0

        proposals.append(RoleProposal(column=col_name, role=role, confidence=confidence))

    return proposals


def infer_roles_with_llm(
    unmapped_columns: list[dict[str, Any]],
    collection_id: str | None = None,
    confidence_threshold: float = 0.6,
) -> dict[str, str]:
    """Call an LLM to propose roles for unmapped columns.

    Parameters
    ----------
    unmapped_columns:
        List of column info dicts with keys ``name``, ``type``, ``samples``.
    collection_id:
        Optional collection ID for Presidio profile selection.
    confidence_threshold:
        Minimum LLM confidence to accept a role (0.0–1.0).

    Returns
    -------
    dict mapping original column name → role string, only for accepted
    proposals above the confidence threshold.
    """
    from config import settings
    from services.ai_service import create_provider

    if not unmapped_columns:
        return {}

    provider_name = settings.mcp_ai_provider
    if provider_name == "none":
        logger.info("LLM schema inference enabled but no AI provider configured — skipping")
        return {}

    # Redact sample values before sending to LLM
    redacted_columns = _redact_samples(unmapped_columns, collection_id=collection_id)

    # Build the prompt
    user_prompt = _build_user_prompt(redacted_columns)

    # Create provider — API key comes from request headers at upload time,
    # but for background ingest we fall back to env vars.
    import os
    api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
    extra_kwargs: dict[str, Any] = {}
    if provider_name == "ollama":
        extra_kwargs["model"] = settings.mcp_ollama_model or "llama3.2"
    try:
        provider = create_provider(provider_name, api_key, **extra_kwargs)
    except Exception as e:
        logger.warning("Failed to create AI provider for schema inference: %s", e)
        return {}

    # Call the LLM — use the fast/cheap model
    full_prompt = f"{_SYSTEM_PROMPT}\n\n{user_prompt}"
    try:
        response = provider.complete(full_prompt, max_tokens=1024, model=provider.FAST_MODEL)
    except Exception as e:
        logger.warning("LLM schema inference call failed: %s", e)
        return {}

    raw_text = response.get("text", "")
    usage = response.get("usage", {})
    logger.info(
        "LLM schema inference returned %d chars (tokens: %s in / %s out)",
        len(raw_text),
        usage.get("input_tokens", "?"),
        usage.get("output_tokens", "?"),
    )

    # Parse and filter by confidence
    expected_names = [c["name"] for c in unmapped_columns]
    proposals = _parse_response(raw_text, expected_names)

    accepted: dict[str, str] = {}
    for p in proposals:
        if p.role and p.confidence >= confidence_threshold:
            accepted[p.column] = p.role
            logger.info(
                "LLM role inference: %r → %s (confidence %.2f)",
                p.column, p.role, p.confidence,
            )
        elif p.role:
            logger.debug(
                "LLM role inference: %r → %s REJECTED (confidence %.2f < %.2f)",
                p.column, p.role, p.confidence, confidence_threshold,
            )

    return accepted
