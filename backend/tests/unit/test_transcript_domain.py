"""Transcript domain: language codes, hallucination guards, coverage, cues, SRT/WebVTT, excerpts."""

from __future__ import annotations

import itertools
import re
import unicodedata

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from vfe_vision.domain.languages import (
    NO_LANGUAGE,
    WHISPER_LANGUAGES,
    language_name,
    whisper_code,
)
from vfe_vision.domain.transcript import (
    MAX_LINE,
    TRANSCRIPT_GUARD_VERSION,
    Cue,
    build_cues,
    clean_untrusted,
    context_clips,
    excerpt_at,
    is_suspect,
    midpoint_within,
    suspect_reasons,
    to_srt,
    to_vtt,
    uncovered_runs,
    wrap_lines,
)
from vfe_vision.ports.asr import AsrSegment, AsrWord

Word = tuple[float, float, str, float]


def seg(
    words: list[Word],
    *,
    text: str | None = None,
    suspect: bool = False,
    start: float | None = None,
    end: float | None = None,
) -> AsrSegment:
    return AsrSegment(
        idx=0,
        start=start if start is not None else (words[0][0] if words else 0.0),
        end=end if end is not None else (words[-1][1] if words else 0.0),
        text=text if text is not None else "".join(w[2] for w in words).strip(),
        avg_logprob=-0.2,
        no_speech_prob=0.0,
        compression_ratio=1.4,
        temperature=0.0,
        language="fr",
        words=tuple(AsrWord(*w) for w in words),
        suspect=suspect,
        second_pass=False,
    )


def spoken(text: str, start: float, step: float = 0.4) -> list[Word]:
    """Words of ``text`` one after the other, ``step`` seconds each, Whisper-style spaces."""
    return [
        (round(start + i * step, 3), round(start + (i + 1) * step, 3), " " + token, 0.9)
        for i, token in enumerate(text.split())
    ]


# ---------------------------------------------------------------- languages
class TestLanguages:
    @pytest.mark.parametrize(
        ("tag", "expected"),
        [
            ("fre", "fr"), ("fra", "fr"), ("FR", "fr"), ("fr-FR", "fr"), ("pt_BR", "pt"),
            ("ger", "de"), ("deu", "de"), ("chi", "zh"), ("zho", "zh"), ("cmn", "zh"),
            ("jav", "jw"), ("jv", "jw"), ("haw", "haw"), ("yue", "yue"), ("nob", "no"),
            ("iw", "he"), ("eng", "en"), (" eng ", "en"), ("gre", "el"), ("ell", "el"),
        ],
    )  # fmt: skip
    def test_container_tags_map_to_whisper_codes(self, tag: str, expected: str) -> None:
        assert whisper_code(tag) == expected

    @pytest.mark.parametrize("tag", ["und", "mis", "mul", "zxx", "UND", "", None, "qaa", "qtz"])
    def test_no_language_tags_let_whisper_detect(self, tag: str | None) -> None:
        assert whisper_code(tag) is None

    def test_unknown_tags_are_not_guessed(self) -> None:
        assert whisper_code("xyz") is None
        assert whisper_code("klingon") is None

    def test_every_whisper_code_is_known(self) -> None:
        assert len(WHISPER_LANGUAGES) == 100
        assert len(set(WHISPER_LANGUAGES)) == 100
        assert all(whisper_code(code) == code for code in WHISPER_LANGUAGES)
        assert {"und", "mis", "mul", "zxx"} == NO_LANGUAGE

    def test_display_names(self) -> None:
        assert language_name("fr") == "français"
        assert language_name("fr", "en") == "French"
        assert language_name("eng") == "anglais"
        assert language_name("yue") == "cantonais"
        assert language_name("xx") == "xx"
        assert language_name(None) is None
        assert all(language_name(code) for code in WHISPER_LANGUAGES)


# ---------------------------------------------------------------- guards
class TestGuards:
    def test_good_segment_is_trusted(self) -> None:
        # RIZ turbo: worst good window -0.38, mean word p >= 0.635.
        reasons = suspect_reasons(
            "J'ai fait cuire le riz.",
            avg_logprob=-0.38,
            word_probabilities=[0.99, 0.95, 0.64],
            temperature=0.2,
            compression_ratio=1.2,
            previous_text="Bonjour à tous.",
        )
        assert reasons == ()

    def test_measured_bad_segments(self) -> None:
        # Samsung phone noise: 'ありがとう', lang ja p=0.59, lp -0.98, word p 0.23 / 0.315.
        assert set(
            suspect_reasons("ありがとう", avg_logprob=-0.98, word_probabilities=[0.23, 0.315])
        ) == {"low_logprob", "low_word_probability", "known_hallucination"}
        # EN intro then French: loop at T=1.0, lp -1.023, cr 2.33.
        loop = suspect_reasons(
            "And I'll see you next time.",
            avg_logprob=-1.023,
            temperature=1.0,
            compression_ratio=2.33,
            previous_text="And I'll see you next time.",
        )
        assert {"low_logprob", "repeat", "fallback_loop", "known_hallucination"} <= set(loop)

    def test_each_rule(self) -> None:
        assert suspect_reasons("Texte", avg_logprob=-0.81) == ("low_logprob",)
        assert suspect_reasons("Texte", avg_logprob=-0.1, word_probabilities=[0.4, 0.5]) == (
            "low_word_probability",
        )
        assert suspect_reasons("On y va !", avg_logprob=-0.1, previous_text="on y va") == (
            "repeat",
        )
        assert suspect_reasons(
            "la la la", avg_logprob=-0.1, temperature=0.8, compression_ratio=2.3
        ) == ("fallback_loop",)
        assert suspect_reasons("Bonjour", avg_logprob=-0.1, language_mismatch=True) == (
            "language_mismatch",
        )

    def test_a_run_of_the_previous_text_is_a_repeat(self) -> None:
        # RIZ second pass: the prompt (previous sentence) copied into a 1 s gap, lp -0.56.
        previous = "On va pas l'entendre mais le riz est en train de gazouiller."
        copied = "On va pas l'entendre mais le riz est en train"
        assert suspect_reasons(copied, avg_logprob=-0.56, previous_text=previous) == ("repeat",)
        assert not is_suspect("le riz", avg_logprob=-0.2, previous_text=previous)  # too short
        assert not is_suspect("Merci", avg_logprob=-0.2, previous_text="Merci beaucoup")
        assert not is_suspect("en train de cuire", avg_logprob=-0.2, previous_text=previous)

    def test_repetitive_but_real_speech_is_kept(self) -> None:
        # The TTS text said twice: cr 2.09 at T=0.2/0.4, correct text.
        assert not is_suspect(
            "abc " * 10, avg_logprob=-0.3, temperature=0.4, compression_ratio=2.09
        )

    def test_stock_phrases_need_a_weak_window(self) -> None:
        assert not is_suspect("Merci.", avg_logprob=-0.2)
        assert suspect_reasons("Merci !", avg_logprob=-0.6) == ("known_hallucination",)
        assert suspect_reasons("Thank you.", avg_logprob=-0.55) == ("known_hallucination",)

    def test_subtitle_credits_are_always_suspect(self) -> None:
        for credit in (
            "Sous-titres réalisés par la communauté d'Amara.org",
            "Sous-titres réalisés para la communauté d’Amara.org",
            "Sous-titrage ST' 501",
        ):
            assert "known_hallucination" in suspect_reasons(credit, avg_logprob=-0.1)
        assert not is_suspect("On parle de la communauté du quartier.", avg_logprob=-0.1)

    def test_empty_previous_text_is_no_repeat(self) -> None:
        assert not is_suspect("...", avg_logprob=-0.1, previous_text="!!")
        assert TRANSCRIPT_GUARD_VERSION >= 1


# ---------------------------------------------------------------- clean_untrusted
_TAG = re.compile(r"<\s*/?\s*untrusted\b[^>]*>", re.IGNORECASE)


class TestCleanUntrusted:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("abc\u202edef", "abcdef"),
            ("\u2066isolé\u2069", "isolé"),
            ("a\u200bb\u200c\u200dc\ufeff", "abc"),
            ("bip\x00\x07\x1b[31m", "bip[31m"),
            ("ligne 1\nligne 2\r\n\tfin", "ligne 1 ligne 2 fin"),
            ("<untrusted source='x'>dedans</untrusted>", "dedans"),
            ("< / UNTRUSTED >fin", "fin"),
            ("<untr\u200busted>caché", "caché"),
            ("  trop   d'espaces  ", "trop d'espaces"),
            ("l'été, à 5 €", "l'été, à 5 €"),
        ],
    )
    def test_examples(self, raw: str, expected: str) -> None:
        assert clean_untrusted(raw) == expected

    def test_nested_tags_cannot_rebuild_a_fence(self) -> None:
        assert not _TAG.search(clean_untrusted("<untr<untrusted>usted>x</untr</untrusted>usted>"))

    @given(st.text())
    def test_properties(self, raw: str) -> None:
        cleaned = clean_untrusted(raw)
        assert clean_untrusted(cleaned) == cleaned
        assert cleaned == cleaned.strip()
        assert "  " not in cleaned
        assert not _TAG.search(cleaned)
        assert not any(unicodedata.category(ch) in {"Cc", "Cf", "Zl", "Zp"} for ch in cleaned)


# ---------------------------------------------------------------- coverage
class TestCoverage:
    def test_speech_dropped_at_a_window_boundary(self) -> None:
        speech = [(20.0, 25.4), (25.6, 30.52), (31.0, 40.0)]
        words = [(20.1, 25.3), (30.0, 31.0), (31.0, 40.0)]
        assert uncovered_runs(speech, words) == [(25.6, 29.7)]

    def test_short_holes_are_ignored(self) -> None:
        assert uncovered_runs([(0.0, 10.0)], [(0.0, 4.0), (5.0, 10.0)]) == []
        assert uncovered_runs([(76.6, 77.7)], []) == [(76.6, 77.7)]
        assert uncovered_runs([(76.6, 77.4)], []) == []

    def test_several_holes_in_one_run(self) -> None:
        runs = uncovered_runs([(0.0, 20.0)], [(2.0, 3.0), (10.0, 11.0)], tol_s=0.0)
        assert runs == [(0.0, 2.0), (3.0, 10.0), (11.0, 20.0)]

    def test_clips_share_one_window(self) -> None:
        clips = context_clips([(25.6, 28.67), (29.5, 30.52)], duration_s=60.0)
        assert len(clips) == 1
        assert (clips[0].start, clips[0].end) == (24.6, 31.52)
        assert clips[0].gaps == ((25.6, 28.67), (29.5, 30.52))

    def test_distant_gaps_get_their_own_clip_within_the_file(self) -> None:
        clips = context_clips([(0.3, 1.5), (50.0, 59.8)], duration_s=60.0)
        assert [(c.start, c.end) for c in clips] == [(0.0, 2.5), (49.0, 60.0)]

    def test_midpoint(self) -> None:
        assert midpoint_within(3.0, 4.0, [(3.4, 6.0)])
        assert not midpoint_within(2.0, 3.0, [(3.4, 6.0)])


# ---------------------------------------------------------------- cues
class TestCues:
    def test_pause_sentence_and_line_rules(self) -> None:
        words = [
            *spoken("J'ai fait cuire le riz.", 0.0),
            *spoken("Ensuite on ajoute un peu de sel", 2.1),  # 0.1 s after the sentence
            *spoken("et on attend.", 6.0),  # 1.1 s pause: new cue
        ]
        cues = build_cues([seg(words)])
        assert [c.text for c in cues] == [
            "J'ai fait cuire le riz.",
            "Ensuite on ajoute un peu de sel",
            "et on attend.",
        ]
        assert all(len(line) <= MAX_LINE for c in cues for line in c.lines)

    def test_long_speech_is_split_in_two_line_cues_of_at_most_7_s(self) -> None:
        text = " ".join(["mot"] * 60)
        cues = build_cues([seg(spoken(text, 10.0, step=0.3))])
        assert len(cues) > 1
        for cue in cues:
            assert 1 <= len(cue.lines) <= 2
            assert all(len(line) <= MAX_LINE for line in cue.lines)
            assert cue.end - cue.start <= 7.0 + 1e-9
        two_lines = [c for c in cues if len(c.lines) == 2]
        assert two_lines
        assert all(abs(len(c.lines[0]) - len(c.lines[1])) <= 4 for c in two_lines)  # balanced

    def test_words_stretched_over_a_pause_are_clamped(self) -> None:
        # RIZ turbo: ' le' spans 71.74-74.58 s across a VAD gap.
        cues = build_cues([seg([(70.0, 71.7, " on", 0.9), (71.74, 74.58, " le", 0.9)])])
        assert cues[-1].end == pytest.approx(71.74 + 1.5)

    def test_short_cues_last_one_second_without_overlap(self) -> None:
        # A pause (> 0.8 s) splits them; the first one is stretched up to the second one.
        cues = build_cues([seg([(1.0, 1.1, " Oui.", 0.9)]), seg([(1.95, 2.3, " Non.", 0.9)])])
        assert [(c.start, c.end) for c in cues] == [(1.0, 1.95), (1.95, 2.95)]
        lone = build_cues([seg([(1.0, 1.2, " Seul.", 0.9)])])
        assert [(c.start, c.end) for c in lone] == [(1.0, 2.0)]

    def test_suspect_segments_are_left_out(self) -> None:
        good = seg(spoken("Bonjour à tous.", 0.0))
        bad = seg(spoken("Sous-titres réalisés par Amara.org", 5.0), suspect=True)
        assert [c.text for c in build_cues([good, bad])] == ["Bonjour à tous."]
        assert len(build_cues([good, bad], include_suspect=True)) == 2

    def test_unordered_segments_and_second_pass_are_sorted(self) -> None:
        late = seg(spoken("plus tard", 20.0))
        early = seg(spoken("au début", 1.0))
        assert [c.text for c in build_cues([late, early])] == ["au début", "plus tard"]

    def test_segment_without_word_timings(self) -> None:
        subtitle = seg([], text="Un sous-titre intégré assez long pour tenir sur deux lignes ici",
                       start=3.0, end=6.0)  # fmt: skip
        cues = build_cues([subtitle])
        assert cues[0].start == 3.0
        assert cues[-1].end <= 6.0 + 1e-9
        assert " ".join(c.text for c in cues).split() == subtitle.text.split()

    def test_text_without_spaces_is_cut(self) -> None:
        words = [(i * 0.2, i * 0.2 + 0.2, "日本語のテキスト", 0.9) for i in range(12)]
        cues = build_cues([seg(words)])
        assert "".join(c.text.replace(" ", "") for c in cues) == "日本語のテキスト" * 12
        assert all(len(line) <= MAX_LINE and len(c.lines) <= 2 for c in cues for line in c.lines)

    def test_wrap_lines(self) -> None:
        assert wrap_lines("court") == ["court"]
        assert wrap_lines("a" * 50) == ["a" * 42, "a" * 8]
        assert wrap_lines("") == []

    @settings(max_examples=300, deadline=None)
    @given(
        st.lists(
            st.tuples(
                st.floats(min_value=0.0, max_value=3.0),  # gap before the word
                st.floats(min_value=0.0, max_value=5.0),  # duration
                st.booleans(),  # leading space
                st.text(
                    alphabet=st.one_of(
                        st.sampled_from(list("abcdéè ,.;:!?-><&日本\u202e\u200b\n")),
                        st.characters(),
                    ),
                    max_size=60,
                ),
            ),
            max_size=80,
        ),
        st.integers(min_value=1, max_value=4),
    )
    def test_cue_invariants(
        self, spec: list[tuple[float, float, bool, str]], n_segments: int
    ) -> None:
        words: list[Word] = []
        at = 0.0
        for gap, duration, space, text in spec:
            at += gap
            words.append((at, at + duration, (" " if space else "") + text, 0.9))
            at += duration
        size = max(1, len(words) // n_segments)
        segments = [seg(words[i : i + size]) for i in range(0, len(words), size)]
        cues = build_cues(segments)
        for cue in cues:
            assert cue.end > cue.start >= 0.0
            assert 1 <= len(cue.lines) <= 2
            assert all(0 < len(line) <= MAX_LINE for line in cue.lines)
            assert all(line == clean_untrusted(line) for line in cue.lines)
        for before, after in itertools.pairwise(cues):
            assert before.start <= after.start
            assert before.end <= after.start
        kept = "".join(clean_untrusted(w[2]) for w in words)
        assert "".join(c.text for c in cues).replace(" ", "") == kept.replace(" ", "")
        srt = to_srt(cues)
        assert srt.count(" --> ") == len(cues)
        assert len([b for b in srt.split("\n\n") if b.strip()]) == len(cues)
        vtt = to_vtt(cues)
        assert vtt.count(" --> ") == len(cues)
        assert "<" not in vtt


# ---------------------------------------------------------------- SRT / WebVTT
class TestSubtitleFiles:
    def test_srt(self) -> None:
        cues = [Cue(1.0, 2.5, ("Bonjour",)), Cue(3725.5, 3727.0, ("deux", "lignes"))]
        assert to_srt(cues) == (
            "1\n00:00:01,000 --> 00:00:02,500\nBonjour\n\n"
            "2\n01:02:05,500 --> 01:02:07,000\ndeux\nlignes\n"
        )

    def test_srt_offset_and_clamp(self) -> None:
        srt = to_srt([Cue(0.2, 1.0, ("x",))], offset_s=3600.0)
        assert "01:00:00,200 --> 01:00:01,000" in srt
        assert "00:00:00,000 --> 00:00:00,500" in to_srt([Cue(0.2, 1.0, ("x",))], offset_s=-0.5)

    def test_timing_arrows_and_blank_lines_are_neutralised(self) -> None:
        cues = [
            Cue(0.0, 1.0, ("a --> b", "c ---> d")),
            Cue(1.0, 2.0, ("", " \n ")),
            Cue(2.0, 3.0, ("x\n\ny",)),
        ]
        srt = to_srt(cues)
        assert srt == (
            "1\n00:00:00,000 --> 00:00:01,000\na -> b\nc -> d\n\n"
            "2\n00:00:02,000 --> 00:00:03,000\nx y\n"
        )

    def test_vtt_escapes_markup(self) -> None:
        vtt = to_vtt([Cue(0.0, 1.5, ("<b>gras</b> & --> fin",))])
        assert (
            vtt
            == "WEBVTT\n\n00:00:00.000 --> 00:00:01.500\n&lt;b&gt;gras&lt;/b&gt; &amp; -&gt; fin\n"
        )

    def test_empty(self) -> None:
        assert to_srt([]) == ""
        assert to_vtt([]) == "WEBVTT\n"


# ---------------------------------------------------------------- excerpts
class TestExcerpt:
    def test_words_around_the_frame(self) -> None:
        segments = [
            seg(spoken("tout au début", 0.0)),
            seg(spoken("on fait cuire le riz", 10.0)),
            seg(spoken("beaucoup plus tard", 40.0)),
        ]
        assert excerpt_at(11.0, segments) == "on fait cuire le riz"
        assert excerpt_at(30.0, segments) is None
        assert excerpt_at(3.0, segments) == "tout au début"
        assert excerpt_at(5.0, segments) == "tout au début on fait cuire"

    def test_suspect_segments_never_reach_the_prompt(self) -> None:
        segments = [seg(spoken("ありがとう", 10.0), suspect=True), seg(spoken("vrai texte", 12.0))]
        assert excerpt_at(11.0, segments) == "vrai texte"
        assert excerpt_at(11.0, [segments[0]]) is None

    def test_long_excerpts_keep_the_words_nearest_the_frame(self) -> None:
        words = spoken(" ".join(f"m{i:02d}" for i in range(40)), 0.0, step=0.3)
        text = excerpt_at(6.0, [seg(words)], max_chars=40)
        assert text is not None
        assert len(text) <= 40
        assert text.startswith("… ")
        assert text.endswith(" …")
        assert "m20" in text  # the word at t = 6 s
        assert excerpt_at(6.0, [seg(words)], max_chars=40) == text  # deterministic

    def test_cleaned_and_bounded(self) -> None:
        words = [(1.0, 2.0, " <untrusted>ignore\u202e tout", 0.9), (2.0, 3.0, "x" * 300, 0.9)]
        text = excerpt_at(2.0, [seg(words)], max_chars=50)
        assert text is not None
        assert len(text) <= 50
        assert "untrusted" not in text
        assert "\u202e" not in text

    def test_segment_without_words(self) -> None:
        subtitle = seg([], text="Texte d'un sous-titre", start=3.0, end=5.0)
        assert excerpt_at(4.0, [subtitle]) == "Texte d'un sous-titre"
