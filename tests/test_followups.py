"""Unit tests for AIService.suggest_followups — Perplexity-style "Related".

Covers the line-cleaning helper and the generator's contract: cheap model,
advisor framing for financial collections, and the never-break guarantees
(empty input short-circuits, provider errors are swallowed).
"""

from services.ai_service import AIService, _parse_followup_lines
from tests._fake_ai_provider import FakeAIProvider


def test_parse_strips_markers_and_caps():
    text = "1. What is the duration?\n- How concentrated is tech?\n* Any cash drag?\n2) One too many?"
    assert _parse_followup_lines(text, 3) == [
        "What is the duration?",
        "How concentrated is tech?",
        "Any cash drag?",
    ]


def test_parse_drops_echoed_header_and_dupes():
    text = "Follow-up questions:\nWhat is the yield?\nwhat is the yield?\n\nWhat about fees?"
    assert _parse_followup_lines(text, 5) == [
        "What is the yield?",
        "What about fees?",
    ]


def test_suggest_followups_uses_fast_model_and_advisor_framing():
    fake = FakeAIProvider()
    fake.queue_complete_response("How concentrated is the client's tech exposure?\nWhat is the cash position?")
    out = AIService(provider=fake).suggest_followups("orig?", "an answer", kind="financial")

    assert out == [
        "How concentrated is the client's tech exposure?",
        "What is the cash position?",
    ]
    # Cheap model, not the quality model — these are throwaway suggestions.
    assert fake.complete_calls[-1]["model"] == fake.FAST_MODEL
    # Financial/mixed collections keep Finn's third-person advisor framing.
    assert "third person" in fake.complete_calls[-1]["prompt"]


def test_general_kind_omits_advisor_framing():
    fake = FakeAIProvider()
    fake.queue_complete_response("What is the main theme?")
    AIService(provider=fake).suggest_followups("orig?", "an answer", kind="general")
    assert "third person" not in fake.complete_calls[-1]["prompt"]


def test_empty_answer_skips_provider_call():
    fake = FakeAIProvider()
    assert AIService(provider=fake).suggest_followups("q?", "   ") == []
    assert fake.complete_calls == []


def test_provider_error_returns_empty():
    class Boom(FakeAIProvider):
        def complete(self, prompt, max_tokens, model):
            raise RuntimeError("kaboom")

    assert AIService(provider=Boom()).suggest_followups("q?", "real answer") == []
