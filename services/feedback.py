"""Feedback report delivery via Resend.

Assembles a user-submitted issue report (free-form description plus an
optional snapshot of the in-memory diagnostics buffer) and emails it to
the address configured in `settings.feedback_email_to`. Diagnostic
contents are run through `services.privacy.redaction_engine` first so
client names, account numbers, and other PII never leave the box —
matching the boundary the MCP layer enforces for tool output.
"""

from __future__ import annotations

import logging
import platform
from dataclasses import dataclass
from html import escape
from typing import Any

from config import settings

logger = logging.getLogger(__name__)

RESEND_ENDPOINT = "https://api.resend.com/emails"

# Caps on diagnostic payload size pulled into the email. Keeps reports
# small enough that Resend won't reject them and reviewers don't drown.
MAX_LOGS = 150
MAX_EVENTS = 80
MAX_DESCRIPTION_CHARS = 8000


@dataclass
class FeedbackContext:
    description: str
    include_diagnostics: bool
    app_route: str | None
    collection_id: str | None
    user_agent: str | None


def _redact(text: str) -> str:
    if not text:
        return text
    try:
        from services.privacy.redaction_engine import redaction_engine
        return redaction_engine.redact_text(text).redacted_text
    except Exception:
        logger.exception("Feedback redaction failed; dropping diagnostic text")
        return "[redaction failed — content withheld]"


def _collect_diagnostics() -> dict[str, Any]:
    from services.diagnostics import get_buffer

    buf = get_buffer()
    raw_logs = buf.recent_logs(limit=MAX_LOGS, min_level="INFO")
    raw_events = buf.recent_events(limit=MAX_EVENTS)

    logs: list[dict[str, Any]] = []
    for r in raw_logs:
        logs.append({
            "ts": r.get("ts"),
            "level": r.get("level"),
            "logger": r.get("logger"),
            "message": _redact(r.get("message") or ""),
            "exc_info": _redact(r.get("exc_info") or "") if r.get("exc_info") else None,
        })

    events: list[dict[str, Any]] = []
    for e in raw_events:
        payload = e.get("payload") or {}
        # Stringify and redact the payload as a whole — it can hold tool
        # arguments and snippets that contain client identifiers.
        try:
            import json
            payload_str = _redact(json.dumps(payload, default=str))
        except Exception:
            payload_str = "[unserializable payload]"
        events.append({
            "ts": e.get("ts"),
            "turn_id": e.get("turn_id"),
            "collection_id": e.get("collection_id"),
            "type": e.get("type"),
            "payload": payload_str,
        })

    return {"logs": logs, "events": events}


def _format_log_line(log: dict[str, Any]) -> str:
    ts = log.get("ts")
    level = log.get("level", "")
    name = log.get("logger", "")
    message = log.get("message", "")
    line = f"[{level:<8}] {name}: {message}"
    if log.get("exc_info"):
        line += "\n" + log["exc_info"]
    return line


def _format_event_line(ev: dict[str, Any]) -> str:
    return (
        f"turn={ev.get('turn_id', '')} "
        f"collection={ev.get('collection_id', '')} "
        f"type={ev.get('type', '')} "
        f"payload={ev.get('payload', '')}"
    )


def _build_email_body(ctx: FeedbackContext, diag: dict[str, Any] | None) -> tuple[str, str]:
    """Return (text_body, html_body)."""
    desc = ctx.description.strip()[:MAX_DESCRIPTION_CHARS]

    metadata_lines = [
        f"App version: {settings.app_version}",
        f"Platform: {platform.system()} {platform.release()}",
        f"Python: {platform.python_version()}",
    ]
    if ctx.app_route:
        metadata_lines.append(f"Route: {ctx.app_route}")
    if ctx.collection_id:
        metadata_lines.append(f"Collection: {ctx.collection_id}")
    if ctx.user_agent:
        metadata_lines.append(f"User-Agent: {ctx.user_agent}")

    text_parts = [
        "Finn — issue report",
        "=" * 60,
        "",
        "## Description",
        desc or "(no description provided)",
        "",
        "## Environment",
        *metadata_lines,
    ]

    if diag is not None:
        log_lines = [_format_log_line(r) for r in diag["logs"]] or ["(none)"]
        event_lines = [_format_event_line(e) for e in diag["events"]] or ["(none)"]
        text_parts += [
            "",
            f"## Recent logs (last {len(diag['logs'])}, INFO+, PII-redacted)",
            *log_lines,
            "",
            f"## Recent chat events (last {len(diag['events'])}, PII-redacted)",
            *event_lines,
        ]

    text_body = "\n".join(text_parts)

    # HTML body — same content with simple structure for readability.
    html_parts = [
        "<h2>Finn — issue report</h2>",
        "<h3>Description</h3>",
        f"<pre style='white-space:pre-wrap;font-family:inherit'>{escape(desc) or '<em>(no description provided)</em>'}</pre>",
        "<h3>Environment</h3>",
        "<ul>" + "".join(f"<li>{escape(line)}</li>" for line in metadata_lines) + "</ul>",
    ]
    if diag is not None:
        if diag["logs"]:
            html_parts.append(f"<h3>Recent logs ({len(diag['logs'])}, INFO+, PII-redacted)</h3>")
            html_parts.append(
                "<pre style='font-size:11px;white-space:pre-wrap;background:#f6f6f6;padding:8px;border-radius:4px'>"
                + escape("\n".join(_format_log_line(r) for r in diag["logs"]))
                + "</pre>"
            )
        if diag["events"]:
            html_parts.append(f"<h3>Recent chat events ({len(diag['events'])}, PII-redacted)</h3>")
            html_parts.append(
                "<pre style='font-size:11px;white-space:pre-wrap;background:#f6f6f6;padding:8px;border-radius:4px'>"
                + escape("\n".join(_format_event_line(e) for e in diag["events"]))
                + "</pre>"
            )
    html_body = "".join(html_parts)

    return text_body, html_body


def send_feedback(ctx: FeedbackContext) -> dict[str, Any]:
    """Send a feedback report. Returns {ok, id?, error?}."""
    if not settings.resend_api_key:
        return {"ok": False, "error": "Email delivery not configured (RESEND_API_KEY missing)."}
    if not settings.feedback_email_to:
        return {"ok": False, "error": "Email delivery not configured (FEEDBACK_EMAIL_TO missing)."}

    diag = _collect_diagnostics() if ctx.include_diagnostics else None
    text_body, html_body = _build_email_body(ctx, diag)

    subject_hint = (ctx.description.strip().splitlines() or [""])[0][:80] or "Issue report"
    subject = f"[Finn feedback] {subject_hint}"

    payload = {
        "from": settings.feedback_email_from or "Finn Feedback <onboarding@resend.dev>",
        "to": [settings.feedback_email_to],
        "subject": subject,
        "text": text_body,
        "html": html_body,
    }

    import httpx

    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(
                RESEND_ENDPOINT,
                json=payload,
                headers={
                    "Authorization": f"Bearer {settings.resend_api_key}",
                    "Content-Type": "application/json",
                },
            )
        if resp.status_code >= 400:
            logger.error(
                "Resend rejected feedback email (status=%s): %s",
                resp.status_code, resp.text[:500],
            )
            return {"ok": False, "error": f"Email delivery failed (HTTP {resp.status_code})."}
        data = resp.json() if resp.content else {}
        return {"ok": True, "id": data.get("id")}
    except Exception as e:
        logger.exception("Feedback delivery failed")
        return {"ok": False, "error": f"Email delivery failed: {e}"}
