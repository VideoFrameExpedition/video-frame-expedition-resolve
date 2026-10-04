"""Spoken-language codes: container tags (ISO 639) to Whisper codes, and display names.

Container audio tags are unreliable (Samsung writes ``eng`` on a French speaker's rushes, Resolve
writes ``und``): they only label subtitle tracks and are never forced on Whisper. Whisper uses
ISO 639-1 codes plus ``haw`` (Hawaiian) and ``yue`` (Cantonese), and the obsolete ``jw`` for
Javanese. Static table, no dependency (no langcodes/pycountry/babel in the environment).
"""

from __future__ import annotations

from typing import Literal

# Whisper code: (ISO 639-2/B, ISO 639-2/T, English name, French name)
_TABLE: dict[str, tuple[str, str, str, str]] = {
    "af": ("afr", "afr", "Afrikaans", "afrikaans"),
    "am": ("amh", "amh", "Amharic", "amharique"),
    "ar": ("ara", "ara", "Arabic", "arabe"),
    "as": ("asm", "asm", "Assamese", "assamais"),
    "az": ("aze", "aze", "Azerbaijani", "azerbaïdjanais"),
    "ba": ("bak", "bak", "Bashkir", "bachkir"),
    "be": ("bel", "bel", "Belarusian", "biélorusse"),
    "bg": ("bul", "bul", "Bulgarian", "bulgare"),
    "bn": ("ben", "ben", "Bengali", "bengali"),
    "bo": ("tib", "bod", "Tibetan", "tibétain"),
    "br": ("bre", "bre", "Breton", "breton"),
    "bs": ("bos", "bos", "Bosnian", "bosnien"),
    "ca": ("cat", "cat", "Catalan", "catalan"),
    "cs": ("cze", "ces", "Czech", "tchèque"),
    "cy": ("wel", "cym", "Welsh", "gallois"),
    "da": ("dan", "dan", "Danish", "danois"),
    "de": ("ger", "deu", "German", "allemand"),
    "el": ("gre", "ell", "Greek", "grec"),
    "en": ("eng", "eng", "English", "anglais"),
    "es": ("spa", "spa", "Spanish", "espagnol"),
    "et": ("est", "est", "Estonian", "estonien"),
    "eu": ("baq", "eus", "Basque", "basque"),
    "fa": ("per", "fas", "Persian", "persan"),
    "fi": ("fin", "fin", "Finnish", "finnois"),
    "fo": ("fao", "fao", "Faroese", "féroïen"),
    "fr": ("fre", "fra", "French", "français"),
    "gl": ("glg", "glg", "Galician", "galicien"),
    "gu": ("guj", "guj", "Gujarati", "gujarati"),
    "ha": ("hau", "hau", "Hausa", "haoussa"),
    "haw": ("haw", "haw", "Hawaiian", "hawaïen"),
    "he": ("heb", "heb", "Hebrew", "hébreu"),
    "hi": ("hin", "hin", "Hindi", "hindi"),
    "hr": ("hrv", "hrv", "Croatian", "croate"),
    "ht": ("hat", "hat", "Haitian Creole", "créole haïtien"),
    "hu": ("hun", "hun", "Hungarian", "hongrois"),
    "hy": ("arm", "hye", "Armenian", "arménien"),
    "id": ("ind", "ind", "Indonesian", "indonésien"),
    "is": ("ice", "isl", "Icelandic", "islandais"),
    "it": ("ita", "ita", "Italian", "italien"),
    "ja": ("jpn", "jpn", "Japanese", "japonais"),
    "jw": ("jav", "jav", "Javanese", "javanais"),
    "ka": ("geo", "kat", "Georgian", "géorgien"),
    "kk": ("kaz", "kaz", "Kazakh", "kazakh"),
    "km": ("khm", "khm", "Khmer", "khmer"),
    "kn": ("kan", "kan", "Kannada", "kannada"),
    "ko": ("kor", "kor", "Korean", "coréen"),
    "la": ("lat", "lat", "Latin", "latin"),
    "lb": ("ltz", "ltz", "Luxembourgish", "luxembourgeois"),
    "ln": ("lin", "lin", "Lingala", "lingala"),
    "lo": ("lao", "lao", "Lao", "lao"),
    "lt": ("lit", "lit", "Lithuanian", "lituanien"),
    "lv": ("lav", "lav", "Latvian", "letton"),
    "mg": ("mlg", "mlg", "Malagasy", "malgache"),
    "mi": ("mao", "mri", "Maori", "maori"),
    "mk": ("mac", "mkd", "Macedonian", "macédonien"),
    "ml": ("mal", "mal", "Malayalam", "malayalam"),
    "mn": ("mon", "mon", "Mongolian", "mongol"),
    "mr": ("mar", "mar", "Marathi", "marathi"),
    "ms": ("may", "msa", "Malay", "malais"),
    "mt": ("mlt", "mlt", "Maltese", "maltais"),
    "my": ("bur", "mya", "Burmese", "birman"),
    "ne": ("nep", "nep", "Nepali", "népalais"),
    "nl": ("dut", "nld", "Dutch", "néerlandais"),
    "nn": ("nno", "nno", "Norwegian Nynorsk", "norvégien nynorsk"),
    "no": ("nor", "nor", "Norwegian", "norvégien"),
    "oc": ("oci", "oci", "Occitan", "occitan"),
    "pa": ("pan", "pan", "Punjabi", "pendjabi"),
    "pl": ("pol", "pol", "Polish", "polonais"),
    "ps": ("pus", "pus", "Pashto", "pachto"),
    "pt": ("por", "por", "Portuguese", "portugais"),
    "ro": ("rum", "ron", "Romanian", "roumain"),
    "ru": ("rus", "rus", "Russian", "russe"),
    "sa": ("san", "san", "Sanskrit", "sanskrit"),
    "sd": ("snd", "snd", "Sindhi", "sindhi"),
    "si": ("sin", "sin", "Sinhala", "cingalais"),
    "sk": ("slo", "slk", "Slovak", "slovaque"),
    "sl": ("slv", "slv", "Slovenian", "slovène"),
    "sn": ("sna", "sna", "Shona", "shona"),
    "so": ("som", "som", "Somali", "somali"),
    "sq": ("alb", "sqi", "Albanian", "albanais"),
    "sr": ("srp", "srp", "Serbian", "serbe"),
    "su": ("sun", "sun", "Sundanese", "soundanais"),
    "sv": ("swe", "swe", "Swedish", "suédois"),
    "sw": ("swa", "swa", "Swahili", "swahili"),
    "ta": ("tam", "tam", "Tamil", "tamoul"),
    "te": ("tel", "tel", "Telugu", "télougou"),
    "tg": ("tgk", "tgk", "Tajik", "tadjik"),
    "th": ("tha", "tha", "Thai", "thaï"),
    "tk": ("tuk", "tuk", "Turkmen", "turkmène"),
    "tl": ("tgl", "tgl", "Tagalog", "tagalog"),
    "tr": ("tur", "tur", "Turkish", "turc"),
    "tt": ("tat", "tat", "Tatar", "tatar"),
    "uk": ("ukr", "ukr", "Ukrainian", "ukrainien"),
    "ur": ("urd", "urd", "Urdu", "ourdou"),
    "uz": ("uzb", "uzb", "Uzbek", "ouzbek"),
    "vi": ("vie", "vie", "Vietnamese", "vietnamien"),
    "yi": ("yid", "yid", "Yiddish", "yiddish"),
    "yo": ("yor", "yor", "Yoruba", "yoruba"),
    "zh": ("chi", "zho", "Chinese", "chinois"),
    "yue": ("yue", "yue", "Cantonese", "cantonais"),
}

WHISPER_LANGUAGES: tuple[str, ...] = tuple(_TABLE)

# Tags that say « no particular language »: undetermined, uncoded, multiple, no linguistic content.
NO_LANGUAGE = frozenset({"und", "mis", "mul", "zxx"})

# Aliases outside the two ISO 639-2 columns: ISO 639-1 codes Whisper spells differently, retired
# codes still written by old muxers, and common ISO 639-3 individual languages.
_ALIASES: dict[str, str] = {
    "jv": "jw",
    "iw": "he",
    "in": "id",
    "ji": "yi",
    "mo": "ro",
    "mol": "ro",
    "sh": "sr",
    "scc": "sr",
    "scr": "hr",
    "nb": "no",
    "nob": "no",
    "fil": "tl",
    "cmn": "zh",
    "swh": "sw",
    "pes": "fa",
    "zsm": "ms",
    "arb": "ar",
}

_BY_ISO: dict[str, str] = {
    iso: code for code, (bib, term, _en, _fr) in _TABLE.items() for iso in (bib, term, code)
} | _ALIASES


def whisper_code(tag: str | None) -> str | None:
    """Whisper code for a language tag (``fre``, ``fra``, ``fr``, ``fr-FR``, ``pt_BR``…).

    ``None`` for missing, undetermined (``und``, ``mis``, ``mul``, ``zxx``), local-use
    (``qaa``–``qtz``) and unknown tags: the caller then lets Whisper detect the language.
    """
    if not tag:
        return None
    base = tag.strip().replace("_", "-").split("-", 1)[0].casefold()
    if not base or base in NO_LANGUAGE:
        return None
    if len(base) == 3 and "qaa" <= base <= "qtz":
        return None
    return _BY_ISO.get(base)


def language_name(code: str | None, locale: Literal["fr", "en"] = "fr") -> str | None:
    """Display name of a Whisper (or ISO 639) code; the code itself when unknown."""
    if not code:
        return None
    whisper = whisper_code(code)
    if whisper is None:
        return code
    _bib, _term, english, french = _TABLE[whisper]
    return french if locale == "fr" else english
