"""Installed models under ``<data>/models`` and their verified download.

Each model lives in ``<models>/<id>/`` with a ``MODEL.json`` manifest. A download goes to a
staging folder, every file is checked (size and sha256, computed while writing, interrupted
files resumed), and the folder only replaces the installed one once complete. Files taken out of
an archive (cuBLAS from NVIDIA's wheel) are extracted only after the archive's own sha256 is
checked, then checked again one by one; the archive is deleted afterwards.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

from vfe_vision import __version__
from vfe_vision.adapters.models.catalog import CATALOG, ModelFile, ModelSpec
from vfe_vision.core.atomic_io import atomic_write_text, replace_dir, replace_with_retry
from vfe_vision.core.cancel import CancelToken
from vfe_vision.core.errors import CancelledError, ServiceUnavailableError, VfeError

MANIFEST = "MODEL.json"
CHUNK = 1 << 20

Progress = Callable[[str, int, int], None]  # file name, bytes done, bytes total (model)


@dataclass(frozen=True, slots=True)
class ModelStatus:
    spec: ModelSpec
    path: Path
    installed: bool
    installed_at: str | None


class ModelStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, spec: ModelSpec) -> Path:
        return self.root.joinpath(*spec.id.split("/"))

    def installed(self, spec: ModelSpec) -> Path | None:
        """The model folder when it holds this exact revision (manifest and file sizes)."""
        folder = self.path(spec)
        try:
            manifest = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        expected = {f.name: f.sha256 for f in spec.files}
        recorded = {f.get("name"): f.get("sha256") for f in manifest.get("files", [])}
        if recorded != expected:
            return None  # another revision: the catalogue changed since
        for item in spec.files:
            target = folder / item.name
            if not target.is_file() or target.stat().st_size != item.size:
                return None
        return folder

    def identity(self, spec: ModelSpec) -> str | None:
        """What analyses record as "the model used" (id and short hash of its largest file)."""
        if self.installed(spec) is None:
            return None
        largest = max(spec.files, key=lambda f: f.size)
        return f"{spec.id}@{largest.sha256[:8]}"

    def statuses(self) -> list[ModelStatus]:
        result: list[ModelStatus] = []
        for spec in CATALOG:
            folder = self.installed(spec)
            when = None
            if folder is not None:
                try:
                    manifest = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
                    when = manifest.get("installed_at")
                except (OSError, ValueError):
                    when = None
            result.append(ModelStatus(spec, self.path(spec), folder is not None, when))
        return result

    def verify(self, spec: ModelSpec) -> list[str]:
        """Full sha256 check of an installed model; the problems found (empty: intact)."""
        folder = self.path(spec)
        problems = []
        for item in spec.files:
            target = folder / item.name
            if not target.is_file():
                problems.append(f"{item.name} : absent")
            elif _sha256(target) != item.sha256:
                problems.append(f"{item.name} : empreinte différente")
        return problems

    def download(
        self,
        spec: ModelSpec,
        *,
        transport: httpx.BaseTransport | None = None,
        progress: Progress | None = None,
        cancel: CancelToken | None = None,
    ) -> Path:
        """Download, check and install ``spec`` (files already fetched are reused)."""
        folder = self.path(spec)
        staging = folder.with_name(f"{folder.name}.staging")
        staging.mkdir(parents=True, exist_ok=True)
        headers = {"User-Agent": f"vfe-vision/{__version__} (local video analysis application)"}
        done, total = 0, spec.download_size
        with httpx.Client(
            timeout=httpx.Timeout(120, connect=15),
            headers=headers,
            follow_redirects=True,  # Hugging Face answers with a redirect to its CDN
            transport=transport,
        ) as client:
            for item in (f for f in spec.files if f.member is None):
                target = staging / item.name
                if not (target.is_file() and target.stat().st_size == item.size):
                    _fetch(
                        client,
                        item,
                        target,
                        before=done,
                        total=total,
                        progress=progress,
                        cancel=cancel,
                    )
                done += item.size
                if progress is not None:
                    progress(item.name, done, total)
            for url in dict.fromkeys(f.url for f in spec.files if f.member is not None):
                members = [f for f in spec.files if f.url == url and f.member is not None]
                done = _from_archive(
                    client, members, staging, before=done, total=total,
                    progress=progress, cancel=cancel,
                )  # fmt: skip
        manifest = {
            "id": spec.id,
            "label": spec.label,
            "licence": spec.licence,
            "source": spec.source,
            "installed_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "files": [
                {
                    "name": f.name,
                    "url": f.url,
                    "size": f.size,
                    "sha256": f.sha256,
                    **({"member": f.member} if f.member else {}),
                }
                for f in spec.files
            ],
        }
        atomic_write_text(
            staging / MANIFEST, json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
        )
        replace_dir(staging, folder)
        return folder


def _fetch(
    client: httpx.Client,
    item: ModelFile,
    target: Path,
    *,
    before: int,
    total: int,
    progress: Progress | None,
    cancel: CancelToken | None,
) -> None:
    part = target.with_name(f"{target.name}.part")
    digest = hashlib.sha256()
    offset = 0
    if part.is_file() and 0 < part.stat().st_size < item.size:  # resume an interrupted file
        with part.open("rb") as existing:
            for block in iter(lambda: existing.read(CHUNK), b""):
                digest.update(block)
        offset = part.stat().st_size
    headers = {"Range": f"bytes={offset}-"} if offset else {}
    try:
        with client.stream("GET", item.url, headers=headers) as response:
            if offset and response.status_code != httpx.codes.PARTIAL_CONTENT:
                digest, offset = hashlib.sha256(), 0  # the server ignored the range: restart
            response.raise_for_status()
            with part.open("ab" if offset else "wb") as out:
                written = offset
                for block in response.iter_bytes(CHUNK):
                    if cancel is not None and cancel.cancelled:
                        raise CancelledError(cancel.reason or "Annulé")
                    out.write(block)
                    digest.update(block)
                    written += len(block)
                    if progress is not None:
                        progress(item.name, before + written, total)
    except httpx.HTTPStatusError as exc:
        raise VfeError(f"Téléchargement refusé ({exc.response.status_code}) : {item.url}") from exc
    except httpx.TransportError as exc:
        raise ServiceUnavailableError(f"Téléchargement interrompu : {item.name} ({exc})") from exc
    size = part.stat().st_size
    if size != item.size or digest.hexdigest() != item.sha256:
        part.unlink(missing_ok=True)
        raise VfeError(
            f"{item.name} : fichier téléchargé invalide (taille ou empreinte différente), "
            "téléchargement annulé."
        )
    replace_with_retry(part, target)


def _from_archive(
    client: httpx.Client,
    members: list[ModelFile],
    staging: Path,
    *,
    before: int,
    total: int,
    progress: Progress | None,
    cancel: CancelToken | None,
) -> int:
    """Fetch one archive (checked), extract ``members`` (each checked), delete the archive."""
    first = members[0]
    size = first.archive_size or 0
    archive = ModelFile(first.url.rsplit("/", 1)[-1], first.url, size, first.archive_sha256 or "")
    target = staging / archive.name
    if all(
        (staging / m.name).is_file() and (staging / m.name).stat().st_size == m.size
        for m in members
    ):
        # Extracted by an earlier, interrupted install: never leave the archive installed.
        target.unlink(missing_ok=True)
        target.with_name(f"{target.name}.part").unlink(missing_ok=True)
        return before + size
    if not (target.is_file() and target.stat().st_size == size):
        _fetch(
            client, archive, target, before=before, total=total, progress=progress, cancel=cancel
        )
    try:
        with zipfile.ZipFile(target) as bundle:
            for member in members:
                if cancel is not None and cancel.cancelled:
                    raise CancelledError(cancel.reason or "Annulé")
                _extract(bundle, member, staging / member.name)
    except (zipfile.BadZipFile, KeyError) as exc:
        target.unlink(missing_ok=True)
        raise VfeError(f"Archive {archive.name} inutilisable : {exc}") from exc
    with contextlib.suppress(OSError):  # an antivirus scan may hold it: only disk space lost
        target.unlink(missing_ok=True)
    if progress is not None:
        progress(first.name, before + size, total)
    return before + size


def _extract(bundle: zipfile.ZipFile, member: ModelFile, target: Path) -> None:
    part = target.with_name(f"{target.name}.part")
    digest = hashlib.sha256()
    with bundle.open(str(member.member)) as source, part.open("wb") as out:
        for block in iter(lambda: source.read(CHUNK), b""):
            out.write(block)
            digest.update(block)
    if part.stat().st_size != member.size or digest.hexdigest() != member.sha256:
        part.unlink(missing_ok=True)
        raise VfeError(
            f"{member.name} : fichier extrait invalide (taille ou empreinte différente)."
        )
    replace_with_retry(part, target)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()
