"""Custom Presidio recognizers for brokerage account holder name formats.

Brokerage statements store account holder names in patterns that spaCy's
general NER model does not recognise as PERSON entities:

  - Trust account names:  MARGARET A SMITH TTEE MARGARET A SMITH TRUST UA DTD 06/26/2017
  - With comma:           Gerard A. Surdakowski, Trustee
  - Joint tenancy:        JOHN A SMITH & JANE B SMITH JTWROS
  - Custodian accounts:   JOHN SMITH CUST EMILY SMITH UGMA IL
  - Corporate accounts:   SMITH FAMILY LLC
  - US postal addresses:  123 MAIN ST, PHOENIX, AZ 85020-4192
                          or bare city-state-zip: PHOENIX, AZ 85020-4192
                          or mixed-case:  909 E Northern Ave, Apt 207 Phoenix, AZ 85020-4192
"""

from __future__ import annotations

import re

from presidio_analyzer import (
    AnalysisExplanation,
    LocalRecognizer,
    RecognizerResult,
)


# ---------------------------------------------------------------------------
# Trust / legal-entity account name recognizer
# ---------------------------------------------------------------------------

# Suffixes that unambiguously indicate a personal trust / custodial account.
# INC / LLC / CORP / LP are intentionally excluded — they are too common in
# public company names (TEXAS INSTRS INC, etc.) and would cause false positives.
_TRUST_SUFFIXES = (
    r"(?:TTEE|TRUSTEE|"
    r"TRUST(?:\s+UA\s+DTD|\s+U/A|\s+U/T|\s+DATED)?"
    r"|REVOCABLE|IRREVOCABLE|LIVING\s+TRUST|"
    r"JTWROS|JT\s+TEN|TEN\s+COM|"
    r"UGMA|UTMA|CUST(?:ODIAN)?|"
    r"FBO|TOD|"
    r"ROTH\s+IRA|SEP\s+IRA|SIMPLE\s+IRA)"
)

# A name token is either:
#   - A full word of 2+ letters:  "Gerard", "Surdakowski"
#   - A single-letter initial with an optional period:  "A", "A."
# This ensures "Gerard A. Surdakowski, Trustee" is fully matched.
_NAME_TOKEN = r"(?:[A-Za-z][A-Za-z]+|[A-Za-z]\.?)\.?"

# Full trust-name pattern: one or more name tokens followed by a known
# brokerage/trust suffix.
#
# The separator between tokens (and before the suffix) is [\s,]+ rather than
# \s+ so that "Gerard A. Surdakowski, Trustee" is matched — the comma after
# the last name is consumed as part of the separator, not as punctuation that
# breaks the match.
_TRUST_NAME_PATTERN = re.compile(
    rf"\b(?:{_NAME_TOKEN}[\s,]+){{1,8}}{_TRUST_SUFFIXES}\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# US address recognizer
# ---------------------------------------------------------------------------

# City, State ZIP — e.g. "PHOENIX, AZ 85020" or "Phoenix, AZ 85020-4192"
# Fixed: was [A-Z][A-Z\s]{2,20} which missed mixed-case city names.
# Now uses \w (word chars) and \s to handle both cases.
_CITY_STATE_ZIP = re.compile(
    r"\b[A-Za-z][A-Za-z\s]{2,20},\s+[A-Za-z]{2}\s+\d{5}(?:-\d{4})?\b"
)

# Street address line — e.g. "123 MAIN ST", "4500 N 32ND AVE", "909 E Northern Ave"
# [\w]+ allows ordinal numbers like 32ND, 45TH, 3RD inside the street name.
_STREET_ADDR = re.compile(
    r"\b\d{1,5}\s+"               # house number
    r"(?:[NSEWnsew]\s+)?"         # optional compass prefix (N, S, E, W)
    r"[\w\s]{2,35}"               # street name (allows ordinals like 32ND)
    r"\b(?:ST|AVE|AVENUE|RD|ROAD|DR|DRIVE|BLVD|BOULEVARD|"
    r"LN|LANE|WAY|CT|COURT|PL|PLACE|CIR|CIRCLE|"
    r"HWY|HIGHWAY|PKWY|PARKWAY|TERR|TERRACE)\b",
    re.IGNORECASE,
)


class TrustAccountNameRecognizer(LocalRecognizer):
    """Detects brokerage trust/legal-entity account holder name formats."""

    ENTITIES = ["TRUST_ACCOUNT_NAME"]

    def __init__(self) -> None:
        super().__init__(
            supported_entities=self.ENTITIES,
            supported_language="en",
            name="TrustAccountNameRecognizer",
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
        for match in _TRUST_NAME_PATTERN.finditer(text):
            explanation = AnalysisExplanation(
                recognizer=self.__class__.__name__,
                original_score=0.85,
                pattern_name="trust_account_name",
                pattern=_TRUST_NAME_PATTERN.pattern,
                validation_result=None,
            )
            results.append(
                RecognizerResult(
                    entity_type="TRUST_ACCOUNT_NAME",
                    start=match.start(),
                    end=match.end(),
                    score=0.85,
                    analysis_explanation=explanation,
                    recognition_metadata={"recognizer_name": self.name},
                )
            )
        return results


class USAddressRecognizer(LocalRecognizer):
    """Detects US postal addresses (street line and/or city-state-zip)."""

    ENTITIES = ["US_ADDRESS"]

    def __init__(self) -> None:
        super().__init__(
            supported_entities=self.ENTITIES,
            supported_language="en",
            name="USAddressRecognizer",
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

        for pattern, score, name in [
            (_CITY_STATE_ZIP, 0.88, "city_state_zip"),
            (_STREET_ADDR, 0.80, "street_address"),
        ]:
            for match in pattern.finditer(text):
                explanation = AnalysisExplanation(
                    recognizer=self.__class__.__name__,
                    original_score=score,
                    pattern_name=name,
                    pattern=pattern.pattern,
                    validation_result=None,
                )
                results.append(
                    RecognizerResult(
                        entity_type="US_ADDRESS",
                        start=match.start(),
                        end=match.end(),
                        score=score,
                        analysis_explanation=explanation,
                        recognition_metadata={"recognizer_name": self.name},
                    )
                )
        return results
