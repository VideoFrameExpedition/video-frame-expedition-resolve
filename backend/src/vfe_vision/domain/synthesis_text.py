"""The texts of the synthesis (pure functions): what the language model reads, and the
tidying of what it writes.

What it reads (input variant "V4", the full form; the variant is stored with each synthesis):
a header (file name as metadata, duration, capture, place, light, weather, sounds, speech), a
blank line, then the blocks under the chapter headings code cut, each with its framing and
usability, its distinct keyframe descriptions, its sounds and its speech. No time, shot number,
camera motion or defect: they cost 15–47% of the tokens and leaked into the prose (« Une vue
panoramique gauche montre… » in 14 of 73 outputs, 0 of 31 without them). Variant "V4c", the
compact form for long videos, keeps one description per block and clips the speech.

Sounds: the list shown in the app keeps single-tagger guesses, a text written by a
model must not (« sabots » over ruins in 10 runs of 10, from a YAMNet-only 0.535 with no horse in
any picture). A sound is written when both taggers heard it, when it is strong and long, or when
it is fair and a picture shows its source. Tuned on 22 files: a principle, not a proven threshold.

What it writes: titles in sentence case (qwen3-vl-4b writes Title Case in more than half of its
titles whatever the prompt says), no « le bloc B4 » in the reasons, tags merged with the
frames' and the sounds'.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass

from vfe_vision.domain.audio_events import CATEGORY_LABELS, Category
from vfe_vision.domain.sound_names import sound_name
from vfe_vision.domain.synthesis_input import Block, Frame, HeardSound, Video, speech_text
from vfe_vision.domain.timecode import format_clock
from vfe_vision.domain.transcript import clean_untrusted

SOUND_GATE_VERSION = 1  # part of the stage's rules version: a new gate rewrites the synthesis
# Tidying of what the model wrote (sentence case, block references, absences): a new rule
# re-tidies the stored texts from the cached answers.
TEXT_RULES_VERSION = 2  # v2: first word capitalised, a name's singular or plural kept

LANGUAGE_HINT: Mapping[str, str] = {
    # « Le vidéo » in 13 of 98 outputs without it, 0 of 35 with it.
    "fr": " In French, « vidéo » is feminine (« la vidéo »).",
}

# ---------------------------------------------------------------- sound gate
BOTH_TAGGERS = 2
STRONG_SCORE = 0.6  # a single tagger this sure…
STRONG_S = 5.0  # …for this long is believed
FAIR_SCORE = 0.35  # a fair guess this long needs a picture of its source
FAIR_S = 3.0
# Words of a keyframe caption or subject that show the source of a sound (s/x plurals match;
# the other forms an earlier substring match caught, « rainy », « gouttelettes », are listed).
_RAIN = frozenset({"pluie", "rain", "rainy", "raining", "raindrop", "averse", "parapluie"})
SOURCES: Mapping[str, frozenset[str]] = {
    "Clip-clop": frozenset({"cheval", "chevaux", "horse", "âne", "poney", "calèche"}),
    "Insect": frozenset({
        "insecte", "abeille", "papillon", "mouche", "guêpe", "bourdon", "chenille", "cigale",
        "grillon", "insect", "bee", "butterfly", "butterflies", "fly", "flies",
    }),
    "Rain": _RAIN,
    "Rain on surface": _RAIN,
    "Raindrop": frozenset({"pluie", "rain", "rainy", "raining", "raindrop", "goutte",
                           "gouttelette"}),
    "Bicycle": frozenset({"vélo", "bicyclette", "bicycle", "cycliste"}),
    "Boat, Water vehicle": frozenset({"bateau", "boat", "barque", "kayak", "canoë"}),
    "Rowboat, canoe, kayak": frozenset({"kayak", "canoë", "barque", "rame", "rowing"}),
    "Frog": frozenset({"grenouille", "frog", "étang", "mare", "lac"}),
    "Croak": frozenset({"grenouille", "frog", "étang", "mare", "lac"}),
    "Gurgling": frozenset({"ruisseau", "rivière", "cascade", "stream", "fontaine"}),
}  # fmt: skip
FAMILY_MIN_SHARE = 0.05
# Other families are shown only when a sound that passed the gate backs them.
ALWAYS_SHOWN = frozenset({"speech", "music", "silence"})
# The display labels « Nature et animaux », « Outils et machines » would tell the model that
# animals or machines were heard: the family alone is written, as in the measured inputs.
_PROMPT_FAMILY: Mapping[Category, str] = {Category.NATURE: "nature", Category.TOOLS: "outils"}
MAX_HEARD = 8
MAX_INSTRUMENTS = 4
AMBIENT_ONLY = "ambient sound only (no specific sound identified)"
# « pas » alone was read as a negation (« un bruit discret de 10 secondes »).
OWN_SOUND_NAMES: Mapping[str, Mapping[str, str]] = {
    "fr": {"Walk, footsteps": "bruits de pas", "Clip-clop": "bruit de sabots"},
    "en": {"Walk, footsteps": "footsteps", "Clip-clop": "clip-clop of hooves"},
}

# ---------------------------------------------------------------- input rendering
FILE_NAME_KEY = "File name (metadata, not a title):"
COMPACT_SPEECH_CHARS = 160
CAPTION_JACCARD = 0.5
FRAMING: Mapping[str, Mapping[str, str]] = {
    "fr": {
        "extreme_wide": "plan très large", "wide": "plan large", "medium": "plan moyen",
        "close_up": "gros plan", "extreme_close_up": "très gros plan", "macro": "macro",
    },
    "en": {
        "extreme_wide": "extreme wide shot", "wide": "wide shot", "medium": "medium shot",
        "close_up": "close-up", "extreme_close_up": "extreme close-up", "macro": "macro",
    },
}  # fmt: skip
# English enum values in the header came back as « bleu hour », « blue hour » (8 outputs).
LIGHT_PHASE: Mapping[str, Mapping[str, str]] = {
    "fr": {
        "day": "jour", "golden_hour": "heure dorée", "blue_hour": "heure bleue",
        "civil_twilight": "crépuscule civil", "nautical_twilight": "crépuscule nautique",
        "astronomical_twilight": "crépuscule astronomique", "night": "nuit",
    },
    "en": {},  # the enum values, « _ » as a space
}  # fmt: skip
DAY_PART: Mapping[str, Mapping[str, str]] = {
    "fr": {"morning": "matin", "midday": "midi", "afternoon": "après-midi", "evening": "soir",
           "night": "nuit"},
    "en": {},
}  # fmt: skip
ORIENTATION: Mapping[str, Mapping[str, str]] = {
    "fr": {"horizontal": "horizontal", "vertical": "vertical", "square": "carré"},
    "en": {},
}

# ---------------------------------------------------------------- tags
TAG_LIMIT = 15
FRAME_TAG_SHARE = 0.2  # of the described keyframes…
FRAME_TAG_MIN = 2  # …and at least this many…
FRAME_TAG_MIN_FRAMES = 4  # …once there are this many (below, any keyframe's tag)
GENERIC_TAGS = frozenset({
    "extérieur", "intérieur", "flou", "jour", "français",
    "outdoor", "outdoors", "indoor", "indoors", "blur", "blurry", "day", "french",
})  # fmt: skip

_TOKEN = re.compile(r"[\w']+")
_STOP = frozenset({
    "un", "une", "des", "de", "du", "la", "le", "les", "l", "d", "et", "à", "au", "aux", "en",
    "sur", "dans", "avec", "a", "the", "of", "in", "on", "and", "with",
})  # fmt: skip
_ENGLISH = frozenset(
    {"the", "of", "and", "with", "is", "are", "in", "on", "an", "its", "from", "against", "under"}
)
_WORD = re.compile(r"\w+")
_CATEGORIES: Mapping[str, Category] = {c.value: c for c in Category}


# ---------------------------------------------------------------- sounds
def sound_in_prompt(video: Video, heard: HeardSound) -> bool:
    """Whether a heard sound may be written into the model's input (and so into its texts):
    both taggers heard it, or it scores ≥ 0.6 over ≥ 5 s, or it scores ≥ 0.35 over ≥ 3 s and a
    keyframe caption or subject names its source (a frog under a pond, clip-clop under a horse).

    Measured on test videos: the false sounds heard on a video of tin cans (rain, bicycle,
    clip-clop), the clip-clop over a video of ruins and the insect in a museum drop out (0/5 runs
    each); frog, croak, boat, rowing, wind and footsteps stay.
    """
    return _passes(heard, lambda: _picture_words(video.frames))


def sound_label(label: str, language: str = "fr") -> str:
    """How a sound is named for the model: lower case (a capital in the middle of a line reads as
    a proper noun, see ``proper_nouns``), « ou » instead of the commas of AudioSet names (the
    list itself is comma-separated), and never « pas » alone."""
    own = OWN_SOUND_NAMES.get(language, OWN_SOUND_NAMES["en"]).get(label)
    if own:
        return own
    name = sound_name(label, language).replace(", ", " ou " if language == "fr" else " or ")
    return _lower_first(name)


def sound_line(video: Video, language: str = "fr") -> str:
    """The « Sound: » line of the header: family shares, the sounds that pass the gate, the
    instruments. A family is shown only for speech, music, silence and the families of the sounds
    kept: « nature 24 %, water 14 % » alone made the model write « sons de nature et d'eau » over
    a rush where no specific sound passed. « Other » is never shown; with nothing named at all
    the line is ``AMBIENT_ONLY``."""
    return _sound_line(video, _prompt_sounds(video), language)


def block_sounds(video: Video, block: Block) -> list[str]:
    """The AudioSet labels heard during a block that pass the gate, in the block's order."""
    passing = {h.label for h in _prompt_sounds(video)}
    return [label for label in dict.fromkeys(block.heard) if label in passing]


def _passes(heard: HeardSound, picture: Callable[[], frozenset[str]]) -> bool:
    if len(set(heard.sources)) >= BOTH_TAGGERS:
        return True
    if heard.score >= STRONG_SCORE and heard.seconds >= STRONG_S:
        return True
    keys = SOURCES.get(heard.label)
    # Written as « not ≥ » so that a NaN score or length never passes.
    if not keys or not (heard.score >= FAIR_SCORE and heard.seconds >= FAIR_S):
        return False
    return _names_any(picture(), keys)


def _prompt_sounds(video: Video) -> list[HeardSound]:
    """The heard sounds that pass the gate (the words of the pictures read once, when needed)."""
    words: list[frozenset[str]] = []

    def picture() -> frozenset[str]:
        if not words:
            words.append(_picture_words(video.frames))
        return words[0]

    return [h for h in video.heard if _passes(h, picture)]


def _picture_words(frames: Iterable[Frame]) -> frozenset[str]:
    """The words of the keyframe captions and subject labels, whole words only: « terrain » must
    not show rain, nor « place » a lake."""
    words: set[str] = set()
    for frame in frames:
        data = frame.data or {}
        texts = [str(data.get("caption") or "")]
        texts += [
            str(s.get("label") or "") for s in data.get("subjects") or [] if isinstance(s, dict)
        ]
        for text in texts:
            words.update(_WORD.findall(text.lower()))
    return frozenset(words)


def _names_any(words: frozenset[str], keys: Iterable[str]) -> bool:
    return any(k in words or f"{k}s" in words or f"{k}x" in words for k in keys)


def _sound_line(video: Video, heard: Sequence[HeardSound], language: str) -> str:
    backed = {h.category for h in heard}
    shares = sorted(video.presence.items(), key=lambda item: -item[1])
    families = [
        f"{_family(name, language)} {share:.0%}"
        for name, share in shares
        if share >= FAMILY_MIN_SHARE
        and name != Category.OTHER
        and (name in ALWAYS_SHOWN or name in backed)
    ]
    parts = [", ".join(families)] if families else []
    if heard:
        parts.append(
            "heard: "
            + ", ".join(
                f"{sound_label(h.label, language)} ({h.seconds:.0f} s)" for h in heard[:MAX_HEARD]
            )
        )
    if video.instruments:
        instruments = video.instruments[:MAX_INSTRUMENTS]
        parts.append("instruments: " + ", ".join(sound_label(i, language) for i in instruments))
    # « Ambient only » when nothing at all is named: an old prompt printed it before a passing
    # sound of a family never shown (« … no specific sound identified ; heard: bruits de pas »).
    return " ; ".join(parts) or AMBIENT_ONLY


def _family(name: str, language: str) -> str:
    category = _CATEGORIES.get(name)
    if language != "fr" or category is None:
        return name
    return _PROMPT_FAMILY.get(category) or _lower_first(CATEGORY_LABELS[category])


def _lower_first(text: str) -> str:
    """« Grenouille » → « grenouille », « Orgue Hammond » → « orgue Hammond »; an acronym stays."""
    first = text.split(" ", 1)[0]
    if not text or (len(first) > 1 and first.isupper()):
        return text
    return text[0].lower() + text[1:]


# ---------------------------------------------------------------- captions
def looks_english(text: str) -> bool:
    """Two English function words or more: a description the vision model wrote in English (it
    slips on some keyframes of a French run)."""
    words = re.findall(r"[a-z']+", text.lower())
    return sum(w in _ENGLISH for w in words) >= 2


@dataclass(slots=True)
class _Group:
    caption: str
    words: frozenset[str]
    count: int
    english: bool


def distinct_captions(frames: Iterable[Frame], *, language: str = "fr") -> list[tuple[str, int]]:
    """The keyframe captions, near-duplicates merged (Jaccard of their words ≥ 0.5), as
    (caption, how many keyframes): those in the output language first, then English slips,
    each by majority. A group keeps a wording in the output language over an English one."""
    groups: list[_Group] = []
    prefer_own = language != "en"
    for frame in frames:
        caption = clean_untrusted(str((frame.data or {}).get("caption") or ""))
        if not caption:
            continue
        words = _tokens(caption)
        english = looks_english(caption)
        group = next((g for g in groups if _same_view(g, caption, words)), None)
        if group is None:
            groups.append(_Group(caption, words, 1, english))
            continue
        group.count += 1
        if prefer_own and group.english and not english:
            group.caption, group.words, group.english = caption, words, False
    groups.sort(key=lambda g: (prefer_own and g.english, -g.count))
    return [(g.caption, g.count) for g in groups]


def _tokens(text: str) -> frozenset[str]:
    return frozenset(w for w in _TOKEN.findall(text.lower()) if w not in _STOP and len(w) > 2)


def _same_view(group: _Group, caption: str, words: frozenset[str]) -> bool:
    if group.caption.casefold() == caption.casefold():
        return True
    if not group.words or not words:
        return False
    return len(group.words & words) / len(group.words | words) >= CAPTION_JACCARD


# ---------------------------------------------------------------- input rendering
def render_input(
    video: Video,
    blocks: Sequence[Block],
    chapters: Sequence[tuple[int, int]],
    usable: Mapping[int, int],
    *,
    weather_line: str | None,
    language: str = "fr",
    compact: bool = False,
) -> str:
    """The analysis data the model writes from (V4, or V4c when ``compact``).

    The header ends at the first blank line: the reduce step of a long video reuses it alone.
    Blocks are listed under ``CHAPTER Ck (Ba–Bz)`` headings when there are two chapters or more
    (a chapter list at the end put 3 chapter texts of 7 on the wrong blocks; grouped, 7 of 7).
    """
    heard = _prompt_sounds(video)
    passing = {h.label for h in heard}
    lines = _header(video, len(blocks), heard, weather_line, language)
    lines.append("")
    spans = list(chapters) or ([(blocks[0].no, blocks[-1].no)] if blocks else [])
    grouped = len(spans) >= 2
    if grouped:
        lines.append(f"BLOCKS in {len(spans)} chapters cut by the application (time order)")
    else:
        lines.append("BLOCKS (time order)")
    for index, (first, last) in enumerate(spans, 1):
        if grouped:
            lines += ["", f"CHAPTER C{index} (B{first}–B{last})"]
        for block in blocks:
            if first <= block.no <= last:
                lines += _block_lines(video, block, usable, passing, language, compact=compact)
    return "\n".join(lines)


def _header(
    video: Video,
    block_count: int,
    heard: Sequence[HeardSound],
    weather_line: str | None,
    language: str,
) -> list[str]:
    lines = [f"{FILE_NAME_KEY} {clean_untrusted(video.filename)}"]
    duration = video.duration if math.isfinite(video.duration) and video.duration > 0 else 0.0
    size = [format_clock(duration)]
    if orientation := _label(ORIENTATION, language, video.orientation):
        size.append(orientation)
    size.append(f"{block_count} block{'' if block_count == 1 else 's'}")
    lines.append("Duration " + " · ".join(size))
    if (at := video.capture_local) is not None:
        lines.append(f"Capture: {at:%Y-%m-%d}, around {at:%H}:00 local time")
    if place := _place(video):
        lines.append(f"Place: {place}")
    if phase := _label(LIGHT_PHASE, language, video.light_phase):
        part = _label(DAY_PART, language, video.day_part)
        lines.append(f"Light: {phase}" + (f", {part}" if part else ""))
    if weather_line and (weather := clean_untrusted(weather_line)):
        lines.append(f"Weather: {weather}")
    lines.append("Sound: " + _sound_line(video, heard, language))
    lines.append(_speech_line(video))
    return lines


def _place(video: Video) -> str:
    label = clean_untrusted(video.place_label or "")
    feature = clean_untrusted(video.place_feature or "")
    if label and feature:
        return f"{label} (near {feature})"
    return label or (f"near {feature}" if feature else "")


def _speech_line(video: Video) -> str:
    if not video.segments:
        return "Speech: none"
    seconds = sum(max(0.0, s.end - s.start) for s in video.segments)
    language = f"language {video.transcript_language}, " if video.transcript_language else ""
    return f"Speech: {language}about {seconds:.0f} s"


def _label(table: Mapping[str, Mapping[str, str]], language: str, key: str | None) -> str | None:
    if not key:
        return None
    return table.get(language, table["en"]).get(key) or key.replace("_", " ")


def _block_lines(
    video: Video,
    block: Block,
    usable: Mapping[int, int],
    passing: set[str],
    language: str,
    *,
    compact: bool,
) -> list[str]:
    """``B7 · 23 s · gros plan · usable 82``, then its descriptions, sounds and speech."""
    seconds = block.duration if math.isfinite(block.duration) else 0.0  # round(NaN) raises
    head = [f"B{block.no}", f"{max(1, round(seconds))} s"]
    if framing := _framing(block.frames, language):
        head.append(framing)
    if (score := usable.get(block.no)) is not None:
        head.append(f"usable {score}")
    line = " · ".join(head)
    captions = [
        caption + (f" (×{count})" if count > 1 else "")
        for caption, count in distinct_captions(block.frames, language=language)
    ]
    if compact and captions:
        others = len(captions) - 1
        line += f" | {captions[0]}"
        if others:
            line += f" (+{others} other view{'s' if others > 1 else ''})"
    out = [line] if compact else [line, *(f"  {caption}" for caption in captions)]
    heard = dict.fromkeys(sound_label(h, language) for h in block.heard if h in passing)
    if heard:
        out.append("  sounds: " + ", ".join(heard))
    said = clean_untrusted(speech_text(video.segments, block.start, block.end))
    if said:
        if compact:
            said = _clip(said, COMPACT_SPEECH_CHARS)
        out.append(f"  speech: <untrusted>{said}</untrusted>")
    return out


def _framing(frames: Iterable[Frame], language: str) -> str | None:
    """The framing most keyframes of the block show (an unknown one is not written)."""
    votes = Counter(
        str(f.data.get("shot_type"))
        for f in frames
        if f.data and f.data.get("shot_type") not in {None, "", "unknown"}
    )
    if not votes:
        return None
    return _label(FRAMING, language, votes.most_common(1)[0][0])


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "…"


# ---------------------------------------------------------------- sentence case
_MID_SENTENCE = re.compile(r"(?<=[\w,;'’)] )[^\W\d_][\w-]*")
_WORDS = re.compile(r"[^\W\d_][\w-]*")
_FILE_WORDS = re.compile(r"[^\W\d_]{3,}")
_EDGE_PUNCTUATION = ',.:;!?«»"()“”…'
_SPACES = re.compile(r"(\s+)")


def proper_nouns(rendered_input: str, filename: str, place_label: str | None) -> set[str]:
    """The capitalised words of the inputs that are names: written with a capital in the middle
    of a sentence (file-name line excluded), a word of the place label, or a word of the file
    name that the inputs never write in lower case (« RIZ.mp4 » is not a name: « le riz » is
    said)."""
    body = "\n".join(
        line for line in rendered_input.splitlines() if not line.startswith(FILE_NAME_KEY)
    )
    proper = {w for w in _MID_SENTENCE.findall(body) if w[0].isupper()}
    proper |= {w for w in _WORDS.findall(place_label or "") if w[0].isupper()}
    lower = {w for w in _WORDS.findall(body) if w[0].islower()}
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    proper |= {w.capitalize() for w in _FILE_WORDS.findall(stem) if w.lower() not in lower}
    return proper


def sentence_case(text: str, proper: set[str]) -> str:
    """French sentence case: every capitalised word after the first is lowered unless it is a
    proper noun of the inputs, an acronym (« IA ») or CamelCase (« PowerPoint »). After an
    apostrophe the part that follows is tested (« d'Époque » → « d'époque »). The first word is
    the first with a letter or digit: an opening « or dash is not one (an earlier rule lowered
    the « Le » that followed it). It gets its capital when the model left it out (« chenille en
    suspension »), unless it has one inside (« iPhone »). A name of the inputs counts in the
    singular and the plural, with or without accents (« HIMALAYAS » and « NEPAL » in the file
    name, « Porte de l'Himalaya » and « Népal » written).

    Measured: 20 of 20 residual Title-Case titles fixed (« Coucher de Soleil au lac »), with
    Hyères, Himalayas, Gorkhas and IA kept.
    """
    parts = _SPACES.split(text)
    first = True
    for index, part in enumerate(parts):
        if not any(c.isalnum() for c in part):
            continue
        if first:
            first = False
            parts[index] = _capitalised(part)
            continue
        core = part.strip(_EDGE_PUNCTUATION)
        word = core.replace("’", "'").rpartition("'")[2]
        if _lowerable(word, proper):
            parts[index] = part.replace(word, word.lower(), 1)
    return "".join(parts)


def _lowerable(word: str, proper: set[str]) -> bool:
    return (
        word[:1].isupper()
        and not word.isupper()
        and not any(c.isupper() for c in word[1:])
        and not {_fold(w) for w in (word, f"{word}s", word.removesuffix("s"))}
        & {_fold(name) for name in proper}
    )


def _fold(word: str) -> str:
    """Without accents: « NEPAL » in a file name names the « Népal » a title writes."""
    return "".join(c for c in unicodedata.normalize("NFKD", word) if not unicodedata.combining(c))


def _capitalised(word: str) -> str:
    if any(c.isupper() for c in word):
        return word
    for index, char in enumerate(word):
        if char.isalpha():
            return word[:index] + char.upper() + word[index + 1 :]
        if char.isdigit():
            break
    return word


# ---------------------------------------------------------------- block references
_FRENCH_VERBS = frozenset({
    "montre", "présente", "offre", "capture", "contient", "illustre", "dévoile", "révèle",
    "met", "permet", "est", "sert", "donne", "propose", "se",
})  # fmt: skip
_ENGLISH_VERBS = frozenset(
    {"shows", "features", "captures", "offers", "presents", "contains", "reveals", "is", "gives"}
)
_LEADING_REFERENCE = re.compile(
    r"^\s*(?:(?:(?:le|ce|the|this)\s+)?(?:bloc|block)(?P<no>\s+B?\d+)?|\(?(?P<ref>B\d+)\)?)"
    r"(?![\w'’])",
    re.IGNORECASE,
)
_TRAILING_REFERENCE = re.compile(
    r"(?:\s*[,;:–—-])?\s*(?:\b(?:dans|du|au|en|in)\s+)?(?:\b(?:le|ce|the)\s+)?"
    r"\(?(?:\b(?:bloc|block)\s+)?\bB\d+\)?(?=\s*[.!?…]?\s*$)",
    re.IGNORECASE,
)
# « (B3) », « (bloc 4) », and lists: « (B3, B4) », « (blocs B3 et B4) », « (B3–B5) ».
_INNER_REFERENCE = re.compile(
    r"\s*\((?:(?:blocs?|blocks?)\s+B?\d+|B\d+)"
    r"(?:\s*(?:[,;&/–—-]|et|and|à|to)\s*B?\d+)*\)",
    re.IGNORECASE,
)
_LEAD_PUNCTUATION = " \t:,;.–—-"
# « Le bloc : une main… » is a reference even without a number; « Bloc-notes » is not.
_SEPARATED = re.compile(r"\s*[:,;]|\s+[–—-]\s")


def strip_block_reference(text: str) -> str:
    """A moment's reason without the block it was asked about: the model opened 7 reasons of 17
    with « Le bloc montre… » or « Bloc B4… ». « Bloc B4 : une main… » → « Une main… »,
    « Le bloc montre… » → « Ce plan montre… » (a sentence, where cutting the words would leave
    « Montre… »), and a « (B3) », « (B3, B4) » or a closing « dans le bloc B3 » is dropped.
    « Bloc de pierre… » (no number, verb or colon after it) is left alone."""
    text = _INNER_REFERENCE.sub("", text.strip())
    text = _TRAILING_REFERENCE.sub("", text).strip()
    if not any(c.isalnum() for c in text):
        return ""
    match = _LEADING_REFERENCE.match(text)
    if match is None:
        return text
    after = text[match.end() :]
    rest = after.lstrip(_LEAD_PUNCTUATION)
    verb = rest.split(" ", 1)[0].strip(",;:.!?…").lower()  # « Le bloc montre, en gros plan… »
    if verb in _FRENCH_VERBS:
        return f"Ce plan {rest}"
    if verb in _ENGLISH_VERBS:
        return f"This shot {rest}"
    if not (match["no"] or match["ref"] or _SEPARATED.match(after)):
        return text  # « Le bloc de granit… »: a real block
    return rest[:1].upper() + rest[1:]


# ---------------------------------------------------------------- absences
# qwen3-vl-4b states what is not there whatever the prompt says: with « never state what is
# missing » (prompt v2), 9 summaries of 26 still said « Aucun son ni parole n'est entendu »
# on a real library, one of them over a music track. An absence is no fact to find footage by.
_THING = (
    r"(?:sons?|bruits?|paroles?|voix|dialogues?|discours|commentaires?|narrations?|musiques?"
    r"|personnages?|personnes?|activité humaine|présence humaine"
    r"|sounds?|noises?|speech|voices?|talking|narration|music|people)"
)
_OF = r"(?:de\s+|d['’]|des\s+)?"
_NONE = (
    rf"(?:aucun(?:e)?\s+|pas\s+{_OF}|il\s+n['’]y\s+a\s+(?:pas\s+{_OF}|aucun(?:e)?\s+)"
    rf"|on\s+n['’]entend\s+(?:pas\s+{_OF}|aucun(?:e)?\s+)|no\s+|there\s+(?:is|are)\s+no\s+)"
)
_ABSENCE = re.compile(rf"{_NONE}{_THING}\b", re.IGNORECASE)
_ABSENCE_CLAUSE = re.compile(rf",\s*(?:mais|et|but|and)\s+{_NONE}{_THING}\b[^.!?…]*", re.IGNORECASE)
# « … sans aucun son ni parole. » only at the end of a sentence: « glisse sans bruit sur l'eau »
# says how the boat glides.
_WITHOUT = re.compile(
    rf",?\s+(?:sans|without)\s+(?:aucun(?:e)?\s+|any\s+)?{_THING}"
    rf"(?:\s+(?:ni|ou|et|or|nor|and)\s+{_OF}{_THING})*(?=\s*[.!?…]?\s*$)",
    re.IGNORECASE,
)
_BUT = re.compile(r",\s+(?:mais|but)\s+", re.IGNORECASE)
_SENTENCE_BREAK = re.compile(r"(?<=[.!?…])\s+")


def strip_absences(text: str) -> str:
    """The text without what it says is missing: « Aucune parole n'est entendue. » goes;
    « Aucun son humain n'est entendu, mais le lac murmure. » keeps « Le lac murmure. »; « …,
    mais il n'y a pas de parole » and a closing « sans aucun son ni parole » leave their
    sentence. A text made only of absences stays as it is: never an empty summary."""
    kept: list[str] = []
    for written in _SENTENCE_BREAK.split(text.strip()):
        sentence = written
        if _ABSENCE.match(written):
            parts = _BUT.split(written, maxsplit=1)
            if len(parts) < 2 or _ABSENCE.match(parts[1]):
                continue
            sentence = parts[1][:1].upper() + parts[1][1:]
        sentence = _WITHOUT.sub("", _ABSENCE_CLAUSE.sub("", sentence)).strip()
        if sentence:
            kept.append(sentence)
    return " ".join(kept) if kept else text.strip()


# ---------------------------------------------------------------- tags
# The stage names the sounds with ``sound_name``: its ambiguous names become the prompt's.
_SOUND_TAGS: Mapping[str, str] = {
    sound_name(label, "fr").lower(): name for label, name in OWN_SOUND_NAMES["fr"].items()
}
_PARENTHESES = re.compile(r"\([^)]*\)")


def merge_tags(
    llm_tags: Sequence[str], frames: Sequence[Frame], sounds: Sequence[str], limit: int = TAG_LIMIT
) -> list[tuple[str, str]]:
    """The tags of a video with their source (``llm``, ``frames``, ``sounds``): the model's, then
    those the vision model gave to at least 20 % of the described keyframes (2 or more once there
    are 4), then the sounds that passed the gate. Lower case, « _ » as a space, no duplicate, no
    generic word (« extérieur », « jour »…). Frame tags alone are generic (« nature »); the model
    adds those only speech and sounds carry (« plantain », « recette », « parking »)."""
    merged: dict[str, str] = {}
    candidates = [
        *((tag, "llm") for tag in llm_tags),
        *((tag, "frames") for tag in _frame_tags(frames)),
        *((_SOUND_TAGS.get(name.strip().lower(), name), "sounds") for name in sounds),
    ]
    for tag, source in candidates:
        if len(merged) >= limit:
            break
        normal = _normal_tag(tag)
        if normal and normal not in GENERIC_TAGS:
            merged.setdefault(normal, source)
    return list(merged.items())


def _frame_tags(frames: Sequence[Frame]) -> list[str]:
    described = [f.data for f in frames if f.data]
    counts = Counter(
        tag
        for data in described
        for tag in dict.fromkeys(_normal_tag(str(t)) for t in data.get("tags") or [])
        if tag
    )
    need = 1
    if len(described) >= FRAME_TAG_MIN_FRAMES:
        need = max(FRAME_TAG_MIN, math.ceil(FRAME_TAG_SHARE * len(described)))
    return [tag for tag, n in counts.most_common() if n >= need]


def _normal_tag(tag: str) -> str:
    text = _PARENTHESES.sub(" ", tag.replace("_", " ").lower())
    return " ".join(text.split()).strip(" #.,;:!?«»\"'")
