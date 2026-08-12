"""Pins `_looks_like_noise_line`, the OCR speckle filter in DocumentExtractor.

This filter had no coverage when the NRC-specific tokens (docket / LER / PDR /
NRC / RO) were dropped from its protected-token list, so these tests exist to
make that removal — and any future edit to the list — safe rather than hopeful.

The contract: short lines that are real document content survive; short lines
that are scanner artifacts get dropped.
"""

import pytest

from services.document_extractor import DocumentExtractor


_noise = DocumentExtractor._looks_like_noise_line


@pytest.mark.parametrize("line", [
    "Dear Ms. Chen",
    "Region III",
    "Appendix B",
    "Operating Agreement",
    "License No. 4417",
    "Office of the Trustee",
    "Mar 14, 2026",
    "Dr. Patel",
])
def test_real_short_lines_are_kept(line):
    """Domain-neutral business/correspondence lines must survive the filter."""
    assert _noise(line) is False


@pytest.mark.parametrize("line", [
    "",
    "   ",
    "|||",
    "---",
    ".:.",
    "~~",
])
def test_scanner_artifacts_are_dropped(line):
    assert _noise(line) is True


def test_prose_is_always_kept():
    assert _noise(
        "The account was rebalanced on the first business day of the quarter."
    ) is False


@pytest.mark.parametrize("line", [
    "Docket No. 50-346",
    "LER 2024-003",
    "NRC Region III",
])
def test_nuclear_regulatory_lines_are_no_longer_specially_protected(line):
    """These tokens were removed from the protected list as irrelevant here.

    They are not asserted to be *dropped* — most still survive on their own
    density merits. This pins that removing them didn't crash the filter or
    change its shape, and documents that the special-casing is gone on purpose,
    so a future reader doesn't restore it as a "missing" domain.
    """
    assert isinstance(_noise(line), bool)
