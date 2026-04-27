"""Tests for LLM-assisted column role inference (v4.1 P0.5).

Tests prompt construction, response parsing, redaction integration,
and the structured_store integration trigger logic.

Run with:
    pytest tests/test_llm_role_inference.py -v
"""

from __future__ import annotations

import json
import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.llm_role_inference import (
    ROLE_TAXONOMY,
    RoleProposal,
    _build_user_prompt,
    _parse_response,
    _redact_samples,
    infer_roles_with_llm,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_COLUMNS = [
    {"name": "Holding", "type": "text", "samples": ["Apple Inc.", "Microsoft Corp", "Tesla Inc"]},
    {"name": "Mkt Val", "type": "real", "samples": ["125,000.50", "89,320.00", "45,100.75"]},
    {"name": "Acct #", "type": "text", "samples": ["ACC-12345678", "ACC-98765432", "ACC-55512345"]},
    {"name": "Purch Dt", "type": "text", "samples": ["2024-01-15", "2023-06-22", "2025-02-10"]},
    {"name": "Wgt", "type": "real", "samples": ["12.5", "8.9", "4.5"]},
]


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

class TestBuildUserPrompt:
    def test_includes_all_columns(self):
        prompt = _build_user_prompt(SAMPLE_COLUMNS)
        for col in SAMPLE_COLUMNS:
            assert col["name"] in prompt

    def test_includes_sample_values(self):
        prompt = _build_user_prompt(SAMPLE_COLUMNS)
        assert "Apple Inc." in prompt
        assert "125,000.50" in prompt

    def test_includes_detected_type(self):
        prompt = _build_user_prompt(SAMPLE_COLUMNS)
        assert "detected type: text" in prompt
        assert "detected type: real" in prompt

    def test_empty_columns(self):
        prompt = _build_user_prompt([])
        assert "Columns to classify" in prompt

    def test_no_samples(self):
        cols = [{"name": "Mystery", "type": "text", "samples": []}]
        prompt = _build_user_prompt(cols)
        assert "(no samples)" in prompt


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

class TestParseResponse:
    def test_valid_json(self):
        response = json.dumps([
            {"column": "Holding", "role": "name", "confidence": 0.95},
            {"column": "Mkt Val", "role": "market_value", "confidence": 0.9},
            {"column": "Acct #", "role": "account", "confidence": 0.85},
        ])
        proposals = _parse_response(response, ["Holding", "Mkt Val", "Acct #"])
        assert len(proposals) == 3
        assert proposals[0].role == "name"
        assert proposals[0].confidence == 0.95

    def test_markdown_fenced_json(self):
        response = "```json\n" + json.dumps([
            {"column": "X", "role": "ticker", "confidence": 0.8},
        ]) + "\n```"
        proposals = _parse_response(response, ["X"])
        assert len(proposals) == 1
        assert proposals[0].role == "ticker"

    def test_null_role(self):
        response = json.dumps([
            {"column": "Notes", "role": None, "confidence": 0.0},
        ])
        proposals = _parse_response(response, ["Notes"])
        assert proposals[0].role is None

    def test_unknown_role_rejected(self):
        response = json.dumps([
            {"column": "X", "role": "banana", "confidence": 0.9},
        ])
        proposals = _parse_response(response, ["X"])
        assert proposals[0].role is None
        assert proposals[0].confidence == 0.0

    def test_invalid_json_returns_empty(self):
        proposals = _parse_response("not json at all", ["X"])
        assert proposals == []

    def test_non_array_returns_empty(self):
        proposals = _parse_response('{"column": "X"}', ["X"])
        assert proposals == []

    def test_missing_confidence_defaults_to_zero(self):
        response = json.dumps([{"column": "X", "role": "ticker"}])
        proposals = _parse_response(response, ["X"])
        assert proposals[0].confidence == 0.0


# ---------------------------------------------------------------------------
# Role taxonomy integrity
# ---------------------------------------------------------------------------

class TestRoleTaxonomy:
    def test_taxonomy_not_empty(self):
        assert len(ROLE_TAXONOMY) >= 19

    def test_all_roles_unique(self):
        roles = [r for r, _ in ROLE_TAXONOMY]
        assert len(roles) == len(set(roles))

    def test_taxonomy_matches_roles_module(self):
        """Ensure our taxonomy covers the same roles as financial/roles.py."""
        from services.financial.roles import _ROLE_PATTERNS
        pattern_roles = {r for r, _ in _ROLE_PATTERNS}
        taxonomy_roles = {r for r, _ in ROLE_TAXONOMY}
        assert pattern_roles == taxonomy_roles, (
            f"Mismatch: in patterns but not taxonomy: {pattern_roles - taxonomy_roles}, "
            f"in taxonomy but not patterns: {taxonomy_roles - pattern_roles}"
        )


# ---------------------------------------------------------------------------
# Redaction integration
# ---------------------------------------------------------------------------

class TestRedactSamples:
    def test_returns_same_structure(self):
        """Even without Presidio, should return same-shaped data."""
        result = _redact_samples(SAMPLE_COLUMNS)
        assert len(result) == len(SAMPLE_COLUMNS)
        for orig, redacted in zip(SAMPLE_COLUMNS, result):
            assert orig["name"] == redacted["name"]
            assert len(orig["samples"]) == len(redacted["samples"])

    def test_does_not_mutate_input(self):
        """Redaction must not modify the original column list."""
        import copy
        original = copy.deepcopy(SAMPLE_COLUMNS)
        _redact_samples(SAMPLE_COLUMNS)
        assert SAMPLE_COLUMNS == original


# ---------------------------------------------------------------------------
# Integration: infer_roles_with_llm with mocked provider
# ---------------------------------------------------------------------------

class TestInferRolesWithLLM:
    def test_returns_empty_when_no_provider(self, monkeypatch):
        """When mcp_ai_provider is 'none', should return {} without error."""
        from config import settings
        monkeypatch.setattr(settings, "enable_llm_schema_inference", True)
        monkeypatch.setattr(settings, "mcp_ai_provider", "none")

        result = infer_roles_with_llm(SAMPLE_COLUMNS)
        assert result == {}

    def test_returns_empty_for_empty_columns(self, monkeypatch):
        from config import settings
        monkeypatch.setattr(settings, "enable_llm_schema_inference", True)
        monkeypatch.setattr(settings, "mcp_ai_provider", "anthropic")

        result = infer_roles_with_llm([])
        assert result == {}

    def test_mocked_llm_call(self, monkeypatch):
        """Mock the AI provider to return a known response."""
        from config import settings
        monkeypatch.setattr(settings, "enable_llm_schema_inference", True)
        monkeypatch.setattr(settings, "mcp_ai_provider", "anthropic")

        llm_response = json.dumps([
            {"column": "Holding", "role": "name", "confidence": 0.92},
            {"column": "Mkt Val", "role": "market_value", "confidence": 0.95},
            {"column": "Acct #", "role": "account", "confidence": 0.88},
            {"column": "Purch Dt", "role": "date", "confidence": 0.85},
            {"column": "Wgt", "role": "weight", "confidence": 0.45},  # below threshold
        ])

        class MockProvider:
            FAST_MODEL = "mock-model"
            def complete(self, prompt, max_tokens, model):
                return {"text": llm_response, "usage": {"input_tokens": 100, "output_tokens": 50}}

        monkeypatch.setattr(
            "services.ai_service.create_provider",
            lambda *a, **kw: MockProvider(),
        )

        result = infer_roles_with_llm(SAMPLE_COLUMNS, confidence_threshold=0.6)
        assert result["Holding"] == "name"
        assert result["Mkt Val"] == "market_value"
        assert result["Acct #"] == "account"
        assert result["Purch Dt"] == "date"
        # Wgt should be excluded — confidence 0.45 < 0.6 threshold
        assert "Wgt" not in result

    def test_llm_failure_returns_empty(self, monkeypatch):
        """If the LLM call throws, should return {} gracefully."""
        from config import settings
        monkeypatch.setattr(settings, "enable_llm_schema_inference", True)
        monkeypatch.setattr(settings, "mcp_ai_provider", "anthropic")

        def broken_provider(*a, **kw):
            raise RuntimeError("API down")

        monkeypatch.setattr(
            "services.ai_service.create_provider",
            broken_provider,
        )

        result = infer_roles_with_llm(SAMPLE_COLUMNS)
        assert result == {}
