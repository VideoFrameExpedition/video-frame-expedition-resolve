"""Bilingual analyses: which texts are translated, what is asked, how it is read."""

from __future__ import annotations

from vfe_vision.domain.translation import (
    AS_WRITTEN,
    FRAME_TEXTS,
    SYNTHESIS_TEXTS,
    Dictionary,
    SourceText,
    TextKind,
    Wanted,
    batches,
    file_suffix,
    plausible,
    text_key,
    texts_at,
    translated,
    wanted,
)

FRAME = {
    "caption": "Un chat dort.",
    "description": "Un chat noir dort sur un canapé.",
    "shot_type": "close_up",
    "subjects": [{"label": "chat", "description": "Un chat noir.", "is_main": True}],
    "actions": ["Le chat dort."],
    "mood": "calme",
    "dominant_colors": ["noir", "gris"],
    "visible_text": "STOP",
    "quality_issues": ["blur"],
    "tags": ["chat", " "],
}


def test_only_the_free_texts_of_a_frame_are_named() -> None:
    found = [text for _kind, text in texts_at(FRAME, FRAME_TEXTS)]
    assert found == [
        "Un chat dort.", "Un chat noir dort sur un canapé.", "calme", "chat", "Un chat noir.",
        "Le chat dort.", "noir", "gris", "chat",
    ]  # fmt: skip
    assert "STOP" not in found  # text as seen
    assert "close_up" not in found  # codes
    assert "blur" not in found


def test_a_translated_copy_leaves_the_rest_and_the_original_alone() -> None:
    copy = translated(FRAME, FRAME_TEXTS, str.upper)
    assert copy["caption"] == "UN CHAT DORT."
    assert copy["subjects"] == [{"label": "CHAT", "description": "UN CHAT NOIR.", "is_main": True}]
    assert copy["tags"] == ["CHAT", " "]  # a blank text stays blank
    assert copy["shot_type"] == "close_up"
    assert copy["visible_text"] == "STOP"
    assert FRAME["caption"] == "Un chat dort."


def test_missing_parts_of_a_synthesis_are_fine() -> None:
    data = {"title": "Été", "chapters": [{"first": 1, "title": "Arrivée"}], "tags": "oops"}
    assert [t for _k, t in texts_at(data, SYNTHESIS_TEXTS)] == ["Été", "Arrivée"]
    assert translated(data, SYNTHESIS_TEXTS, str.upper)["chapters"][0] == {
        "first": 1, "title": "ARRIVÉE",
    }  # fmt: skip


def test_a_dictionary_reads_its_language_or_the_text_as_written() -> None:
    english = Dictionary("en", {text_key("chat"): "cat"})
    assert english("chat") == "cat"
    assert english("chien") == "chien"
    assert english.maybe(None) is None
    assert AS_WRITTEN("chat") == "chat"


def test_each_text_is_asked_for_in_the_other_language_then_back() -> None:
    sources = [
        SourceText("Un chat dort.", TextKind.SENTENCE, "fr"),
        SourceText("A cat sleeps.", TextKind.SENTENCE, "fr"),  # written in the wrong language
        SourceText("Un chat dort.", TextKind.SENTENCE, "fr"),  # the same text once only
        SourceText("renard", TextKind.LABEL, None),  # unknown language: both
    ]
    first, second = wanted(sources, {})
    assert first == [
        Wanted("Un chat dort.", TextKind.SENTENCE, "en"),
        Wanted("A cat sleeps.", TextKind.SENTENCE, "en"),
        Wanted("renard", TextKind.LABEL, "fr"),
        Wanted("renard", TextKind.LABEL, "en"),
    ]
    assert second == []
    known = {
        (text_key("Un chat dort."), "en"): "A cat is sleeping.",
        (text_key("A cat sleeps."), "en"): "A cat sleeps.",  # given back unchanged
        (text_key("renard"), "fr"): "renard",
        (text_key("renard"), "en"): "fox",
    }
    first, second = wanted(sources, known)
    assert first == []
    assert second == [Wanted("A cat sleeps.", TextKind.SENTENCE, "fr")]
    known[(text_key("A cat sleeps."), "fr")] = "Un chat dort."
    assert wanted(sources, known) == ([], [])


def test_requests_hold_one_language_and_kind_within_their_size() -> None:
    items = [Wanted(f"label {i}", TextKind.LABEL, "en") for i in range(70)]
    items += [Wanted("x" * 900, TextKind.PARAGRAPH, "en") for _ in range(3)]
    items.append(Wanted("étiquette", TextKind.LABEL, "fr"))
    found = batches(items, max_chars=2000)
    assert [(b.target, b.kind, len(b.items)) for b in found] == [
        ("en", TextKind.LABEL, 60), ("en", TextKind.LABEL, 10),
        ("en", TextKind.PARAGRAPH, 2), ("en", TextKind.PARAGRAPH, 1),
        ("fr", TextKind.LABEL, 1),
    ]  # fmt: skip


def test_a_commentary_or_an_empty_answer_is_no_translation() -> None:
    assert plausible("chat", "cat")
    assert not plausible("chat", " ")
    assert not plausible("chat", "The French word « chat » means cat, as in the animal. " * 3)
    assert not plausible("Une main tient une aubergine.", "A hand holds a茄子.")
    assert plausible("茄子 (aubergine)", "茄子 (eggplant)")  # already there


def test_file_suffixes() -> None:
    assert file_suffix("fr") == "FR"
    assert file_suffix("en") == "EN"
