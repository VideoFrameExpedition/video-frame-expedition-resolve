"""Copy the production frontend build into the backend package (served by `vfe serve`)."""

from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "frontend" / "dist"
TARGET = ROOT / "backend" / "src" / "vfe_vision" / "web" / "dist"


def main() -> None:
    if not (SOURCE / "index.html").is_file():
        raise SystemExit(
            "Build introuvable : lancez d'abord `pnpm --dir frontend run build`."
        )
    if TARGET.exists():
        shutil.rmtree(TARGET)
    shutil.copytree(SOURCE, TARGET)
    print(f"Frontend copié vers {TARGET}")


if __name__ == "__main__":
    main()
