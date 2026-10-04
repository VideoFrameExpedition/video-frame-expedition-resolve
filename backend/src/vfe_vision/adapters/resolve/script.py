"""The fixed Resolve script, and the only way data goes into it.

``apply_payload_v1.py`` is a real file of the package, read as text and never generated. The
payload replaces one line of it, ``PAYLOAD = json.loads("{}")``, by the same line holding the
payload as a JSON string literal: ``json.dumps`` (ASCII only) then ``repr``, which escapes every
quote, backslash and line break. Whatever the footage or a model wrote stays data.

The script is pure ASCII, payload included: Resolve 21.1's ``run_script`` (Windows) loses the
non-ASCII characters of a script's text, and an accented file path then matches nothing.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from functools import cache
from pathlib import Path
from typing import Any

SCRIPT_FORMAT = "vfe-vision-resolve"
SCRIPT_VERSION = 1
SCRIPT_FILE = "apply_payload_v1.py"
PLACEHOLDER = 'PAYLOAD = json.loads("{}")'


@cache
def script_template() -> str:
    """The script as shipped (checked: ASCII, one placeholder, the expected version)."""
    text = (Path(__file__).parent / SCRIPT_FILE).read_text(encoding="utf-8")
    version = f"SCRIPT_VERSION = {SCRIPT_VERSION}\n"
    if not text.isascii() or text.count(PLACEHOLDER) != 1 or version not in text:
        raise RuntimeError(f"script Resolve inattendu : {SCRIPT_FILE}")
    return text


def render_script(payload: Mapping[str, Any]) -> str:
    """The script with ``payload`` as its data (a JSON string literal, nothing else)."""
    data = json.dumps(payload, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    return script_template().replace(PLACEHOLDER, f"PAYLOAD = json.loads({data!r})", 1)
