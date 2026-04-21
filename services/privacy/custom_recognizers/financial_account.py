"""Custom Presidio recognizer for brokerage/financial account numbers.

Brokerage account numbers come in several formats:
  - Pure digit:        123456789012  (8-12 digits)
  - Alphanumeric:      5UP466789  (letters + digits, common in NetX360/Pershing)
  - Dashed:            123-45678901
  - With label prefix: Account: 5UP466789  /  Acct# 12345678
"""

from __future__ import annotations

from presidio_analyzer import Pattern, PatternRecognizer


class FinancialAccountRecognizer(PatternRecognizer):
    """Detects financial account numbers using context + pattern."""

    CONTEXT_WORDS = [
        "account",
        "acct",
        "account number",
        "account #",
        "account no",
        "brokerage",
        "custodian",
        "portfolio",
        "client id",
        "client number",
        "rep code",
        "advisor",
        "beneficiary",
        "ira",
        "roth",
        "401k",
        "pershing",
        "fidelity",
        "schwab",
        "vanguard",
        "td ameritrade",
        "merrill",
        "morgan stanley",
        "raymond james",
        "edward jones",
        "lpl",
        "netx360",
        "netxinvestor",
    ]

    PATTERNS = [
        # Pure digit account numbers (8-12 chars) — low base score, context boosts
        Pattern(
            "account_number_8_12",
            r"\b\d{8,12}\b",
            0.3,
        ),
        # Alphanumeric account numbers — two sub-patterns:
        #   (a) Letters then digits: "XYZ12345678"
        #   (b) Digit(s) then letters then more digits: "5UP466789" (NetX360/Pershing)
        # Base score is higher than pure-digit because mixed strings are less
        # common outside account contexts.
        Pattern(
            "account_alpha_then_digits",
            r"\b[A-Z]{1,4}\d{4,10}\b",
            0.45,
        ),
        Pattern(
            "account_digit_alpha_digit",
            r"\b\d{1,4}[A-Z]{1,4}\d{3,8}\b",
            0.45,
        ),
        # Explicit label prefix — high confidence regardless of number format
        # Handles both digit-only and alphanumeric values after the label.
        Pattern(
            "account_with_prefix",
            r"(?i)(?:acct?\.?\s*#?\s*:?\s*|account\s*(?:number|#|no\.?)?\s*:?\s*)[A-Z0-9]{4,12}",
            0.85,
        ),
        # Dashed format — e.g. "123-45678901"
        Pattern(
            "account_with_dashes",
            r"\b\d{3,4}[-]\d{4,8}\b",
            0.4,
        ),
    ]

    def __init__(self) -> None:
        super().__init__(
            supported_entity="FINANCIAL_ACCOUNT",
            patterns=self.PATTERNS,
            context=self.CONTEXT_WORDS,
            supported_language="en",
            name="FinancialAccountRecognizer",
        )


class RoutingNumberRecognizer(PatternRecognizer):
    """Detects 9-digit ABA routing numbers.

    Distinguished from SSNs by context clues and the ABA checksum.
    """

    CONTEXT_WORDS = [
        "routing",
        "aba",
        "routing number",
        "transit",
        "wire",
        "bank",
        "transfer",
    ]

    PATTERNS = [
        Pattern(
            "routing_9_digit",
            r"\b\d{9}\b",
            0.2,
        ),
        Pattern(
            "routing_with_label",
            r"(?i)(?:routing|aba|transit)\s*(?:number|#|no\.?)?\s*:?\s*\d{9}\b",
            0.85,
        ),
    ]

    def __init__(self) -> None:
        super().__init__(
            supported_entity="ROUTING_NUMBER",
            patterns=self.PATTERNS,
            context=self.CONTEXT_WORDS,
            supported_language="en",
            name="RoutingNumberRecognizer",
        )
