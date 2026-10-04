"""Pre-commit hook: refuse files larger than 1 MiB (videos and model weights stay out of git)."""

from __future__ import annotations

import sys
from pathlib import Path

LIMIT_BYTES = 1024 * 1024


def main(paths: list[str]) -> int:
    too_big = [
        p for p in paths if Path(p).is_file() and Path(p).stat().st_size > LIMIT_BYTES
    ]
    for path in too_big:
        print(f"Fichier trop volumineux pour git (> 1 Mo) : {path}")
    return 1 if too_big else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
