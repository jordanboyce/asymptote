"""Encoding, delimiter, and decimal-convention detection.

The failure these guard against is silent. A Windows-1252 export read as UTF-8
either raises or mangles security names; a semicolon file read with ``sep=','``
lands every row in one column; a European number read with US assumptions comes
out three orders of magnitude small. None of those announce themselves.
"""

from __future__ import annotations

import pandas as pd
import pytest

from services.tabular.dialect import (
    FileDialect,
    describe,
    detect_decimal_convention,
    detect_delimiter,
    detect_encoding,
    sniff_dialect,
)


def _write(tmp_path, name: str, data, encoding: str = 'utf-8'):
    path = tmp_path / name
    path.write_bytes(data.encode(encoding) if isinstance(data, str) else data)
    return path


# --------------------------------------------------------------------------
# Encoding
# --------------------------------------------------------------------------

def test_plain_ascii_is_utf8(tmp_path):
    path = _write(tmp_path, 'a.csv', 'Symbol,Value\nAAPL,100\nMSFT,200\n')
    encoding, confidence, _ = detect_encoding(path)
    assert encoding == 'utf-8'
    assert confidence > 0.9


def test_utf8_bom_is_detected_and_stripped(tmp_path):
    path = _write(tmp_path, 'a.csv', '﻿Symbol,Value\nAAPL,100\n')
    dialect = sniff_dialect(path)
    assert dialect.encoding == 'utf-8-sig'
    df = pd.read_csv(path, **dialect.pandas_kwargs())
    # Without utf-8-sig the first column name carries an invisible BOM and
    # every lookup by name fails.
    assert list(df.columns) == ['Symbol', 'Value']


def test_windows_1252_accents_survive(tmp_path):
    """The case that decides whether a prospect's names come through intact."""
    text = 'Name,Value\nNestlé S.A.,100\nSociété Générale,200\nZürich,300\n'
    path = _write(tmp_path, 'a.csv', text, encoding='cp1252')

    dialect = sniff_dialect(path)
    assert dialect.encoding == 'cp1252'

    df = pd.read_csv(path, **dialect.pandas_kwargs())
    assert list(df['Name']) == ['Nestlé S.A.', 'Société Générale', 'Zürich']
    assert '�' not in ''.join(df['Name'])


def test_utf16_with_bom_is_detected(tmp_path):
    """Excel's "Unicode Text" export — UTF-16-LE with a BOM and tabs."""
    text = 'Symbol\tValue\nAAPL\t100\nMSFT\t200\nJNJ\t300\n'
    path = _write(tmp_path, 'a.csv', b'\xff\xfe' + text.encode('utf-16-le'))

    dialect = sniff_dialect(path)
    # 'utf-16', not 'utf-16-le': the explicit-endian codec would leave the
    # byte-order mark in the text as a character.
    assert dialect.encoding == 'utf-16'
    assert dialect.delimiter == '\t'

    df = pd.read_csv(path, **dialect.pandas_kwargs())
    assert list(df.columns) == ['Symbol', 'Value']
    assert len(df) == 3


def test_byte_order_mark_does_not_leak_into_the_first_column_name(tmp_path):
    """The BOM must be consumed, not decoded into the header.

    Left in, it becomes an invisible first character of the first column's
    name, and every lookup by that name misses.
    """
    text = 'Symbol\tValue\nAAPL\t100\nMSFT\t200\nJNJ\t300\n'
    path = _write(tmp_path, 'a.csv', b'\xff\xfe' + text.encode('utf-16-le'))

    dialect = sniff_dialect(path)
    from services.tabular.dialect import decode_sample
    assert not decode_sample(path, dialect.encoding).startswith('﻿')


def test_utf16_without_bom_is_detected_from_nul_pattern(tmp_path):
    text = 'Symbol,Value\nAAPL,100\nMSFT,200\nJNJ,300\nXOM,400\n'
    path = _write(tmp_path, 'a.csv', text.encode('utf-16-le'))
    encoding, _, source = detect_encoding(path)
    assert encoding == 'utf-16-le'
    assert 'no byte-order mark' in source


def test_encoding_detection_never_raises_on_binary(tmp_path):
    path = _write(tmp_path, 'a.csv', bytes(range(256)) * 8)
    dialect = sniff_dialect(path)          # must not raise
    assert dialect.encoding


# --------------------------------------------------------------------------
# Delimiter
# --------------------------------------------------------------------------

@pytest.mark.parametrize('delimiter', [',', ';', '\t', '|'])
def test_each_supported_delimiter_is_found(delimiter):
    rows = ['Symbol,Name,Qty,Price'.replace(',', delimiter)]
    rows += [delimiter.join(['AAPL', 'Apple', str(i), '100.5']) for i in range(8)]
    found, confidence, _, _ = detect_delimiter('\n'.join(rows))
    assert found == delimiter
    assert confidence > 0.5


def test_preamble_lines_do_not_beat_the_data_block():
    """A header block above the table must not decide the delimiter.

    Preamble lines disagree with each other about field count, so the scorer
    finds the consistent block underneath them.
    """
    text = (
        'Portfolio Holdings Report\n'
        'As Of: 31.12.2025\n'
        'Currency: EUR\n'
        '\n'
        'Wertpapier;Symbol;Kurswert\n'
        + '\n'.join(f'Name {i};SYM{i};1.234,56' for i in range(10))
    )
    found, _, _, _ = detect_delimiter(text)
    assert found == ';'


def test_commas_inside_quoted_fields_are_not_delimiters():
    text = (
        'Name;Value\n'
        + '\n'.join(f'"Company {i}, Inc.";1.0{i}0,00' for i in range(8))
    )
    found, _, _, _ = detect_delimiter(text)
    assert found == ';'


def test_single_column_file_falls_back_to_comma_with_zero_confidence():
    text = '\n'.join(f'Line {i}' for i in range(10))
    found, confidence, source, _ = detect_delimiter(text)
    assert found == ','
    assert confidence == 0.0
    assert 'single column' in source


# --------------------------------------------------------------------------
# Decimal convention
# --------------------------------------------------------------------------

def test_european_grouped_numbers_switch_the_decimal_separator():
    text = 'A;B\n' + '\n'.join('1.234,56;98.765,43' for _ in range(6))
    decimal, thousands, evidence = detect_decimal_convention(text, ';')
    assert (decimal, thousands) == (',', '.')
    assert evidence['eu_grouped'] > 0


def test_us_grouped_numbers_keep_the_dot():
    text = 'A,B\n' + '\n'.join('1,234.56,98,765.43' for _ in range(6))
    decimal, thousands, _ = detect_decimal_convention(text, ',')
    assert (decimal, thousands) == ('.', ',')


def test_ambiguous_file_defaults_to_us_convention():
    """Absence of evidence must not flip the convention.

    Guessing European on an American file turns "1,234" into 1.234 — the same
    catastrophic error, applied to the common case.
    """
    text = 'A,B\nAAPL,100\nMSFT,200\nJNJ,300\n'
    decimal, thousands, _ = detect_decimal_convention(text, ',')
    assert (decimal, thousands) == ('.', ',')


def test_european_file_parses_to_the_right_magnitude(tmp_path):
    """End to end: the number a European advisor means is the number stored."""
    text = 'Wertpapier;Kurswert\nNestlé;1.234,56\nZürich;98.765,43\nKone;4.500,00\n'
    path = _write(tmp_path, 'eu.csv', text, encoding='cp1252')

    dialect = sniff_dialect(path)
    df = pd.read_csv(path, **dialect.pandas_kwargs())

    assert list(df['Kurswert']) == [1234.56, 98765.43, 4500.00]
    # The failure being prevented: read as US, this sums to about 104.
    assert df['Kurswert'].sum() == pytest.approx(104_499.99)


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

def test_plain_utf8_csv_is_not_reported_as_interesting():
    """The report must stay quiet about a file that needed no interpretation."""
    assert describe(FileDialect()) is None
    assert FileDialect().is_non_default is False


def test_non_default_dialect_describes_itself_in_plain_language():
    dialect = FileDialect(encoding='cp1252', delimiter=';', decimal=',')
    sentence = describe(dialect)
    assert 'Windows-1252' in sentence
    assert 'semicolon' in sentence
    assert 'European' in sentence
