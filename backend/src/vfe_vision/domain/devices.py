"""Device names from model codes (Samsung ``SM-…``), without guessing what cannot be known.

Samsung model numbers cannot be decoded arithmetically (the S26 uses 942/947/948 where the S23-S25
used x1/x6/x8; the S25 Edge is S937; the Z Flip7 is F766 and the Flip7 FE F761). The device's own
3GPP ``auth`` box is preferred; otherwise a small table of verified codes; otherwise only the
family given by the series letter.
"""

from __future__ import annotations

import re

_SAMSUNG_CODE = re.compile(r"^SM-([A-Z])(\d{3})([A-Z0-9]{0,3})(/DS)?$")

# Base code (series letter + 3 digits) → marketing name; each entry verified on the device's own
# metadata or on Samsung/retailer spec pages.
SAMSUNG_MODELS: dict[str, str] = {
    "S911": "Galaxy S23",
    "S916": "Galaxy S23+",
    "S918": "Galaxy S23 Ultra",
    "S921": "Galaxy S24",
    "S926": "Galaxy S24+",
    "S928": "Galaxy S24 Ultra",
    "S931": "Galaxy S25",
    "S936": "Galaxy S25+",
    "S937": "Galaxy S25 Edge",
    "S938": "Galaxy S25 Ultra",
    "S731": "Galaxy S25 FE",
    "S942": "Galaxy S26",
    "S947": "Galaxy S26+",
    "S948": "Galaxy S26 Ultra",
    "F966": "Galaxy Z Fold7",
    "F766": "Galaxy Z Flip7",
    "F761": "Galaxy Z Flip7 FE",
}

SAMSUNG_FAMILIES: dict[str, str] = {
    "S": "Galaxy S",
    "G": "Galaxy",
    "N": "Galaxy Note",
    "F": "Galaxy Z",
    "A": "Galaxy A",
    "M": "Galaxy M",
    "E": "Galaxy F",
    "X": "Galaxy Tab",
    "T": "Galaxy Tab",
}


def samsung_model_name(code: str | None) -> str | None:
    """``SM-S948B`` → ``Galaxy S26 Ultra``; unknown ``SM-A136U`` → ``Galaxy A`` (family only)."""
    match = _SAMSUNG_CODE.match((code or "").strip().upper())
    if match is None:
        return None
    series, digits, suffix = match[1], match[2], match[3]
    prepaid = suffix.endswith("L")  # US prepaid (TracFone: DL, VL, BL) variants of other lines
    known = None if prepaid else SAMSUNG_MODELS.get(f"{series}{digits}")
    if known:
        return known
    # "S" is also the prefix of those prepaid Galaxy A/J variants (SM-S134DL, SM-S767VL): only
    # retail S7xx/S9xx numbers are Galaxy S phones.
    if series == "S" and (prepaid or digits[0] not in "79"):
        return None
    return SAMSUNG_FAMILIES.get(series)
