"""Tests for the <think>...</think> stripper used in chat engine output."""

from __future__ import annotations

import pytest

from services.chat.think_tags import StreamingThinkStripper, strip_think_tags


# ─── Buffered helper ────────────────────────────────────────────────────────

def test_strip_simple_block():
    assert strip_think_tags("hi <think>secret</think>there") == "hi there"


def test_strip_thinking_synonym():
    assert strip_think_tags("a<thinking>x</thinking>b") == "ab"


def test_strip_multiple_blocks():
    text = "before<think>one</think>middle<think>two</think>after"
    assert strip_think_tags(text) == "beforemiddleafter"


def test_strip_multiline_block():
    text = "answer\n\n<think>\nstep 1\nstep 2\n</think>\n\ndone"
    # The post-strip blank-line collapse trims runs of >2 newlines.
    assert "<think>" not in strip_think_tags(text)
    assert "step 1" not in strip_think_tags(text)
    assert "answer" in strip_think_tags(text)
    assert "done" in strip_think_tags(text)


def test_strip_unclosed_tag_left_intact():
    # Don't silently swallow the rest of the response when a tag is malformed.
    text = "hello <think>unfinished"
    assert strip_think_tags(text) == "hello <think>unfinished"


def test_strip_empty():
    assert strip_think_tags("") == ""
    assert strip_think_tags("plain text, no tags") == "plain text, no tags"


def test_case_insensitive_tags():
    assert strip_think_tags("a<THINK>x</Think>b") == "ab"


# ─── Streaming filter ───────────────────────────────────────────────────────

def _drive(stripper: StreamingThinkStripper, deltas: list[str]) -> str:
    parts = [stripper.feed(d) for d in deltas]
    parts.append(stripper.flush())
    return "".join(parts)


def test_stream_no_tags_passthrough():
    s = StreamingThinkStripper()
    assert _drive(s, ["hello ", "world"]) == "hello world"


def test_stream_complete_block_in_one_delta():
    s = StreamingThinkStripper()
    assert _drive(s, ["before<think>secret</think>after"]) == "beforeafter"


def test_stream_open_tag_split_across_deltas():
    s = StreamingThinkStripper()
    # `<thi` then `nk>x</think>!` — opening tag straddles delta boundary.
    assert _drive(s, ["pre <thi", "nk>x</think>!"]) == "pre !"


def test_stream_close_tag_split_across_deltas():
    s = StreamingThinkStripper()
    assert _drive(s, ["a<think>x</thi", "nk>b"]) == "ab"


def test_stream_block_spans_many_deltas():
    s = StreamingThinkStripper()
    assert _drive(s, ["start ", "<think>", "step 1", " step 2", "</think>", " end"]) == "start  end"


def test_stream_unclosed_block_drops_pending():
    s = StreamingThinkStripper()
    # Stream ends inside <think>...> the buffered tail is discarded.
    assert _drive(s, ["ok ", "<think>partial"]) == "ok "


def test_stream_emits_deltas_promptly():
    """Visible text should not be held back longer than the max-tag lookahead."""
    s = StreamingThinkStripper()
    # 11 chars (`</thinking>`) is the max held back; anything before that
    # plus past-the-tail-window emits.
    out = s.feed("hello world this is some text")
    assert "hello world this is" in out  # at least most of it should flush
    rest = s.flush()
    assert (out + rest) == "hello world this is some text"


def test_stream_two_blocks_same_delta():
    s = StreamingThinkStripper()
    assert _drive(s, ["a<think>1</think>b<think>2</think>c"]) == "abc"


def test_stream_thinking_synonym():
    s = StreamingThinkStripper()
    assert _drive(s, ["x<thinking>y</thinking>z"]) == "xz"
