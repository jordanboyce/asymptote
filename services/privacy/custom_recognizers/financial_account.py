"""Custom Presidio recognizer for brokerage/financial account numbers.

Brokerage account numbers are typically 8-12 digits, often preceded by
contextual keywords like "Account #", "Acct", or custodian names.
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
    ]

    PATTERNS = [
        Pattern(
            "account_number_8_12",
            r"\b\d{8,12}\b",
            0.3,
        ),
        Pattern(
            "account_with_prefix",
            r"(?i)(?:acct?\.?\s*#?\s*:?\s*|account\s*(?:number|#|no\.?)?\s*:?\s*)\d{4,12}",
            0.85,
        ),
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
