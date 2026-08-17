"""Core Presidio wrapper -- analyze + anonymize text for PII."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

try:
    from presidio_analyzer import AnalyzerEngine, RecognizerResult
    from presidio_anonymizer import AnonymizerEngine
    from presidio_anonymizer.entities import OperatorConfig
    _HAS_PRESIDIO = True
except ImportError:
    _HAS_PRESIDIO = False
    logger.warning(
        "presidio-analyzer / presidio-anonymizer not installed. "
        "PII redaction will be disabled. Install with: "
        "pip install presidio-analyzer presidio-anonymizer spacy && "
        "python -m spacy download en_core_web_lg"
    )

# Pseudonym pools for consistent_pseudonym mode (deterministic by hash)
_PSEUDONYM_FIRST = [
    "Alex", "Jordan", "Taylor", "Morgan", "Casey", "Riley", "Quinn",
    "Avery", "Drew", "Blake", "Sage", "Rowan", "Finley", "Harper",
]
_PSEUDONYM_LAST = [
    "Smith", "Chen", "Patel", "Johnson", "Kim", "Williams", "Brown",
    "Garcia", "Miller", "Davis", "Lee", "Wilson", "Moore", "Taylor",
]


def _looks_like_decimal(text: str, start: int, end: int) -> bool:
    """True if the span at [start:end) is part of a decimal number.

    Presidio's phone recognizer matches long digit runs, so a yield of
    ``0.08245803`` or a price of ``1234.5678`` reads as a phone number. Three
    shapes have to be caught, and only the first is visible from inside the
    span:

    - ``617.555.0142``  -- the point is within the span
    - ``0.08245803``    -- Presidio returns "08245803"; the point is at start-1
    - ``08245803.5``    -- the point is at end

    A bare point on either side is not enough on its own: "Call 617-555-0142."
    ends a sentence and is a real phone number. Require a digit on the far
    side of the point, which a sentence-ending period never has.
    """
    if "." in text[start:end]:
        return True
    before = text[:start]
    if before.endswith(".") and len(before) >= 2 and before[-2].isdigit():
        return True
    after = text[end:]
    if after.startswith(".") and len(after) >= 2 and after[1].isdigit():
        return True
    return False


def _stable_index(value: str, modulus: int) -> int:
    """Deterministic index into a pseudonym pool.

    Python's builtin ``hash()`` is salted per process (PYTHONHASHSEED), so
    using it here meant "Robert Henderson" mapped to a different pseudonym
    after every restart — the *consistent* in ``consistent_pseudonym`` only
    held within a single run. SHA-256 makes the mapping stable for the life
    of the deployment, which is what an advisor reading two briefs a week
    apart (and a CCO auditing them) actually needs. Same approach as
    ``services.privacy.column_sanitizer._short_hash``.
    """
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % modulus


@dataclass
class RedactionDetail:
    """One redacted entity."""
    entity_type: str
    start: int
    end: int
    score: float
    original_text: str
    replacement: str


@dataclass
class RedactionResult:
    """Result of a redaction pass."""
    redacted_text: str
    details: list[RedactionDetail] = field(default_factory=list)

    @property
    def had_pii(self) -> bool:
        return len(self.details) > 0


class _RedactionEngine:
    """Singleton wrapper around Presidio analyzer + anonymizer."""

    def __init__(self) -> None:
        self._analyzer: Any = None
        self._anonymizer: Any = None
        self._custom_recognizers_registered = False
        self._available = _HAS_PRESIDIO

    @property
    def available(self) -> bool:
        return self._available

    def _ensure_initialized(self) -> None:
        if not self._available:
            return
        if self._analyzer is not None:
            return

        logger.info("Initializing Presidio redaction engine...")
        self._analyzer = AnalyzerEngine()
        self._anonymizer = AnonymizerEngine()

        if not self._custom_recognizers_registered:
            self._register_custom_recognizers()
            self._custom_recognizers_registered = True

        logger.info("Presidio redaction engine ready.")

    def _register_custom_recognizers(self) -> None:
        from services.privacy.custom_recognizers.financial_account import (
            FinancialAccountRecognizer,
            RoutingNumberRecognizer,
        )
        from services.privacy.custom_recognizers.cusip_in_context import (
            CUSIPInContextRecognizer,
        )

        assert self._analyzer is not None
        registry = self._analyzer.registry
        registry.add_recognizer(FinancialAccountRecognizer())
        registry.add_recognizer(RoutingNumberRecognizer())
        registry.add_recognizer(CUSIPInContextRecognizer())
        logger.info("Registered custom financial PII recognizers.")

    def _build_operator_config(
        self, profile: Any, entity_type: str
    ) -> Any:
        from services.privacy.redaction_config import RedactionStyle
        style = profile.style_for_entity(entity_type)

        if style == RedactionStyle.REDACTED:
            return OperatorConfig("replace", {"new_value": "[REDACTED]"})
        elif style == RedactionStyle.ENTITY_TYPE:
            return OperatorConfig("replace", {"new_value": f"[{entity_type}]"})
        elif style == RedactionStyle.PARTIAL_MASK:
            return OperatorConfig("mask", {
                "type": "mask",
                "masking_char": "*",
                "chars_to_mask": 99,
                "from_end": False,
            })
        elif style == RedactionStyle.CONSISTENT_PSEUDONYM:
            # Handled in post-processing -- use placeholder for now
            return OperatorConfig("replace", {"new_value": f"[{entity_type}]"})
        elif style == RedactionStyle.SYNTHETIC_PLACEHOLDER:
            return OperatorConfig("replace", {"new_value": f"<synthetic-{entity_type.lower()}>"})
        else:
            return OperatorConfig("replace", {"new_value": "[REDACTED]"})

    def redact_text(
        self,
        text: str,
        collection_id: str | None = None,
        exclude_entity_types: set[str] | frozenset[str] | None = None,
    ) -> RedactionResult:
        """Analyze and anonymize a single string.

        Returns the redacted text and a list of every entity found with its
        original value, replacement, and confidence score.

        ``exclude_entity_types`` is a per-call exclusion set — entities of
        these types are dropped before anonymization. Used by output-side
        redaction on advisor-drafted documents (Note of Record, follow-up
        email) to keep DATE_TIME from being scrubbed; the prompt injects
        today's date and the model invents action-item due dates, neither
        of which is sensitive PII.
        """
        if not text or not text.strip():
            return RedactionResult(redacted_text=text)

        if not self._available:
            return RedactionResult(redacted_text=text)

        self._ensure_initialized()
        assert self._analyzer is not None
        assert self._anonymizer is not None

        from services.privacy.redaction_config import (
            RedactionProfile,
            RedactionStyle,
            get_redaction_profile,
        )

        profile = get_redaction_profile(collection_id)

        # Build allow list for the analyzer
        analyzer_kwargs: dict[str, Any] = {
            "text": text,
            "language": "en",
            "score_threshold": profile.minimum_score_threshold,
        }
        if profile.entity_types_enabled:
            analyzer_kwargs["entities"] = profile.entity_types_enabled

        # Run analysis
        results: list = self._analyzer.analyze(**analyzer_kwargs)

        if not results:
            return RedactionResult(redacted_text=text)

        # Filter by per-entity-type score thresholds
        if profile.entity_score_thresholds:
            results = [
                r for r in results
                if r.score >= profile.entity_score_thresholds.get(
                    r.entity_type, profile.minimum_score_threshold
                )
            ]

        # Drop PHONE_NUMBER detections that are really decimal numbers.
        # Presidio flags long digit runs like 0.08245803 as phone numbers, and
        # a financial export is full of them.
        #
        # Checking only *inside* the span is not enough: on "0.08245803"
        # Presidio returns the span "08245803", so the decimal point sits one
        # character to the left and an inside-only test misses it — which is
        # how "Yield was 0.[PHONE_NUMBER]" got through. Look at the characters
        # bracketing the span as well.
        results = [
            r for r in results
            if not (
                r.entity_type == "PHONE_NUMBER"
                and _looks_like_decimal(text, r.start, r.end)
            )
        ]

        # Per-call exclusion (for output-side advisor-drafted documents).
        if exclude_entity_types:
            excl = {e.upper() for e in exclude_entity_types}
            results = [r for r in results if r.entity_type.upper() not in excl]

        # Filter by allow list
        if profile.allow_list:
            allow_set = {a.lower() for a in profile.allow_list}
            results = [
                r for r in results
                if text[r.start:r.end].lower() not in allow_set
            ]

        if not results:
            return RedactionResult(redacted_text=text)

        # Build per-entity-type operator configs
        entity_types_in_results = {r.entity_type for r in results}
        operators = {
            et: self._build_operator_config(profile, et)
            for et in entity_types_in_results
        }

        # Capture originals before anonymization (sorted by position)
        originals = [
            (r.entity_type, r.start, r.end, r.score, text[r.start:r.end])
            for r in sorted(results, key=lambda r: r.start)
        ]

        # Run anonymization
        anonymized = self._anonymizer.anonymize(
            text=text,
            analyzer_results=results,
            operators=operators,
        )

        # Build detail records -- anonymized.items has the replacements
        # with their new positions, matched back to originals by entity type
        details: list[RedactionDetail] = []
        anon_items = sorted(anonymized.items, key=lambda i: i.start) if anonymized.items else []

        # Match anonymized items back to originals by order
        for item in anon_items:
            # Find the corresponding original by entity type + position order
            original_text = ""
            score = 0.0
            for orig_et, orig_s, orig_e, orig_score, orig_txt in originals:
                if orig_et == item.entity_type:
                    original_text = orig_txt
                    score = orig_score
                    originals.remove((orig_et, orig_s, orig_e, orig_score, orig_txt))
                    break

            details.append(RedactionDetail(
                entity_type=item.entity_type,
                start=item.start,
                end=item.end,
                score=score,
                original_text=original_text,
                replacement=item.text,
            ))

        # Apply consistent pseudonyms in post-processing if needed
        redacted = anonymized.text
        if profile.redaction_style == RedactionStyle.CONSISTENT_PSEUDONYM:
            redacted = self._apply_pseudonyms(redacted, details)

        return RedactionResult(redacted_text=redacted, details=details)

    def redact_structured(
        self,
        rows: list[dict[str, Any]],
        collection_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], list[RedactionDetail]]:
        """Redact every string-valued cell in a list of row dicts.

        Returns (redacted_rows, all_details).
        """
        all_details: list[RedactionDetail] = []
        redacted_rows: list[dict[str, Any]] = []

        for row in rows:
            new_row: dict[str, Any] = {}
            for key, value in row.items():
                if isinstance(value, str):
                    result = self.redact_text(value, collection_id)
                    new_row[key] = result.redacted_text
                    all_details.extend(result.details)
                else:
                    new_row[key] = value
            redacted_rows.append(new_row)

        return redacted_rows, all_details

    def _apply_pseudonyms(
        self, text: str, details: list[RedactionDetail]
    ) -> str:
        """Replace [PERSON] placeholders with consistent pseudonyms."""
        pseudonym_map: dict[str, str] = {}
        for d in details:
            if d.entity_type == "PERSON" and d.original_text:
                if d.original_text not in pseudonym_map:
                    idx = _stable_index(d.original_text, len(_PSEUDONYM_FIRST))
                    idx2 = _stable_index(
                        d.original_text + "_last", len(_PSEUDONYM_LAST)
                    )
                    pseudonym_map[d.original_text] = (
                        f"{_PSEUDONYM_FIRST[idx]} {_PSEUDONYM_LAST[idx2]}"
                    )
                text = text.replace("[PERSON]", pseudonym_map[d.original_text], 1)
                d.replacement = pseudonym_map[d.original_text]
        return text


# Module-level singleton
redaction_engine = _RedactionEngine()
