"""Character encoding and CSV dialect detection for arbitrary exports.

The pipeline used to assume every uploaded file was UTF-8 and comma-delimited.
Both assumptions hold for the five profiled US brokerages and fail immediately
outside them:

* **Encoding.** Excel's default "CSV (Comma delimited)" export on a Windows
  machine is Windows-1252, not UTF-8. A security name carrying an accent, an
  em dash, or a smart quote — ``Nestlé S.A.``, ``AT&T Inc.—Class A`` — decodes
  to a replacement character at best and raises ``UnicodeDecodeError`` at
  worst. Excel's "Unicode Text" export is UTF-16 with a BOM and tab
  delimiters; read as UTF-8 it is unusable.
* **Delimiter.** A CSV produced on a machine with a European locale is
  semicolon-delimited, because the comma is the decimal separator there. Read
  with ``sep=','`` the whole row lands in one column and every number is text.
* **Decimal separator.** ``1.234,56`` is one thousand two hundred thirty-four
  point five six. Parsed with US assumptions it becomes ``1.234`` — off by
  three orders of magnitude, silently, in exactly the direction that makes a
  portfolio look small.

Detection here is evidence-producing rather than best-effort: every result
carries how it was decided and how confident the decision is, so
:mod:`services.tabular.ingest_report` can tell the advisor "this file was read
as Windows-1252" instead of leaving a mangled name to be discovered later.

Nothing in this Module reads a whole file — detection works on a bounded head
sample, so cost is independent of file size.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Bytes of the file to sample for detection. Large enough to cover a long
# preamble plus a few dozen data rows; small enough to be free on a 200MB file.
_SAMPLE_BYTES = 128 * 1024

# Lines of the decoded sample to consider when scoring delimiters. Preamble
# lines are structurally unlike data rows, so the scorer needs enough lines to
# find the consistent block underneath them.
_SCORE_LINES = 60

# Delimiters worth testing, in preference order for ties. Comma first because
# an ambiguous file is far more likely to be American than not.
_CANDIDATE_DELIMITERS = (',', ';', '\t', '|')

# A delimiter must produce at least this many fields on the rows it explains,
# otherwise "every line has exactly 1 field" would score as perfectly
# consistent for any character that never appears.
_MIN_FIELDS = 2

# Byte-order marks, longest first — UTF-32-LE starts with the same two bytes
# as UTF-16-LE, so order matters.
#
# Each maps to the codec that *consumes* the mark rather than the explicit
# endian one. Decoding BOM-prefixed bytes with 'utf-16-le' leaves U+FEFF as
# the first character, which silently becomes part of the first column's name
# — so a lookup for "Symbol" misses a column called "﻿Symbol". The plain
# 'utf-16' / 'utf-32' codecs read the mark for endianness and drop it.
_BOMS: Tuple[Tuple[bytes, str], ...] = (
    (b'\x00\x00\xfe\xff', 'utf-32'),
    (b'\xff\xfe\x00\x00', 'utf-32'),
    (b'\xef\xbb\xbf',     'utf-8-sig'),
    (b'\xfe\xff',         'utf-16'),
    (b'\xff\xfe',         'utf-16'),
)

# Encodings we are willing to land on, and how to describe them to a human.
ENCODING_LABELS: Dict[str, str] = {
    'utf-8':      'UTF-8',
    'utf-8-sig':  'UTF-8 (with byte-order mark)',
    'utf-16':     'UTF-16',
    'utf-16-le':  'UTF-16 (little-endian, no byte-order mark)',
    'utf-16-be':  'UTF-16 (big-endian, no byte-order mark)',
    'utf-32':     'UTF-32',
    'cp1252':     'Windows-1252',
    'latin-1':    'Latin-1 (ISO-8859-1)',
    'cp1251':     'Windows-1251 (Cyrillic)',
    'shift_jis':  'Shift-JIS',
}

# A number written the European way: dot-grouped thousands and a comma
# decimal. Requires the grouping to be exact 3-digit runs, so "3.14,15" does
# not match and a bare "3,14" is handled separately.
_EU_GROUPED_RE = re.compile(r'^-?\(?\d{1,3}(?:\.\d{3})+,\d+\)?$')
# The US mirror image: comma-grouped thousands and a dot decimal.
_US_GROUPED_RE = re.compile(r'^-?\(?\d{1,3}(?:,\d{3})+(?:\.\d+)?\)?$')
# Ambiguous short forms — "1,50" could be either convention.
_COMMA_DECIMAL_RE = re.compile(r'^-?\(?\d+,\d{1,2}\)?$')
_DOT_DECIMAL_RE = re.compile(r'^-?\(?\d+\.\d{1,2}\)?$')
# Strips currency symbols and whitespace before convention testing.
_CURRENCY_STRIP_RE = re.compile(r'[^\d.,()\-]')


@dataclass
class FileDialect:
    """How a delimited text file should be read, and why we think so.

    ``confidence`` is a 0..1 score for the delimiter decision specifically —
    encoding carries its own ``encoding_confidence``. Callers should read the
    file with these settings regardless of confidence; the score exists so the
    ingest report can flag a shaky call for a human rather than to gate
    parsing on a threshold.
    """
    encoding: str = 'utf-8'
    encoding_confidence: float = 1.0
    encoding_source: str = 'default'
    delimiter: str = ','
    confidence: float = 1.0
    delimiter_source: str = 'default'
    quotechar: str = '"'
    decimal: str = '.'
    thousands: Optional[str] = ','
    evidence: Dict[str, Any] = field(default_factory=dict)

    @property
    def encoding_label(self) -> str:
        return ENCODING_LABELS.get(self.encoding, self.encoding)

    @property
    def delimiter_label(self) -> str:
        return {
            ',': 'comma', ';': 'semicolon', '\t': 'tab', '|': 'pipe',
        }.get(self.delimiter, repr(self.delimiter))

    @property
    def is_non_default(self) -> bool:
        """True when this file needed anything other than plain UTF-8 CSV.

        The ingest report only mentions the dialect when it was interesting;
        telling an advisor their comma-delimited UTF-8 file was read as
        comma-delimited UTF-8 is noise.
        """
        return (
            self.encoding not in ('utf-8', 'utf-8-sig')
            or self.delimiter != ','
            or self.decimal != '.'
        )

    def pandas_kwargs(self) -> Dict[str, Any]:
        """Keyword arguments for ``pandas.read_csv`` matching this dialect.

        ``thousands`` is deliberately omitted when it is a comma and the
        decimal is a dot: pandas applies ``thousands`` only to columns it
        already decided are numeric, and Finn's own inference layer
        (:mod:`services.tabular.inference`) handles grouped numbers with more
        care than pandas does — including accounting parentheses and mixed
        text/number columns. Passing it here would coerce some columns before
        the guard in :mod:`services.tabular.aggregate_guard` gets to see the
        raw form. For European files the separator swap genuinely changes the
        value, so it is passed.
        """
        kwargs: Dict[str, Any] = {
            'encoding': self.encoding,
            'sep': self.delimiter,
            'quotechar': self.quotechar,
        }
        if self.decimal != '.':
            kwargs['decimal'] = self.decimal
            if self.thousands and self.thousands != self.decimal:
                kwargs['thousands'] = self.thousands
        return kwargs


def _read_sample(path: Path, limit: int = _SAMPLE_BYTES) -> bytes:
    try:
        with open(path, 'rb') as fh:
            return fh.read(limit)
    except OSError as e:
        logger.warning(f"dialect: could not sample {path.name}: {e}")
        return b''


def _bom_encoding(sample: bytes) -> Optional[str]:
    for bom, enc in _BOMS:
        if sample.startswith(bom):
            return enc
    return None


def _looks_like_utf16_without_bom(sample: bytes) -> Optional[str]:
    """Detect BOM-less UTF-16 from the NUL-byte pattern of ASCII content.

    ASCII text encoded as UTF-16 puts a NUL beside every character. Which side
    the NULs land on gives the endianness. Requires a decisive majority so a
    binary file with incidental NULs does not qualify.
    """
    if len(sample) < 16:
        return None
    # Work on an even-length prefix so the odd/even split is meaningful.
    body = sample[: len(sample) - (len(sample) % 2)]
    evens = body[0::2]
    odds = body[1::2]
    if not evens or not odds:
        return None
    even_nulls = evens.count(0) / len(evens)
    odd_nulls = odds.count(0) / len(odds)
    if odd_nulls > 0.6 and even_nulls < 0.1:
        return 'utf-16-le'
    if even_nulls > 0.6 and odd_nulls < 0.1:
        return 'utf-16-be'
    return None


def detect_encoding(path: Path) -> Tuple[str, float, str]:
    """Return ``(encoding, confidence, source)`` for a text file.

    Order of evidence, strongest first: a byte-order mark is definitive; a
    clean strict UTF-8 decode is near-definitive because non-UTF-8 byte
    sequences almost always break it; then a BOM-less UTF-16 NUL pattern;
    then whatever ``charset_normalizer`` concludes; then Windows-1252 as the
    floor, chosen over Latin-1 because it decodes the same byte range but maps
    0x80–0x9F to the smart quotes and dashes Excel actually emits.
    """
    sample = _read_sample(path)
    if not sample:
        return 'utf-8', 1.0, 'empty'

    bom = _bom_encoding(sample)
    if bom:
        return bom, 1.0, 'byte-order mark'

    # The NUL-pattern test has to run *before* the UTF-8 test, not after it.
    # BOM-less UTF-16 of ASCII content decodes cleanly as UTF-8 — NUL is a
    # valid UTF-8 byte — so a UTF-8-first order would accept every such file
    # and hand back text with a NUL between every character. Genuine UTF-8
    # text files do not carry interleaved NULs, so this ordering costs
    # nothing.
    utf16 = _looks_like_utf16_without_bom(sample)
    if utf16:
        return utf16, 0.85, 'UTF-16 byte pattern (no byte-order mark)'

    try:
        sample.decode('utf-8')
        return 'utf-8', 0.99, 'decoded cleanly as UTF-8'
    except UnicodeDecodeError as exc:
        # A truncated multi-byte character at the sample boundary is not
        # evidence of anything — retry without the tail.
        if exc.start >= len(sample) - 4:
            try:
                sample[: exc.start].decode('utf-8')
                return 'utf-8', 0.95, 'decoded cleanly as UTF-8'
            except UnicodeDecodeError:
                pass

    try:
        from charset_normalizer import from_bytes  # type: ignore

        best = from_bytes(sample).best()
        if best is not None and best.encoding:
            enc = str(best.encoding).lower().replace('_', '-')
            # charset_normalizer reports the ISO name; prefer the Windows
            # superset, which decodes the same bytes plus 0x80–0x9F.
            if enc in ('iso-8859-1', 'latin-1', 'ascii'):
                enc = 'cp1252'
            elif enc == 'windows-1252':
                enc = 'cp1252'
            try:
                decoded = sample.decode(enc)
            except (UnicodeDecodeError, LookupError):
                return 'cp1252', 0.4, 'fallback (detector result was not decodable)'

            # Single-byte Windows codepages overlap heavily. The detector
            # readily picks cp1250 (Central European) for a file that is
            # really cp1252, and on Western European text the two decode
            # identically — so when they do, say cp1252, which is what a
            # Windows Excel export actually is. Where they genuinely differ,
            # the detector's answer stands.
            if enc.startswith('cp125') and enc != 'cp1252':
                try:
                    if sample.decode('cp1252') == decoded:
                        return 'cp1252', 0.75, 'character-set detection'
                except UnicodeDecodeError:
                    pass
            return enc, 0.75, 'character-set detection'
    except ImportError:
        logger.debug("dialect: charset_normalizer unavailable, using cp1252 fallback")

    return 'cp1252', 0.5, 'fallback for non-UTF-8 bytes'


def decode_sample(path: Path, encoding: str) -> str:
    """Decode the head of a file with a known encoding, tolerating a cut tail."""
    sample = _read_sample(path)
    if not sample:
        return ''
    try:
        return sample.decode(encoding, errors='replace')
    except LookupError:
        return sample.decode('utf-8', errors='replace')


def _count_fields(line: str, delimiter: str, quotechar: str = '"') -> int:
    """Field count for one line, respecting quotes.

    Hand-rolled rather than handed to :mod:`csv` because a single malformed
    line should cost one line of evidence, not raise and abandon the file.
    """
    count = 1
    in_quotes = False
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if ch == quotechar:
            # A doubled quote inside a quoted field is an escaped quote.
            if in_quotes and i + 1 < n and line[i + 1] == quotechar:
                i += 2
                continue
            in_quotes = not in_quotes
        elif ch == delimiter and not in_quotes:
            count += 1
        i += 1
    return count


def _score_delimiter(lines: List[str], delimiter: str) -> Tuple[float, int]:
    """Score how well a delimiter explains a block of lines.

    Returns ``(score, modal_field_count)``. The score rewards a delimiter that
    yields the *same* field count on many lines — the signature of a real
    table — rather than one that merely appears often. A preamble of prose
    lines above the header can never dominate, because prose lines disagree
    with each other about field counts.
    """
    counts: Dict[int, int] = {}
    for line in lines:
        if not line.strip():
            continue
        c = _count_fields(line, delimiter)
        if c >= _MIN_FIELDS:
            counts[c] = counts.get(c, 0) + 1
    if not counts:
        return 0.0, 0

    modal_count, modal_lines = max(counts.items(), key=lambda kv: (kv[1], kv[0]))
    non_blank = sum(1 for line in lines if line.strip()) or 1

    # Share of lines the modal width explains: a real table explains most of
    # the file. Squared so a delimiter explaining 90% beats one explaining 60%
    # by more than 1.5x.
    consistency = (modal_lines / non_blank) ** 2
    # Wider tables are stronger evidence than 2-column ones, with sharply
    # diminishing returns past a handful of columns.
    width_bonus = min(modal_count - 1, 8) / 8.0
    # A delimiter that explains only one or two lines is noise regardless of
    # how consistent those lines are.
    if modal_lines < 2:
        return 0.0, modal_count
    return consistency * (0.55 + 0.45 * width_bonus), modal_count


def detect_delimiter(text: str) -> Tuple[str, float, str, Dict[str, Any]]:
    """Return ``(delimiter, confidence, source, evidence)`` for decoded text.

    Scores every candidate against the same lines and takes the winner, with
    confidence derived from the *margin* over the runner-up. A file that two
    delimiters explain equally well (a one-column file, say) yields low
    confidence and the comma, which is what the old code assumed anyway — the
    difference is that now the report can say the call was ambiguous.
    """
    lines = [ln for ln in text.splitlines()[:_SCORE_LINES]]
    if not lines:
        return ',', 0.0, 'empty file', {}

    scores: Dict[str, Tuple[float, int]] = {
        d: _score_delimiter(lines, d) for d in _CANDIDATE_DELIMITERS
    }
    ranked = sorted(
        scores.items(),
        key=lambda kv: (kv[1][0], -_CANDIDATE_DELIMITERS.index(kv[0])),
        reverse=True,
    )
    best_delim, (best_score, best_width) = ranked[0]
    runner_score = ranked[1][1][0] if len(ranked) > 1 else 0.0

    evidence = {
        'scores': {
            ('tab' if d == '\t' else d): round(s, 3) for d, (s, _) in scores.items()
        },
        'columns_found': best_width,
    }

    if best_score <= 0.0:
        # Nothing looked like a table — a single-column file, or one line.
        return ',', 0.0, 'no delimiter found (single column?)', evidence

    # Margin over the runner-up is what makes the call trustworthy, not the
    # absolute score: a ragged table can score 0.5 and still be unambiguous.
    margin = (best_score - runner_score) / best_score
    confidence = round(min(1.0, 0.5 + 0.5 * margin), 3)
    return best_delim, confidence, 'field-count consistency', evidence


def detect_decimal_convention(
    text: str,
    delimiter: str,
) -> Tuple[str, Optional[str], Dict[str, Any]]:
    """Return ``(decimal, thousands, evidence)`` by inspecting numeric cells.

    The decisive evidence is a *grouped* number, because grouping is
    unambiguous: ``1.234,56`` can only be European and ``1,234.56`` can only
    be American. Ungrouped values like ``12,50`` are counted but never decide
    on their own, since a US file can hold ``1,234`` with no decimal part.

    Defaults to the US convention. Being wrong in the American direction on a
    European file mis-parses; being wrong in the European direction on an
    American file would turn ``1,234`` into 1.234 — the same catastrophic
    error, but applied to the common case. So the bar for switching is
    positive evidence, not absence of evidence.
    """
    eu_grouped = us_grouped = comma_dec = dot_dec = 0

    for line in text.splitlines()[:_SCORE_LINES]:
        if not line.strip():
            continue
        for raw_cell in line.split(delimiter):
            cell = _CURRENCY_STRIP_RE.sub('', raw_cell.strip())
            if not cell or not any(ch.isdigit() for ch in cell):
                continue
            if _EU_GROUPED_RE.match(cell):
                eu_grouped += 1
            elif _US_GROUPED_RE.match(cell):
                us_grouped += 1
            elif _COMMA_DECIMAL_RE.match(cell):
                comma_dec += 1
            elif _DOT_DECIMAL_RE.match(cell):
                dot_dec += 1

    evidence = {
        'eu_grouped': eu_grouped,
        'us_grouped': us_grouped,
        'comma_decimal': comma_dec,
        'dot_decimal': dot_dec,
    }

    # Unambiguous grouped forms decide it outright.
    if eu_grouped and eu_grouped > us_grouped:
        return ',', '.', evidence
    if us_grouped and us_grouped > eu_grouped:
        return '.', ',', evidence

    # No grouped numbers anywhere. Commas can only be decimals in a
    # semicolon-delimited file (in a comma-delimited one they would have split
    # the field), so that combination plus a clear comma-decimal majority is
    # enough. Two decimal places is the giveaway — money.
    if delimiter == ';' and comma_dec >= 3 and comma_dec > dot_dec:
        return ',', '.', evidence

    return '.', ',', evidence


def sniff_dialect(path: Path) -> FileDialect:
    """Detect encoding, delimiter, and numeric convention for a delimited file.

    This is the single entry point the extractor uses. It never raises: an
    undetectable file falls back to UTF-8 comma CSV with zero confidence,
    which is exactly what the pipeline did unconditionally before, so the
    worst case is the old behaviour plus an honest confidence score.
    """
    dialect = FileDialect()
    try:
        encoding, enc_conf, enc_source = detect_encoding(path)
        dialect.encoding = encoding
        dialect.encoding_confidence = enc_conf
        dialect.encoding_source = enc_source

        text = decode_sample(path, encoding)
        delimiter, delim_conf, delim_source, delim_evidence = detect_delimiter(text)
        dialect.delimiter = delimiter
        dialect.confidence = delim_conf
        dialect.delimiter_source = delim_source

        decimal, thousands, dec_evidence = detect_decimal_convention(text, delimiter)
        dialect.decimal = decimal
        dialect.thousands = thousands

        dialect.evidence = {
            'delimiter': delim_evidence,
            'numbers': dec_evidence,
            'encoding_source': enc_source,
        }
    except Exception as e:  # pragma: no cover — defensive
        logger.warning(f"dialect: detection failed for {path.name}: {e}; using defaults")
        return FileDialect(confidence=0.0, delimiter_source='detection failed')

    if dialect.is_non_default:
        logger.info(
            f"dialect: {path.name} read as {dialect.encoding_label}, "
            f"{dialect.delimiter_label}-delimited"
            + (", comma decimals" if dialect.decimal == ',' else '')
        )
    return dialect


def open_text(path: Path, dialect: FileDialect):
    """Open a file for text reading using a detected dialect.

    ``errors='replace'`` stays as a floor: the detector picks an encoding that
    decodes the *sample*, and a rare byte further into the file should degrade
    one character rather than fail the upload.
    """
    return open(path, 'r', encoding=dialect.encoding, errors='replace', newline='')


def csv_reader(path: Path, dialect: FileDialect):
    """Yield rows from a delimited file using a detected dialect."""
    with open_text(path, dialect) as fh:
        yield from csv.reader(fh, delimiter=dialect.delimiter, quotechar=dialect.quotechar)


def describe(dialect: FileDialect) -> Optional[str]:
    """One plain sentence about how the file was read, or None if unremarkable."""
    if not dialect.is_non_default:
        return None
    parts = [f'read as {dialect.encoding_label}']
    if dialect.delimiter != ',':
        parts.append(f'{dialect.delimiter_label}-delimited')
    if dialect.decimal == ',':
        parts.append('with comma decimal separators (European format)')
    return 'This file was ' + ', '.join(parts) + '.'
