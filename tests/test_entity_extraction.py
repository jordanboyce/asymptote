"""Tests for the heuristic entity extractor (services/entity_graph.py).

The extractor is the highest-risk part of the entity-graph feature: it runs
on every chunk with no model, so false positives are its failure mode. These
tests pin the rules that keep precision high — sentence-start suspicion,
stopword heads, acronym matching, phrase boundary.
"""

import pytest

from services.entity_graph import (
    _find_acronyms,
    _find_capitalized_phrases,
    _is_sentence_start,
    corpus_filter,
    extract_entities,
    graph_boost,
    normalize_entity,
)
from collections import Counter


# ── normalize_entity ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("Alice", "alice"),
    ("  Alice.  ", "alice"),
    ("NASA", "nasa"),
    ("Parks", "park"),          # plural -s stripped
    ("Alice's", "alice"),       # possessive stripped
    ("cats", "cat"),
    ("Project Titan", "project titan"),
    ("—Em-Dash—", "em-dash"),   # punctuation stripped, non-ASCII dash kept
])
def test_normalize_entity(raw, expected):
    assert normalize_entity(raw) == expected


# ── acronyms ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("NASA announced the API", ["NASA", "API"]),
    ("lowercase rag is not an acronym", []),
    ("IT is ambiguous — skipped as acronym, kept by phrase rule only mid-sentence", ["IT"]),
    ("A v2 release is not an acronym, too short and lowercase", []),
    ("COVID-19 and X-509 are hyphenated acronyms", ["COVID-19", "X-509"]),
])
def test_find_acronyms(text, expected):
    assert _find_acronyms(text) == expected


# ── capitalized phrases ──────────────────────────────────────────────────────

def test_phrase_at_sentence_start():
    # "Alice" opens the sentence — trust it because the phrase is multi-word.
    text = "Alice Johnson led the project."
    assert "Alice Johnson" in _find_capitalized_phrases(text)


def test_single_word_at_sentence_start_dropped():
    # "The" starts the sentence and is a stopword — must not become an entity.
    text = "The committee convened on Tuesday."
    assert _find_capitalized_phrases(text) == []


def test_stopword_head_stripped_from_phrase():
    # "The Board" → keep "Board", drop "The".
    text = "The Board met yesterday in Denver."
    phrases = _find_capitalized_phrases(text)
    assert "The Board" not in phrases
    # "Denver" is sentence-internal → kept
    assert "Denver" in phrases


def test_multiword_phrase_spans():
    text = "They discussed the Titan Renewal Project during review."
    phrases = _find_capitalized_phrases(text)
    assert any("Titan Renewal Project" in p for p in phrases)


def test_run_breaks_on_punctuation():
    text = "She visited Paris. London was next."
    phrases = _find_capitalized_phrases(text)
    # Neither phrase should absorb across the period.
    assert not any("Paris" in p and "London" in p for p in phrases)
    assert "Paris" in phrases
    # "London" is at sentence start single-word — dropped
    assert "London" not in phrases


def test_mixed_case_word_not_entity():
    text = "the system prompt is not an entity"
    assert _find_capitalized_phrases(text) == []


# ── sentence-start detection ──────────────────────────────────────────────────

@pytest.mark.parametrize("text,target,expected", [
    ("Alice went home.", "Alice", True),
    ("She left. Alice stayed.", "Alice", True),
    # quote between punctuation and word
    ('He said. "Alice agreed."', "Alice", True),
    ("They saw Alice.", "Alice", False),         # mid-sentence
])
def test_is_sentence_start(text, target, expected):
    # find the word's actual offset — hardcoded indices rot as cases change
    assert _is_sentence_start(text, text.index(target)) is expected


# ── extract_entities (end-to-end, one chunk) ─────────────────────────────────

def test_extract_entities_basic():
    ents = extract_entities(
        "Alice Johnson and Dr. Ramirez met on the Titan Renewal Project. "
        "The budget grew under NASA oversight."
    )
    assert "alice johnson" in ents
    assert "titan renewal project" in ents
    assert "nasa" in ents
    # stopwords never appear
    assert "the" not in ents


def test_extract_entities_dedupes_and_stays_stable():
    ents = extract_entities("NASA. NASA! NASA announced NASA results.")
    assert ents.count("nasa") == 1


def test_extract_entities_caps_per_chunk():
    text = " ".join(f"Person{i} Acme{i}" for i in range(40))
    ents = extract_entities(text, max_per_chunk=20)
    assert len(ents) <= 20


def test_extract_entities_min_length():
    # single letters never qualify
    ents = extract_entities("A b c are just letters.")
    assert all(len(e) >= 2 for e in ents)


def test_extract_entities_never_stopword():
    ents = extract_entities(
        "The quick brown fox Jumps over the Lazy Dog near London."
        # intentionally odd casing to force candidates
    )
    for e in ents:
        assert e not in {"the", "over", "near"}


# ── corpus_filter ────────────────────────────────────────────────────────────

def test_corpus_filter_requires_two_chunks():
    counts = Counter({"alice": 1, "titan": 5, "everywhere": 1000})
    kept = corpus_filter(counts, num_chunks=1000)
    assert "alice" not in kept          # df=1, can't bridge documents
    assert "titan" in kept
    assert "everywhere" not in kept     # df = 100% > 30% cap


def test_corpus_filter_empty_corpus():
    assert corpus_filter(Counter(), num_chunks=0) == set()


# ── graph_boost ──────────────────────────────────────────────────────────────

def test_graph_boost_empty_shared_is_zero():
    assert graph_boost([], {"alice": 1.0}, weight=0.2) == 0.0
    assert graph_boost(["unknown"], {"alice": 1.0}, weight=0.2) == 0.0


def test_graph_boost_saturates():
    rarity = {f"e{i}": 1.0 for i in range(10)}
    b_small = graph_boost(["e0"], rarity, weight=0.2)
    b_large = graph_boost(list(rarity), rarity, weight=0.2)
    assert 0 < b_small < b_large < 0.2   # grows, but never reaches weight


def test_graph_boost_rarer_entities_weigh_more():
    high_rarity = {"alice": 0.9}
    low_rarity = {"alice": 0.1}
    assert graph_boost(["alice"], high_rarity) > graph_boost(["alice"], low_rarity)
