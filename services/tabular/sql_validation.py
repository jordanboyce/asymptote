"""Read-only SQL validation for LLM- and user-supplied queries.

Used by every caller that lets a SELECT cross a trust boundary — the chat
tool-use path, the MCP table-query tool, and the HoldingsStore's own
``execute_query`` entrypoint.
"""

from __future__ import annotations

import re


_FORBIDDEN_KEYWORDS = {
    'INSERT', 'UPDATE', 'DELETE', 'DROP', 'ALTER', 'CREATE', 'ATTACH',
    'DETACH', 'PRAGMA', 'VACUUM', 'REINDEX', 'TRUNCATE',
    'GRANT', 'REVOKE', 'BEGIN', 'COMMIT', 'ROLLBACK', 'SAVEPOINT',
}


class SQLValidationError(ValueError):
    """Raised when a user/LLM-supplied SQL string fails validation."""


def validate_select(sql: str) -> str:
    """Validate that *sql* is a single read-only SELECT / WITH statement.

    Returns the cleaned SQL (no trailing semicolon). Raises
    :class:`SQLValidationError` on any violation.
    """
    if not sql or not sql.strip():
        raise SQLValidationError('SQL query is empty')

    cleaned = sql.strip().rstrip(';').strip()
    if ';' in cleaned:
        raise SQLValidationError('Multiple statements are not allowed')

    # Blank out quoted string literals so keywords inside them don't trip the check
    stripped = re.sub(r"'([^'\\]|\\.)*'", "''", cleaned)
    stripped = re.sub(r'"([^"\\]|\\.)*"', '""', stripped)
    upper = stripped.upper()

    for kw in _FORBIDDEN_KEYWORDS:
        if re.search(rf'\b{kw}\b', upper):
            raise SQLValidationError(f'Forbidden keyword: {kw}')

    first_word = re.match(r'^\s*(\w+)', upper)
    if not first_word or first_word.group(1) not in {'SELECT', 'WITH'}:
        raise SQLValidationError('Only SELECT / WITH queries are allowed')

    return cleaned
