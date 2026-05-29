"""HTML + plain-text renderers for the daily morning brief email.

Companion to :mod:`services.digest_template`. Same email-client constraints:
inline CSS only, table-based layout, max 600px content width, explicit colour
values, no JS, no web fonts.

The visual hierarchy is tuned to "glance in 10 seconds before the first
client call":

1. Header band with today's date.
2. Hero summary line — "3 due today, 1 overdue, 1 fresh action item, $5,200
   in tax-loss opportunities."
3. Per-collection cards, in this order inside each card:
   a. Overdue (red badge) — most urgent, surface first.
   b. Due today (blue badge).
   c. Due this week (subtle badge) — for context, not action.
   d. Fresh action items extracted overnight.
   e. Tax-loss candidates.
   f. New documents from overnight.
4. Footer.
"""

from __future__ import annotations

from html import escape
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from services.morning_brief import CollectionMorningSignals, MorningBriefPayload


# Brand palette — share with services.digest_template for visual consistency.
_BRAND_PRIMARY = "#1f3a5f"      # deep navy — header band
_BRAND_ACCENT = "#3b6ea5"
_TEXT = "#1a1a1a"
_TEXT_SUBDUED = "#666666"
_RULE = "#e5e5e5"
_CARD_BG = "#fafafa"

# Per-section badge palette. Overdue is the only "loud" colour — everything
# else stays subdued so the eye lands on what needs action today.
_OVERDUE_BADGE_BG = "#fde8e8"
_OVERDUE_BADGE_FG = "#9b1c1c"
_DUE_TODAY_BADGE_BG = "#e8f1ff"
_DUE_TODAY_BADGE_FG = "#1f3a5f"
_DUE_WEEK_BADGE_BG = "#f3f4f6"
_DUE_WEEK_BADGE_FG = "#374151"
_FRESH_BADGE_BG = "#ecfdf5"
_FRESH_BADGE_FG = "#065f46"
_TLH_BADGE_BG = "#fde8e8"
_TLH_BADGE_FG = "#9b1c1c"
_DOCS_BADGE_BG = "#f3f4f6"
_DOCS_BADGE_FG = "#374151"


def render_morning_brief_html(payload: "MorningBriefPayload", *, is_preview: bool = False) -> str:
    """Render the morning brief as an Outlook-safe HTML document."""
    summary = _summary_line(payload)
    body_blocks: list[str] = []

    if is_preview:
        body_blocks.append(_preview_banner())

    body_blocks.append(_header_block(payload, summary))

    if not payload.has_anything:
        body_blocks.append(_quiet_day_block())
    else:
        for collection in payload.collections:
            if collection.is_empty:
                continue
            body_blocks.append(_collection_card(collection))

    body_blocks.append(_footer_block())

    body_html = "\n".join(body_blocks)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light">
<title>Finn morning brief</title>
</head>
<body style="margin:0;padding:0;background:#f4f5f7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;color:{_TEXT};">
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background:#f4f5f7;">
  <tr><td align="center" style="padding:24px 12px;">
    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="600" style="max-width:600px;background:#ffffff;border-radius:8px;overflow:hidden;">
      <tr><td style="padding:0;">
{body_html}
      </td></tr>
    </table>
  </td></tr>
</table>
</body>
</html>
"""


def render_morning_brief_text(payload: "MorningBriefPayload", *, is_preview: bool = False) -> str:
    """Render the morning brief as a plain-text fallback."""
    out: list[str] = []
    if is_preview:
        out.append("[Preview — this is a sample brief sent on demand.]\n")
    out.append(f"Finn morning brief — {payload.date_label}")
    out.append("=" * 60)
    out.append(_summary_line(payload, plain=True))
    out.append("")

    if not payload.has_anything:
        out.append("Nothing pressing on your plate today.")
        out.append("Finn will check again tomorrow morning.")
    else:
        for collection in payload.collections:
            if collection.is_empty:
                continue
            out.append(collection.collection_name)
            out.append("-" * len(collection.collection_name))

            if collection.overdue_action_items:
                out.append(f"  OVERDUE ({len(collection.overdue_action_items)}):")
                for item in collection.overdue_action_items:
                    out.append(f"    - {_format_item_text(item)}")

            if collection.due_today_action_items:
                out.append(f"  Due today ({len(collection.due_today_action_items)}):")
                for item in collection.due_today_action_items:
                    out.append(f"    - {_format_item_text(item)}")

            if collection.due_this_week_action_items:
                out.append(f"  Due this week ({len(collection.due_this_week_action_items)}):")
                for item in collection.due_this_week_action_items:
                    out.append(f"    - {_format_item_text(item)}")

            if collection.fresh_action_items:
                out.append(f"  Fresh from overnight ({len(collection.fresh_action_items)}):")
                for item in collection.fresh_action_items:
                    out.append(f"    - {_format_item_text(item)}")

            if collection.tlh_candidates:
                total = sum(float(c.get("unrealized_loss") or 0) for c in collection.tlh_candidates)
                out.append(f"  Tax-loss candidates ({len(collection.tlh_candidates)}, ${total:,.0f} total):")
                for c in collection.tlh_candidates[:5]:
                    sym = c.get("symbol") or "(unknown)"
                    loss = float(c.get("unrealized_loss") or 0)
                    out.append(f"    - {sym}: ${loss:,.0f} unrealized loss")

            if collection.new_documents:
                out.append(f"  New documents ({len(collection.new_documents)}):")
                for doc in collection.new_documents:
                    out.append(f"    - {doc.get('filename', '(unnamed)')}")
            out.append("")

    out.append("")
    out.append("— Finn")
    out.append("Manage your brief preferences in Finn → Settings → Morning brief.")
    return "\n".join(out)


# ─── Internal block builders ───────────────────────────────────────────────


def _summary_line(payload: "MorningBriefPayload", *, plain: bool = False) -> str:
    parts: list[str] = []
    if payload.total_overdue:
        parts.append(f"{payload.total_overdue} overdue")
    if payload.total_due_today:
        parts.append(f"{payload.total_due_today} due today")
    if payload.total_due_this_week:
        parts.append(f"{payload.total_due_this_week} due this week")
    if payload.total_fresh:
        parts.append(f"{payload.total_fresh} fresh from overnight")
    n_tlh = sum(len(c.tlh_candidates) for c in payload.collections)
    if n_tlh:
        parts.append(f"{n_tlh} tax-loss candidate{'s' if n_tlh != 1 else ''} (${payload.total_tlh_dollars:,.0f})")
    if payload.total_new_documents:
        parts.append(f"{payload.total_new_documents} new document{'s' if payload.total_new_documents != 1 else ''}")

    if not parts:
        return "Nothing pressing today."
    if plain:
        return "Today: " + ", ".join(parts) + "."
    return "Today: " + ", ".join(parts) + "."


def _preview_banner() -> str:
    return (
        f'<div style="background:#fff4e0;color:#8a5a00;padding:10px 24px;'
        f'font-size:13px;border-bottom:1px solid {_RULE};">'
        "Preview — this is a sample brief sent on demand. Your scheduled briefs fire at your chosen hour."
        "</div>"
    )


def _header_block(payload: "MorningBriefPayload", summary: str) -> str:
    return (
        f'<div style="background:{_BRAND_PRIMARY};color:#ffffff;padding:24px;">'
        f'<div style="font-size:13px;letter-spacing:0.5px;text-transform:uppercase;opacity:0.85;">Finn</div>'
        f'<div style="font-size:22px;font-weight:600;margin-top:4px;">Morning brief</div>'
        f'<div style="font-size:14px;opacity:0.9;margin-top:2px;">{escape(payload.date_label)}</div>'
        "</div>"
        f'<div style="padding:20px 24px;border-bottom:1px solid {_RULE};">'
        f'<div style="font-size:15px;color:{_TEXT};line-height:1.5;">{escape(summary)}</div>'
        "</div>"
    )


def _quiet_day_block() -> str:
    return (
        f'<div style="padding:32px 24px;text-align:center;color:{_TEXT_SUBDUED};">'
        f'<div style="font-size:16px;color:{_TEXT};font-weight:500;">Nothing pressing on your plate today.</div>'
        f'<div style="font-size:13px;margin-top:8px;">Finn will check again tomorrow morning.</div>'
        "</div>"
    )


def _collection_card(collection: "CollectionMorningSignals") -> str:
    sections: list[str] = []

    if collection.overdue_action_items:
        sections.append(_action_items_section(
            collection.overdue_action_items,
            label="Overdue",
            badge_bg=_OVERDUE_BADGE_BG,
            badge_fg=_OVERDUE_BADGE_FG,
        ))

    if collection.due_today_action_items:
        sections.append(_action_items_section(
            collection.due_today_action_items,
            label="Due today",
            badge_bg=_DUE_TODAY_BADGE_BG,
            badge_fg=_DUE_TODAY_BADGE_FG,
        ))

    if collection.due_this_week_action_items:
        sections.append(_action_items_section(
            collection.due_this_week_action_items,
            label="Due this week",
            badge_bg=_DUE_WEEK_BADGE_BG,
            badge_fg=_DUE_WEEK_BADGE_FG,
        ))

    if collection.fresh_action_items:
        sections.append(_action_items_section(
            collection.fresh_action_items,
            label="Fresh from overnight",
            badge_bg=_FRESH_BADGE_BG,
            badge_fg=_FRESH_BADGE_FG,
        ))

    if collection.tlh_candidates:
        sections.append(_tlh_section(collection.tlh_candidates))

    if collection.new_documents:
        sections.append(_new_documents_section(collection.new_documents))

    inner = "\n".join(sections)
    return (
        f'<div style="padding:20px 24px;border-bottom:1px solid {_RULE};">'
        f'<div style="font-size:16px;font-weight:600;color:{_TEXT};margin-bottom:12px;">{escape(collection.collection_name)}</div>'
        f"{inner}"
        "</div>"
    )


def _action_items_section(
    items: list[dict[str, Any]],
    *,
    label: str,
    badge_bg: str,
    badge_fg: str,
) -> str:
    badge = (
        f'<span style="display:inline-block;background:{badge_bg};color:{badge_fg};'
        f'padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600;'
        f'letter-spacing:0.3px;text-transform:uppercase;">{escape(label)} · {len(items)}</span>'
    )
    rows: list[str] = []
    for item in items[:8]:
        description = escape(str(item.get("description") or "(no description)"))
        assignee = item.get("assignee")
        due = item.get("due_date")
        meta_bits: list[str] = []
        if assignee:
            meta_bits.append(escape(str(assignee)))
        if due:
            meta_bits.append("due " + escape(str(due)))
        meta = ""
        if meta_bits:
            meta = (
                f'<div style="font-size:12px;color:{_TEXT_SUBDUED};margin-top:2px;">'
                + " · ".join(meta_bits)
                + "</div>"
            )
        rows.append(
            f'<li style="margin:0 0 8px 0;font-size:14px;color:{_TEXT};line-height:1.45;">'
            f"{description}{meta}"
            "</li>"
        )
    if len(items) > 8:
        rows.append(
            f'<li style="margin:0;font-size:13px;color:{_TEXT_SUBDUED};list-style:none;">'
            f"+ {len(items) - 8} more"
            "</li>"
        )

    return (
        '<div style="margin-bottom:14px;">'
        f"{badge}"
        f'<ul style="margin:8px 0 0 18px;padding:0;">{"".join(rows)}</ul>'
        "</div>"
    )


def _tlh_section(candidates: list[dict[str, Any]]) -> str:
    total = sum(float(c.get("unrealized_loss") or 0) for c in candidates)
    badge = (
        f'<span style="display:inline-block;background:{_TLH_BADGE_BG};color:{_TLH_BADGE_FG};'
        f'padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600;'
        f'letter-spacing:0.3px;text-transform:uppercase;">Tax-loss · {len(candidates)} · ${total:,.0f}</span>'
    )
    rows: list[str] = []
    for c in candidates[:6]:
        symbol = escape(str(c.get("symbol") or "(unknown)"))
        account = escape(str(c.get("account") or ""))
        loss = float(c.get("unrealized_loss") or 0)
        loss_pct = c.get("loss_pct")
        holding = c.get("holding_period")
        pct_str = f" ({loss_pct:.1f}%)" if isinstance(loss_pct, (int, float)) else ""
        holding_label = ""
        if holding == "short_term":
            holding_label = '<span style="color:' + _TLH_BADGE_FG + ';font-size:11px;">short-term</span>'
        elif holding == "long_term":
            holding_label = f'<span style="color:{_TEXT_SUBDUED};font-size:11px;">long-term</span>'
        rows.append(
            '<tr>'
            f'<td style="padding:6px 8px 6px 0;font-size:14px;color:{_TEXT};font-weight:500;">{symbol}</td>'
            f'<td style="padding:6px 8px;font-size:13px;color:{_TEXT_SUBDUED};">{account}</td>'
            f'<td style="padding:6px 0 6px 8px;font-size:14px;color:{_TLH_BADGE_FG};text-align:right;white-space:nowrap;">-${loss:,.0f}{pct_str}</td>'
            f'<td style="padding:6px 0 6px 8px;text-align:right;white-space:nowrap;">{holding_label}</td>'
            '</tr>'
        )
    if len(candidates) > 6:
        rows.append(
            f'<tr><td colspan="4" style="padding:6px 0;font-size:13px;color:{_TEXT_SUBDUED};">+ {len(candidates) - 6} more</td></tr>'
        )

    return (
        '<div style="margin-bottom:14px;">'
        f"{badge}"
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="margin-top:8px;border-collapse:collapse;">'
        + "".join(rows)
        + "</table>"
        "</div>"
    )


def _new_documents_section(docs: list[dict[str, Any]]) -> str:
    badge = (
        f'<span style="display:inline-block;background:{_DOCS_BADGE_BG};color:{_DOCS_BADGE_FG};'
        f'padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600;'
        f'letter-spacing:0.3px;text-transform:uppercase;">New documents · {len(docs)}</span>'
    )
    rows: list[str] = []
    for doc in docs:
        filename = escape(str(doc.get("filename") or "(unnamed)"))
        pages = doc.get("num_pages")
        meta = ""
        if isinstance(pages, int) and pages > 0:
            meta = f' <span style="color:{_TEXT_SUBDUED};font-size:12px;">· {pages} page{"s" if pages != 1 else ""}</span>'
        rows.append(
            f'<li style="margin:0 0 6px 0;font-size:14px;color:{_TEXT};line-height:1.45;">'
            f"{filename}{meta}"
            "</li>"
        )

    return (
        '<div style="margin-bottom:14px;">'
        f"{badge}"
        f'<ul style="margin:8px 0 0 18px;padding:0;">{"".join(rows)}</ul>'
        "</div>"
    )


def _format_item_text(item: dict[str, Any]) -> str:
    description = str(item.get("description") or "(no description)")
    assignee = item.get("assignee")
    due = item.get("due_date")
    meta_bits: list[str] = []
    if assignee:
        meta_bits.append(str(assignee))
    if due:
        meta_bits.append(f"due {due}")
    if meta_bits:
        return f"{description}  ({' · '.join(meta_bits)})"
    return description


def _footer_block() -> str:
    return (
        f'<div style="padding:20px 24px;background:{_CARD_BG};">'
        f'<div style="font-size:12px;color:{_TEXT_SUBDUED};line-height:1.5;">'
        "Computed locally from your collections in Finn — no AI provider call. "
        f'Manage your brief preferences in <a href="#" style="color:{_BRAND_ACCENT};">Finn → Settings → Morning brief</a>.'
        "</div>"
        "</div>"
    )
