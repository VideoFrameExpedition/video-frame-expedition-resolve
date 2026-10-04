"""OCR line filtering, cleaning, normalised boxes and search folding (pure domain)."""

from __future__ import annotations

import math
import unicodedata

import pytest
from hypothesis import given
from hypothesis import strategies as st

from vfe_vision.domain.ocr import (
    OCR_FILTER_VERSION,
    clean_line,
    drop_cjk,
    filter_line,
    fold,
    keep_line,
    normalized_box,
)


class TestKeepLine:
    @pytest.mark.parametrize(
        ("text", "score", "expected"),
        [
            # Kept on the sample keyframes: the cooktop display and its printed labels.
            ("P2", 0.996, True),
            ("3—Boil", 0.97, True),
            ("2—Stew", 0.916, True),
            ("Boost", 1.0, True),
            ("Défense de fumer", 0.976, True),
            # Noise seen before filtering, all dropped.
            ("白", 0.962, False),
            ("日白日", 0.619, False),
            ("7", 0.816, False),
            ("2", 0.618, False),
            ("K", 0.924, False),
            ("T4", 0.622, False),
            ("Pees", 0.612, False),
            ("P2O", 0.765, False),
        ],
    )
    def test_corpus_examples(self, text: str, score: float, expected: bool) -> None:
        assert keep_line(text, score) is expected

    @pytest.mark.parametrize(
        ("text", "score", "expected"),
        [
            ("Boil", 0.8, True),  # 3+ alphanumerics: 0.8 is enough
            ("Boil", 0.7999, False),
            ("P2", 0.9, True),  # exactly 2: needs 0.9
            ("P2", 0.8999, False),
            ("é1", 0.95, True),  # accented letters count
            ("A", 1.0, False),  # a single alphanumeric is never kept
            ("—·→ «»", 1.0, False),  # punctuation and symbols do not count
            ("", 1.0, False),
            ("Boil", math.nan, False),
        ],
    )
    def test_thresholds(self, text: str, score: float, expected: bool) -> None:
        assert keep_line(text, score) is expected

    @pytest.mark.parametrize(
        "text",
        [
            "日白日白",
            "ひらがなです",
            "カタカナ",
            "ﾊﾝｶｸｶﾀｶﾅ",
            "한국어문장",
            "ＡＢＣ１２３",
            "〇〇〇〇",
            "𠀀𠀁𠀂",
        ],
    )
    def test_cjk_family_characters_never_count(self, text: str) -> None:
        assert not keep_line(text, 1.0)
        assert drop_cjk(text) == ""

    def test_latin_text_around_cjk_noise_still_counts(self) -> None:
        assert keep_line("P2 白", 0.95)
        assert not keep_line("P白", 0.99)

    def test_decomposed_input_is_counted_after_nfc(self) -> None:
        decomposed = unicodedata.normalize("NFD", "Été")
        assert keep_line(decomposed, 0.85)
        assert filter_line(decomposed, 0.85) == "Été"

    def test_filter_version_is_a_positive_int(self) -> None:
        assert isinstance(OCR_FILTER_VERSION, int)
        assert OCR_FILTER_VERSION >= 1


class TestCleanLine:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("  Sortie\tde\nsecours  ", "Sortie de secours"),
            ("abc\u202edef\u200f", "abcdef"),  # bidi override and mark
            ("\u2066P2\u2069", "P2"),  # bidi isolates
            ("\x00A\x07B\x1b", "AB"),  # C0 controls
            ("Bou\u200blangerie\ufeff", "Boulangerie"),  # zero-width space, BOM
            ("Cafe\u0301", "Café"),  # NFC
            ("ligne\u2028suivante\u0085fin", "ligne suivante fin"),  # line separators
            ("3,50\u00a0€", "3,50 €"),  # no-break space collapses too
            ("« Ô Délices » — 3,50 €", "« Ô Délices » — 3,50 €"),
        ],
    )
    def test_examples(self, raw: str, expected: str) -> None:
        assert clean_line(raw) == expected

    @given(st.text())
    def test_output_is_one_clean_line(self, raw: str) -> None:
        cleaned = clean_line(raw)
        assert cleaned == cleaned.strip()
        assert "  " not in cleaned
        assert not any(ch.isspace() and ch != " " for ch in cleaned)
        assert not any(unicodedata.category(ch) in {"Cc", "Cf"} for ch in cleaned)
        assert unicodedata.is_normalized("NFC", cleaned)
        assert clean_line(cleaned) == cleaned


class TestFilterLine:
    def test_returns_the_text_to_store(self) -> None:
        assert filter_line("  3—Boil ", 0.97) == "3—Boil"
        assert filter_line("日白日 P2", 0.95) == "P2"
        assert filter_line("Défense\u202e de  fumer", 0.99) == "Défense de fumer"

    def test_drops_rejected_lines(self) -> None:
        assert filter_line("白", 0.96) is None
        assert filter_line("Pees", 0.612) is None

    @given(st.text(), st.floats(min_value=0, max_value=1))
    def test_kept_text_is_clean_and_free_of_cjk(self, raw: str, score: float) -> None:
        kept = filter_line(raw, score)
        if kept is None:
            return
        assert kept == clean_line(kept)
        assert drop_cjk(kept) == kept
        assert sum(ch.isalnum() for ch in kept) >= 2


class TestNormalizedBox:
    def test_scales_to_image_fractions(self) -> None:
        box = [(563.0, 37.0), (898.0, 35.0), (898.0, 59.0), (563.0, 60.0)]
        assert normalized_box(box, 1280, 720) == [
            [0.4398, 0.0514],
            [0.7016, 0.0486],
            [0.7016, 0.0819],
            [0.4398, 0.0833],
        ]

    def test_full_frame_and_clamping(self) -> None:
        full = [(0.0, 0.0), (1280.0, 0.0), (1280.0, 720.0), (0.0, 720.0)]
        assert normalized_box(full, 1280, 720) == [[0, 0], [1, 0], [1, 1], [0, 1]]
        outside = [(-3.0, -1.0), (1300.0, 0.0), (1300.0, 800.0), (-3.0, 800.0)]
        assert normalized_box(outside, 1280, 720) == [[0, 0], [1, 0], [1, 1], [0, 1]]

    @pytest.mark.parametrize(("width", "height"), [(0, 720), (1280, 0), (-1, -1)])
    def test_rejects_an_empty_image(self, width: int, height: int) -> None:
        with pytest.raises(ValueError, match="taille"):
            normalized_box([(0, 0), (1, 0), (1, 1), (0, 1)], width, height)

    def test_rejects_a_box_without_four_corners(self) -> None:
        with pytest.raises(ValueError, match="4 coins"):
            normalized_box([(0, 0), (1, 0), (1, 1)], 10, 10)

    @given(
        st.lists(st.tuples(st.floats(-100, 5000), st.floats(-100, 5000)), min_size=4, max_size=4),
        st.integers(1, 4000),
        st.integers(1, 4000),
    )
    def test_always_four_points_in_the_unit_square(
        self, box: list[tuple[float, float]], width: int, height: int
    ) -> None:
        points = normalized_box(box, width, height)
        assert len(points) == 4
        for point in points:
            assert len(point) == 2
            for value in point:
                assert 0.0 <= value <= 1.0
                assert round(value, 4) == value


class TestFold:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("ÉLÉPHANT ŒUVRE garçon naïf", "elephant oeuvre garcon naif"),
            ("Cæsar Æsop", "caesar aesop"),
            ("Château de Bizy · Été 2026", "chateau de bizy · ete 2026"),
            ("ﬁn", "fin"),  # compatibility ligature
            ("Ｐ２", "p2"),  # fullwidth
            ("İstanbul", "istanbul"),  # lowercase dotted I leaves a combining mark
            ("℃", "°c"),
        ],
    )
    def test_examples(self, text: str, expected: str) -> None:
        assert fold(text) == expected

    def test_ocr_misreadings_match_the_real_words(self) -> None:
        assert fold("naif") == fold("NAÏF")
        assert fold("Oeuvre") == fold("ŒUVRE")
        assert fold("DEFENSE DE FUMER") == fold("Défense de fumer")

    @given(st.text())
    def test_is_idempotent_and_drops_every_combining_mark(self, text: str) -> None:
        folded = fold(text)
        assert fold(folded) == folded
        assert not any(unicodedata.combining(ch) for ch in folded)
        assert "œ" not in folded
        assert "æ" not in folded

    @given(st.text())
    def test_ignores_the_normalisation_form(self, text: str) -> None:
        assert fold(unicodedata.normalize("NFC", text)) == fold(unicodedata.normalize("NFD", text))
