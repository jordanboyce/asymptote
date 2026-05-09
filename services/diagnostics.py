"""In-memory diagnostics buffer for development.

Captures two streams in thread-safe ring buffers:

1. Python `logging` records, via a `logging.Handler` subclass.
2. Chat-engine events (`thinking`, `tool_start`, `tool_end`, `error`, ...),
   pushed by the chat endpoint as it forwards SSE to the client.

Both are exposed via `/api/diagnostics/*` so a developer can inspect or copy
the recent state from a frontend tab without needing to scrape stdout.

Intentionally in-memory only — no persistence, no rotation to disk. Restart
clears the buffers. Sized for a single dev session, not production logging.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any

LOG_BUFFER_MAXLEN = 1000
EVENT_BUFFER_MAXLEN = 500

_LEVEL_ORDER = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


@dataclass
class LogRecord:
    ts: float
    level: str
    logger: str
    message: str
    exc_info: str | None = None


@dataclass
class ChatEvent:
    ts: float
    turn_id: str
    collection_id: str
    type: str
    payload: dict[str, Any] = field(default_factory=dict)


class DiagnosticsBuffer:
    """Thread-safe in-memory ring buffer for log records and chat events."""

    def __init__(self) -> None:
        self._logs: deque[LogRecord] = deque(maxlen=LOG_BUFFER_MAXLEN)
        self._events: deque[ChatEvent] = deque(maxlen=EVENT_BUFFER_MAXLEN)
        self._lock = threading.Lock()

    def push_log(self, rec: LogRecord) -> None:
        with self._lock:
            self._logs.append(rec)

    def push_event(self, ev: ChatEvent) -> None:
        with self._lock:
            self._events.append(ev)

    def recent_logs(self, limit: int = 200, min_level: str = "DEBUG") -> list[dict]:
        try:
            min_idx = _LEVEL_ORDER.index(min_level.upper())
        except ValueError:
            min_idx = 0
        with self._lock:
            items = list(self._logs)
        out: list[dict] = []
        for r in items:
            try:
                if _LEVEL_ORDER.index(r.level) < min_idx:
                    continue
            except ValueError:
                # Custom levels we don't know about — let them through.
                pass
            out.append(asdict(r))
        return out[-limit:]

    def recent_events(self, limit: int = 100) -> list[dict]:
        with self._lock:
            items = list(self._events)[-limit:]
        return [asdict(e) for e in items]

    def clear(self) -> None:
        with self._lock:
            self._logs.clear()
            self._events.clear()


# Module-level singleton — installed once at app startup, read by the
# /api/diagnostics endpoints and written by both the log handler and the
# chat-stream endpoint.
_buffer = DiagnosticsBuffer()


def get_buffer() -> DiagnosticsBuffer:
    return _buffer


class DiagnosticsLogHandler(logging.Handler):
    """A `logging.Handler` that pushes each record into the buffer.

    Plays alongside the existing stream handler — we do not reroute or
    suppress stdout logging; this handler is purely additive.
    """

    def __init__(self, buffer: DiagnosticsBuffer) -> None:
        super().__init__()
        self._buffer = buffer
        # Used only to format the traceback string for exc_info; the handler
        # itself emits structured fields, not a formatted line.
        self._exc_formatter = logging.Formatter()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            exc = (
                self._exc_formatter.formatException(record.exc_info)
                if record.exc_info
                else None
            )
            self._buffer.push_log(
                LogRecord(
                    ts=record.created,
                    level=record.levelname,
                    logger=record.name,
                    message=record.getMessage(),
                    exc_info=exc,
                )
            )
        except Exception:  # noqa: BLE001 - matches stdlib Handler.handleError contract
            self.handleError(record)


def install(level: int = logging.INFO) -> DiagnosticsBuffer:
    """Install the diagnostics handler on the root logger. Idempotent."""
    root = logging.getLogger()
    for h in root.handlers:
        if isinstance(h, DiagnosticsLogHandler):
            return _buffer
    handler = DiagnosticsLogHandler(_buffer)
    handler.setLevel(level)
    root.addHandler(handler)
    return _buffer


def record_chat_event(
    *,
    turn_id: str,
    collection_id: str,
    type_: str,
    payload: dict[str, Any] | None = None,
) -> None:
    """Push a chat-engine event into the diagnostics buffer.

    Called by the chat-stream endpoint as it forwards events to the client,
    so the buffer reflects exactly what the frontend received (post any
    transforms the endpoint applies). Failures are swallowed — diagnostics
    must never break the user-facing stream.
    """
    try:
        _buffer.push_event(
            ChatEvent(
                ts=time.time(),
                turn_id=turn_id,
                collection_id=collection_id,
                type=type_,
                payload=payload or {},
            )
        )
    except Exception:  # noqa: BLE001
        pass
