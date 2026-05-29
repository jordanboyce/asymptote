"""HTML + plain-text renderers for the weekly advisor digest email.

Pure presentation — takes a :class:`services.digest.DigestPayload` and
returns two strings. No I/O, no LLM, easy to snapshot-test.

Email-client constraints honored:

* Inline CSS only (Outlook on Windows strips ``<style>`` blocks).
* Table-based layout for cards — no flexbox or grid.
* Max content width 600px for phone clients.
* All colour values explicit; no CSS variables.
* No web fonts, no JS, no external images.
"""

from __future__ import annotations

from html import escape
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from services.digest import CollectionSignals, DigestPayload


# Brand palette — neutral, advisor-appropriate. Keep narrow so we don't end
# up with a circus on a print-out.
_BRAND_PRIMARY = "#1f3a5f"      # deep navy — header band
_BRAND_ACCENT = "#3b6ea5"       # softer navy — links
_TEXT = "#1a1a1a"
_TEXT_SUBDUED = "#666666"
_RULE = "#e5e5e5"
_CARD_BG = "#fafafa"
_NEW_BADGE_BG = "#e8f1ff"
_NEW_BADGE_FG = "#1f3a5f"
_AGING_BADGE_BG = "#fff4e0"
_AGING_BADGE_FG = "#8a5a00"
_TLH_BADGE_BG = "#fde8e8"
_TLH_BADGE_FG = "#9b1c1c"


def render_digest_html(payload: "DigestPayload", *, is_preview: bool = False) -> str:
    """Render the digest as an Outlook-safe HTML document."""
    summary = _summary_line(payload)
    body_blocks: list[str] = []

    if is_preview:
        body_blocks.append(_preview_banner())

    body_blocks.append(_header_block(payload, summary))

    if not payload.has_anything:
        body_blocks.append(_quiet_week_block())
    else:
        for collection in payload.collections:
            if collection.is_empty:
                continue
            body_blocks.append(_collection_card(collection))

    body_blocks.append(_footer_block(payload))

    body_html = "\n".join(body_blocks)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light">
<title>Finn weekly digest</title>
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


def render_digest_text(payload: "DigestPayload", *, is_preview: bool = False) -> str:
    """Render the digest as a plain-text fallback (for clients that prefer it)."""
    out: list[str] = []
    if is_preview:
        out.append("[Preview — this is a sample digest sent on demand.]\n")
    out.append(f"Finn weekly digest — {payload.period_label}")
    out.append("=" * 60)
    out.append(_summary_line(payload, plain=True))
    out.append("")

    if not payload.has_anything:
        out.append("Nothing changed across your collections this week.")
        out.append("Finn will check again next week.")
    else:
        for collection in payload.collections:
            if collection.is_empty:
                continue
            out.append(collection.collection_name)
            out.append("-" * len(collection.collection_name))

            if collection.new_action_items:
                out.append(f"  New action items ({len(collection.new_action_items)}):")
                for item in collection.new_action_items:
                    out.append(f"    - {item.get('description', '')}")

            if collection.aging_action_items:
                out.append(f"  Aging > 30 days ({len(collection.aging_action_items)}):")
                for item in collection.aging_action_items:
                    out.append(f"    - {item.get('description', '')}")

            if collection.tlh_candidates:
                total = sum(float(c.get("unrealized_loss") or 0) for c in collection.tlh_candidates)
                out.append(f"  Tax-loss candidates ({len(collection.tlh_candidates)}, ${total:,.0f} total):")
                for c in collection.tlh_candidates[:5]:
                    sym = c.get("symbol") or "(unknown)"
                    loss = float(c.get("unrealized_loss") or 0)
                    out.append(f"    - {sym}: ${loss:,.0f} unrealized loss")
            out.append("")

    out.append("")
    out.append("— Finn")
    out.append("Manage your digest preferences in Finn → Settings → Weekly digest.")
    return "\n".join(out)


# ─── Internal block builders ───────────────────────────────────────────────


def _summary_line(payload: "DigestPayload", *, plain: bool = False) -> str:
    parts: list[str] = []
    n_new = payload.total_new_action_items
    n_aging = payload.total_aging_action_items
    n_tlh = sum(len(c.tlh_candidates) for c in payload.collections)
    tlh_total = payload.total_tlh_dollars

    if n_new:
        parts.append(f"{n_new} new action item{'s' if n_new != 1 else ''}")
    if n_aging:
        parts.append(f"{n_aging} aging > 30 days")
    if n_tlh:
        parts.append(f"{n_tlh} tax-loss candidate{'s' if n_tlh != 1 else ''} (${tlh_total:,.0f})")

    if not parts:
        return "No new signals this week."
    if plain:
        return "This week: " + ", ".join(parts) + "."
    return "This week: " + ", ".join(parts) + "."


def _preview_banner() -> str:
    return (
        f'<div style="background:#fff4e0;color:#8a5a00;padding:10px 24px;'
        f'font-size:13px;border-bottom:1px solid {_RULE};">'
        "Preview — this is a sample digest sent on demand. Your scheduled digests fire on your chosen day."
        "</div>"
    )


def _header_block(payload: "DigestPayload", summary: str) -> str:
    return (
        f'<div style="background:{_BRAND_PRIMARY};color:#ffffff;padding:24px;">'
        f'<div style="font-size:13px;letter-spacing:0.5px;text-transform:uppercase;opacity:0.85;">Finn</div>'
        f'<div style="font-size:22px;font-weight:600;margin-top:4px;">Weekly digest</div>'
        f'<div style="font-size:14px;opacity:0.9;margin-top:2px;">{escape(payload.period_label)}</div>'
        "</div>"
        f'<div style="padding:20px 24px;border-bottom:1px solid {_RULE};">'
        f'<div style="font-size:15px;color:{_TEXT};line-height:1.5;">{escape(summary)}</div>'
        "</div>"
    )


def _quiet_week_block() -> str:
    return (
        f'<div style="padding:32px 24px;text-align:center;color:{_TEXT_SUBDUED};">'
        f'<div style="font-size:16px;color:{_TEXT};font-weight:500;">Nothing new across your collections this week.</div>'
        f'<div style="font-size:13px;margin-top:8px;">Finn will check again next week.</div>'
        "</div>"
    )


def _collection_card(collection: "CollectionSignals") -> str:
    sections: list[str] = []

    if collection.new_action_items:
        sections.append(_action_items_section(
            collection.new_action_items,
            label="New action items",
            badge_bg=_NEW_BADGE_BG,
            badge_fg=_NEW_BADGE_FG,
        ))

    if collection.aging_action_items:
        sections.append(_action_items_section(
            collection.aging_action_items,
            label="Aging > 30 days",
            badge_bg=_AGING_BADGE_BG,
            badge_fg=_AGING_BADGE_FG,
        ))

    if collection.tlh_candidates:
        sections.append(_tlh_section(collection.tlh_candidates))

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


def _footer_block(payload: "DigestPayload") -> str:
    return (
        f'<div style="padding:20px 24px;background:{_CARD_BG};">'
        f'<div style="font-size:12px;color:{_TEXT_SUBDUED};line-height:1.5;">'
        "Computed locally from your collections in Finn — no AI provider call. "
        f'Manage your digest preferences in <a href="#" style="color:{_BRAND_ACCENT};">Finn → Settings → Weekly digest</a>.'
        "</div>"
        "</div>"
    )
