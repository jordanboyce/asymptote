"""Middleware that redacts PII from all MCP tool output before it exits Asymptote.

The middleware walks the return value of every MCP tool response (dicts,
lists, strings) and applies Presidio-based redaction to every string value.
Redactions are logged to the audit trail.

Integration: called from mcp_server.py after each tool produces its result
dict, before the response is serialized and sent to the calling LLM.
"""

from __future__ import annotations

import logging
import re
import uuid
from contextvars import ContextVar
from typing import Any

from config import settings

logger = logging.getLogger(__name__)

# Context variable tracking the current MCP session for audit grouping
_current_session_id: ContextVar[str] = ContextVar(
    "redaction_session_id", default=""
)

# Keys that are REMOVED entirely from MCP output (contain local-only metadata)
_STRIP_KEYS = frozenset({
    "source_path",  # local disk path — exposes OS username and PII in filenames
})

# Keys whose values may contain PII embedded in identifiers (underscores,
# hyphens, camelCase). For these keys, also check a normalized (spaces) form.
_NORMALIZE_KEYS = frozenset({
    "name",
    "collection_name",
    "description",
    "filename",
    "sheet_name",
})

# Keys whose string values should NOT be redacted (structural identifiers)
_SKIP_KEYS = frozenset({
    "collection_id",
    "document_id",
    "table_name",
    "sql_name",
    "raw_sql_name",
    "sql",
    "mode",
    "source_format",
    "source_type",
    "extraction_method",
    "embedding_model",
    "schema_version",
    "search_modes",
    "type",
    "role",
    "agg_fn",
    "aggregate_col",
    "group_by",
    "sort_by",
    "identifier",
    "metric",
    "chunk_id",
    "server_id",
    "server_url",
    # Numeric-as-string fields that are never PII
    "similarity_score",
    "rank",
    "page_number",
    "chunk_index",
    "line_start",
    "line_end",
    "total_documents",
    "total_chunks",
    "total_pages",
    "total_results",
    "total_tables",
    "row_count",
    "column_count",
    "total_row_count",
})


def get_or_create_session_id() -> str:
    """Get current session id, creating one if needed."""
    sid = _current_session_id.get("")
    if not sid:
        sid = str(uuid.uuid4())
        _current_session_id.set(sid)
    return sid


def set_session_id(session_id: str) -> None:
    _current_session_id.set(session_id)


def redact_mcp_response(
    response: dict[str, Any],
    tool_name: str,
    collection_id: str | None = None,
) -> dict[str, Any]:
    """Redact PII from an MCP tool response dict.

    Walks the entire response structure, redacting string values that
    contain PII. Structural keys (IDs, SQL names, modes) are skipped.

    Returns the redacted response dict (mutated in place for efficiency).
    """
    if not getattr(settings, "enable_pii_redaction", False):
        return response

    from services.privacy.redaction_engine import redaction_engine, RedactionDetail
    from services.privacy.redaction_log import redaction_log

    if not redaction_engine.available:
        return response

    session_id = get_or_create_session_id()
    all_details: list[RedactionDetail] = []

    # Resolve collection_id from the response if not provided
    if not collection_id:
        collection_id = response.get("collection_id")

    _walk_and_redact(response, collection_id, all_details)

    # Log all redactions from this tool call
    if all_details:
        redaction_log.log_redactions(
            details=all_details,
            session_id=session_id,
            collection_id=collection_id,
            tool_name=tool_name,
        )
        logger.info(
            f"Redacted {len(all_details)} PII entities from {tool_name} response "
            f"(session={session_id[:8]}...)"
        )

    return response


def _walk_and_redact(
    obj: Any,
    collection_id: str | None,
    details: list,
    parent_key: str | None = None,
) -> Any:
    """Recursively walk a data structure and redact string values in place."""
    if isinstance(obj, dict):
        for key in list(obj.keys()):
            if key in _STRIP_KEYS:
                del obj[key]
                continue
            if key in _SKIP_KEYS:
                continue
            obj[key] = _walk_and_redact(obj[key], collection_id, details, parent_key=key)
        return obj
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            obj[i] = _walk_and_redact(item, collection_id, details, parent_key=parent_key)
        return obj
    elif isinstance(obj, str):
        # Skip very short strings (unlikely to contain PII) and hint text
        if len(obj) < 4:
            return obj
        from services.privacy.redaction_engine import redaction_engine
        result = redaction_engine.redact_text(obj, collection_id)
        if result.had_pii:
            details.extend(result.details)
            return result.redacted_text
        # For metadata keys (names, filenames), also check a normalized form
        # since Presidio misses "margarett_sullivan" but catches "margarett sullivan"
        if parent_key in _NORMALIZE_KEYS:
            normalized = re.sub(r"[_\-]+", " ", obj)
            if normalized != obj:
                norm_result = redaction_engine.redact_text(normalized, collection_id)
                if norm_result.had_pii:
                    details.extend(norm_result.details)
                    return norm_result.redacted_text
        return obj
    else:
        return obj
