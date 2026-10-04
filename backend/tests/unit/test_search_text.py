"""Words of the search: safe full-text queries, snippets, fusion of the rankings."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from vfe_vision.domain.search_text import (
    RRF_K,
    Snippet,
    fold,
    fts_query,
    highlight_ranges,
    query_terms,
    rrf,
    snippet,
)
from vfe_vision.domain.timecode import timecode_at

TEXTS = [
    "Une abeille charpentière butine la lavande, l'été, près du lac d'Annecy.",
    'Il dit : « on met le riz » (puis "rit") — NEAR AND OR NOT * ^ : ; -- /* */',
    "Coucher de soleil sur la plage, vagues calmes et ciel orangé.",
]


@pytest.fixture(scope="module")
def fts() -> Iterator[sqlite3.Connection]:
    """The index as migration 0013 creates it (same tokenizer), in memory."""
    con = sqlite3.connect(":memory:")
    con.execute("CREATE VIRTUAL TABLE t USING fts5(text, tokenize='unicode61 remove_diacritics 2')")
    con.executemany("INSERT INTO t(text) VALUES (?)", [(text,) for text in TEXTS])
    yield con
    con.close()


def _found(con: sqlite3.Connection, query: str) -> list[int]:
    expression = fts_query(query)
    if expression is None:
        return []
    return [row[0] for row in con.execute("SELECT rowid FROM t WHERE t MATCH ?", (expression,))]


def test_fold_is_the_index_folding() -> None:
    assert fold("Été À L'ÎLE Œuvre") == "ete a l'ile œuvre"
    assert fold("ﬁlm") == "film"  # compatibility forms


def test_terms_leave_common_words_and_keep_phrases() -> None:
    parsed = query_terms('le chat "coucher de soleil" sur la plage')
    assert parsed.terms == ("chat", "plage")
    assert parsed.phrases == (("coucher", "de", "soleil"),)
    assert query_terms("le la").terms == ("le", "la")  # nothing else: still searched
    assert query_terms("  ").empty
    assert query_terms('"soleil"').terms == ("soleil",)  # a one-word phrase is a word


def test_fts_query_is_quoted_prefix_terms() -> None:
    assert fts_query("Été à Annecy") == '"ete" OR "annecy"*'  # 3 letters: whole word
    assert fts_query('"coucher de soleil" plage') == '"coucher de soleil" OR "plage"*'
    assert fts_query("x") == '"x"'  # one letter: the whole word only
    assert fts_query("bee on a flower") == '"bee" OR "flower"*'  # « bee* » would find « been »
    assert fts_query("!!! ?") is None


def test_the_index_finds_words_with_or_without_accents(fts: sqlite3.Connection) -> None:
    assert _found(fts, "ete") == [1]
    assert _found(fts, "CHARPENTIERE") == [1]
    assert _found(fts, "lava") == [1]  # a prefix (4 letters or more)
    assert _found(fts, '"coucher de soleil"') == [3]
    assert _found(fts, '"soleil de coucher"') == []  # a phrase keeps its order
    assert _found(fts, "NEAR AND OR") == [2]  # FTS keywords are plain words here
    assert sorted(_found(fts, "riz plage")) == [2, 3]  # any of the words


@settings(max_examples=300, deadline=None)
@given(st.text(max_size=80))
def test_no_query_is_a_syntax_error(fts: sqlite3.Connection, text: str) -> None:
    _found(fts, text)  # sqlite3.OperationalError would fail the test


@settings(max_examples=200, deadline=None)
@given(st.text(max_size=60), st.text(min_size=1, max_size=300))
def test_snippets_are_plain_and_their_ranges_mark_matched_words(query: str, text: str) -> None:
    parsed = query_terms(query)
    cut = snippet(text, parsed, size=80)
    assert "\n" not in cut.text
    assert len(cut.text) <= 82  # the window, and an ellipsis on each side
    for start, end in cut.highlights:
        assert 0 <= start < end <= len(cut.text)
        word = fold(cut.text[start:end])
        assert any(word.startswith(term) for term in parsed.terms) or parsed.phrases


def test_snippet_centres_on_the_matches() -> None:
    text = "Début sans intérêt. " * 20 + "Le héron se pose sur le lac. " + "Fin. " * 30
    cut = snippet(text, query_terms("heron lac"), size=80)
    assert cut.text.startswith("…")
    assert cut.text.endswith("…")
    marked = [cut.text[s:e] for s, e in cut.highlights]
    assert marked == ["héron", "lac"]
    short = snippet("Un chat.\nUn chien.", query_terms("chien"))
    assert short == Snippet("Un chat. · Un chien.", ((14, 19),))
    by_meaning = snippet("x " * 200, query_terms("rien"), size=40)
    assert by_meaning.text.startswith("x x")  # nothing matched: the beginning
    assert by_meaning.highlights == ()


def test_highlights_follow_phrases_and_short_words() -> None:
    text = "Au coucher de soleil, le bateau X rentre ; le xylophone joue."
    parsed = query_terms('"coucher de soleil" le x')
    assert parsed.terms == ("x",)  # « le » goes: other words are asked
    marked = [text[s:e] for s, e in highlight_ranges(text, parsed)]
    assert marked == ["coucher", "de", "soleil", "X"]  # « x » alone, not « xylophone »


def test_rrf_sums_reciprocal_ranks() -> None:
    fused = rrf({"words": [10, 20, 30], "meaning": [20, 40]})
    assert [item for item, _, _ in fused] == [20, 10, 40, 30]
    scores = {item: score for item, score, _ in fused}
    assert scores[20] == pytest.approx(1 / (RRF_K + 2) + 1 / (RRF_K + 1))
    assert scores[10] == pytest.approx(1 / (RRF_K + 1))
    assert {item: found for item, _, found in fused}[20] == ("words", "meaning")
    assert rrf({}) == []
    # A tie keeps the first list's order.
    assert [item for item, _, _ in rrf({"a": [1], "b": [2]})] == [1, 2]


@given(st.dictionaries(st.sampled_from(["a", "b", "c"]), st.lists(st.integers(0, 50), unique=True)))
def test_rrf_scores_are_ordered_and_complete(rankings: dict[str, list[int]]) -> None:
    fused = rrf(rankings)
    assert {item for item, _, _ in fused} == {i for items in rankings.values() for i in items}
    scores = [score for _, score, _ in fused]
    assert scores == sorted(scores, reverse=True)


def test_source_timecodes_for_resolve() -> None:
    assert timecode_at("10:00:00:00", 12.5, 25.0) == "10:00:12:12"
    assert timecode_at(None, 61.0, 25.0) == "00:01:01:00"  # a file without its own timecode
    assert timecode_at("01:00:00:00", 1.0, 23.976) == "01:00:00:23"  # frame 23 at 23.976 i/s
    assert timecode_at("23:59:59:24", 0.04, 25.0) == "00:00:00:00"  # wraps at midnight
    assert timecode_at("01:00:00;00", 1.0, 29.97) == "01:00:00;29"  # drop-frame
    assert timecode_at("00:00:59;29", 1 / 29.97, 29.97) == "00:01:00;02"  # 00;00 and 00;01 skipped
    assert timecode_at("bad", 1.0, 25.0) is None
    assert timecode_at("00:00:00:00", 1.0, None) is None
