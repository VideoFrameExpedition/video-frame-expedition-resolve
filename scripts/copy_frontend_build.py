"""Copy the production frontend build into the backend package (served by `vfe serve`).

The copy keeps a stamp of the sources it was built from (``.sources``: their SHA-256). With
``--up-to-date``, the script only says whether the interface in the package was built from the
sources as they are now (exit code 0): the launchers build it again when they changed since,
after an update (``git pull``), instead of serving the previous one.
"""

from __future__ import annotations

import hashlib
import shutil
import sys
from pathlib import Path

from vfe_vision.core.language import tr

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
SOURCE = FRONTEND / "dist"
TARGET = ROOT / "backend" / "src" / "vfe_vision" / "web" / "dist"
STAMP = ".sources"  # in the copy: the SHA-256 of the sources it was built from
SOURCE_DIRS = ("src", "public")  # the code, the static files (the help included)
SOURCE_FILES = ("index.html", "package.json", "pnpm-lock.yaml", "vite.config.ts")


def sources_digest(frontend: Path = FRONTEND) -> str:
    """The SHA-256 of what the interface is built from: its code, its static files and its
    configuration (not what a build writes, nor its dependencies)."""
    files = [path for name in SOURCE_DIRS for path in (frontend / name).rglob("*")]
    files += [frontend / name for name in SOURCE_FILES]
    files += frontend.glob("tsconfig*.json")
    digest = hashlib.sha256()
    for path in sorted((p for p in files if p.is_file()), key=lambda p: p.as_posix()):
        digest.update(path.relative_to(frontend).as_posix().encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def up_to_date(target: Path = TARGET, frontend: Path = FRONTEND) -> bool:
    """The interface in the package was built from the sources as they are now."""
    try:
        built = (target / STAMP).read_text(encoding="ascii").strip()
    except OSError:  # never built, or by a version that kept no stamp
        return False
    return (target / "index.html").is_file() and built == sources_digest(frontend)


def copy_build(
    source: Path = SOURCE, target: Path = TARGET, frontend: Path = FRONTEND
) -> None:
    """The build copied into the package, with the stamp of its sources."""
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)
    (target / STAMP).write_text(sources_digest(frontend) + "\n", encoding="ascii")


def main() -> None:
    if "--up-to-date" in sys.argv[1:]:
        if up_to_date():
            return
        print(
            tr(
                "L'interface web a changé depuis sa dernière construction : elle est reconstruite.",
                "The web interface changed since it was last built: it is built again.",
            )
        )
        raise SystemExit(1)
    if not (SOURCE / "index.html").is_file():
        raise SystemExit(
            tr(
                "Build introuvable : lancez d'abord `pnpm --dir frontend run build`.",
                "Build not found: first run `pnpm --dir frontend run build`.",
            )
        )
    copy_build()
    print(tr(f"Frontend copié vers {TARGET}", f"Frontend copied to {TARGET}"))


if __name__ == "__main__":
    main()
