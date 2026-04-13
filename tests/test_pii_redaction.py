"""Regression tests for the PII redaction layer (v4.1 P0.0).

Tests that realistic PII is detected and redacted by the Presidio-based
engine, including standard entity types and custom financial recognizers.

Run with:
    pytest tests/test_pii_redaction.py -v

Requires: presidio-analyzer, presidio-anonymizer, spacy, en_core_web_lg
    pip install presidio-analyzer presidio-anonymizer spacy
    python -m spacy download en_core_web_lg
"""

from __future__ import annotations

import pytest
import sys
import os

# Ensure the project root is on sys.path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# Skip if Presidio is not installed
# ---------------------------------------------------------------------------

try:
    import presidio_analyzer  # noqa: F401
    import presidio_anonymizer  # noqa: F401
    HAS_PRESIDIO = True
except ImportError:
    HAS_PRESIDIO = False

pytestmark = pytest.mark.skipif(
    not HAS_PRESIDIO,
    reason="presidio-analyzer / presidio-anonymizer not installed",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Realistic fake PII that should be detected
SAMPLE_TEXT_WITH_PII = (
    "Client John Smith (SSN: 219-09-9999) holds account 12345678 at Fidelity. "
    "Contact: john.smith@example.com, phone (555) 123-4567. "
    "Mailing address: 123 Main Street, Springfield, IL 62701. "
    "Date of birth: 03/15/1975. "
    "Credit card ending in 4532-1234-5678-9012."
)

SAMPLE_TEXT_NO_PII = (
    "The S&P 500 index returned 12.5% in 2024. "
    "AAPL traded at $185.50 with a market cap of $2.89 trillion. "
    "The Federal Reserve held rates steady at 5.25-5.50%."
)

SAMPLE_FINANCIAL_ROWS = [
    {
        "Symbol": "AAPL",
        "Description": "Apple Inc",
        "Account": "Account #: 87654321",
        "Owner": "Jane Doe",
        "Market_Value": 125000.00,
        "Cost_Basis": 98000.00,
    },
    {
        "Symbol": "MSFT",
        "Description": "Microsoft Corp",
        "Account": "Acct 87654321",
        "Owner": "Jane Doe",
        "Market_Value": 85000.00,
        "Cost_Basis": 72000.00,
    },
]

SAMPLE_MCP_RESPONSE = {
    "collection_id": "default",
    "query": "client holdings",
    "results": [
        {
            "rank": 1,
            "filename": "portfolio.csv",
            "excerpt": "John Smith holds 500 shares of AAPL in account 12345678. SSN: 219-09-9999.",
            "document_id": "doc-abc-123",
            "similarity_score": 0.95,
        },
        {
            "rank": 2,
            "filename": "notes.txt",
            "excerpt": "Contact client at john.smith@example.com or (555) 123-4567.",
            "document_id": "doc-def-456",
            "similarity_score": 0.88,
        },
    ],
}


# ---------------------------------------------------------------------------
# Tests: Redaction Engine
# ---------------------------------------------------------------------------

class TestRedactionEngine:
    """Test the core Presidio wrapper."""

    def test_detects_person_name(self):
        from services.privacy.redaction_engine import redaction_engine
        result = redaction_engine.redact_text("Client John Smith called today.")
        assert "John Smith" not in result.redacted_text
        assert result.had_pii
        entity_types = {d.entity_type for d in result.details}
        assert "PERSON" in entity_types

    def test_detects_ssn(self):
        from services.privacy.redaction_engine import redaction_engine
        result = redaction_engine.redact_text("SSN: 219-09-9999")
        assert "219-09-9999" not in result.redacted_text
        assert result.had_pii

    def test_detects_email(self):
        from services.privacy.redaction_engine import redaction_engine
        result = redaction_engine.redact_text("Email: john.smith@example.com")
        assert "john.smith@example.com" not in result.redacted_text
        assert result.had_pii
        entity_types = {d.entity_type for d in result.details}
        assert "EMAIL_ADDRESS" in entity_types

    def test_detects_phone(self):
        from services.privacy.redaction_engine import redaction_engine
        result = redaction_engine.redact_text("Call me at (555) 123-4567.")
        assert "(555) 123-4567" not in result.redacted_text
        assert result.had_pii

    def test_detects_credit_card(self):
        from services.privacy.redaction_engine import redaction_engine
        result = redaction_engine.redact_text("Card: 4532-1234-5678-9012")
        assert "4532-1234-5678-9012" not in result.redacted_text
        assert result.had_pii

    def test_no_false_positives_on_clean_text(self):
        from services.privacy.redaction_engine import redaction_engine
        result = redaction_engine.redact_text(SAMPLE_TEXT_NO_PII)
        # Some entity detectors may flag dates/numbers, but core financial
        # terms like AAPL, S&P 500 should not be flagged as PERSON
        person_entities = [d for d in result.details if d.entity_type == "PERSON"]
        assert len(person_entities) == 0

    def test_empty_text_passthrough(self):
        from services.privacy.redaction_engine import redaction_engine
        result = redaction_engine.redact_text("")
        assert result.redacted_text == ""
        assert not result.had_pii

    def test_short_text_passthrough(self):
        from services.privacy.redaction_engine import redaction_engine
        result = redaction_engine.redact_text("hi")
        assert result.redacted_text == "hi"
        assert not result.had_pii

    def test_comprehensive_pii_redaction(self):
        """The big regression test: realistic text with multiple PII types."""
        from services.privacy.redaction_engine import redaction_engine
        result = redaction_engine.redact_text(SAMPLE_TEXT_WITH_PII)

        # None of the original PII should survive
        assert "John Smith" not in result.redacted_text
        assert "219-09-9999" not in result.redacted_text
        assert "john.smith@example.com" not in result.redacted_text
        assert "(555) 123-4567" not in result.redacted_text

        # Should have found multiple entity types
        entity_types = {d.entity_type for d in result.details}
        assert len(entity_types) >= 3  # At least PERSON, email, phone/SSN

    def test_structured_redaction(self):
        """Redact PII from structured row dicts."""
        from services.privacy.redaction_engine import redaction_engine
        redacted_rows, details = redaction_engine.redact_structured(
            SAMPLE_FINANCIAL_ROWS
        )

        # Owner name should be redacted
        for row in redacted_rows:
            assert "Jane Doe" not in str(row.get("Owner", ""))

        # Numeric values should be untouched
        assert redacted_rows[0]["Market_Value"] == 125000.00
        assert redacted_rows[1]["Cost_Basis"] == 72000.00

        # Symbol should be untouched (not PII)
        assert redacted_rows[0]["Symbol"] == "AAPL"


# ---------------------------------------------------------------------------
# Tests: Custom Financial Recognizers
# ---------------------------------------------------------------------------

class TestFinancialRecognizers:
    """Test custom recognizers for financial-specific PII."""

    def test_account_number_with_context(self):
        from services.privacy.redaction_engine import redaction_engine
        result = redaction_engine.redact_text(
            "Fidelity Account #: 12345678 holds 500 shares."
        )
        assert "12345678" not in result.redacted_text
        assert result.had_pii

    def test_account_with_acct_prefix(self):
        from services.privacy.redaction_engine import redaction_engine
        result = redaction_engine.redact_text("Acct 98765432 has a balance of $50,000.")
        assert "98765432" not in result.redacted_text
        assert result.had_pii

    def test_cusip_alone_not_redacted(self):
        """CUSIP without account-owner context should NOT be flagged."""
        from services.privacy.redaction_engine import redaction_engine
        result = redaction_engine.redact_text(
            "AAPL CUSIP is 037833100. Current price $185."
        )
        # The CUSIP itself should survive when there's no PII context
        cusip_redacted = any(
            d.entity_type == "CUSIP_IN_CONTEXT" for d in result.details
        )
        assert not cusip_redacted

    def test_cusip_with_account_context(self):
        """CUSIP near account holder info SHOULD be flagged."""
        from services.privacy.redaction_engine import redaction_engine
        result = redaction_engine.redact_text(
            "Client John Smith, account 12345678, holds CUSIP 037833100."
        )
        # In this context, CUSIP should be treated as PII-adjacent
        cusip_redacted = any(
            d.entity_type == "CUSIP_IN_CONTEXT" for d in result.details
        )
        assert cusip_redacted


# ---------------------------------------------------------------------------
# Tests: Redaction Config
# ---------------------------------------------------------------------------

class TestRedactionConfig:
    """Test per-collection profile management."""

    def test_default_profile(self):
        from services.privacy.redaction_config import (
            get_redaction_profile,
            RedactionStyle,
            clear_profile_cache,
        )
        clear_profile_cache()
        profile = get_redaction_profile("__test_nonexistent__")
        assert profile.redaction_style == RedactionStyle.ENTITY_TYPE
        assert profile.minimum_score_threshold == 0.4
        assert profile.strict_mode is True

    def test_allow_list_filtering(self):
        """Allow-listed strings should not be redacted."""
        from services.privacy.redaction_engine import redaction_engine
        from services.privacy.redaction_config import (
            save_redaction_profile,
            clear_profile_cache,
        )
        clear_profile_cache()
        save_redaction_profile("__test_allow__", {
            "allow_list": ["Fidelity"],
        })

        result = redaction_engine.redact_text(
            "Fidelity is a great company.",
            collection_id="__test_allow__",
        )
        # "Fidelity" should NOT be redacted since it's allow-listed
        assert "Fidelity" in result.redacted_text
        clear_profile_cache()


# ---------------------------------------------------------------------------
# Tests: Redaction Log
# ---------------------------------------------------------------------------

class TestRedactionLog:
    """Test audit log persistence."""

    def test_log_and_retrieve(self, tmp_path):
        from services.privacy.redaction_log import RedactionLog
        from services.privacy.redaction_engine import RedactionDetail

        log = RedactionLog(db_path=tmp_path / "test_redaction.db")
        session_id = "test-session-001"

        details = [
            RedactionDetail(
                entity_type="PERSON",
                start=0,
                end=10,
                score=0.95,
                original_text="John Smith",
                replacement="[PERSON]",
            ),
            RedactionDetail(
                entity_type="US_SSN",
                start=20,
                end=31,
                score=0.99,
                original_text="219-09-9999",
                replacement="[US_SSN]",
            ),
        ]

        log.log_redactions(
            details=details,
            session_id=session_id,
            collection_id="test-collection",
            tool_name="search_collection",
        )

        events = log.get_recent(session_id=session_id)
        assert len(events) == 2
        entity_types = {e["entity_type"] for e in events}
        assert "PERSON" in entity_types
        assert "US_SSN" in entity_types

    def test_session_summary(self, tmp_path):
        from services.privacy.redaction_log import RedactionLog
        from services.privacy.redaction_engine import RedactionDetail

        log = RedactionLog(db_path=tmp_path / "test_redaction.db")
        session_id = "test-session-002"

        details = [
            RedactionDetail("PERSON", 0, 5, 0.9, "Alice", "[PERSON]"),
            RedactionDetail("PERSON", 10, 13, 0.85, "Bob", "[PERSON]"),
            RedactionDetail("EMAIL_ADDRESS", 20, 40, 0.99, "a@b.com", "[EMAIL_ADDRESS]"),
        ]

        log.log_redactions(details, session_id, tool_name="query_table")

        summary = log.get_session_summary(session_id)
        assert summary["total_redactions"] == 3
        assert summary["by_entity_type"]["PERSON"] == 2
        assert summary["by_entity_type"]["EMAIL_ADDRESS"] == 1


# ---------------------------------------------------------------------------
# Tests: Middleware (dict walker)
# ---------------------------------------------------------------------------

class TestRedactionMiddleware:
    """Test the dict-walking middleware."""

    def test_walks_nested_response(self):
        """PII in nested dicts/lists should be redacted."""
        from services.privacy.redaction_middleware import _walk_and_redact

        response = {
            "collection_id": "default",  # should be skipped
            "results": [
                {
                    "excerpt": "Contact John Smith at john.smith@example.com",
                    "document_id": "doc-123",  # should be skipped
                }
            ],
        }

        details = []
        _walk_and_redact(response, None, details)

        excerpt = response["results"][0]["excerpt"]
        assert "John Smith" not in excerpt
        assert "john.smith@example.com" not in excerpt
        # Structural keys preserved
        assert response["collection_id"] == "default"
        assert response["results"][0]["document_id"] == "doc-123"

    def test_skips_structural_keys(self):
        """Keys in _SKIP_KEYS should never be redacted."""
        from services.privacy.redaction_middleware import _walk_and_redact

        response = {
            "collection_id": "john-smith-portfolio",
            "sql": "SELECT * FROM table WHERE name = 'John Smith'",
            "table_name": "csv_data_abc123",
        }

        details = []
        _walk_and_redact(response, None, details)

        # These should be untouched even though they contain name-like strings
        assert response["collection_id"] == "john-smith-portfolio"
        assert response["table_name"] == "csv_data_abc123"

    def test_numeric_values_untouched(self):
        """Non-string values should pass through unchanged."""
        from services.privacy.redaction_middleware import _walk_and_redact

        response = {
            "market_value": 125000.50,
            "row_count": 42,
            "truncated": False,
            "rows": [[1, 2.5, None, True]],
        }

        details = []
        _walk_and_redact(response, None, details)

        assert response["market_value"] == 125000.50
        assert response["row_count"] == 42
        assert response["truncated"] is False
        assert response["rows"] == [[1, 2.5, None, True]]


# ---------------------------------------------------------------------------
# Tests: End-to-end MCP response redaction
# ---------------------------------------------------------------------------

class TestEndToEndRedaction:
    """Full pipeline: realistic MCP response goes in, no PII comes out."""

    def test_mcp_response_pii_removed(self, monkeypatch):
        """Simulate a full MCP tool response and verify all PII is stripped."""
        import copy
        from config import settings
        monkeypatch.setattr(settings, "enable_pii_redaction", True)

        from services.privacy.redaction_middleware import redact_mcp_response

        response = copy.deepcopy(SAMPLE_MCP_RESPONSE)
        redacted = redact_mcp_response(response, "search_collection")

        # Flatten all string values from the response
        all_text = _extract_all_strings(redacted)

        # No PII should survive
        assert "John Smith" not in all_text
        assert "219-09-9999" not in all_text
        assert "john.smith@example.com" not in all_text
        assert "(555) 123-4567" not in all_text
        assert "12345678" not in all_text  # account number

        # Structural fields preserved
        assert redacted["collection_id"] == "default"
        assert len(redacted["results"]) == 2

    def test_redaction_disabled_passthrough(self, monkeypatch):
        """When enable_pii_redaction=False, data passes through unchanged."""
        import copy
        from config import settings
        monkeypatch.setattr(settings, "enable_pii_redaction", False)

        from services.privacy.redaction_middleware import redact_mcp_response

        response = copy.deepcopy(SAMPLE_MCP_RESPONSE)
        redacted = redact_mcp_response(response, "search_collection")

        # Everything should be unchanged
        all_text = _extract_all_strings(redacted)
        assert "John Smith" in all_text
        assert "john.smith@example.com" in all_text


def _extract_all_strings(obj, collected=None):
    """Recursively extract all string values from a nested dict/list."""
    if collected is None:
        collected = []
    if isinstance(obj, str):
        collected.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            _extract_all_strings(v, collected)
    elif isinstance(obj, list):
        for item in obj:
            _extract_all_strings(item, collected)
    return " ".join(collected)


# ---------------------------------------------------------------------------
# Layer 2: False positive prevention tests
# ---------------------------------------------------------------------------


class TestFalsePositivePrevention:
    """Verify that common financial terms are NOT redacted."""

    def setup_method(self):
        from services.privacy.redaction_config import clear_profile_cache
        clear_profile_cache()

    def test_max_drawdown_not_redacted(self):
        from services.privacy.redaction_engine import redaction_engine
        result = redaction_engine.redact_text("Max Drawdown (%)")
        assert result.redacted_text == "Max Drawdown (%)"

    def test_annual_monthly_not_redacted(self):
        from services.privacy.redaction_engine import redaction_engine
        for term in ["Annual Summary", "Monthly Performance", "Quarterly Returns"]:
            result = redaction_engine.redact_text(term)
            assert result.redacted_text == term, f"'{term}' was incorrectly redacted to '{result.redacted_text}'"

    def test_ticker_mub_not_redacted(self):
        from services.privacy.redaction_engine import redaction_engine
        result = redaction_engine.redact_text("MUB")
        assert result.redacted_text == "MUB"

    def test_fund_family_names_not_redacted(self):
        from services.privacy.redaction_engine import redaction_engine
        for name in ["Vanguard", "Fidelity", "PIMCO", "BlackRock", "Schwab", "Invesco"]:
            result = redaction_engine.redact_text(name)
            assert result.redacted_text == name, f"'{name}' was incorrectly redacted"

    def test_decimal_not_flagged_as_phone(self):
        from services.privacy.redaction_engine import redaction_engine
        decimals = [
            "0.08245803532856916",
            "-0.03149558105783117",
            "0.11153474",
            "1591.20",
        ]
        for d in decimals:
            result = redaction_engine.redact_text(d)
            assert "[PHONE_NUMBER]" not in result.redacted_text, (
                f"Decimal '{d}' was flagged as PHONE_NUMBER"
            )

    def test_greek_letters_not_redacted(self):
        from services.privacy.redaction_engine import redaction_engine
        result = redaction_engine.redact_text("Sharpe Ratio: 0.57, Alpha: 8.2%")
        assert "Sharpe" in result.redacted_text
        assert "Alpha" in result.redacted_text

    def test_real_pii_still_caught(self):
        """Ensure the allow-list doesn't break real PII detection."""
        from services.privacy.redaction_engine import redaction_engine
        result = redaction_engine.redact_text("Client: John Smith, SSN: 219-09-9999")
        assert "John Smith" not in result.redacted_text
        assert "219-09-9999" not in result.redacted_text


# ---------------------------------------------------------------------------
# Layer 1: source_path stripping and _STRIP_KEYS tests
# ---------------------------------------------------------------------------


class TestSourcePathStripping:
    """Verify that source_path is removed from MCP output."""

    def test_strip_keys_removes_source_path(self):
        from services.privacy.redaction_middleware import _walk_and_redact

        response = {
            "collection_id": "abc123",
            "filename": "test.csv",
            "source_path": "C:\\Users\\John Smith\\Downloads\\client_data.xlsx",
            "documents": [
                {
                    "filename": "doc.csv",
                    "source_path": "/home/user/secret/path.csv",
                }
            ],
        }

        details = []
        _walk_and_redact(response, None, details)

        assert "source_path" not in response
        assert "source_path" not in response["documents"][0]
        # Other fields preserved
        assert response["collection_id"] == "abc123"
        assert response["documents"][0]["filename"] == "doc.csv"
