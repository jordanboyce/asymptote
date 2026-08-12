"""Multi-signal resolution of table columns to financial roles.

:mod:`services.financial.roles` answers "what is this column?" with an
anchored regex against the column *name*: ``^(market_?value|mv|value|…)$``.
That is exact and fast and it is why Finn handles the five profiled vendors.
It is also why Finn handles nothing else. A column called ``Ending Market
Value``, ``Mkt Val (USD)``, ``Position Value - Base``, or ``Marktwert`` returns
``None``, the portfolio metrics quietly lose their input, and the failure is
invisible because a missing role looks exactly like a column that genuinely
has no role.

This Module resolves the same question from three independent kinds of
evidence and reports how sure it is:

1. **Name.** Token-set matching rather than whole-string matching, with
   brokerage abbreviations expanded (``mkt``→market, ``qty``→quantity,
   ``px``→price) and report modifiers (``ending``, ``current``, ``total``,
   ``USD``) treated as noise rather than as mismatches.
2. **Values.** What the cells actually *are*. A CUSIP is nine characters with
   a computable check digit; an ISIN carries a Luhn checksum; a ticker column
   is short uppercase tokens with high cardinality. These hold no matter what
   the header says, or what language it is in.
3. **Arithmetic coherence.** Quantity × Price should equal Market Value, and
   Market Value − Cost Basis should equal Gain/Loss. When those identities
   hold across the rows, three separate guesses stop being guesses. When they
   fail, that is evidence too — and if a *different* assignment of the same
   columns satisfies the identity, the resolver takes it, which is how a
   mislabeled or entirely unlabeled column gets identified correctly.

Nothing here mutates data. A resolution below :data:`MIN_ASSIGN_CONFIDENCE`
yields no role at all, and every resolution — including the refusals — carries
signals that :mod:`services.tabular.ingest_report` renders in plain language.
The rule from the aggregate guard applies unchanged: surface the uncertainty,
never collapse it.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from services.tabular.inference import NULL_MARKERS, parse_date_like, parse_generic_number

logger = logging.getLogger(__name__)


def _legacy_role(col_name: str, col_type: str) -> Optional[str]:
    """Call the original anchored-regex detector.

    Imported lazily: ``services.financial.roles`` cannot be imported at Module
    scope from here. Reaching it executes ``services/financial/__init__.py``,
    which imports ``holdings_store``, which imports this Module — a cycle that
    fails whenever this Module is the first of the pair to be loaded.
    """
    from services.financial.roles import detect_financial_role
    return detect_financial_role(col_name, col_type)


# Confidence bands. These are the only thresholds in the Module; everything
# else is scored continuously so the bands can move without rewriting rules.
CONFIDENCE_CONFIRMED = 0.85   # independent signals agree, or arithmetic proves it
CONFIDENCE_LIKELY = 0.60      # one strong signal, nothing contradicting it
MIN_ASSIGN_CONFIDENCE = 0.40  # below this the column gets no role at all

# Rows sampled for value-shape and arithmetic tests. Brokerage tables are
# hundreds to low thousands of rows; this is usually the whole table and caps
# the cost on a pathological one.
_SAMPLE_ROWS = 400

# Share of rows an arithmetic identity must hold on to count as confirmation.
# Not 1.0: a real export carries cash lines, fully-redeemed lots, and
# accrued-interest rows that legitimately break the identity.
_COHERENCE_RATIO = 0.80

# Relative tolerance for the identity tests. Brokerage files round quantity to
# 3 decimals and price to 4, so the product drifts from the stated market
# value by well over a floating-point epsilon.
_COHERENCE_TOLERANCE = 0.01

# Minimum rows with all three values present before an arithmetic test is
# allowed to conclude anything.
_MIN_COHERENCE_ROWS = 5

# Distinct values a column must hold before the permutation search will
# consider it. Guards against identities that hold only because the columns
# are constant.
_MIN_DISTINCT_VALUES = 3


# --------------------------------------------------------------------------
# Name normalisation
# --------------------------------------------------------------------------

# Abbreviations that appear in real export headers, expanded before matching.
# Only unambiguous ones: `bal` is deliberately absent because it means balance
# in a cash context and units in a position context.
_ABBREVIATIONS: Dict[str, str] = {
    'mkt': 'market', 'val': 'value', 'vals': 'value', 'mv': 'marketvalue',
    'qty': 'quantity', 'qtys': 'quantity', 'shr': 'shares', 'shrs': 'shares',
    'px': 'price', 'prc': 'price', 'pr': 'price',
    'amt': 'amount', 'amnt': 'amount',
    'ccy': 'currency', 'curr': 'currency', 'cur': 'currency',
    'sec': 'security', 'secs': 'security', 'sym': 'symbol', 'tkr': 'ticker',
    'desc': 'description', 'descr': 'description', 'nm': 'name',
    'unrl': 'unrealized', 'unreal': 'unrealized', 'unrlzd': 'unrealized',
    'rlzd': 'realized', 'realzd': 'realized',
    'gl': 'gainloss', 'pl': 'gainloss', 'pnl': 'gainloss',
    'acct': 'account', 'accnt': 'account', 'ac': 'account',
    'pct': 'percent', 'perc': 'percent', 'pcnt': 'percent',
    'wt': 'weight', 'wgt': 'weight',
    'cb': 'costbasis', 'bkval': 'bookvalue',
    'ytd': 'yeartodate', 'mtd': 'monthtodate', 'qtd': 'quartertodate',
    'yld': 'yield', 'rtn': 'return', 'ret': 'return', 'perf': 'performance',
    'mat': 'maturity', 'exp': 'expiration', 'cpn': 'coupon',
    'num': 'number', 'no': 'number', 'nbr': 'number', 'id': 'identifier',
    'avg': 'average', 'tot': 'total', 'chg': 'change',
    'port': 'portfolio', 'pos': 'position',
}

# Tokens that modify a column without changing what it is. "Ending Market
# Value", "Total Market Value", and "Market Value (USD)" are all the market
# value. Leaving these in would make every real-world header a partial match.
_MODIFIER_TOKENS: Set[str] = {
    'ending', 'end', 'beginning', 'begin', 'opening', 'closing', 'close',
    'current', 'total', 'net', 'gross', 'base', 'local', 'reporting',
    'as', 'of', 'the', 'a', 'and', 'or', 'per', 'in', 'at', 'on', 'for',
    'usd', 'eur', 'gbp', 'cad', 'aud', 'chf', 'jpy', 'dollar', 'dollars',
    'value1', 'col', 'column', 'field', 'data', 'info', 'report',
    'this', 'prior', 'previous', 'last', 'today', 'todays', 'number',
    'level', 'detail', 'summary', 'line', 'item', 'record',
}

# Symbols carrying meaning that would otherwise be stripped as punctuation.
_SYMBOL_WORDS = {'%': ' percent ', '#': ' number ', '$': ' amount ', '&': ' and '}

_SPLIT_RE = re.compile(r'[^a-z0-9]+')
# Splits glued camelCase / PascalCase headers: "MarketValue" -> "Market Value".
_CAMEL_RE = re.compile(r'(?<=[a-z0-9])(?=[A-Z])')


def normalize_tokens(name: str) -> List[str]:
    """Reduce a column name to comparable tokens.

    ``"Mkt Val (USD)"`` → ``['market', 'value']``;
    ``"% of Port."`` → ``['percent', 'portfolio']``;
    ``"UnrealizedGainLoss"`` → ``['unrealized', 'gainloss']``.
    """
    if not name:
        return []
    text = _CAMEL_RE.sub(' ', str(name))
    for symbol, word in _SYMBOL_WORDS.items():
        text = text.replace(symbol, word)
    raw = [t for t in _SPLIT_RE.split(text.lower()) if t]

    tokens: List[str] = []
    for token in raw:
        token = _ABBREVIATIONS.get(token, token)
        if token in _MODIFIER_TOKENS:
            continue
        if token.isdigit():
            continue
        tokens.append(token)
    return tokens


# --------------------------------------------------------------------------
# Name patterns
# --------------------------------------------------------------------------

# (role, required_token_set, weight). A column matches a pattern when it
# contains every token in the set. Weight is the name-signal confidence: 1.0
# for a phrase that can only mean one thing, lower where a human would also
# need context. Patterns are checked in full and the best-scoring one wins, so
# ordering within a role does not matter.
_NAME_PATTERNS: Tuple[Tuple[str, Set[str], float], ...] = (
    # --- identifiers -----------------------------------------------------
    ('ticker',       {'ticker'}, 1.0),
    ('ticker',       {'symbol'}, 0.95),
    ('ticker',       {'security', 'symbol'}, 1.0),
    ('ticker',       {'trading', 'symbol'}, 1.0),
    # Pershing calls its ticker column "Security Identifier". Deliberately
    # weaker than {security, symbol}: the same phrase names a CUSIP column at
    # other custodians, so the cell contents have to break the tie.
    ('ticker',       {'security', 'identifier'}, 0.75),
    ('cusip',        {'security', 'identifier'}, 0.7),
    ('cusip',        {'cusip'}, 1.0),
    ('isin',         {'isin'}, 1.0),
    ('sedol',        {'sedol'}, 1.0),
    ('name',         {'security', 'name'}, 1.0),
    ('name',         {'security', 'description'}, 1.0),
    ('name',         {'description'}, 0.75),
    ('name',         {'holding'}, 0.7),
    ('name',         {'instrument'}, 0.8),
    ('name',         {'issuer'}, 0.8),
    ('name',         {'investment'}, 0.8),
    ('name',         {'fund', 'name'}, 0.9),
    ('name',         {'name'}, 0.6),
    ('account',      {'account'}, 0.95),
    ('account',      {'portfolio', 'identifier'}, 0.9),
    ('account',      {'sleeve'}, 0.8),
    ('account',      {'registration'}, 0.7),

    # --- quantities ------------------------------------------------------
    ('quantity',     {'quantity'}, 1.0),
    ('quantity',     {'shares'}, 0.95),
    ('quantity',     {'units'}, 0.9),
    ('quantity',     {'contracts'}, 0.85),
    ('quantity',     {'face', 'amount'}, 0.8),
    ('quantity',     {'par', 'amount'}, 0.8),
    ('quantity',     {'position', 'size'}, 0.85),

    # --- prices ----------------------------------------------------------
    ('price',        {'price'}, 0.95),
    ('price',        {'market', 'price'}, 1.0),
    ('price',        {'unit', 'price'}, 1.0),
    ('price',        {'nav'}, 0.9),
    ('price',        {'quote'}, 0.75),
    ('price',        {'mark'}, 0.7),

    # --- money -----------------------------------------------------------
    ('market_value', {'market', 'value'}, 1.0),
    ('market_value', {'marketvalue'}, 1.0),
    ('market_value', {'position', 'value'}, 0.95),
    ('market_value', {'position', 'amount'}, 0.8),
    ('market_value', {'holding', 'value'}, 0.9),
    ('market_value', {'notional'}, 0.85),
    ('market_value', {'exposure'}, 0.75),
    ('market_value', {'value'}, 0.65),
    ('market_value', {'balance'}, 0.6),

    ('cost_basis',   {'cost', 'basis'}, 1.0),
    ('cost_basis',   {'costbasis'}, 1.0),
    ('cost_basis',   {'book', 'value'}, 0.95),
    ('cost_basis',   {'bookvalue'}, 0.95),
    ('cost_basis',   {'adjusted', 'cost'}, 1.0),
    ('cost_basis',   {'purchase', 'price'}, 0.9),
    ('cost_basis',   {'acquisition', 'cost'}, 0.95),
    ('cost_basis',   {'average', 'cost'}, 0.9),
    ('cost_basis',   {'unit', 'cost'}, 0.9),
    ('cost_basis',   {'tax', 'cost'}, 0.9),
    ('cost_basis',   {'cost'}, 0.8),

    ('pnl',          {'gainloss'}, 1.0),
    ('pnl',          {'unrealized', 'gain'}, 1.0),
    ('pnl',          {'realized', 'gain'}, 1.0),
    ('pnl',          {'gain'}, 0.85),
    ('pnl',          {'loss'}, 0.8),
    ('pnl',          {'profit'}, 0.85),
    ('pnl',          {'appreciation'}, 0.85),

    # --- ratios ----------------------------------------------------------
    ('weight',       {'weight'}, 0.95),
    ('weight',       {'percent', 'portfolio'}, 1.0),
    ('weight',       {'percent', 'assets'}, 0.9),
    ('weight',       {'allocation'}, 0.9),
    ('weight',       {'allocation', 'percent'}, 1.0),
    ('weight',       {'percent', 'account'}, 0.9),

    ('return',       {'return'}, 0.9),
    ('return',       {'yield'}, 0.85),
    ('return',       {'performance'}, 0.85),
    ('return',       {'yeartodate'}, 0.8),
    # A gain expressed as a percentage is a return, not a P&L amount. Scored
    # above the bare {'gain'} P&L pattern so "Total Gain/Loss %" lands here
    # and "Total Gain/Loss $" does not.
    ('return',       {'gainloss', 'percent'}, 0.95),
    ('return',       {'gain', 'percent'}, 0.95),
    ('return',       {'loss', 'percent'}, 0.9),

    # --- classification --------------------------------------------------
    ('asset_class',  {'asset', 'class'}, 1.0),
    ('asset_class',  {'security', 'type'}, 0.95),
    ('asset_class',  {'instrument', 'type'}, 0.95),
    ('asset_class',  {'category'}, 0.7),
    ('asset_class',  {'asset', 'type'}, 0.95),
    ('sector',       {'sector'}, 1.0),
    ('sector',       {'industry'}, 0.9),
    ('sector',       {'gics'}, 0.95),
    ('region',       {'region'}, 0.95),
    ('region',       {'country'}, 0.9),
    ('region',       {'geography'}, 0.9),
    ('region',       {'domicile'}, 0.85),
    ('currency',     {'currency'}, 0.95),

    # --- dates / terms ---------------------------------------------------
    ('date',         {'date'}, 0.7),
    ('date',         {'trade', 'date'}, 0.95),
    ('date',         {'settlement', 'date'}, 0.95),
    ('date',         {'valuation', 'date'}, 1.0),
    ('date',         {'acquired'}, 0.85),
    ('date',         {'acquisition', 'date'}, 0.95),
    ('maturity',     {'maturity'}, 1.0),
    ('maturity',     {'expiration'}, 0.9),
    ('coupon',       {'coupon'}, 1.0),
    ('coupon',       {'interest', 'rate'}, 0.85),
    ('rating',       {'rating'}, 0.95),
    ('rating',       {'moody'}, 0.95),
)

# Roles that can only sensibly appear once per table. When two columns both
# claim one, the weaker claim is demoted rather than silently duplicated.
_SINGLETON_ROLES: Set[str] = {
    'ticker', 'cusip', 'isin', 'sedol', 'name', 'quantity', 'price',
    'market_value', 'cost_basis', 'weight', 'currency', 'account',
}

# Role names as they read in a sentence. The report Module has richer labels
# for its own UI, but these notes are composed here and "pnl" is not a word.
_ROLE_WORDS: Dict[str, str] = {
    'market_value': 'market value',
    'cost_basis': 'cost basis',
    'pnl': 'gain/loss',
    'asset_class': 'asset class',
}


def _role_words(role: str) -> str:
    return _ROLE_WORDS.get(role, role.replace('_', ' '))


# Roles that must hold numbers. A text column matching one of these by name is
# almost always a label ("Value Category"), not the thing itself.
_NUMERIC_ROLES: Set[str] = {
    'quantity', 'price', 'market_value', 'cost_basis', 'pnl', 'weight',
    'return', 'coupon',
}

_NUMERIC_TYPES: Set[str] = {'integer', 'real', 'currency', 'percent'}


# --------------------------------------------------------------------------
# Value-shape detectors
# --------------------------------------------------------------------------

_CUSIP_RE = re.compile(r'^[0-9A-Z]{8}[0-9A-Z]$')
_ISIN_RE = re.compile(r'^[A-Z]{2}[0-9A-Z]{9}[0-9]$')
_SEDOL_RE = re.compile(r'^[0-9B-DF-HJ-NP-TV-Z]{6}[0-9]$')
_TICKER_RE = re.compile(r'^[A-Z]{1,5}([.\-/][A-Z]{1,3})?$')
_PERCENT_RE = re.compile(r'^-?\(?[\d,]*\.?\d+\)?\s*%$')
_CURRENCY_SYMBOL_RE = re.compile(r'^[$€£¥]\s?-?\(?[\d,]')

# ISO 4217 codes common in advisory books. Used only to recognise a *currency
# column*, never to validate data.
_ISO_CURRENCIES = frozenset({
    'usd', 'eur', 'gbp', 'jpy', 'chf', 'cad', 'aud', 'nzd', 'hkd', 'sgd',
    'sek', 'nok', 'dkk', 'mxn', 'brl', 'zar', 'cny', 'inr', 'krw', 'twd',
})


def _cusip_check_digit(body: str) -> Optional[str]:
    """Compute the CUSIP check digit for the first eight characters.

    Standard modulus-10 double-add-double: letters count as their position in
    the alphabet plus nine, every second value is doubled, digit sums are
    added, and the check digit completes the total to a multiple of ten.
    """
    if len(body) != 8:
        return None
    total = 0
    for i, ch in enumerate(body):
        if ch.isdigit():
            value = int(ch)
        elif ch.isalpha():
            value = ord(ch) - ord('A') + 10
        elif ch == '*':
            value = 36
        elif ch == '@':
            value = 37
        elif ch == '#':
            value = 38
        else:
            return None
        if i % 2 == 1:
            value *= 2
        total += value // 10 + value % 10
    return str((10 - (total % 10)) % 10)


def is_valid_cusip(value: str) -> bool:
    """True when a string is a nine-character CUSIP with a correct check digit.

    The check digit is what makes this worth doing. Nine alphanumerics match
    plenty of things — internal account keys, order IDs; a *correct* check
    digit does not happen by accident, so a column where most values validate
    is a CUSIP column no matter what the header calls it.
    """
    s = str(value).strip().upper()
    if not _CUSIP_RE.match(s):
        return False
    return _cusip_check_digit(s[:8]) == s[8]


def is_valid_isin(value: str) -> bool:
    """True when a string is an ISIN passing its Luhn checksum."""
    s = str(value).strip().upper()
    if not _ISIN_RE.match(s):
        return False
    digits = ''.join(
        str(ord(ch) - ord('A') + 10) if ch.isalpha() else ch for ch in s
    )
    total = 0
    parity = len(digits) % 2
    for i, ch in enumerate(digits):
        d = int(ch)
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _clean_sample(values: Sequence[Any], limit: int = _SAMPLE_ROWS) -> List[str]:
    out: List[str] = []
    for v in values:
        if v is None:
            continue
        s = str(v).strip()
        if not s or s.lower() in NULL_MARKERS:
            continue
        out.append(s)
        if len(out) >= limit:
            break
    return out


def detect_value_shape(values: Sequence[Any]) -> List[Tuple[str, float, str]]:
    """Infer candidate roles from cell contents alone.

    Returns ``(role, confidence, signal)`` triples, strongest first. The
    identifier detectors are checksum-backed and therefore near-certain; the
    softer ones (ticker, currency code) are shape-and-cardinality heuristics
    and score accordingly.
    """
    sample = _clean_sample(values)
    if len(sample) < 3:
        return []

    n = len(sample)
    distinct = len(set(sample))
    results: List[Tuple[str, float, str]] = []

    # A security identifier column names many different securities. Without a
    # cardinality floor the check digit alone is not enough: Fidelity's
    # account number "X12345678" repeats on every row and happens to be a
    # valid CUSIP, which is exactly the sort of confident-and-wrong match this
    # Module exists to avoid.
    identifiers_plausible = distinct >= 4 and distinct / n >= 0.25

    if identifiers_plausible:
        cusip_hits = sum(1 for s in sample if is_valid_cusip(s))
        if cusip_hits / n >= 0.8:
            results.append((
                'cusip', 0.98,
                f'{cusip_hits} of {n} values are valid CUSIPs (check digit verified)',
            ))

        isin_hits = sum(1 for s in sample if is_valid_isin(s))
        if isin_hits / n >= 0.8:
            results.append((
                'isin', 0.98,
                f'{isin_hits} of {n} values are valid ISINs (checksum verified)',
            ))

    if not results and identifiers_plausible:
        upper = [s for s in sample if s.upper() == s]
        ticker_hits = sum(1 for s in sample if _TICKER_RE.match(s.strip()))
        distinct_ratio = distinct / n
        if (
            ticker_hits / n >= 0.85
            and len(upper) / n >= 0.85
            and distinct_ratio >= 0.5
        ):
            results.append((
                'ticker', 0.8,
                f'{ticker_hits} of {n} values look like ticker symbols '
                f'(short, uppercase, {int(distinct_ratio * 100)}% distinct)',
            ))

    currency_hits = sum(1 for s in sample if s.lower() in _ISO_CURRENCIES)
    if currency_hits / n >= 0.9 and len(set(sample)) <= 12:
        results.append((
            'currency', 0.9,
            f'values are ISO currency codes ({", ".join(sorted(set(s.upper() for s in sample))[:4])})',
        ))

    percent_hits = sum(1 for s in sample if _PERCENT_RE.match(s))
    if percent_hits / n >= 0.8:
        results.append((
            'weight', 0.45,
            f'{percent_hits} of {n} values carry a percent sign',
        ))

    date_hits = sum(1 for s in sample if parse_date_like(s) is not None)
    if date_hits / n >= 0.9:
        results.append(('date', 0.6, f'{date_hits} of {n} values parse as dates'))

    results.sort(key=lambda r: r[1], reverse=True)
    return results


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------

@dataclass
class ColumnResolution:
    """What a column was resolved to, how sure we are, and why.

    ``role`` is ``None`` when nothing cleared :data:`MIN_ASSIGN_CONFIDENCE` —
    a deliberate refusal, distinct from "we never looked". ``signals`` is the
    human-readable trail; ``rejected`` records roles that were considered and
    dropped, which is what lets the report say "this could be cost basis or
    market value and I could not tell" instead of going quiet.
    """
    column: str
    role: Optional[str] = None
    confidence: float = 0.0
    source: str = 'none'
    signals: List[str] = field(default_factory=list)
    rejected: List[Tuple[str, float]] = field(default_factory=list)

    @property
    def band(self) -> str:
        if self.role is None:
            return 'unidentified'
        if self.confidence >= CONFIDENCE_CONFIRMED:
            return 'confirmed'
        if self.confidence >= CONFIDENCE_LIKELY:
            return 'likely'
        return 'uncertain'

    @property
    def needs_review(self) -> bool:
        return self.role is not None and self.confidence < CONFIDENCE_LIKELY

    def to_dict(self) -> Dict[str, Any]:
        return {
            'column': self.column,
            'role': self.role,
            'confidence': round(self.confidence, 3),
            'band': self.band,
            'source': self.source,
            'signals': list(self.signals),
            'rejected': [{'role': r, 'score': round(s, 3)} for r, s in self.rejected],
        }


def score_name(column: str) -> List[Tuple[str, float, str]]:
    """Score a column name against every role pattern.

    Returns ``(role, score, signal)`` triples, best first. A role scores by its
    best matching pattern; a small bonus applies when the pattern accounts for
    the whole name, which separates ``Market Value`` from ``Market Value
    Change`` without needing a pattern for every combination.
    """
    tokens = normalize_tokens(column)
    if not tokens:
        return []
    token_set = set(tokens)

    best: Dict[str, Tuple[float, Set[str]]] = {}
    for role, required, weight in _NAME_PATTERNS:
        if not required <= token_set:
            continue
        leftover = token_set - required
        if leftover:
            # Unexplained tokens weaken the match: "Market Value Change" is
            # less certainly the market value than "Market Value" is.
            score = weight * (0.9 ** len(leftover))
        else:
            # The pattern accounts for the entire name. A specific header the
            # author chose deliberately ("Account", "CUSIP") is stronger
            # evidence than any value-shape heuristic, so it gets a bonus that
            # lets it outrank one — checksum-backed detectors included, since
            # those collide with account numbers and internal keys.
            score = min(1.0, weight * (1.12 if weight >= 0.9 else 1.0))
        prev = best.get(role)
        if prev is None or score > prev[0]:
            best[role] = (score, required)

    results: List[Tuple[str, float, str]] = []
    for role, (score, required) in best.items():
        matched = ' + '.join(sorted(required))
        results.append((role, score, f'column name contains "{matched}"'))
    results.sort(key=lambda r: r[1], reverse=True)
    return results


def _combine(
    column: str,
    name_scores: List[Tuple[str, float, str]],
    value_scores: List[Tuple[str, float, str]],
    col_type: Optional[str],
) -> ColumnResolution:
    """Merge name and value evidence for one column into a single resolution."""
    res = ColumnResolution(column=column)

    combined: Dict[str, float] = {}
    signals: Dict[str, List[str]] = {}

    for role, score, signal in name_scores:
        combined[role] = max(combined.get(role, 0.0), score * 0.85)
        signals.setdefault(role, []).append(signal)
    for role, score, signal in value_scores:
        # Value evidence enters at full strength for checksum-backed
        # identifiers and reduced strength for the shape heuristics; the
        # detector already encodes that difference in its score.
        combined[role] = max(combined.get(role, 0.0), score * 0.85)
        signals.setdefault(role, []).append(signal)

    # Independent agreement is the strongest evidence available short of
    # arithmetic: a name that says "CUSIP" over values that validate as CUSIPs
    # is not a coincidence.
    name_roles = {r for r, _, _ in name_scores}
    value_roles = {r for r, _, _ in value_scores}
    for role in name_roles & value_roles:
        combined[role] = min(0.99, combined[role] + 0.25)
        signals.setdefault(role, []).append('name and cell contents agree')

    # A name-only claim on a numeric role that holds text is suspect. This is
    # the "Value Category" case — a label column whose name reads like money.
    if col_type is not None and col_type not in _NUMERIC_TYPES:
        for role in list(combined):
            if role in _NUMERIC_ROLES and role not in value_roles:
                combined[role] *= 0.55
                signals.setdefault(role, []).append(
                    f'stored as {col_type}, not a number — weakens this match'
                )

    if not combined:
        return res

    ranked = sorted(combined.items(), key=lambda kv: kv[1], reverse=True)
    top_role, top_score = ranked[0]

    # Two roles within a hair of each other is genuine ambiguity, not a
    # winner. Damp both rather than picking arbitrarily.
    if len(ranked) > 1:
        second_role, second_score = ranked[1]
        if second_score > 0 and (top_score - second_score) / top_score < 0.12:
            top_score *= 0.75
            signals.setdefault(top_role, []).append(
                f'could also be {_role_words(second_role)} — names are close'
            )

    res.role = top_role if top_score >= MIN_ASSIGN_CONFIDENCE else None
    res.confidence = top_score
    res.signals = signals.get(top_role, [])
    res.rejected = [(r, s) for r, s in ranked[1:] if s >= MIN_ASSIGN_CONFIDENCE * 0.7]
    if res.role:
        # 'heuristic' is retained for a name-only match: that is exactly what
        # the original detector did, and callers (MCP schema summaries, the
        # ingest report) already speak that vocabulary. The new source names
        # are reserved for evidence that did not exist before.
        res.source = 'name+values' if (top_role in name_roles and top_role in value_roles) else (
            'values' if top_role in value_roles else 'heuristic'
        )
    return res


def _numeric_column(values: Sequence[Any]) -> List[Optional[float]]:
    """Parse a column's values as numbers, preserving row alignment."""
    out: List[Optional[float]] = []
    for v in values[:_SAMPLE_ROWS]:
        if v is None:
            out.append(None)
            continue
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            # NaN is a float, so it reaches here rather than the None branch.
            # Left unfiltered it makes every blank row a *comparable* row that
            # can never match, which drags a valid identity below the ratio —
            # and blank rows are ordinary in a file carrying summary and lot
            # rows in the same column.
            f = float(v)
            out.append(None if f != f else f)
            continue
        s = str(v).strip()
        if not s or s.lower() in NULL_MARKERS:
            out.append(None)
            continue
        stripped = re.sub(r'[^\d.,()\-]', '', s)
        out.append(parse_generic_number(stripped))
    return out


def _identity_holds(
    left: List[Optional[float]],
    right: List[Optional[float]],
    expected: List[Optional[float]],
    op: str,
) -> Tuple[bool, int, int]:
    """Test ``left op right ≈ expected`` row-wise.

    Returns ``(holds, matching_rows, comparable_rows)``. Rows where any input
    is missing are not comparable and are excluded from both counts, so a
    column that is half empty cannot fail the test by being empty.
    """
    matched = comparable = 0
    for a, b, c in zip(left, right, expected):
        if a is None or b is None or c is None:
            continue
        computed = a * b if op == '*' else a - b
        comparable += 1
        scale = max(abs(c), abs(computed), 1.0)
        if abs(computed - c) / scale <= _COHERENCE_TOLERANCE:
            matched += 1
    if comparable < _MIN_COHERENCE_ROWS:
        return False, matched, comparable
    return (matched / comparable) >= _COHERENCE_RATIO, matched, comparable


def _apply_coherence(
    resolutions: Dict[str, ColumnResolution],
    column_values: Dict[str, Sequence[Any]],
) -> List[str]:
    """Confirm or correct money roles using arithmetic identities.

    Two identities are tested: ``quantity × price = market_value`` and
    ``market_value − cost_basis = pnl``. Each is tried first with the roles as
    resolved; if that fails, every plausible reassignment among the numeric
    columns is tried, and a permutation that satisfies the identity overrides
    the name-based guess. That last step is the point of the whole Module — it
    is how an export whose header says ``Amount`` in a column of market values
    still gets a correct total.

    Returns the notes to attach to the table-level report.
    """
    notes: List[str] = []
    numeric_cache: Dict[str, List[Optional[float]]] = {}

    def nums(col: str) -> List[Optional[float]]:
        if col not in numeric_cache:
            numeric_cache[col] = _numeric_column(column_values.get(col, []))
        return numeric_cache[col]

    def column_for(role: str) -> Optional[str]:
        best: Optional[Tuple[str, float]] = None
        for col, res in resolutions.items():
            if res.role == role and (best is None or res.confidence > best[1]):
                best = (col, res.confidence)
        return best[0] if best else None

    def _eligible(col: str) -> bool:
        values = [v for v in nums(col) if v is not None]
        if len(values) < _MIN_COHERENCE_ROWS:
            return False
        # An identity that holds across *varying* values is evidence; one that
        # holds across constants is arithmetic trivia. Three columns holding
        # 1, 2, and 3 on every row satisfy a - b = c perfectly and mean
        # nothing, and without this guard the search would name them market
        # value, cost basis, and gain/loss with a straight face.
        if len(set(values)) < _MIN_DISTINCT_VALUES:
            return False
        # A column already confidently resolved to a non-money role (a date, a
        # ticker) must not be stolen to make an identity balance.
        res = resolutions.get(col)
        return not (
            res is not None
            and res.role not in (None, *_NUMERIC_ROLES)
            and res.confidence >= CONFIDENCE_CONFIRMED
        )

    candidates = [col for col in column_values if _eligible(col)]

    def confirm(role_cols: Dict[str, str], identity: str, matched: int, total: int) -> None:
        for role, col in role_cols.items():
            res = resolutions.setdefault(col, ColumnResolution(column=col))
            corrected = res.role != role
            res.role = role
            res.confidence = max(res.confidence, 0.95)
            # A vendor profile is a human assertion about a known format.
            # Arithmetic agreeing with it is corroboration, not a new
            # provenance — overwriting the source here would make a profiled
            # column look inferred to anyone auditing where a role came from.
            if res.source != 'profile':
                res.source = 'arithmetic'
            res.signals.append(
                f'{identity} holds on {matched} of {total} rows'
                + (' — this corrected the name-based match' if corrected else '')
            )
            if corrected:
                notes.append(
                    f'"{col}" was read as {_role_words(role)} because {identity} '
                    f'only balances that way ({matched} of {total} rows).'
                )

    for triple, op, identity in (
        (('quantity', 'price', 'market_value'), '*', 'quantity x price = market value'),
        (('market_value', 'cost_basis', 'pnl'), '-', 'market value - cost basis = gain/loss'),
    ):
        a_role, b_role, c_role = triple
        a_col, b_col, c_col = column_for(a_role), column_for(b_role), column_for(c_role)

        if a_col and b_col and c_col:
            holds, matched, total = _identity_holds(
                nums(a_col), nums(b_col), nums(c_col), op
            )
            if holds:
                confirm({a_role: a_col, b_role: b_col, c_role: c_col},
                        identity, matched, total)
                continue

        # The identity did not hold as resolved. Look for an assignment that
        # does — but the search may only *fill in* or *upgrade*, never
        # contradict. A column already resolved to a different role at
        # `likely` or better keeps its role.
        #
        # This constraint is load-bearing. A flat Pershing export contains
        # Quantity, Unit Cost, and Current Total Cost, and quantity x unit
        # cost = total cost balances perfectly. An unconstrained search would
        # seize on that and relabel "Unit Cost" as the price and "Current
        # Total Cost" as the market value — a confidently wrong answer of
        # exactly the kind this Module exists to prevent.
        def may_take(col: str, role: str) -> bool:
            res = resolutions.get(col)
            if res is None or res.role is None:
                return True
            return res.role == role or res.confidence < CONFIDENCE_LIKELY

        if len(candidates) >= 3:
            found = False
            for a in candidates:
                if found or not may_take(a, a_role):
                    continue
                for b in candidates:
                    if b == a or found or not may_take(b, b_role):
                        continue
                    for c in candidates:
                        if c in (a, b) or not may_take(c, c_role):
                            continue
                        holds, matched, total = _identity_holds(
                            nums(a), nums(b), nums(c), op
                        )
                        if holds:
                            confirm({a_role: a, b_role: b, c_role: c},
                                    identity, matched, total)
                            found = True
                            break
            if found:
                continue

        # Nothing balances. Only say so when the original triple was clearly
        # labelled *and* the mismatch is emphatic. A partial match is the
        # normal signature of a file that mixes granularities — a summary row
        # per position followed by its tax lots — and reporting that as a
        # problem would make the report noisy on files that are perfectly
        # fine, which is the failure mode that matters most here.
        if not (a_col and b_col and c_col):
            continue
        if min(resolutions[c].confidence for c in (a_col, b_col, c_col)) < CONFIDENCE_CONFIRMED:
            continue
        _, matched, total = _identity_holds(nums(a_col), nums(b_col), nums(c_col), op)
        if total >= _MIN_COHERENCE_ROWS and matched / total < 0.25:
            notes.append(
                f'{identity[0].upper()}{identity[1:]} does not hold anywhere in '
                f'this file ({matched} of {total} rows). The columns are labelled '
                f'unambiguously, so this is a property of the data rather than of '
                f'the parse — worth a look before quoting totals.'
            )

    return notes


def _enforce_singletons(resolutions: Dict[str, ColumnResolution]) -> None:
    """Keep at most one column per singleton role, demoting the weaker claims."""
    by_role: Dict[str, List[ColumnResolution]] = {}
    for res in resolutions.values():
        if res.role in _SINGLETON_ROLES:
            by_role.setdefault(res.role, []).append(res)

    for role, claims in by_role.items():
        if len(claims) < 2:
            continue
        profile_claims = [c for c in claims if c.source == 'profile']
        if profile_claims:
            # A vendor profile that maps two columns to the same role did so
            # deliberately — a Fidelity export carries both an account number
            # and an account name, and both are the account. Profile claims
            # therefore never demote each other, but they do outrank every
            # inferred claim: with a declared Quantity column present,
            # "Original Quantity" must not also become the quantity, or a sum
            # over the role double-counts.
            keep = profile_claims
        else:
            claims.sort(key=lambda r: r.confidence, reverse=True)
            keep = claims[:1]

        winner = keep[0]
        for loser in [c for c in claims if c not in keep]:
            loser.signals.append(
                f'"{winner.column}" is the stronger match for '
                f'{_role_words(role)}, so this column was left unassigned'
            )
            loser.rejected.append((role, loser.confidence))
            loser.role = None
            loser.confidence = 0.0
            loser.source = 'demoted'


def resolve_table(
    columns: Sequence[str],
    column_values: Dict[str, Sequence[Any]],
    column_types: Optional[Dict[str, str]] = None,
    role_overrides: Optional[Dict[str, str]] = None,
) -> Tuple[Dict[str, ColumnResolution], List[str]]:
    """Resolve every column in a table to a role, with confidence and evidence.

    Parameters
    ----------
    columns:
        Column names in table order.
    column_values:
        Column name → its values. Only the first :data:`_SAMPLE_ROWS` are read.
    column_types:
        Optional column name → inferred type from
        :mod:`services.tabular.inference`, used to discount a money-sounding
        name over a text column.
    role_overrides:
        Column name → role from a matched vendor profile. These are taken as
        ground truth at full confidence: a profile is a human assertion about
        a known format and outranks every inference here.

    Returns
    -------
    (resolutions, notes)
        ``resolutions`` maps every column name to a
        :class:`ColumnResolution`, including columns that resolved to nothing.
        ``notes`` are table-level observations for the ingest report.
    """
    role_overrides = role_overrides or {}
    column_types = column_types or {}
    resolutions: Dict[str, ColumnResolution] = {}

    for col in columns:
        if col in role_overrides:
            resolutions[col] = ColumnResolution(
                column=col,
                role=role_overrides[col],
                confidence=1.0,
                source='profile',
                signals=['matched a known export format'],
            )
            continue

        values = column_values.get(col, [])
        name_scores = score_name(col)
        value_scores = detect_value_shape(values)
        res = _combine(col, name_scores, value_scores, column_types.get(col))

        # The original anchored regex is kept as a floor: anything it
        # recognises is by definition an exact, unambiguous name, so a lower
        # score here means the new scorer missed something.
        legacy = _legacy_role(col, column_types.get(col, "text"))
        if legacy and (res.role != legacy or res.confidence < 0.9):
            if res.role in (None, legacy):
                res.role = legacy
                res.confidence = max(res.confidence, 0.9)
                res.source = res.source if res.source != 'none' else 'heuristic'
                res.signals.append('column name is an exact match for this field')

        resolutions[col] = res

    _enforce_singletons(resolutions)
    notes = _apply_coherence(resolutions, column_values)
    _enforce_singletons(resolutions)

    identified = sum(1 for r in resolutions.values() if r.role)
    logger.info(
        f"column_resolver: identified {identified}/{len(columns)} columns "
        f"({sum(1 for r in resolutions.values() if r.band == 'confirmed')} confirmed)"
    )
    return resolutions, notes


def roles_from_resolutions(
    resolutions: Dict[str, ColumnResolution],
) -> Dict[str, str]:
    """Flatten resolutions to the plain column→role map the store expects."""
    return {col: res.role for col, res in resolutions.items() if res.role}
