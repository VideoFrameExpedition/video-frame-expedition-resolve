"""Words of the search (pure functions): the full-text query, the highlighted snippet
and the fusion of the two result lists.

The user types plain words, never FTS5 syntax: each word becomes a quoted prefix term (``"lav"*``
finds « lavande »), a quoted phrase stays a phrase, and nothing else reaches SQLite, so a
parenthesis, a quote or ``NEAR`` in the query can never be a syntax error. Accents and case are
folded as the FTS5 tokenizer folds them (``unicode61 remove_diacritics 2``), so a snippet
highlights exactly the words the index matched.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

RRF_K = 60  # reciprocal rank fusion (Cormack et al. 2009)
SNIPPET_CHARS = 240
MAX_TERMS = 12  # words of a query that reach the index
# Shorter words are matched whole: « bee* » found « been » all over an English commentary
# (the search by meaning still finds « eaux » for « eau »).
MIN_PREFIX = 4

_WORD = re.compile(r"\w+", re.UNICODE)
_PHRASE = re.compile(r'"([^"]+)"')
# Words too common to find anything, in the two languages of the application. They are left
# out of the full-text query only: the search by meaning reads the whole sentence.
STOPWORDS = frozenset({
    "a", "au", "aux", "avec", "ce", "ces", "cet", "cette", "d", "dans", "de", "des", "du",
    "elle", "en", "et", "il", "ils", "j", "je", "l", "la", "le", "les", "leur", "lui", "m", "ma",
    "mais", "me", "mes", "mon", "n", "ne", "nous", "on", "ou", "par", "pas", "pour", "qu", "que",
    "qui", "s", "sa", "se", "ses", "son", "sur", "t", "ta", "te", "tes", "ton", "tu", "un", "une",
    "vos", "votre", "vous", "y", "est", "sont",
    "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "into", "is", "it", "its",
    "of", "or", "that", "the", "their", "this", "to", "was", "were", "with",
})  # fmt: skip


def fold(text: str) -> str:
    """Lower case without accents (« Été » → « ete »), as the index compares words."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return unicodedata.normalize("NFC", stripped).casefold()


def words(text: str) -> list[str]:
    """The folded words of a text, in order."""
    return _WORD.findall(fold(text))


@dataclass(frozen=True, slots=True)
class QueryTerms:
    """What a query looks for in the words of a passage."""

    terms: tuple[str, ...]  # folded words, matched as prefixes (or whole when short)
    phrases: tuple[tuple[str, ...], ...]  # folded word sequences asked for between quotes

    @property
    def empty(self) -> bool:
        return not self.terms and not self.phrases


def query_terms(query: str) -> QueryTerms:
    """The words and quoted phrases of a query, common words left out unless nothing else is
    asked (« le » alone is still searched)."""
    phrases: list[tuple[str, ...]] = []
    for found in _PHRASE.findall(query):
        phrase = tuple(words(found))
        if len(phrase) > 1 and phrase not in phrases:
            phrases.append(phrase)
    rest = _PHRASE.sub(" ", query)
    loose = [w for w in words(rest) if w]
    loose += [p[0] for p in (tuple(words(f)) for f in _PHRASE.findall(query)) if len(p) == 1]
    kept = [w for w in loose if w not in STOPWORDS]
    if not kept and not phrases:
        kept = loose
    terms = tuple(dict.fromkeys(kept))[:MAX_TERMS]
    return QueryTerms(terms, tuple(phrases[:MAX_TERMS]))


def fts_query(query: str) -> str | None:
    """A safe FTS5 expression for ``query``: quoted terms joined by OR (BM25 ranks the passages
    that hold more of them first); None when the query has no word at all."""
    parsed = query_terms(query)
    if parsed.empty:
        return None
    parts = [f'"{" ".join(phrase)}"' for phrase in parsed.phrases]
    parts += [f'"{term}"*' if len(term) >= MIN_PREFIX else f'"{term}"' for term in parsed.terms]
    return " OR ".join(parts)


def _matches(word: str, parsed: QueryTerms) -> bool:
    return any(
        word.startswith(term) if len(term) >= MIN_PREFIX else word == term for term in parsed.terms
    )


def highlight_ranges(text: str, parsed: QueryTerms) -> list[tuple[int, int]]:
    """``[start, end)`` of the words of ``text`` the query matches (code point offsets)."""
    spans = [(m.start(), m.end(), fold(m.group())) for m in _WORD.finditer(text)]
    hits: set[int] = {i for i, (_, _, w) in enumerate(spans) if _matches(w, parsed)}
    for phrase in parsed.phrases:
        size = len(phrase)
        for i in range(len(spans) - size + 1):
            if tuple(w for _, _, w in spans[i : i + size]) == phrase:
                hits.update(range(i, i + size))
    return [(spans[i][0], spans[i][1]) for i in sorted(hits)]


@dataclass(frozen=True, slots=True)
class Snippet:
    text: str  # plain text: never markup
    highlights: tuple[tuple[int, int], ...]  # [start, end) in code points of ``text``


def snippet(text: str, parsed: QueryTerms, *, size: int = SNIPPET_CHARS) -> Snippet:
    """At most ``size`` characters of a passage (its lines joined by « · »), around its densest
    group of matched words, or its beginning when none matches (a passage found by meaning).
    Cut between words, with « … » where text is left out."""
    flat = " · ".join(line.strip() for line in text.splitlines() if line.strip())
    ranges = highlight_ranges(flat, parsed)
    if len(flat) <= size:
        return Snippet(flat, tuple(ranges))
    start = 0
    if ranges:

        def held(i: int) -> int:  # matched words in the window that starts at match i
            return sum(1 for s, e in ranges[i:] if e <= ranges[i][0] + size)

        best = max(range(len(ranges)), key=lambda i: (held(i), -i))
        start = max(0, ranges[best][0] - size // 4)  # a little of what comes before
        start = min(start, len(flat) - size)  # near the end: show a full window
        if start > 0:
            start = flat.rfind(" ", 0, start) + 1  # at the start of a word
    end = min(len(flat), start + size)
    if end < len(flat):
        space = flat.rfind(" ", start + size // 2, end)
        end = space if space > 0 else end
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(flat) else ""
    shift = len(prefix) - start
    kept = tuple((s + shift, e + shift) for s, e in ranges if s >= start and e <= end)
    return Snippet(prefix + flat[start:end] + suffix, kept)


def rrf(
    rankings: Mapping[str, Sequence[int]], *, k: int = RRF_K
) -> list[tuple[int, float, tuple[str, ...]]]:
    """Reciprocal rank fusion of result lists (best first): each item scores the sum of
    ``1 / (k + rank)`` over the lists holding it. Returns (item, score, lists), best first;
    ties keep the order of the first list."""
    scores: dict[int, float] = {}
    found: dict[int, list[str]] = {}
    order: dict[int, tuple[int, int]] = {}
    for position, (name, items) in enumerate(rankings.items()):
        for rank, item in enumerate(items, 1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
            found.setdefault(item, []).append(name)
            order.setdefault(item, (position, rank))
    ranked = sorted(scores, key=lambda item: (-scores[item], order[item]))
    return [(item, scores[item], tuple(found[item])) for item in ranked]


def dedupe(items: Iterable[str]) -> list[str]:
    """Items in order, each folded form once (« Chat » and « chat » are one label)."""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = fold(item).strip()
        if key and key not in seen:
            seen.add(key)
            out.append(item.strip())
    return out
