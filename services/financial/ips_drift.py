"""IPS drift — compare a live portfolio against the client's stated policy.

This is the module that lets meeting prep say *"8% over target in tech"*
instead of *"tech is 28% of the portfolio"*. It takes the profile's
:class:`IPSTargets` and an actual allocation and returns per-asset-class
drift, concentration breaches, and prohibited-holding matches.

Two properties matter more than coverage here:

**Never silently wrong.** Label matching between an IPS target
("Fixed Income") and a brokerage export's asset-class column ("Bonds") is a
guess. Exact and alias matches are reported with ``matched_by`` so the
advisor can audit them; anything unmatched is surfaced as a named gap on
both sides — unmatched targets *and* unexpected buckets — never quietly
dropped or folded into "other".

**Partial coverage is not drift.** If a third of the household's market
value has no asset-class label, the drift percentages are computed against
a portfolio the advisor didn't fully see. ``coverage`` reports the
classified share and ``authoritative`` goes False below
:data:`_MIN_COVERAGE_PCT`, so consumers can downgrade the section instead
of reading numbers built on a partial denominator.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

from models.schemas import ClientProfile

# Below this share of market value classified into asset classes, drift is
# reported but flagged non-authoritative.
_MIN_COVERAGE_PCT = 90.0

# Buckets brokerage exports use for "we couldn't classify this".
_UNCLASSIFIED_LABELS = frozenset({"unclassified", "unknown", "n/a", "na", "none", "other", ""})

# Conservative equivalences only — each one is a claim we'd defend to an
# advisor reading the audit trail. Anything less obvious stays unmatched.
_ALIASES: dict[str, str] = {
    "equity": "equities",
    "equities": "equities",
    "stock": "equities",
    "stocks": "equities",
    "uscommonstock": "equities",
    "commonstock": "equities",
    "fixedincome": "fixed income",
    "fixed income": "fixed income",
    "bond": "fixed income",
    "bonds": "fixed income",
    "debt": "fixed income",
    "cash": "cash",
    "cashequivalents": "cash",
    "cashequivalent": "cash",
    "cashandequivalents": "cash",
    "moneymarket": "cash",
    "mmf": "cash",
    "realestate": "real estate",
    "reit": "real estate",
    "reits": "real estate",
    "alternative": "alternatives",
    "alternatives": "alternatives",
    "commodity": "commodities",
    "commodities": "commodities",
}


def _norm(label: Any) -> str:
    """Lowercase, strip punctuation/whitespace — the exact-match key."""
    return re.sub(r"[^a-z0-9]+", "", str(label or "").lower())


def _canonical(label: Any) -> str:
    """Map a label onto its alias family, or fall back to its normalized form."""
    n = _norm(label)
    return _ALIASES.get(n, n)


def _is_unclassified(label: Any) -> bool:
    return str(label or "").strip().lower() in _UNCLASSIFIED_LABELS


def _pct(part: float, whole: float) -> float:
    return round(part / whole * 100.0, 2) if whole else 0.0


def compute_allocation_drift(
    profile: ClientProfile,
    allocation: Iterable[dict[str, Any]],
    total_market_value: Optional[float] = None,
) -> dict[str, Any]:
    """Compare actual asset-class allocation against the IPS targets.

    Parameters
    ----------
    profile:
        The client's profile. ``profile.ips.allocation_targets`` drives this;
        an empty target list returns a well-formed "cannot compute" result.
    allocation:
        Rows of ``{'asset_class'|'label'|'group', 'market_value'}`` — the
        shape ``compute_financial_metric(..., 'breakdown_by_asset_class')``
        and the brief's ``sector_allocation`` both produce.
    total_market_value:
        Household total. Defaults to the sum of ``allocation`` — pass it
        explicitly when the allocation covers only part of the portfolio,
        which is exactly when ``coverage`` earns its keep.

    Returns a dict with ``lines``, ``unmatched_targets``,
    ``unexpected_classes``, ``coverage``, ``authoritative``, ``totals``,
    and ``warnings``.
    """
    targets = profile.ips.allocation_targets
    band = profile.ips.rebalance_band_pct

    actual: dict[str, dict[str, Any]] = {}
    classified_mv = 0.0
    unclassified_mv = 0.0

    for row in allocation or []:
        label = row.get("asset_class") or row.get("label") or row.get("group") or row.get("sector")
        mv = row.get("market_value") or row.get("value") or 0.0
        try:
            mv = float(mv)
        except (TypeError, ValueError):
            continue
        if _is_unclassified(label):
            unclassified_mv += mv
            continue
        key = _canonical(label)
        entry = actual.setdefault(key, {"labels": [], "market_value": 0.0})
        if label not in entry["labels"]:
            entry["labels"].append(label)
        entry["market_value"] += mv
        classified_mv += mv

    denominator = total_market_value if total_market_value is not None else (classified_mv + unclassified_mv)
    denominator = float(denominator or 0.0)

    warnings: list[str] = []

    if not targets:
        return {
            "lines": [],
            "unmatched_targets": [],
            "unexpected_classes": sorted(
                (lbl for e in actual.values() for lbl in e["labels"]),
            ),
            "coverage": {
                "classified_market_value": round(classified_mv, 2),
                "unclassified_market_value": round(unclassified_mv, 2),
                "total_market_value": round(denominator, 2),
                "classified_pct": _pct(classified_mv, denominator),
            },
            "authoritative": False,
            "totals": {"target_pct_sum": None, "max_abs_drift_pct": None, "lines_out_of_band": 0},
            "warnings": [
                "No IPS target allocation is set for this client, so drift cannot be computed. "
                "Add target allocation lines to the client profile to enable this section."
            ],
        }

    if denominator <= 0:
        return {
            "lines": [],
            "unmatched_targets": [t.asset_class for t in targets],
            "unexpected_classes": [],
            "coverage": {
                "classified_market_value": 0.0,
                "unclassified_market_value": 0.0,
                "total_market_value": 0.0,
                "classified_pct": 0.0,
            },
            "authoritative": False,
            "totals": {"target_pct_sum": None, "max_abs_drift_pct": None, "lines_out_of_band": 0},
            "warnings": [
                "Portfolio market value is zero or unavailable, so allocation drift cannot be computed."
            ],
        }

    lines: list[dict[str, Any]] = []
    consumed: set[str] = set()

    for target in targets:
        key = _canonical(target.asset_class)
        match = actual.get(key)
        matched_by: Optional[str] = None
        if match is not None:
            consumed.add(key)
            matched_by = "exact" if _norm(target.asset_class) == _norm(match["labels"][0]) else "alias"

        actual_mv = float(match["market_value"]) if match else 0.0
        actual_pct = _pct(actual_mv, denominator)
        min_pct = target.min_pct if target.min_pct is not None else max(0.0, target.target_pct - band)
        max_pct = target.max_pct if target.max_pct is not None else min(100.0, target.target_pct + band)
        drift = round(actual_pct - target.target_pct, 2)

        if actual_pct > max_pct:
            status = "over"
        elif actual_pct < min_pct:
            status = "under"
        else:
            status = "in_band"

        lines.append({
            "asset_class": target.asset_class,
            "matched_labels": list(match["labels"]) if match else [],
            "matched_by": matched_by,
            "target_pct": target.target_pct,
            "actual_pct": actual_pct,
            "drift_pct": drift,
            "band_min_pct": round(min_pct, 2),
            "band_max_pct": round(max_pct, 2),
            "status": status,
            "market_value": round(actual_mv, 2),
            # Dollars to trade to return to target. Negative = sell.
            "to_target_dollars": round((target.target_pct - actual_pct) / 100.0 * denominator, 2),
        })

    unmatched_targets = [ln["asset_class"] for ln in lines if ln["matched_by"] is None]
    unexpected = sorted(
        lbl
        for key, entry in actual.items()
        if key not in consumed
        for lbl in entry["labels"]
    )

    if unmatched_targets:
        warnings.append(
            "No holdings matched these IPS target classes: "
            + ", ".join(unmatched_targets)
            + ". They are reported at 0% — confirm the label matches the export's asset-class values."
        )
    if unexpected:
        warnings.append(
            "Holdings fall into classes with no IPS target: "
            + ", ".join(unexpected)
            + ". They count toward the denominator but have no band to breach."
        )

    classified_pct = _pct(classified_mv, denominator)
    if classified_pct < _MIN_COVERAGE_PCT:
        warnings.append(
            f"Only {classified_pct}% of household market value carries an asset-class label "
            f"(${unclassified_mv:,.0f} unclassified). Drift percentages below are computed "
            f"against the full portfolio value and will understate every class until the "
            f"remainder is classified."
        )

    target_sum = round(sum(t.target_pct for t in targets), 2)
    if abs(target_sum - 100.0) > 0.5:
        warnings.append(
            f"IPS target allocation sums to {target_sum}%, not 100%. Drift is reported "
            f"as-entered — correct the profile rather than reading these as final."
        )

    out_of_band = [ln for ln in lines if ln["status"] != "in_band"]
    lines.sort(key=lambda ln: abs(ln["drift_pct"]), reverse=True)

    return {
        "lines": lines,
        "unmatched_targets": unmatched_targets,
        "unexpected_classes": unexpected,
        "coverage": {
            "classified_market_value": round(classified_mv, 2),
            "unclassified_market_value": round(unclassified_mv, 2),
            "total_market_value": round(denominator, 2),
            "classified_pct": classified_pct,
        },
        "authoritative": classified_pct >= _MIN_COVERAGE_PCT and not unmatched_targets,
        "totals": {
            "target_pct_sum": target_sum,
            "max_abs_drift_pct": max((abs(ln["drift_pct"]) for ln in lines), default=0.0),
            "lines_out_of_band": len(out_of_band),
        },
        "warnings": warnings,
    }


def check_concentration(
    profile: ClientProfile,
    positions: Iterable[dict[str, Any]],
    total_market_value: Optional[float] = None,
    exclude_cash: bool = True,
) -> dict[str, Any]:
    """Flag positions above the client's own concentration ceiling.

    Distinct from the brief's generic 10% alert: this one is measured
    against ``ips.max_single_position_pct``, which is the number the advisor
    agreed to in writing.

    Cash is excluded by default. A concentration ceiling is a
    single-*security* rule, and a large sweep balance is a liquidity
    question that :func:`check_cash_policy` already answers against the
    reserve target — reporting it here too produces a scary-looking breach
    the advisor has to explain away twice. Pass ``exclude_cash=False`` to
    apply the ceiling literally.
    """
    from services.brief_generator import _is_cash_like

    ceiling = profile.ips.max_single_position_pct
    all_rows = [dict(p) for p in (positions or [])]

    rows = all_rows
    excluded_cash: list[str] = []
    if exclude_cash:
        kept = []
        for row in all_rows:
            if _is_cash_like(str(row.get("name") or ""), row.get("asset_class")):
                excluded_cash.append(str(row.get("name")))
            else:
                kept.append(row)
        rows = kept

    if ceiling is None:
        return {
            "ceiling_pct": None,
            "breaches": [],
            "checked": False,
            "note": (
                "No max single position set in the client profile, so concentration "
                "was not checked against the client's own limit."
            ),
        }

    total = total_market_value
    if total is None:
        # Denominator is the whole portfolio including cash — a position's
        # share is of everything the client holds, not of everything we
        # happened to screen.
        total = sum(float(p.get("market_value") or 0) for p in all_rows)
    total = float(total or 0.0)

    if total <= 0:
        return {
            "ceiling_pct": ceiling,
            "breaches": [],
            "checked": False,
            "note": "Portfolio market value unavailable — concentration not checked.",
        }

    breaches: list[dict[str, Any]] = []
    for pos in rows:
        try:
            mv = float(pos.get("market_value") or 0)
        except (TypeError, ValueError):
            continue
        pct = _pct(mv, total)
        if pct >= ceiling:
            breaches.append({
                "name": pos.get("name"),
                "ticker": pos.get("ticker"),
                "market_value": round(mv, 2),
                "pct_of_portfolio": pct,
                "ceiling_pct": ceiling,
                "excess_pct": round(pct - ceiling, 2),
                "trim_dollars": round((pct - ceiling) / 100.0 * total, 2),
                "source": pos.get("source"),
            })

    breaches.sort(key=lambda b: b["pct_of_portfolio"], reverse=True)
    result: dict[str, Any] = {
        "ceiling_pct": ceiling,
        "breaches": breaches,
        "checked": True,
        "count": len(breaches),
    }
    if excluded_cash:
        result["excluded_cash_positions"] = excluded_cash
        result["exclusion_note"] = (
            "Cash / money-market positions were excluded from the single-position "
            "ceiling; the cash section judges them against the reserve target instead."
        )
    return result


def _is_ticker_like(rule: str) -> bool:
    """A rule with no spaces and <= 5 characters reads as a symbol, not a theme."""
    return len(rule) <= 5 and not re.search(r"\s", rule)


def check_prohibited_holdings(
    profile: ClientProfile,
    positions: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Match held positions against the client's exclusion list.

    Exclusion lists carry two kinds of rule and they need different matching:

    *Symbols* ("XOM") match a ticker exactly, or a name only on a whole-word
    boundary. Substring matching here is actively harmful — the rule "XOM"
    hitting "XOMA Corp" is a false alarm the advisor has to disprove during
    a meeting, and a screen that cries wolf stops being read.

    *Themes* ("tobacco", "private prison") match a name as a
    case-insensitive substring, because that is the only way they can match
    at all.

    Every match reports ``matched_on`` so a questionable hit can be
    dismissed on sight.
    """
    prohibited = [p for p in (profile.ips.prohibited_holdings or []) if str(p).strip()]
    if not prohibited:
        return {
            "rules": [],
            "matches": [],
            "checked": False,
            "note": (
                "No prohibited holdings recorded in the client profile, so the "
                "exclusion screen did not run."
            ),
        }

    matches: list[dict[str, Any]] = []
    for rule in prohibited:
        rule_s = str(rule).strip()
        rule_ticker = rule_s.upper()
        rule_lower = rule_s.lower()
        ticker_like = _is_ticker_like(rule_s)
        word_re = re.compile(rf"\b{re.escape(rule_lower)}\b") if ticker_like else None

        for pos in positions or []:
            ticker = str(pos.get("ticker") or "").strip().upper()
            name = str(pos.get("name") or "").strip()
            matched_on = None
            if ticker and ticker == rule_ticker:
                matched_on = "ticker"
            elif name:
                lowered = name.lower()
                if word_re is not None:
                    if word_re.search(lowered):
                        matched_on = "name"
                elif rule_lower in lowered:
                    matched_on = "name"
            if matched_on:
                matches.append({
                    "rule": rule_s,
                    "matched_on": matched_on,
                    "rule_kind": "symbol" if ticker_like else "theme",
                    "name": pos.get("name"),
                    "ticker": pos.get("ticker"),
                    "market_value": pos.get("market_value"),
                    "source": pos.get("source"),
                })

    return {
        "rules": prohibited,
        "matches": matches,
        "checked": True,
        "count": len(matches),
    }


def check_cash_policy(
    profile: ClientProfile,
    cash_market_value: Optional[float],
    total_market_value: Optional[float],
) -> dict[str, Any]:
    """Judge the cash balance against the reserve target and cash bands.

    The reason this exists: a generic "cash drag" alert fires on a balance
    the client deliberately parked for a known expense. With a reserve
    target and a liquidity event on file, prep can tell the difference.
    """
    liquidity = profile.liquidity
    reserve = liquidity.cash_reserve_target
    min_pct = profile.ips.min_cash_pct
    max_pct = profile.ips.max_cash_pct

    if cash_market_value is None:
        return {
            "checked": False,
            "note": "No cash / money-market position identified in the holdings.",
        }

    total = float(total_market_value or 0.0)
    cash = float(cash_market_value)
    cash_pct = _pct(cash, total) if total > 0 else None

    findings: list[str] = []
    status = "ok"

    if reserve is not None:
        delta = cash - reserve
        if delta < 0:
            status = "below_reserve"
            findings.append(
                f"Cash is ${abs(delta):,.0f} below the ${reserve:,.0f} reserve target."
            )
        else:
            findings.append(
                f"Cash is ${delta:,.0f} above the ${reserve:,.0f} reserve target."
            )

    if cash_pct is not None and max_pct is not None and cash_pct > max_pct:
        status = "above_band"
        findings.append(f"Cash is {cash_pct}% of the portfolio, above the {max_pct}% IPS ceiling.")
    if cash_pct is not None and min_pct is not None and cash_pct < min_pct:
        status = "below_band"
        findings.append(f"Cash is {cash_pct}% of the portfolio, below the {min_pct}% IPS floor.")

    if liquidity.next_liquidity_event:
        amount = liquidity.next_liquidity_amount
        amount_s = f" (${amount:,.0f})" if amount else ""
        findings.append(
            f"Upcoming liquidity need: {liquidity.next_liquidity_event}{amount_s} — "
            f"excess cash may be intentional."
        )

    return {
        "checked": True,
        "cash_market_value": round(cash, 2),
        "cash_pct": cash_pct,
        "reserve_target": reserve,
        "min_cash_pct": min_pct,
        "max_cash_pct": max_pct,
        "status": status,
        "findings": findings,
    }
