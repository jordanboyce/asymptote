"""Strip `<think>...</think>` reasoning blocks from model output.

Open-weight models with explicit thinking modes (Gemma 4, DeepSeek, gpt-oss,
Kimi, etc.) emit reasoning inside `<think>...</think>` tags inside the assistant
text. The advisor shouldn't see this bleed into the chat bubble — it's
internal-monologue noise, not the answer.

Two surfaces:

  * :class:`StreamingThinkStripper` — stateful filter for token-by-token
    streams. Buffers incomplete tag fragments across delta boundaries so
    `<thi` followed by `nk>` is recognized as one opening tag, and emits
    only the visible text.

  * :func:`strip_think_tags` — buffered string transformation. Use for
    thinking-event text (already accumulated between tool-call turns)
    where streaming behaviour doesn't apply.

Both treat `<think>` and `<thinking>` as synonyms — different vendors prefer
different tag names.
"""

from __future__ import annotations

import re
from typing import List

# Match an opening tag (think or thinking, lowercase) optionally with
# attributes; same for closing. The tags we've seen are plain — no attrs —
# but be permissive so we don't choke on a future variant.
_OPEN_TAGS = ("<think>", "<thinking>")
_CLOSE_TAGS = ("</think>", "</thinking>")

_THINK_BLOCK_RE = re.compile(
    r"<think(?:ing)?>.*?</think(?:ing)?>",
    flags=re.IGNORECASE | re.DOTALL,
)


def strip_think_tags(text: str) -> str:
    """Remove every well-formed `<think>...</think>` block from ``text``.

    Drops both the tags and the content. Does not strip an unclosed opening
    tag — leaves it intact so callers can see something is malformed rather
    than silently swallowing the rest of the response. Collapses runs of
    blank lines that the removal leaves behind.
    """
    if not text:
        return text
    cleaned = _THINK_BLOCK_RE.sub("", text)
    # Collapse 3+ consecutive newlines (left behind by removed blocks) to 2.
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip("\n")


class StreamingThinkStripper:
    """Stateful streaming filter — feed deltas, emit visible text.

    The streamed assistant text arrives in arbitrary chunks (often
    sub-token), so an opening or closing tag may straddle two deltas
    (`<thi` + `nk>`). We hold back any trailing chars that *could* be a
    partial tag and emit them only once we've decided.
    """

    # Longest prefix we might need to hold back. `</thinking>` is 11 chars;
    # one less to guard the partial. We hold up to that many trailing chars.
    _MAX_TAG_LEN = max(len(t) for t in (*_OPEN_TAGS, *_CLOSE_TAGS))

    def __init__(self) -> None:
        self._pending = ""        # bytes we haven't emitted yet (might be partial tag)
        self._inside_think = False

    def feed(self, delta: str) -> str:
        """Consume ``delta``, return the slice that should be visible."""
        if not delta:
            return ""
        buf = self._pending + delta
        self._pending = ""
        out: List[str] = []
        i = 0
        n = len(buf)
        while i < n:
            if not self._inside_think:
                idx, tag = _find_first(buf, _OPEN_TAGS, i)
                if idx == -1:
                    # No complete open tag in remainder. Hold back only if
                    # the tail could start one — i.e. contains a `<`. The
                    # common case (no angle brackets at all) flushes
                    # immediately so streaming doesn't feel choppy.
                    last_lt = buf.rfind("<", i)
                    if last_lt == -1:
                        out.append(buf[i:])
                        break
                    if last_lt > i:
                        out.append(buf[i:last_lt])
                    self._pending = buf[last_lt:]
                    break
                # Emit text before the tag, then drop the tag itself.
                if idx > i:
                    out.append(buf[i:idx])
                i = idx + len(tag)
                self._inside_think = True
            else:
                idx, tag = _find_first(buf, _CLOSE_TAGS, i)
                if idx == -1:
                    # Stay inside the block; hold back the tail only if it
                    # could start a closing tag (contains `<`).
                    last_lt = buf.rfind("<", i)
                    self._pending = buf[last_lt:] if last_lt != -1 else ""
                    break
                # Drop everything up to and including the close tag.
                i = idx + len(tag)
                self._inside_think = False
        return "".join(out)

    def flush(self) -> str:
        """Drain any pending bytes once the stream ends.

        If the stream closed inside a `<think>` block (model bug, truncated
        response, etc.), the held-back bytes are discarded — they're inside
        a never-closed thinking block. If we were outside, they're emitted
        as-is so the user doesn't lose trailing answer text.
        """
        rest = self._pending
        self._pending = ""
        if self._inside_think:
            return ""
        return rest


def _find_first(haystack: str, needles: tuple[str, ...], start: int) -> tuple[int, str]:
    """Return the earliest (index, needle) of any needle found at or after start.

    Falls back to (-1, "") when no needle matches. Case-insensitive on the
    tag spelling but NOT on the angle brackets — the latter are unambiguous.
    """
    lower = haystack.lower()
    best_idx = -1
    best_needle = ""
    for needle in needles:
        idx = lower.find(needle, start)
        if idx == -1:
            continue
        if best_idx == -1 or idx < best_idx:
            best_idx = idx
            best_needle = needle
    return best_idx, best_needle
