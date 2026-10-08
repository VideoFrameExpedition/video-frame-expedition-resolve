"""Compiled files Windows refuses to load.

Smart App Control, on by default on a new Windows 11, lets an unsigned file run only when
Microsoft's cloud knows it well enough. A rarely seen extension module (``.pyd``) or library
(``.dll``) is refused, and Python then fails the whole import, even when the package ships the same
module in plain Python next to it (SQLAlchemy does). Setting the refused file aside lets Python take
the plain module: slower, same behaviour. When nothing can stand in for it, the refusal is said
plainly: which file, of which package. ``scan`` checks every compiled file of the application up
front (``vfe doctor --binaries``, run at the end of the installation). A refused file is never run.
"""

from __future__ import annotations

import ctypes
import os
import platform
import re
import sys
from collections import deque
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

POLICY_VIOLATION = 4551  # ERROR_SYSTEM_INTEGRITY_POLICY_VIOLATION
ASIDE_SUFFIX = ".refused"
REFUSED_EXIT = 5  # exit status of a command stopped by a refused file
COMPILED = frozenset({".pyd", ".dll"})
APPLICATION = "vfe-vision"
PROBE_THREADS = 8
# Maps the file as Windows would to run it, so that its code integrity is checked, but runs none of
# its code (no DllMain) and loads none of the libraries it needs.
_DONT_RESOLVE_DLL_REFERENCES = 0x00000001
# Cython's « pure Python mode » sources, which PyAV ships next to its extensions, need Cython.
_CYTHON_SOURCE = re.compile(r"^\s*(?:import|from)\s+cython\b", re.MULTILINE)
# Windows' sentence for a refusal (it ends the failed import's message), in English and French;
# the system's own wording is looked up too.
_SENTENCES = (
    "Application Control policy has blocked this file",
    "contrôle d’application a bloqué ce fichier",
    "contrôle d'application a bloqué ce fichier",
)
HINT = (
    "Smart App Control (Windows) bloque un fichier non signé que Microsoft ne connaît pas encore "
    "assez ; cela ne veut pas dire qu'il est dangereux, et son verdict peut changer : relancez "
    "plus tard, ou mettez l'application à jour. Réinstaller redonne le même fichier, et une "
    "exclusion de l'antivirus n'a aucun effet sur Smart App Control."
)
# Packages that go on without their compiled part, each one tried with that part refused.
# Keys: package names as « pip » writes them, lower case.
DO_WITHOUT = {
    "h3": "sans fuseaux horaires ni frontières pour les lieux hors ligne",
    "hf-xet": "téléchargements des modèles par HTTP",
    "httptools": "serveur web en Python (h11)",
    "watchfiles": "sans effet (rechargement à chaud, inutilisé)",
    "markupsafe": "version en Python",
    "pyyaml": "version en Python",
    "websockets": "version en Python",
}


@dataclass(frozen=True, slots=True)
class Refused:
    """A compiled file Windows refuses, and the package it comes with."""

    path: Path
    package: str | None  # "h3 4.5.0"; None when no installed package lists the file


def refused_by_policy(path: Path) -> bool:
    """Whether Windows' application control refuses to load this file (elsewhere: never)."""
    if sys.platform == "win32":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.LoadLibraryExW.restype = ctypes.c_void_p
        kernel32.LoadLibraryExW.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_uint32]
        kernel32.FreeLibrary.argtypes = [ctypes.c_void_p]
        handle = kernel32.LoadLibraryExW(str(path), None, _DONT_RESOLVE_DLL_REFERENCES)
        if handle:
            kernel32.FreeLibrary(handle)
            return False
        return ctypes.get_last_error() == POLICY_VIOLATION
    else:
        return False


def _policy_sentence() -> str | None:
    """Windows' own words for a refusal, in its language."""
    if sys.platform == "win32":
        return ctypes.FormatError(POLICY_VIOLATION).strip().rstrip(".") or None
    else:
        return None


def refusal_in(text: str) -> bool:
    """Whether this message (a failed import, a child process' errors) reports a refusal."""
    own = _policy_sentence()
    return any(sentence in text for sentence in (*_SENTENCES, *([own] if own else [])))


def is_refusal(
    error: BaseException, *, refused: Callable[[Path], bool] = refused_by_policy
) -> bool:
    """Whether this error is Windows refusing a file: a program that cannot start, or an import
    whose extension (or a library it needs) is refused."""
    if isinstance(error, OSError) and getattr(error, "winerror", None) == POLICY_VIOLATION:
        return True
    if not isinstance(error, ImportError):
        return False
    if refusal_in(str(error)):
        return True
    path = _extension(error)
    return path is not None and refused(path)


def describe(error: BaseException) -> str | None:
    """A refusal said plainly, for a failed analysis or a check; None for any other error."""
    if not is_refusal(error):
        return None
    if isinstance(error, ImportError):
        found = ", ".join(named(refused) for refused in refused_behind(error))
    else:
        found = str(getattr(error, "filename", None) or "")
    return (
        "Windows (Smart App Control) refuse un fichier de l'application"
        + (f" : {found}" if found else f" ({error})")
        + f". {HINT}"
    )


def named(refused: Refused) -> str:
    """``memory.cp312-win_amd64.pyd (h3 4.5.0)``."""
    return f"{refused.path.name} ({refused.package})" if refused.package else str(refused.path)


def consequence(refused: Refused) -> str | None:
    """How the application goes on without this file; None when it cannot."""
    if refused.path.suffix.lower() == ".pyd" and plain_twin(refused.path) is not None:
        return "sa version en Python le remplace au lancement suivant"
    name = refused.package.rsplit(" ", 1)[0].lower() if refused.package else ""
    return DO_WITHOUT.get(name)


def _extension(error: ImportError) -> Path | None:
    path = Path(error.path) if error.path else None
    if path is None or path.suffix.lower() != ".pyd" or not path.is_file():
        return None
    return path


def plain_twin(path: Path) -> Path | None:
    """The same module in plain Python next to a compiled one: ``_util_cy.py`` for
    ``_util_cy.cp312-win_amd64.pyd``. Not a Cython source, which only Cython can run."""
    twin = path.with_name(path.name.split(".", 1)[0] + ".py")
    if not twin.is_file():
        return None
    try:
        source = twin.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return None if _CYTHON_SOURCE.search(source) else twin


def set_aside(
    error: ImportError, *, refused: Callable[[Path], bool] = refused_by_policy
) -> Path | None:
    """After a failed import: when what failed is a compiled extension Windows refuses, and the
    package has it in plain Python too, rename the extension so that the next import takes the
    plain module. Returns the file as renamed; None when the error is something else (a
    missing module, an extension without a twin) and stands."""
    path = _extension(error)
    if path is None:
        return None
    if plain_twin(path) is None or not refused(path):
        return None
    aside = path.with_name(path.name + ASIDE_SUFFIX)
    try:
        aside.unlink(missing_ok=True)
        path.rename(aside)
    except OSError:
        return None
    return aside


def refused_behind(
    error: ImportError, *, refused: Callable[[Path], bool] = refused_by_policy
) -> list[Refused]:
    """The files Windows refuses behind a failed import: the extension itself, or one of the
    libraries its package ships (``av.libs``, ``numpy.libs``…). Empty when none is found."""
    path = _extension(error)
    owner = _owner(path) if path is not None else None
    candidates = list(compiled_files(owner)) if owner is not None else []
    if path is not None and path not in candidates:
        candidates.insert(0, path)
    if owner is not None:
        label: str | None = _label(owner)
    elif path is not None and _key(path) in {_key(file) for file in interpreter_files()}:
        label = f"Python {platform.python_version()}"  # its standard library (_sqlite3.pyd…)
    else:
        label = None
    return [Refused(file, label) for file in _probe_all(candidates, refused)]


def _owner(path: Path) -> metadata.Distribution | None:
    """The installed package whose files include this one (the failed import only gives the
    module's short name)."""
    target = _key(path)
    for distribution in metadata.distributions():
        for file in distribution.files or []:
            if file.suffix.lower() in COMPILED and _key(Path(str(file.locate()))) == target:
                return distribution
    return None


def _key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path))


def _label(distribution: metadata.Distribution) -> str:
    return f"{distribution.metadata['Name']} {distribution.version}"


def compiled_files(distribution: metadata.Distribution) -> Iterable[Path]:
    """The extensions and libraries a package installed next to its modules. Its console
    launchers (``Scripts\\*.exe``) are left out: the application runs nothing through them."""
    for file in distribution.files or []:
        if file.suffix.lower() in COMPILED and not str(file).startswith(".."):
            located = Path(str(file.locate()))
            if located.is_file():
                yield located


def runtime_distributions(root: str = APPLICATION) -> list[metadata.Distribution]:
    """The installed packages the application runs with: its requirements and theirs, with
    the extras they ask for (development tools are not among them)."""
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    found: dict[str, metadata.Distribution] = {}
    extras_seen: dict[str, frozenset[str]] = {}
    queue: deque[tuple[str, frozenset[str]]] = deque([(root, frozenset())])
    while queue:
        name, extras = queue.popleft()
        key = canonicalize_name(name)
        if key in extras_seen and extras <= extras_seen[key]:
            continue
        extras_seen[key] = extras_seen.get(key, frozenset()) | extras
        try:
            distribution = found.get(key) or metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue  # a requirement for another system (pywin32 on macOS, uvloop on Windows)
        found[key] = distribution
        for text in distribution.requires or []:
            requirement = Requirement(text)
            marker = requirement.marker
            if marker is None or any(marker.evaluate({"extra": e}) for e in {"", *extras}):
                queue.append((requirement.name, frozenset(requirement.extras)))
    return [found[key] for key in sorted(found)]


def interpreter_files(base: Path | None = None) -> list[Path]:
    """The compiled files of the Python the application runs on (its DLLs, the standard
    library's extensions): signed when it comes from python.org, not when uv downloaded it."""
    root = Path(sys.base_prefix) if base is None else base
    files = [*root.glob("*.dll"), *(root / "DLLs").glob("*.pyd"), *(root / "DLLs").glob("*.dll")]
    return sorted(file for file in files if not file.name.startswith("_test"))


def scan(
    distributions: Iterable[metadata.Distribution] | None = None,
    *,
    refused: Callable[[Path], bool] = refused_by_policy,
) -> tuple[int, list[Refused]]:
    """Ask Windows about every compiled file of the application (by default: its packages and
    its Python). Returns how many files were checked and the refused ones. Elsewhere than on
    Windows, nothing is ever refused."""
    pairs = [
        (file, _label(distribution))
        for distribution in (runtime_distributions() if distributions is None else distributions)
        for file in compiled_files(distribution)
    ]
    if distributions is None:
        python = f"Python {platform.python_version()}"
        pairs += [(file, python) for file in interpreter_files()]
    labels = dict(pairs)
    found = _probe_all([file for file, _label_ in pairs], refused)
    return len(pairs), [Refused(file, labels[file]) for file in found]


def _probe_all(files: list[Path], refused: Callable[[Path], bool]) -> list[Path]:
    """The refused ones among these files, in their order (Windows checks a large file in a
    fraction of a second: several at a time)."""
    if not files:
        return []
    with ThreadPoolExecutor(min(PROBE_THREADS, len(files))) as pool:
        verdicts = list(pool.map(refused, files))
    return [file for file, verdict in zip(files, verdicts, strict=True) if verdict]
