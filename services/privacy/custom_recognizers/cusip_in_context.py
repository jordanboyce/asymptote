"""Custom Presidio recognizer for CUSIP identifiers in PII context.

A CUSIP alone (9-character alphanumeric security identifier) is NOT PII --
it's a public security identifier. However, a CUSIP appearing within close
proximity to an account holder name, account number, or other personal
identifier becomes PII-adjacent and should be redacted in that context.
"""

from __future__ import annotations

from presidio_analyzer import (
    AnalysisExplanation,
    LocalRecognizer,
    RecognizerResult,
)

import re

# CUSIP: 6 alphanumeric issuer + 2 alphanumeric issue + 1 check digit
_CUSIP_PATTERN = re.compile(r"\b[A-Z0-9]{6}[A-Z0-9]{2}[0-9]\b")

# How many characters of context window to search for PII-adjacent terms
_CONTEXT_WINDOW = 200

_PII_CONTEXT_WORDS = {
    "account",
    "acct",
    "client",
    "beneficiary",
    "holder",
    "owner",
    "ssn",
    "social security",
    "name",
    "mr.",
    "mrs.",
    "ms.",
    "dr.",
}


class CUSIPInContextRecognizer(LocalRecognizer):
    """Flags CUSIP identifiers as PII only when near account-owner context."""

    ENTITIES = ["CUSIP_IN_CONTEXT"]

    def __init__(self) -> None:
        super().__init__(
            supported_entities=self.ENTITIES,
            supported_language="en",
            name="CUSIPInContextRecognizer",
        )

    def load(self) -> None:
        pass

    def analyze(
        self,
        text: str,
        entities: list[str] | None = None,
        nlp_artifacts: dict | None = None,
    ) -> list[RecognizerResult]:
        results: list[RecognizerResult] = []

        for match in _CUSIP_PATTERN.finditer(text):
            start, end = match.start(), match.end()

            # Look at surrounding context
            ctx_start = max(0, start - _CONTEXT_WINDOW)
            ctx_end = min(len(text), end + _CONTEXT_WINDOW)
            context = text[ctx_start:ctx_end].lower()

            has_pii_context = any(word in context for word in _PII_CONTEXT_WORDS)

            if has_pii_context:
                explanation = AnalysisExplanation(
                    recognizer=self.__class__.__name__,
                    original_score=0.7,
                    pattern_name="cusip_in_pii_context",
                    pattern=str(_CUSIP_PATTERN.pattern),
                    validation_result=None,
                )
                results.append(
                    RecognizerResult(
                        entity_type="CUSIP_IN_CONTEXT",
                        start=start,
                        end=end,
                        score=0.7,
                        analysis_explanation=explanation,
                        recognition_metadata={
                            "recognizer_name": self.name,
                        },
                    )
                )

        return results
