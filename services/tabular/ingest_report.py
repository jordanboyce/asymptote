"""Plain-language account of what happened to an uploaded file.

Finn's ingest pipeline already does a lot of quiet work: it finds the real
header row under a preamble, matches a vendor profile, types each column,
maps columns to financial roles, and (via
:mod:`services.tabular.aggregate_guard`) knows when SQLite's TEXT coercion
would silently produce a wrong total. All of that has been invisible.

This Module turns that work into a report the person who uploaded the file
can read. Two audiences, one artifact:

* **The advisor** wants to know their messy export landed intact, and to be
  told — before they quote a number to a client — which columns they cannot
  trust for arithmetic.
* **The prospect watching a demo** wants proof that the parse was real and
  not a happy path.

The report is assembled from two sources. *Ingest-time evidence* (vendor
profile, header offset, dropped preamble) is only knowable while the file is
being read, so :class:`~services.financial.holdings_store.HoldingsStore`
persists it. *Findings* are computed live against the stored table, so they
stay honest if the data is re-ingested and work retroactively on tables
ingested before this Module existed.

Nothing here mutates data or suppresses a result — it only describes. The
same principle as the aggregate guard: surface the uncertainty, never
collapse it.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any, Dict, List, Optional, Sequence

from services.tabular.aggregate_guard import human_numeric_value, is_sum_hazard

logger = logging.getLogger(__name__)


# Cap the per-column scan. Brokerage exports run to hundreds or low thousands
# of rows, so this is effectively a full scan; the cap only protects against a
# pathologically large sheet. When it bites, the report says so rather than
# quietly reporting partial counts as if they were totals.
_MAX_SCAN_ROWS = 20_000

# Blank cells are ordinary in brokerage exports: a flat Pershing file carries a
# summary row per position followed by its lot rows, so half of `Symbol` and
# `Trade Date` being empty is the format working correctly, not a defect. Only
# a column that is *almost entirely* empty says something the advisor doesn't
# already know — that a field came through with nothing in it at all.
_MOSTLY_EMPTY_RATIO = 0.9

# Below this share of numeric values, a column with some numbers in it is
# probably a text column that happens to contain digits (an address, a note),
# not a money column with holes in it.
_NUMERIC_COLUMN_RATIO = 0.4

# Roles whose columns an advisor will do arithmetic on. An untrustworthy
# column in this set is a blocker; elsewhere it's a warning.
_MONEY_ROLES = frozenset({
    'market_value', 'cost_basis', 'pnl', 'price', 'quantity',
    'weight', 'return', 'coupon',
})

# Column types that carry a number, used to decide whether an unmapped column
# is worth flagging. A stray unmapped text column is noise; a stray unmapped
# currency column might be the one holding the total.
_NUMERIC_TYPES = frozenset({'real', 'integer', 'currency', 'percent'})

# Plain-language names for the roles in services.financial.roles. The report is
# read by people who do not know what "pnl" means as a field name.
ROLE_LABELS: Dict[str, str] = {
    'ticker': 'Ticker symbol',
    'cusip': 'CUSIP',
    'isin': 'ISIN',
    'name': 'Security name',
    'quantity': 'Quantity',
    'price': 'Price',
    'cost_basis': 'Cost basis',
    'market_value': 'Market value',
    'pnl': 'Gain / loss',
    'weight': 'Portfolio weight',
    'asset_class': 'Asset class',
    'sector': 'Sector',
    'region': 'Region',
    'currency': 'Currency',
    'date': 'Date',
    'return': 'Return',
    'account': 'Account',
    'maturity': 'Maturity',
    'coupon': 'Coupon / rate',
    'rating': 'Credit rating',
}

# How each role was decided, in words the advisor can act on. "Guessed by AI"
# is deliberately blunt — an LLM-inferred role is the one worth checking.
ROLE_SOURCE_LABELS: Dict[str, str] = {
    'profile': 'Matched a known export format',
    'heuristic': 'Recognised from the column name',
    'name+values': 'Column name and cell contents agree',
    'values': 'Recognised from the cell contents',
    'arithmetic': 'Confirmed by arithmetic against other columns',
    'llm': 'Guessed by AI — worth a check',
}

# Sources ordered by how much an advisor should trust them, weakest first.
# Used only to decide what to draw attention to.
_WEAK_ROLE_SOURCES = frozenset({'llm'})

# Report grades, worst first. `unreliable` does not mean the file failed to
# load; it means at least one number in it would be wrong if quoted.
GRADE_UNRELIABLE = 'unreliable'
GRADE_NEEDS_REVIEW = 'needs_review'
GRADE_CLEAN = 'clean'

SEVERITY_ORDER = {'blocker': 0, 'warning': 1, 'info': 2}


def friendly_role(role: Optional[str]) -> Optional[str]:
    """Human-readable label for a financial role, or None if unmapped."""
    if not role:
        return None
    return ROLE_LABELS.get(role, role.replace('_', ' ').capitalize())


class _ColumnScan:
    """What a full pass over one stored column found.

    Counts are exact unless ``truncated`` is set, in which case they describe
    the first ``_MAX_SCAN_ROWS`` rows only.
    """

    __slots__ = (
        'total', 'nulls', 'text_typed', 'numeric_values', 'non_numeric_text',
        'sum_hazards', 'hazard_example', 'non_numeric_examples', 'truncated',
    )

    def __init__(self) -> None:
        self.total = 0
        self.nulls = 0
        self.text_typed = 0
        self.numeric_values = 0       # reads as a number to a person
        self.non_numeric_text = 0     # text that is not a number at all
        self.sum_hazards = 0          # SQLite would mis-total these
        self.hazard_example = ''
        self.non_numeric_examples: List[str] = []
        self.truncated = False

    @property
    def non_null(self) -> int:
        return self.total - self.nulls

    @property
    def numeric_ratio(self) -> float:
        return self.numeric_values / self.non_null if self.non_null else 0.0


def _scan_column(
    conn: sqlite3.Connection,
    table: str,
    column: str,
) -> Optional[_ColumnScan]:
    """Walk a column's stored values, tallying what a total would do to them.

    Returns None when the column can't be read — the caller declines to judge
    rather than guessing, matching the aggregate guard's posture.
    """
    safe_table = table.replace('"', '""')
    safe_column = column.replace('"', '""')
    try:
        cursor = conn.execute(
            f'SELECT typeof("{safe_column}"), "{safe_column}" '
            f'FROM "{safe_table}" LIMIT {_MAX_SCAN_ROWS + 1}'
        )
        rows: Sequence[Any] = cursor.fetchall()
    except sqlite3.Error as exc:
        logger.debug('Ingest report could not scan %s.%s: %s', table, column, exc)
        return None

    scan = _ColumnScan()
    if len(rows) > _MAX_SCAN_ROWS:
        scan.truncated = True
        rows = rows[:_MAX_SCAN_ROWS]

    for storage_type, value in rows:
        scan.total += 1
        if value is None or (isinstance(value, str) and not value.strip()):
            scan.nulls += 1
            continue
        if (storage_type or '').lower() != 'text':
            # Stored as a real number — SQLite will total it correctly.
            scan.numeric_values += 1
            continue
        scan.text_typed += 1
        as_str = str(value)
        if human_numeric_value(as_str) is None:
            scan.non_numeric_text += 1
            if len(scan.non_numeric_examples) < 3 and as_str not in scan.non_numeric_examples:
                scan.non_numeric_examples.append(as_str.strip()[:60])
            continue
        scan.numeric_values += 1
        if is_sum_hazard(as_str):
            scan.sum_hazards += 1
            if not scan.hazard_example:
                scan.hazard_example = as_str
    return scan


def _plural(count: int, singular: str, plural: Optional[str] = None) -> str:
    """`1 row` / `2 rows`, so findings read as sentences rather than templates."""
    word = singular if count == 1 else (plural or singular + 's')
    return f'{count} {word}'


def _quote_list(values: Sequence[str]) -> str:
    """Render up to three example values as a readable inline list."""
    quoted = [f'"{v}"' for v in values]
    if len(quoted) == 1:
        return quoted[0]
    if len(quoted) == 2:
        return f'{quoted[0]} and {quoted[1]}'
    return f'{", ".join(quoted[:-1])}, and {quoted[-1]}'


def _column_findings(
    column: Dict[str, Any],
    scan: _ColumnScan,
) -> List[Dict[str, Any]]:
    """Everything worth saying about one column."""
    findings: List[Dict[str, Any]] = []
    name = column.get('name') or column.get('sql_name') or ''
    role = column.get('role')
    label = friendly_role(role)

    # The headline case, and the reason this Module exists: a column that is
    # mostly numbers but stored as text, where SQLite's coercion disagrees with
    # what a person reading the cell would say. Totals are silently truncated.
    if scan.sum_hazards and scan.numeric_ratio >= _NUMERIC_COLUMN_RATIO:
        blocking = role in _MONEY_ROLES
        detail_parts = []
        if scan.non_numeric_text:
            detail_parts.append(
                f'{_plural(scan.non_numeric_text, "row")} hold text instead of a '
                f'number ({_quote_list(scan.non_numeric_examples)}), so the whole '
                'column was stored as text.'
            )
        detail_parts.append(
            f'Adding this column up would read "{scan.hazard_example}" as '
            f'{_sqlite_reading(scan.hazard_example)} instead of '
            f'{_human_reading(scan.hazard_example)}.'
        )
        findings.append({
            'code': 'untrusted_totals',
            'severity': 'blocker' if blocking else 'warning',
            'column': name,
            'headline': f'Totals from "{name}" would be wrong',
            'detail': ' '.join(detail_parts),
            'suggestion': (
                'Finn will refuse to total this column rather than report a '
                'wrong number. Fix the text rows in the source file to unlock it.'
            ),
            'evidence': {
                'rows_scanned': scan.total,
                'text_rows': scan.non_numeric_text,
                'example_value': scan.hazard_example,
                'example_values': scan.non_numeric_examples,
            },
        })

    # A near-empty column usually means a field the export was supposed to
    # carry came through with nothing in it.
    elif scan.total and scan.nulls / scan.total >= _MOSTLY_EMPTY_RATIO and role:
        pct = round(100 * scan.nulls / scan.total)
        findings.append({
            'code': 'mostly_empty',
            'severity': 'warning' if role in _MONEY_ROLES else 'info',
            'column': name,
            'headline': f'"{name}" is {pct}% empty',
            'detail': (
                f'{_plural(scan.nulls, "row")} of {scan.total} have no value. '
                'Anything computed from this column covers only the rows that do.'
            ),
            'suggestion': (
                'Check whether the export was filtered or whether these rows '
                'genuinely have no value.'
            ),
            'evidence': {'rows_scanned': scan.total, 'empty_rows': scan.nulls},
        })

    # An AI-guessed role is the one an advisor should confirm — it's the only
    # role source that isn't backed by a vendor profile or an explicit name match.
    if column.get('role_source') == 'llm' and label:
        findings.append({
            'code': 'ai_guessed_role',
            'severity': 'warning',
            'column': name,
            'headline': f'"{name}" was read as {label} — by guess',
            'detail': (
                'The column name did not match anything Finn recognises, so an '
                'AI model inferred what it holds. Every other column here was '
                'matched by name or by a known export format.'
            ),
            'suggestion': 'Confirm this is right before relying on it.',
            'evidence': {'role': role},
        })

    # A role the resolver assigned but is not confident about. Distinct from
    # the AI guess above: the evidence here is real but thin — usually a name
    # that half-matches two roles with no arithmetic able to settle it. Saying
    # so is the whole point; the alternative is a number that looks certain.
    band = column.get('role_band')
    if band == 'uncertain' and label and column.get('role_source') not in ('profile', 'llm'):
        alternatives = [
            friendly_role(alt.get('role'))
            for alt in (column.get('role_alternatives') or [])[:2]
        ]
        detail = (
            f'Finn read "{name}" as {label}, but the evidence was thin — '
            + (column.get('role_signals') or ['nothing else in the file confirmed it'])[0]
            + '.'
        )
        if alternatives:
            detail += f' It could also be {" or ".join(a for a in alternatives if a)}.'
        findings.append({
            'code': 'uncertain_role',
            'severity': 'warning' if role in _MONEY_ROLES else 'info',
            'column': name,
            'headline': f'"{name}" was read as {label} — not certain',
            'detail': detail,
            'suggestion': (
                'Check this column before using it in a total.'
                if role in _MONEY_ROLES else 'Worth a glance.'
            ),
            'evidence': {
                'role': role,
                'confidence': column.get('role_confidence'),
                'signals': column.get('role_signals') or [],
            },
        })

    return findings


def _unnamed_finding(unnamed: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """One line covering every column Finn kept but could not name.

    Emitted as a single grouped finding rather than one per column: a wide
    export routinely has half a dozen columns with no financial role, and six
    near-identical cards would bury the one finding that matters.
    """
    numeric = [c for c in unnamed if c.get('type') in _NUMERIC_TYPES]
    if not numeric:
        return None
    names = [c['name'] for c in numeric if c.get('name')]
    return {
        'code': 'unmapped_numeric',
        'severity': 'info',
        'column': None,
        'headline': (
            f'{_plural(len(numeric), "column")} of numbers Finn could not name'
        ),
        'detail': (
            f'{_quote_list(names[:4])}'
            + (f' and {len(names) - 4} more' if len(names) > 4 else '')
            + '. Finn kept every one and can answer questions about them by '
              'name, but they are left out of portfolio totals because their '
              'meaning is unknown.'
        ),
        'suggestion': 'Ask about one by name in chat to see what it holds.',
        'evidence': {'columns': names},
    }


def _sqlite_reading(text: str) -> str:
    """Format what SQLite would make of a value, for use in a sentence."""
    from services.tabular.aggregate_guard import _sqlite_numeric_value
    return _format_number(_sqlite_numeric_value(str(text)))


def _human_reading(text: str) -> str:
    """Format what a person would make of a value, for use in a sentence."""
    value = human_numeric_value(str(text))
    return _format_number(value) if value is not None else str(text)


def _format_number(value: float) -> str:
    """Render a number the way it would appear in the sentence, not in code."""
    if value == int(value):
        return f'{int(value):,}'
    return f'{value:,.2f}'


def _file_findings(evidence: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Findings about the file as a whole rather than a single column."""
    findings: List[Dict[str, Any]] = []

    header_row = evidence.get('header_row') or 0
    if header_row:
        preamble = evidence.get('preamble') or {}
        named = [k for k in preamble if not str(k).startswith('_')]
        detail = (
            f'The column headings were on line {header_row + 1}, not line 1. '
            f'Finn skipped {_plural(header_row, "line")} of report header above them'
        )
        detail += f' and kept what it found there ({", ".join(named[:4])}).' if named else '.'
        findings.append({
            'code': 'header_offset',
            'severity': 'info',
            'column': None,
            'headline': f'Skipped {_plural(header_row, "line")} above the column headings',
            'detail': detail,
            'suggestion': None,
            'evidence': {'header_row': header_row, 'preamble_fields': named},
        })

    if evidence.get('vendor_profile'):
        findings.append({
            'code': 'vendor_matched',
            'severity': 'info',
            'column': None,
            'headline': f'Recognised as {evidence["vendor_profile"]}',
            'detail': (
                'Finn has a stored profile for this export format, so column '
                'meanings came from the profile rather than from guesswork.'
            ),
            'suggestion': None,
            'evidence': {'vendor_profile': evidence['vendor_profile']},
        })

    if evidence.get('preprocessor'):
        findings.append({
            'code': 'preprocessed',
            'severity': 'info',
            'column': None,
            'headline': f'Untangled the {evidence["preprocessor"]}',
            'detail': (
                'This export nests holdings under per-account sections rather '
                'than listing them in one flat table. Finn flattened it and '
                'carried the account each row belongs to onto the row itself.'
            ),
            'suggestion': None,
            'evidence': {'preprocessor': evidence['preprocessor']},
        })

    dialect = evidence.get('dialect') or {}
    if dialect:
        bits: List[str] = []
        encoding_label = dialect.get('encoding_label')
        if encoding_label and encoding_label not in ('UTF-8',):
            bits.append(f'the text is encoded as {encoding_label}')
        delimiter_label = dialect.get('delimiter_label')
        if delimiter_label and delimiter_label != 'comma':
            bits.append(f'columns are separated by {delimiter_label}s, not commas')
        if dialect.get('decimal') == ',':
            bits.append(
                'numbers use the European convention, where "1.234,56" means '
                'one thousand two hundred thirty-four and fifty-six hundredths'
            )
        if bits:
            findings.append({
                'code': 'dialect_detected',
                'severity': 'info',
                'column': None,
                'headline': 'Read this file in its own format, not the default one',
                'detail': (
                    'Finn detected that ' + '; '.join(bits) + '. Read with the '
                    'usual assumptions, the names would have come through '
                    'garbled and the numbers would have been wrong.'
                ),
                'suggestion': None,
                'evidence': dialect,
            })

    # One finding, however many columns arithmetic settled. Five cards all
    # headed "Checked the columns against each other" would bury the one
    # finding on the page that needs acting on.
    notes = [n for n in (evidence.get('coherence_notes') or []) if n]
    if notes:
        findings.append({
            'code': 'arithmetic_resolution',
            'severity': 'info',
            'column': None,
            'headline': _plural(len(notes), 'column identified by arithmetic',
                                'columns identified by arithmetic'),
            'detail': (
                'Finn checked the number columns against each other rather '
                'than trusting their names alone. ' + ' '.join(notes)
            ),
            'suggestion': None,
            'evidence': {'notes': notes},
        })

    dropped = evidence.get('dropped_repeated_headers') or 0
    if dropped:
        findings.append({
            'code': 'repeated_headers',
            'severity': 'info',
            'column': None,
            'headline': f'Removed {_plural(dropped, "repeated heading row")}',
            'detail': (
                'Multi-account exports repeat the column headings at the start of '
                'each account section. Left in, they would have been counted as '
                'holdings.'
            ),
            'suggestion': None,
            'evidence': {'dropped_rows': dropped},
        })

    return findings


def _read_as(dialect: Dict[str, Any]) -> Optional[str]:
    """Short badge text for a non-default file format, e.g. ``Windows-1252 · semicolon``.

    ``None`` for an ordinary UTF-8 comma CSV — there is nothing to say, and a
    badge on every file is a badge on none.
    """
    if not dialect:
        return None
    parts = [dialect.get('encoding_label') or '']
    delimiter_label = dialect.get('delimiter_label')
    if delimiter_label and delimiter_label != 'comma':
        parts.append(f'{delimiter_label}-delimited')
    if dialect.get('decimal') == ',':
        parts.append('comma decimals')
    return ' · '.join(p for p in parts if p) or None


def build_ingest_report(
    conn: sqlite3.Connection,
    schema: Dict[str, Any],
    evidence: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Assemble the report for one ingested sheet.

    Parameters
    ----------
    conn:
        Open connection to the Collection's ``metadata.db``. Used read-only.
    schema:
        The persisted schema payload from ``csv_schemas`` — the same shape
        :meth:`HoldingsStore.create_table` returns.
    evidence:
        Ingest-time facts that cannot be recovered from the stored table:
        ``vendor_profile``, ``header_row``, ``preamble``,
        ``dropped_repeated_headers``. Absent for tables ingested before this
        Module existed, in which case the report simply omits those lines.
    """
    evidence = evidence or {}
    table_name = schema.get('table_name') or ''
    columns = schema.get('columns') or []
    row_count = schema.get('row_count') or 0

    findings = _file_findings(evidence)
    understood: List[Dict[str, Any]] = []
    unnamed: List[Dict[str, Any]] = []
    truncated_scan = False

    for column in columns:
        sql_name = column.get('sql_name')
        role = column.get('role')
        entry = {
            'name': column.get('name'),
            'type': column.get('type'),
            'role': role,
            'role_label': friendly_role(role),
            'role_source': column.get('role_source'),
            'role_source_label': ROLE_SOURCE_LABELS.get(column.get('role_source') or ''),
            'role_confidence': column.get('role_confidence'),
            'role_band': column.get('role_band'),
            'role_signals': column.get('role_signals') or [],
            'samples': (column.get('samples') or [])[:3],
        }
        (understood if role else unnamed).append(entry)

        if not sql_name:
            continue
        scan = _scan_column(conn, table_name, sql_name)
        if scan is None:
            continue
        truncated_scan = truncated_scan or scan.truncated
        column_findings = _column_findings(column, scan)
        for finding in column_findings:
            entry.setdefault('flags', []).append(finding['code'])
        findings.extend(column_findings)

    grouped = _unnamed_finding(unnamed)
    if grouped:
        findings.append(grouped)

    findings.sort(key=lambda f: SEVERITY_ORDER.get(f['severity'], 9))

    blockers = [f for f in findings if f['severity'] == 'blocker']
    warnings = [f for f in findings if f['severity'] == 'warning']
    if blockers:
        grade = GRADE_UNRELIABLE
    elif warnings:
        grade = GRADE_NEEDS_REVIEW
    else:
        grade = GRADE_CLEAN

    return {
        'document_id': schema.get('document_id'),
        'filename': schema.get('filename'),
        'sheet_name': schema.get('sheet_name') or '',
        'table_name': table_name,
        'row_count': row_count,
        'column_count': schema.get('column_count') or len(columns),
        'columns_understood': len(understood),
        'recognized_as': evidence.get('vendor_profile'),
        'read_as': _read_as(evidence.get('dialect') or {}),
        'header_row': evidence.get('header_row') or 0,
        'understood': understood,
        'unnamed': unnamed,
        'findings': findings,
        'blocker_count': len(blockers),
        'warning_count': len(warnings),
        'grade': grade,
        'headline': _headline(grade, len(blockers), len(warnings)),
        'scan_truncated': truncated_scan,
    }


def _headline(grade: str, blockers: int, warnings: int) -> str:
    """One sentence the advisor reads before anything else."""
    if grade == GRADE_CLEAN:
        return 'Landed clean — every column checked out.'
    if grade == GRADE_UNRELIABLE:
        subject = _plural(blockers, 'column')
        return f'Loaded, but {subject} would give wrong totals.'
    return f'Loaded, with {_plural(warnings, "thing")} worth a look.'


def summarize_reports(reports: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Roll per-sheet reports up to one verdict for a whole file or collection."""
    if not reports:
        return {
            'grade': GRADE_CLEAN,
            'headline': 'Nothing to report yet.',
            'sheet_count': 0,
            'row_count': 0,
            'blocker_count': 0,
            'warning_count': 0,
        }
    blockers = sum(r.get('blocker_count', 0) for r in reports)
    warnings = sum(r.get('warning_count', 0) for r in reports)
    if blockers:
        grade = GRADE_UNRELIABLE
    elif warnings:
        grade = GRADE_NEEDS_REVIEW
    else:
        grade = GRADE_CLEAN
    return {
        'grade': grade,
        'headline': _headline(grade, blockers, warnings),
        'sheet_count': len(reports),
        'row_count': sum(r.get('row_count', 0) for r in reports),
        'blocker_count': blockers,
        'warning_count': warnings,
    }
