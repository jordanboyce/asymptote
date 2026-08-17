"""`prep_for_meeting` — the one page an advisor reads walking into a meeting.

This is a composite, not a new primitive. Every input already existed:

  * ``brief_generator``            — household totals, positions, accounts
  * ``meeting_notes``              — what was said and decided last time
  * ``MeetingNotesStore``          — what is still open, and how long it has been
  * ``client_profile`` / ``ips_drift`` — what the portfolio was *supposed* to look like
  * ``financial.tlh``              — what is harvestable right now
  * ``market_data.corporate_events`` — what moved under the top holdings

What this module adds is the ordering and the honesty. Three rules:

**No LLM.** Every line is computed. That means zero token cost, no
fabrication, and the same page twice for the same data — which is what makes
it safe to put in front of a prospect.

**Every section degrades alone.** A missing profile, an unreadable holdings
table, or a dead network call removes one section and records a gap; it never
fails the page. An advisor reading a prep page with four sections is fine.
An advisor who doesn't know a fifth was silently skipped is not.

**``gaps`` is the point, not the footnote.** Anything prep could not
determine is named there, in the advisor's language, with what to do about
it. This is the same discipline as ``aggregate_guard``: refusing to answer
is a valid answer; pretending the question was covered is not.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from models.schemas import ClientProfile
from services.brief_generator import _is_cash_like, generate_meeting_brief
from services.financial.client_profile import profile_completeness
from services.financial.ips_drift import (
    check_cash_policy,
    check_concentration,
    check_prohibited_holdings,
    compute_allocation_drift,
)
from services.meeting_notes import build_meeting_context

logger = logging.getLogger(__name__)

# Cap on network-backed symbol lookups. Prep is a page the advisor waits on;
# a portfolio with 300 positions must not become 300 HTTP calls.
_MAX_MARKET_SYMBOLS = 5

# An action item older than this with no close is worth saying out loud.
_STALE_ACTION_ITEM_DAYS = 45

_PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _parse_dt(value: Any) -> Optional[datetime]:
    """Best-effort ISO parse. Returns None for free text rather than raising."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _days_between(later: datetime, earlier: datetime) -> int:
    return max(0, (later - earlier).days)


class _Gaps:
    """Collects what prep could not determine, deduped and ordered."""

    def __init__(self) -> None:
        self._items: list[dict[str, str]] = []
        self._seen: set[tuple[str, str]] = set()

    def add(self, section: str, reason: str, remedy: Optional[str] = None) -> None:
        key = (section, reason)
        if key in self._seen:
            return
        self._seen.add(key)
        entry = {"section": section, "reason": reason}
        if remedy:
            entry["remedy"] = remedy
        self._items.append(entry)

    def as_list(self) -> list[dict[str, str]]:
        return list(self._items)


# ── section builders ──────────────────────────────────────────────────────


def _build_since_last_meeting(
    meeting_context: dict[str, Any],
    as_of: datetime,
    gaps: _Gaps,
) -> dict[str, Any]:
    last = meeting_context.get("last_meeting")
    if not last:
        gaps.add(
            "since_last_meeting",
            "No meeting transcript has been extracted for this client, so there is no record of what was discussed last time.",
            "Upload an audio meeting or run POST /api/collections/{id}/meetings/extract.",
        )
        return {
            "has_prior_meeting": False,
            "meetings_count": meeting_context.get("meetings_count", 0),
        }

    extracted = _parse_dt(last.get("extracted_at"))
    days_since = _days_between(as_of, extracted) if extracted else None

    return {
        "has_prior_meeting": True,
        "meetings_count": meeting_context.get("meetings_count", 0),
        "document_id": last.get("document_id"),
        "filename": last.get("filename"),
        "date": last.get("extracted_at"),
        "days_since": days_since,
        "client_concerns": last.get("client_concerns") or [],
        "decisions": last.get("decisions") or [],
        "sentiment_notes": last.get("sentiment_notes"),
        "open_follow_up_questions": meeting_context.get("open_follow_up_questions") or [],
    }


def _build_open_items(
    meeting_context: dict[str, Any],
    as_of: datetime,
    gaps: _Gaps,
) -> dict[str, Any]:
    raw_items = meeting_context.get("open_action_items") or []
    items: list[dict[str, Any]] = []
    overdue = 0
    stale = 0
    undated = 0

    for item in raw_items:
        created = _parse_dt(item.get("extracted_at")) or _parse_dt(item.get("created_at"))
        due = _parse_dt(item.get("due_date"))
        days_open = _days_between(as_of, created) if created else None

        is_overdue = bool(due and due < as_of)
        is_stale = bool(days_open is not None and days_open >= _STALE_ACTION_ITEM_DAYS)
        if is_overdue:
            overdue += 1
        if is_stale:
            stale += 1
        if item.get("due_date") and due is None:
            # Free-text due dates ("next meeting") are common and fine — but
            # they cannot be checked, and saying so beats implying they were.
            undated += 1

        items.append({
            **item,
            "days_open": days_open,
            "is_overdue": is_overdue,
            "is_stale": is_stale,
            "due_date_parsed": due.date().isoformat() if due else None,
        })

    items.sort(
        key=lambda i: (
            not i["is_overdue"],
            not i["is_stale"],
            -(i["days_open"] or 0),
        )
    )

    if undated:
        gaps.add(
            "open_items",
            f"{undated} open action item(s) have a free-text due date that could not be "
            f"parsed, so they are not counted as overdue.",
            "Set an ISO date on the item if the deadline matters.",
        )

    return {
        "count": len(items),
        "total_open": meeting_context.get("total_open_action_items", len(items)),
        "overdue_count": overdue,
        "stale_count": stale,
        "items": items,
    }


def _collect_allocation(
    store: Any,
    gaps: _Gaps,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Merge asset-class breakdowns across every financial table in the store.

    Note this can reach the network: when a table has no asset-class column
    but does have tickers, ``compute_financial_metric`` falls back to
    classifying symbols through the market-data provider. That is the same
    path chat takes, and it produces a better answer than "unclassified" —
    but it is why ``build_meeting_prep`` exposes an ``allocation_fn`` seam
    for tests and offline demos.
    """
    from services.financial.metrics import compute_financial_metric

    merged: dict[str, dict[str, Any]] = {}
    sources: list[str] = []

    try:
        tables = [t for t in store.list_tables() if t.get("financial_roles")]
    except Exception as exc:
        gaps.add("policy", f"Could not enumerate holdings tables: {exc}")
        return [], []

    for table in tables:
        identifier = table.get("table_name")
        try:
            result = compute_financial_metric(
                store, identifier=identifier, metric="breakdown_by_asset_class",
            )
        except Exception as exc:
            logger.debug("allocation: breakdown failed for %s: %s", identifier, exc)
            continue

        sources.append(table.get("filename") or str(identifier))
        for group in result.get("groups") or []:
            label = group.get("group")
            total = group.get("total") or 0
            key = str(label).strip().lower()
            entry = merged.setdefault(key, {"label": label, "market_value": 0.0})
            try:
                entry["market_value"] += float(total)
            except (TypeError, ValueError):
                continue

    if not merged:
        gaps.add(
            "policy",
            "No asset-class data could be derived from this client's holdings, so allocation "
            "drift was not computed.",
            "Either the export has no asset-class column, or symbols could not be classified.",
        )

    return list(merged.values()), sources


def _build_policy(
    profile: Optional[ClientProfile],
    brief: dict[str, Any],
    store: Any,
    gaps: _Gaps,
    allocation_fn: Optional[Callable] = None,
) -> dict[str, Any]:
    """The section that only exists because a client profile exists."""
    completeness = profile_completeness(profile)

    if profile is None:
        for reason in completeness["blocked"]:
            gaps.add("policy", reason, "Fill in the client profile for this collection.")
        return {
            "has_profile": False,
            "completeness": completeness,
            "allocation_drift": None,
            "concentration": None,
            "prohibited": None,
            "cash": None,
        }

    positions = brief.get("top_positions") or []
    total_mv = (brief.get("household_summary") or {}).get("total_market_value") or 0.0

    collect = allocation_fn or _collect_allocation
    allocation, allocation_sources = collect(store, gaps)
    drift = compute_allocation_drift(profile, allocation, total_market_value=total_mv)
    if allocation_sources:
        drift["sources"] = allocation_sources
    for warning in drift.get("warnings") or []:
        gaps.add("policy", warning)

    concentration = check_concentration(profile, positions, total_market_value=total_mv)
    if not concentration.get("checked") and concentration.get("note"):
        gaps.add("policy", concentration["note"], "Set a max single position in the client profile.")

    prohibited = check_prohibited_holdings(profile, positions)
    if not prohibited.get("checked") and prohibited.get("note"):
        gaps.add("policy", prohibited["note"])
    elif prohibited.get("checked"):
        # The screen only saw top_positions; say so rather than implying a
        # full-book scan.
        prohibited["scope"] = "top_positions"
        prohibited["scope_note"] = (
            f"Screened the top {len(positions)} positions by market value, not the full holdings list."
        )

    cash_mv = None
    for alert in brief.get("cash_drag_alerts") or []:
        cash_mv = (cash_mv or 0.0) + (alert.get("market_value") or 0.0)
    if cash_mv is None:
        for pos in positions:
            if _is_cash_like(str(pos.get("name") or ""), pos.get("asset_class")):
                cash_mv = (cash_mv or 0.0) + (pos.get("market_value") or 0.0)

    cash = check_cash_policy(profile, cash_mv, total_mv)
    if not cash.get("checked") and cash.get("note"):
        gaps.add("policy", cash["note"])

    for reason in completeness["blocked"]:
        gaps.add("policy", reason, "Fill in the missing section of the client profile.")

    return {
        "has_profile": True,
        "display_name": profile.display_name,
        "risk_tolerance": profile.risk_tolerance.value if profile.risk_tolerance else None,
        "time_horizon_years": profile.time_horizon_years,
        "completeness": completeness,
        "allocation_drift": drift,
        "concentration": concentration,
        "prohibited": prohibited,
        "cash": cash,
        "goals": [g.model_dump(mode="json") for g in profile.goals],
        "notes": profile.notes,
    }


def _build_opportunities(
    store: Any,
    collection_id: str,
    household_stores: Optional[list[Any]],
    profile: Optional[ClientProfile],
    gaps: _Gaps,
    max_candidates: int = 5,
) -> dict[str, Any]:
    from services.financial.tlh import build_harvest_plan

    try:
        plan = build_harvest_plan(
            store,
            collection_id=collection_id,
            household_stores=household_stores or [store],
            max_candidates=max_candidates,
        )
    except Exception as exc:
        logger.warning("prep: TLH plan failed for %s: %s", collection_id, exc)
        gaps.add(
            "opportunities",
            f"Tax-loss harvesting scan did not run ({exc}).",
            "Check that the holdings export carries cost basis.",
        )
        return {"tax_loss": None}

    tax_loss: dict[str, Any] = {
        "candidates": plan.candidates[:max_candidates],
        "totals": plan.totals,
        "budget": plan.budget,
        "wash_sale_warnings": plan.wash_sale_warnings,
        "guardrails": plan.guardrails,
    }

    if profile is not None and profile.tax.federal_bracket_pct is not None:
        total_loss = (plan.totals or {}).get("total_unrealized_loss") or 0
        rate = profile.tax.federal_bracket_pct / 100.0
        if profile.tax.state_bracket_pct:
            rate += profile.tax.state_bracket_pct / 100.0
        tax_loss["estimated_gross_savings"] = round(total_loss * rate, 2)
        tax_loss["estimated_savings_note"] = (
            f"Gross estimate at the profile's {profile.tax.federal_bracket_pct}% federal"
            + (f" + {profile.tax.state_bracket_pct}% state" if profile.tax.state_bracket_pct else "")
            + " rate, before transaction costs. Assumes the full loss is usable this year."
        )
    else:
        gaps.add(
            "opportunities",
            "Estimated tax savings on harvestable losses were not computed — no federal bracket "
            "in the client profile.",
            "Add the tax section to the client profile.",
        )

    return {"tax_loss": tax_loss}


def _build_market_context(
    brief: dict[str, Any],
    since: Optional[datetime],
    gaps: _Gaps,
    events_fn: Optional[Callable] = None,
) -> dict[str, Any]:
    """Corporate events under the largest holdings since the last meeting."""
    if events_fn is None:
        try:
            from services.market_data.corporate_events import get_corporate_events as events_fn  # type: ignore
        except Exception as exc:
            gaps.add("market_context", f"Market data provider unavailable ({exc}).")
            return {"checked": False, "symbols": [], "events": []}

    symbols: list[str] = []
    for pos in brief.get("top_positions") or []:
        ticker = str(pos.get("ticker") or "").strip().upper()
        if ticker and ticker not in symbols:
            symbols.append(ticker)
        if len(symbols) >= _MAX_MARKET_SYMBOLS:
            break

    if not symbols:
        gaps.add(
            "market_context",
            "No ticker symbols were available on the top holdings, so corporate events "
            "could not be checked.",
            "The export may identify securities by name or CUSIP only.",
        )
        return {"checked": False, "symbols": [], "events": []}

    since_iso = (since or (_utc_now() - timedelta(days=90))).date().isoformat()
    events: list[dict[str, Any]] = []
    failures: list[str] = []

    for symbol in symbols:
        try:
            result = events_fn(symbol=symbol, since=since_iso)
        except Exception as exc:
            logger.debug("prep: corporate events failed for %s: %s", symbol, exc)
            failures.append(symbol)
            continue
        filings = result.get("filings") or []
        dividends = result.get("dividends") or []
        splits = result.get("splits") or []
        earnings = result.get("earnings") or []
        if filings or dividends or splits or earnings:
            events.append({
                "symbol": symbol,
                "filings": filings[:5],
                "dividends": dividends[:5],
                "splits": splits[:5],
                "earnings": earnings[:3],
            })

    if failures:
        gaps.add(
            "market_context",
            f"Corporate-event lookup failed for: {', '.join(failures)}. Those holdings were "
            f"not checked for filings, dividends, or splits.",
        )

    return {
        "checked": True,
        "since": since_iso,
        "symbols": symbols,
        "symbols_note": (
            f"Checked the {len(symbols)} largest holdings by market value, not the full book."
        ),
        "events": events,
    }


# ── talking points ────────────────────────────────────────────────────────


def _build_talking_points(
    since_last: dict[str, Any],
    open_items: dict[str, Any],
    policy: dict[str, Any],
    opportunities: dict[str, Any],
    market: dict[str, Any],
) -> list[dict[str, Any]]:
    """Turn findings into an ordered agenda. Deterministic — no model involved."""
    points: list[dict[str, Any]] = []

    def add(priority: str, category: str, headline: str, detail: str, source: str) -> None:
        points.append({
            "priority": priority,
            "category": category,
            "headline": headline,
            "detail": detail,
            "source": source,
        })

    # Compliance first — a prohibited holding is the one item that cannot wait.
    prohibited = policy.get("prohibited") or {}
    for match in (prohibited.get("matches") or [])[:5]:
        label = match.get("ticker") or match.get("name") or match.get("rule")
        add(
            "high", "compliance",
            f"{label} is on this client's exclusion list",
            f"Matched rule '{match.get('rule')}' on {match.get('matched_on')}. "
            f"Confirm whether it was inherited, transferred in, or bought in error.",
            "client profile — prohibited holdings",
        )

    concentration = policy.get("concentration") or {}
    for breach in (concentration.get("breaches") or [])[:3]:
        label = breach.get("ticker") or breach.get("name") or "position"
        add(
            "high", "concentration",
            f"{label} is {breach.get('pct_of_portfolio')}% of the portfolio, "
            f"above the {breach.get('ceiling_pct')}% limit",
            f"Trimming ${abs(breach.get('trim_dollars') or 0):,.0f} returns it to the ceiling. "
            f"Check the cost basis before proposing a sale.",
            "client profile — max single position",
        )

    drift = policy.get("allocation_drift") or {}
    for line in (drift.get("lines") or []):
        if line.get("status") == "in_band":
            continue
        direction = "over" if line["status"] == "over" else "under"
        dollars = abs(line.get("to_target_dollars") or 0)
        priority = "high" if abs(line.get("drift_pct") or 0) >= 10 else "medium"
        add(
            priority, "allocation",
            f"{line['asset_class']} is {abs(line['drift_pct'])} points {direction} target "
            f"({line['actual_pct']}% vs {line['target_pct']}%)",
            f"Roughly ${dollars:,.0f} to return to target. Band is "
            f"{line['band_min_pct']}–{line['band_max_pct']}%."
            + ("" if drift.get("authoritative") else " Drift is partial — see gaps."),
            "IPS target allocation",
        )

    if open_items.get("overdue_count"):
        overdue = [i for i in open_items.get("items", []) if i.get("is_overdue")][:3]
        for item in overdue:
            add(
                "high", "commitments",
                f"Overdue: {item.get('description')}",
                f"Owed by {item.get('assignee') or 'unassigned'}, due "
                f"{item.get('due_date_parsed') or item.get('due_date')}.",
                "meeting action items",
            )

    stale = [
        i for i in open_items.get("items", [])
        if i.get("is_stale") and not i.get("is_overdue")
    ][:3]
    for item in stale:
        add(
            "medium", "commitments",
            f"Still open after {item.get('days_open')} days: {item.get('description')}",
            f"Owed by {item.get('assignee') or 'unassigned'}. Either close it or "
            f"re-commit to a date in this meeting.",
            "meeting action items",
        )

    for question in (since_last.get("open_follow_up_questions") or [])[:3]:
        add(
            "medium", "follow_up",
            f"Owed from last meeting: {question}",
            "This was left open at the end of the last conversation.",
            "last meeting — follow-up questions",
        )

    for concern in (since_last.get("client_concerns") or [])[:3]:
        add(
            "medium", "client_concern",
            f"Last time they raised: {concern}",
            "Worth revisiting even if nothing has changed — it tells them you listened.",
            "last meeting — client concerns",
        )

    cash = policy.get("cash") or {}
    if cash.get("checked") and cash.get("status") != "ok":
        add(
            "medium", "cash",
            f"Cash position is {cash.get('status', '').replace('_', ' ')}",
            " ".join(cash.get("findings") or []),
            "client profile — liquidity",
        )

    tax_loss = (opportunities or {}).get("tax_loss") or {}
    totals = tax_loss.get("totals") or {}
    if totals.get("candidate_count"):
        savings = tax_loss.get("estimated_gross_savings")
        savings_s = f" — roughly ${savings:,.0f} gross at their bracket" if savings else ""
        add(
            "medium", "tax",
            f"{totals['candidate_count']} harvestable loss(es) totaling "
            f"${abs(totals.get('total_unrealized_loss') or 0):,.0f}{savings_s}",
            "Short-term losses are ranked first. Household wash-sale check has run — "
            "see warnings before trading.",
            "tax-loss harvesting scan",
        )
    if tax_loss.get("wash_sale_warnings"):
        add(
            "high", "tax",
            f"{len(tax_loss['wash_sale_warnings'])} harvest candidate(s) carry wash-sale risk",
            "The same or a substantially identical security is held elsewhere in the "
            "household. Do not trade these without checking recent purchases.",
            "tax-loss harvesting — wash-sale check",
        )

    for entry in (market.get("events") or [])[:3]:
        bits = []
        if entry.get("filings"):
            bits.append(f"{len(entry['filings'])} SEC filing(s)")
        if entry.get("dividends"):
            bits.append("dividend activity")
        if entry.get("splits"):
            bits.append("a split")
        if entry.get("earnings"):
            bits.append("upcoming earnings")
        if not bits:
            continue
        add(
            "low", "market",
            f"{entry['symbol']}: {', '.join(bits)} since the last meeting",
            "Worth a sentence if they follow this holding closely.",
            "corporate events",
        )

    points.sort(key=lambda p: _PRIORITY_ORDER.get(p["priority"], 3))
    return points


# ── entry point ───────────────────────────────────────────────────────────


def build_meeting_prep(
    *,
    holdings_store: Any,
    meeting_notes_store: Any,
    metadata_store: Any,
    client_profile_store: Any,
    collection_id: str,
    when: Optional[str] = None,
    household_stores: Optional[list[Any]] = None,
    include_tlh: bool = True,
    include_market_context: bool = True,
    top_n: int = 10,
    allocation_fn: Optional[Callable] = None,
    events_fn: Optional[Callable] = None,
) -> dict[str, Any]:
    """Assemble the full pre-meeting page for one client collection.

    Parameters
    ----------
    when:
        ISO timestamp of the meeting. Drives "days since last meeting" and
        overdue arithmetic. Defaults to now — pass it when prepping ahead.
    household_stores:
        Additional ``HoldingsStore`` instances for wash-sale scope, same
        semantics as ``find_tax_loss_candidates``.
    include_tlh / include_market_context:
        Turn off the two slowest sections. Both are on by default; both
        degrade to a gap rather than an error when they fail.
    allocation_fn / events_fn:
        Injection seams for tests and offline demos — an asset-class
        collector and a corporate-events fetcher. Both default to the real
        providers, which may reach the network.

    Returns a dict with ``header``, ``since_last_meeting``, ``open_items``,
    ``portfolio``, ``policy``, ``opportunities``, ``market_context``,
    ``talking_points``, and ``gaps``.
    """
    as_of = _parse_dt(when) or _utc_now()
    gaps = _Gaps()

    # ── portfolio ─────────────────────────────────────────────────────────
    try:
        brief = generate_meeting_brief(
            holdings_store,
            collection_id=collection_id,
            thresholds={"top_n": top_n},
        )
    except Exception as exc:
        logger.error("prep: brief generation failed for %s: %s", collection_id, exc)
        gaps.add(
            "portfolio",
            f"Portfolio figures are unavailable ({exc}). Every section that depends on "
            f"holdings has been skipped.",
            "Check that this collection has an ingested holdings file.",
        )
        brief = {}

    if brief and not brief.get("tables_scanned"):
        gaps.add(
            "portfolio",
            "No holdings table with recognized financial columns was found in this collection.",
            "Upload a brokerage export, or check the Trust Report for why roles weren't detected.",
        )

    # A total that failed the P0.6 coercion guard is the single most dangerous
    # thing this page could carry — it is a number the advisor will repeat in
    # the room. brief_generator withholds the value; prep names why, loudly,
    # rather than letting a blank read as "zero" or "nothing here".
    for entry in brief.get("warnings") or []:
        gaps.add(
            "portfolio",
            f"{entry.get('filename', 'A holdings file')}: {entry.get('warning', '')}",
            "Re-ingest the file or fix the column — this total is withheld until then.",
        )

    # ── meeting history ───────────────────────────────────────────────────
    try:
        meeting_context = build_meeting_context(
            meeting_notes_store=meeting_notes_store,
            metadata_store=metadata_store,
            collection_id=collection_id,
        )
    except Exception as exc:
        logger.warning("prep: meeting context failed for %s: %s", collection_id, exc)
        gaps.add("since_last_meeting", f"Meeting history unavailable ({exc}).")
        meeting_context = {}

    since_last = _build_since_last_meeting(meeting_context, as_of, gaps)
    open_items = _build_open_items(meeting_context, as_of, gaps)

    # ── profile / policy ──────────────────────────────────────────────────
    profile: Optional[ClientProfile] = None
    try:
        profile = client_profile_store.get_model(collection_id)
    except Exception as exc:
        logger.warning("prep: profile load failed for %s: %s", collection_id, exc)
        gaps.add("policy", f"Client profile could not be loaded ({exc}).")

    policy = _build_policy(profile, brief, holdings_store, gaps, allocation_fn=allocation_fn)

    # ── opportunities ─────────────────────────────────────────────────────
    if include_tlh and brief.get("tables_scanned"):
        opportunities = _build_opportunities(
            holdings_store, collection_id, household_stores, profile, gaps,
        )
    else:
        opportunities = {"tax_loss": None}
        if include_tlh:
            gaps.add("opportunities", "Tax-loss scan skipped — no holdings data.")

    # ── market context ────────────────────────────────────────────────────
    if include_market_context and brief.get("top_positions"):
        last_dt = _parse_dt(since_last.get("date"))
        market = _build_market_context(brief, last_dt, gaps, events_fn=events_fn)
    else:
        market = {"checked": False, "symbols": [], "events": []}
        if include_market_context:
            gaps.add("market_context", "Corporate-event check skipped — no holdings data.")

    talking_points = _build_talking_points(
        since_last, open_items, policy, opportunities, market,
    )

    household = brief.get("household_summary") or {}
    display_name = (profile.display_name if profile else None)

    return {
        "header": {
            "collection_id": collection_id,
            "client": display_name,
            "meeting_at": as_of.isoformat(),
            "prepared_at": _utc_now().isoformat(),
            "days_since_last_meeting": since_last.get("days_since"),
            "total_market_value": household.get("total_market_value"),
            "total_market_value_reliable": household.get("total_market_value_reliable", True),
            "profile_completeness": (policy.get("completeness") or {}).get("score"),
            "talking_point_count": len(talking_points),
            "gap_count": len(gaps.as_list()),
            "generated_by": "deterministic composite — no LLM call",
        },
        "since_last_meeting": since_last,
        "open_items": open_items,
        "portfolio": {
            "household_summary": household,
            "accounts": brief.get("accounts") or [],
            "top_positions": brief.get("top_positions") or [],
            "sector_allocation": brief.get("sector_allocation") or [],
            "tables_scanned": brief.get("tables_scanned", 0),
        },
        "policy": policy,
        "opportunities": opportunities,
        "market_context": market,
        "talking_points": talking_points,
        "gaps": gaps.as_list(),
    }
